#!/bin/bash
# ============================================================================
# SAVE BACKUP — Backs up emulator save files with rolling retention
#
# Collects saves from all known emulator paths, creates a timestamped
# tarball, rotates old backups, and pushes to NAS via SCP over SSH.
#
# Configuration search order:
#   1. $DECKDOCK_CONFIG
#   2. ~/DeckDock/config.env
#   3. ~/Emulation/tools/config.env
#   4. Built-in defaults
#
# Designed to run on the handheld device (Steam Deck, Legion Go, etc.)
# ============================================================================

set -uo pipefail

# --- Load configuration ---------------------------------------------------
load_config() {
    local candidates=(
        "${DECKDOCK_CONFIG:-}"
        "$HOME/DeckDock/config.env"
        "$HOME/Emulation/tools/config.env"
    )
    for f in "${candidates[@]}"; do
        if [ -n "$f" ] && [ -f "$f" ]; then
            # shellcheck source=/dev/null
            source "$f"
            return 0
        fi
    done
    return 1
}

load_config  # Non-fatal; defaults below cover the gap

# --- Resolve variables with defaults ---------------------------------------
BACKUP_KEEP="${BACKUP_KEEP:-10}"
NAS_HOST="${NAS_HOST:-}"
NAS_USER="${NAS_USER:-root}"
NAS_EXPORT="${NAS_EXPORT:-}"
NAS_SAVE_SUBDIR="${NAS_SAVE_SUBDIR:-saves}"
SSH_KEY="$HOME/.ssh/id_ed25519"

BACKUP_DIR="$HOME/Emulation/save-backups"
TIMESTAMP=$(date '+%Y%m%d-%H%M%S')
ARCHIVE="$BACKUP_DIR/saves-$TIMESTAMP.tar.gz"
WORK_DIR=$(mktemp -d)

# --- Colours ---------------------------------------------------------------
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

log() { echo -e "[$(date '+%H:%M:%S')] $1"; }

# --- Helper: copy a directory if it exists and has files -------------------
collect_dir() {
    local src="$1" dest="$2"
    if [ -d "$src" ] && [ -n "$(ls -A "$src" 2>/dev/null)" ]; then
        mkdir -p "$dest"
        cp -a "$src"/. "$dest/" 2>/dev/null
        return 0
    fi
    return 1
}

# --- Collect saves from all known emulator locations -----------------------
collect_saves() {
    local count=0

    # RetroArch (flatpak)
    local ra="$HOME/.var/app/org.libretro.RetroArch/config/retroarch"
    collect_dir "$ra/saves"  "$WORK_DIR/retroarch/saves"  && count=$((count + 1))
    collect_dir "$ra/states" "$WORK_DIR/retroarch/states" && count=$((count + 1))

    # Azahar / Citra — 3DS (AppImage, saves in ~/.local/share/azahar-emu/)
    local azahar="$HOME/.local/share/azahar-emu"
    collect_dir "$azahar/sdmc"   "$WORK_DIR/azahar/sdmc"   && count=$((count + 1))
    collect_dir "$azahar/nand"   "$WORK_DIR/azahar/nand"   && count=$((count + 1))
    collect_dir "$azahar/states" "$WORK_DIR/azahar/states"  && count=$((count + 1))

    # DuckStation — PSX (AppImage, saves in ~/.local/share/duckstation/)
    local duck="$HOME/.local/share/duckstation"
    collect_dir "$duck/memcards"   "$WORK_DIR/duckstation/memcards"   && count=$((count + 1))
    collect_dir "$duck/savestates" "$WORK_DIR/duckstation/savestates" && count=$((count + 1))

    # PCSX2 — PS2 (AppImage, saves in ~/.config/PCSX2/)
    local pcsx2="$HOME/.config/PCSX2"
    collect_dir "$pcsx2/memcards" "$WORK_DIR/pcsx2/memcards" && count=$((count + 1))
    collect_dir "$pcsx2/sstates"  "$WORK_DIR/pcsx2/sstates"  && count=$((count + 1))

    # Dolphin — GameCube / Wii (flatpak)
    local dolphin="$HOME/.var/app/org.DolphinEmu.dolphin-emu/data/dolphin-emu"
    collect_dir "$dolphin/GC"         "$WORK_DIR/dolphin/GC"         && count=$((count + 1))
    collect_dir "$dolphin/Wii"        "$WORK_DIR/dolphin/Wii"        && count=$((count + 1))
    collect_dir "$dolphin/StateSaves" "$WORK_DIR/dolphin/StateSaves" && count=$((count + 1))

    # PPSSPP (flatpak)
    local ppsspp="$HOME/.var/app/org.ppsspp.PPSSPP/config/ppsspp/PSP"
    collect_dir "$ppsspp/SAVEDATA"     "$WORK_DIR/ppsspp/SAVEDATA"     && count=$((count + 1))
    collect_dir "$ppsspp/PPSSPP_STATE" "$WORK_DIR/ppsspp/PPSSPP_STATE" && count=$((count + 1))

    # melonDS — NDS (flatpak)
    local melonds="$HOME/.var/app/net.kuribo64.melonDS/data/melonDS"
    collect_dir "$melonds" "$WORK_DIR/melonds" && count=$((count + 1))

    # xemu — Xbox (flatpak)
    local xemu="$HOME/.var/app/app.xemu.xemu/data/xemu/xemu"
    if [ -f "$xemu/eeprom.bin" ]; then
        mkdir -p "$WORK_DIR/xemu"
        cp -a "$xemu/eeprom.bin" "$WORK_DIR/xemu/" 2>/dev/null && count=$((count + 1))
    fi

    echo "$count"
}

