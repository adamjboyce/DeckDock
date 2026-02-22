#!/usr/bin/env bash
# ============================================================================
# DeckDock Deploy — Push updated scripts to device and verify system health
#
# Run from PC (WSL2) after making changes. Pushes all device scripts,
# service files, and config to the device via SCP, then verifies system
# tools and restarts services.
#
# Usage:
#   ./deploy.sh              # Full deploy + verify
#   ./deploy.sh --scripts    # Scripts only, skip service restart
#   ./deploy.sh --verify     # Verify only, no file push
# ============================================================================

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="$SCRIPT_DIR/config.env"

# --- Colours ---------------------------------------------------------------
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

ok()   { echo -e "  ${GREEN}[ok]${NC}    $*"; }
warn() { echo -e "  ${YELLOW}[warn]${NC}  $*"; }
fail() { echo -e "  ${RED}[fail]${NC}  $*"; }
info() { echo -e "  ${CYAN}[info]${NC}  $*"; }

# --- Load config -----------------------------------------------------------
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

# --- Parse args ------------------------------------------------------------
MODE="full"
case "${1:-}" in
    --scripts) MODE="scripts" ;;
    --verify)  MODE="verify" ;;
    --help|-h)
        echo "Usage: ./deploy.sh [--scripts|--verify]"
        echo "  (no args)   Full deploy: push scripts + services, verify, restart"
        echo "  --scripts   Push scripts only, skip service restart"
        echo "  --verify    Verify system health only, no file push"
        exit 0 ;;
esac

# --- Connectivity check ----------------------------------------------------
echo ""
echo -e "${BOLD}DeckDock Deploy${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

info "Connecting to $DEVICE_HOST..."
if ! $SSH_CMD "$DEVICE_HOST" "true" 2>/dev/null; then
    fail "Cannot reach $DEVICE_HOST — is the device on and connected to WiFi?"
    exit 1
fi
ok "Device reachable"

# --- Remote paths ----------------------------------------------------------
REMOTE_TOOLS='~/Emulation/tools'
REMOTE_LAUNCHERS='~/Emulation/tools/launchers'
REMOTE_DECKDOCK='~/DeckDock/device'
REMOTE_SYSTEMD='~/.config/systemd/user'

# ============================================================================
# PUSH SCRIPTS
# ============================================================================
if [ "$MODE" != "verify" ]; then
    echo ""
    echo -e "${BOLD}Deploying Scripts${NC}"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

    # Ensure remote directories exist
    $SSH_CMD "$DEVICE_HOST" "mkdir -p $REMOTE_TOOLS $REMOTE_LAUNCHERS $REMOTE_DECKDOCK $REMOTE_SYSTEMD" 2>/dev/null

    DEPLOYED=0
    FAILED=0

    # Helper: SCP a file and report
    push() {
        local src="$1" dest="$2" label="${3:-$(basename "$1")}"
        if $SCP_CMD "$src" "$DEVICE_HOST:$dest/" 2>/dev/null; then
            ok "$label -> $dest/"
            DEPLOYED=$((DEPLOYED + 1))
        else
            fail "$label — SCP failed"
            FAILED=$((FAILED + 1))
        fi
    }

    # --- Scripts to ~/Emulation/tools/ ---
    push "$SCRIPT_DIR/device/save-backup.sh"              "$REMOTE_TOOLS"
    push "$SCRIPT_DIR/device/sleep-watcher.sh"             "$REMOTE_TOOLS"
    push "$SCRIPT_DIR/device/nas-mount.sh"                 "$REMOTE_TOOLS"
    push "$SCRIPT_DIR/device/nas-library-sync.sh"          "$REMOTE_TOOLS"
    push "$SCRIPT_DIR/device/deckdock-launcher.sh"         "$REMOTE_TOOLS"
    push "$SCRIPT_DIR/device/deckdock-nas-hook.sh"         "$REMOTE_TOOLS"
    push "$SCRIPT_DIR/device/deckdock-storage-manager.sh"  "$REMOTE_TOOLS"
    push "$SCRIPT_DIR/device/deckdock-preload.sh"          "$REMOTE_TOOLS"
    push "$SCRIPT_DIR/device/launch-appimage.sh"           "$REMOTE_TOOLS"
    push "$SCRIPT_DIR/device/fetch-boxart.py"              "$REMOTE_TOOLS"
    push "$SCRIPT_DIR/device/add-roms-to-steam.py"         "$REMOTE_TOOLS"

    # --- Launcher scripts to ~/Emulation/tools/launchers/ ---
    push "$SCRIPT_DIR/device/deckdock-azahar.sh"           "$REMOTE_LAUNCHERS"

    # --- Scripts referenced by systemd from ~/DeckDock/device/ ---
    push "$SCRIPT_DIR/device/rom-sorter.sh"                "$REMOTE_DECKDOCK"
    push "$SCRIPT_DIR/device/drop-cleaner.sh"              "$REMOTE_DECKDOCK"

    # --- Systemd service/timer files ---
    push "$SCRIPT_DIR/device/rom-sorter.service"           "$REMOTE_SYSTEMD"
    push "$SCRIPT_DIR/device/rom-sorter.timer"             "$REMOTE_SYSTEMD"
    push "$SCRIPT_DIR/device/save-backup-watcher.service"  "$REMOTE_SYSTEMD"
    push "$SCRIPT_DIR/device/nas-mount.service"            "$REMOTE_SYSTEMD"
    push "$SCRIPT_DIR/device/nas-library-sync.service"     "$REMOTE_SYSTEMD"
    push "$SCRIPT_DIR/device/nas-library-sync.timer"       "$REMOTE_SYSTEMD"
    push "$SCRIPT_DIR/device/add-roms-to-steam.service"    "$REMOTE_SYSTEMD"

    echo ""
    info "Deployed $DEPLOYED file(s), $FAILED failed."

    # --- Set permissions ------------------------------------------------------
    info "Setting permissions..."
    $SSH_CMD "$DEVICE_HOST" "chmod +x $REMOTE_TOOLS/*.sh $REMOTE_TOOLS/*.py $REMOTE_LAUNCHERS/*.sh $REMOTE_DECKDOCK/*.sh 2>/dev/null" && \
        ok "Executable permissions set" || warn "Some chmod calls failed"
