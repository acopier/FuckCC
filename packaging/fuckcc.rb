# Homebrew *cask* for fuckcc.
#
# This file is a *template*: scripts/brew-install-local.sh fills in the url,
# sha256 and version below from a tarball built out of the working tree. For a
# published tap, replace them with a real release URL and sha256.
#
# Local install:  scripts/brew-install-local.sh
# Tap install:    brew install --cask <tap>/fuckcc
#
# Why a cask and not a formula? Homebrew only honours the `zap` stanza for
# casks (formulae have no uninstall hook at all). fuckcc keeps its multi-GB
# runtime state (downloads, staged offline packages, and its private copy of
# Adobe's Setup engine) under $(brew --prefix)/var/fuckcc — outside the Caskroom
# — so a cask `zap` is the only way `brew uninstall --zap --cask fuckcc` can
# prune it cleanly.
cask "fuckcc" do
  version "__VERSION__"
  sha256 "__SHA256__"

  url "__URL__"
  name "fuckcc"
  desc "Install Adobe desktop apps from Adobe's CDN without Creative Cloud"
  homepage "https://github.com/acopier/fuckcc"

  depends_on formula: "python@3.14"

  binary "bin/fuckcc"

  caveats do
    <<~EOS
      fuckcc keeps all of its runtime state under:
        #{HOMEBREW_PREFIX}/var/fuckcc

      Seed Adobe's Setup engine once (no Creative Cloud install required):
        fuckcc engine harvest <Adobe Admin Console package | .dmg | .pkg | dir>

      To remove fuckcc *and* its downloaded data:
        brew uninstall --zap --cask fuckcc
    EOS
  end

  # `zap delete:` would shell out to `rm` through `sudo`, which prompts for a
  # password even though the state dir is owned by the installing user. Run
  # `/bin/rm` directly instead so the zap is prompt-free.
  zap script: {
    executable: "/bin/rm",
    args:       ["-rf", "#{HOMEBREW_PREFIX}/var/fuckcc"],
  }
end
