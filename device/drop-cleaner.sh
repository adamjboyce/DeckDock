#!/bin/bash
# ============================================================================
# DROP CLEANER — Removes junk files and macOS cruft from the drop directory
#
# Cleans up files that commonly hitchhike inside ROM archives: macOS
# resource forks, desktop metadata, Windows thumbs, NFO spam, and other
# detritus that has no business sitting next to your ROMs.
#
# Configuration search order:
#   1. $DECKDOCK_CONFIG
#   2. ~/DeckDock/config.env
#   3. /etc/deckdock/config.env
#   4. Built-in default (~/Emulation/drop)
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

load_config  # Non-fatal; default below covers the gap

# --- Resolve variables with defaults ---------------------------------------
DROP="${DROP_DIR:-$HOME/Emulation/drop}"

if [ ! -d "$DROP" ]; then
    exit 0
fi

# --- macOS resource fork and metadata cleanup ------------------------------
# Remove __MACOSX directories (created by macOS archive tools)
find "$DROP" -type d -name "__MACOSX" -exec rm -rf {} + 2>/dev/null

# Remove .DS_Store files
find "$DROP" -type f -name ".DS_Store" -delete 2>/dev/null

# Remove ._ resource fork files (macOS dual-fork leftovers)
find "$DROP" -type f -name "._*" -delete 2>/dev/null

# --- Windows junk -----------------------------------------------------------
find "$DROP" -type f -name "Thumbs.db" -delete 2>/dev/null
find "$DROP" -type f -name "desktop.ini" -delete 2>/dev/null

# --- Common archive junk extensions ----------------------------------------
# NFO, TXT (scene release notes), SFV (checksum files), DAT, URL shortcuts
find "$DROP" -type f \( \
    -iname "*.nfo" -o \
    -iname "*.txt" -o \
    -iname "*.sfv" -o \
    -iname "*.dat" -o \
    -iname "*.url" -o \
    -iname "*.htm" -o \
    -iname "*.html" -o \
    -iname "*.nzb" -o \
    -iname "*.srr" -o \
    -iname "*.jpg" -o \
    -iname "*.jpeg" -o \
    -iname "*.png" -o \
    -iname "*.gif" -o \
    -iname "*.bmp" \
\) -delete 2>/dev/null

# --- Prune empty directories -----------------------------------------------
find "$DROP" -mindepth 1 -type d -empty -delete 2>/dev/null
