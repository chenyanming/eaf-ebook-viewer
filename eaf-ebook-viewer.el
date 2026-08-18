;;; eaf-ebook-viewer.el --- Calibre E-book Viewer for EAF -*- lexical-binding: t; -*-

;; Copyright (C) 2026 Damon Chan
;; SPDX-License-Identifier: GPL-3.0-or-later

;;; Commentary:

;; Embed calibre's Qt E-book Viewer in EAF.  Calibre's Python source is
;; vendored with this application and runs in EAF's ordinary Python process.

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
    ("j" . "insert_or_scroll_down")
    ("k" . "insert_or_scroll_up")
    ("<down>" . "insert_or_scroll_down")
    ("<up>" . "insert_or_scroll_up")
    ("<left>" . "insert_or_scroll_left")
    ("<right>" . "insert_or_scroll_right")
    ("C-n" . "insert_or_scroll_down")
    ("C-p" . "insert_or_scroll_up")
    ("d" . "insert_or_next_page")
    ("u" . "insert_or_previous_page")
    ("SPC" . "insert_or_next_page")
    ("S-SPC" . "insert_or_previous_page")
    ("C-v" . "insert_or_next_page")
    ("M-v" . "insert_or_previous_page")
    ("l" . "insert_or_next_section")
    ("h" . "insert_or_previous_section")
    ("]]" . "insert_or_next_section")
    ("[[" . "insert_or_previous_section")
    ("H" . "insert_or_history_back")
    ("L" . "insert_or_history_forward")
    ("g b" . "insert_or_history_back")
    ("g f" . "insert_or_history_forward")
    ("g g" . "insert_or_start_of_book")
    ("G" . "insert_or_end_of_book")
    ("<home>" . "insert_or_start_of_file")
    ("<end>" . "insert_or_end_of_file")

    ;; Reading layout.
    ("+" . "insert_or_increase_font_size")
    ("=" . "insert_or_increase_font_size")
    ("-" . "insert_or_decrease_font_size")
    ("0" . "insert_or_default_font_size")
    ("M-]" . "insert_or_increase_number_of_columns")
    ("M-[" . "insert_or_decrease_number_of_columns")
    ("M-0" . "insert_or_reset_number_of_columns")
    ("p" . "insert_or_toggle_paged_mode")
    ("s" . "insert_or_toggle_scrollbar")
    ("v" . "insert_or_toggle_reference_mode")
    ("A" . "insert_or_toggle_autoscroll")
    (">" . "insert_or_increase_autoscroll_speed")
    ("<" . "insert_or_decrease_autoscroll_speed")

    ;; Panels and book information.  Both t and T intentionally match NOV.
    ("t" . "insert_or_toggle_toc")
    ("T" . "insert_or_toggle_toc")
    ("g t" . "insert_or_toggle_toc")
    ("g T" . "insert_or_toggle_toc")
    ("b" . "insert_or_toggle_bookmarks")
    ("m" . "insert_or_new_bookmark")
    ("a" . "insert_or_toggle_highlights")
    ("D" . "insert_or_toggle_lookup")
    ("g m" . "insert_or_show_metadata")
    ("g p" . "insert_or_show_profiles")
    ("," . "insert_or_show_preferences")
    (";" . "insert_or_goto_location")
    ("<escape>" . "insert_or_show_controls")

    ;; Search, selection and utility actions.
    ("/" . "insert_or_show_search")
    ("C-s" . "insert_or_show_search")
    ("n" . "insert_or_find_next")
    ("N" . "insert_or_find_previous")
    ("y" . "insert_or_copy_select")
    ("M-w" . "insert_or_copy_select")
    ("Y" . "insert_or_copy_location")
    ("g y" . "insert_or_copy_location_as_url")
    ("C-a" . "insert_or_select_all")
    ("?" . "insert_or_toggle_hints")
    ("R" . "insert_or_read_aloud")
    ("r" . "insert_or_reload_book")
    ("g r" . "insert_or_reload_book")
    ("f" . "insert_or_toggle_fullscreen")
    ("<f11>" . "insert_or_toggle_fullscreen")
    ("x" . "insert_or_close_buffer"))
  "Keybindings for the Calibre-backed EAF E-book Viewer."
  :type '(alist :key-type string :value-type string)
  :group 'eaf-ebook-viewer)

(defconst eaf-ebook-viewer--directory
  (file-name-directory (or load-file-name buffer-file-name))
  "Directory containing the EAF E-book Viewer application.")

(defvar eaf-ebook-viewer-input-mode-map (make-sparse-keymap)
  "Keymap active while a Calibre text editor has focus.")

