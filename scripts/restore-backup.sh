#!/usr/bin/env bash
set -euo pipefail
dir=$(cd "$(dirname "$0")" && pwd)
source "$dir/maintenance.sh"
check=0
if [[ ${1:-} == --check ]]; then check=1; shift; fi
[[ $# == 1 ]] || { echo 'Использование: restore-backup.sh [--check] ФАЙЛ_ИЛИ_КАТАЛОГ'; exit 1; }
((check || EUID == 0)) || { echo 'Запустите восстановление от root'; exit 1; }
stage=$(mktemp -d)
trap 'rm -rf -- "$stage"' EXIT
python3 "$dir/prepare-restore.py" "$1" "$stage"
echo 'Копия проверена и подготовлена'
((check == 0)) || exit 0

stopped=0
replaced=0
version=$(cat "$APP/VERSION")
lock_maintenance
set_maintenance_status running

replace_config() {
  local file
  file=$(mktemp "$(dirname "$CONF")/.restore.XXXXXX") &&
    install -o root -g root -m 0600 "$1" "$file" && mv -fT -- "$file" "$CONF"
}

recover() {
  if ((replaced)); then
    systemctl stop votemera && replace_database "$previous/votemera.db" "$DB" 0640 || return 1
    if [[ -f "$stage/votemera.db.access" ]]; then
      replace_database "$previous/votemera.db.access" "$DB.access" 0600 &&
        replace_config "$previous/server.env" || return 1
    fi
  fi
  systemctl start votemera && wait_for_health "$version"
}

finish() {
  local result=$?
  trap - EXIT INT TERM
  if ((result)); then
    if ((stopped)) && ! recover; then echo "Проверьте службу и предыдущую копию: $previous" >&2; fi
    set_maintenance_status failed
  else
    set_maintenance_status success
  fi
  rm -rf -- "$stage"
  exit "$result"
}

trap finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
[[ ! -e "$DATA/update-request" ]] || { echo 'Дождитесь установки обновления'; exit 1; }
voting_closed
previous=$(mktemp -d "$BACKUPS/restore.XXXXXX")
stopped=1
systemctl stop votemera
sqlite3 "$DB" ".backup '$previous/votemera.db'"
if [[ -f "$stage/votemera.db.access" ]]; then
  sqlite3 "$DB.access" ".backup '$previous/votemera.db.access'"
  install -m 0600 "$CONF" "$previous/server.env"
fi

replaced=1
replace_database "$stage/votemera.db" "$DB" 0640
if [[ -f "$stage/votemera.db.access" ]]; then
  replace_database "$stage/votemera.db.access" "$DB.access" 0600
  replace_config "$stage/server.env"
fi
systemctl start votemera
wait_for_health "$version"
echo "Копия восстановлена. Текущий этап закрыт, результаты скрыты. Предыдущие данные: $previous"
