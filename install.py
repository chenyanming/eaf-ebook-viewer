#!/usr/bin/env python3
"""Install Ebook Viewer and its vendored Calibre runtime."""

from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parent
REQUIREMENTS = ROOT / "requirements.txt"
PIP = (sys.executable, "-m", "pip", "install")
IMPORT_CHECK = (
    sys.executable,
    "-c",
    "import html5_parser; import lxml.etree",
)


def run(*args):
    subprocess.check_call(args)


def lxml_requirement():
    for raw_line in REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        requirement = raw_line.strip()
        if requirement.startswith("lxml=="):
            return requirement
    raise RuntimeError("requirements.txt must contain a pinned lxml version")


def ensure_parser_compatibility():
    if subprocess.run(IMPORT_CHECK, check=False, capture_output=True).returncode == 0:
        return
    run(
        *PIP,
        "--force-reinstall",
        "--no-deps",
        "--no-binary=lxml",
        lxml_requirement(),
    )
    run(*IMPORT_CHECK)


def main():
    run(*PIP, "-r", str(REQUIREMENTS))
    ensure_parser_compatibility()
    run(*PIP, "--no-deps", str(ROOT))


if __name__ == "__main__":
    main()
