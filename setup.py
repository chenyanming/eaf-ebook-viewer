#!/usr/bin/env python3
"""Build the vendored Calibre reader runtime for EAF's Python."""

import os
from pathlib import Path
import subprocess
import sys
import sysconfig
import tempfile
from xml.etree import ElementTree

from setuptools import setup
from setuptools.command.bdist_wheel import bdist_wheel
from setuptools.command.build import build


ROOT = Path(__file__).resolve().parent
CALIBRE = ROOT / "vendor" / "calibre"
EXTENSIONS = ROOT / "vendor" / "extensions"
MODULES = (
    "icu", "speedup", "html_as_json", "fast_css_transform",
    "fast_html_entities", "cPalmdoc", "lzx", "msdes", "bzzdec",
    "unicode_names",
)


def runtime_dependencies():
    """Return package requirements, excluding pip-only install options."""
    dependency_path = ROOT / "requirements.txt"
    return [
        line
        for raw_line in dependency_path.read_text(encoding="utf-8").splitlines()
        if (line := raw_line.strip()) and not line.startswith(("#", "-"))
    ]


def build_environment():
    query = ROOT / "vendor" / "build" / "qmake"
    query.parent.mkdir(parents=True, exist_ok=True)
    query.write_text(
        '#!/bin/sh\n'
        'echo "QT_INSTALL_LIBS:/nonexistent/eaf-qt-libs"\n'
        'echo "QT_INSTALL_PLUGINS:/nonexistent/eaf-qt-plugins"\n',
        encoding="utf-8",
    )
    query.chmod(0o755)
    env = os.environ.copy()
    python_path = [str(ROOT), str(CALIBRE / "src")]
    python_path.extend(path for path in sys.path if path)
    env.update({
        "QMAKE": str(query),
        "CALIBRE_SETUP_EXTENSIONS_PATH": str(EXTENSIONS),
        "CALIBRE_HEADLESS_PLATFORM": "offscreen",
        # PEP 517 build isolation adds its dependencies to the build backend's
        # sys.path while retaining the outer interpreter in sys.executable.
        # Propagate that complete path when invoking Python subprocesses.
        "PYTHONPATH": os.pathsep.join(dict.fromkeys(python_path)),
    })
    if sys.platform == "darwin":
        try:
            prefix = subprocess.check_output(
                ("brew", "--prefix", "icu4c"), text=True
            ).strip()
        except (OSError, subprocess.CalledProcessError) as error:
            raise RuntimeError("Install Homebrew icu4c before Ebook Viewer") from error
        env["CFLAGS"] = "-I" + prefix + "/include"
        env["LDFLAGS"] = "-L" + prefix + "/lib"
    elif sys.platform.startswith("linux"):
        try:
            icu_libdir = subprocess.check_output(
                ("pkg-config", "--variable=libdir", "icu-uc"), text=True
            ).strip()
        except (OSError, subprocess.CalledProcessError) as error:
            raise RuntimeError(
                "Install pkg-config and the ICU development package before "
                "Ebook Viewer"
            ) from error
        # Conda/Miniforge puts its own lib directory ahead of system libraries
        # when linking Python extensions.  Prefer the ICU library matching the
        # headers reported by pkg-config to avoid unresolved versioned symbols.
        linker_flags = ["-L" + icu_libdir]
        python_libdir = sysconfig.get_config_var("LIBDIR")
        if python_libdir:
            linker_flags.append("-Wl,-rpath," + python_libdir)
        if env.get("LDFLAGS"):
            linker_flags.append(env["LDFLAGS"])
        env["LDFLAGS"] = " ".join(linker_flags)
    return env


def compile_forms(env):
    code = (
        "from PyQt6.uic import compileUi; import sys; "
        "f=open(sys.argv[2], 'w', encoding='utf-8'); "
        "compileUi(sys.argv[1], f); f.close()"
    )
    for form in (CALIBRE / "src" / "calibre" / "gui2").rglob("*.ui"):
        output = form.with_name(form.stem + "_ui.py")
        if not output.exists() or output.stat().st_mtime < form.stat().st_mtime:
            subprocess.check_call(
                (sys.executable, "-c", code, str(form), str(output)), env=env
            )


