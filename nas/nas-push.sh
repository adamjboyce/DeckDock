#!/bin/bash
# ============================================================================
# NAS PUSH — Syncs staged files from the PC to NAS via the device
#
# The device (Steam Deck / Legion Go) has NFS mount access to the NAS;
# the PC typically does not. So we relay files through the device's mount.
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
DEVICE_HOST="${DEVICE_HOST:?DEVICE_HOST must be set in config.env}"
NAS_HOST="${NAS_HOST:?NAS_HOST must be set in config.env}"
NAS_EXPORT="${NAS_EXPORT:?NAS_EXPORT must be set in config.env}"
NAS_MOUNT="${NAS_MOUNT:-/tmp/nas-roms}"
NAS_ROM_SUBDIR="${NAS_ROM_SUBDIR:-roms}"

# --- Colours ---------------------------------------------------------------
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

log() { echo -e "[$(date '+%H:%M:%S')] $1"; }

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

# Check device is reachable
if ! ssh -o ConnectTimeout=3 -o BatchMode=yes "$DEVICE_HOST" "echo ok" &>/dev/null; then
    log "${RED}Can't reach device ($DEVICE_HOST). Is it on?${NC}"
    exit 1
fi

# --- Mount NAS on device ----------------------------------------------------
log "${CYAN}Mounting NAS on device...${NC}"
ssh "$DEVICE_HOST" "mkdir -p $NAS_MOUNT && sudo mount -t nfs ${NAS_HOST}:${NAS_EXPORT} $NAS_MOUNT -o hard,intr,nolock,timeo=600" 2>/dev/null
if [ $? -ne 0 ]; then
    log "${RED}Failed to mount NAS on device.${NC}"
    exit 1
fi

# Ensure ROM subdirectory exists on NAS
ssh "$DEVICE_HOST" "mkdir -p $NAS_MOUNT/$NAS_ROM_SUBDIR"

# --- Sync files via rsync through the device --------------------------------
log "${CYAN}Syncing files to NAS...${NC}"
rsync -avz --progress --no-group -m \
    --exclude=".crawler-state.json" \
    --exclude="*.part" \
    --ignore-existing \
    "$STAGING_DIR/" \
    "$DEVICE_HOST:$NAS_MOUNT/$NAS_ROM_SUBDIR/"

RESULT=$?

# --- Unmount NAS ------------------------------------------------------------
ssh "$DEVICE_HOST" "sudo umount $NAS_MOUNT" 2>/dev/null

# --- Post-sync cleanup ------------------------------------------------------
# rsync exit 23 = "some files/attrs not transferred" — typically just NFS
# timestamp permission errors, not actual file transfer failures. Treat as success.
if [ $RESULT -eq 0 ] || [ $RESULT -eq 23 ]; then
    log "${GREEN}All files pushed to NAS successfully.${NC}"

    # Clean up staged files (keep .crawler-state.json for crawl resume)
    CLEANED=0
    while IFS= read -r -d '' file; do
        rm -f "$file" && CLEANED=$((CLEANED + 1))
    done < <(find "$STAGING_DIR" -type f ! -name ".crawler-state.json" ! -name "*.part" -print0)

    # Remove empty directories (but not the staging root)
    find "$STAGING_DIR" -mindepth 1 -type d -empty -delete 2>/dev/null

    log "${GREEN}Cleaned up $CLEANED staged files. Staging dir ready for next crawl.${NC}"
else
    log "${RED}Sync had errors. Staged files kept for retry.${NC}"
fi
