#!/bin/bash
# ============================================================================
# DeckDock - Storage Manager
# ============================================================================
# Zenity GUI for managing locally-cached NAS games. Scans for real (non-symlink)
# ROM files that also exist on the NAS, lets the user select which to delete,
# then restores them to NAS symlinks — freeing local storage.
#
# Designed to be launched from Steam Gaming Mode (added as a non-Steam shortcut
# by add-roms-to-steam.py).
#
# Usage: deckdock-storage-manager.sh
# ============================================================================

set -uo pipefail

# --- Config ---
NAS_MOUNT="/tmp/nas-roms"
NAS_ROM_SUBDIR="roms"
NAS_HOST=""
NAS_USER="root"
NAS_EXPORT=""
SSH_KEY="$HOME/.ssh/id_ed25519"
ROMS_DIR="$HOME/Emulation/roms"

for config in "$HOME/DeckDock/config.env" "$HOME/Emulation/tools/config.env"; do
    if [ -f "$config" ]; then
        eval "$(grep -E '^(NAS_MOUNT|NAS_ROM_SUBDIR|NAS_HOST|NAS_USER|NAS_EXPORT)=' "$config")"
        break
    fi
done

NAS_ROM_DIR="$NAS_MOUNT/$NAS_ROM_SUBDIR"
NAS_REMOTE_ROMS="$NAS_EXPORT/$NAS_ROM_SUBDIR"
_ssh_cmd=(ssh -i "$SSH_KEY" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=5 "${NAS_USER}@${NAS_HOST}")

# --- Human-readable system labels ---
declare -A SYSTEM_LABELS=(
    [psx]="PlayStation" [ps2]="PlayStation 2" [psp]="PSP"
    [nes]="NES" [snes]="SNES" [sfc]="SNES" [n64]="Nintendo 64"
    [gb]="Game Boy" [gbc]="Game Boy Color" [gba]="Game Boy Advance"
    [nds]="Nintendo DS" [3ds]="Nintendo 3DS" [n3ds]="Nintendo 3DS"
    [gc]="GameCube" [gamecube]="GameCube" [wii]="Wii"
    [genesis]="Genesis" [megadrive]="Genesis"
    [saturn]="Saturn" [dreamcast]="Dreamcast"
    [atari2600]="Atari 2600" [segacd]="Sega CD" [megacd]="Sega CD"
    [mastersystem]="Master System" [gamegear]="Game Gear"
    [mame]="Arcade" [xbox]="Xbox" [scummvm]="ScummVM"
    [pcengine]="PC Engine" [sega32x]="Sega 32X"
    [atari5200]="Atari 5200" [atari7800]="Atari 7800"
)

# Systems that are aliases (destination → source)
declare -A ALIAS_TARGETS=(
    [n3ds]="3ds"
)

# --- Format size helper ---
format_size() {
    local bytes="$1"
    if [ "$bytes" -ge 1073741824 ]; then
        awk "BEGIN { printf \"%.1f GB\", $bytes / 1073741824 }"
    elif [ "$bytes" -ge 1048576 ]; then
        awk "BEGIN { printf \"%.0f MB\", $bytes / 1048576 }"
    else
        awk "BEGIN { printf \"%.0f KB\", $bytes / 1024 }"
    fi
}

# --- Preflight: check NAS via SSH (SSHFS mountpoint check is unreliable) ---
if ! "${_ssh_cmd[@]}" "test -d \"${NAS_REMOTE_ROMS}\"" 2>/dev/null; then
    zenity --error \
        --title="DeckDock - Storage Manager" \
        --text="NAS not available.\nConnect to your home network and try again." \
        --width=400 2>/dev/null || true
    exit 0
fi

# --- Scan for locally-cached NAS games ---
# A file qualifies if: (1) it's a real file (not a symlink), and (2) the same
# filename exists on the NAS in the matching system directory.

