#!/usr/bin/env bash
# Install fuckcc into a local Homebrew tap as a *cask* from this working tree.
#
# It tars the source, computes a sha256, drops a rendered cask into a local tap,
# and installs it. Uninstall (and prune all runtime data) with:
#   brew uninstall --zap --cask fuckcc
#
# A cask (rather than a formula) is used on purpose: Homebrew only runs the
# `zap` stanza for casks, and fuckcc keeps its multi-GB state outside the keg
# under $(brew --prefix)/var/fuckcc.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TAP_USER="local"
TAP_REPO="fuckcc"
TAP_NAME="${TAP_USER}/${TAP_REPO}"

if ! command -v brew >/dev/null 2>&1; then
  echo "Homebrew not found in PATH." >&2
  exit 1
fi

VERSION="$(sed -nE 's/^version[[:space:]]*=[[:space:]]*"([^"]+)".*/\1/p' "$REPO_ROOT/pyproject.toml" | head -1)"
[ -n "$VERSION" ] || { echo "could not read version from pyproject.toml" >&2; exit 1; }

TAP_DIR="$(brew --repository)/Library/Taps/${TAP_USER}/homebrew-${TAP_REPO}"
CASKS_DIR="$TAP_DIR/Casks"
mkdir -p "$CASKS_DIR"

# Drop any cask/formula left over from an older formula-based install.
rm -f "$TAP_DIR/Formula/fuckcc.rb"
rmdir "$TAP_DIR/Formula" 2>/dev/null || true

# Build a *flat* tarball (contents at the archive root, no wrapping directory)
# so Homebrew's cask container ends up staging bin/, src/, ... directly.
TMPDIR_TRIMMED="${TMPDIR:-/tmp}"
TMPDIR_TRIMMED="${TMPDIR_TRIMMED%/}"
BUILD_DIR="$(mktemp -d "${TMPDIR_TRIMMED}/fuckcc-brew.XXXXXX")"
trap 'rm -rf "$BUILD_DIR"' EXIT
STAGE="$BUILD_DIR/stage"
mkdir -p "$STAGE"
cp -R "$REPO_ROOT/src" "$STAGE/"
cp -R "$REPO_ROOT/bin" "$STAGE/"
cp "$REPO_ROOT/pyproject.toml" "$REPO_ROOT/README.md" "$STAGE/"
[ -f "$REPO_ROOT/LICENSE" ] && cp "$REPO_ROOT/LICENSE" "$STAGE/"
chmod +x "$STAGE/bin/fuckcc"

PKG="fuckcc-${VERSION}"
TARBALL="$BUILD_DIR/${PKG}.tar.gz"
tar -C "$STAGE" -czf "$TARBALL" .
SHA="$(shasum -a 256 "$TARBALL" | awk '{print $1}')"
URL="file://${TARBALL}"

echo "Rendering cask into: $CASKS_DIR/fuckcc.rb"
sed -e "s|__URL__|${URL}|g" \
    -e "s|__SHA256__|${SHA}|g" \
    -e "s|__VERSION__|${VERSION}|g" \
    "$REPO_ROOT/packaging/fuckcc.rb" > "$CASKS_DIR/fuckcc.rb"

# Homebrew refuses to load casks from third-party taps until they are trusted.
brew trust --tap "${TAP_NAME}" >/dev/null 2>&1 || true

echo "Installing ${TAP_NAME}/fuckcc (cask) ..."
brew install --cask "${TAP_NAME}/fuckcc"

echo
echo "Done. Try: fuckcc list"
echo "Prune everything later with: brew uninstall --zap --cask fuckcc"
