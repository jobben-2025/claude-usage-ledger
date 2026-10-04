#!/bin/bash
# Daily job: collect, rsync raw logs to archive_target, commit changes in the data repo, push to the 'gitea' remote.
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../hooks/common.sh
. "$DIR/../hooks/common.sh"
[ -n "$DATA_DIR" ] || { echo "data_dir not configured" >&2; exit 2; }
LOG="$DATA_DIR/logs/collect.log"
note() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] daily: $*" >> "$LOG"; }
python3 "$REPO_DIR/ledger_collect.py" || note "collector exit $?"
python3 "$REPO_DIR/ledger_archive.py" --quiet || note "archiver exit $?"
cd "$DATA_DIR" || exit 2
git add -A
if ! git diff --cached --quiet; then
  git commit -q -m "Ledger: $(date +%F)" || note "commit failed"
fi
git push -q gitea main 2>>"$LOG" || note "push failed (gitea offline?)"
exit 0
