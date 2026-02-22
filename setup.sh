#!/usr/bin/env bash
# ============================================================================
# DeckDock — Unified Setup
# ============================================================================
# One script on your PC that sets up everything — config, device folders,
# scripts, emulators, services, NAS — all over SSH. The device just needs
# SSH enabled.
#
# Phases:
#   1.  Configuration wizard (interactive)
#   2.  SSH key setup (semi-automated)
#   3.  Device folder structure (automated)
#   4.  Push scripts & config (automated)
#   5.  EmuDeck (interactive — requires device)
#   6.  Core services (interactive)
#   7.  Xbox Cloud Gaming (interactive, opt-in)
#   8.  Tailscale (interactive, opt-in)
#   9.  NAS game library (interactive, opt-in)
#   10. Steam shortcuts + verification (automated)
#   11. Summary (automated)
#
# Usage:
#   ./setup.sh              Full setup from scratch
#   ./setup.sh --phase N    Start from phase N (1-11)
#   ./setup.sh --skip-config  Skip config wizard, use existing config.env
#   ./setup.sh --verify     Verification only (same as --phase 10)
#   ./setup.sh --help       Show help
# ============================================================================

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="$SCRIPT_DIR/config.env"
EXAMPLE_FILE="$SCRIPT_DIR/config.example.env"

# ── Colours ─────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BOLD='\033[1m'
DIM='\033[2m'
NC='\033[0m'

# ── Logging helpers ─────────────────────────────────────────────────────────
ok()      { echo -e "  ${GREEN}[ok]${NC}    $*"; }
info()    { echo -e "  ${CYAN}[info]${NC}  $*"; }
warn()    { echo -e "  ${YELLOW}[warn]${NC}  $*"; }
fail()    { echo -e "  ${RED}[fail]${NC}  $*"; }

section() {
    echo ""
    echo -e "${BOLD}${CYAN}── $* ──${NC}"
    echo ""
}

phase_header() {
    local num="$1"; shift
    echo ""
    echo -e "${BOLD}Phase ${num}: $*${NC}"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
}

# ── Config helpers ──────────────────────────────────────────────────────────
ask() {
    local prompt="$1" default="$2"
    echo -en "  ${prompt} ${DIM}[${default}]${NC}: "
    read -r REPLY
    REPLY="${REPLY:-$default}"
}

existing_or_default() {
    local key="$1" fallback="$2"
    if [[ -f "$CONFIG_FILE" ]]; then
        local val
        val=$(grep -E "^${key}=" "$CONFIG_FILE" 2>/dev/null | tail -1 | cut -d'=' -f2-)
        if [[ -n "$val" ]]; then
            echo "$val"
            return
        fi
    fi
    echo "$fallback"
}

# ── SSH plumbing ────────────────────────────────────────────────────────────
SSH_KEY="$HOME/.ssh/id_ed25519"
SSH_OPTS="-i $SSH_KEY -o ConnectTimeout=5 -o BatchMode=yes"
SCP_CMD=""   # set after config loaded
SSH_CMD=""   # set after config loaded

init_ssh() {
    SCP_CMD="scp $SSH_OPTS"
    SSH_CMD="ssh $SSH_OPTS"
}

ssh_ok() {
    $SSH_CMD "$CFG_DEVICE_HOST" "true" 2>/dev/null
}

push() {
    local src="$1" dest="$2" label="${3:-$(basename "$1")}"
    if $SCP_CMD "$src" "$CFG_DEVICE_HOST:$dest/" 2>/dev/null; then
        ok "$label -> $dest/"
        PUSH_OK=$((PUSH_OK + 1))
    else
        fail "$label — SCP failed"
        PUSH_FAIL=$((PUSH_FAIL + 1))
    fi
}

# ── Remote paths (device) ──────────────────────────────────────────────────
REMOTE_TOOLS='~/Emulation/tools'
REMOTE_LAUNCHERS='~/Emulation/tools/launchers'
REMOTE_DECKDOCK='~/DeckDock/device'
REMOTE_SYSTEMD='~/.config/systemd/user'

# ── Complete ROM systems list ───────────────────────────────────────────────
ROM_SYSTEMS=(
    nes snes gb gbc gba nds 3ds n64 gc wii switch
    psx ps2 psp dreamcast saturn segacd
    genesis gamegear mastersystem sega32x
    atarijaguar atari2600 atari5200 atari7800 lynx
    colecovision vectrex
    3do cdi pcengine
    ngp wonderswan wonderswancolor
    xbox mame scummvm
)

# ── State ───────────────────────────────────────────────────────────────────
# Config vars (populated by phase 1 or loaded from config.env)
CFG_NAS_HOST="" ; CFG_NAS_USER="" ; CFG_NAS_EXPORT="" ; CFG_NAS_MOUNT=""
CFG_NAS_ROM_SUBDIR="" ; CFG_NAS_SAVE_SUBDIR=""
CFG_DEVICE_HOST="" ; CFG_BACKUP_KEEP="" ; CFG_STAGING_DIR=""
CFG_CRAWLER_PORT="" ; CFG_DEFAULT_DELAY="" ; CFG_DEFAULT_DEPTH=""
CFG_TRICKLE_PUSH="" ; CFG_IGDB_CLIENT_ID="" ; CFG_IGDB_CLIENT_SECRET=""

PUSH_OK=0 ; PUSH_FAIL=0

# Tracking what got set up (for summary)
DID_EMUDECK=false ; DID_ROM_SORTER=false ; DID_SAVE_BACKUP=false
DID_XBOX=false ; DID_TAILSCALE=false ; DID_NAS=false
DID_SHORTCUTS=false

