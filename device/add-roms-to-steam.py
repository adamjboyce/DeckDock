#!/usr/bin/env python3
"""
Add ROM games to Steam as non-Steam shortcuts.
Regenerates shortcuts.vdf from scratch each run.
Launches emulators directly (flatpak/AppImage) — bypasses EmuDeck launcher
wrappers which run git pull + cloud sync on every launch, causing hangs.
"""
import os, struct, re, shutil, binascii, glob, subprocess

STEAM_USERDATA = os.path.expanduser("~/.local/share/Steam/userdata")
ROMS_DIR = os.path.expanduser("~/Emulation/roms")
NAS_MOUNT = "/tmp/nas-roms"
APPS_DIR = os.path.expanduser("~/Applications")
FLATPAK = "/usr/bin/flatpak"
APPIMAGE_LAUNCHER = os.path.expanduser("~/Emulation/tools/launch-appimage.sh")

# Windows games via Proton (RPG Maker fan games, GameMaker projects, etc.)
# Shortcuts launch through launch-proton-game.sh, which sets up Proton's
# environment and exec's the Windows EXE. We deliberately do NOT write Steam's
# CompatToolMapping — programmatic mapping triggers a stalled "DownloadingDepots"
# launch flow for non-Steam game appids that have no depot.
WINDOWS_GAMES_DIR = os.path.expanduser(
    os.environ.get("WINDOWS_GAMES_DIR", "~/Games")
)
PROTON_LAUNCHER = os.path.expanduser("~/Emulation/tools/launch-proton-game.sh")
# Steam Input controller config for RPG Maker / keyboard-driven games.
# Copied to ~/.local/share/Steam/steamapps/common/Steam Controller Configs/
#   <userid>/config/<appid>/controller_switch_pro.vdf for each Windows game.
# Steam auto-loads this on launch — gamepad → keyboard (arrows/Z/X/Enter/Esc/Shift)
RPGMAKER_CONTROLLER_CONFIG = os.path.expanduser(
    "~/Emulation/tools/controller_switch_pro_rpgmaker.vdf"
)
STEAM_CONTROLLER_CONFIGS_BASE = os.path.expanduser(
    "~/.local/share/Steam/steamapps/common/Steam Controller Configs"
)

# Flatpak emulators: exe = /usr/bin/flatpak, launch_opts = "run <id> <rom>"
_FP_RETROARCH = "org.libretro.RetroArch"
_FP_AZAHAR = "org.azahar_emu.Azahar"
_FP_DOLPHIN = "org.DolphinEmu.dolphin-emu"
_FP_MELONDS = "net.kuribo64.melonDS"
_FP_PCSX2 = "net.pcsx2.PCSX2"
_FP_PPSSPP = "org.ppsspp.PPSSPP"
_FP_XEMU = "app.xemu.xemu"
_FP_SCUMMVM = "org.scummvm.ScummVM"

# Map systems to (exe_path, flatpak_id_or_none).
# - Flatpak: exe = /usr/bin/flatpak, id is set
# - AppImage: exe = AppImage path, id is None
def _find_appimage(name):
    """Find an AppImage by name prefix in ~/Applications/."""
    matches = glob.glob(os.path.join(APPS_DIR, name + "*.AppImage"))
    return matches[0] if matches else None

