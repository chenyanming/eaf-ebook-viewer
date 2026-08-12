### EAF Ebook Viewer

Ebook Viewer application for the [Emacs Application Framework](https://github.com/emacs-eaf/emacs-application-framework), powered by calibre's native E-book Viewer.

It supports EPUB, MOBI, AZW3, FB2 and other formats supported by calibre. Books are opened from a read-only local cache, so the original file is not modified.

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
