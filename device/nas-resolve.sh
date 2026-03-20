#!/bin/bash
# ============================================================================
# DeckDock — NAS Host Resolver (sourceable)
# ============================================================================
# Source this after loading config.env. If NAS_TAILSCALE_HOST is set and the
# LAN NAS_HOST is unreachable, transparently switches NAS_HOST to the
# Tailscale IP. Scripts don't need to know which path they're using.
#
# Usage:  . nas-resolve.sh
# ============================================================================

if [ -n "${NAS_TAILSCALE_HOST:-}" ] && [ -n "${NAS_HOST:-}" ]; then
    if ! ping -c 1 -W 1 "$NAS_HOST" >/dev/null 2>&1; then
        if ping -c 1 -W 3 "$NAS_TAILSCALE_HOST" >/dev/null 2>&1 || \
           ssh -n -o ConnectTimeout=5 -o BatchMode=yes \
               -o IdentityFile="${HOME}/.ssh/id_ed25519" \
               "${NAS_USER:-root}@${NAS_TAILSCALE_HOST}" true >/dev/null 2>&1; then
            NAS_HOST="$NAS_TAILSCALE_HOST"
        fi
    fi
fi