fi

# ============================================================================
# VERIFY SYSTEM
# ============================================================================
echo ""
echo -e "${BOLD}System Verification${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# --- Check required system tools -------------------------------------------
TOOLS_OK=true
VERIFY_RESULT=$($SSH_CMD "$DEVICE_HOST" '
    missing=""
    for tool in ssh scp sshfs unzip zenity dbus-monitor tar python3; do
        command -v "$tool" &>/dev/null || missing="$missing $tool"
    done
    # xdotool is optional but used by NAS hook
    command -v xdotool &>/dev/null || missing="$missing xdotool(optional)"
    echo "${missing:-none}"
' 2>/dev/null)

if [ "$VERIFY_RESULT" = "none" ]; then
    ok "All system tools present"
else
    for tool in $VERIFY_RESULT; do
        if [[ "$tool" == *"(optional)"* ]]; then
            warn "Missing: ${tool%%(optional)*} (optional — NAS hook zenity raising)"
        else
            fail "Missing: $tool"
            TOOLS_OK=false
        fi
    done
    if [ "$TOOLS_OK" = false ]; then
        warn "Missing tools may need reinstall after SteamOS update."
        warn "Try: sudo steamos-readonly disable && sudo pacman -S <package> && sudo steamos-readonly enable"
    fi
fi

# --- Check SSH key to NAS ---------------------------------------------------
NAS_HOST="${NAS_HOST:-}"
NAS_USER="${NAS_USER:-root}"
if [ -n "$NAS_HOST" ]; then
    NAS_SSH=$($SSH_CMD "$DEVICE_HOST" "ssh -i ~/.ssh/id_ed25519 -o ConnectTimeout=3 -o BatchMode=yes ${NAS_USER}@${NAS_HOST} 'echo ok' 2>/dev/null" 2>/dev/null)
    if [ "$NAS_SSH" = "ok" ]; then
        ok "Device -> NAS SSH working ($NAS_USER@$NAS_HOST)"
    else
        fail "Device -> NAS SSH failed. Save backups and NAS downloads won't work."
        warn "Fix: ssh into device, then run: ssh-copy-id ${NAS_USER}@${NAS_HOST}"
    fi
fi

# --- Check config exists on device ------------------------------------------
CONFIG_OK=$($SSH_CMD "$DEVICE_HOST" '
    if [ -f ~/Emulation/tools/config.env ]; then echo "tools"
    elif [ -f ~/DeckDock/config.env ]; then echo "deckdock"
    else echo "missing"
    fi
' 2>/dev/null)

case "$CONFIG_OK" in
    tools)   ok "Device config found at ~/Emulation/tools/config.env" ;;
    deckdock) ok "Device config found at ~/DeckDock/config.env" ;;
    missing)
        fail "No config.env on device. NAS features won't work."
        warn "Run emu-setup.sh on the device, or SCP config manually."
        ;;
