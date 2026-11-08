#!/bin/bash
# Двойной щелчок — и H3 Studio установлена на этот Mac. Повторный запуск обновляет её, данные сохраняются.
cd "$(dirname "$0")" || exit 1
set -e
echo "H3 Studio — установка"
echo

if ! xcode-select -p >/dev/null 2>&1; then
  echo "Нужны инструменты командной строки Xcode (для связки ключей и git). Откроется окно установки;"
  echo "после неё запустите этот файл снова."
  xcode-select --install || true
  read -r -p "Нажмите Enter, чтобы закрыть…" _
  exit 1
fi

if ! command -v brew >/dev/null 2>&1; then
  for b in /opt/homebrew/bin/brew /usr/local/bin/brew; do [ -x "$b" ] && eval "$("$b" shellenv)"; done
fi
if ! command -v brew >/dev/null 2>&1; then
  echo "Ставлю Homebrew (менеджер пакетов macOS). Он может спросить пароль от Mac."
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  for b in /opt/homebrew/bin/brew /usr/local/bin/brew; do [ -x "$b" ] && eval "$("$b" shellenv)"; done
fi

echo "== Python и ffmpeg"
brew list python@3.12 >/dev/null 2>&1 || brew install python@3.12
brew list ffmpeg >/dev/null 2>&1 || brew install ffmpeg

H3_PYTHON="$(brew --prefix)/bin/python3.12" bash scripts/install_studio_on_mac.sh
echo
echo "Готово. Studio открыта в браузере: http://127.0.0.1:18187/ — дальше «Первый запуск» подскажет шаги."
read -r -p "Нажмите Enter, чтобы закрыть…" _
