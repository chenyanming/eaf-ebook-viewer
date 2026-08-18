### EAF Ebook Viewer

Ebook Viewer application for the [Emacs Application Framework](https://github.com/emacs-eaf/emacs-application-framework), powered by calibre's native E-book Viewer.

It supports EPUB, MOBI, AZW3, FB2 and other formats supported by calibre. Local books are opened directly. Books stored by a macOS cloud file provider are downloaded to a read-only local cache first.

### Demo

<img width="720" src="./demo.gif">

### Load application

Install [EAF](https://github.com/emacs-eaf/emacs-application-framework#install), then install this app with EAF's installer.

On macOS, install the compiler dependency first:

```Shell
brew install icu4c
```

On Linux, EAF installs the compiler and ICU development packages listed in
`dependencies.json` for Debian/Ubuntu, Fedora and Arch Linux. On other
distributions, install a C/C++ compiler and the ICU development package first.

```Shell
git clone --recurse-submodules https://github.com/chenyanming/eaf-ebook-viewer.git \
  ~/.emacs.d/site-lisp/emacs-application-framework/app/ebook-viewer
cd ~/.emacs.d/site-lisp/emacs-application-framework
./install-eaf.py --install ebook-viewer --force
```

Add the application directory and configuration to Emacs:

```Elisp
(add-to-list 'load-path
             "~/.emacs.d/site-lisp/emacs-application-framework/app/ebook-viewer")
(require 'eaf)
(require 'eaf-ebook-viewer)
```

On macOS and Linux, the app builds its vendored Calibre 8.7 runtime for EAF's
Python. It does not require Calibre to be installed and does not launch
`calibre-debug`. Conversion workers use the same Python as EAF.

Open a supported ebook with `find-file` or `eaf-open`.

Calibre's Read Aloud settings include an optional Microsoft Edge Online TTS
engine. It supports configurable voices, speed, pitch and volume, and requires
an internet connection while synthesizing speech. The system TTS engines remain
available and can be selected at any time.

After a book is loaded, Calibre's parsed data is available in the
buffer-local variables `eaf-ebook-viewer-book-metadata` and
`eaf-ebook-viewer-book-manifest`. The current visible-page text is exposed through
`eaf-ebook-viewer-document-text` and
`eaf-ebook-viewer-document-changed-hook`.

External integrations can use `eaf-ebook-viewer-set-text-highlights` to mark
words without wrapping book text or changing Calibre's native annotations.
Entries may be plain strings or include a `word` and a `style` containing
`background`, `border`, and `opacity`. Native highlights are reported
through `eaf-ebook-viewer-annotation-created-hook` and
`eaf-ebook-viewer-annotation-removed-hook`, using their Calibre UUID.
Changes to their plain-text notes run
`eaf-ebook-viewer-annotation-updated-hook`; selecting one runs
`eaf-ebook-viewer-annotation-clicked-hook`. Calibre selects an existing
highlight with its native double-click gesture; ordinary clicks are left
unchanged.
Clicking a word runs
`eaf-ebook-viewer-word-clicked-hook` with its text context.
`eaf-ebook-viewer-create-highlight` creates a native Calibre highlight from
the current selection. `eaf-ebook-viewer-delete-highlight` removes one by its
Calibre UUID, and `eaf-ebook-viewer-set-highlight-notes` updates its notes.

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
| `x` | Close buffer |
