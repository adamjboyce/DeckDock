#!/usr/bin/env bash
# ============================================================================
# DeckDock Setup Wizard
# Interactive configuration for DeckDock
# Idempotent — safe to re-run at any time to update your config
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="$SCRIPT_DIR/config.env"
EXAMPLE_FILE="$SCRIPT_DIR/config.example.env"

# ----------------------------------------------------------------------------
# Colors
# ----------------------------------------------------------------------------
GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BOLD='\033[1m'
DIM='\033[2m'
RESET='\033[0m'

# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------
info()    { echo -e "${CYAN}[info]${RESET}  $*"; }
success() { echo -e "${GREEN}[ok]${RESET}    $*"; }
warn()    { echo -e "${YELLOW}[warn]${RESET}  $*"; }
error()   { echo -e "${RED}[error]${RESET} $*"; }

# Print a section header
section() {
    echo ""
    echo -e "${BOLD}${CYAN}── $* ──${RESET}"
    echo ""
}

# Prompt for a value with a default. Usage: ask "Prompt text" DEFAULT_VALUE
# Stores result in $REPLY
ask() {
    local prompt="$1"
    local default="$2"
    echo -en "  ${prompt} ${DIM}[${default}]${RESET}: "
    read -r REPLY
    REPLY="${REPLY:-$default}"
}