;; EAF buffers contain a fallback image whose text-property map includes an
;; `i' prefix.  Give actual text input a complete pass-through map so neither
;; that map nor a reader binding can capture characters meant for an editor.
(define-key eaf-ebook-viewer-input-mode-map
            [remap self-insert-command] #'eaf-send-key)
(dotimes (offset 95)
  (define-key eaf-ebook-viewer-input-mode-map
              (vector (+ 32 offset)) #'eaf-send-key))
(dolist (key '("RET" "DEL" "TAB" "<backtab>" "<home>" "<end>"
               "<left>" "<right>" "<up>" "<down>" "<prior>" "<next>"
               "<delete>" "<backspace>" "<return>" "<escape>"))
  (define-key eaf-ebook-viewer-input-mode-map (kbd key) #'eaf-send-key))

(defvar-local eaf-ebook-viewer--input-map-active nil)
(defvar-local eaf-ebook-viewer--saved-overriding-local-map nil)

(defun eaf-ebook-viewer-update-focus-state (buffer-id state)
  "Update editing STATE for Ebook Viewer BUFFER-ID."
  (when-let ((buffer (eaf-get-buffer buffer-id)))
    (with-current-buffer buffer
      (setq-local eaf-buffer-input-focus state)
      (cond
       ((and state (not eaf-ebook-viewer--input-map-active))
        (setq-local eaf-ebook-viewer--saved-overriding-local-map
                    overriding-local-map)
        (setq-local overriding-local-map
                    (make-composed-keymap
                     (delq nil
                           (list eaf-ebook-viewer-input-mode-map
                                 eaf-ebook-viewer--saved-overriding-local-map))
                     (or (cdr (assq t eaf--buffer-map-alist))
                         (current-local-map))))
        (setq-local eaf-ebook-viewer--input-map-active t))
       ((and (not state) eaf-ebook-viewer--input-map-active)
        (setq-local overriding-local-map
                    eaf-ebook-viewer--saved-overriding-local-map)
        (setq-local eaf-ebook-viewer--saved-overriding-local-map nil)
        (setq-local eaf-ebook-viewer--input-map-active nil))))))

(defun eaf-ebook-viewer--add-python-path ()
  "Expose the app bootstrap to EAF's unchanged Python process."
  (let* ((old (getenv "PYTHONPATH"))
         (paths (and old (split-string old path-separator t))))
    (unless (member eaf-ebook-viewer--directory paths)
      (setenv "PYTHONPATH"
              (mapconcat #'identity
                         (cons eaf-ebook-viewer--directory paths)
                         path-separator)))))

(eaf-ebook-viewer--add-python-path)

;; The keybinding uses the input-aware wrapper, while integrations need to
;; request the selection unconditionally through `eaf-execute-app-cmd'.
(eaf--make-py-proxy-function "copy_select")

(defvar-local eaf-ebook-viewer-text-context nil
  "Latest text context returned by Ebook Viewer.")
(defvar-local eaf-ebook-viewer-text-context-ready nil
  "Non-nil after the latest text-context request has completed.")
(defvar-local eaf-ebook-viewer-book-metadata nil
  "Metadata parsed by Calibre for the current book.")
(defvar-local eaf-ebook-viewer-book-manifest nil
  "Book structure manifest generated by Calibre for the current book.")

(defvar eaf-ebook-viewer-word-clicked-hook nil
  "Hook run with text context after a book-content word is clicked.")

(defun eaf-ebook-viewer--parse-json (payload)
  "Parse Ebook Viewer JSON PAYLOAD into an alist."
  (json-parse-string payload
                     :object-type 'alist
                     :array-type 'array))

(defun eaf-ebook-viewer-set-book-data (buffer-id metadata manifest)
  "Store Calibre METADATA and MANIFEST for Ebook Viewer BUFFER-ID."
  (when-let ((buffer (eaf-get-buffer buffer-id)))
    (with-current-buffer buffer
      (setq eaf-ebook-viewer-book-metadata
            (eaf-ebook-viewer--parse-json metadata)
            eaf-ebook-viewer-book-manifest
            (eaf-ebook-viewer--parse-json manifest)))))

(defun eaf-ebook-viewer-notify-word-clicked (buffer-id payload)
  "Notify BUFFER-ID that a word was clicked with context PAYLOAD."
  (when-let ((buffer (eaf-get-buffer buffer-id)))
    (with-current-buffer buffer
      (let ((context (eaf-ebook-viewer--parse-json payload)))
        (setq eaf-ebook-viewer-text-context context
              eaf-ebook-viewer-text-context-ready t)
        (run-hook-with-args 'eaf-ebook-viewer-word-clicked-hook context)))))

(defun eaf-ebook-viewer-set-text-context (buffer-id payload)
  "Store the text context returned by Ebook Viewer BUFFER-ID."
  (when-let ((buffer (eaf-get-buffer buffer-id)))
    (with-current-buffer buffer
      (setq eaf-ebook-viewer-text-context
            (eaf-ebook-viewer--parse-json payload)
            eaf-ebook-viewer-text-context-ready t))))

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
