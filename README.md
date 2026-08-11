### EAF Ebook Viewer

Ebook Viewer application for the [Emacs Application Framework](https://github.com/emacs-eaf/emacs-application-framework), powered by calibre's native E-book Viewer.

It supports EPUB, MOBI, AZW3, FB2 and other formats supported by calibre. Books are opened from a read-only local cache, so the original file is not modified.

### Load application

Install [EAF](https://github.com/emacs-eaf/emacs-application-framework#install) and [calibre](https://calibre-ebook.com/download) first, then clone this repository into EAF's `app` directory:

```Shell
git clone https://github.com/chenyanming/eaf-ebook-viewer.git \
  ~/.emacs.d/site-lisp/emacs-application-framework/app/ebook-viewer
```

Add the application directory and configuration to Emacs:

```Elisp
(add-to-list 'load-path
             "~/.emacs.d/site-lisp/emacs-application-framework/app/ebook-viewer")
(require 'eaf)
(require 'eaf-ebook-viewer)
(eaf-ebook-viewer-use-calibre-runtime)
```

On Linux, point the launcher to `calibre-debug` before EAF starts:

```Elisp
(setenv "EAF_CALIBRE_DEBUG" (executable-find "calibre-debug"))
```

Open a supported ebook with `find-file` or `eaf-open`.

### Keybindings

| Key | Event |
| :-- | :---- |
| `j` / `k` | Scroll down / up |
| `d` / `u` | Next / previous page |
| `h` / `l` | Previous / next section |
| `[[` / `]]` | Previous / next section |
| `H` / `L` | Back / forward |
| `g g` / `G` | Start / end of book |
| `t` / `T` | Toggle table of contents |
| `/` | Search |
| `n` / `N` | Next / previous match |
| `m` | Add bookmark |
| `b` | Toggle bookmarks |
| `a` | Toggle highlights |
| `y` | Copy selected text to Emacs |
| `+` / `-` / `0` | Change / reset font size |
| `p` | Toggle paged mode |
| `R` | Read aloud |
| `r` | Reload book |
| `f` | Toggle fullscreen |
| `x` / `q` | Close buffer |
