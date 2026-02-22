#!/bin/bash
# ============================================================================
# DeckDock — Universal Launcher Wrapper
# ============================================================================
# Intercepts ES-DE game launches. If the ROM is a NAS symlink, downloads it
# locally first (with progress dialog), then delegates to the real EmuDeck
# launcher. After launch, adds the game to Steam in the background.
#
# Usage: deckdock-launcher.sh <system> <rom-path>
# ============================================================================

set -euo pipefail

SYSTEM="${1:?Usage: deckdock-launcher.sh <system> <rom-path>}"
ROM_PATH="${2:?Usage: deckdock-launcher.sh <system> <rom-path>}"

# --- Config ---
NAS_MOUNT="/tmp/nas-roms"
LOCKFILE="/tmp/deckdock-download.lock"
TMP_SUFFIX=".deckdock-tmp"
LAUNCHERS="$HOME/Emulation/tools/launchers"
ADD_TO_STEAM="$HOME/Emulation/tools/add-roms-to-steam.py"
MIN_FREE_MB=2048  # require 2GB free before downloading

# --- System → EmuDeck Launcher mapping ---
get_launcher() {
    local sys="$1"
    case "$sys" in
        psx)                    echo "duckstation.sh" ;;
        ps2)                    echo "pcsx2-qt.sh" ;;
        psp)                    echo "ppsspp.sh" ;;
        3ds|n3ds)               echo "azahar.sh" ;;
        nds)                    echo "melonds.sh" ;;
        gamecube|gc)            echo "dolphin-emu.sh" ;;
        wii)                    echo "dolphin-emu.sh" ;;
        xbox)                   echo "xemu-emu.sh" ;;
        scummvm)                echo "scummvm.sh" ;;
        *)                      echo "retroarch.sh" ;;
    esac
}

# --- Helpers ---
zenity_error() {
    zenity --error --title="DeckDock" --text="$1" --width=400 2>/dev/null || true
}

zenity_info() {
    zenity --info --title="DeckDock" --text="$1" --width=400 2>/dev/null || true
}

