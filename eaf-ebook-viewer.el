;;; eaf-ebook-viewer.el --- Calibre E-book Viewer for EAF -*- lexical-binding: t; -*-

;; Copyright (C) 2026 Damon Chan
;; SPDX-License-Identifier: GPL-3.0-or-later

;;; Commentary:

;; Embed calibre's Qt E-book Viewer in EAF.  The Python side must run inside
;; calibre's bundled Python runtime; `eaf-ebook-viewer-python-command' points
;; at the small launcher shipped with this application.

;;; Code:

(defgroup eaf-ebook-viewer nil
  "Calibre E-book Viewer embedded in EAF."
  :group 'eaf)

(defcustom eaf-ebook-viewer-extension-list
  '("epub" "kepub" "mobi" "azw" "azw3" "azw4" "prc" "pobi"
    "fb2" "fbz" "lit" "lrf" "pdb" "rb" "snb" "tcr" "txtz" "htmlz")
  "E-book extensions handled by the Calibre-backed EAF viewer."
  :type '(repeat string)
  :group 'eaf-ebook-viewer)

(defcustom eaf-ebook-viewer-keybinding
  '(;; NOV/EAF-style navigation.
    ("j" . "scroll_down")
    ("k" . "scroll_up")
    ("<down>" . "scroll_down")
    ("<up>" . "scroll_up")
    ("<left>" . "scroll_left")
    ("<right>" . "scroll_right")
    ("C-n" . "scroll_down")
    ("C-p" . "scroll_up")
    ("d" . "next_page")
    ("u" . "previous_page")
    ("SPC" . "next_page")
    ("S-SPC" . "previous_page")
    ("C-v" . "next_page")
    ("M-v" . "previous_page")
    ("l" . "next_section")
    ("h" . "previous_section")
    ("]]" . "next_section")
    ("[[" . "previous_section")
    ("H" . "history_back")
    ("L" . "history_forward")
    ("g b" . "history_back")
    ("g f" . "history_forward")
    ("g g" . "start_of_book")
    ("G" . "end_of_book")
    ("<home>" . "start_of_file")
    ("<end>" . "end_of_file")

    ;; Reading layout.
    ("+" . "increase_font_size")
    ("=" . "increase_font_size")
    ("-" . "decrease_font_size")
    ("0" . "default_font_size")
    ("M-]" . "increase_number_of_columns")
    ("M-[" . "decrease_number_of_columns")
    ("M-0" . "reset_number_of_columns")
    ("p" . "toggle_paged_mode")
    ("s" . "toggle_scrollbar")
    ("v" . "toggle_reference_mode")
    ("A" . "toggle_autoscroll")
    (">" . "increase_autoscroll_speed")
    ("<" . "decrease_autoscroll_speed")

    ;; Panels and book information.  Both t and T intentionally match NOV.
    ("t" . "toggle_toc")
    ("T" . "toggle_toc")
    ("g t" . "toggle_toc")
    ("g T" . "toggle_toc")
    ("b" . "toggle_bookmarks")
    ("m" . "new_bookmark")
    ("a" . "toggle_highlights")
    ("D" . "toggle_lookup")
    ("g m" . "show_metadata")
    ("g p" . "show_profiles")
    ("," . "show_preferences")
    (";" . "goto_location")
    ("<escape>" . "show_controls")

    ;; Search, selection and utility actions.
    ("/" . "show_search")
    ("C-s" . "show_search")
    ("n" . "find_next")
    ("N" . "find_previous")
    ("y" . "copy_select")
    ("M-w" . "copy_select")
    ("Y" . "copy_location")
    ("g y" . "copy_location_as_url")
    ("C-a" . "select_all")
    ("?" . "toggle_hints")
    ("R" . "read_aloud")
    ("r" . "reload_book")
    ("g r" . "reload_book")
    ("f" . "toggle_fullscreen")
    ("<f11>" . "toggle_fullscreen")
    ("x" . "close_buffer")
    ("q" . "close_buffer"))
  "Keybindings for the Calibre-backed EAF E-book Viewer."
  :type '(alist :key-type string :value-type string)
  :group 'eaf-ebook-viewer)

(defconst eaf-ebook-viewer--directory
  (file-name-directory (or load-file-name buffer-file-name))
  "Directory containing the EAF E-book Viewer application.")

(defcustom eaf-ebook-viewer-python-command
  (expand-file-name "eaf-calibre-python" eaf-ebook-viewer--directory)
  "Launcher that runs the EAF backend in calibre's Python runtime."
  :type 'file
  :group 'eaf-ebook-viewer)

(defun eaf-ebook-viewer-use-calibre-runtime ()
  "Configure EAF to use calibre's bundled Python and Qt runtime.

Call this before the EAF Python process starts.  If EAF is already running,
use `eaf-restart-process' after changing the command."
  (interactive)
  (setq eaf-python-command eaf-ebook-viewer-python-command))

(add-to-list 'eaf-app-binding-alist
             '("ebook-viewer" . eaf-ebook-viewer-keybinding))

(defvar eaf-ebook-viewer-module-path
  (expand-file-name "buffer.py" eaf-ebook-viewer--directory))
(add-to-list 'eaf-app-module-path-alist
             '("ebook-viewer" . eaf-ebook-viewer-module-path))

(add-to-list 'eaf-app-extensions-alist
             '("ebook-viewer" . eaf-ebook-viewer-extension-list))

(provide 'eaf-ebook-viewer)
;;; eaf-ebook-viewer.el ends here