esac

# --- Check key scripts are in place -----------------------------------------
SCRIPTS_CHECK=$($SSH_CMD "$DEVICE_HOST" '
    missing=""
    [ -f ~/Emulation/tools/add-roms-to-steam.py ] || missing="$missing add-roms-to-steam.py"
    [ -f ~/Emulation/tools/save-backup.sh ] || missing="$missing save-backup.sh"
    [ -f ~/Emulation/tools/deckdock-nas-hook.sh ] || missing="$missing deckdock-nas-hook.sh"
    [ -f ~/Emulation/tools/deckdock-storage-manager.sh ] || missing="$missing deckdock-storage-manager.sh"
    [ -f ~/Emulation/tools/launch-appimage.sh ] || missing="$missing launch-appimage.sh"
    [ -f ~/Emulation/tools/launchers/deckdock-azahar.sh ] || missing="$missing deckdock-azahar.sh"
    echo "${missing:-none}"
' 2>/dev/null)

if [ "$SCRIPTS_CHECK" = "none" ]; then
    ok "All DeckDock scripts present on device"
else
    for s in $SCRIPTS_CHECK; do
        fail "Missing on device: $s"
    done
fi

# --- Check systemd services --------------------------------------------------
SERVICES_CHECK=$($SSH_CMD "$DEVICE_HOST" '
    echo "rom-sorter.timer:$(systemctl --user is-active rom-sorter.timer 2>/dev/null)"
    echo "save-backup-watcher:$(systemctl --user is-active save-backup-watcher.service 2>/dev/null)"
    echo "nas-mount:$(systemctl --user is-active nas-mount.service 2>/dev/null)"
    echo "nas-library-sync.timer:$(systemctl --user is-active nas-library-sync.timer 2>/dev/null)"
' 2>/dev/null)

echo ""
info "Systemd services:"
while IFS= read -r line; do
    svc="${line%%:*}"
    status="${line##*:}"
    case "$status" in
        active)   ok "$svc — active" ;;
        inactive) warn "$svc — inactive (not enabled)" ;;
        *)        warn "$svc — $status" ;;
    esac
done <<< "$SERVICES_CHECK"

# ============================================================================
# RESTART SERVICES
# ============================================================================
if [ "$MODE" = "full" ]; then
    echo ""
    echo -e "${BOLD}Restarting Services${NC}"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

    $SSH_CMD "$DEVICE_HOST" '
        systemctl --user daemon-reload
        # Restart active services only — dont enable disabled ones
        systemctl --user is-active --quiet rom-sorter.timer 2>/dev/null && \
            systemctl --user restart rom-sorter.timer
        systemctl --user is-active --quiet save-backup-watcher.service 2>/dev/null && \
            systemctl --user restart save-backup-watcher.service
        systemctl --user is-active --quiet nas-library-sync.timer 2>/dev/null && \
            systemctl --user restart nas-library-sync.timer
    ' 2>/dev/null

    ok "Systemd daemon reloaded, active services restarted"

    # Regenerate Steam shortcuts
    info "Regenerating Steam shortcuts..."
    STEAM_OUT=$($SSH_CMD "$DEVICE_HOST" "python3 ~/Emulation/tools/add-roms-to-steam.py 2>&1" 2>/dev/null)
    TOTAL_LINE=$(echo "$STEAM_OUT" | grep "^Total:")
    if [ -n "$TOTAL_LINE" ]; then
        ok "$TOTAL_LINE"
    else
        warn "Steam shortcut generation returned no summary"
        echo "$STEAM_OUT" | tail -5
    fi
fi

# ============================================================================
# SUMMARY
# ============================================================================
echo ""
echo -e "${BOLD}Deploy Complete${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
