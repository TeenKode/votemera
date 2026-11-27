#!/usr/bin/env bash
# Sourced by the root maintenance commands.
APP=/opt/votemera
DATA=/var/lib/votemera
DB="$DATA/votemera.db"
CONF=/etc/votemera/server.env
BACKUPS=/var/backups/votemera
STATUS_DIR=/var/lib/votemera-update

set_maintenance_status() {
  local temp
  temp=$(mktemp "$STATUS_DIR/.status.XXXXXX") &&
    printf '%s\n' "$1" > "$temp" && chmod 0644 "$temp" && mv -f "$temp" "$STATUS_DIR/status"
}

lock_maintenance() {
  install -d -m 0700 -o root -g root "$BACKUPS"
  install -d -m 0755 -o root -g root "$STATUS_DIR"
  exec 9>"$BACKUPS/.maintenance.lock"
  flock -n 9 || { echo 'Уже выполняется другая операция обслуживания'; return 1; }
}

voting_closed() {
  # Wait for commands that passed the maintenance check before the status changed.
  [[ $(sqlite3 -cmd '.timeout 10000' "$DB" "BEGIN IMMEDIATE; SELECT opened AND (deadline IS NULL OR deadline>unixepoch('subsec')) FROM settings CROSS JOIN runtime_config WHERE settings.id=1 AND runtime_config.id=1; COMMIT;") == 0 ]] || {
    echo 'Сначала закройте голосование'; return 1;
  }
}

wait_for_health() {
  local port version="$1"
  port=$(sed -n 's/^VOTEMERA_PORT=//p' "$CONF")
  [[ "$port" =~ ^[0-9]+$ ]] || return 1
  for _ in {1..20}; do
    if curl -fsS --max-time 2 "http://127.0.0.1:$port/health" 2>/dev/null | python3 -c \
      'import json,sys; d=json.load(sys.stdin); sys.exit(0 if d.get("status")=="ok" and d.get("version")==sys.argv[1] else 1)' "$version" 2>/dev/null; then
      return 0
    fi
    sleep 1
  done
  return 1
}

replace_database() {
  local source="$1" target="$2" mode="$3" temp
  temp=$(mktemp "$(dirname "$target")/.restore.XXXXXX") || return 1
  if install -o votemera -g votemera -m "$mode" "$source" "$temp" &&
    rm -f -- "$target-wal" "$target-shm" "$target-journal" && mv -fT -- "$temp" "$target"; then
    return 0
  fi
  rm -f -- "$temp"
  return 1
}
