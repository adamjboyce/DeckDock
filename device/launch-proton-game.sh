#!/bin/bash
# Wrapper for launching native Windows games (RPG Maker XP/VX/MV/MZ, GameMaker,
# indie EXEs) under Proton from a Steam non-Steam shortcut.
#
# Why this exists: Steam's launch flow for non-Steam game shortcuts with a
# programmatic CompatToolMapping entry stalls at "DownloadingDepots" because
# Steam looks for a depot manifest that does not exist for non-Steam games.
# UI-set compat works (Steam writes accompanying metadata), but programmatic
# mapping does not. This wrapper sidesteps the entire compat-tool path:
# Steam launches it as a plain Linux command, the wrapper sets up the Proton
# runtime environment, and Proton runs the Windows EXE.
#
# Controller config: handled by the DeckDock Decky plugin (decky-plugin/),
# which uses Steam's internal IPC to auto-apply the DeckDock RPG Maker
# template at launch. The plugin is the only working path on SteamOS for
# applying controller configs to non-Steam games without UI interaction.
#
# Usage (from a Steam shortcut):
#   exe          = /home/<user>/Emulation/tools/launch-proton-game.sh
#   launch_opts  = "<path/to/Game.exe>"
#   StartDir     = "<dir containing Game.exe>/"
#
# Optional env vars:
#   PROTON_DIR        Override Proton install dir (default: Proton - Experimental)
#   COMPAT_DATA_BASE  Override compatdata base dir (default: Steam's standard)

set -uo pipefail

GAME_EXE="${1:-}"
if [[ -z "$GAME_EXE" || ! -f "$GAME_EXE" ]]; then
    echo "launch-proton-game.sh: missing or invalid Game.exe path: '$GAME_EXE'" >&2
    exit 1
fi

# Steam tends to inject LD_PRELOAD for the overlay; not all Windows games
# tolerate it propagating to Proton subprocesses.
unset LD_PRELOAD
unset LD_LIBRARY_PATH

STEAM_ROOT="$HOME/.local/share/Steam"
PROTON_DIR="${PROTON_DIR:-$STEAM_ROOT/steamapps/common/Proton - Experimental}"
COMPAT_DATA_BASE="${COMPAT_DATA_BASE:-$STEAM_ROOT/steamapps/compatdata}"

# Per-game compatdata dir, keyed by the game folder name (stable across launches)
GAME_DIR="$(dirname "$GAME_EXE")"
GAME_SLUG="$(basename "$GAME_DIR")"
COMPAT_DATA="$COMPAT_DATA_BASE/$GAME_SLUG"

if [[ ! -x "$PROTON_DIR/proton" ]]; then
    echo "launch-proton-game.sh: Proton not found at '$PROTON_DIR/proton'" >&2
    echo "Install via Steam first: Library → Tools → Proton Experimental → Install" >&2
    exit 2
fi

export STEAM_COMPAT_CLIENT_INSTALL_PATH="$STEAM_ROOT"
export STEAM_COMPAT_DATA_PATH="$COMPAT_DATA"
mkdir -p "$COMPAT_DATA"

# Run from the game's own directory so the EXE finds its assets (RPG Maker
# resolves Audio/, Graphics/, Data/ relative to CWD).
cd "$GAME_DIR" || exit 3

exec "$PROTON_DIR/proton" run "$GAME_EXE"
