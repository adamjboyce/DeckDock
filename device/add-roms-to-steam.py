#!/usr/bin/env python3
"""
Add ROM games to Steam as non-Steam shortcuts.
Regenerates shortcuts.vdf from scratch each run.
Uses EmuDeck launcher scripts. Run with Steam CLOSED.
"""
import os, struct, re, shutil, binascii

STEAM_USERDATA = os.path.expanduser("~/.local/share/Steam/userdata")
ROMS_DIR = os.path.expanduser("~/Emulation/roms")
LAUNCHERS = os.path.expanduser("~/Emulation/tools/launchers")

SYSTEM_LAUNCHER = {
    "3ds": "azahar.sh", "n3ds": "azahar.sh",
    "dreamcast": "retroarch.sh",
    "gamecube": "dolphin-emu.sh", "gc": "dolphin-emu.sh",
    "gb": "retroarch.sh", "gba": "retroarch.sh", "gbc": "retroarch.sh",
    "genesis": "retroarch.sh", "megadrive": "retroarch.sh",
    "n64": "retroarch.sh",
    "nds": "melonds.sh",
    "nes": "retroarch.sh", "famicom": "retroarch.sh",
    "ps2": "pcsx2-qt.sh",
    "psp": "ppsspp.sh",
    "psx": "duckstation.sh",
    "saturn": "retroarch.sh",
    "snes": "retroarch.sh", "sfc": "retroarch.sh",
    "wii": "dolphin-emu.sh",
    "atari2600": "retroarch.sh",
    "atari5200": "retroarch.sh",
    "atari7800": "retroarch.sh",
    "segacd": "retroarch.sh", "megacd": "retroarch.sh",
    "sega32x": "retroarch.sh",
    "pcengine": "retroarch.sh",
    "mastersystem": "retroarch.sh",
    "gamegear": "retroarch.sh",
    "mame": "retroarch.sh",
    "xbox": "xemu-emu.sh",
    "scummvm": "scummvm.sh",
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

    # Scan ROM directories for games
    added = 0
    seen_names = {"EmulationStationDE"}

    for system in sorted(os.listdir(ROMS_DIR)):
        sys_dir = os.path.join(ROMS_DIR, system)
        if not os.path.isdir(sys_dir) or system not in SYSTEM_LAUNCHER:
            continue

        launcher = os.path.join(LAUNCHERS, SYSTEM_LAUNCHER[system])
        if not os.path.exists(launcher):
            continue

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

            rom_path = os.path.join(sys_dir, f)
            exe = '"' + launcher + '"'
            startdir = '"' + os.path.dirname(launcher) + '/"'
            launch_opts = '"' + rom_path + '"'
            tag = SYSTEM_LABELS.get(system, system)

            entries.append(build_entry(
                idx, gamename, exe, startdir,
                launch_opts=launch_opts, tags=[tag]
            ))
            idx += 1
            added += 1
            seen_names.add(gamename)
            print(f"  + [{tag}] {gamename}")

    # Write complete shortcuts.vdf
    with open(vdf_path, "wb") as f:
        f.write(b"\x00shortcuts\x00")  # file header
        for entry in entries:
            f.write(entry)
        f.write(b"\x08\x08")  # end shortcuts map + end file

    size = os.path.getsize(vdf_path)
    print(f"\nWrote {vdf_path} ({size} bytes)")
    print(f"Total: {len(entries)} shortcuts ({added} games + 1 ES-DE)")


if __name__ == "__main__":
    main()