# --- Main -------------------------------------------------------------------
log "${CYAN}Starting save backup...${NC}"

COLLECTED=$(collect_saves)

if [ "$COLLECTED" -eq 0 ]; then
    log "${YELLOW}No save directories found. Nothing to back up.${NC}"
    rm -rf "$WORK_DIR"
    exit 0
fi

log "${CYAN}Collected saves from $COLLECTED source(s).${NC}"

# Create backup archive
mkdir -p "$BACKUP_DIR"
if ! tar -czf "$ARCHIVE" -C "$WORK_DIR" . 2>/dev/null; then
    log "${RED}Failed to create backup archive.${NC}"
    rm -rf "$WORK_DIR"
    exit 1
fi

rm -rf "$WORK_DIR"
ARCHIVE_SIZE=$(du -h "$ARCHIVE" | cut -f1)
log "${GREEN}Backup created: $ARCHIVE ($ARCHIVE_SIZE)${NC}"

# --- Rolling retention — prune old backups ----------------------------------
BACKUP_COUNT=$(find "$BACKUP_DIR" -maxdepth 1 -name "saves-*.tar.gz" | wc -l)
if [ "$BACKUP_COUNT" -gt "$BACKUP_KEEP" ]; then
    PRUNE=$((BACKUP_COUNT - BACKUP_KEEP))
    find "$BACKUP_DIR" -maxdepth 1 -name "saves-*.tar.gz" -print0 \
        | sort -z \
        | head -z -n "$PRUNE" \
        | xargs -0 rm -f
    log "${CYAN}Pruned $PRUNE old backup(s). Keeping $BACKUP_KEEP.${NC}"
fi

# --- Push to NAS via SCP (graceful skip if unreachable) ---------------------
if [ -n "$NAS_HOST" ] && [ -n "$NAS_EXPORT" ]; then
    if ssh -i "$SSH_KEY" -o ConnectTimeout=3 -o BatchMode=yes \
           "${NAS_USER}@${NAS_HOST}" "true" 2>/dev/null; then
        log "${CYAN}NAS reachable. Pushing backup via SCP...${NC}"

        NAS_SAVE_DIR="${NAS_EXPORT}/${NAS_SAVE_SUBDIR}"

        # Ensure remote directory exists
        ssh -n -i "$SSH_KEY" "${NAS_USER}@${NAS_HOST}" \
            "mkdir -p \"${NAS_SAVE_DIR}\"" 2>/dev/null

        # Push the archive
        if scp -i "$SSH_KEY" "$ARCHIVE" \
               "${NAS_USER}@${NAS_HOST}:${NAS_SAVE_DIR}/" 2>/dev/null; then

            # Prune old NAS backups — list, sort, keep newest BACKUP_KEEP
            NAS_COUNT=$(ssh -n -i "$SSH_KEY" "${NAS_USER}@${NAS_HOST}" \
                "ls -1 \"${NAS_SAVE_DIR}/\" 2>/dev/null | grep -c '^saves-.*\\.tar\\.gz$'")
            if [ "${NAS_COUNT:-0}" -gt "$BACKUP_KEEP" ]; then
                NAS_PRUNE=$((NAS_COUNT - BACKUP_KEEP))
                ssh -n -i "$SSH_KEY" "${NAS_USER}@${NAS_HOST}" \
                    "ls -1 \"${NAS_SAVE_DIR}/\" | grep '^saves-.*\\.tar\\.gz$' | sort | head -n ${NAS_PRUNE} | while read -r f; do rm -f \"${NAS_SAVE_DIR}/\$f\"; done" 2>/dev/null
                log "${CYAN}Pruned $NAS_PRUNE old NAS backup(s).${NC}"
            fi

            log "${GREEN}Backup pushed to NAS.${NC}"
        else
            log "${YELLOW}SCP failed. Backup saved locally only.${NC}"
        fi
    else
        log "${YELLOW}NAS unreachable. Backup saved locally only.${NC}"
    fi
else
    log "${YELLOW}NAS not configured. Backup saved locally only.${NC}"
fi

log "${GREEN}Save backup complete.${NC}"
