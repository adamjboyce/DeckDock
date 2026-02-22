#!/bin/bash
# ============================================================================
# DeckDock — NAS Library Sync
# ============================================================================
# Scans NAS ROM directories and creates symlinks in ~/Emulation/roms/<system>/
# for files not already present locally. Also generates ES-DE custom_systems
# XML so the deckdock-launcher wrapper handles all game launches.
#
# Safe behavior:
#   - If NAS is not mounted, exits without touching symlinks
#   - Local real files always win (no symlink created)
#   - Stale symlinks (NAS file deleted) cleaned only when NAS is mounted
#
# Usage: nas-library-sync.sh [--no-xml]
# ============================================================================

set -euo pipefail

# --- Config (defaults, overridden by config.env if present) ---
NAS_MOUNT="/tmp/nas-roms"
NAS_ROM_SUBDIR="roms"
ROMS_DIR="$HOME/Emulation/roms"
LAUNCHER="$HOME/Emulation/tools/deckdock-launcher.sh"
ESDE_CUSTOM_DIR="$HOME/ES-DE/custom_systems"
ESDE_CUSTOM_XML="$ESDE_CUSTOM_DIR/es_systems.xml"
# XML generation disabled — ES-DE uses built-in system definitions + EmuDeck's
# custom entries. NAS games appear via symlinks and load through SSHFS.
# Use --xml flag to force XML generation (adds DeckDock wrapper as launch command).
GENERATE_XML=false

# Load config if available
for config in "$HOME/DeckDock/config.env" "$HOME/Emulation/tools/config.env"; do
    if [ -f "$config" ]; then
        eval "$(grep -E '^(NAS_MOUNT|NAS_ROM_SUBDIR)=' "$config")"
        break
    fi
done

if [ "${1:-}" = "--xml" ]; then
    GENERATE_XML=true
fi

NAS_ROM_DIR="$NAS_MOUNT/$NAS_ROM_SUBDIR"