def install_resources():
    for name in ("viewer.js", "user-agent-data.json", "icons.rcc"):
        target = CALIBRE / "resources" / name
        if not target.exists():
            source = ROOT / "generated" / name
            if not source.exists():
                raise RuntimeError("generated/{} is missing".format(name))
            target.write_bytes(source.read_bytes())

    viewer_html = CALIBRE / "resources" / "viewer.html"
    if not viewer_html.exists():
        svg_namespace = "http://www.w3.org/2000/svg"
        xlink_namespace = "http://www.w3.org/1999/xlink"
        ElementTree.register_namespace("", svg_namespace)
        ElementTree.register_namespace("xlink", xlink_namespace)
        icons = ElementTree.Element(
            "{{{}}}svg".format(svg_namespace), {"style": "display:none"}
        )
        for path in sorted((CALIBRE / "imgsrc" / "srv").glob("*.svg")):
            source = ElementTree.parse(path).getroot()
            symbol = ElementTree.SubElement(
                icons,
                "{{{}}}symbol".format(svg_namespace),
                {"id": "icon-" + path.stem, "viewBox": source.get("viewBox", "")},
            )
            symbol.extend(list(source))
        reset = (
            CALIBRE / "resources" / "content-server" / "reset.css"
        ).read_text(encoding="utf-8")
        html = "<!DOCTYPE html>\n<html><head><style>{}</style></head><body>{}</body></html>".format(
            reset, ElementTree.tostring(icons, encoding="unicode")
        )
        viewer_html.write_text(html, encoding="utf-8")


def build_calibre_metadata_resources(env):
    resources = CALIBRE / "resources" / "localization"
    required = (
        resources / "iso639.calibre_msgpack",
        resources / "iso3166.calibre_msgpack",
    )
    if not all(path.exists() for path in required):
        code = (
            "import os, runpy, sys; "
            "from calibre_bootstrap import initialize; initialize(); "
            "os.chdir(sys.argv[1]); "
            "sys.argv = ['setup.py', sys.argv[2]]; "
            "runpy.run_path('setup.py', run_name='__main__')"
        )
        for command, output in zip(("iso639", "iso3166"), required):
            if not output.exists():
                subprocess.check_call(
                    (sys.executable, "-c", code, str(CALIBRE), command),
                    cwd=ROOT,
                    env=env,
                )


def build_calibre():
    if not (CALIBRE / "setup.py").exists():
        raise RuntimeError("Clone Ebook Viewer with --recurse-submodules")
    EXTENSIONS.mkdir(parents=True, exist_ok=True)
    modules = MODULES + (("cocoa",) if sys.platform == "darwin" else ())
    env = build_environment()
    with tempfile.TemporaryDirectory(prefix="eaf-calibre-build-") as build_dir:
        for module in modules:
            output = EXTENSIONS / (module + ".so")
            output.unlink(missing_ok=True)
            subprocess.check_call(
                (
                    sys.executable, "setup.py", "build", "--only", module,
                    "--build-dir", build_dir, "--output-dir", str(EXTENSIONS),
                ),
                cwd=CALIBRE,
                env=env,
            )
    install_resources()
    build_calibre_metadata_resources(env)
    compile_forms(env)


class BuildRuntime(build):
    def run(self):
        build_calibre()
        super().run()


class PlatformWheel(bdist_wheel):
    def finalize_options(self):
        super().finalize_options()
        self.root_is_pure = False


setup(
    name="eaf-ebook-viewer-runtime",
    version="8.7.0",
    description="Calibre runtime builder for EAF Ebook Viewer",
    packages=[],
    install_requires=runtime_dependencies(),
    cmdclass={"build": BuildRuntime, "bdist_wheel": PlatformWheel},
    python_requires=">=3.10",
)
