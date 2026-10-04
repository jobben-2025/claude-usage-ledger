# shellcheck shell=bash
# Shared helpers: resolve repo dir and data dir (config: ~/.config/usage-ledger/config.json).
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
CONFIG="${USAGE_LEDGER_CONFIG:-$HOME/.config/usage-ledger/config.json}"
DATA_DIR="${USAGE_LEDGER_DATA_DIR:-$(python3 -c '
import json, os, sys
try:
    print(os.path.expanduser(json.load(open(sys.argv[1]))["data_dir"]))
except Exception:
    pass
' "$CONFIG" 2>/dev/null)}"