# --- Patch EmuDeck launchers with NAS download hook ---
# Injects a source line into each launcher so NAS-symlinked games get downloaded
# with a zenity progress bar before the emulator launches. Idempotent — skips
# already-patched launchers. Runs unconditionally (before NAS mount check) so
# launchers stay patched even after EmuDeck updates.
patch_launchers() {
    local hook_line='[ -f "$HOME/Emulation/tools/deckdock-nas-hook.sh" ] && . "$HOME/Emulation/tools/deckdock-nas-hook.sh"'
    local launchers_dir="$HOME/Emulation/tools/launchers"
    local patched=0

    for launcher in "$launchers_dir"/*.sh; do
        [ -f "$launcher" ] || continue
        # Skip if already patched
        grep -qF "deckdock-nas-hook.sh" "$launcher" && continue
        # Inject after shebang line
        sed -i "1a\\$hook_line" "$launcher"
        patched=$((patched + 1))
    done

    if [ "$patched" -gt 0 ]; then
        echo "Patched $patched launcher(s) with NAS hook."
    fi
}

patch_launchers

# --- Extension map per system (both cases for Linux case-sensitivity) ---
declare -A SYSTEM_EXTENSIONS=(
    [psx]=".chd .CHD .cue .CUE .bin .BIN .iso .ISO .pbp .PBP .m3u .M3U"
    [ps2]=".chd .CHD .iso .ISO .bin .BIN .cue .CUE .m3u .M3U"
    [psp]=".iso .ISO .cso .CSO .pbp .PBP"
    [3ds]=".3ds .3DS .cci .CCI .cxi .CXI .cia .CIA .7z .7Z .zip .ZIP"
    [n3ds]=".3ds .3DS .cci .CCI .cxi .CXI .cia .CIA .7z .7Z .zip .ZIP"
    [nds]=".nds .NDS .zip .ZIP .7z .7Z"
    [gamecube]=".iso .ISO .gcz .GCZ .rvz .RVZ .gcm .GCM"
    [gc]=".iso .ISO .gcz .GCZ .rvz .RVZ .gcm .GCM"
    [wii]=".iso .ISO .wbfs .WBFS .rvz .RVZ .gcz .GCZ"
    [xbox]=".iso .ISO"
    [scummvm]=".scummvm .SCUMMVM"
    [nes]=".nes .NES .zip .ZIP .7z .7Z"
    [snes]=".smc .SMC .sfc .SFC .zip .ZIP .7z .7Z"
    [sfc]=".smc .SMC .sfc .SFC .zip .ZIP .7z .7Z"
    [n64]=".z64 .Z64 .n64 .N64 .v64 .V64 .zip .ZIP .7z .7Z"
    [gb]=".gb .GB .zip .ZIP .7z .7Z"
    [gbc]=".gbc .GBC .zip .ZIP .7z .7Z"
    [gba]=".gba .GBA .zip .ZIP .7z .7Z"
    [genesis]=".md .MD .gen .GEN .zip .ZIP .7z .7Z"
    [megadrive]=".md .MD .gen .GEN .zip .ZIP .7z .7Z"
    [saturn]=".chd .CHD .iso .ISO .cue .CUE .bin .BIN"
    [dreamcast]=".chd .CHD .gdi .GDI .cue .CUE .bin .BIN"
    [segacd]=".chd .CHD .iso .ISO .cue .CUE .bin .BIN"
    [sega32x]=".32x .32X .zip .ZIP .7z .7Z"
    [mastersystem]=".sms .SMS .zip .ZIP .7z .7Z"
    [gamegear]=".gg .GG .zip .ZIP .7z .7Z"
    [atari2600]=".a26 .A26 .bin .BIN .zip .ZIP .7z .7Z"
    [atari5200]=".a52 .A52 .bin .BIN .zip .ZIP .7z .7Z"
    [atari7800]=".a78 .A78 .bin .BIN .zip .ZIP .7z .7Z"
    [pcengine]=".pce .PCE .chd .CHD .cue .CUE .zip .ZIP .7z .7Z"
    [mame]=".zip .ZIP .7z .7Z"
)

# --- Fullname / platform / theme for ES-DE XML ---
declare -A SYSTEM_FULLNAME=(
    [psx]="Sony PlayStation" [ps2]="Sony PlayStation 2" [psp]="Sony PSP"
    [3ds]="Nintendo 3DS" [n3ds]="Nintendo 3DS" [nds]="Nintendo DS"
    [gamecube]="Nintendo GameCube" [gc]="Nintendo GameCube"
    [wii]="Nintendo Wii" [xbox]="Microsoft Xbox" [scummvm]="ScummVM"
    [nes]="Nintendo Entertainment System" [snes]="Super Nintendo"
    [sfc]="Super Famicom" [n64]="Nintendo 64"
    [gb]="Game Boy" [gbc]="Game Boy Color" [gba]="Game Boy Advance"
    [genesis]="Sega Genesis" [megadrive]="Sega Mega Drive"
    [saturn]="Sega Saturn" [dreamcast]="Sega Dreamcast"
    [segacd]="Sega CD" [sega32x]="Sega 32X"
    [mastersystem]="Sega Master System" [gamegear]="Sega Game Gear"
    [atari2600]="Atari 2600" [atari5200]="Atari 5200" [atari7800]="Atari 7800"
    [pcengine]="NEC PC Engine" [mame]="Arcade"
)

# Systems that use the wrapper launcher (as opposed to pass-through systems
# like switch, wiiu, ps4 that have their own custom_systems entries)
WRAPPER_SYSTEMS=(
    psx ps2 psp 3ds n3ds nds gamecube gc wii xbox scummvm
    nes snes sfc n64 gb gbc gba genesis megadrive saturn dreamcast
    segacd sega32x mastersystem gamegear atari2600 atari5200 atari7800
    pcengine mame
)

# --- Preflight ---
if ! mountpoint -q "$NAS_MOUNT" 2>/dev/null; then
    echo "NAS not mounted at $NAS_MOUNT — skipping sync."
    exit 0
fi

if [ ! -d "$NAS_ROM_DIR" ]; then
    echo "NAS ROM directory not found: $NAS_ROM_DIR"
    exit 0
fi

# --- Sync: create symlinks for NAS files not already local ---
sync_count=0
clean_count=0

for nas_sys_dir in "$NAS_ROM_DIR"/*/; do
    [ -d "$nas_sys_dir" ] || continue
    system="$(basename "$nas_sys_dir")"
    local_sys_dir="$ROMS_DIR/$system"

    # Create local system dir if needed
    mkdir -p "$local_sys_dir"

    # Create symlinks for NAS files not already present locally
    for nas_file in "$nas_sys_dir"*; do
        [ -f "$nas_file" ] || continue
        filename="$(basename "$nas_file")"
        local_file="$local_sys_dir/$filename"

        # Skip if a real (non-symlink) local file exists
        if [ -f "$local_file" ] && [ ! -L "$local_file" ]; then
            continue
        fi

        # Skip if symlink already points to the right place
        if [ -L "$local_file" ] && [ "$(readlink "$local_file")" = "$nas_file" ]; then
            continue
        fi

        # Create (or update) symlink
        ln -sf "$nas_file" "$local_file"
        sync_count=$((sync_count + 1))
    done

    # Clean stale symlinks (only when NAS is mounted — we know it is here)
    for local_file in "$local_sys_dir"/*; do
        [ -L "$local_file" ] || continue
        target="$(readlink "$local_file")"
        # Only clean symlinks that point into the NAS mount
        [[ "$target" == "$NAS_MOUNT"/* ]] || continue
        if [ ! -f "$target" ]; then
            rm -f "$local_file"
            clean_count=$((clean_count + 1))
        fi
    done
done

# --- System aliases: mirror content to ES-DE expected folder names ---
# ES-DE custom_systems may use different folder names than the NAS.
# e.g., NAS has "3ds" but ES-DE expects "n3ds"
declare -A SYSTEM_ALIASES=(
    [3ds]="n3ds"
)

for src_system in "${!SYSTEM_ALIASES[@]}"; do
    dst_system="${SYSTEM_ALIASES[$src_system]}"
    src_dir="$ROMS_DIR/$src_system"
    dst_dir="$ROMS_DIR/$dst_system"
    [ -d "$src_dir" ] || continue
    mkdir -p "$dst_dir"
    for src_file in "$src_dir"/*; do
        [ -f "$src_file" ] || [ -L "$src_file" ] || continue
        filename="$(basename "$src_file")"
        dst_file="$dst_dir/$filename"
        # Skip if real file exists in destination
        if [ -f "$dst_file" ] && [ ! -L "$dst_file" ]; then
            continue
        fi
        # Create/update symlink to the source (which may itself be a NAS symlink)
        if [ ! -L "$dst_file" ] || [ "$(readlink "$dst_file")" != "$src_file" ]; then
            ln -sf "$src_file" "$dst_file"
            sync_count=$((sync_count + 1))
        fi
    done
done

echo "Sync complete: $sync_count new symlinks, $clean_count stale removed."

# --- Fetch artwork for new games in background ---
if [ "$sync_count" -gt 0 ]; then
    ( python3 "$HOME/Emulation/tools/fetch-boxart.py" >/dev/null 2>&1 ) &
fi

# --- Generate ES-DE custom_systems XML ---
if [ "$GENERATE_XML" = true ]; then
    mkdir -p "$ESDE_CUSTOM_DIR"

    # Preserve existing non-wrapper custom system entries
    # (e.g., switch, wiiu, ps4, atarijaguar from EmuDeck)
    existing_entries=""
    if [ -f "$ESDE_CUSTOM_XML" ]; then
        # Build a lookup string of wrapper system names
        wrapper_lookup="|$(printf '%s|' "${WRAPPER_SYSTEMS[@]}")"

        # Extract <system>...</system> blocks whose <name> is NOT one of ours
        existing_entries="$(awk '
            /<system>/ { block=""; capture=1 }
            capture { block = block $0 "\n" }
            /<\/system>/ {
                capture=0
                # Extract name from block
                n = block
                gsub(/.*<name>/, "", n)
                gsub(/<\/name>.*/, "", n)
                gsub(/\n.*/, "", n)
                print "CHECK:" n "|" block > "/dev/stderr"
                printf "%s", block
            }
        ' "$ESDE_CUSTOM_XML" 2>/dev/null)" || true

        # Simpler approach: use sed to extract blocks, then filter
        existing_entries=""
        # Read file and split into system blocks
        in_block=false
        block=""
        block_name=""
        while IFS= read -r line; do
            if [[ "$line" == *"<system>"* ]]; then
                in_block=true
                block="$line"$'\n'
                block_name=""
                continue
            fi
            if [ "$in_block" = true ]; then
                block+="$line"$'\n'
                # Extract name if this line has it
                if [[ "$line" == *"<name>"* ]]; then
                    block_name="$(echo "$line" | sed 's/.*<name>\(.*\)<\/name>.*/\1/')"
                fi
                if [[ "$line" == *"</system>"* ]]; then
                    in_block=false
                    # Check if this system is NOT one of our wrapper systems
                    is_wrapper=false
                    for ws in "${WRAPPER_SYSTEMS[@]}"; do
                        if [ "$ws" = "$block_name" ]; then
                            is_wrapper=true
                            break
                        fi
                    done
                    if [ "$is_wrapper" = false ] && [ -n "$block_name" ]; then
                        existing_entries+="$block"
                    fi
                fi
            fi
        done < "$ESDE_CUSTOM_XML"
    fi

    # Build the XML
    {
        echo '<?xml version="1.0"?>'
        echo '<!-- DeckDock wrapper entries are auto-generated by nas-library-sync.sh -->'
        echo '<!-- EmuDeck entries (switch, wiiu, etc.) are preserved automatically -->'
        echo '<systemList>'

        # DeckDock wrapper entries — only for systems with local content
        for system in "${WRAPPER_SYSTEMS[@]}"; do
            local_sys_dir="$ROMS_DIR/$system"
            # Only generate entry if the system dir has files
            if [ -d "$local_sys_dir" ] && [ -n "$(ls -A "$local_sys_dir" 2>/dev/null)" ]; then
                extensions="${SYSTEM_EXTENSIONS[$system]:-}"
                [ -z "$extensions" ] && continue
                fullname="${SYSTEM_FULLNAME[$system]:-$system}"

                echo "  <system>"
                echo "    <name>$system</name>"
                echo "    <fullname>$fullname</fullname>"
                echo "    <path>%ROMPATH%/$system</path>"
                echo "    <extension>$extensions</extension>"
                echo "    <command label=\"DeckDock\">/bin/bash $LAUNCHER $system %ROM%</command>"
                echo "    <platform>$system</platform>"
                echo "    <theme>$system</theme>"
                echo "  </system>"
            fi
        done

        # Preserved non-wrapper entries from EmuDeck
        if [ -n "$existing_entries" ]; then
            printf '%s' "$existing_entries"
        fi

        echo '</systemList>'
    } > "$ESDE_CUSTOM_XML"

    echo "ES-DE custom_systems XML updated: $ESDE_CUSTOM_XML"
fi
