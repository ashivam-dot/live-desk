#!/bin/bash
# Sets up (or updates) the live desk on an Ubuntu 24.04 server. Run as root; safe to run again.
# The stream keys are not handled here: they live in /etc/live-desk/<channel>.key, written by the owner.
set -euo pipefail

REPO=https://github.com/ashivam-dot/live-desk.git
APP=/opt/live-desk

export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q ffmpeg git python3-venv libraqm0 libfribidi0 libharfbuzz0b tzdata

id live-desk >/dev/null 2>&1 || useradd --system --home-dir /var/lib/live-desk --shell /usr/sbin/nologin live-desk
if [ -d "$APP/.git" ]; then
  git -C "$APP" pull --ff-only -q
else
  git clone -q "$REPO" "$APP"
fi
python3 -m venv "$APP/.venv"
"$APP/.venv/bin/pip" install -q --upgrade pip
"$APP/.venv/bin/pip" install -q -r "$APP/requirements.txt"

install -d -m 700 -o live-desk -g live-desk /etc/live-desk
for ch in orbitwire palpal; do
  key=/etc/live-desk/$ch.key
  [ -f "$key" ] || echo "PASTE_${ch^^}_STREAM_KEY_HERE" > "$key"
  chown live-desk:live-desk "$key"
  chmod 600 "$key"
done

install -m 644 "$APP"/server/live-desk@.service "$APP"/server/live-desk-update.service \
  "$APP"/server/live-desk-update.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable live-desk@orbitwire live-desk@palpal live-desk-update.timer
systemctl restart live-desk@orbitwire live-desk@palpal live-desk-update.timer
# `systemctl status` would print the pusher's command line, which holds the stream key; these do not.
echo "live desk installed; check with: systemctl is-active live-desk@orbitwire live-desk@palpal"
echo "logs: journalctl -u live-desk@orbitwire -n 50"
