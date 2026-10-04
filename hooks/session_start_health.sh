#!/bin/bash
# SessionStart health check: prints ONE line only when something is broken.
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=common.sh
. "$DIR/common.sh"
cat >/dev/null 2>&1 || true
problem=""
if [ ! -f "$REPO_DIR/ledger_collect.py" ]; then
  problem="Sammler fehlt"
elif [ -z "$DATA_DIR" ] || [ ! -d "$DATA_DIR" ]; then
  problem="Daten-Verzeichnis fehlt (Konfiguration prüfen)"
else
  last="$(grep -v -e ERROR -e ' archive: ' "$DATA_DIR/logs/collect.log" 2>/dev/null | tail -1 | sed -n 's/^\[\([^]]*\)\].*/\1/p')"
  if [ -z "$last" ]; then
    problem="noch kein erfolgreicher Sammler-Lauf"
  else
    age=$(python3 -c '
import datetime as d, sys
print(int((d.datetime.now() - d.datetime.strptime(sys.argv[1], "%Y-%m-%d %H:%M:%S")).total_seconds() // 3600))
' "$last" 2>/dev/null)
    if [ -z "$age" ] || [ "$age" -ge 48 ]; then problem="letzter Sammler-Lauf vor mehr als 48 h"; fi
  fi
  if [ -z "$problem" ]; then
    bad="$(git -C "$DATA_DIR" remote 2>/dev/null | grep -vx gitea | head -1)"
    [ -n "$bad" ] && problem="Daten-Repo hat Remote '$bad' (nur gitea erlaubt)"
  fi
fi
if [ -z "$problem" ] && [ -f "$DATA_DIR/state/archive_missing_since" ]; then
  miss="$(python3 -c '
import datetime as d, sys
print((d.date.today() - d.date.fromisoformat(sys.argv[1])).days)
' "$(tr -d '[:space:]' < "$DATA_DIR/state/archive_missing_since")" 2>/dev/null)"
  if [ -n "$miss" ] && [ "$miss" -gt 7 ]; then problem="Archivziel fehlt seit mehr als 7 Tagen (archive_target in config.json)"; fi
fi
[ -n "$problem" ] && echo "[usage-ledger] $problem"
exit 0
