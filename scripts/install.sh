#!/usr/bin/env bash
set -euo pipefail
[[ $EUID == 0 ]] || { echo 'Запустите от root'; exit 1; }
SRC=$(cd "$(dirname "$0")/.." && pwd)
APP=/opt/votemera; CONF=/etc/votemera; DATA=/var/lib/votemera
apt-get update
apt-get install -y python3 python3-venv git curl rsync openssl sqlite3 fonts-dejavu-core
id votemera >/dev/null 2>&1 || useradd --system --home-dir "$DATA" --shell /usr/sbin/nologin votemera
install -d -m 0755 "$APP" "$CONF"; install -d -m 0750 -o votemera -g votemera "$DATA"
if [[ ! -f "$CONF/server.env" ]]; then
  (umask 077; printf 'VOTEMERA_ADMIN_PASSWORD=%s\nVOTEMERA_TERMINAL_KEY=%s\nVOTEMERA_DB=%s/votemera.db\nVOTEMERA_PORT=8080\nVOTEMERA_HOST=0.0.0.0\n' "$(openssl rand -hex 16)" "$(openssl rand -hex 8)" "$DATA" > "$CONF/server.env")
fi
if [[ "$SRC" != "$APP" ]]; then
  rsync -a --exclude .venv --exclude data --exclude __pycache__ --exclude .pytest_cache "$SRC/" "$APP/"
fi
python3 -m venv "$APP/.venv"
"$APP/.venv/bin/pip" install -r "$APP/requirements.txt"
cat > /etc/systemd/system/votemera.service <<'UNIT'
[Unit]
Description=Votemera voting server
After=network.target
[Service]
Type=simple
User=votemera
Group=votemera
WorkingDirectory=/opt/votemera
EnvironmentFile=/etc/votemera/server.env
ExecStart=/opt/votemera/.venv/bin/uvicorn app:app --host ${VOTEMERA_HOST} --port ${VOTEMERA_PORT} --no-access-log
Restart=on-failure
RestartSec=2
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/votemera
[Install]
WantedBy=multi-user.target
UNIT
bash "$APP/scripts/install-updater.sh"
bash "$APP/scripts/install-backup.sh"
systemctl daemon-reload
systemctl enable --now votemera
systemctl restart votemera
PORT=$(sed -n 's/^VOTEMERA_PORT=//p' "$CONF/server.env")
for i in {1..20}; do if curl -fsS "http://127.0.0.1:${PORT}/health"; then echo; break; fi; sleep 1; done
curl -fsS "http://127.0.0.1:${PORT}/health" >/dev/null
printf '\nАдреса: http://<IP-контейнера>:%s/{admin,vote,display}\n' "$PORT"
grep -E '^VOTEMERA_(ADMIN_PASSWORD|TERMINAL_KEY)=' "$CONF/server.env"
