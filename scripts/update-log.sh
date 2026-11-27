#!/usr/bin/env bash
# Only fixed, operator-safe messages are exposed to the web log.
# Raw output of external programs stays in the systemd journal (may contain URLs).
update_note() {
  local line
  line="[$(date '+%Y-%m-%d %H:%M:%S %z')] $*"
  printf '%s\n' "$line"
  if [[ "${VOTEMERA_PANEL_LOG:-0}" == 1 ]]; then
    printf '%s\n' "$line" >> /var/lib/votemera-update/update.log || true
  fi
}
