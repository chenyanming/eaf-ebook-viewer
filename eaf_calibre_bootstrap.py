#!/usr/bin/env python3
"""Bootstrap the normal EAF entry point inside calibre's Python runtime."""

import json
import os
import runpy
import sys


def add_eaf_dependency_paths():
    paths = json.loads(os.environ.pop("EAF_SYSTEM_PYTHON_PATHS", "[]"))
    for path in paths:
        if path and "site-packages" in path and path not in sys.path:
            # Append so calibre's bundled PyQt6 and binary modules win over
            # similarly named packages in the system Python environment.
            sys.path.append(path)


def main():
    if len(sys.argv) < 5:
        raise SystemExit(
            "usage: eaf_calibre_bootstrap.py EAF.py WIDTH HEIGHT EPC_PORT"
        )

    eaf_entry = os.path.abspath(sys.argv[1])
    eaf_args = sys.argv[2:]
    eaf_dir = os.path.dirname(eaf_entry)
    # Doom's Straight build directory can lag behind a local EAF checkout
    # when new Python modules are added.  Prefer the source tree containing
    # this extension, then fall back to the build directory passed by Emacs.
    extension_dir = os.path.dirname(os.path.abspath(__file__))
    eaf_source_dir = os.path.abspath(os.path.join(extension_dir, "..", ".."))
    for path in (eaf_dir, eaf_source_dir):
        if path in sys.path:
            sys.path.remove(path)
    sys.path.insert(0, eaf_dir)
    sys.path.insert(0, eaf_source_dir)
    add_eaf_dependency_paths()

    # The fake calibre:// protocol must be registered before QApplication is
    # created.  EAF then uses calibre's QApplication subclass, which supplies
    # the palette, WebEngine and font services expected by EbookViewer.
    from calibre.utils.webengine import setup_fake_protocol

    setup_fake_protocol()

    from calibre.gui2 import Application
    import PyQt6.QtWidgets

    PyQt6.QtWidgets.QApplication = Application
    sys.argv = [eaf_entry, *eaf_args]
    runpy.run_path(eaf_entry, run_name="__main__")


if __name__ == "__main__":
    main()
