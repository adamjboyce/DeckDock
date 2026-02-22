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
NAS_USER="root"
NAS_EXPORT=""
NAS_MOUNT="/tmp/nas-mount"
NAS_ROM_SUBDIR="roms"
NAS_SAVE_SUBDIR="saves"
BACKUP_KEEP=10

if [ -f "$CONFIG_FILE" ]; then
    info "Loading settings from config.env..."
    # Source only specific known variables (safe)
    eval "$(grep -E '^(NAS_HOST|NAS_USER|NAS_EXPORT|NAS_MOUNT|NAS_ROM_SUBDIR|NAS_SAVE_SUBDIR|BACKUP_KEEP)=' "$CONFIG_FILE")"
    log "Config loaded."
else
    warn "No config.env found. We'll ask you a few questions instead."
fi

# ============================================================================
# Step 1: EmuDeck — Emulator Manager
# ============================================================================
echo ""
echo -e "${BOLD}Step 1: EmuDeck — Emulator Manager${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

INSTALL_EMUDECK=false
EMUDECK_ALREADY_INSTALLED=false

if [ -d "$HOME/.config/EmuDeck" ] && [ -f "$HOME/.config/EmuDeck/settings.sh" ]; then
    EMUDECK_ALREADY_INSTALLED=true
    log "EmuDeck is already installed."
    ask "Do you want to update/reinstall it? [y/N]: "
    read -r REINSTALL_EMUDECK
    if [[ "$REINSTALL_EMUDECK" =~ ^[Yy] ]]; then
        INSTALL_EMUDECK=true
    else
        info "Keeping current EmuDeck installation."
    fi
else
    info "EmuDeck isn't installed yet. This is the tool that sets up all"
    info "your emulators and configures them with good defaults."
    echo ""
    ask "Install EmuDeck now? [Y/n]: "
    read -r INSTALL_EMUDECK_INPUT
    if [[ "$INSTALL_EMUDECK_INPUT" =~ ^[Nn] ]]; then
        warn "Skipping EmuDeck. You can install it later from https://www.emudeck.com"
    else
        INSTALL_EMUDECK=true
    fi
fi

