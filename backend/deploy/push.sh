#!/usr/bin/env bash
# Copies this backend to the server and sets it up (the first time) or restarts it (after that). Run it on your own computer:
#     HOST=ubuntu@203.0.113.10 KEY=~/.ssh/deskmate_deploy API_HOST=api.example.com APP_ORIGIN=https://app.example.com bash deploy/push.sh
set -euo pipefail
: "${HOST:?set HOST, e.g. HOST=ubuntu@203.0.113.10}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SSH_OPTS="-o StrictHostKeyChecking=accept-new${KEY:+ -i $KEY}"
remote() { ssh $SSH_OPTS "$HOST" "$@"; }

echo "==> Preparing the server"
remote 'command -v rsync >/dev/null || { sudo apt-get update -qq && sudo apt-get install -y -qq rsync; }; sudo mkdir -p /opt/deskmate/backend && sudo chown -R "$(id -un)": /opt/deskmate/backend'
echo "==> Copying the code"
rsync -az --delete -e "ssh $SSH_OPTS" \
  --exclude '.git/' --exclude '.venv/' --exclude '__pycache__/' --exclude '*.pyc' --exclude '.env' --exclude 'data/' --exclude 'tests/' --exclude 'evals/' --exclude '.DS_Store' \
  "$ROOT/" "$HOST:/opt/deskmate/backend/"
echo "==> Setting up and restarting"
remote "sudo API_HOST='${API_HOST:-api.deskmate.londoncbs.uk}' APP_ORIGIN='${APP_ORIGIN:-https://deskmate.londoncbs.uk}' bash /opt/deskmate/backend/deploy/setup.sh"
