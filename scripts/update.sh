#!/usr/bin/env bash
set -euo pipefail
[[ $EUID == 0 ]] || exit 1
cd /opt/votemera
[[ -d .git ]] || { echo 'Обновление требует Git-клона в /opt/votemera'; exit 1; }
[[ -z $(git status --porcelain) ]] || { echo 'Рабочее дерево изменено; обновление отменено'; exit 1; }
old=$(git rev-parse HEAD)
git fetch origin
branch=$(git symbolic-ref --short HEAD)
git merge-base --is-ancestor HEAD "origin/$branch" || { echo 'Не fast-forward'; exit 1; }
[[ $(git rev-parse "origin/$branch") != "$old" ]] || { echo 'Уже актуально'; exit 0; }
install -d -m 0700 /var/backups/votemera
backup="/var/backups/votemera/$(date +%Y%m%d-%H%M%S).db"
# Stop writes for a consistent rollback boundary.
systemctl stop votemera
if ! sqlite3 /var/lib/votemera/votemera.db ".backup '$backup'"; then systemctl start votemera; exit 1; fi
if git merge --ff-only "origin/$branch" && .venv/bin/pip install -r requirements.txt && systemctl start votemera; then
  port=$(sed -n 's/^VOTEMERA_PORT=//p' /etc/votemera/server.env)
  for i in {1..15}; do if curl -fsS "http://127.0.0.1:$port/health"; then echo; exit 0; fi; sleep 1; done
fi
echo 'Ошибка обновления, откат к предыдущему коммиту и БД' >&2
systemctl stop votemera || true
git reset --hard "$old"
rm -f /var/lib/votemera/votemera.db{,-wal,-shm}
install -o votemera -g votemera -m 0640 "$backup" /var/lib/votemera/votemera.db
.venv/bin/pip install -r requirements.txt
systemctl start votemera
exit 1
