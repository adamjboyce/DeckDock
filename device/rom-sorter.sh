#!/bin/bash
# ============================================================================
# ROM AUTO-SORTER — Extracts archives and sorts ROMs into system folders
#
# Watches a drop directory for new files. Archives (zip/7z/rar) are
# extracted in place. Individual ROM files are moved to the appropriate
# system subdirectory under the ROMS root based on file extension and,
# for disc images, filename keyword heuristics.
#
# Configuration search order:
#   1. $DECKDOCK_CONFIG
#   2. ~/DeckDock/config.env
#   3. /etc/deckdock/config.env
#   4. Built-in defaults (~/Emulation/drop, ~/Emulation/roms)
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
DROP="${DROP_DIR:-$HOME/Emulation/drop}"
ROMS="${ROMS_DIR:-$HOME/Emulation/roms}"
LOG="${SORTER_LOG:-$HOME/Emulation/tools/sorter.log}"
EXTRACTING="$DROP/.extracting"

# --- Sorting logic ---------------------------------------------------------
sort_file() {
    local file="$1"
    local name
    name=$(basename "$file")
    local ext="${name##*.}"
    ext=$(echo "$ext" | tr '[:upper:]' '[:lower:]')
    local dest=""

    case "$ext" in
        zip|7z|rar)
            [ -f "$EXTRACTING" ] && return
            touch "$EXTRACTING"
            local tmpdir
            tmpdir=$(mktemp -d "$DROP/.extract_XXXXXX")
            case "$ext" in
                zip) unzip -qo "$file" -d "$tmpdir" 2>/dev/null ;;
                7z)  7z x -y -o"$tmpdir" "$file" >/dev/null 2>&1 ;;
                rar) unrar x -y -o+ "$file" "$tmpdir/" >/dev/null 2>&1 ;;
            esac
            if [ $? -eq 0 ]; then
                find "$tmpdir" -type f | while read -r extracted; do
                    mv "$extracted" "$DROP/"
                done
                rm -rf "$tmpdir"
                rm -f "$file"
            else
                rm -rf "$tmpdir"
            fi
            rm -f "$EXTRACTING"
            return ;;

        # --- Nintendo ---
        nes|unf|unif)           dest="nes" ;;
        sfc|smc)                dest="snes" ;;
        gb)                     dest="gb" ;;
        gbc)                    dest="gbc" ;;
        gba)                    dest="gba" ;;
        nds)                    dest="nds" ;;
        3ds|cia|3dsx|cci)       dest="3ds" ;;
        n64|z64|v64)            dest="n64" ;;
        gcm|gcz|rvz|wbfs|dol)  dest="gc" ;;
        wad)                    dest="wii" ;;

        # --- Sony ---
        pbp|ecm)                dest="psx" ;;
        cso|csz)                dest="psp" ;;

        # --- Sega ---
        sms)                    dest="mastersystem" ;;
        gg)                     dest="gamegear" ;;
        gen|md|sgd)             dest="genesis" ;;
        32x)                    dest="sega32x" ;;
        cdi)                    dest="dreamcast" ;;
        gdi)                    dest="dreamcast" ;;

        # --- Atari ---
        a26)                    dest="atari2600" ;;
        a52)                    dest="atari5200" ;;
        a78)                    dest="atari7800" ;;
        lnx)                    dest="lynx" ;;
        j64)                    dest="atarijaguar" ;;

        # --- Other ---
        pce)                    dest="pcengine" ;;
        ws)                     dest="wonderswan" ;;
        wsc)                    dest="wonderswancolor" ;;
        ngp|ngc)                dest="ngp" ;;
        col)                    dest="colecovision" ;;
        vec)                    dest="vectrex" ;;

        # --- Disc images (keyword detection) ---
        chd|iso|cue|bin)
            local lower
            lower=$(echo "$name" | tr '[:upper:]' '[:lower:]')

            case "$ext" in
                chd|iso)
                    if echo "$lower" | grep -qiE 'ps2|playstation.?2'; then dest="ps2"
                    elif echo "$lower" | grep -qiE 'psx|ps1|playstation'; then dest="psx"
                    elif echo "$lower" | grep -qiE 'dreamcast|dc[^a-z]'; then dest="dreamcast"
                    elif echo "$lower" | grep -qiE 'psp'; then dest="psp"
                    elif echo "$lower" | grep -qiE 'saturn'; then dest="saturn"
                    elif echo "$lower" | grep -qiE 'segacd|sega.?cd|mega.?cd'; then dest="segacd"
                    elif echo "$lower" | grep -qiE 'gamecube|gc[^a-z]'; then dest="gc"
                    elif echo "$lower" | grep -qiE 'wii[^u]'; then dest="wii"
                    fi ;;
                cue)
                    if echo "$lower" | grep -qiE 'ps2|playstation.?2'; then dest="ps2"
                    elif echo "$lower" | grep -qiE 'saturn'; then dest="saturn"
                    elif echo "$lower" | grep -qiE 'segacd|sega.?cd|mega.?cd'; then dest="segacd"
                    elif echo "$lower" | grep -qiE 'dreamcast|dc[^a-z]'; then dest="dreamcast"
                    else dest="psx"; fi ;;
                bin)
                    local base="${name%.*}"
                    if [ -f "$DROP/${base}.cue" ] || [ -f "$DROP/${base}.CUE" ]; then return; fi
                    if echo "$lower" | grep -qiE 'genesis|mega.?drive'; then dest="genesis"; fi ;;
            esac ;;
    esac

    if [ -n "$dest" ] && [ -d "$ROMS/$dest" ]; then
        mv "$file" "$ROMS/$dest/"
        echo "$(date '+%Y-%m-%d %H:%M:%S')  $name -> $dest/" >> "$LOG"

        # Move paired .bin/.img files along with a .cue
        if [ "$ext" = "cue" ]; then
            local base="${name%.*}"
            for binfile in "$DROP/${base}"*.bin "$DROP/${base}"*.BIN "$DROP/${base}"*.img "$DROP/${base}"*.IMG; do
                [ -f "$binfile" ] && mv "$binfile" "$ROMS/$dest/"
            done
        fi
    fi
}

# --- Main loop: process all non-hidden files in the drop directory ----------
for file in "$DROP"/*; do
    [ -f "$file" ] || continue
    [[ "$(basename "$file")" == .* ]] && continue
    sort_file "$file"
done
