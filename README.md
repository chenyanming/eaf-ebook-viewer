### EAF Ebook Viewer

Ebook Viewer application for the [Emacs Application Framework](https://github.com/emacs-eaf/emacs-application-framework), powered by calibre's native E-book Viewer.

It supports EPUB, MOBI, AZW3, FB2 and other formats supported by calibre. Local books are opened directly. Books stored by a macOS cloud file provider are downloaded to a read-only local cache first.

### Demo

<img width="720" src="./demo.gif">

### Install

Install [EAF](https://github.com/emacs-eaf/emacs-application-framework#install)
first, then run the following commands from the EAF repository root. Ebook
Viewer is not in EAF's application registry yet, so `install-eaf.py` cannot
install it.

```Shell
git clone --recurse-submodules https://github.com/chenyanming/eaf-ebook-viewer.git \
  app/ebook-viewer
```

If you already cloned the repository without its submodules, run:

```Shell
git -C app/ebook-viewer submodule update --init --recursive
```

Install the system build dependencies for your platform:

```Shell
# Debian / Ubuntu
sudo apt install build-essential libicu-dev libpulse0 libxrandr2 \
  libxml2-dev libxslt1-dev pkg-config pyqt6-dev-tools

# Fedora
sudo dnf install gcc gcc-c++ libicu-devel libXrandr libxml2-devel \
  libxslt-devel pkgconf-pkg-config pulseaudio-libs

# Arch Linux
sudo pacman -S base-devel icu libpulse libxrandr libxml2 libxslt pkgconf

# macOS (Xcode Command Line Tools are also required)
brew install icu4c
```

Build and install Ebook Viewer with the same Python environment used by EAF:

```Shell
python3 app/ebook-viewer/install.py
```

If you configured `eaf-python-command` to use a different interpreter, replace
`python3` above with that interpreter. The installer verifies that lxml and
html5-parser use compatible native libraries before building the vendored
Calibre runtime.

Add the application directory and configuration to Emacs:

```Elisp
(require 'eaf)
(add-to-list 'load-path
             (expand-file-name "app/ebook-viewer" eaf-source-dir))
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