# --- System Picker: ask what they want to play BEFORE running EmuDeck ---
if [ "$INSTALL_EMUDECK" = true ]; then
    echo ""
    echo -e "${BOLD}Which gaming systems do you want to play?${NC}"
    echo "We'll set up the right emulators for each one you pick."
    echo "(Press Enter to accept the default shown in brackets)"
    echo ""

    # --- Nintendo Consoles ---
    ask "  Nintendo consoles (NES, SNES, N64, GameCube, Wii)? [Y/n]: "
    read -r WANT_NINTENDO_CONSOLE
    EMU_RETROARCH=true  # Always needed — handles NES, SNES, and many others
    EMU_DOLPHIN=true
    if [[ "$WANT_NINTENDO_CONSOLE" =~ ^[Nn] ]]; then
        EMU_DOLPHIN=false
    fi

    # --- Nintendo Handhelds ---
    ask "  Nintendo handhelds (Game Boy, GBA, DS, 3DS)? [Y/n]: "
    read -r WANT_NINTENDO_HANDHELD
    EMU_MELONDS=true
    EMU_AZAHAR=true
    EMU_MGBA=false
    if [[ "$WANT_NINTENDO_HANDHELD" =~ ^[Nn] ]]; then
        EMU_MELONDS=false
        EMU_AZAHAR=false
    fi

    # --- PlayStation ---
    ask "  PlayStation (PS1, PS2, PSP)? [Y/n]: "
    read -r WANT_PLAYSTATION
    EMU_DUCKSTATION=true
    EMU_PCSX2=true
    EMU_PPSSPP=true
    if [[ "$WANT_PLAYSTATION" =~ ^[Nn] ]]; then
        EMU_DUCKSTATION=false
        EMU_PCSX2=false
        EMU_PPSSPP=false
    fi

    # --- PS3 (separate because it's resource-heavy) ---
    ask "  PlayStation 3? (needs a powerful device) [y/N]: "
    read -r WANT_PS3
    EMU_RPCS3=false
    if [[ "$WANT_PS3" =~ ^[Yy] ]]; then
        EMU_RPCS3=true
    fi

    # --- Sega ---
    ask "  Sega (Genesis, Saturn, Dreamcast, Game Gear)? [Y/n]: "
    read -r WANT_SEGA
    EMU_FLYCAST=true
    if [[ "$WANT_SEGA" =~ ^[Nn] ]]; then
        EMU_FLYCAST=false
    fi

    # --- Xbox ---
    ask "  Original Xbox? [y/N]: "
    read -r WANT_XBOX
    EMU_XEMU=false
    if [[ "$WANT_XBOX" =~ ^[Yy] ]]; then
        EMU_XEMU=true
    fi

    # --- Nintendo Switch ---
    ask "  Nintendo Switch? (needs game files + firmware) [y/N]: "
    read -r WANT_SWITCH
    EMU_RYUJINX=false
    if [[ "$WANT_SWITCH" =~ ^[Yy] ]]; then
        EMU_RYUJINX=true
    fi

    # --- Wii U ---
    ask "  Wii U? [y/N]: "
    read -r WANT_WIIU
    EMU_CEMU=false
    if [[ "$WANT_WIIU" =~ ^[Yy] ]]; then
        EMU_CEMU=true
    fi

    # --- Arcade ---
    ask "  Arcade games (MAME)? [y/N]: "
    read -r WANT_ARCADE
    EMU_MAME=false
    if [[ "$WANT_ARCADE" =~ ^[Yy] ]]; then
        EMU_MAME=true
    fi

    # --- Classic PC Games ---
    ask "  Classic PC adventure games (ScummVM)? [y/N]: "
    read -r WANT_SCUMMVM
    EMU_SCUMMVM=false
    if [[ "$WANT_SCUMMVM" =~ ^[Yy] ]]; then
        EMU_SCUMMVM=true
    fi

    # --- PS Vita ---
    ask "  PS Vita? [y/N]: "
    read -r WANT_VITA
    EMU_VITA3K=false
    if [[ "$WANT_VITA" =~ ^[Yy] ]]; then
        EMU_VITA3K=true
    fi

    # --- Write EmuDeck settings BEFORE launching the installer ---
    echo ""
    info "Pre-configuring EmuDeck with your choices..."
    EMUDECK_CONFIG_DIR="$HOME/.config/EmuDeck"
    mkdir -p "$EMUDECK_CONFIG_DIR"

    # If settings.sh exists, update it; otherwise create fresh
    SETTINGS_FILE="$EMUDECK_CONFIG_DIR/settings.sh"

    # Write emulator toggles — EmuDeck reads these during setup
    # We only set the emulator enable/disable flags; EmuDeck handles
    # paths, BIOS, controller config, and everything else.
    cat > "/tmp/deckdock-emu-prefs.sh" << EMUEOF
