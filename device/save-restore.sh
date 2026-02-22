#!/bin/bash
# ============================================================================
# DeckDock - Save Restore
# ============================================================================
# Zenity GUI for restoring emulator save files from NAS backups. Lists
# available save archives on the NAS, lets the user pick one, downloads it,
# and restores saves to the correct emulator paths.
#
# Designed to be launched from Steam Gaming Mode (added as a non-Steam shortcut
# by add-roms-to-steam.py).
#
# Archive format (from save-backup.sh):
#   saves-YYYYMMDD-HHMMSS.tar.gz containing:
#     retroarch/saves/, retroarch/states/
#     azahar/sdmc/, azahar/nand/, azahar/states/
#     duckstation/memcards/, duckstation/savestates/
#     pcsx2/memcards/, pcsx2/sstates/
#     dolphin/GC/, dolphin/Wii/, dolphin/StateSaves/
#     ppsspp/SAVEDATA/, ppsspp/PPSSPP_STATE/
#     melonds/
#     xemu/
#
# Usage: save-restore.sh
# ============================================================================

set -uo pipefail

# --- Config ---
NAS_HOST=""
NAS_USER="root"
NAS_EXPORT=""
NAS_SAVE_SUBDIR="saves"
SSH_KEY="$HOME/.ssh/id_ed25519"

for config in "$HOME/DeckDock/config.env" "$HOME/Emulation/tools/config.env"; do
    if [ -f "$config" ]; then
        eval "$(grep -E '^(NAS_HOST|NAS_USER|NAS_EXPORT|NAS_SAVE_SUBDIR)=' "$config")"
        break
    fi
done

NAS_SAVE_DIR="${NAS_EXPORT}/${NAS_SAVE_SUBDIR}"
_ssh_cmd=(ssh -i "$SSH_KEY" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=5 "${NAS_USER}@${NAS_HOST}")

# --- Emulator restore paths ---
# Maps archive directory names to their device restore paths.
# Must match the collect paths in save-backup.sh.
declare -A RESTORE_PATHS=(
    [retroarch]="$HOME/.var/app/org.libretro.RetroArch/config/retroarch"
    [azahar]="$HOME/.local/share/azahar-emu"
    [duckstation]="$HOME/.local/share/duckstation"
    [pcsx2]="$HOME/.config/PCSX2"
    [dolphin]="$HOME/.var/app/org.DolphinEmu.dolphin-emu/data/dolphin-emu"
    [ppsspp]="$HOME/.var/app/org.ppsspp.PPSSPP/config/ppsspp/PSP"
    [melonds]="$HOME/.var/app/net.kuribo64.melonDS/data/melonDS"
    [xemu]="$HOME/.var/app/app.xemu.xemu/data/xemu/xemu"
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

# --- Format timestamp from filename ---
# saves-YYYYMMDD-HHMMSS.tar.gz -> "YYYY-MM-DD HH:MM:SS"
format_timestamp() {
    local filename="$1"
    local ts
    ts="$(echo "$filename" | sed -n 's/^saves-\([0-9]\{8\}\)-\([0-9]\{6\}\)\.tar\.gz$/\1 \2/p')"
    if [ -z "$ts" ]; then
        echo "$filename"
        return
    fi
    local d="${ts%% *}"
    local t="${ts##* }"
    echo "${d:0:4}-${d:4:2}-${d:6:2} ${t:0:2}:${t:2:2}:${t:4:2}"
}

# --- Preflight: check NAS connectivity ---
if [ -z "$NAS_HOST" ] || [ -z "$NAS_EXPORT" ]; then
    zenity --error \
        --title="DeckDock - Save Restore" \
        --text="NAS not configured.\nRun emu-setup.sh to set up NAS connection." \
        --width=400 2>/dev/null || true
    exit 0
fi

if ! "${_ssh_cmd[@]}" "test -d \"${NAS_SAVE_DIR}\"" 2>/dev/null; then
    zenity --error \
        --title="DeckDock - Save Restore" \
        --text="NAS not available.\nConnect to your home network and try again." \
        --width=400 2>/dev/null || true
    exit 0
fi

# --- List available backups on NAS ---
backup_listing="$("${_ssh_cmd[@]}" "ls -1 \"${NAS_SAVE_DIR}/\" 2>/dev/null | grep '^saves-.*\\.tar\\.gz$' | sort -r" 2>/dev/null)" || backup_listing=""

if [ -z "$backup_listing" ]; then
    zenity --info \
        --title="DeckDock - Save Restore" \
        --text="No save backups found on NAS.\nRun a save backup first (put your device to sleep)." \
        --width=400 2>/dev/null || true
    exit 0
fi

# --- Get file sizes in one SSH call ---
size_data="$("${_ssh_cmd[@]}" "cd \"${NAS_SAVE_DIR}\" && ls -l saves-*.tar.gz 2>/dev/null | awk '{print \$NF, \$5}'" 2>/dev/null)" || size_data=""

declare -A file_sizes
while IFS= read -r line; do
    fname="${line%% *}"
    fsize="${line##* }"
    file_sizes["$fname"]="$fsize"
done <<< "$size_data"

# --- Build zenity list data ---
list_data=()
backup_count=0