# ── Load existing config ────────────────────────────────────────────────────
load_config() {
    if [[ ! -f "$CONFIG_FILE" ]]; then
        fail "config.env not found. Run phase 1 first (or remove --skip-config)."
        exit 1
    fi
    # shellcheck source=/dev/null
    source "$CONFIG_FILE"
    CFG_NAS_HOST="${NAS_HOST:-}"
    CFG_NAS_USER="${NAS_USER:-root}"
    CFG_NAS_EXPORT="${NAS_EXPORT:-}"
    CFG_NAS_MOUNT="${NAS_MOUNT:-/tmp/nas-roms}"
    CFG_NAS_ROM_SUBDIR="${NAS_ROM_SUBDIR:-roms}"
    CFG_NAS_SAVE_SUBDIR="${NAS_SAVE_SUBDIR:-saves}"
    CFG_DEVICE_HOST="${DEVICE_HOST:-}"
    CFG_BACKUP_KEEP="${BACKUP_KEEP:-10}"
    CFG_STAGING_DIR="${STAGING_DIR:-\$HOME/nas-staging}"
    CFG_CRAWLER_PORT="${CRAWLER_PORT:-7072}"
    CFG_DEFAULT_DELAY="${DEFAULT_DELAY:-5}"
    CFG_DEFAULT_DEPTH="${DEFAULT_DEPTH:-3}"
    CFG_TRICKLE_PUSH="${TRICKLE_PUSH:-true}"
    CFG_IGDB_CLIENT_ID="${IGDB_CLIENT_ID:-}"
    CFG_IGDB_CLIENT_SECRET="${IGDB_CLIENT_SECRET:-}"
    init_ssh
}

# ============================================================================
# PHASE 1 — Configuration Wizard
# ============================================================================
phase_config() {
    phase_header 1 "Configuration Wizard"

    if [[ -f "$CONFIG_FILE" ]]; then
        info "Existing config.env found — current values shown as defaults."
        info "Press Enter to keep a value, or type a new one."
    else
        info "No config.env found — creating one now."
        info "Press Enter to accept defaults shown in brackets."
    fi

    # --- Network ---
    section "Network"

    ask "NAS IP address" "$(existing_or_default NAS_HOST 192.168.1.100)"
    CFG_NAS_HOST="$REPLY"

    ask "NAS SSH user" "$(existing_or_default NAS_USER root)"
    CFG_NAS_USER="$REPLY"

    ask "NAS export path" "$(existing_or_default NAS_EXPORT /volume/shared/roms)"
    CFG_NAS_EXPORT="$REPLY"

    ask "Device SSH connection (user@ip)" "$(existing_or_default DEVICE_HOST deck@192.168.1.150)"
    CFG_DEVICE_HOST="$REPLY"

    # --- Paths ---
    section "Paths"

    ask "Local staging directory" "$(existing_or_default STAGING_DIR '$HOME/nas-staging')"
    CFG_STAGING_DIR="$REPLY"

    ask "NAS mount point on device" "$(existing_or_default NAS_MOUNT /tmp/nas-roms)"
    CFG_NAS_MOUNT="$REPLY"

    ask "NAS subdirectory for ROMs" "$(existing_or_default NAS_ROM_SUBDIR roms)"
    CFG_NAS_ROM_SUBDIR="$REPLY"

    ask "NAS subdirectory for save backups" "$(existing_or_default NAS_SAVE_SUBDIR saves)"
    CFG_NAS_SAVE_SUBDIR="$REPLY"

    # --- Crawler ---
    section "Crawler"

    ask "Crawler web UI port" "$(existing_or_default CRAWLER_PORT 7072)"
    CFG_CRAWLER_PORT="$REPLY"

    ask "Default delay between downloads (seconds)" "$(existing_or_default DEFAULT_DELAY 5)"
    CFG_DEFAULT_DELAY="$REPLY"

    ask "Default crawl depth" "$(existing_or_default DEFAULT_DEPTH 3)"
    CFG_DEFAULT_DEPTH="$REPLY"

    # --- Backup ---
    section "Backup"

    ask "Number of rolling save backups to keep" "$(existing_or_default BACKUP_KEEP 10)"
    CFG_BACKUP_KEEP="$REPLY"

    # --- Trickle Push ---
    section "Trickle Push"

    ask "Auto-push files to NAS after download (true/false)" "$(existing_or_default TRICKLE_PUSH true)"
    CFG_TRICKLE_PUSH="$REPLY"

    # --- IGDB ---
    section "IGDB API (optional — for ROM title classification)"

    ask "IGDB Client ID (blank to skip)" "$(existing_or_default IGDB_CLIENT_ID '')"
    CFG_IGDB_CLIENT_ID="$REPLY"

    ask "IGDB Client Secret" "$(existing_or_default IGDB_CLIENT_SECRET '')"
    CFG_IGDB_CLIENT_SECRET="$REPLY"

    # --- Write config.env ---
    section "Writing Configuration"

    cat > "$CONFIG_FILE" << CONF
# ============================================================================
# DeckDock Configuration
# Generated by setup.sh on $(date '+%Y-%m-%d %H:%M:%S')
# Re-run ./setup.sh at any time to update these values
# ============================================================================

# --- Network ---
NAS_HOST=${CFG_NAS_HOST}
NAS_USER=${CFG_NAS_USER}
NAS_EXPORT=${CFG_NAS_EXPORT}
DEVICE_HOST=${CFG_DEVICE_HOST}

# --- Paths ---
STAGING_DIR=${CFG_STAGING_DIR}
NAS_MOUNT=${CFG_NAS_MOUNT}
NAS_ROM_SUBDIR=${CFG_NAS_ROM_SUBDIR}
NAS_SAVE_SUBDIR=${CFG_NAS_SAVE_SUBDIR}

# --- Crawler ---
CRAWLER_PORT=${CFG_CRAWLER_PORT}
DEFAULT_DELAY=${CFG_DEFAULT_DELAY}
DEFAULT_DEPTH=${CFG_DEFAULT_DEPTH}

# --- Backup ---
BACKUP_KEEP=${CFG_BACKUP_KEEP}

# --- Trickle Push ---
TRICKLE_PUSH=${CFG_TRICKLE_PUSH}

# --- IGDB API ---
IGDB_CLIENT_ID=${CFG_IGDB_CLIENT_ID}
IGDB_CLIENT_SECRET=${CFG_IGDB_CLIENT_SECRET}
CONF

    ok "Config written to $CONFIG_FILE"
    init_ssh

    # --- Validation ---
    section "Validation"

    echo -en "  Test NAS connectivity (${CFG_NAS_HOST})? ${DIM}[Y/n]${NC}: "
    read -r ans
    if [[ ! "${ans:-Y}" =~ ^[Nn] ]]; then
        if ping -c 1 -W 3 "$CFG_NAS_HOST" &>/dev/null; then
            ok "NAS at ${CFG_NAS_HOST} is reachable."
        else
            warn "NAS at ${CFG_NAS_HOST} is not responding."
        fi
    fi

    echo -en "  Test SSH to device (${CFG_DEVICE_HOST})? ${DIM}[Y/n]${NC}: "
    read -r ans
    if [[ ! "${ans:-Y}" =~ ^[Nn] ]]; then
        if ssh -o ConnectTimeout=5 -o BatchMode=yes "$CFG_DEVICE_HOST" "echo ok" &>/dev/null; then
            ok "SSH to ${CFG_DEVICE_HOST} works."
        else
            warn "Could not SSH to ${CFG_DEVICE_HOST}. Phase 2 will set up keys."
        fi
    fi
}

