#!/usr/bin/env bash
set -euo pipefail
[[ $EUID == 0 ]] || { echo 'Запустите от root'; exit 1; }
source "$(dirname "$0")/maintenance.sh"
cd "$APP"
lock_maintenance

stopped=0
switched=0
prepared=''
previous=''
swap="$APP/.venv-swap-$$"
cleanup() {
  local result=$?
  trap - EXIT INT TERM
  if ((result && stopped)); then
    echo 'Возвращаем предыдущий релиз и резервную копию' >&2
    if ! rollback; then
      echo "Откат не завершился; резервные копии: $BACKUPS" >&2
      set_maintenance_status failed
      exit "$result"
    fi
  fi
  rm -f -- "$swap"
  if ((result)); then
    if [[ -n "$prepared" ]]; then rm -rf -- "$prepared"; fi
    set_maintenance_status failed
  else
    set_maintenance_status success
  fi
  exit "$result"
}

activate_environment() {
  rm -f -- "$swap" && ln -s -- "$1" "$swap" && mv -fT -- "$swap" "$APP/.venv"
}

rollback() {
  if ((switched)); then
    systemctl stop votemera && git reset --hard "$old" && activate_environment "$previous" &&
      replace_database "$backup" "$DB" 0640 || return 1
    if [[ -f "$backup.access" ]]; then
      replace_database "$backup.access" "$DB.access" 0600 || return 1
    else
      rm -f -- "$DB.access" "$DB.access-wal" "$DB.access-shm" "$DB.access-journal" || return 1
    fi
  elif [[ -n "$previous" && -x "$previous/bin/python" && ! -e .venv ]]; then
    activate_environment "$previous" || return 1
  fi
  systemctl start votemera && wait_for_health "$version"
}

set_maintenance_status running
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
rm -f -- "$DATA/update-request"
[[ -d .git ]] || { echo 'Обновление требует Git-клона в /opt/votemera'; exit 1; }
[[ -z $(git status --porcelain) ]] || { echo 'Рабочее дерево изменено; обновление отменено'; exit 1; }
voting_closed

old=$(git rev-parse HEAD)
version=$(cat VERSION)
tag=$(.venv/bin/python release_target.py "$version")
[[ "$tag" != current ]] || { echo 'Стабильная версия актуальна'; exit 0; }
[[ "$tag" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]
export GIT_TERMINAL_PROMPT=0 GIT_ASKPASS=/bin/false GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null
[[ $(git remote get-url origin) == https://github.com/TeenKode/votemera.git ]] || { echo 'Требуется официальный публичный origin'; exit 1; }
[[ $(git symbolic-ref --short HEAD) == main ]] || { echo 'Требуется ветка main'; exit 1; }
git -c credential.helper= fetch --no-tags origin refs/heads/main:refs/remotes/origin/main "refs/tags/$tag:refs/tags/$tag"
target=$(git rev-parse "$tag^{commit}")
git merge-base --is-ancestor "$old" "$target" && git merge-base --is-ancestor "$target" origin/main || {
  echo 'Релиз не является продолжением main'; exit 1;
}
[[ $(git show "$target:VERSION") == "${tag#v}" ]] || { echo 'Несовпадение версии релиза'; exit 1; }

# Stage the verified release's dependencies while the current service is running.
venvs=/opt/votemera-venvs
install -d -m 0755 "$venvs"
prepared=$(mktemp -d "$venvs/$tag.XXXXXX")
chmod 0755 "$prepared"
git show "$target:requirements.lock" > "$prepared/requirements.lock"
python3 -m venv "$prepared/venv"
"$prepared/venv/bin/python" -m pip install --require-hashes --only-binary=:all: -r "$prepared/requirements.lock"
"$prepared/venv/bin/python" -m pip check

voting_closed
stopped=1
systemctl stop votemera
backup=$(mktemp "$BACKUPS/upgrade.XXXXXX.db")
sqlite3 "$DB" ".backup '$backup'"
if [[ -f "$DB.access" ]]; then
  (umask 077; sqlite3 "$DB.access" ".backup '$backup.access'")
fi

if [[ -L .venv ]]; then
  previous=$(readlink -f .venv)
else
  # Old installations have a real directory. Restore its original path via the active symlink.
  legacy=$(mktemp -d "$venvs/legacy.XXXXXX")
  chmod 0755 "$legacy"
  previous="$legacy/venv"
  mv -- .venv "$previous"
fi
switched=1
git merge --ff-only "$target"
activate_environment "$prepared/venv"
systemctl start votemera
wait_for_health "${tag#v}"
if ! bash scripts/install-backup.sh; then
  echo 'Не удалось зарегистрировать таймер резервного копирования' >&2
fi
echo 'Обновление завершено'
