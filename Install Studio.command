#!/bin/bash
# Double-click to install TaleMondo Studio. Running it again updates the app and preserves data.
cd "$(dirname "$0")" || exit 1
set -e
echo "TaleMondo Studio — installation"
echo

if ! xcode-select -p >/dev/null 2>&1; then
  echo "Xcode Command Line Tools are required for Keychain and Git. The installer will open;"
  echo "run this file again after installation."
  xcode-select --install || true
  read -r -p "Press Enter to close…" _
  exit 1
fi

if ! command -v brew >/dev/null 2>&1; then
  for b in /opt/homebrew/bin/brew /usr/local/bin/brew; do [ -x "$b" ] && eval "$("$b" shellenv)"; done
fi
if ! command -v brew >/dev/null 2>&1; then
  echo "Installing Homebrew. macOS may ask for your password."
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  for b in /opt/homebrew/bin/brew /usr/local/bin/brew; do [ -x "$b" ] && eval "$("$b" shellenv)"; done
fi

echo "== Python and ffmpeg"
brew list python@3.12 >/dev/null 2>&1 || brew install python@3.12
brew list ffmpeg >/dev/null 2>&1 || brew install ffmpeg

H3_PYTHON="$(brew --prefix)/bin/python3.12" bash scripts/install_studio_on_mac.sh
echo
echo "Done. Studio is open at http://127.0.0.1:18187/ . Follow the first-run wizard."
read -r -p "Press Enter to close…" _
