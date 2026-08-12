#!/usr/bin/env python3
"""Load vendored Calibre source into the ordinary EAF Python process."""

from __future__ import annotations

import os
import sys


APP_DIR = os.path.dirname(os.path.abspath(__file__))
CALIBRE_ROOT = os.path.join(APP_DIR, "vendor", "calibre")
CALIBRE_SOURCE = os.path.join(CALIBRE_ROOT, "src")
EXTENSIONS = os.path.join(APP_DIR, "vendor", "extensions")
RESOURCES = os.path.join(CALIBRE_ROOT, "resources")
WORKER = os.path.abspath(__file__)


def initialize():
    if getattr(initialize, "done", False):
        from calibre_compat import prepare_eaf_application

        prepare_eaf_application()
        return
    if not os.path.isdir(CALIBRE_SOURCE):
        raise RuntimeError("Vendored Calibre source is missing: {}".format(CALIBRE_SOURCE))

    for path in (APP_DIR, CALIBRE_SOURCE):
        if os.path.isdir(path) and path not in sys.path:
            sys.path.insert(0, path)

    required = ("icu.so", "speedup.so", "html_as_json.so")
    missing = [name for name in required if not os.path.isfile(os.path.join(EXTENSIONS, name))]
    if missing:
        raise RuntimeError(
            "Ebook Viewer runtime is not built (missing {}). Run EAF's "
            "installer for ebook-viewer with --force.".format(", ".join(missing))
        )
    if not os.path.isfile(os.path.join(RESOURCES, "viewer.js")):
        raise RuntimeError("Calibre viewer.js is missing; reinstall ebook-viewer")
    sys.resources_location = RESOURCES
    sys.extensions_location = EXTENSIONS
    sys.executables_location = APP_DIR
    sys.system_plugins_location = None

    from calibre_compat import install_imageops_shim, install_progress_indicator_shim

    install_progress_indicator_shim()
    install_imageops_shim()
    import calibre  # noqa: F401 - initializes Calibre's import hooks

    # Calibre normally launches calibre-parallel here.  Keep the official
    # worker implementation, but execute it with the same Python as EAF.
    import calibre.startup as startup
    import calibre.utils.ipc.launch as ipc_launch

    worker_command = lambda *args, **kwargs: [sys.executable, WORKER, "--worker"]
    startup.get_debug_executable = worker_command
    ipc_launch.headless_exe_path = worker_command

    from calibre_compat import prepare_eaf_application

    prepare_eaf_application()
    initialize.done = True


if __name__ == "__main__" and "--worker" in sys.argv:
    initialize()
    from calibre.utils.ipc.worker import main

    raise SystemExit(main())
