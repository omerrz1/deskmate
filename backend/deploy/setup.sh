#!/usr/bin/env bash
# Sets up the Deskmates backend on a fresh Ubuntu 24.04 server. Run it on the server, as root:
#     sudo API_HOST=api.example.com APP_ORIGIN=https://app.example.com bash /opt/deskmate/backend/deploy/setup.sh
# (push.sh copies the code there first.) Safe to run again: it only adds what is missing, and never overwrites your settings or your data.
set -euo pipefail

API_HOST="${API_HOST:-api.deskmate.londoncbs.uk}"
APP_ORIGIN="${APP_ORIGIN:-https://deskmate.londoncbs.uk}"
APP=/opt/deskmate; CODE=$APP/backend; VENV=$APP/venv; BROWSERS=$APP/browsers
DATA=/var/lib/deskmate; CONF=/etc/deskmate
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
[ "$(id -u)" -eq 0 ] || { echo "Run this with sudo."; exit 1; }
[ -f "$CODE/app/main.py" ] || { echo "The code is not in $CODE yet. Run deploy/push.sh from your computer first."; exit 1; }
export DEBIAN_FRONTEND=noninteractive

say "Checking the system"
. /etc/os-release
[ "${ID:-}" = ubuntu ] || echo "Warning: this was written for Ubuntu 24.04 and you are on ${PRETTY_NAME:-something else}."
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' || { echo "Python 3.12 or newer is needed (Ubuntu 24.04 has it)."; exit 1; }
RAM_MB=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)
echo "memory: ${RAM_MB} MB"

say "Installing packages"
apt-get update -qq
apt-get install -y -qq ca-certificates curl gpg git rsync ufw sqlite3 python3-venv python3-pip build-essential libffi-dev libssl-dev debian-keyring debian-archive-keyring apt-transport-https

if [ "$RAM_MB" -lt 3500 ] && ! swapon --show | grep -q .; then
  say "Adding 2 GB of swap (the browser tool is hungry on a small server)"
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

say "Creating the service user and folders"
id deskmate >/dev/null 2>&1 || useradd --system --home-dir "$APP" --shell /usr/sbin/nologin deskmate
install -d -m 0755 -o deskmate -g deskmate "$APP" "$BROWSERS"
install -d -m 0700 -o deskmate -g deskmate "$DATA"      # databases, files and the encryption key: only the service can read them
install -d -m 0750 -o root -g deskmate "$CONF"
chown -R deskmate:deskmate "$CODE"

say "Python environment"
[ -x "$VENV/bin/python" ] || sudo -u deskmate python3 -m venv "$VENV"
sudo -u deskmate "$VENV/bin/pip" install -q --upgrade pip
if ! sudo -u deskmate "$VENV/bin/pip" install -q -r "$CODE/requirements.lock"; then
  echo "The pinned versions would not install here; using the looser list instead."
  sudo -u deskmate "$VENV/bin/pip" install -q -r "$CODE/requirements.txt"
fi

say "Browser for the employees (Chromium)"
PLAYWRIGHT_BROWSERS_PATH="$BROWSERS" "$VENV/bin/playwright" install --with-deps chromium
chown -R deskmate:deskmate "$BROWSERS"

say "Settings"
if [ ! -f "$CONF/backend.env" ]; then
  install -m 0640 -o root -g deskmate "$HERE/backend.env.example" "$CONF/backend.env"
  sed -i "s|^ALLOWED_ORIGINS=.*|ALLOWED_ORIGINS=$APP_ORIGIN|; s|^PUBLIC_BASE_URL=.*|PUBLIC_BASE_URL=https://$API_HOST|" "$CONF/backend.env"
  echo "Wrote $CONF/backend.env (edit it, then: systemctl restart deskmate)"
else
  echo "Keeping your existing $CONF/backend.env"
fi

say "Starting the backend"
install -m 0644 "$HERE/deskmate.service" /etc/systemd/system/deskmate.service
systemctl daemon-reload
systemctl enable deskmate >/dev/null
systemctl restart deskmate
for i in $(seq 1 40); do curl -fsS http://127.0.0.1:8000/api/health >/dev/null 2>&1 && break; sleep 1; done
curl -fsS http://127.0.0.1:8000/api/health >/dev/null || { echo "The backend did not start. Look at: journalctl -u deskmate -n 80"; exit 1; }
echo "The backend answers on this machine."

say "HTTPS (Caddy)"
if ! command -v caddy >/dev/null; then
  curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/gpg.key | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -qq && apt-get install -y -qq caddy
fi
API_HOST="$API_HOST" bash "$HERE/caddy.sh"
# CloudFront's addresses change now and then: refresh them every week
printf '#!/bin/sh\nAPI_HOST=%s bash %s/caddy.sh >/var/log/deskmate-caddy-refresh.log 2>&1\n' "$API_HOST" "$HERE" > /etc/cron.weekly/deskmate-caddy && chmod +x /etc/cron.weekly/deskmate-caddy

say "Firewall"
ufw allow OpenSSH >/dev/null; ufw allow 80/tcp >/dev/null; ufw allow 443/tcp >/dev/null
ufw --force enable >/dev/null && ufw status | head -8

say "Done"
cat <<MSG
The backend is running. Next:
  1. Point DNS at this server:   $API_HOST  ->  this server's public IP address (an A record)
  2. Check it from anywhere:     curl https://$API_HOST/api/health      (the certificate is fetched on the first request)
  3. Tell Amplify to send /api to it (see deploy/README.md)
Logs:        journalctl -u deskmate -f
Settings:    $CONF/backend.env        (then: systemctl restart deskmate)
Data:        $DATA     (back this up, including the .keyfile)
MSG
