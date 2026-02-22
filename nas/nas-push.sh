#!/bin/bash
# ============================================================================
# NAS PUSH — Syncs staged files from the PC directly to NAS via SSH/SCP
#
# Pushes files directly to the NAS over SSH — no device intermediary needed.
# Files are chmod'd a+r after push so SSHFS on the device can read them.
#
# Configuration is read from config.env. Search order:
#   1. $DECKDOCK_CONFIG               (explicit override)
#   2. $(dirname "$0")/../config.env   (project-relative)
#
# Usage: bash nas-push.sh [staging-dir]
#   staging-dir overrides STAGING_DIR from config.
# ============================================================================

# --- Load configuration ---------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG_FILE="${DECKDOCK_CONFIG:-$SCRIPT_DIR/../config.env}"

if [ ! -f "$CONFIG_FILE" ]; then
    echo "[ERROR] Config not found: $CONFIG_FILE"
    echo "  Copy config.example.env to config.env and fill in your values."
    exit 1
fi
# shellcheck source=../config.env
source "$CONFIG_FILE"

# --- Resolve variables (allow CLI override for staging dir) ----------------
STAGING_DIR="${1:-${STAGING_DIR:-$HOME/nas-staging}}"
NAS_HOST="${NAS_HOST:?NAS_HOST must be set in config.env}"
NAS_USER="${NAS_USER:-root}"
NAS_EXPORT="${NAS_EXPORT:?NAS_EXPORT must be set in config.env}"
NAS_ROM_SUBDIR="${NAS_ROM_SUBDIR:-roms}"
SSH_KEY="$HOME/.ssh/id_ed25519"

# --- Colours ---------------------------------------------------------------
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

log() { echo -e "[$(date '+%H:%M:%S')] $1"; }

SSH_TARGET="${NAS_USER}@${NAS_HOST}"
_ssh() { ssh -n -i "$SSH_KEY" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=5 "$SSH_TARGET" "$@"; }

# --- Preflight checks ------------------------------------------------------
if [ ! -d "$STAGING_DIR" ]; then
    log "${RED}Staging directory not found: $STAGING_DIR${NC}"
    exit 1
fi

# Count files to push (exclude state files, partial downloads, and directories)
FILE_COUNT=$(find "$STAGING_DIR" -type f ! -name ".crawler-state.json" ! -name "*.part" | wc -l)

if [ "$FILE_COUNT" -eq 0 ]; then
    log "${YELLOW}No files to push.${NC}"
    exit 0
fi

log "${CYAN}Found $FILE_COUNT files to push to NAS${NC}"

# Check NAS is reachable
if ! _ssh "echo ok" &>/dev/null; then
    log "${RED}Can't reach NAS ($NAS_HOST). Is it on?${NC}"
    exit 1
fi

# Ensure ROM subdirectory exists on NAS
_ssh "mkdir -p \"${NAS_EXPORT}/${NAS_ROM_SUBDIR}\""

# --- Push files directly to NAS via SCP ------------------------------------
log "${CYAN}Pushing files to NAS...${NC}"
PUSHED=0
ERRORS=0

while IFS= read -r -d '' file; do
    rel_path="${file#$STAGING_DIR/}"
    target_dir="${NAS_EXPORT}/${NAS_ROM_SUBDIR}/$(dirname "$rel_path")"
    filename="$(basename "$file")"

    # Ensure target dir exists
    _ssh "mkdir -p \"${target_dir}\"" 2>/dev/null

    # Check if file already exists on NAS
    if _ssh "test -f \"${target_dir}/${filename}\"" 2>/dev/null; then
        log "  ${YELLOW}Skipping (exists): ${rel_path}${NC}"
        rm -f "$file"
        PUSHED=$((PUSHED + 1))
        continue
    fi

    # SCP the file directly to NAS (modern SCP uses SFTP protocol internally,
    # so remote paths are used as-is — no shell escaping needed)
    scp -i "$SSH_KEY" \
        -o StrictHostKeyChecking=accept-new \
        -o ConnectTimeout=10 \
        "$file" "${SSH_TARGET}:${target_dir}/${filename}" >/dev/null 2>&1

    if [ $? -eq 0 ]; then
        # Set permissions so SSHFS on device can read
        _ssh "chmod a+r \"${target_dir}/${filename}\"" 2>/dev/null
        rm -f "$file"
        PUSHED=$((PUSHED + 1))
        log "  ${GREEN}Pushed: ${rel_path}${NC}"
    else
        ERRORS=$((ERRORS + 1))
        log "  ${RED}Failed: ${rel_path}${NC}"
    fi
done < <(find "$STAGING_DIR" -type f ! -name ".crawler-state.json" ! -name "*.part" -print0)

# Remove empty directories (but not the staging root)
find "$STAGING_DIR" -mindepth 1 -type d -empty -delete 2>/dev/null

if [ "$ERRORS" -eq 0 ]; then
    log "${GREEN}All done: $PUSHED files pushed to NAS.${NC}"
else
    log "${YELLOW}Done: $PUSHED pushed, $ERRORS failed. Failed files kept for retry.${NC}"
fi
