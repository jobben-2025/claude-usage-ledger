#!/bin/bash
# Render the plist templates for this machine and (re)load the LaunchAgents
# (daily collector + 15-minute usage sampler).
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
for LABEL in com.user.usage-ledger com.user.usage-ledger-sample; do
  DEST="$HOME/Library/LaunchAgents/$LABEL.plist"
  sed -e "s|__REPO__|$REPO|g" -e "s|__HOME__|$HOME|g" "$REPO/launchd/$LABEL.plist.template" > "$DEST"
  plutil -lint "$DEST"
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$DEST"
done
launchctl list | grep usage-ledger
