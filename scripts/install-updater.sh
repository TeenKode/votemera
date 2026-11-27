#!/usr/bin/env bash
set -euo pipefail
[[ $EUID == 0 ]] || exit 1
install -d -o root -g root -m 0755 /var/lib/votemera-update
if [[ ! -f /var/lib/votemera-update/status ]]; then
  printf 'idle\n' > /var/lib/votemera-update/status
  chmod 0644 /var/lib/votemera-update/status
fi
cat > /etc/systemd/system/votemera-update.service <<'UNIT'
[Unit]
Description=Votemera maintenance update
[Service]
Type=oneshot
ExecStart=/bin/bash /opt/votemera/scripts/panel-update.sh
TimeoutStartSec=900
UNIT
cat > /etc/systemd/system/votemera-update.path <<'UNIT'
[Unit]
Description=Watch Votemera update requests
[Path]
PathExists=/var/lib/votemera/update-request
Unit=votemera-update.service
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now votemera-update.path
