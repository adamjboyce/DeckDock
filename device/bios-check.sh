#!/bin/bash
# ============================================================================
# DeckDock — BIOS File Checker
# ============================================================================
# Checks which BIOS files are present and which are missing for your
# selected emulators. Tells you exactly what you need and where to put it.
#
# Run this ON your handheld device (Steam Deck, Legion Go, etc.)
#
# Usage:  bash bios-check.sh
# ============================================================================

# --- Colors ---
GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BOLD='\033[1m'
DIM='\033[2m'
NC='\033[0m'

ok()   { echo -e "  ${GREEN}✓${NC} $1"; }
miss() { echo -e "  ${RED}✗${NC} $1"; }
info() { echo -e "  ${DIM}$1${NC}"; }
warn() { echo -e "  ${YELLOW}!${NC} $1"; }

# --- Determine BIOS directory ---
BIOS_DIR="${BIOS_DIR:-$HOME/Emulation/bios}"

# Try EmuDeck settings for the real path
if [ -f "$HOME/.config/EmuDeck/settings.sh" ]; then
    EMUDECK_BIOS=$(grep '^biosPath=' "$HOME/.config/EmuDeck/settings.sh" 2>/dev/null | cut -d'=' -f2- | tr -d '"')
    if [ -n "$EMUDECK_BIOS" ] && [ -d "$EMUDECK_BIOS" ]; then
        BIOS_DIR="$EMUDECK_BIOS"
    fi
fi

# --- Track totals ---
TOTAL=0
FOUND=0
MISSING=0
MISSING_LIST=""

# --- Helper: check a single BIOS file ---
# Usage: check_bios "System Name" "relative/path/to/bios.bin" "expected_md5" "required|optional"
check_bios() {
    local system="$1"
    local rel_path="$2"
    local expected_md5="$3"
    local importance="$4"
    local full_path="$BIOS_DIR/$rel_path"

    TOTAL=$((TOTAL + 1))

    if [ -f "$full_path" ]; then
        # File exists — verify MD5 if we have one
        if [ -n "$expected_md5" ]; then
            actual_md5=$(md5sum "$full_path" 2>/dev/null | awk '{print $1}')
            if [ "$actual_md5" = "$expected_md5" ]; then
                ok "$rel_path  ${DIM}(verified ✓)${NC}"
                FOUND=$((FOUND + 1))
            else
                miss "$rel_path  ${YELLOW}(wrong file — MD5 mismatch)${NC}"
                info "Expected: $expected_md5"
                info "Got:      $actual_md5"
                MISSING=$((MISSING + 1))
                MISSING_LIST="${MISSING_LIST}\n  - ${rel_path} (${system}, ${importance}) — WRONG FILE"
            fi
        else
            ok "$rel_path  ${DIM}(present, no MD5 to verify)${NC}"
            FOUND=$((FOUND + 1))
        fi
    else
        if [ "$importance" = "required" ]; then
            miss "$rel_path  ${RED}(REQUIRED — games won't work without this)${NC}"
        else
            miss "$rel_path  ${YELLOW}(optional — most games work without it)${NC}"
        fi
        MISSING=$((MISSING + 1))
        MISSING_LIST="${MISSING_LIST}\n  - ${rel_path} (${system}, ${importance})"
    fi
}

# ============================================================================
# Header
# ============================================================================
echo ""
echo -e "${CYAN}${BOLD}"
echo "  BIOS File Checker"
echo "  ─────────────────"
echo -e "${NC}"
echo -e "  Checking: ${BOLD}$BIOS_DIR${NC}"
echo ""

if [ ! -d "$BIOS_DIR" ]; then
    echo -e "${RED}  BIOS directory not found: $BIOS_DIR${NC}"
    echo "  Run EmuDeck or emu-setup.sh first to create the directory structure."
    exit 1
fi

# ============================================================================
# PlayStation 1 (RetroArch / DuckStation)
# ============================================================================
echo -e "${BOLD}PlayStation 1${NC}  ${DIM}(DuckStation / Beetle PSX)${NC}"

check_bios "PS1" "SCPH1001.BIN" "924e392ed05558ffdb115408c263dccf" "required"
check_bios "PS1" "scph5500.bin" "8dd7d5296a650fac7319bce665a6a53c" "optional"
check_bios "PS1" "scph5501.bin" "490f666e1afb15b7571ff904e345e522" "optional"
check_bios "PS1" "scph5502.bin" "32736f17079d0b2b7024407c39bd3050" "optional"
echo ""

# ============================================================================
# PlayStation 2 (PCSX2)
# ============================================================================
echo -e "${BOLD}PlayStation 2${NC}  ${DIM}(PCSX2)${NC}"

check_bios "PS2" "SCPH-70012_BIOS_V12_USA_200.BIN" "" "required"
# Alternative names PCSX2 accepts
check_bios "PS2" "SCPH-77001_BIOS_V15_USA_230.BIN" "" "optional"
info "PCSX2 accepts many PS2 BIOS versions. Any USA/EUR/JPN BIOS file works."
info "Common names: SCPH-70012, SCPH-77001, SCPH-39001, SCPH-70004 (EUR)"
echo ""

