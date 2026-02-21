#!/bin/bash
# ============================================================================
# SAVE BACKUP — Backs up emulator save files with rolling retention
#
# Collects saves from all known emulator paths, creates a timestamped
# tarball, rotates old backups, and optionally pushes to NAS via NFS.
#
# Configuration search order:
#   1. $DECKDOCK_CONFIG
#   2. ~/DeckDock/config.env
#   3. /etc/deckdock/config.env
#   4. Built-in defaults
#
# Designed to run on the handheld device (Steam Deck, Legion Go, etc.)
# ============================================================================

# --- Load configuration ---------------------------------------------------
load_config() {
    local candidates=(
        "${DECKDOCK_CONFIG:-}"
        "$HOME/DeckDock/config.env"
        "/etc/deckdock/config.env"
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
NAS_EXPORT="${NAS_EXPORT:-}"
NAS_MOUNT="${NAS_MOUNT:-/tmp/nas-roms}"
NAS_SAVE_SUBDIR="${NAS_SAVE_SUBDIR:-saves}"

BACKUP_DIR="$HOME/Emulation/save-backups"
TIMESTAMP=$(date '+%Y%m%d-%H%M%S')
ARCHIVE="$BACKUP_DIR/saves-$TIMESTAMP.tar.gz"
TMPDIR=$(mktemp -d)

# --- Colours ---------------------------------------------------------------
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

log() { echo -e "[$(date '+%H:%M:%S')] $1"; }

# --- Collect saves from all known emulator locations -----------------------
collect_saves() {
    local count=0

    # RetroArch
    local ra_saves="$HOME/.var/app/org.libretro.RetroArch/config/retroarch/saves"
    local ra_states="$HOME/.var/app/org.libretro.RetroArch/config/retroarch/states"
    if [ -d "$ra_saves" ]; then
        mkdir -p "$TMPDIR/retroarch/saves"
        cp -a "$ra_saves"/. "$TMPDIR/retroarch/saves/" 2>/dev/null && count=$((count + 1))
    fi
    if [ -d "$ra_states" ]; then
        mkdir -p "$TMPDIR/retroarch/states"
        cp -a "$ra_states"/. "$TMPDIR/retroarch/states/" 2>/dev/null && count=$((count + 1))
    fi

    # DuckStation
    local duck_memcards="$HOME/.var/app/org.duckstation.DuckStation/data/duckstation/memcards"
    local duck_states="$HOME/.var/app/org.duckstation.DuckStation/data/duckstation/savestates"
    if [ -d "$duck_memcards" ]; then
        mkdir -p "$TMPDIR/duckstation/memcards"
        cp -a "$duck_memcards"/. "$TMPDIR/duckstation/memcards/" 2>/dev/null && count=$((count + 1))
    fi
    if [ -d "$duck_states" ]; then
        mkdir -p "$TMPDIR/duckstation/savestates"
        cp -a "$duck_states"/. "$TMPDIR/duckstation/savestates/" 2>/dev/null && count=$((count + 1))
    fi

    # PCSX2
    local pcsx2_memcards="$HOME/.var/app/net.pcsx2.PCSX2/config/PCSX2/memcards"
    local pcsx2_states="$HOME/.var/app/net.pcsx2.PCSX2/config/PCSX2/sstates"
    if [ -d "$pcsx2_memcards" ]; then
        mkdir -p "$TMPDIR/pcsx2/memcards"
        cp -a "$pcsx2_memcards"/. "$TMPDIR/pcsx2/memcards/" 2>/dev/null && count=$((count + 1))
    fi
    if [ -d "$pcsx2_states" ]; then
        mkdir -p "$TMPDIR/pcsx2/sstates"
        cp -a "$pcsx2_states"/. "$TMPDIR/pcsx2/sstates/" 2>/dev/null && count=$((count + 1))
    fi

    # Dolphin (GameCube / Wii)
    local dolphin_gc="$HOME/.var/app/org.DolphinEmu.dolphin-emu/data/dolphin-emu/GC"
    local dolphin_wii="$HOME/.var/app/org.DolphinEmu.dolphin-emu/data/dolphin-emu/Wii"
    local dolphin_states="$HOME/.var/app/org.DolphinEmu.dolphin-emu/data/dolphin-emu/StateSaves"
    if [ -d "$dolphin_gc" ]; then
        mkdir -p "$TMPDIR/dolphin/GC"
        cp -a "$dolphin_gc"/. "$TMPDIR/dolphin/GC/" 2>/dev/null && count=$((count + 1))
    fi
    if [ -d "$dolphin_wii" ]; then
        mkdir -p "$TMPDIR/dolphin/Wii"
        cp -a "$dolphin_wii"/. "$TMPDIR/dolphin/Wii/" 2>/dev/null && count=$((count + 1))
    fi
    if [ -d "$dolphin_states" ]; then
        mkdir -p "$TMPDIR/dolphin/StateSaves"
        cp -a "$dolphin_states"/. "$TMPDIR/dolphin/StateSaves/" 2>/dev/null && count=$((count + 1))
    fi

    # PPSSPP
    local ppsspp_saves="$HOME/.var/app/org.ppsspp.PPSSPP/config/ppsspp/PSP/SAVEDATA"
    local ppsspp_states="$HOME/.var/app/org.ppsspp.PPSSPP/config/ppsspp/PSP/PPSSPP_STATE"
    if [ -d "$ppsspp_saves" ]; then
        mkdir -p "$TMPDIR/ppsspp/SAVEDATA"
        cp -a "$ppsspp_saves"/. "$TMPDIR/ppsspp/SAVEDATA/" 2>/dev/null && count=$((count + 1))
    fi
    if [ -d "$ppsspp_states" ]; then
        mkdir -p "$TMPDIR/ppsspp/PPSSPP_STATE"
        cp -a "$ppsspp_states"/. "$TMPDIR/ppsspp/PPSSPP_STATE/" 2>/dev/null && count=$((count + 1))
    fi

    echo "$count"
}

# --- Main -------------------------------------------------------------------
log "${CYAN}Starting save backup...${NC}"

COLLECTED=$(collect_saves)

if [ "$COLLECTED" -eq 0 ]; then
    log "${YELLOW}No save directories found. Nothing to back up.${NC}"
    rm -rf "$TMPDIR"
    exit 0
fi

log "${CYAN}Collected saves from $COLLECTED source(s).${NC}"

# Create backup archive
mkdir -p "$BACKUP_DIR"
tar -czf "$ARCHIVE" -C "$TMPDIR" . 2>/dev/null

if [ $? -ne 0 ]; then
    log "${RED}Failed to create backup archive.${NC}"
    rm -rf "$TMPDIR"
    exit 1
fi

rm -rf "$TMPDIR"
log "${GREEN}Backup created: $ARCHIVE${NC}"

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

# --- Push to NAS (graceful skip if unreachable) -----------------------------
if [ -n "$NAS_HOST" ] && [ -n "$NAS_EXPORT" ]; then
    if ping -c 1 -W 2 "$NAS_HOST" &>/dev/null; then
        log "${CYAN}NAS reachable. Pushing backup...${NC}"

        mkdir -p "$NAS_MOUNT"
        sudo mount -t nfs "${NAS_HOST}:${NAS_EXPORT}" "$NAS_MOUNT" -o nolock,soft,timeo=10 2>/dev/null

        if [ $? -eq 0 ]; then
            NAS_SAVE_DIR="$NAS_MOUNT/$NAS_SAVE_SUBDIR"
            mkdir -p "$NAS_SAVE_DIR"

            cp "$ARCHIVE" "$NAS_SAVE_DIR/"

            # Prune old NAS backups too
            NAS_COUNT=$(find "$NAS_SAVE_DIR" -maxdepth 1 -name "saves-*.tar.gz" | wc -l)
            if [ "$NAS_COUNT" -gt "$BACKUP_KEEP" ]; then
                NAS_PRUNE=$((NAS_COUNT - BACKUP_KEEP))
                find "$NAS_SAVE_DIR" -maxdepth 1 -name "saves-*.tar.gz" -print0 \
                    | sort -z \
                    | head -z -n "$NAS_PRUNE" \
                    | xargs -0 rm -f
                log "${CYAN}Pruned $NAS_PRUNE old NAS backup(s).${NC}"
            fi

            sudo umount "$NAS_MOUNT" 2>/dev/null
            log "${GREEN}Backup pushed to NAS.${NC}"
        else
            log "${YELLOW}Could not mount NAS. Backup saved locally only.${NC}"
        fi
    else
        log "${YELLOW}NAS unreachable. Backup saved locally only.${NC}"
    fi
else
    log "${YELLOW}NAS not configured. Backup saved locally only.${NC}"
fi

log "${GREEN}Save backup complete.${NC}"
