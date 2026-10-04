#!/usr/bin/env bash
# Install the repo's git hooks as symlinks (idempotent). Re-run after every fresh clone:
# git does not clone hooks.
set -euo pipefail
repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
hooks_dir="$(git -C "$repo" rev-parse --git-path hooks)"
case "$hooks_dir" in /*) ;; *) hooks_dir="$repo/$hooks_dir" ;; esac
mkdir -p "$hooks_dir"
src="$repo/tools/hooks/pre-push"
dst="$hooks_dir/pre-push"
if [ -L "$dst" ] && [ "$(readlink "$dst")" = "$src" ]; then
  echo "pre-push already installed"
elif [ -e "$dst" ] || [ -L "$dst" ]; then
  echo "WARNING: $dst exists and is not our symlink — not overwriting" >&2
  exit 1
else
  ln -s "$src" "$dst"
  echo "installed $dst -> $src"
fi