SYSTEM_EMULATOR = {
    "3ds": "deckdock:azahar", "n3ds": "deckdock:azahar",
    "dreamcast": _FP_RETROARCH,
    "gamecube": _FP_DOLPHIN, "gc": _FP_DOLPHIN,
    "gb": _FP_RETROARCH, "gba": _FP_RETROARCH, "gbc": _FP_RETROARCH,
    "genesis": _FP_RETROARCH, "megadrive": _FP_RETROARCH,
    "n64": _FP_RETROARCH,
    "nds": _FP_MELONDS,
    "nes": _FP_RETROARCH, "famicom": _FP_RETROARCH,
    "ps2": "appimage:pcsx2-Qt",
    "psp": _FP_PPSSPP,
    "psx": "appimage:DuckStation",  # AppImage only, no flatpak
    "saturn": _FP_RETROARCH,
    "snes": _FP_RETROARCH, "sfc": _FP_RETROARCH,
    "wii": _FP_DOLPHIN,
    "atari2600": _FP_RETROARCH, "atari5200": _FP_RETROARCH,
    "atari7800": _FP_RETROARCH,
    "segacd": _FP_RETROARCH, "megacd": _FP_RETROARCH,
    "sega32x": _FP_RETROARCH,
    "pcengine": _FP_RETROARCH,
    "mastersystem": _FP_RETROARCH, "gamegear": _FP_RETROARCH,
    "mame": _FP_RETROARCH,
    "atarijaguar": _FP_RETROARCH,
    "lynx": _FP_RETROARCH,
    "ngp": _FP_RETROARCH,
    "wonderswan": _FP_RETROARCH,
    "wonderswancolor": _FP_RETROARCH,
    "colecovision": _FP_RETROARCH,
    "vectrex": _FP_RETROARCH,
    "3do": _FP_RETROARCH,
    "xbox": _FP_XEMU,
    "scummvm": _FP_SCUMMVM,
}

ROM_EXTENSIONS = {
    ".chd", ".iso", ".bin", ".cue", ".gdi",
    ".nes", ".smc", ".sfc", ".z64", ".n64", ".v64",
    ".gb", ".gbc", ".gba", ".nds", ".3ds", ".cia", ".cci",
    ".md", ".gen", ".sms", ".gg",
    ".pbp", ".cso",
    ".zip", ".7z",
    ".rvz", ".gcz", ".wbfs",
    ".m3u",
}

SKIP_IF_BETTER = {".bin": [".cue", ".chd"], ".iso": [".chd"]}

# Disc file patterns — skip individual discs when an .m3u playlist exists
DISC_PATTERN = re.compile(r"^(.+?)\s*\(Disc\s*\d+\)", re.IGNORECASE)

SYSTEM_LABELS = {
    "psx": "PlayStation", "ps2": "PlayStation 2", "psp": "PSP",
    "nes": "NES", "snes": "SNES", "sfc": "SNES", "n64": "Nintendo 64",
    "gb": "Game Boy", "gbc": "Game Boy Color", "gba": "Game Boy Advance",
    "nds": "Nintendo DS", "3ds": "Nintendo 3DS", "n3ds": "Nintendo 3DS",
    "gc": "GameCube", "gamecube": "GameCube", "wii": "Wii",
    "genesis": "Genesis", "megadrive": "Genesis",
    "saturn": "Saturn", "dreamcast": "Dreamcast",
    "atari2600": "Atari 2600", "segacd": "Sega CD",
    "mastersystem": "Master System", "gamegear": "Game Gear",
    "mame": "Arcade", "xbox": "Xbox", "scummvm": "ScummVM",
    "pcengine": "PC Engine",
    "atarijaguar": "Atari Jaguar",
    "lynx": "Atari Lynx",
    "ngp": "Neo Geo Pocket",
    "wonderswan": "WonderSwan",
    "wonderswancolor": "WonderSwan Color",
    "colecovision": "ColecoVision",
    "vectrex": "Vectrex",
    "3do": "3DO",
}


def _s(key, val):
    """Write a string field: \x01 key \x00 value \x00"""
    return b"\x01" + key.encode() + b"\x00" + val.encode() + b"\x00"


def _i(key, val):
    """Write an int32 field: \x02 key \x00 <4 bytes LE>"""
    return b"\x02" + key.encode() + b"\x00" + struct.pack("<I", val & 0xFFFFFFFF)


def appid(exe, name):
    """Match Steam's algorithm: CRC32 of unquoted exe + appname."""
    raw_exe = exe.strip('"')
    return (binascii.crc32((raw_exe + name).encode()) & 0xFFFFFFFF) | 0x80000000


