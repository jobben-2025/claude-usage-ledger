#!/bin/bash
# SessionEnd hook: start the collector detached; silent, always exit 0.
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
cat >/dev/null 2>&1 || true
nohup python3 "$DIR/../ledger_collect.py" >/dev/null 2>&1 </dev/null &
exit 0
