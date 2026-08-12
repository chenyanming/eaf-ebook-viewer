"""Register Calibre's private WebEngine scheme before EAF creates Qt."""

import os
import sys


APP_DIR = os.path.dirname(os.path.abspath(__file__))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

try:
    from PyQt6.QtWebEngineCore import QWebEngineUrlScheme

    scheme_name = b"clbr"
    if not QWebEngineUrlScheme.schemeByName(scheme_name).name():
        scheme = QWebEngineUrlScheme(scheme_name)
        scheme.setSyntax(QWebEngineUrlScheme.Syntax.Host)
        scheme.setFlags(QWebEngineUrlScheme.Flag.SecureScheme)
        QWebEngineUrlScheme.registerScheme(scheme)
except ImportError:
    pass