def build_entry(idx, name, exe, startdir, icon="", launch_opts="",
                tags=None, last_play=0):
    """Build one shortcut entry matching Steam's exact binary format."""
    e = b"\x00" + str(idx).encode() + b"\x00"  # map start
    e += _i("appid", appid(exe, name))
    e += _s("appname", name)
    e += _s("exe", exe)
    e += _s("StartDir", startdir)
    e += _s("icon", icon)
    e += _s("ShortcutPath", "")
    e += _s("LaunchOptions", launch_opts)
    e += _i("IsHidden", 0)
    e += _i("AllowDesktopConfig", 1)
    e += _i("AllowOverlay", 1)
    e += _i("OpenVR", 0)
    e += _i("Devkit", 0)
    e += _s("DevkitGameID", "")
    e += _i("DevkitOverrideAppID", 0)
    e += _i("LastPlayTime", last_play)
    e += _s("FlatpakAppID", "")
    e += _s("sortas", "")
    e += b"\x00tags\x00"  # tags sub-map start
    for i, t in enumerate(tags or []):
        e += _s(str(i), t)
    e += b"\x08"  # end tags
    e += b"\x08"  # end entry
    return e


def clean_name(filename):
    name = os.path.splitext(filename)[0]
    name = re.sub(r"\s*\(Rev\s*\d*\)", "", name)
    return name.strip()


def install_rpgmaker_controller_config(steam_userid, appid_dec):
    """Drop the RPG Maker controller config at the per-appid path Steam
    auto-loads at game launch. Idempotent. Returns True if installed/refreshed.

    Steam loads <controller_type>.vdf from the per-appid dir matching the
    currently-active controller. We ship variants for the common handheld
    controllers (switch_pro for Steam Deck and other Switch-Pro-emulating
    devices; legion_go_s for the Legion Go). Source file declares
    controller_switch_pro; legion_go_s is derived by substitution.
    """
    if not os.path.exists(RPGMAKER_CONTROLLER_CONFIG):
        return False
    target_dir = os.path.join(
        STEAM_CONTROLLER_CONFIGS_BASE,
        str(steam_userid), "config", str(appid_dec)
    )
    try:
        os.makedirs(target_dir, exist_ok=True)
        # 1) switch_pro variant (source as-is)
        shutil.copy2(
            RPGMAKER_CONTROLLER_CONFIG,
            os.path.join(target_dir, "controller_switch_pro.vdf"),
        )
        # 2) legion_go_s variant (substitute controller_type)
        with open(RPGMAKER_CONTROLLER_CONFIG) as f:
            text = f.read()
        legion_text = text.replace(
            '"controller_type"\t\t"controller_switch_pro"',
            '"controller_type"\t\t"controller_legion_go_s"',
        )
        with open(os.path.join(target_dir, "controller_legion_go_s.vdf"), "w") as f:
            f.write(legion_text)
        return True
    except OSError:
        return False


def find_windows_games(games_dir):
    """Yield (display_name, exe_path, start_dir) for each Windows game folder.

    Each subdirectory under games_dir is treated as one game.
    Prefers Game.exe (RPG Maker XP/VX/MV/MZ convention); falls back to a single
    top-level .exe if exactly one is present (avoids picking up uninstallers,
    setup tools, or bundled utilities).
    """
    if not os.path.isdir(games_dir):
        return
    for entry in sorted(os.listdir(games_dir)):
        path = os.path.join(games_dir, entry)
        if not os.path.isdir(path):
            continue
        game_exe = os.path.join(path, "Game.exe")
        if os.path.isfile(game_exe):
            exe = game_exe
        else:
            top_exes = [
                f for f in os.listdir(path)
                if f.lower().endswith(".exe")
                and os.path.isfile(os.path.join(path, f))
            ]
            if len(top_exes) != 1:
                continue  # ambiguous or none — skip
            exe = os.path.join(path, top_exes[0])
        # Display name: folder name with trailing version suffix stripped
        name = re.sub(r"\s+\d+(\.\d+)+\s*$", "", entry).strip() or entry
        yield name, exe, path


