#!/usr/bin/env bash
# Sets up the Deskmates backend on a fresh Ubuntu server (24.04 or newer; tried on 26.04). Run it on the server, as root:
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
[ "${ID:-}" = ubuntu ] || echo "Warning: this was written for Ubuntu and you are on ${PRETTY_NAME:-something else}."
RAM_MB=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)
FREE_GB=$(df -BG --output=avail / | tail -1 | tr -dc '0-9')
echo "memory: ${RAM_MB} MB, free disk: ${FREE_GB} GB"
[ "$FREE_GB" -ge 3 ] || { echo "Less than 3 GB of free disk: grow the disk first (AWS: EC2, Volumes, Modify volume)."; exit 1; }
[ "$FREE_GB" -ge 12 ] || echo "Note: the disk is small. It is enough to install, but give it 30 GB or more before real data arrives."

say "Installing packages"
apt-get update -qq
apt-get install -y -qq ca-certificates curl gpg git rsync ufw sqlite3 build-essential libffi-dev libssl-dev debian-keyring debian-archive-keyring apt-transport-https

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

say "Python 3.12 (the version this app is built and tested on, whatever the system ships)"
command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin INSTALLER_NO_MODIFY_PATH=1 sh
export UV_PYTHON_INSTALL_DIR="$APP/python"
uv python install 3.12
[ -x "$VENV/bin/python" ] || uv venv --python 3.12 --seed "$VENV"
chown -R deskmate:deskmate "$APP/python" "$VENV"

say "Python packages"
if ! sudo -u deskmate "$VENV/bin/pip" install -q --no-cache-dir -r "$CODE/requirements.lock"; then
  echo "The pinned versions would not install here; using the looser list instead."
  sudo -u deskmate "$VENV/bin/pip" install -q --no-cache-dir -r "$CODE/requirements.txt"
fi

say "Browser for the employees (Google Chrome)"
if [ ! -x /opt/google/chrome/chrome ]; then
  if [ "$(uname -m)" = x86_64 ] && curl -fsSL -o /tmp/chrome.deb https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb && apt-get install -y -qq /tmp/chrome.deb; then
    echo "Google Chrome installed."
  else
    echo "Chrome could not be installed here; installing Playwright's own Chromium instead."
    PLAYWRIGHT_BROWSERS_PATH="$BROWSERS" "$VENV/bin/playwright" install --with-deps chromium
    chown -R deskmate:deskmate "$BROWSERS"
  fi
  rm -f /tmp/chrome.deb
fi

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
Anyone can create a company. To make that invitation-only, add a SIGNUP_CODE line to that file.
Data:        $DATA     (back this up, including the .keyfile)
MSG
