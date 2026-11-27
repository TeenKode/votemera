#!/usr/bin/env bash
set -euo pipefail
[[ $EUID == 0 ]] || exit 1
exec /bin/bash /opt/votemera/scripts/update.sh
