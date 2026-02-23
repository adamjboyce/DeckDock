#!/usr/bin/env bash
# ============================================================================
# DeckDock BIOS Push — Scan local BIOS files and push to device
#
# Scans a folder of BIOS files on your PC, identifies them via MD5 hash and
# filename pattern matching, renames as needed, and SCPs everything to the
# correct location on your device.
#
# Usage:
#   ./bios-push.sh /path/to/bios/folder           # Scan, confirm, push
#   ./bios-push.sh /path/to/bios/folder --dry-run  # Scan only, no push
#   ./bios-push.sh                                 # Prompts for folder
#   ./bios-push.sh --help
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
SSH_KEY="$HOME/.ssh/id_ed25519"

if [ -z "$DEVICE_HOST" ]; then
    fail "DEVICE_HOST not set in config.env"
    exit 1
fi

SSH_OPTS="-i $SSH_KEY -o ConnectTimeout=5 -o BatchMode=yes"
SCP_CMD="scp $SSH_OPTS"
SSH_CMD="ssh $SSH_OPTS"

REMOTE_BIOS='~/Emulation/bios'

# --- Parse args -----------------------------------------------------------
DRY_RUN=false
SOURCE_DIR=""

for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=true ;;
        --help|-h)
            echo "Usage: ./bios-push.sh [/path/to/bios/folder] [--dry-run]"
            echo ""
            echo "  Scans a folder for BIOS files, identifies them by MD5 hash and"
            echo "  filename, and pushes them to the correct location on your device."
            echo ""
            echo "  /path/to/folder   Folder containing your BIOS files"
            echo "  --dry-run         Scan and report only, don't push anything"
            echo "  --help            Show this help"
            exit 0
            ;;
        *)  [ -z "$SOURCE_DIR" ] && SOURCE_DIR="$arg" ;;
    esac
done

if [ -z "$SOURCE_DIR" ]; then
    read -rp "  Path to your BIOS files folder: " SOURCE_DIR
    if [ -z "$SOURCE_DIR" ]; then
        fail "No folder provided."
        exit 1
    fi
fi

SOURCE_DIR="${SOURCE_DIR/#\~/$HOME}"
if [ ! -d "$SOURCE_DIR" ]; then
    fail "Folder not found: $SOURCE_DIR"
    exit 1
fi

# ============================================================================
# BIOS DATABASE
# ============================================================================

# Pass 1: MD5-verified files  (md5 -> "canonical_name|system|subdir")
declare -A MD5_DB=(
    ["924e392ed05558ffdb115408c263dccf"]="SCPH1001.BIN|PS1|"
    ["8dd7d5296a650fac7319bce665a6a53c"]="scph5500.bin|PS1|"
    ["490f666e1afb15b7571ff904e345e522"]="scph5501.bin|PS1|"
    ["32736f17079d0b2b7024407c39bd3050"]="scph5502.bin|PS1|"
    ["e10c53c2f8b90bab96ead2d368858623"]="dc_boot.bin|Dreamcast|dc"
    ["0a93f7940c455905bea6e392dfde92a4"]="dc_flash.bin|Dreamcast|dc"
    ["85ec9ca47d8f6571be4571e668e13ab2"]="sega_101.bin|Saturn|"
    ["3240872c70984b6cbfda1586cab68dbe"]="mpr-17933.bin|Saturn|"
    ["2efd74e3232ff260e371b99f84024f7f"]="bios_CD_U.bin|Sega CD|"
    ["e66fa1dc5820d254611fdcdba0662372"]="bios_CD_E.bin|Sega CD|"
    ["278a9397d192149e84e820ac621a8edd"]="bios_CD_J.bin|Sega CD|"
    ["df692a80a5b1bc90728bc3dfc76cd948"]="bios7.bin|NDS|"
    ["a392174eb3e572fed6447e956bde4b25"]="bios9.bin|NDS|"
    ["a860e8c0b6d573d191e4ec7db1b1e4f6"]="gba_bios.bin|GBA|"
)

# Pass 2: Name-matched files  ("pattern|dest_name|system|subdir|match_type")
# match_type: exact = case-insensitive exact filename, regex = grep -iE
NAME_DB=(
    "SCPH.*70012|SCPH-70012_BIOS_V12_USA_200.BIN|PS2||regex"
    "SCPH.*77001|SCPH-77001_BIOS_V15_USA_230.BIN|PS2||regex"
    "firmware.bin|firmware.bin|NDS||exact"
    "saturn.*bios|saturn_bios.bin|Saturn|kronos|regex"
    "prod.keys|prod.keys|Switch||exact"
    "title.keys|title.keys|Switch||exact"
    "mcpx_1.0.bin|mcpx_1.0.bin|Xbox|xbox|exact"
    "Complex.*4627|Complex_4627v1.03.bin|Xbox|xbox|regex"
)

# ============================================================================
# SCAN SOURCE FOLDER
# ============================================================================

echo ""
echo -e "${BOLD}DeckDock BIOS Push${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
info "Scanning: $SOURCE_DIR"

