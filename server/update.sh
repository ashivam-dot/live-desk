#!/bin/bash
# Pulls the latest code; restarts the streams only when stream code changed, since a restart blips the live.
set -euo pipefail
APP=/opt/live-desk
before=$(git -C "$APP" rev-parse HEAD)
git -C "$APP" pull --ff-only -q
"$APP/.venv/bin/pip" install -q -r "$APP/requirements.txt"
after=$(git -C "$APP" rev-parse HEAD)
[ "$before" = "$after" ] && exit 0
if git -C "$APP" diff --quiet "$before" "$after" -- server; then :; else
  install -m 644 "$APP"/server/live-desk@.service "$APP"/server/live-desk-update.service \
    "$APP"/server/live-desk-update.timer /etc/systemd/system/
  systemctl daemon-reload
fi
if git -C "$APP" diff --quiet "$before" "$after" -- livedesk server requirements.txt; then
  echo "updated $before -> $after (no stream code changed)"
  exit 0
fi
systemctl restart live-desk@orbitwire live-desk@palpal
echo "updated $before -> $after; streams restarted"
