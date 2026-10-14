# fuckcc

Install Adobe desktop apps on macOS **without the Creative Cloud desktop app**.

`fuckcc` talks to Adobe's own public software-delivery endpoints to download the
official installers, stages them as an Adobe-standard offline package, and
installs them with Adobe's own `Setup` engine. Apps still activate through your
normal Adobe account sign-in — **nothing here bypasses Adobe's licensing or uses
cracked installers.**

```
$ fuckcc list
$ fuckcc install PHSP --lang en_US
```

---

## Why this exists

The Creative Cloud desktop app is a heavy, always-running background app whose
only essential job (for someone who just wants the software) is to be a package
manager. `fuckcc` does that job directly:

* **No CC desktop app required to download.** The product listing, per-build
  manifests, and installer packages all come from Adobe's public CDN
  (`prod-rel-ffc-ccm.oobesaas.adobe.com`, `cdn-ffc.oobesaas.adobe.com`,
  `ccmdls.adobe.com`) with no login.
* **Exactly the official bits.** Packages are downloaded verbatim from Adobe and
  verified by size (optionally ZIP CRC).
* **Nothing left lying around.** Everything lives under one directory that
  Homebrew can prune.

## How it actually works (and the one honest catch)

Adobe's apps are installed by a component Adobe calls the **Setup engine**
(`HDBox/Setup`, a.k.a. HyperDrive). It is the only software that understands the
encrypted `.pimx` descriptors inside each package that say *where every file
goes*. The engine normally ships with Creative Cloud.

`fuckcc` therefore needs the engine **once**, then keeps its own private copy and
never needs Creative Cloud again. Three ways to seed it:

| Method | Command | Notes |
| --- | --- | --- |
| From an Adobe offline package you already have | `fuckcc engine harvest <path>` | **Recommended.** Accepts an Admin Console package, `.dmg`, `.pkg`, `.zip`, or an unzipped folder containing `Adobe Desktop Common/HDBox`. No CC install. |
| From Adobe's official CC installer | `fuckcc engine bootstrap` | **Experimental / invasive.** Downloads Adobe's real CC installer, lets it drop the shared engine, harvests it, then tells you to remove CC. Needs admin. |
| Borrow from another machine | copy `HDBox/` to `$FUCKCC_HOME/engine/HDBox` | e.g. `FUCKCC_HOME=...` or `fuckcc engine harvest` on a machine that has it. |

If you have a Teams/Enterprise Adobe account, the Admin Console's "Create
Package" (offline) output is the cleanest source — it legally bundles the engine
alongside the app packages.

> If you don't want to deal with the engine at all, `fuckcc fetch` still gives you
> a fully-formed, self-contained offline installer tree that any machine with the
> engine (e.g. one that already ran an Adobe installer) can run via the generated
> `Install <APP>.command`.

## Install

### Homebrew (from this working tree)

fuckcc ships as a Homebrew **cask**, not a formula. Homebrew only honours the
`zap` stanza for casks, and fuckcc keeps its multi-GB runtime state *outside* the
keg (under `$(brew --prefix)/var/fuckcc`), so a cask is what makes a clean,
prompt-free uninstall possible.

```
scripts/brew-install-local.sh
fuckcc list
```

Remove it and **all** of its data:

```
brew uninstall --zap --cask fuckcc
```

The cask installs the code into the Caskroom, symlinks `fuckcc` into
`$(brew --prefix)/bin`, and its `zap` stanza deletes `$(brew --prefix)/var/fuckcc`
via `/bin/rm` (no `sudo` prompt).

### Run from source (no install)

```
./bin/fuckcc --help          # uses the working tree
# or
PYTHONPATH=src python3.14 -m fuckcc --help
```

Requires Python 3.14+ (standard library only — no pip packages).

## Usage

```
fuckcc list                       # installable products (SAP codes)
fuckcc versions PHSP              # versions available
fuckcc languages PHSP             # install languages
fuckcc install PHSP --lang en_US  # download + install
fuckcc fetch PPRO --minimal       # just stage an offline package
fuckcc engine status
fuckcc engine harvest ~/Downloads/AdobePhotoshop-package.zip
fuckcc clean --all
```

Useful flags for `install` / `fetch`:

| Flag | Meaning |
| --- | --- |
| `--version 27.10` | pin a version (default: newest) |
| `--lang en_US` / `--lang ALL` | install language |
| `--arch arm64\|x64` | target architecture (default: this Mac) |
| `--no-deps` | skip shared dependency components |
| `--minimal` | core packages only (skip on-demand modules) |
| `--recursive-deps` | also pull dependencies of dependencies |
| `--jobs N` | parallel downloads |
| `--install-dir DIR` | install root (default `/Applications`) |
| `--verify` | CRC-check ZIP packages after download |

Common SAP codes: `PHSP` Photoshop · `ILST` Illustrator · `PPRO` Premiere Pro ·
`AEFT` After Effects · `IDSN` InDesign · `LTRM` Lightroom Classic · `AUDT`
Audition · `AME` Media Encoder · `DRWV` Dreamweaver · `KBRG` Bridge.

## Where files go

Everything is under `$FUCKCC_HOME` (default `$(brew --prefix)/var/fuckcc`):

```
cache/    downloaded ZIP packages + cached product listing
stage/    assembled offline installer trees (products/driver.xml, ...)
engine/   your private copy of Adobe's Setup engine (HDBox/)
log/
```

Override with `FUCKCC_HOME=/some/dir`. `fuckcc paths` prints the locations.

## Design notes

* **No third-party dependencies.** Pure stdlib Python, so install/prune is
  trivial and auditable.
* **Resumable downloads** (`Range`), retries with backoff, size verification.
* **Package selection** evaluates Adobe's package `Condition` expressions
  (`[OSArchitecture]`, `[installLanguage]`, `&&`/`||`) so you get exactly the
  core + language + module packages for your machine.
* **Dependency resolution** follows each manifest's `Dependencies`, pulling
  shared components (color sets, Camera Raw, CoreSync, UXP/CCX runtime, …) from
  the same CDN.

## Legal

`fuckcc` downloads Adobe's official installers and installs them with Adobe's own
engine. It does **not** crack activation, patch binaries, or bypass subscription
checks; you still need a valid Adobe license/account to activate the apps. Use it
in accordance with Adobe's terms and your license agreement.

## Tests

```
make test
```
