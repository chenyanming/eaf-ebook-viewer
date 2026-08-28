### EAF Ebook Viewer

Ebook Viewer application for the [Emacs Application Framework](https://github.com/emacs-eaf/emacs-application-framework), powered by calibre's native E-book Viewer.

It supports EPUB, MOBI, AZW3, FB2 and other formats supported by calibre. Local books are opened directly. Books stored by a macOS cloud file provider are downloaded to a read-only local cache first.

### Demo

<img width="720" src="./demo.gif">

### Install

Run all commands from the EAF repository root.

#### 1. Install EAF

Install [EAF](https://github.com/emacs-eaf/emacs-application-framework#install).

Ebook Viewer is not in the EAF application registry. The EAF installer cannot
clone Ebook Viewer.

#### 2. Clone Ebook Viewer

```Shell
git clone --recurse-submodules https://github.com/chenyanming/eaf-ebook-viewer.git \
  app/ebook-viewer
```

If you cloned the repository without its submodules, initialize them:

```Shell
git -C app/ebook-viewer submodule update --init --recursive
```

#### 3. Install the system build dependencies

Run the command for your operating system.

##### Debian or Ubuntu

```Shell
sudo apt install build-essential libicu-dev libpulse0 libxrandr2 \
  libxml2-dev libxslt1-dev pkg-config pyqt6-dev-tools
```

##### Fedora

```Shell
sudo dnf install gcc gcc-c++ libicu-devel libXrandr libxml2-devel \
  libxslt-devel pkgconf-pkg-config pulseaudio-libs
```

##### Arch Linux

```Shell
sudo pacman -S base-devel icu libpulse libxrandr libxml2 libxslt pkgconf
```

##### macOS

Install the Xcode Command Line Tools:

```Shell
xcode-select --install
```

Then install ICU:

```Shell
brew install icu4c
```

#### 4. Build and install Ebook Viewer

Use the same Python environment that EAF uses:

```Shell
python3 app/ebook-viewer/install.py
```

If `eaf-python-command` uses a different interpreter, replace `python3` with
that interpreter.

The installer makes sure that lxml and html5-parser use compatible native
libraries. Then it builds the vendored Calibre runtime.

#### 5. Configure Emacs

Add this configuration to Emacs:

```Elisp
(require 'eaf)
(add-to-list 'load-path
             (expand-file-name "app/ebook-viewer" eaf-source-dir))
(require 'eaf-ebook-viewer)
```

#### 6. Open an ebook

Open a supported ebook with `find-file` or `eaf-open`.

### Installation details

On macOS and Linux, Ebook Viewer builds its vendored Calibre 8.7 runtime for
the EAF Python environment. You do not have to install Calibre. Ebook Viewer
does not start `calibre-debug`. Conversion workers use the same Python
interpreter as EAF.

### Read aloud

Calibre Read Aloud includes the optional Microsoft Edge Online TTS engine. You
can configure its voice, speed, pitch, and volume. This engine requires an
internet connection while it creates speech.

The system TTS engines remain available. You can select one at any time.

### Emacs integration

#### Book data

After a book is loaded, Calibre's parsed data is available in the
buffer-local variables `eaf-ebook-viewer-book-metadata` and
`eaf-ebook-viewer-book-manifest`.

The current visible-page text is available in
`eaf-ebook-viewer-document-text`. Text changes run
`eaf-ebook-viewer-document-changed-hook`.

#### Text highlights

External integrations can use `eaf-ebook-viewer-set-text-highlights` to mark
words without wrapping book text or changing Calibre's native annotations.
An entry can be a plain string. It can also contain a `word` and a `style`.
The `style` can contain `background`, `border`, and `opacity`.

The hooks `eaf-ebook-viewer-annotation-created-hook` and
`eaf-ebook-viewer-annotation-removed-hook` report native highlights. Each
event contains the Calibre UUID.

#### Native annotations

Changes to plain-text notes run `eaf-ebook-viewer-annotation-updated-hook`.
Selection of an annotation runs `eaf-ebook-viewer-annotation-clicked-hook`.

Calibre selects an existing highlight with its native double-click gesture.
An ordinary click does not select the highlight.

#### Selection commands

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