# ============================================================================
# Dreamcast (Flycast / RetroArch)
# ============================================================================
echo -e "${BOLD}Sega Dreamcast${NC}  ${DIM}(Flycast)${NC}"

check_bios "Dreamcast" "dc/dc_boot.bin" "e10c53c2f8b90bab96ead2d368858623" "required"
check_bios "Dreamcast" "dc/dc_flash.bin" "0a93f7940c455905bea6e392dfde92a4" "optional"
echo ""

# ============================================================================
# Sega Saturn (Kronos / Beetle Saturn)
# ============================================================================
echo -e "${BOLD}Sega Saturn${NC}  ${DIM}(Kronos / Beetle Saturn)${NC}"

check_bios "Saturn" "kronos/saturn_bios.bin" "" "required"
# RetroArch Beetle Saturn uses different name/location
check_bios "Saturn" "sega_101.bin" "85ec9ca47d8f6571be4571e668e13ab2" "optional"
check_bios "Saturn" "mpr-17933.bin" "3240872c70984b6cbfda1586cab68dbe" "optional"
echo ""

# ============================================================================
# Sega CD / Mega CD (RetroArch Genesis Plus GX / PicoDrive)
# ============================================================================
echo -e "${BOLD}Sega CD / Mega CD${NC}  ${DIM}(Genesis Plus GX / PicoDrive)${NC}"

check_bios "Sega CD" "bios_CD_U.bin" "2efd74e3232ff260e371b99f84024f7f" "required"
check_bios "Sega CD" "bios_CD_E.bin" "e66fa1dc5820d254611fdcdba0662372" "optional"
check_bios "Sega CD" "bios_CD_J.bin" "278a9397d192149e84e820ac621a8edd" "optional"
echo ""

# ============================================================================
# Nintendo DS (melonDS / RetroArch)
# ============================================================================
echo -e "${BOLD}Nintendo DS${NC}  ${DIM}(melonDS)${NC}"

check_bios "NDS" "bios7.bin" "df692a80a5b1bc90728bc3dfc76cd948" "required"
check_bios "NDS" "bios9.bin" "a392174eb3e572fed6447e956bde4b25" "required"
check_bios "NDS" "firmware.bin" "" "required"
info "melonDS can run in LLE or HLE mode. HLE works without BIOS for many games,"
info "but some games (especially those with Wi-Fi features) require the real BIOS."
echo ""

# ============================================================================
# Game Boy Advance (mGBA / RetroArch)
# ============================================================================
echo -e "${BOLD}Game Boy Advance${NC}  ${DIM}(mGBA / VBA-M)${NC}"

check_bios "GBA" "gba_bios.bin" "a860e8c0b6d573d191e4ec7db1b1e4f6" "optional"
info "GBA games work fine without BIOS (HLE mode). The BIOS just adds the"
info "real boot animation — the 'GameBoy' splash screen."
echo ""

# ============================================================================
# Nintendo 3DS (Azahar / Citra)
# ============================================================================
echo -e "${BOLD}Nintendo 3DS${NC}  ${DIM}(Azahar)${NC}"

# 3DS uses encrypted keys, not a traditional BIOS
if [ -L "$BIOS_DIR/azahar/keys" ] || [ -d "$BIOS_DIR/azahar/keys" ]; then
    # Check for AES keys file
    AZAHAR_SYSDATA=""
    if [ -L "$BIOS_DIR/azahar/keys" ]; then
        AZAHAR_SYSDATA=$(readlink -f "$BIOS_DIR/azahar/keys" 2>/dev/null)
    else
        AZAHAR_SYSDATA="$BIOS_DIR/azahar/keys"
    fi

    if [ -d "$AZAHAR_SYSDATA" ] && [ "$(ls -A "$AZAHAR_SYSDATA" 2>/dev/null)" ]; then
        ok "azahar/keys  ${DIM}(system data directory exists with files)${NC}"
        TOTAL=$((TOTAL + 1))
        FOUND=$((FOUND + 1))
    else
        miss "azahar/keys  ${YELLOW}(directory exists but is empty)${NC}"
        TOTAL=$((TOTAL + 1))
        MISSING=$((MISSING + 1))
        MISSING_LIST="${MISSING_LIST}\n  - azahar system keys (3DS, required for encrypted games)"
    fi
else
    miss "azahar/keys  ${RED}(not found)${NC}"
    TOTAL=$((TOTAL + 1))
    MISSING=$((MISSING + 1))
    MISSING_LIST="${MISSING_LIST}\n  - azahar system keys (3DS, required for encrypted games)"
fi
info "3DS emulation uses encryption keys, not a traditional BIOS."
info "Decrypted games (.3ds/.cia) may work without keys."
echo ""

# ============================================================================
# Nintendo Switch (Ryujinx)
# ============================================================================
echo -e "${BOLD}Nintendo Switch${NC}  ${DIM}(Ryujinx)${NC}"

