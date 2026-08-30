#!/usr/bin/env python3
"""Calibre E-book Viewer adapter for EAF.

The renderer, pagination, navigation, annotations and format conversion all
come from calibre.  This module only adapts calibre's QMainWindow to an EAF
Buffer and gives every book an isolated WebEngine profile/content handler.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import traceback
import zipfile
from uuid import uuid4

from PyQt6.QtCore import QEvent, QFile, QIODevice, QTimer, Qt, pyqtSlot
from PyQt6.QtGui import QCursor, QKeyEvent
from PyQt6.QtWebChannel import QWebChannel
from PyQt6.QtWebEngineCore import QWebEngineScript
from PyQt6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QDialog,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QTextEdit,
    QWidget,
)

from core.buffer import Buffer, QT_KEY_DICT, QT_MODIFIER_DICT, QT_TEXT_DICT
from core.utils import (
    PostGui,
    eval_in_emacs,
    focus_emacs_buffer,
    interactive,
    message_to_emacs,
    post_event,
)

_APP_DIR = os.path.dirname(os.path.abspath(__file__))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from calibre_bootstrap import initialize as initialize_calibre


_PROFILE_HANDLERS = {}
_ZIP_EBOOK_EXTENSIONS = {".epub", ".fbz", ".htmlz", ".txtz"}
_CALIBRE_RESIZE_HANDLERS = (
    (
        b'window.addEventListener("resize", '
        b'debounce(view.on_resize.bind(self), 250));',
        b'''(function () {
            var eafResizePending = false;
            window.addEventListener("resize", function () {
                if (!eafResizePending) {
                    eafResizePending = true;
                    window.requestAnimationFrame(function () {
                        eafResizePending = false;
                        view.on_resize();
                    });
                }
            });
        })();''',
    ),
    (
        b'window.addEventListener("resize", '
        b'debounce(self.onresize, 500));',
        b'''(function () {
            var eafResizePending = false;
            window.addEventListener("resize", function () {
                if (!eafResizePending) {
                    eafResizePending = true;
                    window.requestAnimationFrame(function () {
                        eafResizePending = false;
                        self.onresize();
                    });
                }
            });
        })();''',
    ),
)


def _qt_webchannel_script():
    resource = QFile(":/qtwebchannel/qwebchannel.js")
    if not resource.open(QIODevice.OpenModeFlag.ReadOnly):
        raise RuntimeError("Qt WebChannel JavaScript resource is unavailable")
    try:
        return bytes(resource.readAll()).decode("utf-8")
    finally:
        resource.close()


def _enable_immediate_calibre_resize(viewer_js):
    """Update Calibre layout before paint and again after resize settles."""
    for delayed, responsive in _CALIBRE_RESIZE_HANDLERS:
        if delayed in viewer_js:
            viewer_js = viewer_js.replace(
                delayed, responsive + b"\n" + delayed, 1
            )
    return viewer_js


def _activate_emacs_after_mouse_release():
    """Return keyboard focus to Emacs across old and new EAF versions."""
    app = QApplication.instance()
    tracker = getattr(app, "macos_window_tracker", None)
    if tracker is not None:
        tracker.activate_emacs_after_mouse_release()
    else:
        # Older EAF releases have no native macOS window tracker.  This
        # function has been part of EAF for much longer and is also what its
        # PDF viewer uses after completing a mouse selection.
        eval_in_emacs("eaf-activate-emacs-window", [])


def _is_cloud_storage_path(path):
    if platform.system() != "Darwin":
        return False
    parts = os.path.abspath(path).split(os.sep)
    return any(
        parts[index : index + 2] == ["Library", "CloudStorage"]
        for index in range(max(0, len(parts) - 1))
    )


def _validate_zip_ebook(path, extension=None):
    """Raise a concise error if a ZIP-based e-book is incomplete."""
    extension = (extension or os.path.splitext(path)[1]).lower()
    if extension not in _ZIP_EBOOK_EXTENSIONS:
        return
    try:
        with zipfile.ZipFile(path) as archive:
            if extension == ".epub":
                try:
                    mimetype = archive.read("mimetype").strip()
                except KeyError as error:
                    raise ValueError("the EPUB has no mimetype entry") from error
                if mimetype != b"application/epub+zip":
                    raise ValueError("the EPUB mimetype entry is invalid")
            bad_member = archive.testzip()
            if bad_member is not None:
                raise ValueError(
                    "the archive member {!r} failed its CRC check".format(bad_member)
                )
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        raise RuntimeError(
            "The e-book is not fully downloaded or its cloud version is "
            "damaged ({}). Restore/download the complete file in Finder, "
            "then reopen it. Source: {}".format(error, path)
        ) from error


def _validate_staged_book(path, expected_size, extension):
    """Validate every staged format, with deeper checks for ZIP containers."""
    actual_size = os.path.getsize(path)
    if expected_size <= 0 or actual_size <= 0 or actual_size != expected_size:
        raise RuntimeError(
            "The e-book is not fully downloaded: expected {} bytes, got {}. "
            "Source: {}".format(expected_size, actual_size, path)
        )
    _validate_zip_ebook(path, extension)


def _copy_cloud_file(source, destination):
    """Copy SOURCE completely, allowing macOS File Provider to materialize it."""
    if platform.system() == "Darwin":
        result = subprocess.run(
            ["/bin/cp", source, destination],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if result.returncode:
            raise RuntimeError(
                "Could not download the cloud e-book ({}): {}".format(
                    result.stderr.strip() or "cp failed", source
                )
            )
        return
    with open(source, "rb") as cloud_file, open(destination, "wb") as output:
        shutil.copyfileobj(cloud_file, output, length=4 * 1024 * 1024)
        output.flush()
        os.fsync(output.fileno())


def _stage_book(path):
    """Materialize cloud books; pass ordinary local books through unchanged."""
    source = os.path.abspath(path)
    extension = os.path.splitext(source)[1].lower()

    if not _is_cloud_storage_path(source):
        source_stat = os.stat(source)
        if source_stat.st_size <= 0:
            raise RuntimeError("The e-book is empty: {}".format(source))
        _validate_zip_ebook(source, extension)
        return source

    if platform.system() == "Darwin":
        cache_root = os.path.expanduser("~/Library/Caches")
    elif platform.system() == "Windows":
        cache_root = os.environ.get(
            "LOCALAPPDATA", os.path.expanduser("~/.cache")
        )
    else:
        cache_root = os.environ.get(
            "XDG_CACHE_HOME", os.path.expanduser("~/.cache")
        )
    announced = False
    for attempt in range(3):
        # The first read can cause File Provider to materialize a placeholder,
        # changing its metadata. Recompute the cache key and retry within this
        # same open request instead of making the user open the book twice.
        source_stat = os.stat(source)
        fingerprint = "\0".join(
            (source, str(source_stat.st_size), str(source_stat.st_mtime_ns))
        ).encode("utf-8")
        cache_key = hashlib.sha256(fingerprint).hexdigest()
        cache_dir = os.path.join(
            cache_root, "eaf-ebook-viewer", "books", cache_key
        )
        os.makedirs(cache_dir, exist_ok=True)
        cached_path = os.path.join(cache_dir, os.path.basename(source))

        if os.path.isfile(cached_path):
            try:
                _validate_staged_book(
                    cached_path, source_stat.st_size, extension
                )
                os.chmod(cached_path, 0o444)
                return cached_path
            except RuntimeError:
                # A prior interrupted download left a bad cache entry.
                pass

        if not announced:
            message_to_emacs(
                "Downloading cloud e-book to the local EAF cache..."
            )
            announced = True
        descriptor, temporary_path = tempfile.mkstemp(
            prefix=cache_key + ".", suffix=".download", dir=cache_dir
        )
        os.close(descriptor)
        try:
            _copy_cloud_file(source, temporary_path)
            final_stat = os.stat(source)
            changed = (
                final_stat.st_size != source_stat.st_size
                or final_stat.st_mtime_ns != source_stat.st_mtime_ns
            )
            if changed:
                if attempt < 2:
                    continue
                raise RuntimeError(
                    "Google Drive repeatedly changed the e-book while it was "
                    "downloading. Wait for sync to finish, then reopen it: "
                    "{}".format(source)
                )
            _validate_staged_book(
                temporary_path, source_stat.st_size, extension
            )
            os.replace(temporary_path, cached_path)
            os.chmod(cached_path, 0o444)
            return cached_path
        finally:
            try:
                os.unlink(temporary_path)
            except FileNotFoundError:
                pass

    raise RuntimeError("Could not cache the cloud e-book: {}".format(source))


def _install_isolated_profile_factory(web_view_module):
    """Make calibre create a fresh profile and book handler per WebView."""
    from qt.core import QByteArray
    from qt.webengine import QWebEngineProfile, QWebEngineSettings

    from calibre import as_unicode, prints
    from calibre.constants import (
        FAKE_HOST,
        FAKE_PROTOCOL,
        __version__,
        in_develop_mode,
        is_running_from_develop,
    )
    from calibre.ebooks.oeb.polish.utils import guess_type
    from calibre.gui2.viewer.config import viewer_config_dir
    from calibre.srv.code import get_translations_data
    from calibre.utils.filenames import make_long_path_useable
    from calibre.utils.resources import get_path as resource_path
    from calibre.utils.serialize import json_loads
    from calibre.utils.shared_file import share_open
    from calibre.utils.webengine import (
        create_script,
        insert_scripts,
        send_reply,
        setup_profile,
    )
    from polyglot.builtins import as_bytes

    sandbox_host = FAKE_HOST.rpartition(".")[0] + ".sandbox"

    class IsolatedBookHandler(web_view_module.UrlSchemeHandler):
        """Serve one prepared calibre book without module-global book data."""

        def __init__(self, parent=None):
            super().__init__(parent)
            self.allowed_hosts = (FAKE_HOST, sandbox_host)
            self.book_path = None
            self.path_to_ebook = None
            self.metadata = None
            self.manifest = None
            self.manifest_mime = None
            self.parsed_metadata = None
            self.parsed_manifest = None

        def set_book(self, path, path_to_ebook):
            self.book_path = os.path.abspath(path)
            self.path_to_ebook = path_to_ebook
            self.metadata = self.get_data("calibre-book-metadata.json")[0]
            self.manifest, self.manifest_mime = self.get_data(
                "calibre-book-manifest.json"
            )
            if self.metadata is None or self.manifest is None:
                raise ValueError("calibre did not prepare the book manifest")
            self.parsed_metadata = json_loads(self.metadata)
            self.parsed_manifest = json_loads(self.manifest)

        def path_for_name(self, name):
            if self.book_path is None:
                return None
            candidate = os.path.abspath(os.path.join(self.book_path, name))
            try:
                if os.path.commonpath((self.book_path, candidate)) != self.book_path:
                    return None
            except ValueError:
                return None
            return candidate

        def get_data(self, name):
            path = self.path_for_name(name)
            if path is None:
                return None, None
            try:
                with share_open(path, "rb") as book_file:
                    return book_file.read(), guess_type(name)
            except OSError as error:
                prints(
                    "Failed to read from book file: {} with error: {}".format(
                        name, as_unicode(error)
                    )
                )
                return None, None

        def requestStarted(self, request):  # noqa: N802 - Qt API name
            from qt.webengine import QWebEngineUrlRequestJob

            if bytes(request.requestMethod()) != b"GET":
                return self.fail_request(
                    request, QWebEngineUrlRequestJob.Error.RequestDenied
                )

            url = request.requestUrl()
            host = url.host()
            if host not in self.allowed_hosts or url.scheme() != FAKE_PROTOCOL:
                return self.fail_request(request)

            name = url.path()[1:]
            if host == sandbox_host and name.partition("/")[0] not in (
                "book",
                "mathjax",
            ):
                return self.fail_request(request)

            if name.startswith("book/"):
                name = name.partition("/")[2]
                if name in ("__index__", "__popup__"):
                    send_reply(request, "text/html", b"<div>\xa0</div>")
                    return
                try:
                    data, mime_type = self.get_data(name)
                    if data is None:
                        request.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)
                        return
                    mime_type = {
                        "application/vnd.ms-opentype": "application/x-font-ttf",
                        "application/x-font-truetype": "application/x-font-ttf",
                        "application/font-sfnt": "application/x-font-ttf",
                    }.get(mime_type, mime_type)
                    if mime_type == "text/css":
                        mime_type += "; charset=utf-8"
                    send_reply(request, mime_type, as_bytes(data))
                except Exception:
                    traceback.print_exc()
                    self.fail_request(
                        request, QWebEngineUrlRequestJob.Error.RequestFailed
                    )
            elif name == "manifest":
                if self.manifest is None or self.metadata is None:
                    request.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)
                    return
                data = b"[" + self.manifest + b"," + self.metadata + b"]"
                send_reply(request, self.manifest_mime, data)
            elif name == "reader-background":
                mime_type, data = web_view_module.background_image()
                if data:
                    send_reply(request, mime_type, data)
                else:
                    request.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)
            elif name.startswith("reader-background-"):
                encoded_name = name[len("reader-background-") :]
                mime_type, data = web_view_module.background_image(encoded_name)
                if data:
                    send_reply(request, mime_type, data)
                else:
                    request.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)
            elif name.startswith("mathjax/"):
                web_view_module.handle_mathjax_request(request, name)
            elif not name:
                send_reply(request, "text/html", web_view_module.viewer_html())
            else:
                self.fail_request(request)

    def create_isolated_profile():
        profile_name = "eaf-ebook-{}".format(uuid4())
        profile = setup_profile(
            QWebEngineProfile(profile_name, QApplication.instance())
        )
        os_name = "windows" if platform.system() == "Windows" else (
            "macos" if platform.system() == "Darwin" else "linux"
        )
        profile.setHttpUserAgent("calibre-viewer {} {}".format(__version__, os_name))

        if is_running_from_develop:
            from calibre.utils.rapydscript import compile_viewer

            compile_viewer()

        viewer_js = resource_path(
            "viewer.js", data=True, allow_user_override=False
        )
        viewer_js = _enable_immediate_calibre_resize(viewer_js)
        try:
            translations = get_translations_data() or b"null"
        except FileNotFoundError:
            # Raw Calibre source does not contain the release translation
            # archive.  The viewer supports an untranslated null catalog.
            translations = b"null"
        viewer_js = viewer_js.replace(b"__TRANSLATIONS_DATA__", translations, 1)
        if in_develop_mode:
            viewer_js = viewer_js.replace(b"__IN_DEVELOP_MODE__", b"1")
        insert_scripts(profile, create_script("viewer.js", viewer_js))
        insert_scripts(
            profile,
            create_script(
                "qwebchannel.js",
                _qt_webchannel_script(),
                injection_point=QWebEngineScript.InjectionPoint.DocumentCreation,
            ),
            create_script(
                "eaf-selection-context.js",
                open(
                    os.path.join(_APP_DIR, "selection_context.js"),
                    encoding="utf-8",
                ).read(),
            ),
            create_script(
                "eaf-text-highlights.js",
                open(
                    os.path.join(_APP_DIR, "text_highlights.js"),
                    encoding="utf-8",
                ).read(),
            ),
        )

        handler = IsolatedBookHandler(profile)
        profile.installUrlSchemeHandler(
            QByteArray(FAKE_PROTOCOL.encode("ascii")), handler
        )
        settings = profile.settings()
        settings.setDefaultTextEncoding("utf-8")
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LinksIncludedInFocusChain, False
        )
        _PROFILE_HANDLERS[id(profile)] = handler
        return profile

    web_view_module.create_profile = create_isolated_profile


class AppBuffer(Buffer):
    """An EAF buffer containing one isolated calibre EbookViewer widget."""

    def __init__(self, buffer_id, url, arguments):
        super().__init__(buffer_id, url, arguments, False)
        self.viewer = None
        self.book_handler = None
        self._annotation_events_ready = False
        self._known_highlights = {}
        self._last_selected_highlight_id = None
        self._last_text_context = {}
        self._text_highlights = []
        self._input_focused = False
        self._key_event_widget = None
        self._content_ready = False
        self._pending_cfi = None
        self._pending_cfi_generation = 0
        self._pending_cfi_navigations = 0
        self._pending_cfi_active = False
        self._pending_cfi_add_to_history = False
        try:
            options = json.loads(arguments) if arguments else {}
        except (TypeError, ValueError):
            options = {}
        self._open_at = options.get("open_at")
        if isinstance(self._open_at, str) and self._open_at.startswith("epubcfi("):
            # Calibre performs the first navigation as part of load_ebook().
            # Replay it once after the content document and pagination settle.
            self._pending_cfi = self._open_at
            self._pending_cfi_generation += 1
            self._pending_cfi_navigations = 1
            self._pending_cfi_active = True

        try:
            self._create_viewer(url)
        except Exception as error:
            traceback.print_exc()
            self._show_startup_error(error)

        self.build_all_methods(self)

    def _create_viewer(self, url):
        book_path = _stage_book(url)
        initialize_calibre()
        app = QApplication.instance()
        if app is None:
            raise RuntimeError("EAF did not create a QApplication")

        from calibre.gui2.viewer import get_boss, get_current_book_data
        from calibre.gui2.viewer.convert_book import initialize_worker
        from calibre.gui2.viewer.ui import EbookViewer
        import calibre.gui2.viewer.ui as viewer_ui
        import calibre.gui2.viewer.web_view as web_view_module
        from calibre.utils.webengine import setup_default_profile
        from qt.core import Qt

        if not getattr(app, "_eaf_calibre_profile_ready", False):
            setup_default_profile()
            app._eaf_calibre_profile_ready = True

        _install_isolated_profile_factory(web_view_module)
        initialize_worker()

        buffer = self

        class EmbeddedEbookViewer(EbookViewer):
            def update_window_title(self):
                """Keep the EAF buffer name consistent with NOV."""
                self.setWindowTitle(os.path.basename(url))

            def load_ebook(self, *args, **kwargs):
                # A reload replaces current_book_data asynchronously.  Do not
                # let integrations operate on the previous book's annotations
                # while Calibre is preparing the replacement.
                buffer._annotation_events_ready = False
                buffer._known_highlights = {}
                buffer._last_selected_highlight_id = None
                return super().load_ebook(*args, **kwargs)

            def load_finished(self, ok, data):
                if ok:
                    buffer.book_handler.set_book(
                        data["base"], data["pathtoebook"]
                    )
                super().load_finished(ok, data)
                if (
                    ok
                    and self.current_book_data
                    and "annotations_map" in self.current_book_data
                ):
                    buffer._known_highlights = {
                        item.get("uuid"): dict(item)
                        for item in self.current_book_data["annotations_map"].get(
                            "highlight", ()
                        )
                        if item.get("uuid") and not item.get("removed")
                    }
                    buffer._annotation_events_ready = True
                    buffer.activate_context()
                    # This is deliberately after Calibre has populated
                    # current_book_data and the initial annotation snapshot.
                    buffer._send_book_data()
                    buffer._apply_text_highlights()

        viewer = self.viewer = EmbeddedEbookViewer()
        viewer.setWindowFlags(Qt.WindowType.Widget)
        page = viewer.web_view.page()
        profile = page.profile()
        self.web_channel = QWebChannel(page)
        self.web_channel.registerObject("pyobject", self)
        page.setWebChannel(
            self.web_channel, QWebEngineScript.ScriptWorldId.ApplicationWorld
        )
        self.book_handler = _PROFILE_HANDLERS[id(profile)]

        self.add_widget(viewer)
        self._install_application_event_filter()
        viewer.windowTitleChanged.connect(self.change_title)
        viewer.web_view.loadFinished.connect(self._web_page_loaded)
        viewer.web_view.show_loading_message.connect(
            self._content_loading_changed
        )
        viewer.web_view.content_file_changed.connect(
            self._content_file_changed
        )
        viewer.web_view.highlights_changed.connect(
            self._highlights_changed
        )
        viewer.web_view.selection_changed.connect(
            self._selection_changed
        )

        # Fullscreen and quit belong to the containing EAF buffer, not to an
        # independent calibre top-level window.
        try:
            viewer.web_view.toggle_full_screen.disconnect()
        except (TypeError, RuntimeError):
            pass
        viewer.web_view.toggle_full_screen.connect(self.toggle_fullscreen)
        try:
            viewer.web_view.quit.disconnect()
        except (TypeError, RuntimeError):
            pass
        viewer.web_view.quit.connect(self.close_buffer)

        self._get_boss = get_boss
        self._get_current_book_data = get_current_book_data
        self._viewer_ui = viewer_ui
        self._web_view_module = web_view_module
        self.activate_context()
        viewer.load_ebook(book_path, open_at=self._open_at)

    def _content_loading_changed(self, message):
        """Track when Calibre starts replacing the visible content file."""
        if message:
            self._content_ready = False

    def _content_file_changed(self, _name):
        """Resume a queued CFI after Calibre finishes pagination."""
        self._content_ready = True
        if self._pending_cfi_active:
            self._pending_cfi_active = False
        self._schedule_pending_cfi()

    def _schedule_pending_cfi(self):
        if self._pending_cfi is None:
            return
        generation = self._pending_cfi_generation
        QTimer.singleShot(
            100,
            lambda: self._drive_pending_cfi(generation),
        )

    def _drive_pending_cfi(self, generation):
        if (
            generation != self._pending_cfi_generation
            or self._pending_cfi is None
            or self._pending_cfi_active
            or not self._content_ready
        ):
            return
        if self._pending_cfi_navigations <= 0:
            self._pending_cfi = None
            return

        location = self._pending_cfi
        add_to_history = self._pending_cfi_add_to_history
        self._pending_cfi_add_to_history = False
        self._pending_cfi_navigations -= 1
        self._pending_cfi_active = True
        self._content_ready = False
        self.activate_context()
        self.viewer.goto_cfi(location, add_to_history=add_to_history)

    @PostGui()
    def open_at(self, location):
        """Move an already-open viewer using a stable Calibre location."""
        if self.viewer is None or not location:
            return
        self._pending_cfi_generation += 1
        if location.startswith("search:"):
            self._pending_cfi = None
            self.viewer.show_search(location[len("search:"):], trigger=True)
        elif location.startswith("epubcfi("):
            self._pending_cfi = location
            # Run once after the EAF window switch settles, then once more
            # after Calibre confirms that the target content was paginated.
            self._pending_cfi_navigations = 2
            self._pending_cfi_active = False
            self._pending_cfi_add_to_history = True
            self._schedule_pending_cfi()

    def _show_startup_error(self, error):
        message = "EAF Calibre E-book Viewer failed to start:\n\n{}".format(error)
        message_to_emacs(message.replace("\n", " "))
        label = QLabel(message)
        label.setWordWrap(True)
        label.setMargin(36)
        label.setStyleSheet(
            "QLabel { color: #3b3028; background: #f3eadb; "
            "font-family: 'Iowan Old Style', serif; font-size: 16px; "
            "border: 1px solid #c8b79e; }"
        )
        self.add_widget(label)

    def activate_context(self):
        """Select this viewer for calibre's remaining process-global helpers."""
        if self.viewer is None:
            return
        self._get_boss(self.viewer)
        self._get_current_book_data(self.viewer.current_book_data)
        if self.viewer.current_book_data:
            base = self.viewer.current_book_data.get("base")
            path = self.viewer.current_book_data.get("pathtoebook", self.url)
            if base:
                self._web_view_module.set_book_path(base, path)

    def eventFilter(self, watched, event):  # noqa: N802 - Qt API name
        if not isinstance(watched, QWidget) or not self._belongs_to_viewer(watched):
            return False
        if event.type() == QEvent.Type.Show and isinstance(watched, QDialog):
            QTimer.singleShot(
                0, lambda dialog=watched: self._prepare_native_dialog_input(dialog)
            )
        if event.type() in (
            QEvent.Type.FocusIn,
            QEvent.Type.KeyPress,
            QEvent.Type.MouseButtonPress,
            QEvent.Type.Wheel,
        ):
            self.activate_context()

        if event.type() == QEvent.Type.FocusIn:
            web_view = self.viewer.web_view
            if watched is web_view or web_view.isAncestorOf(watched):
                QTimer.singleShot(0, self._refresh_web_input_focus)
            else:
                input_widget = self._text_input_widget(watched)
                self._set_input_focus(input_widget is not None, input_widget)

        focus_event_types = (
            QEvent.Type.MouseButtonPress,
            QEvent.Type.MouseButtonRelease,
            QEvent.Type.MouseButtonDblClick,
        )
        if platform.system() != "Darwin":
            focus_event_types += (QEvent.Type.Wheel,)
        return_to_emacs = False
        if event.type() in focus_event_types:
            web_view = self.viewer.web_view
            in_web_view = watched is web_view or web_view.isAncestorOf(watched)
            if in_web_view:
                if event.type() == QEvent.Type.MouseButtonRelease:
                    # Calibre creates the inline highlight textarea from the
                    # click handler and focuses it with setTimeout(0). Query
                    # after that handler has completed, not before WebEngine
                    # has received the mouse-release event.
                    QTimer.singleShot(50, self._refresh_web_input_focus)
                return_to_emacs = True
            else:
                input_widget = self._text_input_widget(watched)
                self._set_input_focus(input_widget is not None, input_widget)
                dialog = self._dialog_for_widget(watched)
                if dialog is None and input_widget is None:
                    # A Qt-side click (for example in the TOC) leaves the
                    # prior DOM activeElement intact. Blur it so the next
                    # is_focus() query cannot mistake an old editor for the
                    # current target.
                    self.viewer.web_view.page().runjs(
                        "document.activeElement?.blur()"
                    )
                # Native modal dialogs must keep Qt focus for QTextEdit and
                # other controls logically, while Emacs remains the actual
                # keyboard owner just as it does for browser DOM inputs.
                return_to_emacs = dialog is None or input_widget is not None
            if return_to_emacs:
                focus_emacs_buffer(self.buffer_id)

        if event.type() in (QEvent.Type.Close, QEvent.Type.Hide):
            if isinstance(watched, QDialog):
                self._set_input_focus(False)
                focus_emacs_buffer(self.buffer_id)
                if platform.system() == "Darwin":
                    QTimer.singleShot(50, _activate_emacs_after_mouse_release)

        if (
            event.type() == QEvent.Type.MouseButtonRelease
            and platform.system() == "Darwin"
            and return_to_emacs
        ):
            # Match EAF's browser behavior: finish the native Qt gesture,
            # then return keyboard control to Emacs.  Keys are forwarded back
            # to the selected Qt/HTML input while input focus is active.
            QTimer.singleShot(50, _activate_emacs_after_mouse_release)
        return False

    @staticmethod
    def _text_input_widget(widget):
        while isinstance(widget, QWidget):
            if isinstance(widget, (QLineEdit, QTextEdit, QPlainTextEdit,
                                   QAbstractSpinBox)):
                return widget
            if isinstance(widget, QComboBox) and widget.isEditable():
                return widget.lineEdit()
            widget = widget.parentWidget()
        return None

    @staticmethod
    def _dialog_for_widget(widget):
        while isinstance(widget, QWidget):
            if isinstance(widget, QDialog):
                return widget
            widget = widget.parentWidget()
        return None

    def _belongs_to_viewer(self, widget):
        while widget is not None:
            if widget is self.viewer:
                return True
            widget = widget.parent()
        return False

    def _prepare_native_dialog_input(self, dialog):
        """Route Emacs keystrokes to a Calibre dialog's text editor."""
        input_widget = next(
            (
                dialog.findChild(widget_type)
                for widget_type in (
                    QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox
                )
                if dialog.findChild(widget_type) is not None
            ),
            None,
        )
        if input_widget is not None:
            input_widget.setFocus()
            self._set_input_focus(True, input_widget)

    def _set_input_focus(self, focused, widget=None):
        self._input_focused = bool(focused)
        self._key_event_widget = widget if focused else None
        eval_in_emacs(
            "eaf-ebook-viewer-update-focus-state",
            [self.buffer_id, self._input_focused],
        )

    @PostGui()
    def _refresh_web_input_focus(self):
        page = self.viewer.web_view.page()
        page.runJavaScript(
            """(() => {
                const active = document.activeElement;
                if (!active) return false;
                if (active.isContentEditable) return true;
                const tag = active.tagName.toLowerCase();
                if (tag === 'textarea' || tag === 'select') return true;
                if (tag !== 'input') return false;
                return !['button', 'checkbox', 'color', 'file', 'hidden',
                    'image', 'radio', 'range', 'reset', 'submit'].includes(
                        (active.type || 'text').toLowerCase());
            })()""",
            QWebEngineScript.ScriptWorldId.ApplicationWorld,
            self._web_input_focus_changed,
        )

    def _web_input_focus_changed(self, focused):
        focused = bool(focused)
        if not focused and self._key_event_widget not in (
            None, self.viewer.web_view
        ):
            try:
                if self._key_event_widget.isVisible():
                    return
            except RuntimeError:
                pass
        self._set_input_focus(
            focused, self.viewer.web_view if focused else None
        )

    def is_focus(self):
        """Query the current editor, matching EAF BrowserBuffer behavior."""
        if self._key_event_widget is not None and (
            self._key_event_widget is not self.viewer.web_view
        ):
            try:
                if self._key_event_widget.isVisible():
                    return True
            except RuntimeError:
                self._key_event_widget = None
        native_input = self._text_input_widget(QApplication.focusWidget())
        if native_input is not None and (
            native_input is self.viewer or self.viewer.isAncestorOf(native_input)
        ):
            self._set_input_focus(True, native_input)
            return True
        return self._input_focused

    def _install_application_event_filter(self):
        # Calibre editors such as NotesEditDialog are native top-level
        # QDialogs. An application filter sees them reliably; parent-chain
        # scoping in eventFilter keeps other EAF buffers and apps untouched.
        QApplication.instance().installEventFilter(self)

    def _shortcut(self, action):
        if self.viewer is not None:
            self.activate_context()
            self.viewer.web_view.trigger_shortcut(action)

    def _send_text_context(self, text_context):
        text_context = dict(text_context or {})
        self._last_text_context = text_context
        text_context["book"] = {"path": self.url}
        eval_in_emacs(
            "eaf-ebook-viewer-set-text-context",
            [self.buffer_id, json.dumps(text_context, ensure_ascii=False)],
        )

    @staticmethod
    def _annotation_cfi(annotation):
        start_cfi = annotation.get("start_cfi") or ""
        if start_cfi.startswith("epubcfi("):
            return start_cfi
        try:
            spine_number = 2 * (int(annotation["spine_index"]) + 1)
        except (KeyError, TypeError, ValueError):
            return ""
        return "epubcfi(/{0}{1})".format(spine_number, start_cfi)

    def _send_annotation_created(self, annotation):
        selected = " ".join(
            (self._last_text_context.get("selection") or "").split()
        )
        highlighted = " ".join(
            (annotation.get("highlighted_text") or "").split()
        )
        text_context = (
            self._last_text_context
            if selected and selected == highlighted
            else None
        )
        payload = {
            "annotation": annotation,
            "locator": {
                "type": "epubcfi",
                "value": self._annotation_cfi(annotation),
            },
            "text_context": text_context,
            "book": {"path": self.url},
        }
        eval_in_emacs(
            "eaf-ebook-viewer-notify-annotation-created",
            [self.buffer_id, json.dumps(payload, ensure_ascii=False)],
        )

    def _send_annotation_removed(self, annotation):
        payload = {
            "annotation": annotation,
            "book": {"path": self.url},
        }
        eval_in_emacs(
            "eaf-ebook-viewer-notify-annotation-removed",
            [self.buffer_id, json.dumps(payload, ensure_ascii=False)],
        )

    def _send_annotation_updated(self, annotation):
        payload = {
            "annotation": annotation,
            "locator": {
                "type": "epubcfi",
                "value": self._annotation_cfi(annotation),
            },
            "book": {"path": self.url},
        }
        eval_in_emacs(
            "eaf-ebook-viewer-notify-annotation-updated",
            [self.buffer_id, json.dumps(payload, ensure_ascii=False)],
        )

    def _send_annotation_clicked(self, annotation):
        payload = {
            "annotation": annotation,
            "locator": {
                "type": "epubcfi",
                "value": self._annotation_cfi(annotation),
            },
            "book": {"path": self.url},
        }
        eval_in_emacs(
            "eaf-ebook-viewer-notify-annotation-clicked",
            [self.buffer_id, json.dumps(payload, ensure_ascii=False)],
        )

    def _annotations_map(self):
        if self.viewer is None:
            return None
        current_book_data = getattr(self.viewer, "current_book_data", None)
        if not isinstance(current_book_data, dict):
            return None
        annotations_map = current_book_data.get("annotations_map")
        return annotations_map if hasattr(annotations_map, "get") else None

    def _annotation_by_uuid(self, uuid):
        annotations_map = self._annotations_map()
        if annotations_map is None or not uuid:
            return None
        return next(
            (
                item
                for item in annotations_map.get("highlight", ())
                if item.get("uuid") == uuid and not item.get("removed")
            ),
            None,
        )

    def _selection_changed(self, _text, annotation_id):
        if not annotation_id:
            self._last_selected_highlight_id = None
            return
        if annotation_id == self._last_selected_highlight_id:
            return
        self._last_selected_highlight_id = annotation_id
        annotation = self._annotation_by_uuid(annotation_id)
        if annotation is not None:
            self._send_annotation_clicked(annotation)

    def _highlights_changed(self, highlights):
        current = {
            item.get("uuid"): dict(item)
            for item in highlights
            if item.get("uuid") and not item.get("removed")
        }
        if not self._annotation_events_ready:
            self._known_highlights = current
            return
        for annotation in highlights:
            annotation_id = annotation.get("uuid")
            if (
                annotation_id
                and annotation_id in self._known_highlights
                and annotation.get("removed")
            ):
                self._send_annotation_removed(annotation)
            elif (
                annotation_id
                and annotation_id not in self._known_highlights
                and not annotation.get("removed")
                and annotation.get("highlighted_text")
            ):
                self._send_annotation_created(annotation)
            elif (
                annotation_id
                and annotation_id in self._known_highlights
                and not annotation.get("removed")
                and annotation.get("notes")
                != self._known_highlights[annotation_id].get("notes")
            ):
                self._send_annotation_updated(annotation)
        self._known_highlights = current
        # This signal originates in the WebChannel. Running JavaScript again
        # before its callback unwinds can crash Qt WebEngine on macOS.
        QTimer.singleShot(50, self._apply_text_highlights)

    def _apply_text_highlights(self):
        if self.viewer is None:
            return
        payload = json.dumps(self._text_highlights, ensure_ascii=False)
        self.viewer.web_view.page().runjs(
            "if (window.eafEbookSetTextHighlights) {"
            "window.eafEbookSetTextHighlights(%s);}" % payload
        )

    def _web_page_loaded(self, ok):
        if ok:
            self._apply_text_highlights()

    @PostGui()
    def set_text_highlights(self, payload):
        """Highlight a JSON list of words without changing book markup."""
        try:
            if isinstance(payload, str) and payload.startswith("base64:"):
                payload = base64.b64decode(
                    payload[len("base64:"):]
                ).decode("utf-8")
            entries = json.loads(payload) if isinstance(payload, str) else payload
        except (TypeError, ValueError) as error:
            print("Failed to parse text highlights: {!r}".format(error))
            entries = []
        self._text_highlights = [
            entry
            for entry in (entries or [])
            if isinstance(entry, (str, dict))
        ]
        self._apply_text_highlights()

    @PostGui()
    def clear_text_highlights(self):
        """Remove text highlights supplied by external integrations."""
        self._text_highlights = []
        self._apply_text_highlights()

    def _send_book_data(self):
        """Expose calibre's parsed metadata and manifest to the EAF buffer."""
        eval_in_emacs(
            "eaf-ebook-viewer-set-book-data",
            [
                self.buffer_id,
                json.dumps(
                    self.book_handler.parsed_metadata,
                    ensure_ascii=False,
                ),
                json.dumps(
                    self.book_handler.parsed_manifest,
                    ensure_ascii=False,
                ),
            ],
        )

    @pyqtSlot(str)
    def text_context_changed(self, payload):
        try:
            text_context = json.loads(payload)
        except (TypeError, ValueError):
            return
        text_context["word"] = " ".join(
            (text_context.get("word") or "").split()
        )
        text_context["selection"] = " ".join(
            (text_context.get("selection") or "").split()
        )
        text_context["context"] = " ".join(
            (text_context.get("context") or "").split()
        )
        self._send_text_context(text_context)

    @pyqtSlot(str)
    def document_text_changed(self, payload):
        try:
            document = json.loads(payload)
        except (TypeError, ValueError):
            return
        eval_in_emacs(
            "eaf-ebook-viewer-set-document-text",
            [self.buffer_id, json.dumps(document, ensure_ascii=False)],
        )

    @pyqtSlot(str)
    def word_clicked(self, payload):
        try:
            context = json.loads(payload)
        except (TypeError, ValueError):
            return
        context["word"] = " ".join((context.get("word") or "").split())
        context["context"] = " ".join(
            (context.get("context") or "").split()
        )
        context["book"] = {"path": self.url}
        eval_in_emacs(
            "eaf-ebook-viewer-notify-word-clicked",
            [self.buffer_id, json.dumps(context, ensure_ascii=False)],
        )

    @pyqtSlot(str)
    def text_highlights_rendered(self, payload):
        try:
            status = json.loads(payload)
        except (TypeError, ValueError):
            return
        eval_in_emacs(
            "eaf-ebook-viewer-set-text-highlight-status",
            [self.buffer_id, json.dumps(status, ensure_ascii=False)],
        )

    def _request_text_context(self):
        if self.viewer is None:
            self._send_text_context(
                {"source": "mouse", "word": "", "context": ""}
            )
            return
        web_view = self.viewer.web_view
        point = web_view.mapFromGlobal(QCursor.pos())
        web_view.page().runjs(
            "window.eafEbookTextContextAtPoint({}, {})".format(
                point.x(), point.y()
            )
        )

    @interactive(insert_or_do=True)
    def copy_select(self):
        self._request_text_context()

    @interactive(insert_or_do=True)
    def create_highlight(self):
        if self.viewer is not None:
            self.viewer.web_view.page().runjs(
                "window.eafEbookCreateHighlight && "
                "window.eafEbookCreateHighlight()"
            )

    @interactive
    @PostGui()
    def delete_highlight(self, uuid):
        """Delete Calibre's native highlight identified by UUID."""
        if (
            self.viewer is not None
            and uuid
            and self._annotation_events_ready
            and self._annotations_map() is not None
        ):
            self.activate_context()
            annotation = self._annotation_by_uuid(uuid)
            if annotation is None:
                self._send_annotation_removed(
                    {"uuid": uuid, "removed": True}
                )
            else:
                # WebView.highlight_action() forcibly focuses the embedded
                # WebEngine view after dispatch. That native focus change can
                # crash the EAF process on macOS, so use Calibre's same bridge
                # action without the QWidget focus side effect.
                self.viewer.web_view.execute_when_ready(
                    "highlight_action", uuid, "delete"
                )

    @interactive
    @PostGui()
    def set_highlight_notes(self, uuid, notes):
        """Set plain-text NOTES on Calibre's native highlight UUID."""
        if (
            self._annotation_events_ready
            and self._annotation_by_uuid(uuid) is not None
        ):
            self.activate_context()
            self.viewer.web_view.generic_action(
                "set-notes-in-highlight",
                {"uuid": uuid, "notes": notes or ""},
            )

    @interactive(insert_or_do=True)
    def scroll_down(self):
        self._shortcut("down")

    @interactive(insert_or_do=True)
    def scroll_up(self):
        self._shortcut("up")

    @interactive(insert_or_do=True)
    def next_page(self):
        self._shortcut("pagedown")

    @interactive(insert_or_do=True)
    def previous_page(self):
        self._shortcut("pageup")

    @interactive(insert_or_do=True)
    def scroll_left(self):
        self._shortcut("left")

    @interactive(insert_or_do=True)
    def scroll_right(self):
        self._shortcut("right")

    @interactive(insert_or_do=True)
    def next_section(self):
        self._shortcut("next_section")

    @interactive(insert_or_do=True)
    def previous_section(self):
        self._shortcut("previous_section")

    @interactive(insert_or_do=True)
    def history_back(self):
        self._shortcut("back")

    @interactive(insert_or_do=True)
    def history_forward(self):
        self._shortcut("forward")

    @interactive(insert_or_do=True)
    def start_of_file(self):
        self._shortcut("start_of_file")

    @interactive(insert_or_do=True)
    def end_of_file(self):
        self._shortcut("end_of_file")

    @interactive(insert_or_do=True)
    def start_of_book(self):
        self._shortcut("start_of_book")

    @interactive(insert_or_do=True)
    def end_of_book(self):
        self._shortcut("end_of_book")

    @interactive(insert_or_do=True)
    def increase_font_size(self):
        self._shortcut("increase_font_size")

    @interactive(insert_or_do=True)
    def decrease_font_size(self):
        self._shortcut("decrease_font_size")

    @interactive(insert_or_do=True)
    def default_font_size(self):
        self._shortcut("default_font_size")

    @interactive(insert_or_do=True)
    def increase_number_of_columns(self):
        self._shortcut("increase_number_of_columns")

    @interactive(insert_or_do=True)
    def decrease_number_of_columns(self):
        self._shortcut("decrease_number_of_columns")

    @interactive(insert_or_do=True)
    def reset_number_of_columns(self):
        self._shortcut("reset_number_of_columns")

    @interactive(insert_or_do=True)
    def toggle_paged_mode(self):
        self._shortcut("toggle_paged_mode")

    @interactive(insert_or_do=True)
    def toggle_scrollbar(self):
        self._shortcut("toggle_scrollbar")

    @interactive(insert_or_do=True)
    def toggle_reference_mode(self):
        self._shortcut("toggle_reference_mode")

    @interactive(insert_or_do=True)
    def toggle_autoscroll(self):
        self._shortcut("toggle_autoscroll")

    @interactive(insert_or_do=True)
    def increase_autoscroll_speed(self):
        self._shortcut("scrollspeed_increase")

    @interactive(insert_or_do=True)
    def decrease_autoscroll_speed(self):
        self._shortcut("scrollspeed_decrease")

    @interactive(insert_or_do=True)
    def toggle_toc(self):
        self._shortcut("toggle_toc")

    @interactive(insert_or_do=True)
    def show_search(self):
        self._shortcut("start_search")

    @interactive(insert_or_do=True)
    def find_next(self):
        self._shortcut("next_match")

    @interactive(insert_or_do=True)
    def find_previous(self):
        self._shortcut("previous_match")

    @interactive(insert_or_do=True)
    def new_bookmark(self):
        self._shortcut("new_bookmark")

    @interactive(insert_or_do=True)
    def toggle_bookmarks(self):
        self._shortcut("toggle_bookmarks")

    @interactive(insert_or_do=True)
    def toggle_highlights(self):
        self._shortcut("toggle_highlights")

    @interactive(insert_or_do=True)
    def toggle_lookup(self):
        self._shortcut("toggle_lookup")

    @interactive(insert_or_do=True)
    def show_metadata(self):
        self._shortcut("metadata")

    @interactive(insert_or_do=True)
    def show_profiles(self):
        self._shortcut("show_profiles")

    @interactive(insert_or_do=True)
    def show_preferences(self):
        self._shortcut("preferences")

    @interactive(insert_or_do=True)
    def goto_location(self):
        self._shortcut("goto_location")

    @interactive(insert_or_do=True)
    def show_controls(self):
        self._shortcut("show_chrome")

    @interactive(insert_or_do=True)
    def copy_location(self):
        self._shortcut("copy_location_to_clipboard")

    @interactive(insert_or_do=True)
    def copy_location_as_url(self):
        self._shortcut("copy_location_as_url_to_clipboard")

    @interactive(insert_or_do=True)
    def select_all(self):
        self._shortcut("select_all")

    @interactive(insert_or_do=True)
    def toggle_hints(self):
        self._shortcut("toggle_hints")

    @interactive(insert_or_do=True)
    def read_aloud(self):
        self._shortcut("read_aloud")

    @interactive(insert_or_do=True)
    def reload_book(self):
        if self.viewer is not None:
            self.activate_context()
            self.viewer.reload_book()

    @interactive(insert_or_do=True)
    def toggle_fullscreen(self):
        """Toggle EAF fullscreen, or type the invoking key in an editor."""
        super().toggle_fullscreen()

    @interactive(insert_or_do=True)
    def update_theme(self):
        if self.viewer is not None:
            self.activate_context()
            self.viewer.web_view.palette_changed()

    def get_key_event_widgets(self):
        if self._key_event_widget is not None:
            if self._key_event_widget is self.viewer.web_view:
                return [self.viewer.web_view.focusProxy()]
            return [self._key_event_widget]
        if self.viewer is not None:
            # Match EAF BrowserBuffer: QWebEngineView accepts synthetic key
            # events through its focus proxy, not the outer view widget.
            return [self.viewer.web_view.focusProxy()]
        return [self.buffer_widget]

    @staticmethod
    def _key_event_parts(event_string):
        """Return Qt key data for one Emacs key description."""
        parts = event_string.split("-")
        modifiers = Qt.KeyboardModifier.NoModifier
        while len(parts) > 1 and parts[0] in QT_MODIFIER_DICT:
            modifiers |= QT_MODIFIER_DICT[parts.pop(0)]

        key_name = "-".join(parts)
        lookup_name = key_name.lower() if len(key_name) == 1 else key_name
        if (
            modifiers == Qt.KeyboardModifier.NoModifier
            and (
                key_name == "<backtab>"
                or (len(key_name) == 1 and key_name.isupper())
            )
        ):
            modifiers = Qt.KeyboardModifier.ShiftModifier

        text = QT_TEXT_DICT.get(key_name, key_name)
        return QT_KEY_DICT.get(lookup_name, Qt.Key.Key_unknown), modifiers, text

    def _forward_key_events(self, event_string):
        """Forward an Emacs key description to the active Calibre editor."""
        widgets = self.get_key_event_widgets()
        for chord in event_string.split():
            key, modifiers, text = self._key_event_parts(chord)
            for widget in widgets:
                post_event(
                    widget,
                    QKeyEvent(QEvent.Type.KeyPress, key, modifiers, text),
                )
        self.send_key_filter(event_string)

    @PostGui()
    def send_key(self, event_string):
        """Forward single, modified, or multi-key Emacs input."""
        self._forward_key_events(event_string)

    @PostGui()
    def send_key_sequence(self, event_string):
        """Use the same active-editor routing for explicit key sequences."""
        self._forward_key_events(event_string)

    def all_views_hide(self):
        pass

    def some_view_show(self):
        self.activate_context()

    def save_session_data(self):
        # Calibre persists CFI/annotations itself.
        return ""

    def restore_session_data(self, session_data):
        self.activate_context()

    def scroll_other_buffer(self, scroll_direction, scroll_type):
        self._shortcut("pagedown" if scroll_direction == "up" else "pageup")

    def send_key_filter(self, event_string):
        if self.viewer is not None:
            # A key can create or close Calibre's inline highlight editor
            # (H, Escape, Ctrl+Enter). Refresh after WebEngine has dispatched
            # it so Emacs' insert-or-command state follows activeElement.
            QTimer.singleShot(50, self._refresh_web_input_focus)

    def action_quit(self):
        self.close_buffer()

    def destroy_buffer(self):
        QApplication.instance().removeEventFilter(self)
        if self.viewer is not None:
            self.activate_context()
            try:
                self.viewer.force_close()
            except RuntimeError:
                pass
        super().destroy_buffer()
