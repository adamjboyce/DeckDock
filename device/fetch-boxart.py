#!/usr/bin/env python3
"""
Fetch box art for games from libretro-thumbnails.

Pass 1 — ROM directories: ES-DE covers for ALL games (local + NAS symlinks).
Pass 2 — shortcuts.vdf: Steam grid art for games with Steam shortcuts.

Skips games that already have art. Safe to re-run.
"""
import io
import os
import re
import struct
import urllib.request
import urllib.parse

STEAM_USERDATA = os.path.expanduser("~/.local/share/Steam/userdata")
EMUDECK_MEDIA = os.path.expanduser("~/Emulation/tools/downloaded_media")
ROMS_DIR = os.path.expanduser("~/Emulation/roms")

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
DISC_PATTERN = re.compile(r"^(.+?)\s*\(Disc\s*\d+\)", re.IGNORECASE)

STEAM_GRID_SIZE = (600, 900)   # Steam portrait grid dimensions
STEAM_BANNER_SIZE = (920, 430) # Steam landscape banner dimensions
COVER_SIZE = (512, 512)       # ES-DE cover placeholder size

# Short labels for placeholder art
SYSTEM_LABEL = {
    "psx": "PlayStation", "ps2": "PlayStation 2", "psp": "PSP",
    "3ds": "Nintendo 3DS", "n3ds": "Nintendo 3DS",
    "nds": "Nintendo DS", "n64": "Nintendo 64",
    "snes": "SNES", "sfc": "SNES",
    "nes": "NES", "gb": "Game Boy", "gbc": "Game Boy Color",
    "gba": "Game Boy Advance", "gamecube": "GameCube", "gc": "GameCube",
    "wii": "Wii", "genesis": "Genesis", "megadrive": "Genesis",
    "saturn": "Saturn", "dreamcast": "Dreamcast",
    "sega32x": "Sega 32X", "segacd": "Sega CD",
    "mastersystem": "Master System", "gamegear": "Game Gear",
    "atari2600": "Atari 2600", "atari5200": "Atari 5200",
    "atari7800": "Atari 7800", "pcengine": "PC Engine",
    "mame": "Arcade", "xbox": "Xbox", "scummvm": "ScummVM",
}

THUMB_BASE = "https://thumbnails.libretro.com"

# ROM title prefixes that differ from libretro-thumbnails naming.
# Maps the No-Intro title (before region tags) to the libretro equivalent.
# Add entries here when a game misses due to naming convention mismatch.
NAME_FIXES = {
    "Metal Gear Solid - Snake Eater 3D": "Metal Gear Solid 3D - Snake Eater",
}


def clean_name(filename):
    """Strip file extension and (Rev N) from ROM filename."""
    name = os.path.splitext(filename)[0]
    name = re.sub(r"\s*\(Rev\s*\d*\)", "", name)
    return name.strip()


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


def apply_name_fix(name):
    """If a ROM title prefix has a known libretro mismatch, return the corrected
    full name (with region tags preserved). Returns None if no fix applies."""
    for wrong, right in NAME_FIXES.items():
        if name.startswith(wrong):
            return right + name[len(wrong):]
    return None


def pad_to_steam_grid(img_data):
    """Fit boxart onto a 600x900 black canvas for Steam's portrait grid.
    Returns PNG bytes. Falls back to original data if Pillow is unavailable."""
    try:
        from PIL import Image
    except ImportError:
        return img_data

    src = Image.open(io.BytesIO(img_data)).convert("RGBA")
    tw, th = STEAM_GRID_SIZE

    # Scale to fit within target while preserving aspect ratio
    scale = min(tw / src.width, th / src.height)
    new_w = int(src.width * scale)
    new_h = int(src.height * scale)
    resized = src.resize((new_w, new_h), Image.LANCZOS)

    canvas = Image.new("RGBA", (tw, th), (0, 0, 0, 255))
    x = (tw - new_w) // 2
    y = (th - new_h) // 2
    canvas.paste(resized, (x, y), resized)

    buf = io.BytesIO()
    canvas.save(buf, format="PNG")
    return buf.getvalue()


def pad_to_steam_banner(img_data):
    """Fit boxart onto a 920x430 black canvas for Steam's landscape banner.
    Returns PNG bytes. Falls back to original data if Pillow is unavailable."""
    try:
        from PIL import Image
    except ImportError:
        return img_data

    src = Image.open(io.BytesIO(img_data)).convert("RGBA")
    tw, th = STEAM_BANNER_SIZE

    scale = min(tw / src.width, th / src.height)
    new_w = int(src.width * scale)
    new_h = int(src.height * scale)
    resized = src.resize((new_w, new_h), Image.LANCZOS)

    canvas = Image.new("RGBA", (tw, th), (0, 0, 0, 255))
    x = (tw - new_w) // 2
    y = (th - new_h) // 2
    canvas.paste(resized, (x, y), resized)

    buf = io.BytesIO()
    canvas.save(buf, format="PNG")
    return buf.getvalue()