# DeckDock system choices (pre-configured by emu-setup.sh)
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

    if [ -f "$SETTINGS_FILE" ]; then
        # Merge our choices into the existing settings file
        # For each key, replace the line if it exists, or append if it doesn't
        while IFS='=' read -r key val; do
            [[ "$key" =~ ^#.*$ || -z "$key" ]] && continue
            if grep -q "^${key}=" "$SETTINGS_FILE" 2>/dev/null; then
                sed -i "s|^${key}=.*|${key}=${val}|" "$SETTINGS_FILE"
            else
                echo "${key}=${val}" >> "$SETTINGS_FILE"
            fi
        done < "/tmp/deckdock-emu-prefs.sh"
        log "Updated existing EmuDeck settings with your choices."
    else
        cp "/tmp/deckdock-emu-prefs.sh" "$SETTINGS_FILE"
        log "Created EmuDeck settings with your choices."
    fi
    rm -f "/tmp/deckdock-emu-prefs.sh"

    # Summary of what we're setting up
    echo ""
    info "Emulators that will be installed:"
    [ "$EMU_RETROARCH" = true ]   && echo "    RetroArch  — NES, SNES, Game Boy, Genesis, and more"
    [ "$EMU_DOLPHIN" = true ]     && echo "    Dolphin    — GameCube and Wii"
    [ "$EMU_DUCKSTATION" = true ] && echo "    DuckStation — PlayStation 1"
    [ "$EMU_PCSX2" = true ]       && echo "    PCSX2      — PlayStation 2"
    [ "$EMU_RPCS3" = true ]       && echo "    RPCS3      — PlayStation 3"
    [ "$EMU_PPSSPP" = true ]      && echo "    PPSSPP     — PSP"
    [ "$EMU_VITA3K" = true ]      && echo "    Vita3K     — PS Vita"
    [ "$EMU_AZAHAR" = true ]      && echo "    Azahar     — Nintendo 3DS"
    [ "$EMU_MELONDS" = true ]     && echo "    melonDS    — Nintendo DS"
    [ "$EMU_FLYCAST" = true ]     && echo "    Flycast    — Dreamcast"
    [ "$EMU_XEMU" = true ]        && echo "    xemu       — Original Xbox"
    [ "$EMU_RYUJINX" = true ]     && echo "    Ryujinx    — Nintendo Switch"
    [ "$EMU_CEMU" = true ]        && echo "    Cemu       — Wii U"
    [ "$EMU_MAME" = true ]        && echo "    MAME       — Arcade"
    [ "$EMU_SCUMMVM" = true ]     && echo "    ScummVM    — Classic PC adventures"
    echo ""

    # Now download and launch EmuDeck
    info "Downloading EmuDeck installer..."
    EMUDECK_INSTALLER="/tmp/EmuDeck.desktop"
    curl -sL "https://www.emudeck.com/EmuDeck.desktop" -o "$EMUDECK_INSTALLER" 2>/dev/null

    if [ -f "$EMUDECK_INSTALLER" ]; then
        log "EmuDeck installer downloaded."
        info "Launching EmuDeck setup — it will pick up your system choices."
        info "Follow any remaining on-screen prompts (storage location, etc.)"
        info "When it's done, come back here and press Enter to continue."
        echo ""
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
        warn "Your emulator choices have been saved and will be picked up when you run EmuDeck."
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
# Step 4: Xbox Cloud Gaming (Optional)
# ============================================================================
echo ""
echo -e "${BOLD}Step 4: Xbox Cloud Gaming (Optional)${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Play Xbox games on your handheld by streaming them from the cloud."
echo "This works over Wi-Fi — no downloads needed. You'll need an Xbox"
echo "Game Pass Ultimate subscription to use it."
echo ""

SETUP_XBOX=false
ask "Set up Xbox Cloud Gaming? [y/N]: "
read -r XBOX_INPUT
if [[ "$XBOX_INPUT" =~ ^[Yy] ]]; then
    SETUP_XBOX=true

    # Find a Chromium-based browser (Chrome, Edge, or Chromium)
    XBOX_BROWSER=""
    XBOX_BROWSER_NAME=""

    if flatpak list 2>/dev/null | grep -q "com.microsoft.Edge"; then
        XBOX_BROWSER="com.microsoft.Edge"
        XBOX_BROWSER_NAME="Microsoft Edge"
    elif flatpak list 2>/dev/null | grep -q "com.google.Chrome"; then
        XBOX_BROWSER="com.google.Chrome"
        XBOX_BROWSER_NAME="Google Chrome"
    elif flatpak list 2>/dev/null | grep -q "org.chromium.Chromium"; then
        XBOX_BROWSER="org.chromium.Chromium"
        XBOX_BROWSER_NAME="Chromium"
    fi

    if [ -z "$XBOX_BROWSER" ]; then
        info "No compatible browser found. Installing Google Chrome..."
        if flatpak install -y --user flathub com.google.Chrome 2>/dev/null; then
            XBOX_BROWSER="com.google.Chrome"
            XBOX_BROWSER_NAME="Google Chrome"
            log "Google Chrome installed."
        else
            fail "Couldn't install Chrome. You may need to add the Flathub repo first:"
            echo "  flatpak remote-add --if-not-exists flathub https://flathub.org/repo/flathub.flatpakrepo"
            SETUP_XBOX=false
        fi
    else
        log "Found $XBOX_BROWSER_NAME — will use that."
    fi

    if [ "$SETUP_XBOX" = true ]; then
        # Create a .desktop launcher for Xbox Cloud Gaming
        XBOX_DESKTOP="$HOME/.local/share/applications/xbox-cloud-gaming.desktop"
        mkdir -p "$(dirname "$XBOX_DESKTOP")"
        cat > "$XBOX_DESKTOP" << XBOXEOF
[Desktop Entry]
Name=Xbox Cloud Gaming
Comment=Stream Xbox games from the cloud
Exec=flatpak run $XBOX_BROWSER --kiosk --start-fullscreen --app=https://www.xbox.com/play
Type=Application
Categories=Game;
Icon=applications-games
Terminal=false
XBOXEOF
        log "Xbox Cloud Gaming shortcut created."

        # Also add as a Steam shortcut if possible
        info "You can add Xbox Cloud Gaming to Steam in two ways:"
        echo "    1. In Steam Desktop mode: Games → Add a Non-Steam Game → Xbox Cloud Gaming"
        echo "    2. Through Steam ROM Manager (we'll set that up next)"
        echo ""
        echo "  Tip: In Gaming Mode, use the on-screen controls or pair an Xbox controller"
        echo "  over Bluetooth for the best experience."
    fi
fi

# ============================================================================
# Step 5: Steam ROM Manager — Add Games to Steam
# ============================================================================
echo ""
echo -e "${BOLD}Step 5: Steam ROM Manager — Add Games to Steam${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Steam ROM Manager scans your game folders and adds every game to your"
echo "Steam library automatically — with artwork, icons, and categories."
echo "After this, all your retro games show up in Gaming Mode."
echo ""

# Find SRM — could be AppImage (EmuDeck installs this) or Flatpak
SRM_CMD=""
SRM_NAME=""

if [ -f "$HOME/Emulation/tools/Steam-ROM-Manager.AppImage" ]; then
    SRM_CMD="$HOME/Emulation/tools/Steam-ROM-Manager.AppImage"
    SRM_NAME="AppImage"
elif [ -f "$EMU_BASE/tools/Steam-ROM-Manager.AppImage" ]; then
    SRM_CMD="$EMU_BASE/tools/Steam-ROM-Manager.AppImage"
    SRM_NAME="AppImage"
elif flatpak list 2>/dev/null | grep -q "com.steamgriddb.steam-rom-manager"; then
    SRM_CMD="flatpak run com.steamgriddb.steam-rom-manager"
    SRM_NAME="Flatpak"
fi

if [ -n "$SRM_CMD" ]; then
    log "Steam ROM Manager found ($SRM_NAME)."
    echo ""
    echo "  You have two options:"
    echo ""
    echo "    1. Auto-add — Scans your ROM folders and adds everything to Steam right now."
    echo "       (Steam must be closed for this to work.)"
    echo ""
    echo "    2. Open the app — Launch Steam ROM Manager so you can preview and customize"
    echo "       which games get added and how they look."
    echo ""
    ask "What would you like to do? [1=Auto-add / 2=Open app / N=Skip]: "
    read -r SRM_CHOICE

    if [[ "$SRM_CHOICE" == "1" ]]; then
        # Check if Steam is running
        if pgrep -x steam > /dev/null 2>&1; then
            warn "Steam is currently running. Please close it first."
            ask "Press Enter once Steam is closed (or type 'skip' to skip)... "
            read -r STEAM_CLOSED
            if [[ "$STEAM_CLOSED" == "skip" ]]; then
                warn "Skipping Steam ROM Manager. You can run it later from Desktop Mode."
                SRM_CHOICE="skip"
            fi
        fi

        if [[ "$SRM_CHOICE" == "1" ]]; then
            info "Adding all games to Steam (this may take a minute)..."
            if echo "$SRM_CMD" | grep -q "flatpak"; then
                flatpak run com.steamgriddb.steam-rom-manager add 2>/dev/null && \
                    log "Games added to Steam! They'll appear when you restart Steam." || \
                    warn "Auto-add didn't work. Try opening the app instead (option 2)."
            else
                "$SRM_CMD" add 2>/dev/null && \
                    log "Games added to Steam! They'll appear when you restart Steam." || \
                    warn "Auto-add didn't work. Try opening the app instead (option 2)."
            fi
        fi
    elif [[ "$SRM_CHOICE" == "2" ]]; then
        info "Opening Steam ROM Manager..."
        if echo "$SRM_CMD" | grep -q "flatpak"; then
            flatpak run com.steamgriddb.steam-rom-manager &
        else
            "$SRM_CMD" &
        fi
        SRM_PID=$!
        echo ""
        echo "  In Steam ROM Manager:"
        echo "    1. Click 'Preview' to see what games will be added"
        echo "    2. Click 'Save to Steam' to add them"
        echo "    3. Close the app when you're done"
        echo ""
        ask "Press Enter when you're done with Steam ROM Manager..."
        read -r
        wait "$SRM_PID" 2>/dev/null || true
        log "Steam ROM Manager done."
    else
        info "Skipping for now. You can run Steam ROM Manager anytime from Desktop Mode."
    fi
else
    warn "Steam ROM Manager isn't installed yet."
    info "EmuDeck usually installs it. If you installed EmuDeck in Step 1,"
    info "try running this setup again after EmuDeck finishes."
    echo ""
    info "Or install it manually:"
    echo "  flatpak install --user flathub com.steamgriddb.steam-rom-manager"
fi

# ============================================================================
# Step 6: Tailscale — Access From Anywhere (Optional)
# ============================================================================
echo ""
echo -e "${BOLD}Step 6: Tailscale — Access From Anywhere (Optional)${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Tailscale lets you access your device from anywhere — not just your"
echo "home Wi-Fi. Stream games remotely, push ROMs from your phone, or SSH"
echo "in from work. It's free for personal use."
echo ""

SETUP_TAILSCALE=false
ask "Set up Tailscale for remote access? [y/N]: "
read -r TS_INPUT
if [[ "$TS_INPUT" =~ ^[Yy] ]]; then
    SETUP_TAILSCALE=true

    # Check if Tailscale is already installed
    if command -v tailscale &>/dev/null; then
        log "Tailscale is already installed."

        if tailscale status &>/dev/null; then
            TS_IP=$(tailscale ip -4 2>/dev/null || echo "unknown")
            log "Tailscale is connected. Your device's Tailscale IP: $TS_IP"
        else
            info "Tailscale is installed but not connected. Logging in..."
            echo ""
            echo "  A browser window will open. Sign in with your Tailscale account"
            echo "  (Google, Microsoft, GitHub, etc.) to connect this device."
            echo ""
            sudo -n tailscale up 2>/dev/null || tailscale up 2>/dev/null || \
                warn "Couldn't start Tailscale automatically. Run: sudo tailscale up"
        fi
    else
        info "Installing Tailscale..."
        if curl -fsSL https://tailscale.com/install.sh | sh 2>/dev/null; then
            log "Tailscale installed."
            info "Starting Tailscale — a browser window will open for you to sign in."
            echo ""
            echo "  Sign in with your Tailscale account (Google, Microsoft, GitHub, etc.)"
            echo "  to connect this device to your personal network."
            echo ""
            sudo -n systemctl enable --now tailscaled 2>/dev/null || true
            sudo -n tailscale up 2>/dev/null || tailscale up 2>/dev/null || \
                warn "Couldn't start Tailscale. Run: sudo tailscale up"

            if tailscale status &>/dev/null; then
                TS_IP=$(tailscale ip -4 2>/dev/null || echo "unknown")
                log "Tailscale connected! Your device's Tailscale IP: $TS_IP"
                echo ""
                echo "  You can now reach this device from anywhere using: $TS_IP"
                echo "  Install Tailscale on your other devices at: https://tailscale.com/download"
            fi
        else
            fail "Couldn't install Tailscale. You can install it manually from:"
            echo "  https://tailscale.com/download"
        fi
    fi

    if [ "$SETUP_TAILSCALE" = true ]; then
        echo ""
        echo "  What Tailscale gives you:"
        echo "    - SSH into your handheld from anywhere (not just home Wi-Fi)"
        echo "    - Push games from your PC when you're away from home"
        echo "    - Use Steam Remote Play over Tailscale for on-the-go streaming"
        echo "    - Access your NAS from anywhere too (if Tailscale is on your NAS)"
    fi
fi

# ============================================================================
# Step 7: NAS Connection (Optional)
# ============================================================================
echo ""
echo -e "${BOLD}Step 7: Network Storage (Optional)${NC}"
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
# Step 8: NAS Game Library (Optional)
# ============================================================================
echo ""
echo -e "${BOLD}Step 8: NAS Game Library (Optional)${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Browse your entire NAS game library from ES-DE without downloading"
echo "everything first. Games download on-demand when you select them."
echo ""

SETUP_NAS_LIBRARY=false

if [ -n "$NAS_HOST" ] && [ -n "$NAS_EXPORT" ]; then
    ask "Set up NAS game library browsing? [Y/n]: "
    read -r NAS_LIB_INPUT
    if [[ ! "$NAS_LIB_INPUT" =~ ^[Nn] ]]; then
        SETUP_NAS_LIBRARY=true
    fi
else
    info "Skipping — no NAS configured. Set up NAS connection first (Step 7)."
fi

if [ "$SETUP_NAS_LIBRARY" = true ]; then
    # --- Check SSHFS is available ---
    if ! command -v sshfs &>/dev/null; then
        warn "SSHFS is not installed. Checking if we can install it..."
        if command -v flatpak &>/dev/null; then
            warn "SSHFS needs to be available on the base system."
        fi
        warn "Try: sudo pacman -S sshfs  (or install via your package manager)"
        warn "Skipping NAS library setup until SSHFS is available."
        SETUP_NAS_LIBRARY=false
    else
        log "SSHFS is available — no sudo needed for NAS mount."
    fi
fi

if [ "$SETUP_NAS_LIBRARY" = true ]; then
    # --- SSH key for NAS ---
    NAS_USER="${NAS_USER:-root}"
    if [ ! -f "$HOME/.ssh/id_ed25519" ]; then
        info "Generating SSH key for NAS access..."
        mkdir -p "$HOME/.ssh"
        ssh-keygen -t ed25519 -f "$HOME/.ssh/id_ed25519" -N "" -q
        log "SSH key created."
    fi

    echo ""
    info "We need to set up passwordless SSH to your NAS."
    info "This will copy your SSH key to ${NAS_USER}@${NAS_HOST}."
    echo ""
    ask "Copy SSH key to NAS now? [Y/n]: "
    read -r COPY_NAS_KEY
    if [[ ! "$COPY_NAS_KEY" =~ ^[Nn] ]]; then
        ssh-copy-id -o StrictHostKeyChecking=accept-new "${NAS_USER}@${NAS_HOST}" 2>/dev/null && \
            log "SSH key copied to NAS." || \
            warn "Couldn't copy key automatically. You may need to enter the NAS password."
    fi

    # --- Verify SSH access ---
    info "Testing SSH connection to NAS..."
    if ssh -o BatchMode=yes -o ConnectTimeout=5 "${NAS_USER}@${NAS_HOST}" "echo ok" &>/dev/null; then
        log "Passwordless SSH to NAS is working."
    else
        warn "SSH to NAS failed. NAS library won't work until SSH access is set up."
        warn "Run: ssh-copy-id ${NAS_USER}@${NAS_HOST}"
    fi

    # Create mount point
    mkdir -p "$NAS_MOUNT"

    # --- Install NAS library scripts ---
    info "Installing NAS library tools..."
    cp "$SCRIPT_DIR/nas-mount.sh" "$EMU_BASE/tools/"
    cp "$SCRIPT_DIR/deckdock-launcher.sh" "$EMU_BASE/tools/"
    cp "$SCRIPT_DIR/nas-library-sync.sh" "$EMU_BASE/tools/"
    chmod +x "$EMU_BASE/tools/nas-mount.sh"
    chmod +x "$EMU_BASE/tools/deckdock-launcher.sh"
    chmod +x "$EMU_BASE/tools/nas-library-sync.sh"
    log "NAS mount, launcher wrapper, and sync scripts installed."

    # --- Flatpak override: expose /tmp to RetroArch ---
    # RetroArch's Flatpak sandbox isolates /tmp by default. Since NAS symlinks
    # point to /tmp/nas-roms, RetroArch can't follow them without this override.
    if flatpak list --app 2>/dev/null | grep -q org.libretro.RetroArch; then
        flatpak override --user --filesystem=/tmp org.libretro.RetroArch 2>/dev/null && \
            log "RetroArch Flatpak: added /tmp filesystem access." || \
            warn "Couldn't set RetroArch Flatpak override."
    fi

    # --- Install systemd services ---
    cp "$SCRIPT_DIR/nas-mount.service" "$SYSTEMD_DIR/"
    cp "$SCRIPT_DIR/nas-library-sync.service" "$SYSTEMD_DIR/"
    cp "$SCRIPT_DIR/nas-library-sync.timer" "$SYSTEMD_DIR/"
    systemctl --user daemon-reload

    # Enable SSHFS mount service
    systemctl --user enable nas-mount.service 2>/dev/null && \
        log "NAS mount service enabled (SSHFS — no sudo needed)." || \
        warn "Couldn't enable mount service."

    # Enable library sync timer
    systemctl --user enable --now nas-library-sync.timer 2>/dev/null && \
        log "Library sync timer enabled (every 5 minutes)." || \
        warn "Couldn't enable sync timer."

    # --- Try initial mount + sync ---
    info "Attempting initial NAS mount via SSHFS..."
    if bash "$EMU_BASE/tools/nas-mount.sh" mount 2>/dev/null; then
        log "NAS mounted at $NAS_MOUNT"
        info "Running initial library sync..."
        bash "$EMU_BASE/tools/nas-library-sync.sh"
        log "NAS library sync complete. Your games should now appear in ES-DE."
    else
        warn "Couldn't mount NAS right now. The sync will run automatically"
        warn "next time you're on your home network."
    fi
fi

# ============================================================================
# Step 9: SSH Key Setup
# ============================================================================
echo ""
echo -e "${BOLD}Step 9: Remote Access (SSH)${NC}"
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
# Step 10: Save Settings
# ============================================================================
echo ""
echo -e "${BOLD}Step 10: Saving Settings${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

LOCAL_CONFIG="$DECKDOCK_DIR/config.env"

# Write all configured settings
{
    echo "# DeckDock Device Config (auto-generated by emu-setup.sh)"
    echo "BACKUP_KEEP=$BACKUP_KEEP"
    if [ -n "$NAS_HOST" ]; then
        echo "NAS_HOST=$NAS_HOST"
        echo "NAS_USER=$NAS_USER"
        echo "NAS_EXPORT=$NAS_EXPORT"
        echo "NAS_MOUNT=$NAS_MOUNT"
        echo "NAS_ROM_SUBDIR=$NAS_ROM_SUBDIR"
        echo "NAS_SAVE_SUBDIR=$NAS_SAVE_SUBDIR"
    fi
} > "$LOCAL_CONFIG"
log "Settings saved to $LOCAL_CONFIG"

# ============================================================================
# Step 11: BIOS Check
# ============================================================================
echo ""
echo -e "${BOLD}Step 11: BIOS File Check${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Some emulators need BIOS files (firmware dumped from real consoles)"
echo "to work properly. Let's check what you've got."
echo ""

BIOS_CHECK_SCRIPT="$SCRIPT_DIR/bios-check.sh"
if [ -f "$BIOS_CHECK_SCRIPT" ]; then
    bash "$BIOS_CHECK_SCRIPT"
else
    warn "BIOS checker not found. You can run it later:"
    echo "  bash device/bios-check.sh"
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
if [ "$SETUP_XBOX" = true ]; then
echo "  Xbox Cloud     Open 'Xbox Cloud Gaming' from your app menu or Steam"
echo "                 to stream Xbox games. Requires Game Pass Ultimate."
echo ""
fi
if [ -n "$NAS_HOST" ]; then
echo "  NAS backups    Saves also get pushed to your NAS at $NAS_HOST"
echo "                 when you're on your home network."
echo ""
fi
if [ "$SETUP_NAS_LIBRARY" = true ]; then
echo "  NAS Library    Your full NAS game library is browsable in ES-DE."
echo "                 Select a game — it downloads automatically and plays."
echo "                 Syncs every 5 minutes when NAS is reachable."
echo ""
fi
if [ "$SETUP_TAILSCALE" = true ]; then
TS_IP=$(tailscale ip -4 2>/dev/null || echo "your-tailscale-ip")
echo "  Tailscale      Access this device from anywhere at $TS_IP"
echo "                 Install Tailscale on your other devices too."
echo ""
fi
echo "  Next steps:"
echo "    1. Drop some ROMs into $EMU_BASE/drop/ to test sorting"
if [ -z "$SRM_CMD" ] || [[ ! "$SRM_CHOICE" =~ ^[12]$ ]]; then
echo "    2. Open Steam ROM Manager to add sorted games to Steam"
else
echo "    2. Restart Steam to see your newly added games"
fi
echo "    3. From your PC, run the DeckDock crawler to download games"
echo "       and push them to this device automatically"
echo ""
echo "  Need help?  https://github.com/adamjboyce/DeckDock"
echo ""