game_list=()    # zenity row data: FALSE|system|filename|size_human|size_bytes|local_path
total_found=0

# Fetch all NAS ROM filenames in one SSH call (system/filename per line)
_nas_all="$("${_ssh_cmd[@]}" "find \"${NAS_REMOTE_ROMS}\" -maxdepth 2 -type f -printf '%P\n'" 2>/dev/null)" || _nas_all=""

for sys_dir in "$ROMS_DIR"/*/; do
    [ -d "$sys_dir" ] || continue
    system="$(basename "$sys_dir")"

    # For alias systems, check the source system's NAS dir
    check_system="$system"
    if [ -n "${ALIAS_TARGETS[$system]:-}" ]; then
        check_system="${ALIAS_TARGETS[$system]}"
    fi

    # Filter NAS file list for this system
    nas_file_list="$(echo "$_nas_all" | sed -n "s|^${check_system}/||p")"
    [ -z "$nas_file_list" ] && continue

    for local_file in "$sys_dir"*; do
        [ -f "$local_file" ] || continue
        # Must be a real file, not a symlink
        [ -L "$local_file" ] && continue

        filename="$(basename "$local_file")"

        # Must have a matching file on NAS
        echo "$nas_file_list" | grep -qFx "$filename" || continue

        size_bytes="$(stat -c%s "$local_file" 2>/dev/null || echo 0)"
        size_human="$(format_size "$size_bytes")"

        label="${SYSTEM_LABELS[$system]:-$system}"
        game_name="${filename%.*}"

        game_list+=("FALSE" "$game_name" "$label" "$size_human" "$size_bytes" "$system" "$filename")
        total_found=$((total_found + 1))
    done
done

if [ "$total_found" -eq 0 ]; then
    zenity --info \
        --title="DeckDock - Storage Manager" \
        --text="No locally-cached NAS games found.\nAll games are already running from NAS symlinks." \
        --width=400 2>/dev/null || true
    exit 0
fi

# --- Show selection dialog ---
selection="$(zenity --list --checklist \
    --title="DeckDock - Storage Manager" \
    --text="Select games to remove from local storage.\nThey'll be restored as NAS symlinks (playable over network)." \
    --column="Select" --column="Game" --column="System" --column="Size" \
    --column="Bytes" --column="SysID" --column="Filename" \
    --hide-column=5,6,7 \
    --width=800 --height=500 \
    --print-column=5,6,7 \
    --separator='|' \
    "${game_list[@]}" 2>/dev/null)" || exit 0

[ -z "$selection" ] && exit 0

# --- Parse selections and delete ---
freed_bytes=0
cleaned=0
errors=()

IFS='|' read -ra items <<< "$selection"
# Each selection is 3 fields: bytes|system|filename
i=0
while [ "$i" -lt "${#items[@]}" ]; do
    size_bytes="${items[$i]}"
    system="${items[$((i+1))]}"
    filename="${items[$((i+2))]}"
    i=$((i + 3))

    local_path="$ROMS_DIR/$system/$filename"
    ext="${filename##*.}"
    ext="${ext,,}"

    # For alias systems, NAS path uses the source system
    nas_system="$system"
    if [ -n "${ALIAS_TARGETS[$system]:-}" ]; then
        nas_system="${ALIAS_TARGETS[$system]}"
    fi
    nas_path="$NAS_ROM_DIR/$nas_system/$filename"

    # Collect files to delete (main file + companions)
    delete_files=("$local_path")
    nas_dir="$(dirname "$nas_path")"
    local_dir="$(dirname "$local_path")"

    case "$ext" in
        m3u)
            # Delete disc files referenced in the .m3u
            if [ -f "$local_path" ]; then
                while IFS= read -r line; do
                    line="$(echo "$line" | sed 's/\r$//')"
                    [ -z "$line" ] && continue
                    [[ "$line" == \#* ]] && continue
                    companion="$local_dir/$line"
                    [ -f "$companion" ] && [ ! -L "$companion" ] && delete_files+=("$companion")
                done < "$local_path"
            fi
            ;;
        cue)
            # Delete .bin files referenced in the .cue
            if [ -f "$local_path" ]; then
                while IFS= read -r binfile; do
                    companion="$local_dir/$binfile"
                    [ -f "$companion" ] && [ ! -L "$companion" ] && delete_files+=("$companion")
                done < <(grep -i '^[[:space:]]*FILE' "$local_path" | sed -E 's/^[[:space:]]*FILE[[:space:]]+"?([^"]+)"?.*/\1/')
            fi
            ;;
    esac

    # Delete files and track freed space
    file_freed=0
    for f in "${delete_files[@]}"; do
        if [ -f "$f" ] && [ ! -L "$f" ]; then
            sz="$(stat -c%s "$f" 2>/dev/null || echo 0)"
            if rm -f "$f"; then
                file_freed=$((file_freed + sz))
            else
                errors+=("Failed to delete: $f")
            fi
        fi
    done

    # Restore NAS symlink for the main file (point to mount path — resolves when NAS is mounted)
    ln -sf "$nas_path" "$local_path"

    # For alias systems, also restore the alias symlink in the alias dir
    if [ -n "${ALIAS_TARGETS[$system]:-}" ]; then
        src_system="${ALIAS_TARGETS[$system]}"
        src_link="$ROMS_DIR/$src_system/$filename"
        # Restore the source system's NAS symlink if needed
        if [ ! -L "$src_link" ]; then
            ln -sf "$NAS_ROM_DIR/$src_system/$filename" "$src_link"
        fi
        # Re-point the alias to the source
        ln -sf "$src_link" "$local_path"
    fi

    # Restore companion symlinks for multi-disc games (read file lists via SSH)
    nas_remote_path="${NAS_REMOTE_ROMS}/${nas_system}/${filename}"
    case "$ext" in
        m3u)
            while IFS= read -r line; do
                line="$(echo "$line" | sed 's/\r$//')"
                [ -z "$line" ] && continue
                [[ "$line" == \#* ]] && continue
                nas_companion="$nas_dir/$line"
                local_companion="$local_dir/$line"
                if [ ! -f "$local_companion" ]; then
                    ln -sf "$nas_companion" "$local_companion"
                fi
            done < <("${_ssh_cmd[@]}" "cat \"${nas_remote_path}\"" 2>/dev/null)
            ;;
        cue)
            while IFS= read -r binfile; do
                nas_companion="$nas_dir/$binfile"
                local_companion="$local_dir/$binfile"
                if [ ! -f "$local_companion" ]; then
                    ln -sf "$nas_companion" "$local_companion"
                fi
            done < <("${_ssh_cmd[@]}" "grep -i '^[[:space:]]*FILE' \"${nas_remote_path}\"" 2>/dev/null | \
                sed -E 's/^[[:space:]]*FILE[[:space:]]+"?([^"]+)"?.*/\1/')
            ;;
    esac

    freed_bytes=$((freed_bytes + file_freed))
    cleaned=$((cleaned + 1))
done

# --- Post-cleanup: update Steam shortcuts and restart Steam to reload ---
# Use setsid to detach from Steam's reaper (kills all children on exit)
setsid bash -c '
    python3 "$HOME/Emulation/tools/add-roms-to-steam.py" >/dev/null 2>&1
    sleep 2
    steam -shutdown >/dev/null 2>&1
' &

# --- Summary ---
freed_human="$(format_size "$freed_bytes")"
summary="Cleaned $cleaned game(s), freed $freed_human."
if [ "${#errors[@]}" -gt 0 ]; then
    summary="$summary\n\nWarnings:\n$(printf '%s\n' "${errors[@]}")"
fi

zenity --info \
    --title="DeckDock - Storage Manager" \
    --text="$summary" \
    --width=400 2>/dev/null || true
