#!/usr/bin/env bash
set -euo pipefail
[[ $EUID == 0 ]] || exit 1
install -d -m 0700 -o root -g root /var/backups/votemera/daily
cat > /etc/systemd/system/votemera-backup.service <<'UNIT'
[Unit]
Description=Votemera daily data and access backup
After=votemera.service
[Service]
Type=oneshot
ExecStart=/opt/votemera/.venv/bin/python /opt/votemera/scripts/backup.py
UMask=0077
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=/var/backups/votemera /var/lib/votemera
UNIT
cat > /etc/systemd/system/votemera-backup.timer <<'UNIT'
[Unit]
Description=Daily Votemera backup
[Timer]
OnCalendar=*-*-* 03:00:00
Persistent=true
RandomizedDelaySec=300
[Install]
WantedBy=timers.target
UNIT
systemctl daemon-reload
systemctl enable --now votemera-backup.timer
