#!/bin/bash
# ============================================================================
# DeckDock — First-Time Device Setup
# ============================================================================
# Sets up a Steam Deck or Linux handheld for retro gaming:
#   1. Installs EmuDeck (the emulator manager)
#   2. Creates the folder structure for ROMs, saves, tools
#   3. Installs DeckDock device services (save backups, ROM sorter)
#   4. Configures NAS connection for remote storage
#   5. Sets up SSH keys for passwordless access from your PC
#
# Run this ON your handheld device (Steam Deck, Legion Go, etc.)
# in Desktop Mode, from a terminal (Konsole).
#
# Usage:  bash emu-setup.sh
# ============================================================================

set -euo pipefail

# --- Colors ---
GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BOLD='\033[1m'
NC='\033[0m'

log()  { echo -e "${GREEN}[OK]${NC} $1"; }
info() { echo -e "${CYAN}[..]${NC} $1"; }
warn() { echo -e "${YELLOW}[!!]${NC} $1"; }
fail() { echo -e "${RED}[FAIL]${NC} $1"; }
ask()  { echo -en "${BOLD}$1${NC}"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DECKDOCK_DIR="$(dirname "$SCRIPT_DIR")"
CONFIG_FILE="${DECKDOCK_CONFIG:-$DECKDOCK_DIR/config.env}"

# ============================================================================
# Header
# ============================================================================
clear
echo -e "${CYAN}"
cat << 'BANNER'
  ____            _    ____             _
 |  _ \  ___  ___| | _|  _ \  ___   ___| | __
 | | | |/ _ \/ __| |/ / | | |/ _ \ / __| |/ /
 | |_| |  __/ (__|   <| |_| | (_) | (__|   <
 |____/ \___|\___|_|\_\____/ \___/ \___|_|\_\

   Device Setup — Get your handheld game-ready
BANNER
echo -e "${NC}"
echo "This will set up your device for retro gaming with:"
echo "  - EmuDeck (emulator manager and configurator)"
echo "  - Automatic ROM sorting and organization"
echo "  - Save game backups (every time you put the device to sleep)"
echo "  - NAS integration for storing your game library"
echo ""

# ============================================================================
# Step 0: Load config if it exists
# ============================================================================
NAS_HOST=""
NAS_EXPORT=""
NAS_MOUNT="/tmp/nas-mount"
NAS_SAVE_SUBDIR="saves"
BACKUP_KEEP=10

if [ -f "$CONFIG_FILE" ]; then
    info "Loading settings from config.env..."
    # Source only specific known variables (safe)
    eval "$(grep -E '^(NAS_HOST|NAS_EXPORT|NAS_MOUNT|NAS_SAVE_SUBDIR|BACKUP_KEEP)=' "$CONFIG_FILE")"
    log "Config loaded."
else
    warn "No config.env found. We'll ask you a few questions instead."
fi

# ============================================================================
# Step 1: EmuDeck
# ============================================================================
echo ""
echo -e "${BOLD}Step 1: EmuDeck — Emulator Manager${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

if [ -d "$HOME/.config/EmuDeck" ] && [ -f "$HOME/.config/EmuDeck/settings.sh" ]; then
    log "EmuDeck is already installed."
    ask "Do you want to update/reinstall it? [y/N]: "
    read -r REINSTALL_EMUDECK
    if [[ ! "$REINSTALL_EMUDECK" =~ ^[Yy] ]]; then
        info "Keeping current EmuDeck installation."
        INSTALL_EMUDECK=false
    else
        INSTALL_EMUDECK=true
    fi
else
    info "EmuDeck isn't installed yet. This is the tool that sets up all"
    info "your emulators (RetroArch, DuckStation, PCSX2, Dolphin, etc.)"
    info "and configures them with good defaults."
    echo ""
    ask "Install EmuDeck now? [Y/n]: "
    read -r INSTALL_EMUDECK_INPUT
    if [[ "$INSTALL_EMUDECK_INPUT" =~ ^[Nn] ]]; then
        INSTALL_EMUDECK=false
        warn "Skipping EmuDeck. You can install it later from https://www.emudeck.com"
    else
        INSTALL_EMUDECK=true
    fi
fi

if [ "$INSTALL_EMUDECK" = true ]; then
    info "Downloading EmuDeck installer..."
    EMUDECK_INSTALLER="/tmp/EmuDeck.desktop"
    curl -sL "https://www.emudeck.com/EmuDeck.desktop" -o "$EMUDECK_INSTALLER" 2>/dev/null

    if [ -f "$EMUDECK_INSTALLER" ]; then
        log "EmuDeck installer downloaded."
        info "Launching EmuDeck setup — follow the on-screen prompts."
        info "When it's done, come back here and press Enter to continue."
        echo ""
        # Try to launch it
        chmod +x "$EMUDECK_INSTALLER"
        bash -c "$(grep '^Exec=' "$EMUDECK_INSTALLER" | sed 's/^Exec=//')" &
        EMUDECK_PID=$!

        ask "Press Enter when EmuDeck setup is finished..."
        read -r
        wait "$EMUDECK_PID" 2>/dev/null || true
        log "EmuDeck setup complete."
    else
        fail "Couldn't download EmuDeck. Check your internet connection."
        warn "You can install it manually from https://www.emudeck.com"
    fi
fi

# ============================================================================
# Step 2: Folder Structure
# ============================================================================
echo ""
echo -e "${BOLD}Step 2: Folder Structure${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

EMU_BASE="$HOME/Emulation"

# These are the folders DeckDock tools need
DIRS_TO_CREATE=(
    "$EMU_BASE/drop"
    "$EMU_BASE/saves"
    "$EMU_BASE/backups"
    "$EMU_BASE/tools"
)

info "Making sure all the right folders exist..."
for dir in "${DIRS_TO_CREATE[@]}"; do
    mkdir -p "$dir"
done

# Create system ROM folders if EmuDeck didn't already
ROM_SYSTEMS=(
    nes snes gb gbc gba nds 3ds n64 gc wii switch
    psx ps2 psp dreamcast saturn segacd
    genesis gamegear mastersystem sega32x
    atari2600 atari5200 atari7800 pcengine
    ngp wonderswan mame scummvm
)

mkdir -p "$EMU_BASE/roms"
for sys in "${ROM_SYSTEMS[@]}"; do
    mkdir -p "$EMU_BASE/roms/$sys"
done

log "Folder structure ready."
echo "  Drop folder:  $EMU_BASE/drop/"
echo "  ROM folders:  $EMU_BASE/roms/<system>/"
echo "  Save backups: $EMU_BASE/backups/"

# ============================================================================
# Step 3: Install DeckDock Services
# ============================================================================
echo ""
echo -e "${BOLD}Step 3: DeckDock Services${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

info "Installing DeckDock tools and services..."

# Copy scripts to tools directory
cp "$SCRIPT_DIR/rom-sorter.sh" "$EMU_BASE/tools/"
cp "$SCRIPT_DIR/drop-cleaner.sh" "$EMU_BASE/tools/"
cp "$SCRIPT_DIR/save-backup.sh" "$EMU_BASE/tools/"
cp "$SCRIPT_DIR/sleep-watcher.sh" "$EMU_BASE/tools/"
chmod +x "$EMU_BASE/tools/rom-sorter.sh"
chmod +x "$EMU_BASE/tools/drop-cleaner.sh"
chmod +x "$EMU_BASE/tools/save-backup.sh"
chmod +x "$EMU_BASE/tools/sleep-watcher.sh"
log "Scripts installed to $EMU_BASE/tools/"

# Install systemd user services
SYSTEMD_DIR="$HOME/.config/systemd/user"
mkdir -p "$SYSTEMD_DIR"

cp "$SCRIPT_DIR/rom-sorter.service" "$SYSTEMD_DIR/"
cp "$SCRIPT_DIR/rom-sorter.timer" "$SYSTEMD_DIR/"
cp "$SCRIPT_DIR/save-backup-watcher.service" "$SYSTEMD_DIR/"

systemctl --user daemon-reload

# ROM Sorter (runs every 30 seconds, sorts files dropped into the drop folder)
echo ""
info "The ROM Sorter watches your drop folder and automatically moves files"
info "into the right emulator folders based on file type."
ask "Enable automatic ROM sorting? [Y/n]: "
read -r ENABLE_SORTER
if [[ ! "$ENABLE_SORTER" =~ ^[Nn] ]]; then
    systemctl --user enable --now rom-sorter.timer
    log "ROM Sorter enabled — drop files into $EMU_BASE/drop/"
else
    warn "ROM Sorter not enabled. You can enable it later with:"
    echo "  systemctl --user enable --now rom-sorter.timer"
fi

# Save Backup (triggers when device goes to sleep)
echo ""
info "Save Backup automatically backs up your game saves every time"
info "you put your device to sleep. Never lose progress again."
ask "Enable automatic save backups? [Y/n]: "
read -r ENABLE_BACKUP
if [[ ! "$ENABLE_BACKUP" =~ ^[Nn] ]]; then
    systemctl --user enable --now save-backup-watcher.service
    log "Save Backup enabled — saves backed up on every sleep."
else
    warn "Save Backup not enabled. You can enable it later with:"
    echo "  systemctl --user enable --now save-backup-watcher.service"
fi

# ============================================================================
# Step 4: NAS Connection (Optional)
# ============================================================================
echo ""
echo -e "${BOLD}Step 4: Network Storage (Optional)${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "If you have a NAS (network storage device), DeckDock can automatically"
echo "back up your saves there too. This keeps your saves safe even if"
echo "something happens to your handheld."
echo ""

if [ -z "$NAS_HOST" ]; then
    ask "Do you have a NAS you'd like to connect? [y/N]: "
    read -r SETUP_NAS
    if [[ "$SETUP_NAS" =~ ^[Yy] ]]; then
        ask "NAS IP address (e.g. 192.168.1.100): "
        read -r NAS_HOST
        ask "NAS shared folder path (e.g. /volume1/shared): "
        read -r NAS_EXPORT
        ask "Folder name for saves on the NAS [saves]: "
        read -r NAS_SAVE_SUBDIR
        NAS_SAVE_SUBDIR="${NAS_SAVE_SUBDIR:-saves}"
    fi
fi

if [ -n "$NAS_HOST" ]; then
    info "Testing NAS connection..."
    if ping -c 1 -W 2 "$NAS_HOST" &>/dev/null; then
        log "NAS is reachable at $NAS_HOST"
    else
        warn "Can't reach $NAS_HOST right now. That's OK — save backups"
        warn "will work locally and push to the NAS when it's available."
    fi
fi

# ============================================================================
# Step 5: SSH Key Setup
# ============================================================================
echo ""
echo -e "${BOLD}Step 5: Remote Access${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "DeckDock works best when your PC can send files to this device"
echo "without needing a password every time. This uses SSH keys."
echo ""

if [ ! -f "$HOME/.ssh/id_ed25519" ] && [ ! -f "$HOME/.ssh/id_rsa" ]; then
    ask "Generate an SSH key for this device? [Y/n]: "
    read -r GEN_SSH
    if [[ ! "$GEN_SSH" =~ ^[Nn] ]]; then
        ssh-keygen -t ed25519 -f "$HOME/.ssh/id_ed25519" -N "" -q
        log "SSH key created."
        echo ""
        echo "To connect from your PC without a password, run this on your PC:"
        echo ""
        echo -e "  ${CYAN}ssh-copy-id $(whoami)@$(hostname -I | awk '{print $1}')${NC}"
        echo ""
    fi
else
    log "SSH key already exists."
fi

# Make sure sshd is running so the PC can connect TO this device
if systemctl is-active --quiet sshd 2>/dev/null; then
    log "SSH server is running."
else
    info "Starting SSH server so your PC can connect to this device..."
    sudo -n systemctl enable --now sshd 2>/dev/null && log "SSH server enabled." || \
        warn "Couldn't start SSH server automatically. Run: sudo systemctl enable --now sshd"
fi

# ============================================================================
# Step 6: Write local config
# ============================================================================
echo ""
echo -e "${BOLD}Step 6: Saving Settings${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

LOCAL_CONFIG="$DECKDOCK_DIR/config.env"

# Only write NAS settings if provided
if [ -n "$NAS_HOST" ]; then
    cat > "$LOCAL_CONFIG" << EOF
# DeckDock Device Config (auto-generated by emu-setup.sh)
NAS_HOST=$NAS_HOST
NAS_EXPORT=$NAS_EXPORT
NAS_MOUNT=$NAS_MOUNT
NAS_SAVE_SUBDIR=$NAS_SAVE_SUBDIR
BACKUP_KEEP=$BACKUP_KEEP
EOF
    log "Settings saved to $LOCAL_CONFIG"
else
    log "No NAS configured — local-only mode."
fi

# ============================================================================
# Done!
# ============================================================================
echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}  Setup Complete!${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo "Here's what's set up:"
echo ""
echo "  Drop folder    $EMU_BASE/drop/"
echo "                 Drop ROM files here — they'll be sorted automatically."
echo ""
echo "  ROM folders    $EMU_BASE/roms/<system>/"
echo "                 Where your sorted games live."
echo ""
echo "  Save backups   Every time you put this device to sleep,"
echo "                 your game saves are backed up automatically."
echo ""
if [ -n "$NAS_HOST" ]; then
echo "  NAS backups    Saves also get pushed to your NAS at $NAS_HOST"
echo "                 when you're on your home network."
echo ""
fi
echo "  Next steps:"
echo "    1. Drop some ROMs into $EMU_BASE/drop/ to test sorting"
echo "    2. Open Steam ROM Manager to add sorted games to Steam"
echo "    3. From your PC, run the DeckDock crawler to download games"
echo "       and push them to this device automatically"
echo ""
echo "  Need help?  https://github.com/adamjboyce/DeckDock"
echo ""
