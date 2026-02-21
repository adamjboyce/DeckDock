#!/bin/bash
# ============================================================================
# SLEEP WATCHER — Monitors system suspend events and triggers save backup
#
# Listens to systemd-logind's PrepareForSleep signal via dbus-monitor.
# When the system is about to suspend, it runs save-backup.sh to snapshot
# all emulator saves before the device goes to sleep.
#
# Intended to run as a user-level systemd service on the handheld device.
# ============================================================================

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKUP_SCRIPT="$SCRIPT_DIR/save-backup.sh"

if [ ! -x "$BACKUP_SCRIPT" ]; then
    echo "[ERROR] save-backup.sh not found or not executable at: $BACKUP_SCRIPT"
    exit 1
fi

dbus-monitor --system "type='signal',interface='org.freedesktop.login1.Manager',member='PrepareForSleep'" 2>/dev/null |
while read -r line; do
    if echo "$line" | grep -q "boolean true"; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] Suspend detected — running save backup..."
        bash "$BACKUP_SCRIPT"
    fi
done