def generate_placeholder(game_name, system, size=None):
    """Generate a placeholder cover with game name and system label.
    Returns PNG bytes, or None if Pillow is unavailable."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return None

    w, h = size or COVER_SIZE
    bg = (30, 30, 30, 255)
    canvas = Image.new("RGBA", (w, h), bg)
    draw = ImageDraw.Draw(canvas)

    label = SYSTEM_LABEL.get(system, system)

    # Strip region/language tags for cleaner display
    title = re.sub(r"\s*\([^)]*\)", "", game_name).strip()

    # Try to load a decent font, fall back to default
    font_title = ImageFont.load_default(size=28)
    font_label = ImageFont.load_default(size=18)

    # System label near top
    lbox = draw.textbbox((0, 0), label, font=font_label)
    lw = lbox[2] - lbox[0]
    draw.text(((w - lw) // 2, 40), label, fill=(120, 120, 120), font=font_label)

    # Game title — word-wrap centered
    words = title.split()
    lines = []
    line = ""
    for word in words:
        test = f"{line} {word}".strip()
        tbox = draw.textbbox((0, 0), test, font=font_title)
        if tbox[2] - tbox[0] > w - 60:
            if line:
                lines.append(line)
            line = word
        else:
            line = test
    if line:
        lines.append(line)

    total_h = len(lines) * 38
    y = (h - total_h) // 2
    for ln in lines:
        tbox = draw.textbbox((0, 0), ln, font=font_title)
        tw = tbox[2] - tbox[0]
        draw.text(((w - tw) // 2, y), ln, fill=(200, 200, 200), font=font_title)
        y += 38

    buf = io.BytesIO()
    canvas.save(buf, format="PNG")
    return buf.getvalue()


def fetch_image(system, game_name):
    """Download boxart PNG from libretro-thumbnails. Returns bytes or None."""
    libretro_sys = LIBRETRO_SYSTEM.get(system)
    if not libretro_sys:
        return None

    name_variants = [game_name]
    if "(Disc" in game_name:
        base = game_name.split("(Disc")[0].strip()
        name_variants.extend([base + "(Disc 1)", base])

    # Try known name corrections as fallback variants
    fixed = apply_name_fix(game_name)
    if fixed and fixed not in name_variants:
        name_variants.append(fixed)

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


def scan_rom_directories():
    """Pass 1: Fetch ES-DE covers for all games in ROM directories."""
    if not os.path.isdir(ROMS_DIR):
        print("ROM directory not found, skipping Pass 1.")
        return 0, 0, 0, {}

    fetched = 0
    skipped = 0
    missed = 0
    # Cache: (libretro_system_name, game_name) -> bytes|None
    cache = {}

    for system in sorted(os.listdir(ROMS_DIR)):
        sys_dir = os.path.join(ROMS_DIR, system)
        if not os.path.isdir(sys_dir) or system not in LIBRETRO_SYSTEM:
            continue

        files = sorted(os.listdir(sys_dir))
        fileset = set(f.lower() for f in files)

        # Game bases that have .m3u playlists — skip individual discs
        m3u_bases = set()
        for f in files:
            if f.lower().endswith(".m3u"):
                m3u_bases.add(os.path.splitext(f)[0].lower())

        for f in files:
            ext = os.path.splitext(f)[1].lower()
            if ext not in ROM_EXTENSIONS:
                continue

            # Skip inferior formats when better exists
            if ext in SKIP_IF_BETTER:
                base = os.path.splitext(f)[0]
                skip = False
                for better in SKIP_IF_BETTER[ext]:
                    if (base + better).lower() in fileset:
                        skip = True
                        break
                if skip:
                    continue

            # Skip individual disc files when .m3u playlist exists
            if ext != ".m3u":
                disc_match = DISC_PATTERN.match(os.path.splitext(f)[0])
                if disc_match:
                    base_name = disc_match.group(1).strip()
                    if base_name.lower() in m3u_bases:
                        continue

            # ES-DE matches covers by exact ROM filename (minus extension)
            rom_name = os.path.splitext(f)[0]
            # Libretro lookup uses cleaned name (strips Rev N, etc.)
            lookup_name = clean_name(f)

            # Check if cover already exists
            media_dir = os.path.join(EMUDECK_MEDIA, system, "covers")
            media_path = os.path.join(media_dir, f"{rom_name}.png")
            if os.path.exists(media_path):
                skipped += 1
                continue

            # Check cache — avoids re-downloading for n3ds/3ds aliases
            libretro_sys = LIBRETRO_SYSTEM[system]
            cache_key = (libretro_sys, lookup_name)

            if cache_key in cache:
                img_data = cache[cache_key]
            else:
                print(f"  Fetching: {lookup_name} [{system}]...")
                img_data = fetch_image(system, lookup_name)
                cache[cache_key] = img_data

            if not img_data:
                img_data = generate_placeholder(lookup_name, system)
                if img_data:
                    print(f"    MISS: generated placeholder")
                else:
                    missed += 1
                    print(f"    MISS: no art found (Pillow unavailable)")
                    continue

            os.makedirs(media_dir, exist_ok=True)
            with open(media_path, "wb") as fh:
                fh.write(img_data)
            print(f"    ES-DE: {system}/covers/{rom_name}.png ({len(img_data)} bytes)")
            fetched += 1

    return fetched, skipped, missed, cache


def process_steam_shortcuts(grid_dir, vdf_path, image_cache):
    """Pass 2: Add Steam grid art for games in shortcuts.vdf."""
    entries = parse_entries(vdf_path)
    fetched = 0
    skipped = 0

    for e in entries:
        if e["name"] == "EmulationStationDE" or not e["system"]:
            continue

        grid_path = os.path.join(grid_dir, f'{e["appid"]}p.png')
        banner_path = os.path.join(grid_dir, f'{e["appid"]}.png')
        if os.path.exists(grid_path) and os.path.exists(banner_path):
            skipped += 1
            continue

        img_data = None

        # Check in-memory cache from Pass 1
        libretro_sys = LIBRETRO_SYSTEM.get(e["system"])
        if libretro_sys:
            cache_key = (libretro_sys, e["name"])
            img_data = image_cache.get(cache_key)

        # Try reading from ES-DE media dir (may have been saved in Pass 1)
        if not img_data:
            media_path = os.path.join(
                EMUDECK_MEDIA, e["system"], "covers", f"{e['name']}.png"
            )
            if os.path.exists(media_path):
                with open(media_path, "rb") as fh:
                    img_data = fh.read()

        # Fetch from remote as last resort
        if not img_data:
            print(f"  Fetching: {e['name']} [{e['system']}]...")
            img_data = fetch_image(e["system"], e["name"])

        if not img_data:
            img_data = generate_placeholder(
                e["name"], e["system"], size=STEAM_GRID_SIZE
            )
            if not img_data:
                print(f"    MISS: no art found (Pillow unavailable)")
                continue
            print(f"    MISS: generated placeholder")

        # Portrait grid (600x900) — library view
        if not os.path.exists(grid_path):
            grid_data = pad_to_steam_grid(img_data)
            with open(grid_path, "wb") as fh:
                fh.write(grid_data)
            print(f"    Steam grid: {e['appid']}p.png ({len(grid_data)} bytes)")

        # Landscape banner (920x430) — search/recent view
        if not os.path.exists(banner_path):
            banner_data = pad_to_steam_banner(img_data)
            with open(banner_path, "wb") as fh:
                fh.write(banner_data)
            print(f"    Steam banner: {e['appid']}.png ({len(banner_data)} bytes)")

        # Also save to ES-DE media if not already there
        media_dir = os.path.join(EMUDECK_MEDIA, e["system"], "covers")
        media_path = os.path.join(media_dir, f"{e['name']}.png")
        if not os.path.exists(media_path):
            os.makedirs(media_dir, exist_ok=True)
            with open(media_path, "wb") as fh:
                fh.write(img_data)
            print(f"    ES-DE: {e['name']}.png")

        fetched += 1

    return fetched, skipped


def main():
    # Pass 1: ES-DE covers for ALL games (local + NAS symlinks)
    print("=== Pass 1: ES-DE covers (all ROM directories) ===")
    esde_fetched, esde_skipped, esde_missed, cache = scan_rom_directories()
    print(
        f"\nES-DE: {esde_fetched} fetched, {esde_skipped} already had art, "
        f"{esde_missed} not found"
    )

    # Pass 2: Steam grid art for shortcut games
    grid_dir = find_steam_grid_dir()
    vdf_path = find_vdf_path()

    if vdf_path and os.path.exists(vdf_path) and grid_dir:
        print("\n=== Pass 2: Steam grid art (shortcuts.vdf) ===")
        steam_fetched, steam_skipped = process_steam_shortcuts(
            grid_dir, vdf_path, cache
        )
        print(
            f"\nSteam: {steam_fetched} fetched, {steam_skipped} already had art"
        )
    else:
        print("\nNo shortcuts.vdf found — skipping Steam grid art.")

    print("\nDone.")


if __name__ == "__main__":
    main()
