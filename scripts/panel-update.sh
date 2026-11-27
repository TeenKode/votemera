#!/usr/bin/env bash
set -euo pipefail
[[ $EUID == 0 ]] || exit 1
status_dir=/var/lib/votemera-update
cd /opt/votemera
source scripts/update-log.sh
set_status(){ local f; f=$(mktemp "$status_dir/.status.XXXXXX"); printf '%s\n' "$1" > "$f"; chmod 0644 "$f"; mv -f "$f" "$status_dir/status"; }
# New root-owned generation; atomic replacement means readers never see partial initialization.
log=$(mktemp "$status_dir/.log.XXXXXX")
chmod 0644 "$log"
mv -f "$log" "$status_dir/update.log"
export VOTEMERA_PANEL_LOG=1
set_status running
finish(){
  local rc=$?
  if ((rc)); then
    update_note "Обновление завершилось с ошибкой (код $rc). Подробности: journalctl -u votemera-update.service -n 100"
    set_status failed
  else
    update_note 'Обновление успешно завершено'
    set_status success
  fi
}
trap finish EXIT
trap 'exit 143' TERM
trap 'exit 130' INT
rm -f /var/lib/votemera/update-request
update_note 'Запрос обновления получен. Проверка условий запуска.'
[[ $(sqlite3 /var/lib/votemera/votemera.db "SELECT opened AND (deadline IS NULL OR deadline>CAST(strftime('%s','now') AS INTEGER)) FROM settings CROSS JOIN runtime_config WHERE settings.id=1 AND runtime_config.id=1;") == 0 ]] || { update_note 'Сначала закройте голосование'; exit 1; }
[[ $(git symbolic-ref --short HEAD) == main ]] || { update_note 'Требуется ветка main'; exit 1; }
[[ $(git remote get-url origin) == https://github.com/TeenKode/votemera.git ]] || { update_note 'Неверный адрес официального репозитория'; exit 1; }
bash scripts/update.sh
