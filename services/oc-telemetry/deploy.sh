#!/usr/bin/env bash
# Deploy the shared oc-telemetry receiver to the OpenConverters box
# (51.15.253.66 — same host that serves kelvin/kirchhoff, and where the OM Umami
# container already runs on 127.0.0.1:3001). Idempotent; safe to re-run.
#
#   ./deploy.sh
#
# What it does:
#   1. rsync the service (app.py, db.py, requirements.txt) to /opt/oc-telemetry
#   2. build a venv + install deps
#   3. write /etc/oc-telemetry.env from THIS shell's OM_DB_* (never hardcoded)
#   4. install + start the systemd unit (uvicorn on 127.0.0.1:8787)
#   5. install the nginx snippet to /etc/nginx/snippets/oc-telemetry.conf
#   6. health-check the service over the loopback
#
# The nginx `include` line for each site's vhost and the DB schema (auto-created
# by the service on first event) are covered in README.md.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
HOST=root@51.15.253.66
KEY="$HOME/.ssh/om_scaleway"
SSH="ssh -i $KEY -o StrictHostKeyChecking=no"
APPDIR=/opt/oc-telemetry

for v in OM_DB_ADDRESS OM_DB_PORT OM_DB_NAME OM_DB_USER OM_DB_PASSWORD; do
  [[ -n "${!v:-}" ]] || { echo "ERROR: $v is not set in this shell (source ~/.bashrc)." >&2; exit 1; }
done

echo "==> 1/6 rsync service → $HOST:$APPDIR"
$SSH "$HOST" "mkdir -p $APPDIR"
rsync -az -e "$SSH" \
  "$HERE/app.py" "$HERE/db.py" "$HERE/requirements.txt" \
  "$HOST:$APPDIR/"

echo "==> 2/6 venv + deps"
$SSH "$HOST" "cd $APPDIR && (python3 -m venv venv || true) && \
  ./venv/bin/pip install --quiet --upgrade pip && \
  ./venv/bin/pip install --quiet -r requirements.txt"

echo "==> 3/6 write /etc/oc-telemetry.env (root:root 0600)"
# Pipe the secrets over stdin so they never appear in argv/process list.
$SSH "$HOST" "umask 077 && cat > /etc/oc-telemetry.env" <<ENV
OM_DB_ADDRESS=${OM_DB_ADDRESS}
OM_DB_PORT=${OM_DB_PORT}
OM_DB_NAME=${OM_DB_NAME}
OM_DB_USER=${OM_DB_USER}
OM_DB_PASSWORD=${OM_DB_PASSWORD}
ENV

echo "==> 4/6 systemd unit"
scp -i "$KEY" -o StrictHostKeyChecking=no "$HERE/oc-telemetry.service" \
  "$HOST:/etc/systemd/system/oc-telemetry.service"
$SSH "$HOST" "systemctl daemon-reload && systemctl enable --now oc-telemetry && \
  systemctl restart oc-telemetry && sleep 1 && systemctl is-active oc-telemetry"

echo "==> 5/6 nginx snippet → /etc/nginx/snippets/oc-telemetry.conf"
$SSH "$HOST" "mkdir -p /etc/nginx/snippets"
scp -i "$KEY" -o StrictHostKeyChecking=no "$HERE/nginx-telemetry.snippet" \
  "$HOST:/etc/nginx/snippets/oc-telemetry.conf"

echo "==> 6/6 health check (loopback)"
$SSH "$HOST" "curl -sf -o /dev/null -w 'oc-telemetry health http %{http_code}\n' \
  http://127.0.0.1:8787/telemetry/health"

echo
echo "Service deployed. Remaining wiring (see README.md):"
echo "  - add  include /etc/nginx/snippets/oc-telemetry.conf;  to each site's 443 vhost, nginx -t && reload"
echo "  - register Kelvin + Kirchhoff websites in the OM Umami, put their ids in each main.js"
echo "  - the openconverters_telemetry schema is auto-created on the first event"
