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
NAS_TAILSCALE_HOST=""

# Load config if available
for config in "$HOME/DeckDock/config.env" "$HOME/Emulation/tools/config.env"; do
    if [ -f "$config" ]; then
        eval "$(grep -E '^(NAS_HOST|NAS_USER|NAS_EXPORT|NAS_MOUNT|NAS_TAILSCALE_HOST)=' "$config")"
        break
    fi
done

# Resolve NAS host (LAN → Tailscale fallback)
for _resolver in "$HOME/DeckDock/device/nas-resolve.sh" "$HOME/Emulation/tools/nas-resolve.sh"; do
    [ -f "$_resolver" ] && . "$_resolver" && break
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

        # Can we reach the NAS? (ping first, fall back to SSH probe for Tailscale subnet routing)
        if ! ping -c 1 -W 3 "$NAS_HOST" >/dev/null 2>&1; then
            if ! ssh -n -o ConnectTimeout=5 -o BatchMode=yes -o IdentityFile="$HOME/.ssh/id_ed25519" \
                    "${NAS_USER}@${NAS_HOST}" true >/dev/null 2>&1; then
                echo "NAS unreachable at $NAS_HOST — skipping mount."
                exit 0
            fi
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