RYUJINX_KEYS=""
if [ -L "$BIOS_DIR/ryujinx/keys" ]; then
    RYUJINX_KEYS=$(readlink -f "$BIOS_DIR/ryujinx/keys" 2>/dev/null)
elif [ -d "$HOME/.config/Ryujinx/system" ]; then
    RYUJINX_KEYS="$HOME/.config/Ryujinx/system"
fi

SWITCH_CHECK=false
if [ -n "$RYUJINX_KEYS" ] && [ -d "$RYUJINX_KEYS" ]; then
    if [ -f "$RYUJINX_KEYS/prod.keys" ]; then
        ok "prod.keys  ${DIM}(Switch decryption keys present)${NC}"
        TOTAL=$((TOTAL + 1))
        FOUND=$((FOUND + 1))
        SWITCH_CHECK=true
    fi
    if [ -f "$RYUJINX_KEYS/title.keys" ]; then
        ok "title.keys  ${DIM}(title-specific keys present)${NC}"
        TOTAL=$((TOTAL + 1))
        FOUND=$((FOUND + 1))
    else
        info "title.keys not found (optional — only needed for some games)"
        TOTAL=$((TOTAL + 1))
    fi
fi

if [ "$SWITCH_CHECK" = false ]; then
    miss "prod.keys  ${RED}(REQUIRED for Switch emulation)${NC}"
    TOTAL=$((TOTAL + 1))
    MISSING=$((MISSING + 1))
    MISSING_LIST="${MISSING_LIST}\n  - prod.keys (Switch, required)"
fi
info "Switch emulation requires firmware + keys from your own console."
echo ""

# ============================================================================
# Original Xbox (xemu)
# ============================================================================
echo -e "${BOLD}Original Xbox${NC}  ${DIM}(xemu)${NC}"

# xemu looks in its own config dir or in the BIOS folder
XEMU_BIOS_FOUND=false
for xemu_path in "$BIOS_DIR/xbox/mcpx_1.0.bin" "$HOME/.local/share/xemu/bios/mcpx_1.0.bin"; do
    if [ -f "$xemu_path" ]; then
        ok "mcpx_1.0.bin  ${DIM}(Xbox MCPX boot ROM)${NC}"
        XEMU_BIOS_FOUND=true
        FOUND=$((FOUND + 1))
        break
    fi
done
TOTAL=$((TOTAL + 1))
if [ "$XEMU_BIOS_FOUND" = false ]; then
    miss "mcpx_1.0.bin  ${RED}(REQUIRED — Xbox boot ROM)${NC}"
    MISSING=$((MISSING + 1))
    MISSING_LIST="${MISSING_LIST}\n  - mcpx_1.0.bin (Xbox, required)"
fi

XEMU_FLASH_FOUND=false
for xemu_path in "$BIOS_DIR/xbox/Complex_4627v1.03.bin" "$HOME/.local/share/xemu/bios/Complex_4627v1.03.bin"; do
    if [ -f "$xemu_path" ]; then
        ok "Complex_4627v1.03.bin  ${DIM}(Xbox flash BIOS)${NC}"
        XEMU_FLASH_FOUND=true
        FOUND=$((FOUND + 1))
        break
    fi
done
TOTAL=$((TOTAL + 1))
if [ "$XEMU_FLASH_FOUND" = false ]; then
    miss "Complex_4627v1.03.bin  ${RED}(REQUIRED — Xbox flash BIOS)${NC}"
    MISSING=$((MISSING + 1))
    MISSING_LIST="${MISSING_LIST}\n  - Complex_4627v1.03.bin (Xbox, required)"
fi
echo ""

# ============================================================================
# Summary
# ============================================================================
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

if [ "$MISSING" -eq 0 ]; then
    echo -e "  ${GREEN}${BOLD}All $FOUND BIOS files present. You're good to go!${NC}"
else
    echo -e "  ${BOLD}Found:${NC}   $FOUND / $TOTAL"
    echo -e "  ${BOLD}Missing:${NC} $MISSING"
    echo ""
    echo -e "  ${BOLD}${RED}Missing files:${NC}"
    echo -e "$MISSING_LIST"
    echo ""
    echo -e "  ${BOLD}Where to put them:${NC}"
    echo -e "  Copy BIOS files to: ${CYAN}$BIOS_DIR/${NC}"
    echo "  Each system has its own subfolder (dc/, kronos/, xbox/, etc.)"
    echo ""
    echo -e "  ${BOLD}How to get BIOS files:${NC}"
    echo "  BIOS files are copyrighted firmware dumped from real hardware."
    echo "  You can dump them from consoles you own, or search online."
    echo "  Look for the exact filenames listed above."
    echo ""
    echo -e "  ${BOLD}Quick transfer from your PC:${NC}"
    echo "  scp your_bios_file.bin $(whoami)@$(hostname -I 2>/dev/null | awk '{print $1}'):$BIOS_DIR/"
fi

echo ""
