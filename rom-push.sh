#!/usr/bin/env bash
# ============================================================================
# DeckDock ROM Push — Bulk push ROMs from PC to device
#
# Pushes ROM files from the staging directory (or any system-organized folder)
# directly to ~/Emulation/roms/<system>/ on the device via SCP.
# Files stay on your PC — the staging directory IS your local library.
#
# Usage:
#   ./rom-push.sh                    # Push all systems from STAGING_DIR
#   ./rom-push.sh /path/to/roms      # Push from a specific folder
#   ./rom-push.sh --system psx       # Push only one system
#   ./rom-push.sh --dry-run           # Show what would be pushed
#   ./rom-push.sh --help
# ============================================================================

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="$SCRIPT_DIR/config.env"

# --- Colors ---------------------------------------------------------------
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
BOLD='\033[1m'
DIM='\033[2m'
NC='\033[0m'

ok()   { echo -e "  ${GREEN}[ok]${NC}    $*"; }
warn() { echo -e "  ${YELLOW}[warn]${NC}  $*"; }
fail() { echo -e "  ${RED}[fail]${NC}  $*"; }
info() { echo -e "  ${CYAN}[info]${NC}  $*"; }

# --- Load config ----------------------------------------------------------
if [ ! -f "$CONFIG_FILE" ]; then
    fail "config.env not found at $CONFIG_FILE"
    echo "  Run setup.sh first to create it."
    exit 1
fi
# shellcheck source=/dev/null
source "$CONFIG_FILE"

DEVICE_HOST="${DEVICE_HOST:-}"
STAGING_DIR="${STAGING_DIR:-$HOME/nas-staging}"
SSH_KEY="$HOME/.ssh/id_ed25519"

if [ -z "$DEVICE_HOST" ]; then
    fail "DEVICE_HOST not set in config.env"
    exit 1
fi

SSH_OPTS="-i $SSH_KEY -o ConnectTimeout=5 -o BatchMode=yes"
SCP_CMD="scp $SSH_OPTS"
SSH_CMD="ssh $SSH_OPTS"

REMOTE_ROMS='~/Emulation/roms'

# --- Parse args -----------------------------------------------------------
DRY_RUN=false
SYSTEM_FILTER=""
SOURCE_DIR=""

while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run)  DRY_RUN=true ;;
        --system)   shift; SYSTEM_FILTER="${1:-}"
                    if [ -z "$SYSTEM_FILTER" ]; then
                        fail "--system requires a system slug (e.g. psx, nes, snes)"
                        exit 1
                    fi ;;
        --help|-h)
            echo "Usage: ./rom-push.sh [/path/to/roms] [--system <slug>] [--dry-run]"
            echo ""
            echo "  Pushes ROM files from your staging directory to the device."
            echo "  Files are organized by system (psx/, nes/, etc.) and pushed"
            echo "  to ~/Emulation/roms/<system>/ on the device."
            echo ""
            echo "  /path/to/roms      Source folder (default: STAGING_DIR from config.env)"
            echo "  --system <slug>    Push only one system (e.g. psx, nes, snes)"
            echo "  --dry-run          Show what would be pushed, don't transfer"
            echo "  --help             Show this help"
            echo ""
            echo "  Files that already exist on the device (by name) are skipped."
            echo "  Local files are NOT deleted — your staging dir is your library."
            exit 0
            ;;
        *)  [ -z "$SOURCE_DIR" ] && SOURCE_DIR="$1" ;;
    esac
    shift
done

SOURCE_DIR="${SOURCE_DIR:-$STAGING_DIR}"
SOURCE_DIR="${SOURCE_DIR/#\~/$HOME}"

if [ ! -d "$SOURCE_DIR" ]; then
    fail "Source directory not found: $SOURCE_DIR"
    exit 1
fi

# ============================================================================
# SCAN SOURCE DIRECTORY
# ============================================================================

echo ""
echo -e "${BOLD}DeckDock ROM Push${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
info "Source: $SOURCE_DIR"

# Skip patterns: crawler state, partial downloads, m3u playlists
SKIP_PATTERN='\.crawler-state\.json$|\.part$|\.m3u$'

# Find system subdirectories containing pushable files
declare -a SYSTEMS=()
declare -A SYS_FILES=()     # system -> newline-separated file list
declare -A SYS_COUNT=()     # system -> file count
declare -A SYS_SIZE=()      # system -> total bytes

TOTAL_FILES=0
TOTAL_BYTES=0