# ============================================================================
# PHASE 2 — SSH Key Setup
# ============================================================================
phase_ssh_keys() {
    phase_header 2 "SSH Key Setup"

    # --- PC SSH key ---
    if [[ ! -f "$SSH_KEY" ]]; then
        info "Generating PC SSH key..."
        mkdir -p "$HOME/.ssh"
        ssh-keygen -t ed25519 -f "$SSH_KEY" -N "" -q
        ok "PC SSH key created at $SSH_KEY"
    else
        ok "PC SSH key exists ($SSH_KEY)"
    fi

    # --- Test BatchMode SSH to device ---
    if ssh -o ConnectTimeout=5 -o BatchMode=yes "$CFG_DEVICE_HOST" "true" 2>/dev/null; then
        ok "Passwordless SSH to device already works."
    else
        info "Copying SSH key to device (you'll be prompted for the device password once)..."
        echo ""
        ssh-copy-id -i "$SSH_KEY.pub" -o ConnectTimeout=5 "$CFG_DEVICE_HOST" || {
            warn "ssh-copy-id failed. Make sure SSH is enabled on the device and try again."
            warn "Manual fix: ssh-copy-id $CFG_DEVICE_HOST"
        }

        # Verify
        if ssh -o ConnectTimeout=5 -o BatchMode=yes "$CFG_DEVICE_HOST" "true" 2>/dev/null; then
            ok "Passwordless SSH to device is now working."
        else
            fail "Still can't SSH without a password. Fix this before continuing."
            echo "  Try: ssh-copy-id $CFG_DEVICE_HOST"
            return 1
        fi
    fi

    # --- Device SSH key (for device→NAS later) ---
    DEVICE_HAS_KEY=$($SSH_CMD "$CFG_DEVICE_HOST" "test -f ~/.ssh/id_ed25519 && echo yes || echo no" 2>/dev/null)
    if [[ "$DEVICE_HAS_KEY" == "yes" ]]; then
        ok "Device SSH key exists."
    else
        info "Generating SSH key on device..."
        $SSH_CMD "$CFG_DEVICE_HOST" "mkdir -p ~/.ssh && ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519 -N '' -q" 2>/dev/null
        ok "Device SSH key created."
    fi
}

# ============================================================================
# PHASE 3 — Device Folder Structure
# ============================================================================
phase_folders() {
    phase_header 3 "Device Folder Structure"

    info "Creating directories on device..."

    # Build the mkdir command for all ROM system dirs
    local rom_dirs=""
    for sys in "${ROM_SYSTEMS[@]}"; do
        rom_dirs="$rom_dirs ~/Emulation/roms/$sys"
    done

    $SSH_CMD "$CFG_DEVICE_HOST" "
        mkdir -p ~/Emulation/drop ~/Emulation/saves ~/Emulation/backups \
                 ~/Emulation/tools ~/Emulation/tools/launchers ~/Emulation/roms \
                 ~/DeckDock/device ~/.config/systemd/user \
                 $rom_dirs
    " 2>/dev/null

    ok "Device folder structure ready (${#ROM_SYSTEMS[@]} ROM system dirs)"
}