while IFS= read -r filename; do
    [ -z "$filename" ] && continue
    human_date="$(format_timestamp "$filename")"
    size_bytes="${file_sizes[$filename]:-0}"
    size_human="$(format_size "$size_bytes")"

    list_data+=("$filename" "$human_date" "$size_human")
    backup_count=$((backup_count + 1))
done <<< "$backup_listing"

if [ "$backup_count" -eq 0 ]; then
    zenity --info \
        --title="DeckDock - Save Restore" \
        --text="No save backups found on NAS." \
        --width=400 2>/dev/null || true
    exit 0
fi

# --- Show selection dialog ---
selected="$(zenity --list \
    --title="DeckDock - Save Restore" \
    --text="Select a save backup to restore.\nThis will overwrite your current emulator saves." \
    --column="Filename" --column="Date" --column="Size" \
    --hide-column=1 \
    --width=500 --height=400 \
    --print-column=1 \
    "${list_data[@]}" 2>/dev/null)" || exit 0

[ -z "$selected" ] && exit 0

human_date="$(format_timestamp "$selected")"

# --- Check for running emulators ---
EMU_PROCESSES="retroarch|azahar|duckstation|PCSX2|dolphin|ppsspp|melonDS|xemu"
running_emus="$(pgrep -ai "$EMU_PROCESSES" 2>/dev/null | head -5)" || running_emus=""

if [ -n "$running_emus" ]; then
    zenity --warning \
        --title="DeckDock - Save Restore" \
        --text="Emulator processes detected:\n\n${running_emus}\n\nClose all emulators before restoring saves." \
        --width=500 2>/dev/null || true
    exit 0
fi

# --- Confirmation dialog ---
zenity --question \
    --title="DeckDock - Save Restore" \
    --text="Restore saves from:\n\n${human_date}\n\nThis will overwrite your current emulator saves.\nAre you sure?" \
    --width=400 \
    --ok-label="Restore" \
    --cancel-label="Cancel" \
    2>/dev/null || exit 0

# --- Download archive from NAS ---
TMP_DIR="/tmp/deckdock-restore"
rm -rf "$TMP_DIR"
mkdir -p "$TMP_DIR"

ARCHIVE_PATH="$TMP_DIR/$selected"

(
    echo "# Downloading backup from NAS..."
    scp -i "$SSH_KEY" "${NAS_USER}@${NAS_HOST}:${NAS_SAVE_DIR}/${selected}" "$ARCHIVE_PATH" 2>/dev/null
    echo "100"
) | zenity --progress \
    --title="DeckDock - Save Restore" \
    --text="Downloading backup from NAS..." \
    --pulsate --auto-close --no-cancel \
    --width=400 2>/dev/null || true

if [ ! -f "$ARCHIVE_PATH" ]; then
    zenity --error \
        --title="DeckDock - Save Restore" \
        --text="Download failed.\nCheck your NAS connection and try again." \
        --width=400 2>/dev/null || true
    rm -rf "$TMP_DIR"
    exit 0
fi

# --- Extract archive ---
EXTRACT_DIR="$TMP_DIR/extracted"
mkdir -p "$EXTRACT_DIR"

if ! tar -xzf "$ARCHIVE_PATH" -C "$EXTRACT_DIR" 2>/dev/null; then
    zenity --error \
        --title="DeckDock - Save Restore" \
        --text="Failed to extract backup archive.\nThe file may be corrupted." \
        --width=400 2>/dev/null || true
    rm -rf "$TMP_DIR"
    exit 0
fi

# --- Restore saves to emulator paths ---
restored=()
errors=()

for emu_dir in "$EXTRACT_DIR"/*/; do
    [ -d "$emu_dir" ] || continue
    emu_name="$(basename "$emu_dir")"
    dest="${RESTORE_PATHS[$emu_name]:-}"

    if [ -z "$dest" ]; then
        errors+=("Unknown emulator directory: $emu_name")
        continue
    fi

    # Create destination if it doesn't exist
    mkdir -p "$dest"

    # Copy with preserved timestamps, overwriting existing files
    if cp -a "$emu_dir"/. "$dest/" 2>/dev/null; then
        # Count restored subdirectories for summary
        sub_count="$(find "$emu_dir" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | wc -l)"
        if [ "$sub_count" -gt 0 ]; then
            subs="$(find "$emu_dir" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' 2>/dev/null | tr '\n' ', ' | sed 's/,$//')"
            restored+=("$emu_name ($subs)")
        else
            restored+=("$emu_name")
        fi
    else
        errors+=("Failed to restore: $emu_name")
    fi
done

# --- Cleanup ---
rm -rf "$TMP_DIR"

# --- Summary dialog ---
if [ "${#restored[@]}" -eq 0 ] && [ "${#errors[@]}" -eq 0 ]; then
    zenity --info \
        --title="DeckDock - Save Restore" \
        --text="Backup was empty - nothing to restore." \
        --width=400 2>/dev/null || true
    exit 0
fi

summary="Restored saves from ${human_date}:\n"
for r in "${restored[@]}"; do
    summary="${summary}\n  - ${r}"
done

if [ "${#errors[@]}" -gt 0 ]; then
    summary="${summary}\n\nWarnings:"
    for e in "${errors[@]}"; do
        summary="${summary}\n  - ${e}"
    done
fi

zenity --info \
    --title="DeckDock - Save Restore" \
    --text="$summary" \
    --width=450 2>/dev/null || true