# Detect 3DS key directory first (so we can exclude from file scan)
AZAHAR_DIR=""
for candidate in "$SOURCE_DIR/azahar/keys" "$SOURCE_DIR/azahar" "$SOURCE_DIR/keys"; do
    if [ -d "$candidate" ] && [ -n "$(find "$candidate" -maxdepth 1 -type f 2>/dev/null | head -1)" ]; then
        AZAHAR_DIR="$candidate"
        break
    fi
done

# Collect all regular files, excluding 3DS dir (handled separately)
declare -a ALL_FILES=()
while IFS= read -r -d '' file; do
    [ -n "$AZAHAR_DIR" ] && [[ "$file" == "$AZAHAR_DIR"/* ]] && continue
    ALL_FILES+=("$file")
done < <(find "$SOURCE_DIR" -type f -print0 2>/dev/null)

if [ ${#ALL_FILES[@]} -eq 0 ] && [ -z "$AZAHAR_DIR" ]; then
    fail "No files found in $SOURCE_DIR"
    exit 1
fi

info "Found ${#ALL_FILES[@]} file(s). Computing MD5 hashes..."

# Hash every file
declare -A FILE_MD5S=()
for file in "${ALL_FILES[@]}"; do
    FILE_MD5S["$file"]=$(md5sum "$file" 2>/dev/null | awk '{print $1}')
done

# ============================================================================
# MATCHING
# ============================================================================

declare -a MATCHED=()        # "local_path|canonical_name|system|subdir"
declare -A MATCHED_SET=()    # Track which local files have been claimed

# --- Pass 1: MD5 ---
echo ""
echo -e "${BOLD}Pass 1: MD5 Matching${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

MD5_HITS=0
for file in "${ALL_FILES[@]}"; do
    md5="${FILE_MD5S[$file]}"
    if [ -n "${MD5_DB[$md5]+x}" ]; then
        IFS='|' read -r canon system subdir <<< "${MD5_DB[$md5]}"
        MATCHED+=("$file|$canon|$system|$subdir")
        MATCHED_SET["$file"]=1
        bn=$(basename "$file")
        if [ "$bn" = "$canon" ]; then
            ok "$bn  ${DIM}($system — MD5 verified)${NC}"
        else
            ok "$bn -> $canon  ${DIM}($system — MD5 verified, renamed)${NC}"
        fi
        MD5_HITS=$((MD5_HITS + 1))
    fi
done
[ $MD5_HITS -eq 0 ] && info "No MD5 matches."

# --- Pass 2: Name ---
echo ""
echo -e "${BOLD}Pass 2: Name Matching${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

NAME_HITS=0
for entry in "${NAME_DB[@]}"; do
    IFS='|' read -r pattern dest system subdir mtype <<< "$entry"
    for file in "${ALL_FILES[@]}"; do
        [ -n "${MATCHED_SET[$file]+x}" ] && continue
        bn=$(basename "$file")
        hit=false
        if [ "$mtype" = "exact" ]; then
            [[ "${bn,,}" = "${pattern,,}" ]] && hit=true
        else
            echo "$bn" | grep -qiE "$pattern" && hit=true
        fi
        if [ "$hit" = true ]; then
            MATCHED+=("$file|$dest|$system|$subdir")
            MATCHED_SET["$file"]=1
            if [ "$bn" = "$dest" ]; then
                ok "$bn  ${DIM}($system — name match)${NC}"
            else
                ok "$bn -> $dest  ${DIM}($system — name match, renamed)${NC}"
            fi
            NAME_HITS=$((NAME_HITS + 1))
            break
        fi
    done
done
[ $NAME_HITS -eq 0 ] && info "No name matches."

# --- Pass 3: 3DS directory ---
echo ""
echo -e "${BOLD}Pass 3: 3DS Key Directory${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

if [ -n "$AZAHAR_DIR" ]; then
    AZAHAR_COUNT=$(find "$AZAHAR_DIR" -type f 2>/dev/null | wc -l)
    ok "Found: $AZAHAR_DIR ($AZAHAR_COUNT file(s))"
else
    info "No 3DS key directory found."
fi

# Collect unrecognized files
declare -a SKIPPED=()
for file in "${ALL_FILES[@]}"; do
    [ -n "${MATCHED_SET[$file]+x}" ] && continue
    SKIPPED+=("$file")
done

# ============================================================================
# MANIFEST
# ============================================================================

echo ""
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

TOTAL=${#MATCHED[@]}
[ -n "$AZAHAR_DIR" ] && TOTAL=$((TOTAL + 1))

if [ $TOTAL -eq 0 ]; then
    fail "No BIOS files identified in $SOURCE_DIR"
    echo ""
    echo "  Make sure your folder contains BIOS files with recognizable"
    echo "  filenames or matching MD5 hashes. Run bios-check.sh on your"
    echo "  device to see what's needed."
    exit 1
fi

echo -e "  ${BOLD}Ready to push:${NC} $TOTAL item(s) to $DEVICE_HOST"
[ ${#SKIPPED[@]} -gt 0 ] && echo -e "  ${DIM}Skipped: ${#SKIPPED[@]} unrecognized file(s)${NC}"
echo ""

# --- Dry run exits here ---
if [ "$DRY_RUN" = true ]; then
    info "Dry run — nothing was pushed."
    if [ ${#SKIPPED[@]} -gt 0 ]; then
        echo ""
        info "Unrecognized files:"
        for file in "${SKIPPED[@]}"; do
            echo -e "    ${DIM}$(basename "$file")${NC}"
        done
    fi
    echo ""
    exit 0
fi

# --- Confirm ---
read -rp "  Push BIOS files to device? [Y/n] " confirm
[[ "$confirm" =~ ^[Nn]$ ]] && echo "  Aborted." && exit 0

# ============================================================================
# PUSH TO DEVICE
# ============================================================================

echo ""
echo -e "${BOLD}Pushing BIOS Files${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# --- SSH connectivity check ---
info "Connecting to $DEVICE_HOST..."
if ! $SSH_CMD "$DEVICE_HOST" "true" 2>/dev/null; then
    fail "Cannot reach $DEVICE_HOST — is the device on and connected to WiFi?"
    exit 1
fi
ok "Device reachable"

# --- Create remote subdirectories (single SSH call) ---
$SSH_CMD "$DEVICE_HOST" "mkdir -p $REMOTE_BIOS $REMOTE_BIOS/dc $REMOTE_BIOS/kronos $REMOTE_BIOS/xbox $REMOTE_BIOS/azahar/keys" 2>/dev/null
ok "Remote directories ready"

# --- Push matched files ---
PUSHED=0
PUSH_FAILED=0

for entry in "${MATCHED[@]}"; do
    IFS='|' read -r local_file canon system subdir <<< "$entry"
    [ -n "$subdir" ] && rdest="$REMOTE_BIOS/$subdir" || rdest="$REMOTE_BIOS"
    bn=$(basename "$local_file")

    if [ "$bn" = "$canon" ]; then
        if $SCP_CMD "$local_file" "$DEVICE_HOST:$rdest/" 2>/dev/null; then
            ok "$canon -> $rdest/"
            PUSHED=$((PUSHED + 1))
        else
            fail "$canon — SCP failed"
            PUSH_FAILED=$((PUSH_FAILED + 1))
        fi
    else
        if $SCP_CMD "$local_file" "$DEVICE_HOST:$rdest/$canon" 2>/dev/null; then
            ok "$bn -> $rdest/$canon"
            PUSHED=$((PUSHED + 1))
        else
            fail "$bn -> $canon — SCP failed"
            PUSH_FAILED=$((PUSH_FAILED + 1))
        fi
    fi
done

# --- Push 3DS key directory ---
if [ -n "$AZAHAR_DIR" ]; then
    info "Pushing 3DS key directory..."
    if $SCP_CMD -r "$AZAHAR_DIR/"* "$DEVICE_HOST:$REMOTE_BIOS/azahar/keys/" 2>/dev/null; then
        ok "3DS keys -> $REMOTE_BIOS/azahar/keys/"
        PUSHED=$((PUSHED + 1))
    else
        fail "3DS key directory — SCP failed"
        PUSH_FAILED=$((PUSH_FAILED + 1))
    fi
fi

# ============================================================================
# VALIDATION
# ============================================================================

echo ""
echo -e "${BOLD}Validation${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

BIOS_CHECK=$($SSH_CMD "$DEVICE_HOST" '
    for p in ~/Emulation/tools/bios-check.sh ~/DeckDock/device/bios-check.sh; do
        [ -f "$p" ] && echo "$p" && exit 0
    done
' 2>/dev/null)

if [ -n "$BIOS_CHECK" ]; then
    info "Running bios-check.sh on device..."
    echo ""
    $SSH_CMD "$DEVICE_HOST" "bash $BIOS_CHECK" 2>/dev/null
else
    warn "bios-check.sh not found on device — run deploy.sh to push it."
    info "Verifying files manually..."
    for entry in "${MATCHED[@]}"; do
        IFS='|' read -r _ canon _ subdir <<< "$entry"
        [ -n "$subdir" ] && rpath="$REMOTE_BIOS/$subdir/$canon" || rpath="$REMOTE_BIOS/$canon"
        if $SSH_CMD -n "$DEVICE_HOST" "test -f $rpath" 2>/dev/null; then
            ok "$canon present"
        else
            fail "$canon not found after push"
        fi
    done
fi

# ============================================================================
# SUMMARY
# ============================================================================

echo ""
echo -e "${BOLD}BIOS Push Complete${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo -e "  ${BOLD}Pushed:${NC}  $PUSHED item(s)"
[ $PUSH_FAILED -gt 0 ] && echo -e "  ${RED}${BOLD}Failed:${NC} $PUSH_FAILED item(s)"
[ ${#SKIPPED[@]} -gt 0 ] && echo -e "  ${DIM}Skipped: ${#SKIPPED[@]} unrecognized file(s)${NC}"
echo ""
