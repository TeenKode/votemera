#!/usr/bin/env bash
set -euo pipefail
[[ $EUID == 0 ]] || { echo 'Запустите от root'; exit 1; }
bind=127.0.0.1
tls=()
case "$#" in
  0) ;;
  1) [[ "$1" == --trusted-http ]] || { echo 'Ожидается --trusted-http'; exit 1; }; bind=0.0.0.0 ;;
  3) [[ "$1" == --tls ]] || { echo 'Ожидается --tls FULLCHAIN KEY'; exit 1; }; tls=("$(realpath "$2")" "$(realpath "$3")") ;;
  *) echo 'Установка: install.sh [--tls FULLCHAIN KEY | --trusted-http]'; exit 1 ;;
esac
SRC=$(cd "$(dirname "$0")/.." && pwd)
APP=/opt/votemera; CONF=/etc/votemera; DATA=/var/lib/votemera
apt-get update
apt-get install -y python3 python3-venv git curl rsync openssl sqlite3 fonts-dejavu-core
id votemera >/dev/null 2>&1 || useradd --system --home-dir "$DATA" --shell /usr/sbin/nologin votemera
install -d -m 0755 "$APP" "$CONF"; install -d -m 0750 -o votemera -g votemera "$DATA"
new_config=0
if [[ ! -f "$CONF/server.env" ]]; then
  new_config=1
  (umask 077; printf 'VOTEMERA_ADMIN_PASSWORD=%s\nVOTEMERA_TERMINAL_KEY=%s\nVOTEMERA_DB=%s/votemera.db\nVOTEMERA_PORT=8080\nVOTEMERA_HOST=%s\n' "$(openssl rand -hex 16)" "$(openssl rand -hex 16)" "$DATA" "$bind" > "$CONF/server.env")
fi
if [[ "$SRC" != "$APP" ]]; then
  rsync -a --exclude .venv --exclude data --exclude __pycache__ --exclude .pytest_cache "$SRC/" "$APP/"
fi
if [[ ! -x "$APP/.venv/bin/python" ]]; then python3 -m venv "$APP/.venv"; fi
"$APP/.venv/bin/python" -m pip install --require-hashes --only-binary=:all: -r "$APP/requirements.lock"
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
ExecStart=/opt/votemera/.venv/bin/uvicorn app:app --host ${VOTEMERA_HOST} --port ${VOTEMERA_PORT} --workers 1 --proxy-headers --forwarded-allow-ips 127.0.0.1 --no-access-log
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
if ((${#tls[@]})); then
  bash "$APP/scripts/enable-https.sh" "${tls[@]}"
else
  HOST=$(sed -n 's/^VOTEMERA_HOST=//p' "$CONF/server.env")
  if [[ "$HOST" == 0.0.0.0 ]]; then HOST='<IP-контейнера>'; fi
  printf '\nАдреса: http://%s:%s/{admin,vote,display}\n' "$HOST" "$PORT"
fi
if ((new_config)); then grep -E '^VOTEMERA_(ADMIN_PASSWORD|TERMINAL_KEY)=' "$CONF/server.env"; else echo 'Существующий пароль и ключи сохранены; управление доступом — в панели'; fi
