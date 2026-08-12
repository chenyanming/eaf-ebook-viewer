#!/usr/bin/env python3
"""Calibre E-book Viewer adapter for EAF.

The renderer, pagination, navigation, annotations and format conversion all
come from calibre.  This module only adapts calibre's QMainWindow to an EAF
Buffer and gives every book an isolated WebEngine profile/content handler.
"""

from __future__ import annotations

import hashlib
import os
import platform
import shutil
import sys
import tempfile
import traceback
import zipfile
from uuid import uuid4

from PyQt6.QtCore import QEvent, QTimer
from PyQt6.QtWidgets import QApplication, QLabel, QWidget

from core.buffer import Buffer
from core.utils import (
    eval_in_emacs,
    focus_emacs_buffer,
    interactive,
    message_to_emacs,
)

_APP_DIR = os.path.dirname(os.path.abspath(__file__))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from calibre_bootstrap import initialize as initialize_calibre


_PROFILE_HANDLERS = {}
_ZIP_EBOOK_EXTENSIONS = {".epub", ".fbz", ".htmlz", ".txtz"}


def _activate_emacs_after_mouse_release():
    """Return keyboard focus to Emacs across old and new EAF versions."""
    tracker = getattr(QApplication.instance(), "macos_window_tracker", None)
    if tracker is not None:
        tracker.activate_emacs_after_mouse_release()
    else:
        # Older EAF releases have no native macOS window tracker.  This
        # function has been part of EAF for much longer and is also what its
        # PDF viewer uses after completing a mouse selection.
        eval_in_emacs("eaf-activate-emacs-window", [])


def _handle_webengine_mouse_event(buffer_id, event):
    """Integrate calibre's foreign WebEngine view with EAF input handling."""
    focus_event_types = (
        QEvent.Type.MouseButtonPress,
        QEvent.Type.MouseButtonRelease,
        QEvent.Type.MouseButtonDblClick,
    )
    if platform.system() != "Darwin":
        focus_event_types += (QEvent.Type.Wheel,)

    if (
        event.type() == QEvent.Type.MouseButtonRelease
        and platform.system() == "Darwin"
    ):
        # Let Qt finish its native text-selection gesture before moving the
        # keyboard focus.  New EAF verifies the foreground process through
        # its tracker; old EAF falls back to its established Emacs helper.
        QTimer.singleShot(50, _activate_emacs_after_mouse_release)

    if event.type() in focus_event_types:
        focus_emacs_buffer(buffer_id)


def _is_cloud_storage_path(path):
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