for sysdir in "$SOURCE_DIR"/*/; do
    [ ! -d "$sysdir" ] && continue
    system="$(basename "$sysdir")"

    # Apply system filter if specified
    if [ -n "$SYSTEM_FILTER" ] && [ "$system" != "$SYSTEM_FILTER" ]; then
        continue
    fi

    # Collect files (skip artifacts)
    count=0
    bytes=0
    files=""
    while IFS= read -r -d '' file; do
        # Skip files matching skip pattern
        basename "$file" | grep -qE "$SKIP_PATTERN" && continue
        size=$(stat --format='%s' "$file" 2>/dev/null || echo 0)
        bytes=$((bytes + size))
        count=$((count + 1))
        files+="$file"$'\n'
    done < <(find "$sysdir" -type f -print0 2>/dev/null)

    [ $count -eq 0 ] && continue

    SYSTEMS+=("$system")
    SYS_FILES["$system"]="${files%$'\n'}"
    SYS_COUNT["$system"]=$count
    SYS_SIZE["$system"]=$bytes
    TOTAL_FILES=$((TOTAL_FILES + count))
    TOTAL_BYTES=$((TOTAL_BYTES + bytes))
done

if [ -n "$SYSTEM_FILTER" ] && [ ${#SYSTEMS[@]} -eq 0 ]; then
    fail "No files found for system '$SYSTEM_FILTER' in $SOURCE_DIR"
    exit 1
fi

if [ ${#SYSTEMS[@]} -eq 0 ]; then
    warn "No system directories with files found in $SOURCE_DIR"
    echo ""
    echo "  Expected structure: $SOURCE_DIR/<system>/game-files"
    echo "  Example: $SOURCE_DIR/psx/Final Fantasy VII.chd"
    exit 0
fi

# --- Human-readable size ---
human_size() {
    local bytes=$1
    if [ "$bytes" -ge 1073741824 ]; then
        echo "$(echo "scale=1; $bytes / 1073741824" | bc)GB"
    elif [ "$bytes" -ge 1048576 ]; then
        echo "$(echo "scale=1; $bytes / 1048576" | bc)MB"
    elif [ "$bytes" -ge 1024 ]; then
        echo "$(echo "scale=0; $bytes / 1024" | bc)KB"
    else
        echo "${bytes}B"
    fi
}

# ============================================================================
# MANIFEST
# ============================================================================

echo ""
echo -e "${BOLD}Manifest${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# Sort systems alphabetically
IFS=$'\n' SORTED_SYSTEMS=($(printf '%s\n' "${SYSTEMS[@]}" | sort)); unset IFS

for system in "${SORTED_SYSTEMS[@]}"; do
    echo -e "  ${CYAN}${system}${NC}  ${SYS_COUNT[$system]} file(s), $(human_size "${SYS_SIZE[$system]}")"
done

echo ""
echo -e "  ${BOLD}Total:${NC} $TOTAL_FILES file(s), $(human_size $TOTAL_BYTES)"
echo -e "  ${BOLD}Target:${NC} $DEVICE_HOST:$REMOTE_ROMS/<system>/"
echo ""

# --- Dry run exits here ---
if [ "$DRY_RUN" = true ]; then
    info "Dry run — nothing was pushed."
    echo ""
    exit 0
fi

# --- Confirm ---
read -rp "  Push ROMs to device? [Y/n] " confirm
[[ "$confirm" =~ ^[Nn]$ ]] && echo "  Aborted." && exit 0

# ============================================================================
# PUSH TO DEVICE
# ============================================================================

echo ""
echo -e "${BOLD}Pushing ROMs${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# --- SSH connectivity check ---
info "Connecting to $DEVICE_HOST..."
if ! $SSH_CMD "$DEVICE_HOST" "true" 2>/dev/null; then
    fail "Cannot reach $DEVICE_HOST — is the device on and connected to WiFi?"
    exit 1
fi
ok "Device reachable"

# --- Create remote directories (single SSH call) ---
MKDIR_CMD=""
for system in "${SORTED_SYSTEMS[@]}"; do
    MKDIR_CMD+="$REMOTE_ROMS/$system "
done
$SSH_CMD "$DEVICE_HOST" "mkdir -p $MKDIR_CMD" 2>/dev/null
ok "Remote directories ready"

# --- Get existing files on device (single SSH call per system) ---
declare -A REMOTE_EXISTS=()
info "Checking existing files on device..."
for system in "${SORTED_SYSTEMS[@]}"; do
    existing=$($SSH_CMD -n "$DEVICE_HOST" "ls -1 $REMOTE_ROMS/$system/ 2>/dev/null" 2>/dev/null || true)
    if [ -n "$existing" ]; then
        while IFS= read -r name; do
            REMOTE_EXISTS["$system/$name"]=1
        done <<< "$existing"
    fi
done

# --- Push files system-by-system ---
PUSHED=0
SKIPPED=0
ERRORS=0

for system in "${SORTED_SYSTEMS[@]}"; do
    echo ""
    echo -e "  ${BOLD}$system${NC}"

    while IFS= read -r file; do
        [ -z "$file" ] && continue
        filename="$(basename "$file")"

        # Skip if already on device
        if [ -n "${REMOTE_EXISTS["$system/$filename"]+x}" ]; then
            echo -e "    ${DIM}skip  $filename (exists)${NC}"
            SKIPPED=$((SKIPPED + 1))
            continue
        fi

        # Push via SCP
        if $SCP_CMD "$file" "$DEVICE_HOST:$REMOTE_ROMS/$system/" 2>/dev/null; then
            ok "  $filename"
            PUSHED=$((PUSHED + 1))
        else
            fail "  $filename"
            ERRORS=$((ERRORS + 1))
        fi
    done <<< "${SYS_FILES[$system]}"
done

# ============================================================================
# REGENERATE STEAM SHORTCUTS
# ============================================================================

if [ $PUSHED -gt 0 ]; then
    echo ""
    echo -e "${BOLD}Steam Shortcuts${NC}"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    info "Regenerating Steam shortcuts..."
    STEAM_OUT=$($SSH_CMD "$DEVICE_HOST" "python3 ~/Emulation/tools/add-roms-to-steam.py 2>&1" 2>/dev/null)
    TOTAL_LINE=$(echo "$STEAM_OUT" | grep "^Total:")
    if [ -n "$TOTAL_LINE" ]; then
        ok "$TOTAL_LINE"
    else
        warn "Steam shortcut generation returned no summary"
    fi
fi

# ============================================================================
# SUMMARY
# ============================================================================

echo ""
echo -e "${BOLD}ROM Push Complete${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo -e "  ${BOLD}Pushed:${NC}  $PUSHED file(s)"
[ $SKIPPED -gt 0 ] && echo -e "  ${DIM}Skipped: $SKIPPED file(s) (already on device)${NC}"
[ $ERRORS -gt 0 ]  && echo -e "  ${RED}${BOLD}Failed:${NC} $ERRORS file(s)"
echo ""
