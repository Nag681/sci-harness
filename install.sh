#!/bin/sh
# Online installer for sci-harness, the software-engineer agent skill. Linux, macOS, WSL and Git Bash.
#
#   curl -fsSL https://raw.githubusercontent.com/Nag681/sci-harness/main/install.sh | sh
#   curl -fsSL https://raw.githubusercontent.com/Nag681/sci-harness/main/install.sh | sh -s -- --hooks
#
# Downloads the repository and runs its install.py with the arguments given (--help lists them).
# Needs Python 3.8+ and curl or wget. Optional environment variables:
#   SCI_REF     branch, tag or commit to install (default: main)
#   SCI_REPO    GitHub owner/name (default: Nag681/sci-harness)
#   SCI_SOURCE  install from this local folder or .tar.gz instead of downloading
#   GH_TOKEN    or GITHUB_TOKEN, to install from a private fork
#
# Everything is inside main(), which runs only after the whole script has arrived.
set -eu

say() { printf 'sci-harness: %s\n' "$*" >&2; }
die() { say "$*"; exit 1; }

pick_python() {
  # Each candidate is run once: on Windows, python3/python can be Microsoft Store stubs that only print a hint.
  for c in python3 python "py -3"; do
    # shellcheck disable=SC2086 # split "py -3" into words
    set -- $c
    command -v "$1" >/dev/null 2>&1 || continue
    if "$@" -c 'import sys; sys.exit(sys.version_info < (3, 8))' >/dev/null 2>&1; then
      echo "$c"
      return 0
    fi
  done
  return 1
}

fetch() {  # fetch URL FILE
  if command -v curl >/dev/null 2>&1; then
    if [ -n "$token" ]; then
      curl -fsSL --retry 3 -H "Authorization: Bearer $token" -o "$2" "$1"
    else
      curl -fsSL --retry 3 -o "$2" "$1"
    fi
  elif command -v wget >/dev/null 2>&1; then
    if [ -n "$token" ]; then
      wget -q --header="Authorization: Bearer $token" -O "$2" "$1"
    else
      wget -q -O "$2" "$1"
    fi
  else
    die "curl or wget is required."
  fi
}

main() {
  repo=${SCI_REPO:-Nag681/sci-harness}
  ref=${SCI_REF:-main}
  token=${GH_TOKEN:-${GITHUB_TOKEN:-}}
  src=${SCI_SOURCE:-}

  if ! py=$(pick_python); then
    say "Python 3.8+ is required. Install it, then run this again:"
    say "  Debian/Ubuntu: sudo apt install python3   Fedora: sudo dnf install python3   Arch: sudo pacman -S python"
    say "  macOS: brew install python3   Windows: winget install Python.Python.3.12"
    exit 1
  fi
  command -v git >/dev/null 2>&1 || say "warning: git not found; the skill needs git in every repository it manages."

  tmp=$(mktemp -d 2>/dev/null || mktemp -d -t sci-harness)
  trap 'rm -rf "$tmp"' EXIT
  trap 'exit 130' INT TERM HUP

  if [ -n "$src" ] && [ -d "$src" ]; then
    dir=$src
  else
    archive=$tmp/source.tar.gz
    if [ -n "$src" ]; then
      cp "$src" "$archive"
    else
      if [ -n "$token" ]; then
        url=https://api.github.com/repos/$repo/tarball/$ref
      else
        url=https://github.com/$repo/archive/$ref.tar.gz
      fi
      say "downloading $repo ($ref)"
      fetch "$url" "$archive" || die "download failed: $url"
    fi
    tar -xzf "$archive" -C "$tmp" || die "could not unpack $archive"
    dir=
    for d in "$tmp"/*/; do
      if [ -f "${d}install.py" ]; then
        dir=${d%/}
        break
      fi
    done
    [ -n "$dir" ] || die "install.py not found in the downloaded archive."
  fi

  # $py may be two words (py -3). stdin is closed so nothing can eat the rest of a piped script.
  # shellcheck disable=SC2086
  $py "$dir/install.py" "$@" </dev/null
}

main "$@"
