#!/bin/bash
# ============================================================================
# DeckDock — NAS SSHFS Mount
# ============================================================================
# Mounts the NAS via SSHFS (no root/sudo required — FUSE userspace mount).
# Called by nas-mount.service or manually.
#
# Usage: nas-mount.sh [mount|unmount|status]
# ============================================================================

set -euo pipefail

# --- Config (defaults, overridden by config.env if present) ---
NAS_HOST=""
NAS_USER="root"
NAS_EXPORT=""
NAS_MOUNT="/tmp/nas-roms"

# Load config if available
for config in "$HOME/DeckDock/config.env" "$HOME/Emulation/tools/config.env"; do
    if [ -f "$config" ]; then
        eval "$(grep -E '^(NAS_HOST|NAS_USER|NAS_EXPORT|NAS_MOUNT)=' "$config")"
        break
    fi
done

ACTION="${1:-mount}"

case "$ACTION" in
    mount)
        # Already mounted?
        if mountpoint -q "$NAS_MOUNT" 2>/dev/null; then
            echo "NAS already mounted at $NAS_MOUNT"
            exit 0
        fi

        # Preflight
        if [ -z "$NAS_HOST" ] || [ -z "$NAS_EXPORT" ]; then
            echo "ERROR: NAS_HOST or NAS_EXPORT not configured."
            exit 1
        fi

        # Can we reach the NAS?
        if ! ping -c 1 -W 3 "$NAS_HOST" >/dev/null 2>&1; then
            echo "NAS unreachable at $NAS_HOST — skipping mount."
            exit 0
        fi

        mkdir -p "$NAS_MOUNT"

        # Mount via SSHFS — all userspace, no sudo needed
        sshfs \
            -o reconnect \
            -o ServerAliveInterval=15 \
            -o ServerAliveCountMax=3 \
            -o ConnectTimeout=10 \
            -o follow_symlinks \
            -o StrictHostKeyChecking=accept-new \
            -o IdentityFile="$HOME/.ssh/id_ed25519" \
            "${NAS_USER}@${NAS_HOST}:${NAS_EXPORT}" \
            "$NAS_MOUNT"

        echo "NAS mounted at $NAS_MOUNT"
        ;;

    unmount|umount)
        if mountpoint -q "$NAS_MOUNT" 2>/dev/null; then
            fusermount -u "$NAS_MOUNT" 2>/dev/null || \
                fusermount3 -u "$NAS_MOUNT" 2>/dev/null || \
                echo "WARNING: Could not unmount $NAS_MOUNT"
            echo "NAS unmounted."
        else
            echo "NAS not mounted."
        fi
        ;;

    status)
        if mountpoint -q "$NAS_MOUNT" 2>/dev/null; then
            echo "Mounted at $NAS_MOUNT"
            ls "$NAS_MOUNT" 2>/dev/null || echo "(contents unavailable)"
        else
            echo "Not mounted."
        fi
        ;;

    *)
        echo "Usage: nas-mount.sh [mount|unmount|status]"
        exit 1
        ;;
esac
