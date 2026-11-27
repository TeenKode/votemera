#!/usr/bin/env bash
set -euo pipefail
[[ $EUID == 0 ]] || exit 1
cd /opt/votemera
source scripts/update-log.sh
update_note 'Проверка рабочей копии и блокировки обслуживания'
[[ -d .git ]] || { update_note 'Обновление требует Git-клона в /opt/votemera'; exit 1; }
[[ -z $(git status --porcelain) ]] || { update_note 'Рабочее дерево изменено; обновление отменено'; exit 1; }
install -d -m 0700 -o root -g root /var/backups/votemera
exec 9>/var/backups/votemera/.maintenance.lock
flock -n 9 || { update_note 'Обновление уже выполняется'; exit 1; }
old=$(git rev-parse HEAD)
[[ $(sqlite3 /var/lib/votemera/votemera.db "SELECT opened AND (deadline IS NULL OR deadline>CAST(strftime('%s','now') AS INTEGER)) FROM settings CROSS JOIN runtime_config WHERE settings.id=1 AND runtime_config.id=1;") == 0 ]] || { update_note 'Сначала закройте голосование'; exit 1; }
version=$(cat VERSION)
update_note 'Проверка последнего стабильного релиза GitHub'
tag=$(.venv/bin/python release_target.py "$version") || { update_note 'Не удалось определить стабильный релиз. Проверьте доступ к GitHub.'; exit 1; }
[[ "$tag" != current ]] || { update_note 'Стабильная версия актуальна'; exit 0; }
[[ "$tag" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || { update_note "Некорректная версия релиза"; exit 1; }
update_note "Найдена версия $tag. Загрузка изменений GitHub."
# Official public origin only; never prompt for secrets in systemd or the console.
export GIT_TERMINAL_PROMPT=0 GIT_ASKPASS=/bin/false GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null
[[ $(git remote get-url origin) == https://github.com/TeenKode/votemera.git ]] || { update_note 'Неверный URL origin: ожидается официальный публичный репозиторий TeenKode/votemera'; exit 1; }
git -c credential.helper= fetch --no-tags origin refs/heads/main:refs/remotes/origin/main "refs/tags/$tag:refs/tags/$tag" || { update_note 'Не удалось получить обновления с GitHub без авторизации. Проверьте сеть и публичную доступность репозитория.' >&2; exit 1; }
branch=$(git symbolic-ref --short HEAD)
[[ "$branch" == main ]] || { update_note 'Требуется ветка main'; exit 1; }
target=$(git rev-parse "$tag^{commit}")
git merge-base --is-ancestor "$old" "$target" && git merge-base --is-ancestor "$target" origin/main || { update_note 'Релиз не является продолжением main'; exit 1; }
[[ $(git show "$target:VERSION") == "${tag#v}" ]] || { update_note 'Несовпадение версии релиза'; exit 1; }
install -d -m 0700 /var/backups/votemera
if [[ ! -f /usr/share/fonts/truetype/dejavu/DejaVuSans.ttf ]]; then update_note "Установка шрифта PDF"; apt-get update && apt-get install -y fonts-dejavu-core || { update_note 'Не удалось установить шрифт PDF'; exit 1; }; fi
backup=$(mktemp /var/backups/votemera/XXXXXXXX.db)
access_backup="$backup.access"
# Stop writes for a consistent rollback boundary.
update_note 'Остановка сервера для резервного копирования. Панель временно недоступна.'
systemctl stop votemera
update_note 'Создание резервной копии базы голосований'
if ! sqlite3 /var/lib/votemera/votemera.db ".backup '$backup'"; then update_note 'Не удалось сохранить базу голосований'; systemctl start votemera; exit 1; fi
if [[ -f /var/lib/votemera/votemera.db.access ]]; then
  update_note 'Создание резервной копии настроек доступа'
  if ! sqlite3 /var/lib/votemera/votemera.db.access ".backup '$access_backup'"; then update_note 'Не удалось сохранить настройки доступа'; systemctl start votemera; exit 1; fi
  chmod 0600 "$access_backup"
fi
update_note 'Резервные копии готовы. Применение файлов релиза.'
if git merge --ff-only "$target" && { update_note 'Установка зависимостей Python'; .venv/bin/pip install --disable-pip-version-check -r requirements.txt; } && { update_note 'Запуск обновлённого сервера'; systemctl start votemera; }; then
  update_note 'Проверка работоспособности и версии сервера'
  port=$(sed -n 's/^VOTEMERA_PORT=//p' /etc/votemera/server.env)
  for i in {1..15}; do if curl -fsS "http://127.0.0.1:$port/health" | .venv/bin/python -c 'import json,sys; d=json.load(sys.stdin); sys.exit(0 if d.get("status")=="ok" and d.get("version")==sys.argv[1] else 1)' "${tag#v}"; then update_note 'Сервер ответил успешно. Проверка таймера резервного копирования.'; if ! bash scripts/install-backup.sh; then update_note 'Предупреждение: не удалось зарегистрировать таймер бэкапов; повторите установку таймера' >&2; fi; update_note 'Обновление завершено'; exit 0; fi; update_note "Ожидание ответа сервера: попытка $i из 15"; sleep 1; done
fi
update_note 'Ошибка обновления, откат к предыдущему коммиту и БД' >&2
systemctl stop votemera || true
git reset --hard "$old"
rm -f /var/lib/votemera/votemera.db{,-wal,-shm}
install -o votemera -g votemera -m 0640 "$backup" /var/lib/votemera/votemera.db
if [[ -f "$access_backup" ]]; then
  rm -f /var/lib/votemera/votemera.db.access{,-wal,-shm}
  install -o votemera -g votemera -m 0600 "$access_backup" /var/lib/votemera/votemera.db.access
fi
update_note 'Восстановление зависимостей предыдущей версии'
.venv/bin/pip install -r requirements.txt
systemctl start votemera
update_note 'Предыдущая версия восстановлена'
exit 1
