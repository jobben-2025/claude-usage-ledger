#!/usr/bin/env bash
# Build the orphan branch `release` (single commit "Initial public release") from the tree of `main`.
# Allow-list: only the paths in PUBLIC below are published; everything else (working files, .claude/, new
# files nobody listed) stays private by default. No checkout, no touching the working tree. Refuses to overwrite an
# existing `release` branch (a published history must not be rewritten).
set -euo pipefail
repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo"
src="${1:-main}"
if git show-ref --verify --quiet refs/heads/release; then
  echo "release branch already exists — not overwriting" >&2
  exit 1
fi
idx="$(mktemp -u)"
trap 'rm -f "$idx"' EXIT
export GIT_INDEX_FILE="$idx"
git read-tree "$src"
PUBLIC='^(\.gitignore|LICENSE|README\.md|config\.example\.json|prices\.json|ledger_[a-z_]+\.py|usage_sample\.py|(docs|hooks|launchd|tests|tools)/.+)$'
git ls-files -z | { grep -zvE "$PUBLIC" || true; } | xargs -0 -r git rm -q --cached --
tree="$(git write-tree)"
commit="$(git commit-tree "$tree" -m "Initial public release")"
git update-ref refs/heads/release "$commit"
echo "release -> $commit"