# ============================================================================
# PHASE 4 — Push Scripts & Config
# ============================================================================
phase_push() {
    phase_header 4 "Push Scripts & Config"

    PUSH_OK=0
    PUSH_FAIL=0

    info "Deploying scripts to device..."

    # --- Scripts to ~/Emulation/tools/ ---
    push "$SCRIPT_DIR/device/save-backup.sh"              "$REMOTE_TOOLS"
    push "$SCRIPT_DIR/device/sleep-watcher.sh"             "$REMOTE_TOOLS"
    push "$SCRIPT_DIR/device/nas-mount.sh"                 "$REMOTE_TOOLS"
    push "$SCRIPT_DIR/device/nas-library-sync.sh"          "$REMOTE_TOOLS"
    push "$SCRIPT_DIR/device/deckdock-launcher.sh"         "$REMOTE_TOOLS"
    push "$SCRIPT_DIR/device/deckdock-nas-hook.sh"         "$REMOTE_TOOLS"
    push "$SCRIPT_DIR/device/deckdock-storage-manager.sh"  "$REMOTE_TOOLS"
    push "$SCRIPT_DIR/device/save-restore.sh"              "$REMOTE_TOOLS"
    push "$SCRIPT_DIR/device/deckdock-preload.sh"          "$REMOTE_TOOLS"
    push "$SCRIPT_DIR/device/launch-appimage.sh"           "$REMOTE_TOOLS"
    push "$SCRIPT_DIR/device/fetch-boxart.py"              "$REMOTE_TOOLS"
    push "$SCRIPT_DIR/device/add-roms-to-steam.py"         "$REMOTE_TOOLS"
    push "$SCRIPT_DIR/device/bios-check.sh"                "$REMOTE_TOOLS"

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
    info "Deployed $PUSH_OK file(s), $PUSH_FAIL failed."

    # --- Push device config (subset — no PC-specific vars) ---
    info "Pushing device config.env..."
    local tmp_device_config
    tmp_device_config=$(mktemp)
    cat > "$tmp_device_config" << DCONF
# DeckDock Device Config (generated by setup.sh on $(date '+%Y-%m-%d %H:%M:%S'))
NAS_HOST=${CFG_NAS_HOST}
NAS_USER=${CFG_NAS_USER}
NAS_EXPORT=${CFG_NAS_EXPORT}
NAS_MOUNT=${CFG_NAS_MOUNT}
NAS_ROM_SUBDIR=${CFG_NAS_ROM_SUBDIR}
NAS_SAVE_SUBDIR=${CFG_NAS_SAVE_SUBDIR}
BACKUP_KEEP=${CFG_BACKUP_KEEP}
DCONF

    $SCP_CMD "$tmp_device_config" "$CFG_DEVICE_HOST:~/DeckDock/config.env" 2>/dev/null && \
        ok "config.env -> ~/DeckDock/" || fail "config.env -> ~/DeckDock/ failed"

    $SCP_CMD "$tmp_device_config" "$CFG_DEVICE_HOST:$REMOTE_TOOLS/config.env" 2>/dev/null && \
        ok "config.env -> $REMOTE_TOOLS/" || fail "config.env -> $REMOTE_TOOLS/ failed"

    rm -f "$tmp_device_config"

    # --- Set permissions ---
    info "Setting permissions..."
    $SSH_CMD "$CFG_DEVICE_HOST" "
        chmod +x $REMOTE_TOOLS/*.sh $REMOTE_TOOLS/*.py \
                 $REMOTE_LAUNCHERS/*.sh $REMOTE_DECKDOCK/*.sh 2>/dev/null
    " 2>/dev/null
    ok "Executable permissions set"

    # --- Reload systemd ---
    $SSH_CMD "$CFG_DEVICE_HOST" "systemctl --user daemon-reload" 2>/dev/null
    ok "Systemd daemon reloaded"
}

# ============================================================================
# PHASE 5 — EmuDeck
# ============================================================================
phase_emudeck() {
    phase_header 5 "EmuDeck"

    # Check if already installed
    local emudeck_installed
    emudeck_installed=$($SSH_CMD "$CFG_DEVICE_HOST" \
        "test -d ~/.config/EmuDeck && test -f ~/.config/EmuDeck/settings.sh && echo yes || echo no" 2>/dev/null)

    if [[ "$emudeck_installed" == "yes" ]]; then
        ok "EmuDeck is already installed on device."
        echo -en "  Update/reinstall? ${DIM}[y/N]${NC}: "
        read -r reinstall
        if [[ ! "$reinstall" =~ ^[Yy] ]]; then
            info "Keeping current EmuDeck installation."
            return 0
        fi
    else
        info "EmuDeck not found on device."
        echo -en "  Install EmuDeck? ${DIM}[Y/n]${NC}: "
        read -r install_it
        if [[ "$install_it" =~ ^[Nn] ]]; then
            warn "Skipping EmuDeck. Install later from https://www.emudeck.com"
            return 0
        fi
    fi

    # --- System Picker ---
    echo ""
    echo -e "${BOLD}Which gaming systems do you want to play?${NC}"
    echo "Press Enter to accept the default shown in brackets."
    echo ""

    local EMU_RETROARCH=true
    local EMU_DOLPHIN=true EMU_MELONDS=true EMU_AZAHAR=true EMU_MGBA=false
    local EMU_DUCKSTATION=true EMU_PCSX2=true EMU_PPSSPP=true
    local EMU_RPCS3=false EMU_FLYCAST=true EMU_XEMU=false
    local EMU_RYUJINX=false EMU_CEMU=false EMU_MAME=false
    local EMU_SCUMMVM=false EMU_VITA3K=false

    echo -en "  Nintendo consoles (NES, SNES, N64, GameCube, Wii)? ${DIM}[Y/n]${NC}: "
    read -r ans; [[ "$ans" =~ ^[Nn] ]] && EMU_DOLPHIN=false

    echo -en "  Nintendo handhelds (Game Boy, GBA, DS, 3DS)? ${DIM}[Y/n]${NC}: "
    read -r ans; [[ "$ans" =~ ^[Nn] ]] && { EMU_MELONDS=false; EMU_AZAHAR=false; }

    echo -en "  PlayStation (PS1, PS2, PSP)? ${DIM}[Y/n]${NC}: "
    read -r ans; [[ "$ans" =~ ^[Nn] ]] && { EMU_DUCKSTATION=false; EMU_PCSX2=false; EMU_PPSSPP=false; }

    echo -en "  PlayStation 3? (needs powerful device) ${DIM}[y/N]${NC}: "
    read -r ans; [[ "$ans" =~ ^[Yy] ]] && EMU_RPCS3=true

    echo -en "  Sega (Genesis, Saturn, Dreamcast, Game Gear)? ${DIM}[Y/n]${NC}: "
    read -r ans; [[ "$ans" =~ ^[Nn] ]] && EMU_FLYCAST=false

    echo -en "  Original Xbox? ${DIM}[y/N]${NC}: "
    read -r ans; [[ "$ans" =~ ^[Yy] ]] && EMU_XEMU=true

    echo -en "  Nintendo Switch? (needs game files + firmware) ${DIM}[y/N]${NC}: "
    read -r ans; [[ "$ans" =~ ^[Yy] ]] && EMU_RYUJINX=true

    echo -en "  Wii U? ${DIM}[y/N]${NC}: "
    read -r ans; [[ "$ans" =~ ^[Yy] ]] && EMU_CEMU=true

    echo -en "  Arcade games (MAME)? ${DIM}[y/N]${NC}: "
    read -r ans; [[ "$ans" =~ ^[Yy] ]] && EMU_MAME=true

    echo -en "  Classic PC adventure games (ScummVM)? ${DIM}[y/N]${NC}: "
    read -r ans; [[ "$ans" =~ ^[Yy] ]] && EMU_SCUMMVM=true

    echo -en "  PS Vita? ${DIM}[y/N]${NC}: "
    read -r ans; [[ "$ans" =~ ^[Yy] ]] && EMU_VITA3K=true

    # --- Write EmuDeck settings to device ---
    info "Pushing emulator preferences to device..."
    local tmp_prefs
    tmp_prefs=$(mktemp)
    cat > "$tmp_prefs" << EMUEOF
# DeckDock system choices (pre-configured by setup.sh)
RetroArch=${EMU_RETROARCH}
dolphin=${EMU_DOLPHIN}
pcsx2Qt=${EMU_PCSX2}
rpcs3=${EMU_RPCS3}
azahar=${EMU_AZAHAR}
duckstation=${EMU_DUCKSTATION}
cemu=${EMU_CEMU}
ryujinx=${EMU_RYUJINX}
ppsspp=${EMU_PPSSPP}
xemu=${EMU_XEMU}
melonds=${EMU_MELONDS}
mame=${EMU_MAME}
flycast=${EMU_FLYCAST}
mgba=${EMU_MGBA}
scummvm=${EMU_SCUMMVM}
vita3k=${EMU_VITA3K}
EMUEOF

    # Ensure EmuDeck config dir exists and push prefs
    $SSH_CMD "$CFG_DEVICE_HOST" "mkdir -p ~/.config/EmuDeck" 2>/dev/null

    local settings_exists
    settings_exists=$($SSH_CMD "$CFG_DEVICE_HOST" "test -f ~/.config/EmuDeck/settings.sh && echo yes || echo no" 2>/dev/null)

    if [[ "$settings_exists" == "yes" ]]; then
        # Merge preferences into existing settings
        $SCP_CMD "$tmp_prefs" "$CFG_DEVICE_HOST:/tmp/deckdock-emu-prefs.sh" 2>/dev/null
        $SSH_CMD "$CFG_DEVICE_HOST" '
            while IFS="=" read -r key val; do
                case "$key" in \#*|"") continue ;; esac
                if grep -q "^${key}=" ~/.config/EmuDeck/settings.sh 2>/dev/null; then
                    sed -i "s|^${key}=.*|${key}=${val}|" ~/.config/EmuDeck/settings.sh
                else
                    echo "${key}=${val}" >> ~/.config/EmuDeck/settings.sh
                fi
            done < /tmp/deckdock-emu-prefs.sh
            rm -f /tmp/deckdock-emu-prefs.sh
        ' 2>/dev/null
        ok "Merged emulator choices into existing EmuDeck settings."
    else
        $SCP_CMD "$tmp_prefs" "$CFG_DEVICE_HOST:~/.config/EmuDeck/settings.sh" 2>/dev/null
        ok "Created EmuDeck settings with your choices."
    fi
    rm -f "$tmp_prefs"

    # --- Stage installer on device ---
    info "Staging EmuDeck installer on device..."
    $SSH_CMD "$CFG_DEVICE_HOST" \
        'curl -sL "https://www.emudeck.com/EmuDeck.desktop" -o /tmp/EmuDeck.desktop && chmod +x /tmp/EmuDeck.desktop' \
        2>/dev/null

    local installer_ok
    installer_ok=$($SSH_CMD "$CFG_DEVICE_HOST" "test -f /tmp/EmuDeck.desktop && echo yes || echo no" 2>/dev/null)

    if [[ "$installer_ok" == "yes" ]]; then
        ok "EmuDeck installer staged at /tmp/EmuDeck.desktop"
    else
        warn "Couldn't download EmuDeck installer. Device may not have internet."
        warn "Download manually on device: https://www.emudeck.com"
    fi

    echo ""
    echo -e "  ${BOLD}Action required on your device:${NC}"
    echo "  ──────────────────────────────────────────────────"
    echo "  1. On your device, open a terminal (Konsole)"
    echo "  2. Run:  bash /tmp/EmuDeck.desktop"
    echo "     (or double-click EmuDeck.desktop in the file manager)"
    echo "  3. Follow the EmuDeck installer prompts"
    echo "  4. When finished, come back here and press Enter"
    echo "  ──────────────────────────────────────────────────"
    echo ""
    echo -en "  ${BOLD}Press Enter when EmuDeck setup is finished on the device...${NC}"
    read -r

    # Verify
    emudeck_installed=$($SSH_CMD "$CFG_DEVICE_HOST" \
        "test -d ~/.config/EmuDeck && test -f ~/.config/EmuDeck/settings.sh && echo yes || echo no" 2>/dev/null)
    if [[ "$emudeck_installed" == "yes" ]]; then
        ok "EmuDeck installation verified."
        DID_EMUDECK=true
    else
        warn "EmuDeck settings not detected. It may not have completed."
        warn "You can run EmuDeck on the device later."
    fi
}

# ============================================================================
# PHASE 6 — Core Services
# ============================================================================
phase_services() {
    phase_header 6 "Core Services"

    # --- ROM Sorter ---
    echo ""
    info "ROM Sorter watches the drop folder and auto-sorts files by type."
    echo -en "  Enable automatic ROM sorting? ${DIM}[Y/n]${NC}: "
    read -r ans
    if [[ ! "${ans:-Y}" =~ ^[Nn] ]]; then
        $SSH_CMD "$CFG_DEVICE_HOST" "systemctl --user enable --now rom-sorter.timer" 2>/dev/null
        ok "ROM Sorter enabled (rom-sorter.timer)"
        DID_ROM_SORTER=true
    else
        warn "ROM Sorter not enabled. Enable later: systemctl --user enable --now rom-sorter.timer"
    fi

    # --- Save Backup ---
    echo ""
    info "Save Backup automatically backs up game saves when the device sleeps."
    echo -en "  Enable automatic save backups? ${DIM}[Y/n]${NC}: "
    read -r ans
    if [[ ! "${ans:-Y}" =~ ^[Nn] ]]; then
        $SSH_CMD "$CFG_DEVICE_HOST" "systemctl --user enable --now save-backup-watcher.service" 2>/dev/null
        ok "Save Backup enabled (save-backup-watcher.service)"
        DID_SAVE_BACKUP=true
    else
        warn "Save Backup not enabled. Enable later: systemctl --user enable --now save-backup-watcher.service"
    fi
}

# ============================================================================
# PHASE 7 — Xbox Cloud Gaming
# ============================================================================
phase_xbox() {
    phase_header 7 "Xbox Cloud Gaming (Optional)"

    echo "Stream Xbox games via the cloud. Needs Xbox Game Pass Ultimate."
    echo ""
    echo -en "  Set up Xbox Cloud Gaming? ${DIM}[y/N]${NC}: "
    read -r ans
    if [[ ! "$ans" =~ ^[Yy] ]]; then
        info "Skipping Xbox Cloud Gaming."
        return 0
    fi

    # Detect or install a Chromium browser
    local browser=""
    local browser_name=""
    browser=$($SSH_CMD "$CFG_DEVICE_HOST" '
        for id in com.microsoft.Edge com.google.Chrome org.chromium.Chromium; do
            flatpak info "$id" &>/dev/null && echo "$id" && exit 0
        done
        echo ""
    ' 2>/dev/null)

    if [[ -n "$browser" ]]; then
        ok "Found browser: $browser"
    else
        info "No Chromium browser found. Installing Google Chrome..."
        $SSH_CMD "$CFG_DEVICE_HOST" "flatpak install -y --user flathub com.google.Chrome" 2>/dev/null
        browser=$($SSH_CMD "$CFG_DEVICE_HOST" "flatpak info com.google.Chrome &>/dev/null && echo com.google.Chrome" 2>/dev/null)
        if [[ -n "$browser" ]]; then
            ok "Google Chrome installed."
        else
            fail "Couldn't install Chrome. Add Flathub first:"
            echo "  flatpak remote-add --if-not-exists flathub https://flathub.org/repo/flathub.flatpakrepo"
            return 0
        fi
    fi

    # Push .desktop file
    info "Creating Xbox Cloud Gaming shortcut on device..."
    $SSH_CMD "$CFG_DEVICE_HOST" "
        mkdir -p ~/.local/share/applications
        cat > ~/.local/share/applications/xbox-cloud-gaming.desktop << 'XEOF'
[Desktop Entry]
Name=Xbox Cloud Gaming
Comment=Stream Xbox games from the cloud
Exec=flatpak run $browser --kiosk --start-fullscreen --app=https://www.xbox.com/play
Type=Application
Categories=Game;
Icon=applications-games
Terminal=false
XEOF
    " 2>/dev/null

    ok "Xbox Cloud Gaming shortcut created."
    info "It will appear as a Steam shortcut after Phase 10."
    DID_XBOX=true
}

# ============================================================================
# PHASE 8 — Tailscale
# ============================================================================
phase_tailscale() {
    phase_header 8 "Tailscale (Optional)"

    echo "Access your device from anywhere — SSH, remote play, file push."
    echo "Free for personal use."
    echo ""
    echo -en "  Set up Tailscale? ${DIM}[y/N]${NC}: "
    read -r ans
    if [[ ! "$ans" =~ ^[Yy] ]]; then
        info "Skipping Tailscale."
        return 0
    fi

    local ts_installed
    ts_installed=$($SSH_CMD "$CFG_DEVICE_HOST" "command -v tailscale &>/dev/null && echo yes || echo no" 2>/dev/null)

    if [[ "$ts_installed" == "yes" ]]; then
        ok "Tailscale is already installed on device."
        local ts_status
        ts_status=$($SSH_CMD "$CFG_DEVICE_HOST" "tailscale status &>/dev/null && tailscale ip -4 2>/dev/null || echo 'not-connected'" 2>/dev/null)
        if [[ "$ts_status" != "not-connected" && -n "$ts_status" ]]; then
            ok "Tailscale connected. Device IP: $ts_status"
            DID_TAILSCALE=true
        else
            warn "Tailscale installed but not connected."
            info "Run 'tailscale up' on the device to authenticate (needs a browser)."
        fi
    else
        info "Installing Tailscale on device..."
        $SSH_CMD "$CFG_DEVICE_HOST" 'curl -fsSL https://tailscale.com/install.sh | sh' 2>/dev/null
        ts_installed=$($SSH_CMD "$CFG_DEVICE_HOST" "command -v tailscale &>/dev/null && echo yes || echo no" 2>/dev/null)
        if [[ "$ts_installed" == "yes" ]]; then
            ok "Tailscale installed."
            DID_TAILSCALE=true
        else
            fail "Tailscale installation failed."
        fi
    fi

    if [[ "$DID_TAILSCALE" == true ]] || [[ "$ts_installed" == "yes" ]]; then
        echo ""
        info "Post-setup action: Run 'tailscale up' on the device to authenticate."
        info "(Requires a browser on the device for first-time login.)"
    fi
}

# ============================================================================
# PHASE 9 — NAS Game Library
# ============================================================================
phase_nas() {
    phase_header 9 "NAS Game Library (Optional)"

    if [[ -z "$CFG_NAS_HOST" ]]; then
        info "No NAS configured (NAS_HOST is empty). Skipping."
        return 0
    fi

    echo "Browse your NAS game library from ES-DE. Games download on-demand."
    echo ""
    echo -en "  Set up NAS game library? ${DIM}[Y/n]${NC}: "
    read -r ans
    if [[ "$ans" =~ ^[Nn] ]]; then
        info "Skipping NAS game library."
        return 0
    fi

    # --- Check SSHFS on device ---
    local sshfs_ok
    sshfs_ok=$($SSH_CMD "$CFG_DEVICE_HOST" "command -v sshfs &>/dev/null && echo yes || echo no" 2>/dev/null)
    if [[ "$sshfs_ok" == "yes" ]]; then
        ok "SSHFS available on device."
    else
        warn "SSHFS not found on device. NAS library needs SSHFS."
        info "On device: sudo pacman -S sshfs"
        return 0
    fi

    # --- Device → NAS SSH keys ---
    local nas_ssh_ok
    nas_ssh_ok=$($SSH_CMD "$CFG_DEVICE_HOST" \
        "ssh -i ~/.ssh/id_ed25519 -o ConnectTimeout=3 -o BatchMode=yes ${CFG_NAS_USER}@${CFG_NAS_HOST} 'echo ok' 2>/dev/null" 2>/dev/null)

    if [[ "$nas_ssh_ok" == "ok" ]]; then
        ok "Device -> NAS SSH already works."
    else
        info "Setting up device -> NAS SSH keys..."
        echo "  You may be prompted for the NAS password."
        echo ""
        # Use -t for pseudo-tty to allow password input through SSH chain
        ssh -o ConnectTimeout=5 -t "$CFG_DEVICE_HOST" \
            "ssh-copy-id -o StrictHostKeyChecking=accept-new ${CFG_NAS_USER}@${CFG_NAS_HOST}" 2>/dev/null || {
            warn "ssh-copy-id failed. Run on device: ssh-copy-id ${CFG_NAS_USER}@${CFG_NAS_HOST}"
        }

        # Verify
        nas_ssh_ok=$($SSH_CMD "$CFG_DEVICE_HOST" \
            "ssh -i ~/.ssh/id_ed25519 -o ConnectTimeout=3 -o BatchMode=yes ${CFG_NAS_USER}@${CFG_NAS_HOST} 'echo ok' 2>/dev/null" 2>/dev/null)
        if [[ "$nas_ssh_ok" == "ok" ]]; then
            ok "Device -> NAS SSH is now working."
        else
            warn "Device -> NAS SSH still failing. NAS features may not work."
        fi
    fi

    # --- RetroArch flatpak /tmp override ---
    info "Setting RetroArch Flatpak /tmp override..."
    $SSH_CMD "$CFG_DEVICE_HOST" '
        if flatpak list --app 2>/dev/null | grep -q org.libretro.RetroArch; then
            flatpak override --user --filesystem=/tmp org.libretro.RetroArch 2>/dev/null
        fi
    ' 2>/dev/null
    ok "RetroArch /tmp access configured."

    # --- Enable NAS services ---
    info "Enabling NAS mount service..."
    $SSH_CMD "$CFG_DEVICE_HOST" "systemctl --user enable nas-mount.service" 2>/dev/null && \
        ok "nas-mount.service enabled" || warn "Could not enable nas-mount.service"

    info "Enabling NAS library sync timer..."
    $SSH_CMD "$CFG_DEVICE_HOST" "systemctl --user enable --now nas-library-sync.timer" 2>/dev/null && \
        ok "nas-library-sync.timer enabled" || warn "Could not enable nas-library-sync.timer"

    # --- Initial mount + sync ---
    info "Attempting initial NAS mount..."
    local mount_ok
    mount_ok=$($SSH_CMD "$CFG_DEVICE_HOST" \
        "bash ~/Emulation/tools/nas-mount.sh mount 2>/dev/null && echo ok || echo fail" 2>/dev/null)
    if [[ "$mount_ok" == "ok" ]]; then
        ok "NAS mounted at $CFG_NAS_MOUNT"
        info "Running initial library sync..."
        $SSH_CMD "$CFG_DEVICE_HOST" "bash ~/Emulation/tools/nas-library-sync.sh" 2>/dev/null
        ok "Library sync complete."
    else
        warn "NAS mount failed. Sync will run automatically on home network."
    fi

    DID_NAS=true
}

# ============================================================================
# PHASE 10 — Steam Shortcuts + Verification
# ============================================================================
phase_verify() {
    phase_header 10 "Steam Shortcuts + Verification"

    # --- Generate Steam shortcuts ---
    info "Generating Steam shortcuts on device..."
    local steam_out
    steam_out=$($SSH_CMD "$CFG_DEVICE_HOST" "python3 ~/Emulation/tools/add-roms-to-steam.py 2>&1" 2>/dev/null)
    local total_line
    total_line=$(echo "$steam_out" | grep "^Total:")
    if [[ -n "$total_line" ]]; then
        ok "$total_line"
        DID_SHORTCUTS=true
    else
        warn "Steam shortcut generation returned no summary."
        echo "$steam_out" | tail -5
    fi

    # --- BIOS check ---
    echo ""
    info "Running BIOS check..."
    local bios_out
    bios_out=$($SSH_CMD "$CFG_DEVICE_HOST" \
        "test -f ~/Emulation/tools/bios-check.sh && bash ~/Emulation/tools/bios-check.sh 2>&1 || echo 'bios-check.sh not found'" 2>/dev/null)
    echo "$bios_out"

    # --- System tool check ---
    echo ""
    info "Checking system tools..."
    local tools_result
    tools_result=$($SSH_CMD "$CFG_DEVICE_HOST" '
        missing=""
        for tool in ssh scp sshfs unzip zenity dbus-monitor tar python3; do
            command -v "$tool" &>/dev/null || missing="$missing $tool"
        done
        command -v xdotool &>/dev/null || missing="$missing xdotool(optional)"
        echo "${missing:-none}"
    ' 2>/dev/null)

    if [[ "$tools_result" == "none" ]]; then
        ok "All system tools present"
    else
        for tool in $tools_result; do
            if [[ "$tool" == *"(optional)"* ]]; then
                warn "Missing: ${tool%%(optional)*} (optional)"
            else
                fail "Missing: $tool"
            fi
        done
    fi

    # --- NAS SSH check ---
    if [[ -n "$CFG_NAS_HOST" ]]; then
        local nas_ssh
        nas_ssh=$($SSH_CMD "$CFG_DEVICE_HOST" \
            "ssh -i ~/.ssh/id_ed25519 -o ConnectTimeout=3 -o BatchMode=yes ${CFG_NAS_USER}@${CFG_NAS_HOST} 'echo ok' 2>/dev/null" 2>/dev/null)
        if [[ "$nas_ssh" == "ok" ]]; then
            ok "Device -> NAS SSH working (${CFG_NAS_USER}@${CFG_NAS_HOST})"
        else
            fail "Device -> NAS SSH failed."
        fi
    fi

    # --- Config check ---
    local config_ok
    config_ok=$($SSH_CMD "$CFG_DEVICE_HOST" '
        if [ -f ~/Emulation/tools/config.env ]; then echo "tools"
        elif [ -f ~/DeckDock/config.env ]; then echo "deckdock"
        else echo "missing"
        fi
    ' 2>/dev/null)

    case "$config_ok" in
        tools)   ok "Device config at ~/Emulation/tools/config.env" ;;
        deckdock) ok "Device config at ~/DeckDock/config.env" ;;
        missing)  fail "No config.env on device" ;;
    esac

    # --- Script check ---
    local scripts_check
    scripts_check=$($SSH_CMD "$CFG_DEVICE_HOST" '
        missing=""
        [ -f ~/Emulation/tools/add-roms-to-steam.py ] || missing="$missing add-roms-to-steam.py"
        [ -f ~/Emulation/tools/save-backup.sh ] || missing="$missing save-backup.sh"
        [ -f ~/Emulation/tools/deckdock-nas-hook.sh ] || missing="$missing deckdock-nas-hook.sh"
        [ -f ~/Emulation/tools/deckdock-storage-manager.sh ] || missing="$missing deckdock-storage-manager.sh"
        [ -f ~/Emulation/tools/save-restore.sh ] || missing="$missing save-restore.sh"
        [ -f ~/Emulation/tools/launch-appimage.sh ] || missing="$missing launch-appimage.sh"
        [ -f ~/Emulation/tools/launchers/deckdock-azahar.sh ] || missing="$missing deckdock-azahar.sh"
        echo "${missing:-none}"
    ' 2>/dev/null)

    if [[ "$scripts_check" == "none" ]]; then
        ok "All DeckDock scripts present on device"
    else
        for s in $scripts_check; do
            fail "Missing on device: $s"
        done
    fi

    # --- Service status ---
    echo ""
    info "Systemd services:"
    local services_check
    services_check=$($SSH_CMD "$CFG_DEVICE_HOST" '
        echo "rom-sorter.timer:$(systemctl --user is-active rom-sorter.timer 2>/dev/null)"
        echo "save-backup-watcher:$(systemctl --user is-active save-backup-watcher.service 2>/dev/null)"
        echo "nas-mount:$(systemctl --user is-active nas-mount.service 2>/dev/null)"
        echo "nas-library-sync.timer:$(systemctl --user is-active nas-library-sync.timer 2>/dev/null)"
    ' 2>/dev/null)

    while IFS= read -r line; do
        local svc="${line%%:*}"
        local status="${line##*:}"
        case "$status" in
            active)   ok "$svc - active" ;;
            inactive) warn "$svc - inactive" ;;
            *)        warn "$svc - $status" ;;
        esac
    done <<< "$services_check"
}

# ============================================================================
# PHASE 11 — Summary
# ============================================================================
phase_summary() {
    phase_header 11 "Summary"

    echo ""
    echo -e "${BOLD}${GREEN}"
    echo "  ============================================"
    echo "  DeckDock Setup Complete"
    echo "  ============================================"
    echo -e "${NC}"

    echo -e "  ${BOLD}What was configured:${NC}"
    echo -e "    Device ........... ${CFG_DEVICE_HOST}"
    [[ -n "$CFG_NAS_HOST" ]] && \
    echo -e "    NAS .............. ${CFG_NAS_USER}@${CFG_NAS_HOST}"
    echo ""

    echo -e "  ${BOLD}Services:${NC}"
    $DID_ROM_SORTER    && echo "    [active] ROM Sorter (auto-sort drop folder)"
    $DID_SAVE_BACKUP   && echo "    [active] Save Backup (backup on sleep)"
    $DID_NAS           && echo "    [active] NAS Library (SSHFS mount + sync)"
    $DID_SHORTCUTS     && echo "    [done]   Steam shortcuts generated"
    $DID_EMUDECK       && echo "    [done]   EmuDeck installed"
    $DID_XBOX          && echo "    [done]   Xbox Cloud Gaming"
    $DID_TAILSCALE     && echo "    [done]   Tailscale"
    echo ""

    # Post-setup action items
    local has_actions=false
    echo -e "  ${BOLD}Post-setup actions:${NC}"

    if ! $DID_EMUDECK; then
        echo "    - Install EmuDeck on the device (https://www.emudeck.com)"
        has_actions=true
    fi

    if $DID_TAILSCALE; then
        echo "    - Run 'tailscale up' on device to authenticate (needs browser)"
        has_actions=true
    fi

    if ! $has_actions; then
        echo "    None — you're good to fly."
    fi

    echo ""
    echo -e "  ${DIM}Quick re-deploy after code changes: ./deploy.sh${NC}"
    echo -e "  ${DIM}Re-run full setup: ./setup.sh${NC}"
    echo -e "  ${DIM}Re-run from a specific phase: ./setup.sh --phase N${NC}"
    echo ""
}

# ============================================================================
# CLI PARSING
# ============================================================================
show_help() {
    echo ""
    echo -e "${BOLD}DeckDock Setup${NC}"
    echo ""
    echo "Usage: ./setup.sh [OPTIONS]"
    echo ""
    echo "  (no args)       Full setup from scratch"
    echo "  --phase N       Start from phase N (1-11)"
    echo "  --skip-config   Skip config wizard, use existing config.env"
    echo "  --verify        Verification only (same as --phase 10)"
    echo "  --help          Show this help"
    echo ""
    echo "Phases:"
    echo "   1  Configuration wizard"
    echo "   2  SSH key setup"
    echo "   3  Device folder structure"
    echo "   4  Push scripts & config"
    echo "   5  EmuDeck"
    echo "   6  Core services (ROM sorter, save backup)"
    echo "   7  Xbox Cloud Gaming"
    echo "   8  Tailscale"
    echo "   9  NAS game library"
    echo "  10  Steam shortcuts + verification"
    echo "  11  Summary"
    echo ""
}

# ── Parse arguments ─────────────────────────────────────────────────────────
START_PHASE=1
SKIP_CONFIG=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --phase)
            START_PHASE="${2:-}"
            if [[ -z "$START_PHASE" ]] || ! [[ "$START_PHASE" =~ ^[0-9]+$ ]] || \
               [[ "$START_PHASE" -lt 1 ]] || [[ "$START_PHASE" -gt 11 ]]; then
                fail "Invalid phase: ${2:-}. Must be 1-11."
                exit 1
            fi
            shift 2 ;;
        --skip-config)
            SKIP_CONFIG=true
            shift ;;
        --verify)
            START_PHASE=10
            SKIP_CONFIG=true
            shift ;;
        --help|-h)
            show_help
            exit 0 ;;
        *)
            fail "Unknown option: $1"
            show_help
            exit 1 ;;
    esac
done

# ============================================================================
# MAIN
# ============================================================================
main() {
    echo ""
    echo -e "${CYAN}${BOLD}"
    cat << 'BANNER'
    ____            __   ____             __
   / __ \___  _____/ /__/ __ \____  _____/ /__
  / / / / _ \/ ___/ //_/ / / / __ \/ ___/ //_/
 / /_/ /  __/ /__/ ,< / /_/ / /_/ / /__/ ,<
/_____/\___/\___/_/|_/_____/\____/\___/_/|_|
BANNER
    echo -e "${NC}"
    echo -e "  ${DIM}Unified setup — runs on PC, configures everything over SSH${NC}"
    echo ""

    # If skipping config or starting past phase 1, load existing config
    if [[ "$SKIP_CONFIG" == true ]] || [[ "$START_PHASE" -gt 1 ]]; then
        load_config
        info "Loaded config.env (device: $CFG_DEVICE_HOST)"
    fi

    # If starting past phase 2, verify SSH connectivity
    if [[ "$START_PHASE" -gt 2 ]]; then
        if ! ssh_ok; then
            fail "Cannot SSH to $CFG_DEVICE_HOST. Fix connectivity first."
            exit 1
        fi
        ok "Device reachable ($CFG_DEVICE_HOST)"
    fi

    # Run phases from START_PHASE onward
    [[ "$START_PHASE" -le 1 ]]  && phase_config
    # After phase 1, ensure SSH is initialized
    [[ "$START_PHASE" -le 1 ]]  && init_ssh

    [[ "$START_PHASE" -le 2 ]]  && phase_ssh_keys
    [[ "$START_PHASE" -le 3 ]]  && phase_folders
    [[ "$START_PHASE" -le 4 ]]  && phase_push
    [[ "$START_PHASE" -le 5 ]]  && phase_emudeck
    [[ "$START_PHASE" -le 6 ]]  && phase_services
    [[ "$START_PHASE" -le 7 ]]  && phase_xbox
    [[ "$START_PHASE" -le 8 ]]  && phase_tailscale
    [[ "$START_PHASE" -le 9 ]]  && phase_nas
    [[ "$START_PHASE" -le 10 ]] && phase_verify
    [[ "$START_PHASE" -le 11 ]] && phase_summary
}

main
