#!/usr/bin/env bash
set -euo pipefail
[[ $EUID == 0 ]] || { echo 'Запустите от root'; exit 1; }
cd /opt/votemera
db=$(sed -n 's/^VOTEMERA_DB=//p' /etc/votemera/server.env)
[[ -n "$db" && -f "$db.access" ]] || { echo 'База доступа не найдена'; exit 1; }
runuser -u votemera -- env VOTEMERA_DB="$db" /opt/votemera/.venv/bin/python -c '
import getpass
import app
password=getpass.getpass("Новый пароль администратора: ")
if password!=getpass.getpass("Повторите пароль: "):
    raise SystemExit("Пароли не совпадают")
try:
    app.set_admin_password(app.checked_secret(password))
except app.HTTPException as exc:
    raise SystemExit(exc.detail)
print("Пароль изменён.")
'