def _stage_book(path):
    """Copy a book into a read-only local cache before calibre sees it."""
    source = os.path.abspath(path)
    source_stat = os.stat(source)
    extension = os.path.splitext(source)[1].lower()
    fingerprint = "\0".join(
        (source, str(source_stat.st_size), str(source_stat.st_mtime_ns))
    ).encode("utf-8")
    cache_key = hashlib.sha256(fingerprint).hexdigest()
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
    cache_dir = os.path.join(
        cache_root, "eaf-ebook-viewer", "books", cache_key[:2]
    )
    os.makedirs(cache_dir, exist_ok=True)
    cached_path = os.path.join(
        cache_dir, cache_key + extension
    )

    if os.path.isfile(cached_path):
        try:
            _validate_zip_ebook(cached_path)
            os.chmod(cached_path, 0o444)
            return cached_path
        except RuntimeError:
            # A prior interrupted download left a bad cache entry.  Replace it
            # atomically after copying into a separate temporary file.
            pass

    if _is_cloud_storage_path(source):
        message_to_emacs("Downloading cloud e-book to the local EAF cache...")
    else:
        message_to_emacs("Preparing a read-only local copy for EAF...")
    descriptor, temporary_path = tempfile.mkstemp(
        prefix=cache_key + ".", suffix=".download", dir=cache_dir
    )
    try:
        with os.fdopen(descriptor, "wb") as destination:
            with open(source, "rb") as cloud_file:
                shutil.copyfileobj(cloud_file, destination, length=4 * 1024 * 1024)
            destination.flush()
            os.fsync(destination.fileno())

        final_stat = os.stat(source)
        if (
            final_stat.st_size != source_stat.st_size
            or final_stat.st_mtime_ns != source_stat.st_mtime_ns
        ):
            raise RuntimeError(
                "Google Drive changed the e-book while it was downloading. "
                "Wait for sync to finish, then reopen it: {}".format(source)
            )
        if os.path.getsize(temporary_path) != source_stat.st_size:
            raise RuntimeError(
                "Google Drive returned only part of the e-book. In Finder, "
                "choose Download Now and reopen it: {}".format(source)
            )

        _validate_zip_ebook(temporary_path, extension)
        os.replace(temporary_path, cached_path)
        os.chmod(cached_path, 0o444)
        return cached_path
    finally:
        try:
            os.unlink(temporary_path)
        except FileNotFoundError:
            pass


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
        self._last_selected_text = ""

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
            def load_finished(self, ok, data):
                if ok:
                    buffer.book_handler.set_book(
                        data["base"], data["pathtoebook"]
                    )
                super().load_finished(ok, data)
                if ok:
                    buffer.activate_context()

        viewer = self.viewer = EmbeddedEbookViewer()
        viewer.setWindowFlags(Qt.WindowType.Widget)
        profile = viewer.web_view.page().profile()
        self.book_handler = _PROFILE_HANDLERS[id(profile)]

        self.add_widget(viewer)
        self._install_webengine_event_filters()
        viewer.windowTitleChanged.connect(self.change_title)
        viewer.web_view.selection_changed.connect(self._selection_changed)

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
        viewer.load_ebook(book_path)

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
        if event.type() in (QEvent.Type.ChildAdded, QEvent.Type.ChildPolished):
            child = event.child()
            if isinstance(child, QWidget):
                child.installEventFilter(self)
        if event.type() in (
            QEvent.Type.FocusIn,
            QEvent.Type.KeyPress,
            QEvent.Type.MouseButtonPress,
            QEvent.Type.Wheel,
        ):
            self.activate_context()
        _handle_webengine_mouse_event(self.buffer_id, event)
        return False

    def _install_webengine_event_filters(self):
        web_view = self.viewer.web_view
        web_view.installEventFilter(self)
        for child in web_view.findChildren(QWidget):
            child.installEventFilter(self)

    def _shortcut(self, action):
        if self.viewer is not None:
            self.activate_context()
            self.viewer.web_view.trigger_shortcut(action)

    def _selection_changed(self, text, annotation_data):
        del annotation_data
        self._last_selected_text = text or ""

    def selected_text(self):
        if self.viewer is None:
            return ["", self._last_selected_text]
        return [self.viewer.web_view.selectedText(), self._last_selected_text]

    @interactive
    def copy_select(self):
        current, last = self.selected_text()
        text = current or last
        if text:
            eval_in_emacs("kill-new", [text])
            message_to_emacs(text)

    @interactive
    def scroll_down(self):
        self._shortcut("down")

    @interactive
    def scroll_up(self):
        self._shortcut("up")

    @interactive
    def next_page(self):
        self._shortcut("pagedown")

    @interactive
    def previous_page(self):
        self._shortcut("pageup")

    @interactive
    def scroll_left(self):
        self._shortcut("left")

    @interactive
    def scroll_right(self):
        self._shortcut("right")

    @interactive
    def next_section(self):
        self._shortcut("next_section")

    @interactive
    def previous_section(self):
        self._shortcut("previous_section")

    @interactive
    def history_back(self):
        self._shortcut("back")

    @interactive
    def history_forward(self):
        self._shortcut("forward")

    @interactive
    def start_of_file(self):
        self._shortcut("start_of_file")

    @interactive
    def end_of_file(self):
        self._shortcut("end_of_file")

    @interactive
    def start_of_book(self):
        self._shortcut("start_of_book")

    @interactive
    def end_of_book(self):
        self._shortcut("end_of_book")

    @interactive
    def increase_font_size(self):
        self._shortcut("increase_font_size")

    @interactive
    def decrease_font_size(self):
        self._shortcut("decrease_font_size")

    @interactive
    def default_font_size(self):
        self._shortcut("default_font_size")

    @interactive
    def increase_number_of_columns(self):
        self._shortcut("increase_number_of_columns")

    @interactive
    def decrease_number_of_columns(self):
        self._shortcut("decrease_number_of_columns")

    @interactive
    def reset_number_of_columns(self):
        self._shortcut("reset_number_of_columns")

    @interactive
    def toggle_paged_mode(self):
        self._shortcut("toggle_paged_mode")

    @interactive
    def toggle_scrollbar(self):
        self._shortcut("toggle_scrollbar")

    @interactive
    def toggle_reference_mode(self):
        self._shortcut("toggle_reference_mode")

    @interactive
    def toggle_autoscroll(self):
        self._shortcut("toggle_autoscroll")

    @interactive
    def increase_autoscroll_speed(self):
        self._shortcut("scrollspeed_increase")

    @interactive
    def decrease_autoscroll_speed(self):
        self._shortcut("scrollspeed_decrease")

    @interactive
    def toggle_toc(self):
        self._shortcut("toggle_toc")

    @interactive
    def show_search(self):
        self._shortcut("start_search")

    @interactive
    def find_next(self):
        self._shortcut("next_match")

    @interactive
    def find_previous(self):
        self._shortcut("previous_match")

    @interactive
    def new_bookmark(self):
        self._shortcut("new_bookmark")

    @interactive
    def toggle_bookmarks(self):
        self._shortcut("toggle_bookmarks")

    @interactive
    def toggle_highlights(self):
        self._shortcut("toggle_highlights")

    @interactive
    def toggle_lookup(self):
        self._shortcut("toggle_lookup")

    @interactive
    def show_metadata(self):
        self._shortcut("metadata")

    @interactive
    def show_profiles(self):
        self._shortcut("show_profiles")

    @interactive
    def show_preferences(self):
        self._shortcut("preferences")

    @interactive
    def goto_location(self):
        self._shortcut("goto_location")

    @interactive
    def show_controls(self):
        self._shortcut("show_chrome")

    @interactive
    def copy_location(self):
        self._shortcut("copy_location_to_clipboard")

    @interactive
    def copy_location_as_url(self):
        self._shortcut("copy_location_as_url_to_clipboard")

    @interactive
    def select_all(self):
        self._shortcut("select_all")

    @interactive
    def toggle_hints(self):
        self._shortcut("toggle_hints")

    @interactive
    def read_aloud(self):
        self._shortcut("read_aloud")

    @interactive
    def reload_book(self):
        if self.viewer is not None:
            self.activate_context()
            self.viewer.reload_book()

    @interactive
    def update_theme(self):
        if self.viewer is not None:
            self.activate_context()
            self.viewer.web_view.palette_changed()

    def get_key_event_widgets(self):
        if self.viewer is not None:
            return [self.viewer.web_view]
        return [self.buffer_widget]

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
        pass

    def action_quit(self):
        self.close_buffer()

    def destroy_buffer(self):
        if self.viewer is not None:
            self.activate_context()
            try:
                self.viewer.force_close()
            except RuntimeError:
                pass
        super().destroy_buffer()