# Load an existing config value if config.env exists, otherwise return the default
existing_or_default() {
    local key="$1"
    local fallback="$2"
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

# ----------------------------------------------------------------------------
# ASCII Art Header
# ----------------------------------------------------------------------------
show_header() {
    echo ""
    echo -e "${CYAN}${BOLD}"
    cat << 'BANNER'
    ____            __   ____             __
   / __ \___  _____/ /__/ __ \____  _____/ /__
  / / / / _ \/ ___/ //_/ / / / __ \/ ___/ //_/
 / /_/ /  __/ /__/ ,< / /_/ / /_/ / /__/ ,<
/_____/\___/\___/_/|_/_____/\____/\___/_/|_|

BANNER
    echo -e "${RESET}"
    echo -e "  ${DIM}Extensibility tools for SteamOS and home infrastructure${RESET}"
    echo -e "  ${DIM}─────────────────────────────────────────────────────────${RESET}"
    echo ""

    if [[ -f "$CONFIG_FILE" ]]; then
        info "Existing config.env found — current values shown as defaults."
        info "Press Enter to keep a value, or type a new one to update it."
    else
        info "No config.env found — we'll create one now."
        info "Press Enter to accept defaults shown in brackets."
    fi
}

# ----------------------------------------------------------------------------
# Collect Configuration
# ----------------------------------------------------------------------------
collect_network() {
    section "Network"

    local def_nas_host
    def_nas_host=$(existing_or_default "NAS_HOST" "192.168.1.100")
    ask "NAS IP address" "$def_nas_host"
    CFG_NAS_HOST="$REPLY"

    local def_nas_export
    def_nas_export=$(existing_or_default "NAS_EXPORT" "/volume/shared/roms")
    ask "NFS export path on NAS" "$def_nas_export"
    CFG_NAS_EXPORT="$REPLY"

    local def_device_host
    def_device_host=$(existing_or_default "DEVICE_HOST" "deck@192.168.1.150")
    ask "Device SSH connection (user@ip)" "$def_device_host"
    CFG_DEVICE_HOST="$REPLY"
}

collect_paths() {
    section "Paths"

    local def_staging
    def_staging=$(existing_or_default "STAGING_DIR" "\$HOME/nas-staging")
    ask "Local staging directory" "$def_staging"
    CFG_STAGING_DIR="$REPLY"

    local def_mount
    def_mount=$(existing_or_default "NAS_MOUNT" "/tmp/nas-roms")
    ask "NAS mount point on device" "$def_mount"
    CFG_NAS_MOUNT="$REPLY"

    local def_rom_sub
    def_rom_sub=$(existing_or_default "NAS_ROM_SUBDIR" "roms")
    ask "NAS subdirectory for ROMs" "$def_rom_sub"
    CFG_NAS_ROM_SUBDIR="$REPLY"

    local def_save_sub
    def_save_sub=$(existing_or_default "NAS_SAVE_SUBDIR" "saves")
    ask "NAS subdirectory for save backups" "$def_save_sub"
    CFG_NAS_SAVE_SUBDIR="$REPLY"
}

collect_crawler() {
    section "Crawler"

    local def_port
    def_port=$(existing_or_default "CRAWLER_PORT" "7072")
    ask "Crawler web UI port" "$def_port"
    CFG_CRAWLER_PORT="$REPLY"

    local def_delay
    def_delay=$(existing_or_default "DEFAULT_DELAY" "5")
    ask "Default delay between downloads (seconds)" "$def_delay"
    CFG_DEFAULT_DELAY="$REPLY"

    local def_depth
    def_depth=$(existing_or_default "DEFAULT_DEPTH" "3")
    ask "Default crawl depth" "$def_depth"
    CFG_DEFAULT_DEPTH="$REPLY"
}

collect_backup() {
    section "Backup"

    local def_keep
    def_keep=$(existing_or_default "BACKUP_KEEP" "10")
    ask "Number of rolling save backups to keep" "$def_keep"
    CFG_BACKUP_KEEP="$REPLY"
}

collect_trickle() {
    section "Trickle Push"

    local def_trickle
    def_trickle=$(existing_or_default "TRICKLE_PUSH" "true")
    ask "Auto-push files to NAS after download (true/false)" "$def_trickle"
    CFG_TRICKLE_PUSH="$REPLY"
}

# ----------------------------------------------------------------------------
# Validation
# ----------------------------------------------------------------------------
validate_network() {
    section "Validation"

    # --- Ping NAS ---
    echo -en "  Test NAS connectivity (${CFG_NAS_HOST})? ${DIM}[Y/n/skip]${RESET}: "
    read -r ans
    ans="${ans:-Y}"
    if [[ "$ans" =~ ^[Ss] ]]; then
        warn "Skipping NAS connectivity test."
    elif [[ "$ans" =~ ^[Yy] ]]; then
        if ping -c 1 -W 3 "$CFG_NAS_HOST" &>/dev/null; then
            success "NAS at ${CFG_NAS_HOST} is reachable."
        else
            warn "NAS at ${CFG_NAS_HOST} is not responding. Check the IP or try again later."
        fi
    fi

    # --- SSH to Device ---
    local device_ip
    device_ip="${CFG_DEVICE_HOST#*@}"
    echo -en "  Test SSH to device (${CFG_DEVICE_HOST})? ${DIM}[Y/n/skip]${RESET}: "
    read -r ans
    ans="${ans:-Y}"
    if [[ "$ans" =~ ^[Ss] ]]; then
        warn "Skipping device SSH test."
    elif [[ "$ans" =~ ^[Yy] ]]; then
        if ssh -o ConnectTimeout=5 -o BatchMode=yes "$CFG_DEVICE_HOST" "echo ok" &>/dev/null; then
            success "SSH to ${CFG_DEVICE_HOST} works."
        else
            warn "Could not SSH to ${CFG_DEVICE_HOST}. Ensure the device is on and SSH keys are set up."
        fi
    fi
}

# ----------------------------------------------------------------------------
# Write config.env
# ----------------------------------------------------------------------------
write_config() {
    section "Writing Configuration"

    cat > "$CONFIG_FILE" << CONF
# ============================================================================
# DeckDock Configuration
# Generated by setup.sh on $(date '+%Y-%m-%d %H:%M:%S')
# Re-run ./setup.sh at any time to update these values
# ============================================================================

# --- Network ---
NAS_HOST=${CFG_NAS_HOST}
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
CONF

    success "Config written to ${CONFIG_FILE}"
}

# ----------------------------------------------------------------------------
# Optional: Install Python dependencies
# ----------------------------------------------------------------------------
offer_pip_install() {
    local req_file="$SCRIPT_DIR/crawler/requirements.txt"

    if [[ ! -f "$req_file" ]]; then
        return
    fi

    section "Python Dependencies"
    echo -en "  Install crawler Python dependencies? ${DIM}[y/N]${RESET}: "
    read -r ans
    if [[ "$ans" =~ ^[Yy] ]]; then
        info "Running: pip install -r $req_file"
        if pip install -r "$req_file"; then
            success "Python dependencies installed."
        else
            error "pip install failed. You may need to activate a virtualenv or install pip."
        fi
    else
        info "Skipped. Run manually later: pip install -r crawler/requirements.txt"
    fi
}

# ----------------------------------------------------------------------------
# Optional: Install systemd services
# ----------------------------------------------------------------------------
offer_systemd_install() {
    local service_dir="$SCRIPT_DIR/device"
    local service_files=()

    # Collect any .service or .timer files
    while IFS= read -r -d '' f; do
        service_files+=("$f")
    done < <(find "$service_dir" -maxdepth 1 -name '*.service' -o -name '*.timer' 2>/dev/null | tr '\n' '\0')

    if [[ ${#service_files[@]} -eq 0 ]]; then
        return
    fi

    section "Systemd Services"
    info "Found ${#service_files[@]} service file(s) in device/:"
    for f in "${service_files[@]}"; do
        echo -e "    ${DIM}$(basename "$f")${RESET}"
    done
    echo ""

    echo -en "  Install and enable systemd services? ${DIM}[y/N]${RESET}: "
    read -r ans
    if [[ "$ans" =~ ^[Yy] ]]; then
        local dest="$HOME/.config/systemd/user"
        mkdir -p "$dest"

        for f in "${service_files[@]}"; do
            local name
            name=$(basename "$f")
            cp "$f" "$dest/$name"
            success "Copied $name to $dest/"
        done

        systemctl --user daemon-reload
        success "Systemd user daemon reloaded."

        for f in "${service_files[@]}"; do
            local name
            name=$(basename "$f")
            echo -en "  Enable ${name}? ${DIM}[y/N]${RESET}: "
            read -r enable_ans
            if [[ "$enable_ans" =~ ^[Yy] ]]; then
                systemctl --user enable "$name"
                success "Enabled $name"
            fi
        done
    else
        info "Skipped. Install manually later if needed."
    fi
}

# ----------------------------------------------------------------------------
# Summary
# ----------------------------------------------------------------------------
show_summary() {
    echo ""
    echo -e "${BOLD}${GREEN}"
    echo "  ============================================"
    echo "  DeckDock Setup Complete"
    echo "  ============================================"
    echo -e "${RESET}"
    echo -e "  ${BOLD}Configuration:${RESET}  ${CONFIG_FILE}"
    echo ""
    echo -e "  ${CYAN}Network${RESET}"
    echo -e "    NAS Host ........... ${CFG_NAS_HOST}"
    echo -e "    NFS Export ......... ${CFG_NAS_EXPORT}"
    echo -e "    Device SSH ......... ${CFG_DEVICE_HOST}"
    echo ""
    echo -e "  ${CYAN}Paths${RESET}"
    echo -e "    Staging Dir ........ ${CFG_STAGING_DIR}"
    echo -e "    NAS Mount .......... ${CFG_NAS_MOUNT}"
    echo -e "    ROM Subdir ......... ${CFG_NAS_ROM_SUBDIR}"
    echo -e "    Save Subdir ........ ${CFG_NAS_SAVE_SUBDIR}"
    echo ""
    echo -e "  ${CYAN}Crawler${RESET}"
    echo -e "    Web UI Port ........ ${CFG_CRAWLER_PORT}"
    echo -e "    Download Delay ..... ${CFG_DEFAULT_DELAY}s"
    echo -e "    Crawl Depth ........ ${CFG_DEFAULT_DEPTH}"
    echo ""
    echo -e "  ${CYAN}Backup${RESET}"
    echo -e "    Rolling Backups .... ${CFG_BACKUP_KEEP}"
    echo ""
    echo -e "  ${CYAN}Trickle Push${RESET}"
    echo -e "    Auto-push .......... ${CFG_TRICKLE_PUSH}"
    echo ""
    echo -e "  ${DIM}Re-run ./setup.sh at any time to update your config.${RESET}"
    echo ""
}

# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------
main() {
    show_header

    collect_network
    collect_paths
    collect_crawler
    collect_backup
    collect_trickle

    validate_network
    write_config

    offer_pip_install
    offer_systemd_install

    show_summary
}

main "$@"
