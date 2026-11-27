#!/usr/bin/env bash
set -euo pipefail
[[ $EUID == 0 ]] || exit 1
status_dir=/var/lib/votemera-update
set_status(){ local f; f=$(mktemp "$status_dir/.status.XXXXXX"); printf '%s\n' "$1" > "$f"; chmod 0644 "$f"; mv -f "$f" "$status_dir/status"; }
set_status running
trap 'rc=$?; if ((rc)); then set_status failed; else set_status success; fi' EXIT
rm -f /var/lib/votemera/update-request
[[ $(sqlite3 /var/lib/votemera/votemera.db 'SELECT opened FROM settings WHERE id=1;') == 0 ]] || exit 1
cd /opt/votemera
[[ $(git symbolic-ref --short HEAD) == main ]] || exit 1
[[ $(git remote get-url origin) == https://github.com/TeenKode/votemera.git ]] || exit 1
bash scripts/update.sh
