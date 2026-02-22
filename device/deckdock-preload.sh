#!/bin/bash
# ============================================================================
# DeckDock — NAS Preload Wrapper
# ============================================================================
# Thin wrapper that downloads NAS-symlinked ROMs before launching the emulator.
# Designed to be prepended to ES-DE launch commands without changing them.
#
# Usage: deckdock-preload.sh <rom-path> -- <emulator-command> [args...]
#
# If the ROM is a NAS symlink, downloads it locally (with zenity progress),
# then runs the emulator command. If the ROM is already local, passes through
# immediately with zero overhead.
#
# Example ES-DE command:
#   /bin/bash ~/Emulation/tools/deckdock-preload.sh %ROM% -- %EMULATOR_AZAHAR% %ROM%
# ============================================================================

set -euo pipefail

ROM_PATH="${1:?Usage: deckdock-preload.sh <rom-path> -- <emulator-command> [args...]}"
shift

# Find the -- separator
while [ $# -gt 0 ] && [ "$1" != "--" ]; do
    shift
done
[ "${1:-}" = "--" ] && shift

# Remaining args are the emulator command
EMULATOR_CMD=("$@")
if [ ${#EMULATOR_CMD[@]} -eq 0 ]; then
    echo "ERROR: No emulator command after --"
    exit 1
fi

# --- Config ---
NAS_MOUNT="/tmp/nas-roms"
LOCKFILE="/tmp/deckdock-download.lock"
TMP_SUFFIX=".deckdock-tmp"
MIN_FREE_MB=2048

# Load config if available
for config in "$HOME/DeckDock/config.env" "$HOME/Emulation/tools/config.env"; do
    if [ -f "$config" ]; then
        eval "$(grep -E '^NAS_MOUNT=' "$config")"
        break
    fi
done

# --- Helpers ---
zenity_error() {
    zenity --error --title="DeckDock" --text="$1" --width=400 2>/dev/null || true
}

is_nas_symlink() {
    [ -L "$1" ] && [[ "$(readlink "$1")" == "$NAS_MOUNT"/* ]]
}

nas_is_mounted() {
    mountpoint -q "$NAS_MOUNT" 2>/dev/null
}

# --- Resolve companion files for multi-file ROMs ---
resolve_companion_files() {
    local rom="$1"
    local nas_target
    nas_target="$(readlink "$rom")"
    local nas_dir
    nas_dir="$(dirname "$nas_target")"
    local ext="${rom##*.}"
    ext="${ext,,}"

    case "$ext" in
        m3u)
            echo "$nas_target"
            while IFS= read -r line; do
                line="$(echo "$line" | sed 's/\r$//')"
                [ -z "$line" ] && continue
                [[ "$line" == \#* ]] && continue
                echo "$nas_dir/$line"
            done < "$nas_target"
            ;;
        cue)
            echo "$nas_target"
            grep -i '^[[:space:]]*FILE' "$nas_target" | \
                sed -E 's/^[[:space:]]*FILE[[:space:]]+"?([^"]+)"?.*/\1/' | \
                while IFS= read -r binfile; do
                    echo "$nas_dir/$binfile"
                done
            ;;
        *)
            echo "$nas_target"
            ;;
    esac
}

# --- Copy with rsync + zenity progress ---
copy_with_progress() {
    local src="$1" dst="$2" label="$3"
    local tmp_dst="${dst}${TMP_SUFFIX}"
    mkdir -p "$(dirname "$dst")"

    rsync --progress --whole-file "$src" "$tmp_dst" 2>/dev/null | \
        awk '/[0-9]+%/ { for(i=1;i<=NF;i++) if($i ~ /%$/) { gsub(/%/,"",$i); print $i; fflush() } }' | \
        zenity --progress \
            --title="DeckDock — Downloading" \
            --text="$label" \
            --percentage=0 \
            --no-cancel \
            --auto-close \
            --width=400 2>/dev/null || true

    if [ -f "$tmp_dst" ]; then
        mv "$tmp_dst" "$dst"
        return 0
    fi
    return 1
}

# --- Download from NAS if needed ---
if is_nas_symlink "$ROM_PATH"; then
    nas_target="$(readlink "$ROM_PATH")"

    if [ ! -f "$nas_target" ]; then
        if nas_is_mounted; then
            zenity_error "This game was removed from the NAS library."
        else
            zenity_error "NAS is not available.\nThis game hasn't been downloaded yet.\nConnect to your home network and try again."
        fi
        exit 1
    fi

    # Acquire lock
    exec 9>"$LOCKFILE"
    if ! flock -n 9; then
        zenity_error "Another download is in progress.\nPlease wait and try again."
        exit 1
    fi

    rom_dir="$(dirname "$ROM_PATH")"
    rom_name="$(basename "$ROM_PATH")"

    # Resolve all files to copy
    files_list="$(resolve_companion_files "$ROM_PATH")"

    # Check disk space
    total_bytes=0
    while IFS= read -r src; do
        [ -f "$src" ] || continue
        sz="$(stat -c%s "$src" 2>/dev/null || echo 0)"
        total_bytes=$((total_bytes + sz))
    done <<< "$files_list"
    total_mb=$((total_bytes / 1024 / 1024))
    free_mb="$(df --output=avail -m "$rom_dir" | tail -1 | tr -d ' ')"

    if [ "$free_mb" -lt "$((total_mb + MIN_FREE_MB))" ]; then
        zenity_error "Not enough disk space.\nNeed ~${total_mb}MB but only ${free_mb}MB free."
        flock -u 9
        exit 1
    fi

    # Clean up partial downloads
    find "$rom_dir" -name "*${TMP_SUFFIX}" -delete 2>/dev/null || true

    # Copy files
    file_count="$(echo "$files_list" | wc -l)"
    current=0
    failed=false

    while IFS= read -r src_path; do
        [ -f "$src_path" ] || continue
        current=$((current + 1))
        filename="$(basename "$src_path")"
        dst_path="$rom_dir/$filename"
        label="Downloading ${filename}"
        [ "$file_count" -gt 1 ] && label="Downloading ${filename} (${current}/${file_count})"

        if ! copy_with_progress "$src_path" "$dst_path" "$label"; then
            failed=true
            break
        fi
    done <<< "$files_list"

    flock -u 9

    if [ "$failed" = true ]; then
        zenity_error "Download failed or was interrupted.\nThe game will re-download next time."
        find "$rom_dir" -name "*${TMP_SUFFIX}" -delete 2>/dev/null || true
        exit 1
    fi

    # Replace symlink with the downloaded file
    # The copy loop may have already replaced the symlink via mv — only rm if still a link
    main_filename="$(basename "$nas_target")"
    [ -L "$ROM_PATH" ] && rm -f "$ROM_PATH"
    if [ "$rom_name" != "$main_filename" ] && [ -f "$rom_dir/$main_filename" ]; then
        mv "$rom_dir/$main_filename" "$ROM_PATH"
    fi
fi

# --- Launch the emulator ---
exec "${EMULATOR_CMD[@]}"
