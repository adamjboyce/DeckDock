#!/usr/bin/env python3
"""
Fetch box art for non-Steam shortcuts from libretro-thumbnails.
Places images in Steam's grid folder and EmuDeck's downloaded_media.

Run after add-roms-to-steam.py to populate artwork.
Skips games that already have art. Safe to re-run.
"""
import os
import struct
import urllib.request
import urllib.parse

STEAM_USERDATA = os.path.expanduser("~/.local/share/Steam/userdata")
EMUDECK_MEDIA = os.path.expanduser("~/Emulation/tools/downloaded_media")

# Map ROM system dirs to libretro-thumbnails system names
LIBRETRO_SYSTEM = {
    "psx": "Sony - PlayStation",
    "ps2": "Sony - PlayStation 2",
    "psp": "Sony - PlayStation Portable",
    "3ds": "Nintendo - Nintendo 3DS",
    "n3ds": "Nintendo - Nintendo 3DS",
    "nds": "Nintendo - Nintendo DS",
    "n64": "Nintendo - Nintendo 64",
    "snes": "Nintendo - Super Nintendo Entertainment System",
    "sfc": "Nintendo - Super Nintendo Entertainment System",
    "nes": "Nintendo - Nintendo Entertainment System",
    "gb": "Nintendo - Game Boy",
    "gbc": "Nintendo - Game Boy Color",
    "gba": "Nintendo - Game Boy Advance",
    "gamecube": "Nintendo - GameCube",
    "gc": "Nintendo - GameCube",
    "wii": "Nintendo - Wii",
    "genesis": "Sega - Mega Drive - Genesis",
    "megadrive": "Sega - Mega Drive - Genesis",
    "saturn": "Sega - Saturn",
    "dreamcast": "Sega - Dreamcast",
    "sega32x": "Sega - 32X",
    "segacd": "Sega - Mega-CD - Sega CD",
    "mastersystem": "Sega - Master System - Mark III",
    "gamegear": "Sega - Game Gear",
    "atari2600": "Atari - 2600",
    "atari5200": "Atari - 5200",
    "atari7800": "Atari - 7800",
    "pcengine": "NEC - PC Engine - TurboGrafx 16",
    "mame": "MAME",
    "xbox": "Microsoft - Xbox",
    "scummvm": "ScummVM",
}

THUMB_BASE = "https://thumbnails.libretro.com"


def find_steam_grid_dir():
    """Find the Steam grid directory for the first user."""
    if not os.path.isdir(STEAM_USERDATA):
        return None
    users = [d for d in os.listdir(STEAM_USERDATA) if d.isdigit()]
    if not users:
        return None
    grid = os.path.join(STEAM_USERDATA, users[0], "config", "grid")
    os.makedirs(grid, exist_ok=True)
    return grid


def find_vdf_path():
    """Find shortcuts.vdf for the first Steam user."""
    if not os.path.isdir(STEAM_USERDATA):
        return None
    users = [d for d in os.listdir(STEAM_USERDATA) if d.isdigit()]
    if not users:
        return None
    return os.path.join(STEAM_USERDATA, users[0], "config", "shortcuts.vdf")


def fetch_image(system, game_name):
    """Download boxart PNG from libretro-thumbnails. Returns bytes or None."""
    libretro_sys = LIBRETRO_SYSTEM.get(system)
    if not libretro_sys:
        return None

    name_variants = [game_name]
    if "(Disc" in game_name:
        base = game_name.split("(Disc")[0].strip()
        name_variants.extend([base + "(Disc 1)", base])

    for name in name_variants:
        safe_name = name.replace("&", "_").replace("/", "_")
        encoded = urllib.parse.quote(safe_name, safe="")
        sys_encoded = urllib.parse.quote(libretro_sys, safe="")
        url = f"{THUMB_BASE}/{sys_encoded}/Named_Boxarts/{encoded}.png"

        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            resp = urllib.request.urlopen(req, timeout=10)
            data = resp.read()
            if len(data) >= 1000:
                return data
        except Exception:
            continue
    return None


def parse_entries(vdf_path):
    """Extract game entries from shortcuts.vdf."""
    with open(vdf_path, "rb") as f:
        data = f.read()

    entries = []
    i = 0
    while i < len(data):
        marker = b"\x02appid\x00"
        pos = data.find(marker, i)
        if pos < 0:
            break
        appid_bytes = data[pos + len(marker):pos + len(marker) + 4]
        appid_val = struct.unpack("<I", appid_bytes)[0]

        nm = b"\x01appname\x00"
        np_ = data.find(nm, pos)
        if np_ < 0:
            break
        ns = np_ + len(nm)
        ne = data.find(b"\x00", ns)
        appname = data[ns:ne].decode("utf-8", errors="replace")

        lo = b"\x01LaunchOptions\x00"
        lp = data.find(lo, ne)
        system = ""
        if lp >= 0:
            ls = lp + len(lo)
            le = data.find(b"\x00", ls)
            launch = data[ls:le].decode("utf-8", errors="replace")
            if "/roms/" in launch:
                system = launch.split("/roms/")[1].split("/")[0]

        entries.append({"appid": appid_val, "name": appname, "system": system})
        i = ne + 1

    return entries


def main():
    grid_dir = find_steam_grid_dir()
    vdf_path = find_vdf_path()

    if not vdf_path or not os.path.exists(vdf_path):
        print("No shortcuts.vdf found. Run add-roms-to-steam.py first.")
        return
    if not grid_dir:
        print("No Steam grid directory found.")
        return

    entries = parse_entries(vdf_path)
    fetched = 0
    skipped = 0

    for e in entries:
        if e["name"] == "EmulationStationDE" or not e["system"]:
            continue

        # Check if Steam grid art already exists
        grid_path = os.path.join(grid_dir, f'{e["appid"]}p.png')
        if os.path.exists(grid_path):
            skipped += 1
            continue

        print(f"Fetching: {e['name']} [{e['system']}]...")
        img_data = fetch_image(e["system"], e["name"])

        if not img_data:
            print(f"  MISS: no art found")
            continue

        # Save to Steam grid folder
        with open(grid_path, "wb") as f:
            f.write(img_data)
        print(f"  Steam grid: {e['appid']}p.png ({len(img_data)} bytes)")

        # Also save to EmuDeck downloaded_media for ES-DE
        media_dir = os.path.join(EMUDECK_MEDIA, e["system"], "covers")
        os.makedirs(media_dir, exist_ok=True)
        media_path = os.path.join(media_dir, f"{e['name']}.png")
        if not os.path.exists(media_path):
            with open(media_path, "wb") as f:
                f.write(img_data)
            print(f"  EmuDeck media: {e['name']}.png")

        fetched += 1

    total = len([e for e in entries if e["name"] != "EmulationStationDE" and e["system"]])
    print(f"\nDone: {fetched} fetched, {skipped} already had art, {total} total games")


if __name__ == "__main__":
    main()