def main():
    users = [d for d in os.listdir(STEAM_USERDATA) if d.isdigit()]
    if not users:
        print("No Steam user found!")
        return

    vdf_path = os.path.join(STEAM_USERDATA, users[0], "config", "shortcuts.vdf")

    # Backup existing file
    if os.path.exists(vdf_path):
        shutil.copy2(vdf_path, vdf_path + ".bak")

    # Build ALL entries from scratch
    entries = []
    idx = 0

    # Entry 0: EmulationStation DE (preserve the existing shortcut)
    esde_launcher = os.path.expanduser("~/Emulation/tools/launchers/es-de/es-de.sh")
    esde_icon = os.path.expanduser(
        "~/.config/EmuDeck/backend/icons/ico/EmulationStationDE.ico"
    )
    entries.append(build_entry(
        idx, "EmulationStationDE",
        '"' + esde_launcher + '"',
        '"' + os.path.expanduser("~/Applications/") + '"',
        icon='"' + esde_icon + '"',
        tags=["favorite"],
        last_play=0x699a1d7a,  # preserve original timestamp
    ))
    idx += 1

    # Entry 1: DeckDock Storage Manager
    storage_mgr = os.path.expanduser("~/Emulation/tools/deckdock-storage-manager.sh")
    if os.path.exists(storage_mgr):
        entries.append(build_entry(
            idx, "DeckDock Storage Manager",
            '"' + storage_mgr + '"',
            '"' + os.path.expanduser("~/Emulation/tools/") + '"',
            tags=["DeckDock"],
        ))
        idx += 1

    # Entry 2: Plex HTPC
    entries.append(build_entry(
        idx, "Plex HTPC",
        '"' + FLATPAK + '"',
        '"' + os.path.expanduser("~/") + '"',
        launch_opts="run tv.plex.PlexHTPC",
        tags=["Media"],
    ))
    idx += 1

    # Entry 3: Xbox Cloud Gaming (conditional — needs a Chromium browser)
    XBOX_BROWSERS = [
        "com.microsoft.Edge",
        "com.google.Chrome",
        "org.chromium.Chromium",
    ]
    xbox_browser = None
    for browser_id in XBOX_BROWSERS:
        if subprocess.run(["flatpak", "info", browser_id],
                          capture_output=True).returncode == 0:
            xbox_browser = browser_id
            break

    if xbox_browser:
        entries.append(build_entry(
            idx, "Xbox Cloud Gaming",
            '"' + FLATPAK + '"',
            '"' + os.path.expanduser("~/") + '"',
            launch_opts=f"run {xbox_browser} --kiosk --start-fullscreen --app=https://www.xbox.com/play",
            tags=["Media"],
        ))
        idx += 1

    # Entry 4: DeckDock Save Restore (conditional — needs script on device)
    save_restore = os.path.expanduser("~/Emulation/tools/save-restore.sh")
    if os.path.exists(save_restore):
        entries.append(build_entry(
            idx, "DeckDock Save Restore",
            '"' + save_restore + '"',
            '"' + os.path.expanduser("~/Emulation/tools/") + '"',
            tags=["DeckDock"],
        ))
        idx += 1

    # Scan ROM directories for games
    added = 0
    seen_names = {"EmulationStationDE", "DeckDock Storage Manager", "Plex HTPC",
                   "Xbox Cloud Gaming", "DeckDock Save Restore"}

    for system in sorted(os.listdir(ROMS_DIR)):
        sys_dir = os.path.join(ROMS_DIR, system)
        if not os.path.isdir(sys_dir) or system not in SYSTEM_EMULATOR:
            continue

        emu_ref = SYSTEM_EMULATOR[system]

        files = sorted(os.listdir(sys_dir))
        fileset = set(f.lower() for f in files)

        # Build set of game bases that have .m3u playlists
        m3u_bases = set()
        for f in files:
            if f.lower().endswith(".m3u"):
                m3u_bases.add(os.path.splitext(f)[0].lower())

        for f in files:
            ext = os.path.splitext(f)[1].lower()
            if ext not in ROM_EXTENSIONS:
                continue

            # Skip NAS symlinks — only locally-downloaded games get Steam shortcuts
            rom_path = os.path.join(sys_dir, f)
            if os.path.islink(rom_path) and os.path.realpath(rom_path).startswith(NAS_MOUNT):
                continue

            if ext in SKIP_IF_BETTER:
                base = os.path.splitext(f)[0]
                skip = False
                for better in SKIP_IF_BETTER[ext]:
                    if (base + better).lower() in fileset:
                        skip = True
                        break
                if skip:
                    continue

            # Skip individual disc files when an .m3u playlist exists
            if ext != ".m3u":
                disc_match = DISC_PATTERN.match(os.path.splitext(f)[0])
                if disc_match:
                    base_name = disc_match.group(1).strip()
                    if base_name.lower() in m3u_bases:
                        continue

            gamename = clean_name(f)
            if gamename in seen_names:
                continue

            # Resolve exe + launch options based on emulator type
            if emu_ref.startswith("deckdock:"):
                # DeckDock launcher script (handles zip extraction, etc.)
                launcher_name = emu_ref.split(":", 1)[1]
                launcher_path = os.path.expanduser(
                    f"~/Emulation/tools/launchers/deckdock-{launcher_name}.sh")
                exe = '"' + launcher_path + '"'
                launch_opts = '"' + rom_path + '"'
            elif emu_ref.startswith("appimage:"):
                appimage_name = emu_ref.split(":", 1)[1]
                appimage_path = _find_appimage(appimage_name)
                if not appimage_path:
                    print(f"  ! AppImage not found: {appimage_name}*.AppImage")
                    continue
                # Wrapper keeps bash as parent so Steam reaper tracks it
                exe = '"' + APPIMAGE_LAUNCHER + '"'
                launch_opts = '"' + appimage_path + '" "' + rom_path + '"'
            else:
                # Flatpak: exe is /usr/bin/flatpak, launch_opts = run <id> "<rom>"
                exe = '"' + FLATPAK + '"'
                launch_opts = 'run ' + emu_ref + ' "' + rom_path + '"'
            startdir = '"' + os.path.dirname(rom_path) + '/"'
            tag = SYSTEM_LABELS.get(system, system)

            entries.append(build_entry(
                idx, gamename, exe, startdir,
                launch_opts=launch_opts, tags=[tag]
            ))
            idx += 1
            added += 1
            seen_names.add(gamename)
            print(f"  + [{tag}] {gamename}")

    # Scan Windows games directory — each subdir = one Proton-launched game.
    # Shortcut points at launch-proton-game.sh, which sets up Proton env and
    # exec's the Windows EXE. No CompatToolMapping (programmatic mapping stalls
    # Steam's launch flow at "DownloadingDepots" for non-Steam appids).
    # Also drops the RPG Maker controller config per-appid so the gamepad
    # auto-maps to keyboard (arrows, Z, X, Enter, Esc, Shift) on launch.
    windows_added = 0
    controllers_installed = 0
    for win_name, win_exe, win_dir in find_windows_games(WINDOWS_GAMES_DIR):
        if win_name in seen_names:
            continue
        if not os.path.exists(PROTON_LAUNCHER):
            print(f"  ! Windows game found ({win_name}) but launcher missing at "
                  f"{PROTON_LAUNCHER} — skipping. Run setup.sh or deploy.sh to install.")
            continue
        quoted_exe = '"' + PROTON_LAUNCHER + '"'  # the wrapper IS the exe
        quoted_dir = '"' + win_dir + '/"'
        quoted_args = '"' + win_exe + '"'         # Windows EXE as launch arg
        win_appid = appid(quoted_exe, win_name)
        entries.append(build_entry(
            idx, win_name, quoted_exe, quoted_dir,
            launch_opts=quoted_args, tags=["Windows"],
        ))
        idx += 1
        windows_added += 1
        seen_names.add(win_name)
        # Drop the RPG Maker controller config per-appid (Steam auto-loads it)
        if install_rpgmaker_controller_config(users[0], win_appid):
            controllers_installed += 1
        print(f"  + [Windows] {win_name}")

    # Write complete shortcuts.vdf
    with open(vdf_path, "wb") as f:
        f.write(b"\x00shortcuts\x00")  # file header
        for entry in entries:
            f.write(entry)
        f.write(b"\x08\x08")  # end shortcuts map + end file

    size = os.path.getsize(vdf_path)
    print(f"\nWrote {vdf_path} ({size} bytes)")
    pinned = len(entries) - added - windows_added
    print(f"Total: {len(entries)} shortcuts "
          f"({added} ROMs + {windows_added} Windows + {pinned} pinned)")
    if windows_added:
        print(f"Controller configs installed for {controllers_installed}/{windows_added} Windows games "
              f"(gamepad → keyboard auto-mapping)")


if __name__ == "__main__":
    main()