is_nas_symlink() {
    [ -L "$1" ] && [[ "$(readlink "$1")" == "$NAS_MOUNT"/* ]]
}

nas_is_mounted() {
    mountpoint -q "$NAS_MOUNT" 2>/dev/null
}

# --- Resolve companion files for multi-file ROMs ---
# Returns a newline-separated list of NAS source paths to copy
resolve_companion_files() {
    local rom="$1"
    local rom_dir
    rom_dir="$(dirname "$rom")"
    local nas_target
    nas_target="$(readlink "$rom")"
    local nas_dir
    nas_dir="$(dirname "$nas_target")"
    local ext
    ext="${rom##*.}"
    ext="${ext,,}"  # lowercase

    case "$ext" in
        m3u)
            # .m3u lists disc files, one per line
            echo "$nas_target"
            while IFS= read -r line; do
                line="$(echo "$line" | sed 's/\r$//')"
                [ -z "$line" ] && continue
                [[ "$line" == \#* ]] && continue
                echo "$nas_dir/$line"
            done < "$nas_target"
            ;;
        cue)
            # .cue references BIN files via FILE "name" BINARY
            echo "$nas_target"
            grep -i '^[[:space:]]*FILE' "$nas_target" | \
                sed -E 's/^[[:space:]]*FILE[[:space:]]+"?([^"]+)"?.*/\1/' | \
                while IFS= read -r binfile; do
                    echo "$nas_dir/$binfile"
                done
            ;;
        *)
            # Single file
            echo "$nas_target"
            ;;
    esac
}

# --- Calculate total download size (bytes) ---
calc_total_size() {
    local total=0
    while IFS= read -r src; do
        [ -f "$src" ] || continue
        local sz
        sz="$(stat -c%s "$src" 2>/dev/null || echo 0)"
        total=$((total + sz))
    done
    echo "$total"
}

# --- Copy a single file from NAS with rsync progress piped to zenity ---
copy_with_progress() {
    local src="$1"
    local dst="$2"
    local label="$3"
    local tmp_dst="${dst}${TMP_SUFFIX}"

    mkdir -p "$(dirname "$dst")"

    # rsync --progress output: lines like "  1,234,567  50%  12.34MB/s  0:00:05"
    rsync --progress --whole-file "$src" "$tmp_dst" 2>/dev/null | \
        awk '/[0-9]+%/ { for(i=1;i<=NF;i++) if($i ~ /%$/) { gsub(/%/,"",$i); print $i; fflush() } }' | \
        zenity --progress \
            --title="DeckDock — Downloading" \
            --text="$label" \
            --percentage=0 \
            --no-cancel \
            --auto-close \
            --width=400 2>/dev/null || true

    # Verify the copy completed
    if [ -f "$tmp_dst" ]; then
        mv "$tmp_dst" "$dst"
        return 0
    else
        return 1
    fi
}

# --- Main download flow ---
download_from_nas() {
    local rom="$1"
    local rom_dir
    rom_dir="$(dirname "$rom")"
    local rom_name
    rom_name="$(basename "$rom")"

    # Acquire lock (non-blocking)
    exec 9>"$LOCKFILE"
    if ! flock -n 9; then
        zenity_error "Another download is already in progress.\nPlease wait and try again."
        return 1
    fi

    # Check NAS is mounted
    if ! nas_is_mounted; then
        zenity_error "NAS is not available.\nThis game hasn't been downloaded yet.\nConnect to your home network and try again."
        flock -u 9
        return 1
    fi

    # Resolve all files we need to copy
    local files_list
    files_list="$(resolve_companion_files "$rom")"

    # Calculate total size and check disk space
    local total_bytes
    total_bytes="$(echo "$files_list" | calc_total_size)"
    local total_mb=$((total_bytes / 1024 / 1024))

    local free_mb
    free_mb="$(df --output=avail -m "$rom_dir" | tail -1 | tr -d ' ')"

    if [ "$free_mb" -lt "$((total_mb + MIN_FREE_MB))" ]; then
        zenity_error "Not enough disk space.\nNeed ~${total_mb}MB but only ${free_mb}MB free.\nFree up some space and try again."
        flock -u 9
        return 1
    fi

    # Clean up any previous partial downloads in this directory
    find "$rom_dir" -name "*${TMP_SUFFIX}" -delete 2>/dev/null || true

    # Copy each file
    local file_count
    file_count="$(echo "$files_list" | wc -l)"
    local current=0
    local failed=false

    while IFS= read -r src_path; do
        [ -f "$src_path" ] || continue
        current=$((current + 1))

        local filename
        filename="$(basename "$src_path")"
        local dst_path="$rom_dir/$filename"
        local label="Downloading ${filename}"
        [ "$file_count" -gt 1 ] && label="Downloading ${filename} (${current}/${file_count})"

        if ! copy_with_progress "$src_path" "$dst_path" "$label"; then
            failed=true
            break
        fi
    done <<< "$files_list"

    # Release lock
    flock -u 9

    if [ "$failed" = true ]; then
        zenity_error "Download failed or was interrupted.\nThe game will re-download next time you try."
        # Clean up partial files
        find "$rom_dir" -name "*${TMP_SUFFIX}" -delete 2>/dev/null || true
        return 1
    fi

    # Replace symlink with the real main ROM file
    # (companion files are already real files in the same dir)
    local nas_target
    nas_target="$(readlink "$rom")"
    local main_filename
    main_filename="$(basename "$nas_target")"
    rm -f "$rom"
    # The main file was already copied as $rom_dir/$main_filename
    # If the symlink name differs from the NAS filename, rename
    if [ "$rom_name" != "$main_filename" ]; then
        mv "$rom_dir/$main_filename" "$rom"
    fi

    return 0
}

# ============================================================================
# Main
# ============================================================================

# Is this a NAS symlink that needs downloading?
if is_nas_symlink "$ROM_PATH"; then
    # Check if NAS file actually exists (symlink not dangling)
    nas_target="$(readlink "$ROM_PATH")"
    if [ ! -f "$nas_target" ]; then
        if nas_is_mounted; then
            zenity_error "This game was removed from the NAS library.\nIt's no longer available for download."
        else
            zenity_error "NAS is not available.\nThis game hasn't been downloaded yet.\nConnect to your home network and try again."
        fi
        exit 1
    fi

    # Download it
    if ! download_from_nas "$ROM_PATH"; then
        exit 1
    fi
fi

# Resolve the launcher
LAUNCHER_SCRIPT="$(get_launcher "$SYSTEM")"
LAUNCHER_PATH="$LAUNCHERS/$LAUNCHER_SCRIPT"

if [ ! -f "$LAUNCHER_PATH" ]; then
    zenity_error "Emulator launcher not found:\n${LAUNCHER_PATH}\n\nMake sure EmuDeck is installed."
    exit 1
fi

# Add to Steam in the background (only runs on real local files)
if [ -f "$ADD_TO_STEAM" ] && [ ! -L "$ROM_PATH" ]; then
    nohup python3 "$ADD_TO_STEAM" >/dev/null 2>&1 &
fi

# Hand off to the real EmuDeck launcher
exec bash "$LAUNCHER_PATH" "$ROM_PATH"
