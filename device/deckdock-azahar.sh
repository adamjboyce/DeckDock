#!/bin/bash
# DeckDock Azahar wrapper — NAS hook + zip extraction
[ -f "$HOME/Emulation/tools/deckdock-nas-hook.sh" ] && . "$HOME/Emulation/tools/deckdock-nas-hook.sh"

# Azahar can't open .zip — extract to /tmp cache before launching
rom="$1"
if [[ "${rom,,}" == *.zip ]] && [ -f "$rom" ]; then
    EXTRACT_DIR="/tmp/deckdock-extract"
    rm -rf "$EXTRACT_DIR"
    mkdir -p "$EXTRACT_DIR"
    unzip -o "$rom" -d "$EXTRACT_DIR" 2>/dev/null
    extracted=$(find "$EXTRACT_DIR" -type f \( -iname "*.3ds" -o -iname "*.cci" -o -iname "*.cxi" -o -iname "*.cia" \) | head -1)
    [ -n "$extracted" ] && rom="$extracted"
fi

exe_path=$(find "$HOME/Applications" -iname "azahar*.AppImage" | sort -n | tail -n 1)
[ -z "$exe_path" ] && { echo "Azahar not found"; exit 1; }
chmod +x "$exe_path"
"$exe_path" "$rom"
