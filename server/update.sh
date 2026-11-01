#!/bin/bash
# Pulls the latest code; restarts the streams only when something changed, since a restart blips the live.
set -euo pipefail
APP=/opt/live-desk
before=$(git -C "$APP" rev-parse HEAD)
git -C "$APP" pull --ff-only -q
after=$(git -C "$APP" rev-parse HEAD)
[ "$before" = "$after" ] && exit 0
"$APP/.venv/bin/pip" install -q -r "$APP/requirements.txt"
install -m 644 "$APP"/server/live-desk@.service "$APP"/server/live-desk-update.service \
  "$APP"/server/live-desk-update.timer /etc/systemd/system/
systemctl daemon-reload
systemctl restart live-desk@orbitwire live-desk@palpal
echo "updated $before -> $after"
