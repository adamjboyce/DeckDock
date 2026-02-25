#!/usr/bin/env python3
"""
DeckDock Crawler GUI — Browser-based interface for the site crawler.
Enter a URL, watch it crawl and download in real time.

Configuration is read from ../config.env (relative to this script)
or from the path specified in the DECKDOCK_CONFIG environment variable.
"""

import hashlib
import http.server
import json
import lzma
import os
import re
import struct
import subprocess
import sys
import threading
import time
import urllib.parse
import zipfile
import zlib
from pathlib import Path

import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
from bs4 import BeautifulSoup

# 7z archive peeking
import py7zr
import shutil
import tempfile

# RAR archive peeking (uses 7z as backend since unrar isn't available)
import rarfile
rarfile.UNRAR_TOOL = "/usr/bin/7z"
rarfile.ALT_TOOL = "/usr/bin/7z"


# ============================================================================
# CONFIGURATION LOADER
# ============================================================================

def load_config():
    """Load configuration from config.env file.

    Searches for config in this order:
    1. Path in $DECKDOCK_CONFIG environment variable
    2. ../config.env relative to this script's location
    3. ./config.env relative to the current working directory

    Returns a dict of KEY=VALUE pairs. Lines starting with # are ignored.
    Values referencing $HOME or ~ are expanded.
    """
    config = {}

    # Determine config file path
    config_path = os.environ.get("DECKDOCK_CONFIG")
    if not config_path:
        script_dir = Path(__file__).resolve().parent
        config_path = script_dir.parent / "config.env"
        if not config_path.exists():
            config_path = Path.cwd() / "config.env"

    config_path = Path(config_path)
    if not config_path.exists():
        print(f"WARNING: Config file not found at {config_path}")
        print("Copy config.example.env to config.env and fill in your values.")
        print("Falling back to defaults.")
        return config

    with open(config_path, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            # Strip surrounding quotes if present
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]
            # Expand $HOME and ~ references
            value = value.replace("$HOME", os.path.expanduser("~"))
            value = os.path.expanduser(value)
            config[key] = value

    return config


# Load config at module level
_CONFIG = load_config()


def cfg(key, default=None):
    """Get a config value with an optional default."""
    return _CONFIG.get(key, default)


# ============================================================================
# DERIVED CONFIGURATION
# ============================================================================

# Compression tools
CHDMAN_BIN = os.path.expanduser("~/.local/bin/chdman-wrapper")

# Extensions that are already in optimal format — never reprocess
OPTIMAL_EXTENSIONS = {".chd", ".rvz", ".cso", ".pbp"}

# Disc image extensions — convert to CHD when found inside archives
DISC_IMAGE_EXTENSIONS = {
    ".bin", ".cue", ".iso", ".gdi", ".img", ".mdf", ".mds",
    ".cdi", ".nrg", ".ccd", ".sub",
}

# Cartridge/ROM extensions — computed after EXT_TO_SYSTEM is defined below
CARTRIDGE_EXTENSIONS = None  # set at module load

# Optional: Playwright for JS-rendered sites
try:
    from playwright.sync_api import sync_playwright
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False


class _FileResponse:
    """Wraps a local file to look like a requests.Response for uniform handling."""
    def __init__(self, filepath):
        self._path = filepath
        self._size = os.path.getsize(filepath)
        self.headers = {
            "content-length": str(self._size),
            "content-type": "application/octet-stream",
        }
        self.status_code = 200

    def iter_content(self, chunk_size=65536):
        with open(self._path, "rb") as f:
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                yield chunk
        # Clean up the temp file after reading
        try:
            os.unlink(self._path)
            os.rmdir(os.path.dirname(self._path))
        except OSError:
            pass

    def close(self):
        pass

    def raise_for_status(self):
        pass


# Port, staging dir, and defaults from config
PORT = int(cfg("CRAWLER_PORT", "7072"))
STAGING_BASE = cfg("STAGING_DIR", os.path.expanduser("~/nas-staging"))
DEFAULT_DELAY = int(cfg("DEFAULT_DELAY", "5"))
DEFAULT_DEPTH = int(cfg("DEFAULT_DEPTH", "3"))

# Network targets from config
DEVICE_HOST = cfg("DEVICE_HOST", "")
NAS_HOST = cfg("NAS_HOST", "")
NAS_USER = cfg("NAS_USER", "root")
NAS_EXPORT = cfg("NAS_EXPORT", "")
NAS_MOUNT = cfg("NAS_MOUNT", "/tmp/nas-roms")
NAS_ROM_SUBDIR = cfg("NAS_ROM_SUBDIR", "roms")

# Trickle push from config
TRICKLE_PUSH = cfg("TRICKLE_PUSH", "false").lower() == "true"

# Track script modification time for hot-reload on start
_SCRIPT_MTIME = os.path.getmtime(__file__)

# IGDB API config (optional — for auto-classifying unknown game titles)
IGDB_CLIENT_ID = cfg("IGDB_CLIENT_ID", "")
IGDB_CLIENT_SECRET = cfg("IGDB_CLIENT_SECRET", "")


# ============================================================================
# TITLE-BASED CLASSIFICATION (Layer 1: curated JSON, Layer 2: IGDB API)
# ============================================================================

# IGDB platform ID -> our system slug (disc-based systems only)
_IGDB_PLATFORM_MAP = {
    7: "psx",          # PlayStation
    8: "ps2",          # PlayStation 2
    21: "gc",           # GameCube
    29: "genesis",      # Genesis / Mega Drive
    32: "saturn",       # Sega Saturn
    23: "dreamcast",    # Dreamcast
    78: "segacd",       # Sega CD
    50: "3do",          # 3DO
    117: "cdi",         # CD-i
    62: "atarijaguar",  # Atari Jaguar
    61: "atarilynx",   # Atari Lynx (for disambiguation)
    11: "xbox",        # Xbox (for disambiguation)
    18: "nes",          # NES
    19: "snes",         # SNES
    4: "n64",           # Nintendo 64
    5: "wii",           # Wii
    38: "psp",          # PSP
    52: "arcade",       # Arcade
    86: "pcengine",     # PC Engine / TurboGrafx-16
}

# All system slugs we support (used for IGDB disambiguation — if a game
# matches multiple of these, it's ambiguous and we skip it)
_MAPPED_SYSTEMS = set(_IGDB_PLATFORM_MAP.values())

_TITLE_DB_PATH = Path(__file__).resolve().parent / "title-systems.json"

# In-memory IGDB OAuth token cache
_igdb_token = None
_igdb_token_expires = 0


def _load_title_database():
    """Load the curated title-to-system JSON database.

    Returns a list of (pattern, system) tuples sorted by pattern length
    descending (longest/most-specific match wins).
    """
    if not _TITLE_DB_PATH.exists():
        return []

    try:
        with open(_TITLE_DB_PATH, "r") as f:
            db = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"WARNING: Failed to load title database: {e}")
        return []

    pairs = []
    for system, titles in db.items():
        if system.startswith("_"):
            continue  # skip metadata keys
        if not isinstance(titles, list):
            continue
        for title in titles:
            pairs.append((title.lower(), system))

    # Sort by pattern length descending — longest match wins
    pairs.sort(key=lambda p: len(p[0]), reverse=True)
    return pairs


def _load_no_match_cache():
    """Load the _no_match list from the title database."""
    if not _TITLE_DB_PATH.exists():
        return set()
    try:
        with open(_TITLE_DB_PATH, "r") as f:
            db = json.load(f)
        return set(db.get("_no_match", []))
    except (json.JSONDecodeError, OSError):
        return set()


def _save_title_to_database(title_lower, system):
    """Append a new title mapping to the curated JSON database."""
    try:
        with open(_TITLE_DB_PATH, "r") as f:
            db = json.load(f)
    except (json.JSONDecodeError, OSError):
        db = {"_format": "title-systems-v1"}

    if system not in db:
        db[system] = []
    if title_lower not in db[system]:
        db[system].append(title_lower)

    with open(_TITLE_DB_PATH, "w") as f:
        json.dump(db, f, indent=2, ensure_ascii=False)
        f.write("\n")


def _save_no_match(title_lower):
    """Add a title to the _no_match cache so we don't re-query IGDB."""
    try:
        with open(_TITLE_DB_PATH, "r") as f:
            db = json.load(f)
    except (json.JSONDecodeError, OSError):
        db = {"_format": "title-systems-v1"}

    if "_no_match" not in db:
        db["_no_match"] = []
    if title_lower not in db["_no_match"]:
        db["_no_match"].append(title_lower)

    with open(_TITLE_DB_PATH, "w") as f:
        json.dump(db, f, indent=2, ensure_ascii=False)
        f.write("\n")


def _igdb_authenticate():
    """Get an IGDB API token via Twitch OAuth2 client credentials flow."""
    global _igdb_token, _igdb_token_expires

    if _igdb_token and time.time() < _igdb_token_expires:
        return _igdb_token

    if not IGDB_CLIENT_ID or not IGDB_CLIENT_SECRET:
        return None

    try:
        resp = requests.post("https://id.twitch.tv/oauth2/token", data={
            "client_id": IGDB_CLIENT_ID,
            "client_secret": IGDB_CLIENT_SECRET,
            "grant_type": "client_credentials",
        }, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        _igdb_token = data["access_token"]
        _igdb_token_expires = time.time() + data.get("expires_in", 3600) - 60
        return _igdb_token
    except Exception as e:
        print(f"IGDB auth failed: {e}")
        return None


def _igdb_lookup(title):
    """Query IGDB for a game title and return the system slug, or None.

    Returns None if:
    - IGDB credentials not configured
    - API error
    - No results
    - Multiple disc-based systems match (ambiguous)
    """
    token = _igdb_authenticate()
    if not token:
        return None

    # Rate limit: 0.3s between calls
    time.sleep(0.3)

    try:
        resp = requests.post(
            "https://api.igdb.com/v4/games",
            headers={
                "Client-ID": IGDB_CLIENT_ID,
                "Authorization": f"Bearer {token}",
            },
            data=f'search "{title}"; fields name,platforms; limit 5;',
            timeout=10,
        )
        resp.raise_for_status()
        results = resp.json()
    except Exception as e:
        print(f"IGDB lookup failed for '{title}': {e}")
        return None

    if not results:
        return None

    # Collect all matching disc-based systems across results
    matched_systems = set()
    for game in results:
        platforms = game.get("platforms", [])
        for pid in platforms:
            slug = _IGDB_PLATFORM_MAP.get(pid)
            if slug:
                matched_systems.add(slug)

    if len(matched_systems) == 1:
        return matched_systems.pop()

    # Multiple disc systems or none — ambiguous, skip
    return None


# Load the title database at module level
TITLE_DATABASE = _load_title_database()
_NO_MATCH_CACHE = _load_no_match_cache()


# ============================================================================
# BINARY HEADER ANALYSIS (Layer 3: magic byte identification)
# ============================================================================


def _read_system_cnf(data):
    """Parse ISO 9660 root directory to find and read SYSTEM.CNF.

    PSX discs contain "BOOT = cdrom:\\SLUS_123.45;1"
    PS2 discs contain "BOOT2 = cdrom0:\\SLUS_123.45;1"

    Args:
        data: Raw disc data (needs at least ~128KB to reach root dir + file data)
    Returns:
        Contents of SYSTEM.CNF as string, or None if not found.
    """
    pvd_offset = 0x8000
    if len(data) < pvd_offset + 0x100:
        return None

    pvd = data[pvd_offset:]
    if pvd[0:1] != b"\x01" or pvd[1:6] != b"CD001":
        return None

    root_record = pvd[0x9C:0x9C + 34]
    if len(root_record) < 34:
        return None

    root_lba = struct.unpack("<I", root_record[2:6])[0]
    root_len = struct.unpack("<I", root_record[10:14])[0]

    root_offset = root_lba * 2048
    if root_offset + root_len > len(data):
        return None

    root_dir = data[root_offset:root_offset + root_len]

    pos = 0
    while pos < len(root_dir):
        entry_len = root_dir[pos]
        if entry_len == 0:
            next_sector = ((pos // 2048) + 1) * 2048
            if next_sector >= len(root_dir):
                break
            pos = next_sector
            continue

        if pos + entry_len > len(root_dir):
            break

        entry = root_dir[pos:pos + entry_len]
        if len(entry) < 34:
            pos += entry_len
            continue

        id_len = entry[32]
        if id_len > 0 and pos + 33 + id_len <= pos + entry_len:
            file_id = entry[33:33 + id_len].decode("ascii", errors="ignore")
            file_name = file_id.split(";")[0].strip()

            if file_name.upper() == "SYSTEM.CNF":
                file_lba = struct.unpack("<I", entry[2:6])[0]
                file_len = struct.unpack("<I", entry[10:14])[0]
                file_offset = file_lba * 2048

                if file_offset + file_len > len(data):
                    return None

                return data[file_offset:file_offset + file_len].decode(
                    "ascii", errors="ignore"
                )

        pos += entry_len

    return None


def _playstation_version(data):
    """Distinguish PSX from PS2 using SYSTEM.CNF contents.

    Returns "ps2" or "psx". Falls back to PVD heuristics if SYSTEM.CNF
    can't be read.
    """
    system_cnf = _read_system_cnf(data)
    if system_cnf:
        if "BOOT2" in system_cnf:
            return "ps2"
        if "BOOT" in system_cnf:
            return "psx"

    # Fallback: PVD field heuristics
    pvd = data[0x8000:]
    system_id = pvd[8:40].decode("ascii", errors="ignore").strip()
    if "2" in system_id:
        return "ps2"

    publisher = pvd[0x8E:0x8E + 128].decode("ascii", errors="ignore").strip()
    if "2" in publisher and "PLAYSTATION" in publisher.upper():
        return "ps2"

    return "psx"


def _system_from_header_bytes(data):
    """Identify system from raw disc/ROM header bytes.

    Args:
        data: At least first 128KB of disc image data (raw, not CHD-wrapped).
              Covers sector 0, sector 16 PVD, root directory, and SYSTEM.CNF.
    Returns:
        System slug or None.
    """
    if len(data) < 16:
        return None

    # --- Cartridge ROMs ---
    if data[:4] == b"NES\x1a":
        return "nes"

    if len(data) >= 4:
        n64_magic = struct.unpack(">I", data[0:4])[0]
        if n64_magic in (0x80371240, 0x37804012, 0x40123780):
            return "n64"

    if len(data) >= 0x120 and data[0x100:0x104] == b"SEGA":
        header_text = data[0x100:0x120]
        if b"GENESIS" in header_text or b"MEGA DRIVE" in header_text:
            return "genesis"

    if len(data) >= 0x20:
        gc_magic = struct.unpack(">I", data[0x1C:0x20])[0]
        if gc_magic == 0xC2339F3D:
            return "gc"

    if len(data) >= 0x1C:
        wii_magic = struct.unpack(">I", data[0x18:0x1C])[0]
        if wii_magic == 0x5D1C9EA3:
            return "wii"

    # --- Disc-based systems: sector 0 ---
    if data[:15] == b"SEGA SEGASATURN":
        return "saturn"

    if data[:14] == b"SEGADISCSYSTEM" or data[:15] == b"SEGA DISCSYSTEM":
        return "segacd"

    if data[:15] == b"SEGA SEGAKATANA":
        return "dreamcast"
    if len(data) >= 11 and data[:11] == b"SEGA SEGADC":
        return "dreamcast"

    if len(data) >= 0x2E:
        if data[0:4] == b"\x01\x00\x00\x00" and data[0x28:0x2E] == b"CD-ROM":
            return "3do"

    # --- Sector 16 (0x8000) — ISO 9660 Primary Volume Descriptor ---
    pvd_offset = 0x8000
    if len(data) > pvd_offset + 0x240:
        pvd = data[pvd_offset:]

        if pvd[0:1] == b"\x01" and pvd[1:6] == b"CD001":
            system_id = pvd[8:40].decode("ascii", errors="ignore").strip()

            if "PLAYSTATION" in system_id:
                return _playstation_version(data)

        if pvd[0:1] == b"\xff":
            return "cdi"

    # PC Engine CD
    if len(data) >= 0x100:
        if b"PC Engine" in data[:0x100] or b"PC-ENGINE" in data[:0x100]:
            return "pcengine"

    return None


def _parse_chd_header(chd_header_bytes):
    """Parse a CHD v5 file header. Returns info dict or None."""
    if len(chd_header_bytes) < 124 or chd_header_bytes[:8] != b"MComprHD":
        return None

    version = struct.unpack(">I", chd_header_bytes[12:16])[0]
    if version != 5:
        return None

    compressors = []
    for i in range(4):
        c = chd_header_bytes[16 + i * 4:20 + i * 4]
        if c != b"\x00\x00\x00\x00":
            compressors.append(c)

    logical_bytes = struct.unpack(">Q", chd_header_bytes[32:40])[0]
    map_offset = struct.unpack(">Q", chd_header_bytes[40:48])[0]
    hunk_bytes = struct.unpack(">I", chd_header_bytes[56:60])[0]

    hunk_count = (logical_bytes + hunk_bytes - 1) // hunk_bytes if hunk_bytes else 0

    return {
        "version": 5,
        "hunk_bytes": hunk_bytes,
        "hunk_count": hunk_count,
        "map_offset": map_offset,
        "compressors": compressors,
        "logical_bytes": logical_bytes,
    }


def _decompress_chd_hunk(data, offset, length, compressor):
    """Decompress a single CHD hunk. Returns bytes or None."""
    compressed = data[offset:offset + length]
    if not compressed:
        return None

    tag = compressor.decode("ascii", errors="ignore").strip("\x00")

    try:
        if tag in ("zlib", "cdzl"):
            return zlib.decompress(compressed, -15)
        elif tag in ("lzma", "cdlz"):
            return lzma.decompress(compressed)
        elif tag == "none":
            return compressed
    except Exception:
        pass

    # Fallback: try raw inflate and standard zlib
    for wbits in (-15, 15):
        try:
            return zlib.decompress(compressed, wbits)
        except Exception:
            pass
    return None


def _read_chd_sector_data(filepath, max_read=512 * 1024):
    """Extract first ~64KB of disc data from a CHD file.

    Reads the CHD header, parses the hunk map, and decompresses the first
    few hunks to get sector 0 + sector 16 of the actual disc.
    """
    try:
        with open(filepath, "rb") as f:
            raw = f.read(max_read)
    except OSError:
        return None

    info = _parse_chd_header(raw)
    if not info:
        return None

    hunk_bytes = info["hunk_bytes"]
    map_offset = info["map_offset"]
    hunk_count = info["hunk_count"]
    compressors = info["compressors"]

    if not hunk_bytes or not compressors:
        return None

    needed_bytes = 0x20000  # 128KB: sector 16 PVD + root dir + SYSTEM.CNF
    map_entry_size = 12
    result = bytearray()
    hunks_needed = (needed_bytes + hunk_bytes - 1) // hunk_bytes

    for hunk_idx in range(min(hunks_needed, hunk_count)):
        entry_offset = map_offset + hunk_idx * map_entry_size
        if entry_offset + map_entry_size > len(raw):
            break

        entry = raw[entry_offset:entry_offset + map_entry_size]
        comp_type = entry[0]
        comp_length = (entry[1] << 16) | (entry[2] << 8) | entry[3]
        hunk_offset = struct.unpack(">Q", b"\x00\x00" + entry[4:10])[0]

        if hunk_offset + comp_length > len(raw):
            break

        if comp_type == 4:  # uncompressed
            result.extend(raw[hunk_offset:hunk_offset + hunk_bytes])
        elif comp_type == 5:  # self-ref
            break
        elif comp_type < len(compressors):
            decompressed = _decompress_chd_hunk(raw, hunk_offset, comp_length, compressors[comp_type])
            if decompressed:
                result.extend(decompressed)
            else:
                break
        else:
            break

        if len(result) >= needed_bytes:
            break

    return bytes(result) if len(result) >= 16 else None


def _system_from_file_header(filepath):
    """Read binary headers from a local file and return system slug or None.

    Handles both CHD containers and raw disc images (.iso, .bin, .img).
    """
    ext = filepath.suffix.lower()

    if ext == ".chd":
        data = _read_chd_sector_data(filepath)
    elif ext in (".iso", ".bin", ".img"):
        try:
            with open(filepath, "rb") as f:
                data = f.read(0x20000)
        except OSError:
            return None
    else:
        return None

    if not data:
        return None

    return _system_from_header_bytes(data)


# ============================================================================
# CRAWLER ENGINE
# ============================================================================

DOWNLOAD_EXTENSIONS = {
    ".zip", ".7z", ".rar", ".gz", ".tar", ".bz2", ".xz",
    ".iso", ".bin", ".cue", ".chd", ".img", ".mdf", ".mds",
    ".nrg", ".ccd", ".sub", ".ecm", ".pbp", ".cso",
    ".nes", ".unf", ".sfc", ".smc", ".gb", ".gbc", ".gba",
    ".nds", ".3ds", ".cia", ".cci", ".n64", ".z64", ".v64",
    ".gcm", ".gcz", ".rvz", ".wbfs", ".wad", ".nsp", ".xci",
    ".md", ".smd", ".gen", ".gg", ".sms", ".32x",
    ".cdi", ".gdi", ".a26", ".a52", ".a78", ".lnx", ".jag",
    ".pce", ".ngp", ".ngc", ".ws", ".wsc", ".vec", ".col", ".sg",
    ".psf", ".minipsf", ".scummvm",
}

PAGE_EXTENSIONS = {"", ".html", ".htm", ".php", ".asp", ".aspx", ".jsp", "/"}

# Extension -> system slug mapping (for auto-detect)
EXT_TO_SYSTEM = {
    ".nes": "nes", ".unf": "nes", ".unif": "nes",
    ".sfc": "snes", ".smc": "snes",
    ".gb": "gb", ".gbc": "gbc", ".gba": "gba",
    ".nds": "nds", ".3ds": "3ds", ".cia": "3ds", ".cci": "3ds",
    ".n64": "n64", ".z64": "n64", ".v64": "n64",
    ".gcm": "gc", ".gcz": "gc", ".rvz": "gc",
    ".wbfs": "wii", ".wad": "wii",
    ".nsp": "switch", ".xci": "switch",
    ".pbp": "psx", ".ecm": "psx",
    ".cso": "psp",
    ".smd": "genesis", ".gen": "genesis",
    ".gg": "gamegear", ".sms": "mastersystem",
    ".32x": "sega32x",
    ".cdi": "dreamcast", ".gdi": "dreamcast",
    ".a26": "atari2600", ".a52": "atari5200", ".a78": "atari7800",
    ".lnx": "atarilynx", ".lyx": "atarilynx", ".jag": "atarijaguar",
    ".pce": "pcengine",
    ".ngp": "ngp", ".ngc": "ngpc",
    ".ws": "wonderswan", ".wsc": "wonderswancolor",
    ".vec": "vectrex", ".col": "coleco",
}

# Now compute cartridge extensions (ROM files that should be in .zip, not disc images)
CARTRIDGE_EXTENSIONS = set(EXT_TO_SYSTEM.keys()) - DISC_IMAGE_EXTENSIONS

# URL path keywords -> system slug (for detecting system from directory structure)
# Order matters — more specific patterns first to avoid false matches
PATH_KEYWORDS = [
    ("playstation-2", "ps2"), ("playstation2", "ps2"), ("ps2", "ps2"),
    ("playstation-3", "ps3"), ("playstation3", "ps3"), ("ps3", "ps3"),
    ("playstation", "psx"), ("psx", "psx"), ("ps1", "psx"),
    ("psp", "psp"), ("vita", "psvita"),
    ("super-nintendo", "snes"), ("super_nintendo", "snes"), ("snes", "snes"),
    ("super-famicom", "snes"), ("super_famicom", "snes"),
    ("nintendo-64", "n64"), ("nintendo64", "n64"), ("n64", "n64"),
    ("nintendo-ds", "nds"), ("nds", "nds"), ("ds", "nds"),
    ("nintendo-3ds", "3ds"), ("3ds", "3ds"),
    ("gamecube", "gc"), ("game-cube", "gc"), ("game_cube", "gc"), ("ngc", "gc"),
    ("game-boy-advance", "gba"), ("gameboy-advance", "gba"),
    ("game_boy_advance", "gba"), ("gba", "gba"),
    ("game-boy-color", "gbc"), ("gameboy-color", "gbc"),
    ("game_boy_color", "gbc"), ("gbc", "gbc"),
    ("game-boy", "gb"), ("gameboy", "gb"), ("game_boy", "gb"),
    ("nes", "nes"), ("nintendo-entertainment", "nes"), ("famicom", "nes"),
    ("wii-u", "wiiu"), ("wiiu", "wiiu"),
    ("wii", "wii"),
    ("switch", "switch"), ("nsp", "switch"),
    ("mega-drive", "genesis"), ("megadrive", "genesis"), ("genesis", "genesis"),
    ("sega-cd", "segacd"), ("segacd", "segacd"), ("mega-cd", "segacd"),
    ("sega-saturn", "saturn"), ("saturn", "saturn"),
    ("dreamcast", "dreamcast"), ("dream-cast", "dreamcast"),
    ("master-system", "mastersystem"), ("mastersystem", "mastersystem"),
    ("game-gear", "gamegear"), ("gamegear", "gamegear"),
    ("sega-32x", "sega32x"), ("32x", "sega32x"),
    ("arcade", "arcade"), ("mame", "arcade"), ("neo-geo", "arcade"), ("neogeo", "arcade"),
    ("pc-engine", "pcengine"), ("pcengine", "pcengine"),
    ("turbografx", "pcengine"), ("tg16", "pcengine"), ("tg-16", "pcengine"),
    ("atari-2600", "atari2600"), ("atari2600", "atari2600"),
    ("atari-5200", "atari5200"), ("atari5200", "atari5200"),
    ("atari-7800", "atari7800"), ("atari7800", "atari7800"),
    ("atari-lynx", "atarilynx"), ("lynx", "atarilynx"),
    ("atari-jaguar", "atarijaguar"), ("jaguar", "atarijaguar"),
    ("xbox", "xbox"),
    ("scummvm", "scummvm"),
    ("wonderswan-color", "wonderswancolor"), ("wonderswancolor", "wonderswancolor"),
    ("wonderswan", "wonderswan"),
    ("neo-geo-pocket", "ngp"), ("neogeopocket", "ngp"),
    ("vectrex", "vectrex"), ("coleco", "coleco"),
]

# All system choices for the dropdown
SYSTEM_CHOICES = [
    ("auto", "Auto-detect from file type"),
    ("psx", "PlayStation (PSX)"),
    ("ps2", "PlayStation 2"),
    ("ps3", "PlayStation 3"),
    ("psp", "PSP"),
    ("nes", "NES"),
    ("snes", "SNES"),
    ("n64", "Nintendo 64"),
    ("gb", "Game Boy"),
    ("gbc", "Game Boy Color"),
    ("gba", "Game Boy Advance"),
    ("nds", "Nintendo DS"),
    ("3ds", "Nintendo 3DS"),
    ("gc", "GameCube"),
    ("wii", "Wii"),
    ("switch", "Nintendo Switch"),
    ("genesis", "Sega Genesis"),
    ("saturn", "Sega Saturn"),
    ("dreamcast", "Dreamcast"),
    ("segacd", "Sega CD"),
    ("mastersystem", "Master System"),
    ("gamegear", "Game Gear"),
    ("arcade", "Arcade / MAME"),
    ("pcengine", "PC Engine / TG16"),
    ("atari2600", "Atari 2600"),
    ("atari7800", "Atari 7800"),
    ("atarilynx", "Atari Lynx"),
    ("atarijaguar", "Atari Jaguar"),
    ("3do", "3DO"),
    ("cdi", "Philips CD-i"),
    ("xbox", "Xbox"),
    ("scummvm", "ScummVM"),
    ("other", "Other / Unsorted"),
]


class CrawlJob:
    """A single crawl job with progress tracking."""

    def __init__(self, base_url, output_dir, max_depth=3, delay=5, system="auto",
                 js_mode=False):
        self.base_url = base_url.rstrip("/")
        self.output_dir = Path(output_dir)
        self.max_depth = max_depth
        self.delay = delay
        self.system = system  # "auto" or a specific system slug
        self.js_mode = js_mode and HAS_PLAYWRIGHT

        parsed = urllib.parse.urlparse(self.base_url)
        self.domain = parsed.netloc
        self.scheme = parsed.scheme

        self.session = requests.Session()
        self.session.verify = False  # Many ROM sites have broken SSL chains
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,*/*",
            "Accept-Language": "en-US,en;q=0.9",
        })

        # Playwright browser (lazy-init)
        self._pw = None
        self._browser = None
        self._page = None

        # Trickle push state
        self._trickle_enabled = TRICKLE_PUSH
        self._nas_reachable = None     # None = unknown, True/False = cached result
        self._nas_check_time = 0       # timestamp of last reachability check

        # State
        self.state_file = self.output_dir / ".crawler-state.json"
        self.visited_pages = set()
        self.downloaded_files = set()
        self.failed_files = set()
        self.discovered_files = []
        # Dedup registry: {filename -> {url, size, sha256}} — catches same-name collisions
        self.file_registry = {}
        self.dupes_skipped = 0

        # Progress (read by UI)
        self.status = "idle"  # idle, crawling, downloading, complete, stopped, error
        self.phase = ""
        self.log_lines = []
        self.pages_crawled = 0
        self.files_found = 0
        self.files_downloaded = 0
        self.files_failed = 0
        self.files_total = 0
        self.current_file = ""
        self.current_progress = 0  # 0-100
        self.current_speed = ""
        self.bytes_downloaded = 0
        self.stop_requested = False

        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._load_state()

    def _log(self, msg):
        ts = time.strftime("%H:%M:%S")
        line = f"[{ts}] {msg}"
        self.log_lines.append(line)
        # Keep last 500 lines
        if len(self.log_lines) > 500:
            self.log_lines = self.log_lines[-500:]

    def _load_state(self):
        if self.state_file.exists():
            try:
                state = json.loads(self.state_file.read_text())
                # NOTE: visited_pages intentionally NOT restored — each crawl
                # starts fresh so we re-discover pages and find new files.
                # Only download history persists (that's the dedup data).
                self.downloaded_files = set(state.get("downloaded_files", []))
                self.failed_files = set(state.get("failed_files", []))
                self.file_registry = state.get("file_registry", {})
                if self.downloaded_files:
                    self._log(f"Resumed: {len(self.downloaded_files)} files downloaded, "
                              f"{len(self.file_registry)} in dedup registry")
            except (json.JSONDecodeError, KeyError):
                pass

    def _save_state(self):
        state = {
            "downloaded_files": list(self.downloaded_files),
            "failed_files": list(self.failed_files),
            "file_registry": self.file_registry,
            "base_url": self.base_url,
            "last_run": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        self.state_file.write_text(json.dumps(state, indent=2))

    @staticmethod
    def _file_sha256(filepath, chunk_size=1024 * 256):
        """Compute SHA-256 hash of a file."""
        h = hashlib.sha256()
        with open(filepath, "rb") as f:
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()

    def _dedup_filepath(self, filepath, url):
        """Check for duplicates, return (final_path, should_download).

        Returns (filepath, True) if the file should be downloaded.
        Returns (new_filepath, True) if a name collision exists but content differs.
        Returns (None, False) if the file is a known duplicate.
        """
        name = filepath.name
        system = filepath.parent.name

        # If file doesn't exist locally, check the registry for content-level dups
        if not filepath.exists():
            return filepath, True

        # File exists locally — is it the same URL we already downloaded?
        if url in self.downloaded_files:
            return None, False

        # File exists from a different URL — compare sizes first (cheap)
        existing_size = filepath.stat().st_size
        registry_key = f"{system}/{name}"

        if registry_key in self.file_registry:
            reg = self.file_registry[registry_key]
            if reg.get("url") == url:
                # Same URL, already downloaded
                return None, False

        # Try a HEAD request to compare content-length before downloading
        try:
            head = self.session.head(url, timeout=15, allow_redirects=True)
            remote_size = int(head.headers.get("content-length", 0))
        except Exception:
            remote_size = 0

        if remote_size > 0 and remote_size == existing_size:
            # Same name, same size — almost certainly the same file
            self._log(f"  Dedup: {name} (same size {existing_size} bytes, skipping)")
            self.downloaded_files.add(url)
            self.dupes_skipped += 1
            self._save_state()
            return None, False

        if remote_size > 0 and remote_size != existing_size:
            # Different size — this is a different file with the same name.
            # Give it a unique suffix.
            stem = filepath.stem
            suffix = filepath.suffix
            counter = 2
            new_path = filepath.parent / f"{stem}_{counter}{suffix}"
            while new_path.exists():
                counter += 1
                new_path = filepath.parent / f"{stem}_{counter}{suffix}"
            self._log(f"  Name collision: {name} -> {new_path.name} (different file)")
            return new_path, True

        # Couldn't determine remote size — download and check after
        return filepath, True

    def _is_same_domain(self, url):
        parsed = urllib.parse.urlparse(url)
        return parsed.netloc == self.domain or parsed.netloc == ""

    def _is_child_or_pagination(self, link, parent_url):
        """True if link is under parent_url's path, or is a paginated variant."""
        link_p = urllib.parse.urlparse(link)
        url_p = urllib.parse.urlparse(parent_url)
        url_path = url_p.path.rstrip("/")
        link_path = link_p.path.rstrip("/")
        if link_path.startswith(url_path + "/"):
            return True
        if link_path == url_path and link_p.query and link_p.query != url_p.query:
            return True
        return False

    def _normalize_url(self, url, page_url):
        if url.startswith("//"):
            url = f"{self.scheme}:{url}"
        elif url.startswith("/"):
            url = f"{self.scheme}://{self.domain}{url}"
        elif not url.startswith("http"):
            url = urllib.parse.urljoin(page_url, url)
        parsed = urllib.parse.urlparse(url)
        path = parsed.path.rstrip("/") or "/"
        normalized = f"{parsed.scheme}://{parsed.netloc}{path}"
        if parsed.query:
            normalized += f"?{parsed.query}"
        return normalized

    def _is_downloadable(self, url):
        path = urllib.parse.urlparse(url).path.lower()
        return Path(path).suffix.lower() in DOWNLOAD_EXTENSIONS

    def _is_page(self, url):
        path = urllib.parse.urlparse(url).path.lower()
        if path.endswith("/"):
            return True
        return Path(path).suffix.lower() in PAGE_EXTENSIONS

    def _system_from_url_path(self, url):
        """Try to detect system from the URL's directory structure.

        ROM sites almost always organize files into system folders like
        /roms/snes/ or /vault/GBA/ — this catches that.
        """
        path = urllib.parse.unquote(urllib.parse.urlparse(url).path).lower()
        # Split into path segments and check each one
        segments = [s for s in path.split("/") if s]
        # Remove the filename (last segment) — we only want directories
        if segments:
            segments = segments[:-1]
        path_str = "/".join(segments)

        for keyword, system in PATH_KEYWORDS:
            # Check if keyword appears as a full path segment or substring
            for seg in segments:
                if seg == keyword or seg == keyword.replace("-", ""):
                    return system
            # Fallback: substring match on the full path (less precise but catches more)
            if keyword in path_str:
                return system
        return None

    def _system_from_title(self, filename):
        """Try to classify a file by matching its title against the curated
        title database (Layer 1) and optionally the IGDB API (Layer 2).

        Returns a system slug or None if no confident match.
        """
        global TITLE_DATABASE
        # Strip extension and region tags to get a clean title
        stem = Path(filename).stem
        # Remove region/revision tags: (USA), (Europe), (Disc 1), (Rev 2), (v2.01), (En,Ja,...), (T-En)
        clean = re.sub(r'\s*\([^)]*\)', '', stem).strip()
        title_lower = clean.lower()

        if not title_lower:
            return None

        # Layer 1: Curated database (fast, offline)
        for pattern, system in TITLE_DATABASE:
            if pattern in title_lower:
                self._log(f"  Title match: '{clean}' -> {system} (curated)")
                return system

        # Layer 2: IGDB API fallback (if configured)
        if not IGDB_CLIENT_ID or not IGDB_CLIENT_SECRET:
            return None

        # Check no-match cache to avoid re-querying
        if title_lower in _NO_MATCH_CACHE:
            return None

        self._log(f"  IGDB lookup: '{clean}'...")
        system = _igdb_lookup(clean)

        if system:
            self._log(f"  IGDB match: '{clean}' -> {system}")
            # Cache the result in the curated database for future runs
            _save_title_to_database(title_lower, system)
            # Reload the in-memory database
            TITLE_DATABASE = _load_title_database()
            return system
        else:
            self._log(f"  IGDB: no confident match for '{clean}'")
            _save_no_match(title_lower)
            _NO_MATCH_CACHE.add(title_lower)
            return None

    def _get_system_for_file(self, filename, url=None):
        """Determine which system folder a file belongs in."""
        if self.system != "auto":
            return self.system

        ext = Path(filename).suffix.lower()

        # 1. Direct extension match (unambiguous ROM types)
        system = EXT_TO_SYSTEM.get(ext)
        if system:
            return system

        # 2. Check URL path for system keywords (most reliable for archives)
        if url:
            system = self._system_from_url_path(url)
            if system:
                return system

        # 3. Ambiguous extensions — guess from filename content
        fname_lower = filename.lower()
        if ext in (".iso", ".chd", ".bin", ".cue", ".img"):
            if "ps2" in fname_lower or "playstation 2" in fname_lower:
                return "ps2"
            elif "ps1" in fname_lower or "psx" in fname_lower or "playstation" in fname_lower:
                return "psx"
            elif "dreamcast" in fname_lower or "dc" in fname_lower:
                return "dreamcast"
            elif "saturn" in fname_lower:
                return "saturn"
            elif "segacd" in fname_lower or "sega cd" in fname_lower:
                return "segacd"
            elif "gamecube" in fname_lower or "gc" in fname_lower:
                return "gc"
            elif "wii" in fname_lower:
                return "wii"
            elif "xbox" in fname_lower:
                return "xbox"

        # 3.5. Title-based classification (curated database + IGDB fallback)
        if ext in (".zip", ".7z", ".rar", ".chd", ".iso"):
            system = self._system_from_title(filename)
            if system:
                return system

        # 4. Archives with no other clues -> "other" (may be reclassified after download)
        if ext in (".zip", ".7z", ".rar", ".gz", ".bz2", ".xz", ".tar"):
            return "other"

        return "other"

    def _peek_archive_system(self, filepath):
        """Peek inside an archive to detect system from contained file extensions.

        Supports .zip, .7z, and .rar files.
        """
        ext = filepath.suffix.lower()
        filenames = []

        try:
            if ext == ".zip":
                with zipfile.ZipFile(filepath, "r") as zf:
                    filenames = [i.filename for i in zf.infolist() if not i.is_dir()]

            elif ext == ".7z":
                with py7zr.SevenZipFile(filepath, "r") as sz:
                    filenames = sz.getnames()

            elif ext == ".rar":
                with rarfile.RarFile(filepath, "r") as rf:
                    filenames = [i.filename for i in rf.infolist() if not i.is_dir()]

        except Exception as e:
            self._log(f"  Archive peek failed ({ext}): {e}")
            return None

        # Check each file inside for a known ROM extension
        for name in filenames:
            inner_ext = Path(name).suffix.lower()
            system = EXT_TO_SYSTEM.get(inner_ext)
            if system:
                return system

        return None

    def _reclassify_archive(self, filepath, url):
        """After downloading, reclassify if the file landed in 'other'.

        Tries two strategies:
        1. Archive peek: look inside .zip/.7z/.rar for known ROM extensions
        2. Header analysis: read binary headers from .chd/.iso/.bin files
        """
        if filepath.parent.name != "other":
            return filepath  # Already classified

        ext = filepath.suffix.lower()
        new_system = None

        # Strategy 1: peek inside archives
        if ext in (".zip", ".7z", ".rar"):
            new_system = self._peek_archive_system(filepath)

        # Strategy 2: binary header analysis for disc images
        if not new_system and ext in (".chd", ".iso", ".bin", ".img"):
            new_system = _system_from_file_header(filepath)
            if new_system:
                self._log(f"  Header analysis: {filepath.name} -> {new_system}")

        if not new_system:
            return filepath  # Can't determine, leave in "other"

        # Move to the correct system directory
        new_dir = self.output_dir / new_system
        new_dir.mkdir(parents=True, exist_ok=True)
        new_path = new_dir / filepath.name

        if new_path.exists():
            self._log(f"  Reclassify: {filepath.name} -> {new_system}/ (already exists, kept in other/)")
            return filepath

        source = "header" if ext in (".chd", ".iso", ".bin", ".img") else "archive contents"
        filepath.rename(new_path)
        self._log(f"  Reclassify: {filepath.name} -> {new_system}/ (detected from {source})")
        return new_path

    # ------------------------------------------------------------------
    # Multi-disc detection + .m3u generation
    # ------------------------------------------------------------------

    _DISC_RE = re.compile(r"^(.+?)\s*\(Disc\s*(\d+)\)", re.IGNORECASE)

    def _check_multi_disc(self, filepath):
        """After downloading a Disc 1 file, look for sibling discs and generate .m3u.

        Only triggers on files matching (Disc 1) pattern. Searches discovered_files
        for matching sibling disc URLs, and tries URL manipulation as fallback.
        After all discs are accounted for, generates the .m3u playlist.
        """
        stem = filepath.stem
        m = self._DISC_RE.match(stem)
        if not m:
            return  # Not a disc-numbered file

        disc_num = int(m.group(2))
        if disc_num != 1:
            return  # Only trigger on Disc 1

        base_name = m.group(1).strip()
        system_dir = filepath.parent
        ext = filepath.suffix  # e.g., ".chd"

        self._log(f"  Multi-disc: detected Disc 1 for '{base_name}'")

        # Find existing disc files in the same system directory
        existing_discs = {}
        if system_dir.exists():
            for f in system_dir.iterdir():
                if not f.is_file():
                    continue
                dm = self._DISC_RE.match(f.stem)
                if dm and dm.group(1).strip().lower() == base_name.lower() and f.suffix.lower() == ext.lower():
                    existing_discs[int(dm.group(2))] = f.name

        # Check discovered_files for sibling disc URLs not yet downloaded
        sibling_urls = []
        base_lower = base_name.lower()
        for url in self.discovered_files:
            if url in self.downloaded_files:
                continue
            # Extract filename from URL
            url_path = urllib.parse.urlparse(url).path
            url_fname = urllib.parse.unquote(url_path.split("/")[-1])
            dm = self._DISC_RE.match(Path(url_fname).stem)
            if dm and dm.group(1).strip().lower() == base_lower and int(dm.group(2)) != 1:
                sibling_urls.append((int(dm.group(2)), url))

        # Try URL manipulation as fallback for discs 2-4
        if not sibling_urls:
            # Find the URL that produced this Disc 1 file
            disc1_url = None
            for url in self.downloaded_files:
                url_path = urllib.parse.urlparse(url).path
                url_fname = urllib.parse.unquote(url_path.split("/")[-1])
                if url_fname == filepath.name or base_lower in url_fname.lower():
                    disc1_url = url
                    break

            if disc1_url:
                for disc_n in range(2, 5):
                    if disc_n in existing_discs:
                        continue
                    # Try common URL patterns for disc numbering
                    for old, new in [
                        (f"Disc 1", f"Disc {disc_n}"),
                        (f"Disc_1", f"Disc_{disc_n}"),
                        (f"disc1", f"disc{disc_n}"),
                        (f"disc_1", f"disc_{disc_n}"),
                        (f"(Disc 1)", f"(Disc {disc_n})"),
                        (f"(Disc%201)", f"(Disc%20{disc_n})"),
                    ]:
                        if old in disc1_url:
                            test_url = disc1_url.replace(old, new)
                            if test_url != disc1_url:
                                sibling_urls.append((disc_n, test_url))
                                break

        # Stage 3 — Vault page scrape: parse media array from source page.
        # For synthetic POST URLs (from Vimm), the 4th pipe segment is the
        # source page URL. Fetch it, parse const media=[...], and construct
        # synthetic POST URLs for sibling disc mediaIds.
        if not sibling_urls:
            # Find disc1 URL if not already found (covers re-crawl scenarios
            # where the download URL is a POST synthetic URL)
            if not disc1_url:
                for u in self.downloaded_files:
                    if not isinstance(u, str):
                        continue
                    if u.startswith("POST|"):
                        # For POST URLs, check if the referer page name
                        # contains the game's base name
                        parts = u.split("|", 3)
                        referer = parts[3] if len(parts) > 3 else ""
                        referer_lower = urllib.parse.unquote(referer).lower()
                        if base_lower in referer_lower:
                            disc1_url = u
                            break
                    else:
                        u_path = urllib.parse.urlparse(u).path
                        u_fname = urllib.parse.unquote(u_path.split("/")[-1])
                        if u_fname == filepath.name or base_lower in u_fname.lower():
                            disc1_url = u
                            break

            if disc1_url and "|" in disc1_url:
                parts = disc1_url.split("|", 3)
                if len(parts) >= 4:
                    source_page = parts[3]
                    disc1_params = dict(urllib.parse.parse_qsl(parts[2]))
                    disc1_mid = disc1_params.get("mediaId", "")
                    form_action = parts[1]

                    self._log(f"  Multi-disc: scraping source page for media array...")
                    try:
                        html = self._fetch_page_html(source_page)
                        if html:
                            page_soup = BeautifulSoup(html, "html.parser")
                            for script in page_soup.find_all("script"):
                                script_text = script.string or ""
                                media_match = re.search(
                                    r'const\s+media\s*=\s*(\[.+?\]);', script_text)
                                if not media_match:
                                    continue
                                try:
                                    media_list = json.loads(media_match.group(1))
                                except (json.JSONDecodeError, ValueError):
                                    continue
                                if not isinstance(media_list, list) or len(media_list) <= 1:
                                    continue
                                for entry in media_list:
                                    if not isinstance(entry, dict):
                                        continue
                                    mid = str(entry.get("ID", ""))
                                    if not mid or mid == disc1_mid:
                                        continue
                                    disc_post = urllib.parse.urlencode({"mediaId": mid})
                                    disc_url = f"POST|{form_action}|{disc_post}|{source_page}"
                                    if disc_url not in self.downloaded_files:
                                        disc_label = entry.get("Label", f"media {mid}")
                                        self._log(f"  Multi-disc: found {disc_label} (media {mid})")
                                        # We don't know the exact disc number from the media array,
                                        # so assign sequentially after disc 1
                                        next_disc = max(existing_discs.keys(), default=1) + 1
                                        while next_disc in existing_discs:
                                            next_disc += 1
                                        sibling_urls.append((next_disc, disc_url))
                                break  # Only process first media array
                    except Exception as e:
                        self._log(f"  Multi-disc: scrape failed — {e}")

        # Download any discovered sibling discs
        for disc_n, url in sorted(sibling_urls):
            if disc_n in existing_discs:
                continue
            self._log(f"  Multi-disc: downloading Disc {disc_n}...")
            success = self.download_file(url)
            if success:
                # Re-scan directory for the new file
                for f in system_dir.iterdir():
                    dm = self._DISC_RE.match(f.stem)
                    if dm and dm.group(1).strip().lower() == base_lower and int(dm.group(2)) == disc_n:
                        existing_discs[disc_n] = f.name
                        break

        # Re-scan to get final disc inventory
        existing_discs = {}
        if system_dir.exists():
            for f in system_dir.iterdir():
                if not f.is_file():
                    continue
                dm = self._DISC_RE.match(f.stem)
                if dm and dm.group(1).strip().lower() == base_lower and f.suffix.lower() == ext.lower():
                    existing_discs[int(dm.group(2))] = f.name

        if len(existing_discs) > 1:
            self._generate_m3u(system_dir, base_name, existing_discs)

    def _generate_m3u(self, system_dir, base_name, disc_map):
        """Generate a .m3u playlist file for a multi-disc game.

        Args:
            system_dir: Directory containing the disc files
            base_name: Game title without disc number (e.g., "Final Fantasy VII (USA)")
            disc_map: Dict of {disc_number: filename}
        """
        # Check for completeness (no gaps in disc numbers)
        max_disc = max(disc_map.keys())
        missing = [n for n in range(1, max_disc + 1) if n not in disc_map]
        if missing:
            self._log(f"  M3U: incomplete set for '{base_name}' — missing disc(s) {missing}")
            return

        m3u_name = f"{base_name}.m3u"
        m3u_path = system_dir / m3u_name

        if m3u_path.exists():
            self._log(f"  M3U: {m3u_name} already exists")
            return

        # Write playlist — one filename per line, sorted by disc number
        lines = [disc_map[n] for n in sorted(disc_map.keys())]
        m3u_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self._log(f"  M3U: created {m3u_name} ({len(lines)} discs)")

        # Trickle push the .m3u file to NAS
        self._trickle_push(m3u_path)

    def _sweep_m3u(self):
        """Post-crawl sweep: scan all system dirs in staging for multi-disc games
        that need .m3u playlists. Catches anything _check_multi_disc missed
        (e.g., discs downloaded in separate crawl sessions).
        """
        if not self.output_dir.exists():
            return

        disc_re = self._DISC_RE
        m3u_count = 0

        for system_dir in self.output_dir.iterdir():
            if not system_dir.is_dir():
                continue

            # Group disc files by base name
            games = {}
            existing_m3u = set()
            for f in system_dir.iterdir():
                if not f.is_file():
                    continue
                if f.suffix.lower() == ".m3u":
                    existing_m3u.add(f.stem.lower())
                    continue
                m = disc_re.match(f.stem)
                if m:
                    base = m.group(1).strip()
                    disc_num = int(m.group(2))
                    if base not in games:
                        games[base] = {}
                    games[base][disc_num] = f.name

            # Generate .m3u for any complete sets without one
            for base_name, disc_map in games.items():
                if base_name.lower() in existing_m3u:
                    continue
                if len(disc_map) < 2:
                    continue
                self._generate_m3u(system_dir, base_name, disc_map)
                m3u_count += 1

        if m3u_count:
            self._log(f"M3U sweep: generated {m3u_count} playlist(s)")

    def _post_process(self, filepath):
        """Recompress downloaded file to optimal format.

        - Archives containing disc images (.bin/.cue, .iso, .gdi) -> extract -> CHD
        - Archives containing cartridge ROMs -> extract -> repack as .zip
        - Bare disc images -> convert to CHD
        - Bare cartridge ROMs -> pack as .zip
        - Already optimal formats (.chd, .rvz, .cso, .pbp) -> skip
        - .zip files already -> skip (assume good enough; avoid re-extract/repack churn)

        Note: We use .zip instead of .7z because RetroArch's built-in extractor
        cannot handle LZMA2-compressed .7z archives.

        Returns the new filepath (may differ from input).
        """
        ext = filepath.suffix.lower()
        parent = filepath.parent

        # Already optimal — leave it
        if ext in OPTIMAL_EXTENSIONS:
            return filepath

        # Already .zip — skip reprocessing
        if ext == ".zip":
            return filepath

        # --- Bare (non-archived) files ---
        if ext not in (".7z", ".rar"):
            if ext in DISC_IMAGE_EXTENSIONS:
                return self._convert_to_chd(filepath)
            if ext in CARTRIDGE_EXTENSIONS:
                return self._pack_to_zip(filepath)
            return filepath  # Unknown type, leave it

        # --- Archives (.7z, .rar) — extract, identify, reprocess ---
        return self._reprocess_archive(filepath)

    def _convert_to_chd(self, filepath):
        """Convert a disc image to CHD format using chdman."""
        if not os.path.isfile(CHDMAN_BIN):
            self._log(f"  Skip CHD convert (chdman not found)")
            return filepath

        ext = filepath.suffix.lower()
        chd_path = filepath.with_suffix(".chd")

        if chd_path.exists():
            self._log(f"  CHD already exists: {chd_path.name}")
            filepath.unlink()
            return chd_path

        # chdman createcd for .cue/.gdi, createraw for .iso/.bin/.img
        if ext in (".cue", ".gdi"):
            cmd = [CHDMAN_BIN, "createcd", "-i", str(filepath), "-o", str(chd_path)]
        elif ext in (".iso",):
            cmd = [CHDMAN_BIN, "createdvd", "-i", str(filepath), "-o", str(chd_path)]
        else:
            # .bin without .cue, .img, .mdf — try createcd first, fallback to createraw
            cmd = [CHDMAN_BIN, "createcd", "-i", str(filepath), "-o", str(chd_path)]

        self._log(f"  Converting to CHD: {filepath.name}")
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            if result.returncode != 0:
                # If createcd failed for a raw .bin, try createraw with reasonable hunk size
                if ext in (".bin", ".img", ".mdf") and "createcd" in cmd:
                    cmd = [CHDMAN_BIN, "createraw", "-i", str(filepath), "-o", str(chd_path),
                           "-hs", "2048"]
                    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
                if result.returncode != 0:
                    self._log(f"  CHD convert failed: {result.stderr[:120]}")
                    if chd_path.exists():
                        chd_path.unlink()
                    return filepath

            orig_size = filepath.stat().st_size
            chd_size = chd_path.stat().st_size
            savings = (1 - chd_size / orig_size) * 100 if orig_size > 0 else 0
            self._log(f"  CHD done: {chd_path.name} ({savings:.0f}% smaller)")

            # Clean up original
            filepath.unlink()
            return chd_path

        except (subprocess.TimeoutExpired, OSError) as e:
            self._log(f"  CHD convert error: {e}")
            if chd_path.exists():
                chd_path.unlink()
            return filepath

    def _pack_to_zip(self, filepath):
        """Pack a bare ROM file into .zip (deflate compression)."""
        zip_path = filepath.with_suffix(".zip")

        if zip_path.exists():
            self._log(f"  zip already exists: {zip_path.name}")
            filepath.unlink()
            return zip_path

        self._log(f"  Packing to zip: {filepath.name}")
        try:
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
                zf.write(filepath, filepath.name)

            orig_size = filepath.stat().st_size
            new_size = zip_path.stat().st_size
            savings = (1 - new_size / orig_size) * 100 if orig_size > 0 else 0
            self._log(f"  zip done: {zip_path.name} ({savings:.0f}% smaller)")

            filepath.unlink()
            return zip_path

        except (OSError, zipfile.BadZipFile) as e:
            self._log(f"  zip pack error: {e}")
            if zip_path.exists():
                zip_path.unlink()
            return filepath

    def _reprocess_archive(self, filepath):
        """Extract a .7z or .rar, convert/repack contents optimally, clean up."""
        ext = filepath.suffix.lower()
        stem = filepath.stem
        parent = filepath.parent
        tmp_dir = parent / f".extract_{stem}"

        try:
            tmp_dir.mkdir(parents=True, exist_ok=True)

            # Extract
            self._log(f"  Extracting {filepath.name}...")
            if ext == ".7z":
                with py7zr.SevenZipFile(filepath, "r") as sz:
                    sz.extractall(tmp_dir)
            elif ext == ".rar":
                with rarfile.RarFile(filepath, "r") as rf:
                    rf.extractall(tmp_dir)
            else:
                return filepath  # Shouldn't happen

            # Collect extracted files
            extracted = list(tmp_dir.rglob("*"))
            extracted_files = [f for f in extracted if f.is_file()]

            if not extracted_files:
                self._log(f"  Archive was empty, keeping original")
                shutil.rmtree(tmp_dir, ignore_errors=True)
                return filepath

            # Classify contents
            has_disc = any(f.suffix.lower() in DISC_IMAGE_EXTENSIONS for f in extracted_files)
            has_cue = any(f.suffix.lower() == ".cue" for f in extracted_files)
            has_gdi = any(f.suffix.lower() == ".gdi" for f in extracted_files)

            if has_disc and (has_cue or has_gdi):
                # Disc image with cue/gdi sheet — convert to CHD
                cue_file = next((f for f in extracted_files if f.suffix.lower() in (".cue", ".gdi")), None)
                if cue_file:
                    chd_name = stem + ".chd"
                    chd_path = parent / chd_name
                    self._log(f"  Converting disc image to CHD...")
                    try:
                        cmd_type = "createcd" if cue_file.suffix.lower() in (".cue", ".gdi") else "createdvd"
                        result = subprocess.run(
                            [CHDMAN_BIN, cmd_type, "-i", str(cue_file), "-o", str(chd_path)],
                            capture_output=True, text=True, timeout=600,
                        )
                        if result.returncode == 0:
                            orig_size = filepath.stat().st_size
                            chd_size = chd_path.stat().st_size
                            savings = (1 - chd_size / orig_size) * 100 if orig_size > 0 else 0
                            self._log(f"  CHD done: {chd_name} ({savings:.0f}% smaller)")
                            filepath.unlink()  # Remove original archive
                            shutil.rmtree(tmp_dir, ignore_errors=True)
                            return chd_path
                        else:
                            self._log(f"  CHD convert failed: {result.stderr[:120]}")
                    except (subprocess.TimeoutExpired, OSError) as e:
                        self._log(f"  CHD convert error: {e}")
                    # Fallthrough — CHD failed, repack as zip instead

            # Repack all extracted files as .zip (RetroArch can't handle .7z LZMA2)
            zip_path = parent / (stem + ".zip")
            self._log(f"  Repacking as zip...")
            try:
                with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
                    for f in extracted_files:
                        zf.write(f, f.name)

                orig_size = filepath.stat().st_size
                new_size = zip_path.stat().st_size
                savings = (1 - new_size / orig_size) * 100 if orig_size > 0 else 0
                self._log(f"  zip done: {zip_path.name} ({savings:.0f}% smaller)")
                filepath.unlink()
                shutil.rmtree(tmp_dir, ignore_errors=True)
                return zip_path
            except (OSError, zipfile.BadZipFile) as e:
                self._log(f"  zip repack error: {e}")
                if zip_path.exists():
                    zip_path.unlink()

            # Cleanup on failure — keep original
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return filepath

        except Exception as e:
            self._log(f"  Reprocess error: {e}")
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return filepath

    # ========================================================================
    # TRICKLE PUSH — push each file to NAS as it's downloaded
    # ========================================================================

    def _is_nas_reachable(self):
        """Check if the NAS is reachable via SSH. Caches result for 60 seconds."""
        now = time.time()
        if self._nas_reachable is not None and (now - self._nas_check_time) < 60:
            return self._nas_reachable

        if not NAS_HOST or not NAS_USER or not NAS_EXPORT:
            self._nas_reachable = False
            self._nas_check_time = now
            return False

        try:
            result = subprocess.run(
                ["ssh", "-i", os.path.expanduser("~/.ssh/id_ed25519"),
                 "-o", "ConnectTimeout=5", "-o", "BatchMode=yes",
                 "-o", "StrictHostKeyChecking=accept-new",
                 f"{NAS_USER}@{NAS_HOST}", "echo ok"],
                capture_output=True, text=True, timeout=10,
            )
            self._nas_reachable = (result.returncode == 0)
        except Exception:
            self._nas_reachable = False

        self._nas_check_time = now
        if not self._nas_reachable:
            self._log("[TRICKLE] NAS not reachable, skipping push")
        return self._nas_reachable

    def _trickle_push(self, filepath):
        """Push a single downloaded file directly to the NAS via SCP.

        - Checks NAS reachability (cached for 60s)
        - Uses SCP to push the file directly to NAS_EXPORT/NAS_ROM_SUBDIR/<system>/
        - Sets file permissions so SSHFS on device can read it
        - Deletes the local file on success
        - Logs success/failure

        Returns True if the file was pushed and deleted, False otherwise.
        """
        if not self._trickle_enabled:
            return False

        if not filepath.exists():
            return False

        # Check NAS reachability
        if not self._is_nas_reachable():
            return False

        # Compute the relative path from staging dir
        # e.g., filepath = /home/user/nas-staging/snes/game.7z
        #        rel_path = snes/game.7z
        try:
            rel_path = filepath.relative_to(self.output_dir)
        except ValueError:
            self._log(f"[TRICKLE] Cannot compute relative path for {filepath}")
            return False

        ssh_key = os.path.expanduser("~/.ssh/id_ed25519")
        ssh_target = f"{NAS_USER}@{NAS_HOST}"
        # Target path on NAS: NAS_EXPORT/NAS_ROM_SUBDIR/snes/game.7z
        target_dir = f"{NAS_EXPORT}/{NAS_ROM_SUBDIR}/{rel_path.parent}"

        self._log(f"[TRICKLE] Pushing {rel_path}...")

        try:
            # Ensure target directory exists on NAS
            subprocess.run(
                ["ssh", "-i", ssh_key,
                 "-o", "StrictHostKeyChecking=accept-new",
                 "-o", "ConnectTimeout=5",
                 ssh_target, f"mkdir -p \"{target_dir}\""],
                capture_output=True, text=True, timeout=10,
            )

            # Check if file already exists on NAS (--ignore-existing equivalent)
            filename = filepath.name
            check = subprocess.run(
                ["ssh", "-i", ssh_key,
                 "-o", "ConnectTimeout=5",
                 ssh_target,
                 f"test -f \"{target_dir}/{filename}\""],
                capture_output=True, text=True, timeout=10,
            )
            if check.returncode == 0:
                # File already exists on NAS — delete local and move on
                filepath.unlink()
                self._log(f"[TRICKLE] Already on NAS, cleaned local: {rel_path}")
                return True

            # SCP the file directly to NAS (modern SCP uses SFTP protocol
            # internally, so remote paths are used as-is — no shell escaping)
            scp_dst = f"{ssh_target}:{target_dir}/{filename}"

            result = subprocess.run(
                ["scp", "-i", ssh_key,
                 "-o", "StrictHostKeyChecking=accept-new",
                 "-o", "ConnectTimeout=10",
                 str(filepath), scp_dst],
                capture_output=True, text=True, timeout=600,
            )

            if result.returncode == 0:
                # Set permissions so SSHFS on device can read the file
                subprocess.run(
                    ["ssh", "-i", ssh_key,
                     "-o", "ConnectTimeout=5",
                     ssh_target,
                     f"chmod a+r \"{target_dir}/{filename}\""],
                    capture_output=True, text=True, timeout=10,
                )
                # Delete local file on success
                filepath.unlink()
                self._log(f"[TRICKLE] Pushed and cleaned: {rel_path}")
                return True
            else:
                self._log(f"[TRICKLE] SCP failed: {result.stderr[:120]}")
                return False

        except subprocess.TimeoutExpired:
            self._log(f"[TRICKLE] Push timed out for {rel_path}")
            return False
        except Exception as e:
            self._log(f"[TRICKLE] Push error: {e}")
            return False

    # ========================================================================
    # URL / DOWNLOAD HANDLING
    # ========================================================================

    def _url_to_filepath(self, url):
        # Handle POST form downloads: "POST|action_url|params|referer"
        if url.startswith("POST|"):
            parts = url.split("|", 3)
            referer = parts[3] if len(parts) > 3 else ""
            params = urllib.parse.parse_qs(parts[2]) if len(parts) > 2 else {}
            # Use the game name we'll extract during download, or a fallback
            # For now return a placeholder — download_file will set the real name
            return None  # Handled specially in download_file

        parsed = urllib.parse.urlparse(url)
        filename = urllib.parse.unquote(Path(parsed.path).name)
        if not filename:
            return None
        system = self._get_system_for_file(filename, url=url)
        return self.output_dir / system / filename

    def _init_browser(self):
        """Lazy-init Playwright browser for JS rendering.
        Also recovers if the browser process died unexpectedly."""
        if not self.js_mode:
            return
        # Check if existing browser is still alive
        if self._browser is not None:
            try:
                self._browser.contexts  # probe — throws if dead
                return  # browser is alive, nothing to do
            except Exception:
                self._log("  Browser process died, reinitializing...")
                self._pw = None
                self._browser = None
                self._page = None
        if self._pw is None:
            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch(headless=True)
            self._page = self._browser.new_page()
            self._page.set_extra_http_headers({
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                              "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
            })

    def _close_browser(self):
        """Clean up Playwright. Safe to call even if browser is already dead."""
        try:
            if self._browser:
                self._browser.close()
        except Exception:
            pass
        try:
            if self._pw:
                self._pw.stop()
        except Exception:
            pass
        self._pw = None
        self._browser = None
        self._page = None

    def _fetch_page_html(self, url):
        """Fetch page HTML. Uses requests first (fast), falls back to
        Playwright only if JS mode is on AND the requests fetch fails or
        returns a page with no links (sign of JS-rendered content)."""

        # Always try requests first — it's 10x faster than a headless browser
        html = None
        try:
            resp = self.session.get(url, timeout=30, allow_redirects=True)
            resp.raise_for_status()
            if "text/html" not in resp.headers.get("content-type", ""):
                return None
            html = resp.text
        except requests.RequestException as e:
            msg = str(e)
            if "SSLError" in msg or "SSL" in msg:
                msg = "SSL certificate error -- site has a broken cert chain"
            elif len(msg) > 120:
                msg = msg[:120] + "..."
            self._log(f"  Error fetching page: {msg}")

        # If requests got HTML, check if it has links — if yes, no need for JS
        if html:
            soup = BeautifulSoup(html, "html.parser")
            link_count = len(soup.find_all("a", href=True))
            if link_count > 2:
                return html  # Normal page, no need for browser rendering
            # Very few links — might be JS-rendered, fall through to Playwright

        # Fall back to Playwright if JS mode is on
        if self.js_mode:
            self._init_browser()
            try:
                self._page.goto(url, timeout=60000, wait_until="domcontentloaded")
                # Wait a bit for JS to render, but don't wait for every asset
                self._page.wait_for_timeout(3000)
                return self._page.content()
            except Exception as e:
                msg = str(e)
                if len(msg) > 120:
                    msg = msg[:120] + "..."
                self._log(f"  JS render error: {msg}")
                return html  # Return whatever requests got, even if sparse

        return html

    def crawl_page(self, url, depth=0):
        if self.stop_requested:
            return
        if depth > self.max_depth:
            return
        if url in self.visited_pages:
            return
        if not self._is_same_domain(url):
            return

        # Skip detail pages (game pages) if we already have a download from this URL.
        # This saves ~20-30s per game page on re-crawls.
        url_path = urllib.parse.urlparse(url).path.rstrip("/")
        last_seg = url_path.split("/")[-1] if url_path else ""
        if last_seg.isdigit() and self.downloaded_files:
            # Check if any already-downloaded file references this page URL
            already_have = any(url in dl for dl in self.downloaded_files)
            if already_have:
                self.visited_pages.add(url)
                self.pages_crawled = len(self.visited_pages)
                return

        short_url = url.replace(self.base_url, "~")
        self._log(f"Crawl: {short_url} (depth {depth})")

        html = self._fetch_page_html(url)
        if html is None:
            # Don't mark as visited so retries can try again
            return

        self.visited_pages.add(url)
        self.pages_crawled = len(self.visited_pages)

        soup = BeautifulSoup(html, "html.parser")
        links = set()
        pre_scan_count = len(self.discovered_files)  # track what's new on THIS page

        # --- Scan <a href> links ---
        for tag in soup.find_all("a", href=True):
            href = tag["href"].strip()
            if not href or href.startswith("#") or href.startswith("mailto:"):
                continue
            if href.startswith("javascript:"):
                continue

            full_url = self._normalize_url(href, url)
            if not self._is_same_domain(full_url):
                continue

            if self._is_downloadable(full_url):
                if full_url not in self.downloaded_files and full_url not in [f for f in self.discovered_files]:
                    name = urllib.parse.unquote(Path(full_url).name)
                    self._log(f"  Found: {name}")
                    self.discovered_files.append(full_url)
                    self.files_found = len(self.discovered_files) + len(self.downloaded_files)
            elif self._is_page(full_url):
                if full_url not in self.visited_pages:
                    links.add(full_url)

        # --- Scan <form> tags for download actions ---
        for form in soup.find_all("form"):
            action = form.get("action", "").strip()
            if not action or action == "#":
                continue
            method = (form.get("method", "GET") or "GET").upper()
            if method != "POST":
                continue

            action_url = self._normalize_url(action, url)

            # Collect hidden inputs as POST data
            form_data = {}
            for inp in form.find_all("input"):
                name = inp.get("name")
                val = inp.get("value", "")
                if name:
                    form_data[name] = val

            # Heuristic: if form has mediaId, downloadId, fileId, or similar,
            # it's likely a download form
            download_keys = {"mediaid", "downloadid", "fileid", "romid", "id", "gameid"}
            form_keys_lower = {k.lower() for k in form_data}
            if not form_keys_lower.intersection(download_keys):
                continue

            # Build a synthetic download entry: store as "POST|action_url|key=val&..."
            post_params = urllib.parse.urlencode(form_data)
            synthetic_url = f"POST|{action_url}|{post_params}|{url}"

            if synthetic_url not in self.downloaded_files and \
               synthetic_url not in [f for f in self.discovered_files]:
                # Try to get a name from the page title or form context
                title_tag = soup.find("title")
                page_title = title_tag.get_text(strip=True) if title_tag else ""
                h1 = soup.find("h1")
                game_name = h1.get_text(strip=True) if h1 else page_title
                # Strip common site name prefixes from titles
                for prefix in ["The Vault:", "The Vault", "Vimm's Lair:",
                               "Vimm's Lair", "CoolROM.com -"]:
                    if game_name.startswith(prefix):
                        game_name = game_name[len(prefix):]
                # Clean up the name for a filename
                game_name = re.sub(r'[<>:"/\\|?*]', '', game_name).strip()
                if not game_name:
                    game_name = f"download_{form_data.get('mediaId', form_data.get('id', 'unknown'))}"

                self._log(f"  Found (form): {game_name}")
                self.discovered_files.append(synthetic_url)
                self.files_found = len(self.discovered_files) + len(self.downloaded_files)

        # --- Scan Vimm's JS media array for additional disc mediaIds ---
        # Vimm embeds all disc data in: const media=[{"ID":5122,...},{"ID":13604,...}]
        # The form scanner captures only the default disc's mediaId.
        # This pass discovers additional discs for multi-disc games.
        for script in soup.find_all("script"):
            script_text = script.string or ""
            media_match = re.search(r'const\s+media\s*=\s*(\[.+?\]);', script_text)
            if not media_match:
                continue
            try:
                media_list = json.loads(media_match.group(1))
            except (json.JSONDecodeError, ValueError):
                continue
            if not isinstance(media_list, list) or len(media_list) <= 1:
                continue  # Single disc — already handled by form scanner

            # Find the form action URL from any download form on this page
            form_action = None
            for form in soup.find_all("form"):
                action = form.get("action", "").strip()
                method = (form.get("method", "GET") or "GET").upper()
                if action and action != "#" and method == "POST":
                    form_action = self._normalize_url(action, url)
                    break
            if not form_action:
                continue

            # Collect mediaIds already discovered by the form scanner
            existing_ids = set()
            for disc_url in self.discovered_files + list(self.downloaded_files):
                if not isinstance(disc_url, str) or not disc_url.startswith("POST|"):
                    continue
                disc_params = dict(urllib.parse.parse_qsl(disc_url.split("|", 3)[2]))
                mid = disc_params.get("mediaId")
                if mid:
                    existing_ids.add(mid)

            # Get game name for logging
            title_tag = soup.find("title")
            h1 = soup.find("h1")
            game_name = h1.get_text(strip=True) if h1 else (
                title_tag.get_text(strip=True) if title_tag else "")
            for prefix in ["The Vault:", "The Vault", "Vimm's Lair:",
                           "Vimm's Lair"]:
                if game_name.startswith(prefix):
                    game_name = game_name[len(prefix):]
            game_name = re.sub(r'[<>:"/\\|?*]', '', game_name).strip()

            for entry in media_list:
                if not isinstance(entry, dict):
                    continue
                mid = str(entry.get("ID", ""))
                if not mid or mid in existing_ids:
                    continue
                # Build synthetic POST URL for this disc
                disc_params = urllib.parse.urlencode({"mediaId": mid})
                disc_synthetic = f"POST|{form_action}|{disc_params}|{url}"
                if disc_synthetic not in self.downloaded_files and \
                   disc_synthetic not in self.discovered_files:
                    disc_label = entry.get("Label", f"Disc (media {mid})")
                    self._log(f"  Found (media): {game_name} — {disc_label}")
                    self.discovered_files.append(disc_synthetic)
                    self.files_found = len(self.discovered_files) + len(self.downloaded_files)
            break  # Only process the first media array found

        # --- Scan inline <script> for JS-embedded download URLs ---
        # Sites like CoolROM hide download URLs in JavaScript (e.g.,
        # window.location.href = "https://dl.coolrom.com.au/dl/ID/TOKEN/TS/")
        # instead of <a href> or <form> tags.
        if not self.discovered_files[pre_scan_count:]:
            for script in soup.find_all("script"):
                script_text = script.string or ""
                # Match URLs pointing to a download subdomain or /dl/ path
                for m in re.finditer(
                    r'(?:window\.(?:location\.href|open)\s*[=(]\s*["\'])'
                    r'(https?://dl\.[^"\']+)',
                    script_text
                ):
                    dl_url = m.group(1)
                    if dl_url in self.downloaded_files or dl_url in self.discovered_files:
                        continue
                    # Derive a filename from the page title
                    title_tag = soup.find("title")
                    h1 = soup.find("h1")
                    game_name = (h1.get_text(strip=True) if h1
                                 else title_tag.get_text(strip=True) if title_tag
                                 else "")
                    for prefix in ["CoolROM.com -", "CoolROM -"]:
                        if game_name.startswith(prefix):
                            game_name = game_name[len(prefix):].strip()
                    game_name = re.sub(r'[<>:"/\\|?*]', '', game_name).strip()
                    if game_name:
                        self._log(f"  Found (js): {game_name}")
                    else:
                        self._log(f"  Found (js): {dl_url}")
                    self.discovered_files.append(dl_url)
                    self.files_found = len(self.discovered_files) + len(self.downloaded_files)
                    break  # one download URL per page is enough
                if self.discovered_files[pre_scan_count:]:
                    break

        # --- Download immediately: files found on THIS page ---
        # Instead of queuing everything up for a post-crawl download phase,
        # we download as we go. Each system's games are downloaded before
        # moving on to the next system.
        page_downloads = self.discovered_files[pre_scan_count:]  # only new finds
        if page_downloads:
            self.status = "downloading"
            for dl_url in page_downloads:
                if self.stop_requested:
                    return
                self.files_total = len(self.discovered_files) - len(self.downloaded_files)
                dl_num = len(self.downloaded_files) + 1
                self.phase = f"Downloading ({dl_num} done, {self.files_total} remain)"

                if self.download_file(dl_url):
                    self.files_downloaded += 1
                else:
                    self.files_failed += 1

                # Politeness delay between downloads
                if not self.stop_requested:
                    time.sleep(self.delay)

            self.status = "crawling"
            self.phase = "Crawling for files..."

        self._save_state()

        # Split links into detail pages (likely have downloads) and nav/index pages.
        # Process detail pages FIRST so we find files before burning time on
        # more index pages.
        detail_pages = []
        nav_pages = []

        for link in sorted(links):
            if self._is_same_domain(link):
                link_path = urllib.parse.urlparse(link).path.rstrip("/")
                last_segment = link_path.split("/")[-1] if link_path else ""
                if last_segment.isdigit():
                    detail_pages.append(link)
                elif link.startswith(self.base_url) and self._is_child_or_pagination(link, url):
                    nav_pages.append(link)

        # Crawl detail/game pages first (they have download forms)
        for link in detail_pages:
            if self.stop_requested:
                return
            time.sleep(0.3)  # Light politeness delay for crawling (not downloading)
            self.crawl_page(link, self.max_depth)

        # Then crawl child nav/index pages (they lead to more detail pages)
        for link in nav_pages:
            if self.stop_requested:
                return
            time.sleep(0.3)
            self.crawl_page(link, depth + 1)

    def _browser_download(self, page_url):
        """Use Playwright to navigate to a page and click the Download button.

        Returns (filepath, filename) on success, or (None, None) on failure.
        Handles slow downloads (e.g., Vimm throttles to ~600 KB/s, so a 2GB
        file takes ~55 min). The download itself has no timeout — only the
        page navigation and button detection have timeouts.
        """
        if not HAS_PLAYWRIGHT:
            self._log("  Playwright not available for browser download")
            return None, None

        self._init_browser()
        import tempfile
        dl_dir = tempfile.mkdtemp(prefix="crawler_dl_")

        try:
            context = self._browser.new_context(accept_downloads=True)
            context.set_extra_http_headers({
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                              "AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36",
            })
            page = context.new_page()
            # Only apply default timeout to navigation/element finding, not downloads
            page.set_default_navigation_timeout(30000)
            page.set_default_timeout(30000)

            self._log(f"  Browser: navigating to {page_url}")
            page.goto(page_url, timeout=30000, wait_until="domcontentloaded")
            page.wait_for_timeout(3000)

            # Look for a download button/submit
            download_btn = None
            for selector in [
                'button:has-text("Download")',
                'input[type="submit"][value*="Download"]',
                'a:has-text("Download")',
                'button[type="submit"]',
                'form[method="POST"] button',
            ]:
                try:
                    btn = page.locator(selector).first
                    if btn.is_visible(timeout=2000):
                        download_btn = btn
                        break
                except Exception:
                    continue

            if not download_btn:
                self._log("  Browser: no download button found")
                context.close()
                return None, None

            # 120s for download event to START (not finish) — covers slow
            # server responses and redirect chains
            self._log("  Browser: clicking download button...")
            with page.expect_download(timeout=120000) as dl_info:
                download_btn.click()

            download = dl_info.value
            filename = download.suggested_filename
            save_path = os.path.join(dl_dir, filename)

            # save_as() waits for the download to complete — no timeout.
            # For large files on throttled servers this can take 60+ minutes.
            self._log(f"  Browser: downloading {filename}...")
            download.save_as(save_path)

            # Check if it actually succeeded (save_as raises on failure,
            # but double-check the file exists and has content)
            if not os.path.exists(save_path) or os.path.getsize(save_path) == 0:
                fail = download.failure()
                self._log(f"  Browser: download failed — {fail or 'empty file'}")
                context.close()
                return None, None

            size_mb = os.path.getsize(save_path) / (1024 * 1024)
            self._log(f"  Browser: downloaded {filename} ({size_mb:.1f} MB)")
            context.close()
            return save_path, filename

        except Exception as e:
            msg = str(e)
            if len(msg) > 120:
                msg = msg[:120] + "..."
            self._log(f"  Browser download error: {msg}")
            try:
                context.close()
            except Exception:
                pass
            if "closed" in msg.lower() or "crashed" in msg.lower():
                self._close_browser()
            return None, None

    def _do_download_request(self, url):
        """Initiate a download request — handles both GET URLs and POST form entries.

        Returns (response, filename_hint, referer_url) or raises on error.
        For form downloads, tries requests first, falls back to Playwright
        if the site has bot protection (400/403 response).
        """
        if url.startswith("POST|"):
            parts = url.split("|", 3)
            action_url = parts[1]
            params_str = parts[2] if len(parts) > 2 else ""
            referer = parts[3] if len(parts) > 3 else ""
            form_data = dict(urllib.parse.parse_qsl(params_str))

            headers = {}
            if referer:
                headers["Referer"] = referer

            # Try the direct POST first (works on most sites)
            try:
                resp = self.session.post(action_url, data=form_data,
                                         headers=headers, stream=True, timeout=300)
                if resp.status_code in (400, 403, 429, 503):
                    # Bot protection detected — fall back to Playwright
                    resp.close()
                    raise requests.RequestException(
                        f"Bot protection (HTTP {resp.status_code})")

                resp.raise_for_status()

                cd = resp.headers.get("content-disposition", "")
                filename = None
                if "filename=" in cd:
                    match = re.search(r'filename[*]?=["\']?([^"\';\r\n]+)', cd)
                    if match:
                        filename = urllib.parse.unquote(match.group(1).strip())

                # Sanity check: if response is HTML, it's probably an error page
                ct = resp.headers.get("content-type", "")
                if "text/html" in ct:
                    resp.close()
                    raise requests.RequestException("Got HTML instead of file")

                return resp, filename, referer

            except requests.RequestException as e:
                # Fall back to Playwright browser download
                self._log(f"  Direct POST blocked: {e}")
                if not referer:
                    raise

                self._log("  Trying browser download (Playwright)...")
                saved_path, filename = self._browser_download(referer)
                if saved_path and os.path.exists(saved_path):
                    # Return a fake response-like wrapper so download_file
                    # can handle it uniformly
                    return _FileResponse(saved_path), filename, referer
                else:
                    raise requests.RequestException(
                        "Browser download also failed")
        else:
            resp = self.session.get(url, stream=True, timeout=300)
            resp.raise_for_status()

            cd = resp.headers.get("content-disposition", "")
            filename = None
            if "filename=" in cd:
                match = re.search(r'filename[*]?=["\']?([^"\';\r\n]+)', cd)
                if match:
                    filename = urllib.parse.unquote(match.group(1).strip())

            return resp, filename, url

    def download_file(self, url):
        if url in self.downloaded_files:
            return True

        # For POST form downloads, we don't know the filepath until we start.
        # Same for extensionless download URLs (e.g., dl.coolrom.com.au/dl/ID/TOKEN/)
        # where the real filename comes from Content-Disposition.
        is_form_download = url.startswith("POST|")
        is_extensionless = (not is_form_download
                            and not self._is_downloadable(url))

        if not is_form_download and not is_extensionless:
            filepath = self._url_to_filepath(url)
            if filepath is None:
                return False

            name = filepath.name
            self.current_file = name
            self.current_progress = 0
            filepath.parent.mkdir(parents=True, exist_ok=True)

            # --- Dedup check ---
            filepath, should_download = self._dedup_filepath(filepath, url)
            if not should_download:
                return True
            name = filepath.name
        else:
            name = "form download..."
            self.current_file = name
            self.current_progress = 0

        self._log(f"Downloading: {name}")

        try:
            resp, server_filename, referer = self._do_download_request(url)

            # For form/extensionless downloads, determine filepath from server response
            if is_form_download or is_extensionless:
                if server_filename:
                    fname = server_filename
                elif is_form_download:
                    # Fallback: extract game name from the synthetic URL
                    parts = url.split("|", 3)
                    params = dict(urllib.parse.parse_qsl(parts[2])) if len(parts) > 2 else {}
                    referer_url = parts[3] if len(parts) > 3 else ""
                    fname = f"download_{params.get('mediaId', params.get('id', 'unknown'))}"
                    # Add extension from content-type if possible
                    ct = resp.headers.get("content-type", "")
                    if "zip" in ct:
                        fname += ".zip"
                    elif "octet-stream" in ct:
                        fname += ".bin"
                else:
                    # Extensionless URL — derive name from URL path or content-type
                    path_name = Path(urllib.parse.urlparse(url).path.rstrip("/")).name
                    ct = resp.headers.get("content-type", "")
                    ext = ".zip" if "zip" in ct else ".bin" if "octet" in ct else ""
                    fname = f"{path_name}{ext}" if path_name else f"download{ext}"

                system = self._get_system_for_file(fname, url=referer)
                filepath = self.output_dir / system / fname
                filepath.parent.mkdir(parents=True, exist_ok=True)
                name = filepath.name
                self.current_file = name
                self._log(f"  Filename: {name}")

                # Dedup check for form/extensionless downloads
                filepath, should_download = self._dedup_filepath(filepath, url)
                if not should_download:
                    resp.close()
                    return True
                name = filepath.name

            total = int(resp.headers.get("content-length", 0))
            downloaded = 0
            start_time = time.time()
            tmp_path = filepath.with_suffix(filepath.suffix + ".part")

            with open(tmp_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=1024 * 64):
                    if self.stop_requested:
                        tmp_path.unlink(missing_ok=True)
                        return False
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        self.bytes_downloaded += len(chunk)

                        if total > 0:
                            self.current_progress = int(downloaded / total * 100)
                            elapsed = time.time() - start_time
                            if elapsed > 0:
                                speed = downloaded / elapsed
                                if speed > 1024 * 1024:
                                    self.current_speed = f"{speed/1024/1024:.1f} MB/s"
                                else:
                                    self.current_speed = f"{speed/1024:.0f} KB/s"

            tmp_path.rename(filepath)
            size_mb = downloaded / 1024 / 1024
            self._log(f"  Done: {name} ({size_mb:.1f} MB)")
            self.current_progress = 100

            # If this archive landed in "other", peek inside to reclassify
            filepath = self._reclassify_archive(filepath, url)

            # Post-process: convert to optimal format (CHD for disc, 7z ultra for ROMs)
            filepath = self._post_process(filepath)
            name = filepath.name

            # Compute hash BEFORE trickle push (file may be deleted after push)
            system = filepath.parent.name
            registry_key = f"{system}/{name}"
            if filepath.exists():
                file_hash = self._file_sha256(filepath)
            else:
                file_hash = "unknown"

            # Trickle push: send to NAS immediately if enabled
            self._trickle_push(filepath)

            # Multi-disc: if this is Disc 1, look for sibling discs + generate .m3u
            self._check_multi_disc(filepath)

            # Register in dedup registry with hash
            # (even if trickle-push deleted the local copy, we still record it)
            self.file_registry[registry_key] = {
                "url": url,
                "size": downloaded,
                "sha256": file_hash,
            }

            # Check if this exact content already exists under a different name
            if file_hash not in ("unknown",):
                for reg_key, reg_val in self.file_registry.items():
                    if reg_key != registry_key and reg_val.get("sha256") == file_hash:
                        self._log(f"  Note: identical content to {reg_key}")
                        break

            self.downloaded_files.add(url)
            self.failed_files.discard(url)
            self._save_state()
            return True

        except requests.RequestException as e:
            msg = str(e)
            if len(msg) > 150:
                msg = msg[:150] + "..."
            self._log(f"  FAIL: {name} -- {msg}")
            self.failed_files.add(url)
            self._save_state()
            try:
                tmp_path = filepath.with_suffix(filepath.suffix + ".part")
                if tmp_path.exists():
                    tmp_path.unlink()
            except (NameError, UnboundLocalError):
                pass  # filepath was never set (form download failed early)
            return False

    def run(self):
        self.status = "crawling"
        self.phase = "Crawling for files..."
        system_label = self.system if self.system != "auto" else "auto-detect"
        self._log(f"Starting crawl: {self.base_url}")
        self._log(f"System: {system_label}, Depth: {self.max_depth}, Delay: {self.delay}s")
        self._log(f"Saving to: {self.output_dir}/<system>/")
        if self._trickle_enabled:
            self._log(f"Trickle push: ENABLED (files push to NAS as they download)")

        self.crawl_page(self.base_url)

        if self.stop_requested:
            self.status = "stopped"
            self._log("Stopped by user.")
            self._close_browser()
            return

        # Mop-up pass: any discovered files that weren't downloaded during the crawl
        # (safety net — most downloads happen inline during crawl_page now)
        remaining = []
        seen = set()
        for url in self.discovered_files:
            if url not in seen and url not in self.downloaded_files:
                remaining.append(url)
                seen.add(url)

        self.files_found = len(self.discovered_files) + len(self.downloaded_files)

        self._log(f"Crawl complete: {self.pages_crawled} pages, {self.files_found} files found, "
                  f"{self.files_downloaded} downloaded during crawl")

        if remaining:
            self._log(f"Mop-up: {len(remaining)} files still need downloading")
            self.status = "downloading"
            for i, url in enumerate(remaining, 1):
                if self.stop_requested:
                    self.status = "stopped"
                    self._log("Stopped by user.")
                    return

                self.phase = f"Mop-up {i}/{len(remaining)}"

                if self.download_file(url):
                    self.files_downloaded += 1
                else:
                    self.files_failed += 1

                if i < len(remaining) and not self.stop_requested:
                    time.sleep(self.delay)

        # Post-crawl: sweep local staging for any multi-disc games needing .m3u
        self._sweep_m3u()

        self.status = "complete"
        self.phase = "All downloads complete"
        summary = f"Done: {self.files_downloaded} downloaded, {self.files_failed} failed"
        if self.dupes_skipped > 0:
            summary += f", {self.dupes_skipped} duplicates skipped"
        self._log(summary)
        self.current_file = ""
        self._close_browser()

    def get_progress(self):
        return {
            "status": self.status,
            "phase": self.phase,
            "pages_crawled": self.pages_crawled,
            "files_found": self.files_found,
            "files_total": self.files_total,
            "files_downloaded": self.files_downloaded,
            "files_failed": self.files_failed,
            "dupes_skipped": self.dupes_skipped,
            "current_file": self.current_file,
            "current_progress": self.current_progress,
            "current_speed": self.current_speed,
            "bytes_total": self.bytes_downloaded,
            "log": self.log_lines[-80:],
        }


# ============================================================================
# EMULATOR AUTO-INSTALLER
# ============================================================================

# System slug -> required flatpak/emulator
SYSTEM_EMULATORS = {
    "nes":          ("org.libretro.RetroArch", "RetroArch (NES core)"),
    "snes":         ("org.libretro.RetroArch", "RetroArch (SNES core)"),
    "gb":           ("org.libretro.RetroArch", "RetroArch (GB core)"),
    "gbc":          ("org.libretro.RetroArch", "RetroArch (GBC core)"),
    "gba":          ("io.mgba.mGBA", "mGBA"),
    "n64":          ("org.libretro.RetroArch", "RetroArch (N64 core)"),
    "nds":          ("net.kuribo64.melonDS", "melonDS"),
    "3ds":          ("org.azahar_emu.Azahar", "Azahar (3DS)"),
    "gc":           ("org.DolphinEmu.dolphin-emu", "Dolphin"),
    "wii":          ("org.DolphinEmu.dolphin-emu", "Dolphin"),
    "psx":          (None, "DuckStation (AppImage)"),  # Already installed as AppImage
    "ps2":          ("net.pcsx2.PCSX2", "PCSX2"),
    "ps3":          ("net.rpcs3.RPCS3", "RPCS3"),
    "psp":          ("org.ppsspp.PPSSPP", "PPSSPP"),
    "genesis":      ("org.libretro.RetroArch", "RetroArch (Genesis core)"),
    "saturn":       ("org.libretro.RetroArch", "RetroArch (Saturn core)"),
    "dreamcast":    ("org.flycast.Flycast", "Flycast"),
    "segacd":       ("org.libretro.RetroArch", "RetroArch (Sega CD core)"),
    "mastersystem": ("org.libretro.RetroArch", "RetroArch (SMS core)"),
    "gamegear":     ("org.libretro.RetroArch", "RetroArch (GG core)"),
    "arcade":       ("org.mamedev.MAME", "MAME"),
    "pcengine":     ("org.libretro.RetroArch", "RetroArch (PCE core)"),
    "atari2600":    ("org.libretro.RetroArch", "RetroArch (Atari core)"),
    "atari7800":    ("org.libretro.RetroArch", "RetroArch (Atari core)"),
    "atarilynx":    ("org.libretro.RetroArch", "RetroArch (Lynx core)"),
    "xbox":         ("app.xemu.xemu", "xemu"),
    "scummvm":      ("org.scummvm.ScummVM", "ScummVM"),
}


def _ensure_emulators(systems, log):
    """Check if required emulators are installed on the device, install if missing."""
    needed_flatpaks = {}  # flatpak_id -> display_name
    for system in systems:
        emu = SYSTEM_EMULATORS.get(system)
        if not emu:
            continue
        flatpak_id, name = emu
        if flatpak_id and flatpak_id not in needed_flatpaks:
            needed_flatpaks[flatpak_id] = name

    if not needed_flatpaks:
        log("[EMU] All systems use existing emulators")
        return

    if not DEVICE_HOST:
        log("[EMU] No DEVICE_HOST configured, skipping emulator check")
        return

    # Check which are already installed
    try:
        result = subprocess.run(
            ["ssh", DEVICE_HOST, "flatpak list --app --columns=application"],
            capture_output=True, text=True, timeout=15
        )
        installed = set(result.stdout.strip().split("\n"))
    except Exception:
        log("[EMU] Couldn't check installed emulators")
        return

    missing = {fid: name for fid, name in needed_flatpaks.items()
               if fid not in installed}

    if not missing:
        log("[EMU] All required emulators already installed")
        return

    log(f"[EMU] Installing {len(missing)} new emulator(s)...")
    for flatpak_id, name in missing.items():
        log(f"[EMU]   Installing {name} ({flatpak_id})...")
        try:
            result = subprocess.run(
                ["ssh", DEVICE_HOST,
                 f"flatpak install --user -y flathub {flatpak_id}"],
                capture_output=True, text=True, timeout=300
            )
            if result.returncode == 0:
                log(f"[EMU]   Installed: {name}")
            else:
                log(f"[EMU]   Failed: {name} -- {result.stderr[:150]}")
        except subprocess.TimeoutExpired:
            log(f"[EMU]   Timeout installing {name}")
        except Exception as e:
            log(f"[EMU]   Error: {e}")

    log("[EMU] Emulator check complete")


# ============================================================================
# WEB SERVER
# ============================================================================

current_job = None
job_thread = None


def _human_bytes(b):
    for u in ["B", "KB", "MB", "GB", "TB"]:
        if b < 1024:
            return f"{b:.1f} {u}"
        b /= 1024


class GUIHandler(http.server.BaseHTTPRequestHandler):

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/":
            self._serve_html()
        elif parsed.path == "/api/status":
            self._serve_status()
        else:
            self.send_error(404)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""

        if parsed.path == "/api/start":
            self._handle_start(body)
        elif parsed.path == "/api/stop":
            self._handle_stop()
        elif parsed.path == "/api/push-nas":
            self._handle_push_nas()
        else:
            self.send_error(404)

    def _json(self, data, code=200):
        self.send_response(code)
        self.send_header("Content-type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def _serve_status(self):
        global current_job
        if current_job is None:
            self._json({"status": "idle", "log": []})
        else:
            self._json(current_job.get_progress())

    def _handle_start(self, body):
        global current_job, job_thread

        if current_job and current_job.status in ("crawling", "downloading"):
            self._json({"error": "Job already running"}, 409)
            return

        try:
            params = json.loads(body)
        except json.JSONDecodeError:
            self._json({"error": "Invalid JSON"}, 400)
            return

        url = params.get("url", "").strip()
        if not url:
            self._json({"error": "URL is required"}, 400)
            return
        if not url.startswith("http"):
            url = "https://" + url

        # If the source file has been modified since this process started,
        # restart the process to pick up code changes.
        if os.path.getmtime(__file__) > _SCRIPT_MTIME:
            os.environ["CRAWLER_AUTOSTART"] = json.dumps(params)
            self._json({"status": "restarting"})
            def _restart():
                time.sleep(0.5)
                os.execv(sys.executable, [sys.executable] + sys.argv)
            threading.Thread(target=_restart, daemon=False).start()
            return

        depth = int(params.get("depth", DEFAULT_DEPTH))
        delay = int(params.get("delay", DEFAULT_DELAY))
        system = params.get("system", "auto")
        js_mode = bool(params.get("js_mode", False))

        current_job = CrawlJob(
            url, STAGING_BASE, max_depth=depth, delay=delay,
            system=system, js_mode=js_mode,
        )

        def run_job():
            try:
                current_job.run()
            except Exception as e:
                current_job.status = "error"
                current_job._log(f"ERROR: {e}")

        job_thread = threading.Thread(target=run_job, daemon=True)
        job_thread.start()

        self._json({"status": "started", "output_dir": STAGING_BASE})

    def _handle_stop(self):
        global current_job
        if current_job and current_job.status in ("crawling", "downloading"):
            current_job.stop_requested = True
            self._json({"status": "stopping"})
        else:
            self._json({"status": "not_running"})

    def _handle_push_nas(self):
        """Push staged files to NAS.

        First tries to shell out to ../nas/nas-push.sh (the shared DeckDock
        NAS push script). If that script doesn't exist, falls back to the
        built-in push logic using config values.
        """
        def push():
            job = current_job
            log = job._log if job else lambda m: None
            staging = Path(STAGING_BASE)

            if not staging.exists():
                log("[NAS] No staging directory found")
                return

            # Find system subdirectories with files
            system_dirs = [d for d in staging.iterdir()
                          if d.is_dir() and d.name != ".crawler-state.json"
                          and any(d.iterdir())]
            if not system_dirs:
                log("[NAS] No files to push")
                return

            if not NAS_HOST or not NAS_USER or not NAS_EXPORT:
                log("[NAS] NAS config incomplete (need NAS_HOST, NAS_USER, NAS_EXPORT)")
                return

            ssh_key = os.path.expanduser("~/.ssh/id_ed25519")
            ssh_target = f"{NAS_USER}@{NAS_HOST}"

            # Check NAS is reachable
            log("[NAS] Connecting to NAS...")
            try:
                rc = subprocess.run(
                    ["ssh", "-i", ssh_key,
                     "-o", "ConnectTimeout=5", "-o", "BatchMode=yes",
                     "-o", "StrictHostKeyChecking=accept-new",
                     ssh_target, "echo ok"],
                    capture_output=True, timeout=10
                ).returncode
                if rc != 0:
                    log("[NAS] Can't reach NAS. Is it on?")
                    return
            except Exception:
                log("[NAS] Can't reach NAS. Is it on?")
                return

            # Pre-push: generate .m3u playlists for any multi-disc sets in staging
            disc_re = re.compile(r"^(.+?)\s*\(Disc\s*(\d+)\)", re.IGNORECASE)
            for sdir in system_dirs:
                games = {}
                existing_m3u = set()
                for f in sdir.iterdir():
                    if not f.is_file():
                        continue
                    if f.suffix.lower() == ".m3u":
                        existing_m3u.add(f.stem.lower())
                        continue
                    m = disc_re.match(f.stem)
                    if m:
                        base = m.group(1).strip()
                        disc_num = int(m.group(2))
                        games.setdefault(base, {})[disc_num] = f.name
                for base_name, disc_map in games.items():
                    if base_name.lower() in existing_m3u or len(disc_map) < 2:
                        continue
                    max_d = max(disc_map.keys())
                    if any(n not in disc_map for n in range(1, max_d + 1)):
                        continue  # Incomplete set
                    m3u_path = sdir / f"{base_name}.m3u"
                    lines = [disc_map[n] for n in sorted(disc_map.keys())]
                    m3u_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
                    log(f"[M3U] Created {sdir.name}/{base_name}.m3u ({len(lines)} discs)")

            # Re-scan system dirs (m3u files may have been added)
            system_dirs = [d for d in staging.iterdir()
                          if d.is_dir() and d.name != ".crawler-state.json"
                          and any(d.iterdir())]

            # Push each system directory via SCP
            total_files = 0
            for sdir in sorted(system_dirs):
                system = sdir.name
                files = [f for f in sdir.iterdir() if f.is_file()
                         and not f.name.endswith(".part")
                         and f.name != ".crawler-state.json"]
                if not files:
                    continue

                target_dir = f"{NAS_EXPORT}/{NAS_ROM_SUBDIR}/{system}"
                log(f"[NAS] Pushing {len(files)} files to {NAS_ROM_SUBDIR}/{system}/")

                # Ensure target dir exists on NAS
                subprocess.run(
                    ["ssh", "-i", ssh_key,
                     "-o", "StrictHostKeyChecking=accept-new",
                     "-o", "ConnectTimeout=5",
                     ssh_target, f"mkdir -p \"{target_dir}\""],
                    capture_output=True, timeout=10
                )

                pushed = 0
                for filepath in files:
                    filename = filepath.name
                    # Check if file already exists on NAS
                    check = subprocess.run(
                        ["ssh", "-i", ssh_key,
                         "-o", "ConnectTimeout=5",
                         ssh_target,
                         f"test -f \"{target_dir}/{filename}\""],
                        capture_output=True, timeout=10,
                    )
                    if check.returncode == 0:
                        log(f"[NAS]   Skipping (exists): {filename}")
                        filepath.unlink()
                        pushed += 1
                        continue

                    # SCP the file directly (modern SCP uses SFTP — no shell escaping)
                    scp_dst = f"{ssh_target}:{target_dir}/{filename}"

                    result = subprocess.run(
                        ["scp", "-i", ssh_key,
                         "-o", "StrictHostKeyChecking=accept-new",
                         "-o", "ConnectTimeout=10",
                         str(filepath), scp_dst],
                        capture_output=True, text=True, timeout=600,
                    )
                    if result.returncode == 0:
                        # Set permissions so SSHFS on device can read the file
                        subprocess.run(
                            ["ssh", "-i", ssh_key,
                             "-o", "ConnectTimeout=5",
                             ssh_target,
                             f"chmod a+r \"{target_dir}/{filename}\""],
                            capture_output=True, timeout=10,
                        )
                        filepath.unlink()
                        pushed += 1
                    else:
                        log(f"[NAS]   Error pushing {filename}: {result.stderr[:150]}")

                total_files += pushed
                log(f"[NAS]   Done: {NAS_ROM_SUBDIR}/{system}/ ({pushed} files)")

            # Remove empty directories (but not the staging root)
            for sdir in system_dirs:
                if sdir.exists() and not any(sdir.iterdir()):
                    sdir.rmdir()

            log(f"[NAS] Push complete: {total_files} files to NAS")

            # Check if device needs new emulators for these systems
            if DEVICE_HOST:
                pushed_systems = [d.name for d in system_dirs]
                if pushed_systems:
                    log("[EMU] Checking if device needs new emulators...")
                    _ensure_emulators(pushed_systems, log)

        threading.Thread(target=push, daemon=True).start()
        self._json({"status": "pushing"})

    def _serve_html(self):
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        self.wfile.write(HTML.encode())

    def log_message(self, format, *args):
        pass


HTML = r"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>DeckDock Crawler</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, 'Segoe UI', sans-serif; background: #0a0a14;
         color: #e0e0e0; min-height: 100vh; }
  .header { background: linear-gradient(135deg, #0f172a 0%, #1e1b4b 100%);
            padding: 28px; text-align: center; border-bottom: 2px solid #7c3aed; }
  .header h1 { color: #a78bfa; font-size: 1.8em; margin-bottom: 4px; }
  .header p { color: #666; font-size: 0.9em; }

  .form-section { max-width: 700px; margin: 24px auto; padding: 0 16px; }
  .input-group { margin-bottom: 12px; }
  .input-group label { display: block; font-size: 0.85em; color: #888;
    margin-bottom: 4px; font-weight: 600; }
  .url-input { width: 100%; padding: 14px 18px; font-size: 1.05em;
    border: 2px solid #2d2d4a; border-radius: 10px; background: #12122a;
    color: #fff; outline: none; transition: border-color 0.3s; }
  .url-input:focus { border-color: #7c3aed; }
  .url-input::placeholder { color: #555; }

  .options { display: flex; gap: 12px; }
  .options .input-group { flex: 1; }
  .opt-input { width: 100%; padding: 10px 14px; font-size: 0.95em;
    border: 2px solid #2d2d4a; border-radius: 8px; background: #12122a;
    color: #fff; outline: none; }
  .opt-input:focus { border-color: #7c3aed; }

  .btn-row { display: flex; gap: 10px; margin-top: 16px; }
  .btn { padding: 12px 28px; border-radius: 10px; border: none; font-weight: 700;
    font-size: 0.95em; cursor: pointer; transition: all 0.2s; }
  .btn-primary { background: #7c3aed; color: white; flex: 1; }
  .btn-primary:hover { background: #6d28d9; transform: translateY(-1px); }
  .btn-danger { background: #991b1b; color: white; }
  .btn-danger:hover { background: #b91c1c; }
  .btn-secondary { background: #1e3a5f; color: #93c5fd; }
  .btn-secondary:hover { background: #1e4a7f; }
  .btn:disabled { opacity: 0.4; cursor: not-allowed; transform: none; }

  .progress-section { max-width: 700px; margin: 0 auto; padding: 0 16px; }

  .stats { display: grid; grid-template-columns: repeat(5, 1fr); gap: 10px;
           margin-bottom: 16px; }
  .stat-card { background: #12122a; border-radius: 10px; padding: 14px;
    text-align: center; border: 1px solid #1e1e3a; }
  .stat-val { font-size: 1.6em; font-weight: 700; color: #a78bfa; }
  .stat-label { font-size: 0.75em; color: #666; margin-top: 2px; }

  .current-dl { background: #12122a; border-radius: 10px; padding: 16px;
    margin-bottom: 16px; border: 1px solid #1e1e3a; }
  .current-dl .filename { font-weight: 600; margin-bottom: 8px;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .progress-bar-wrap { background: #1e1e3a; border-radius: 6px; height: 10px;
    overflow: hidden; }
  .progress-bar { background: linear-gradient(90deg, #7c3aed, #a78bfa);
    height: 100%; transition: width 0.3s; border-radius: 6px; }
  .dl-meta { display: flex; justify-content: space-between; margin-top: 6px;
    font-size: 0.8em; color: #888; }

  .phase-badge { display: inline-block; padding: 4px 12px; border-radius: 20px;
    font-size: 0.85em; font-weight: 600; margin-bottom: 16px; }
  .phase-crawling { background: #1e3a5f; color: #93c5fd; }
  .phase-downloading { background: #1b4332; color: #6ee7b7; }
  .phase-complete { background: #064e3b; color: #34d399; }
  .phase-stopped { background: #451a03; color: #fbbf24; }
  .phase-error { background: #450a0a; color: #fca5a5; }
  .phase-idle { background: #1e1e3a; color: #888; }

  .log-section { max-width: 700px; margin: 16px auto; padding: 0 16px; }
  .log-box { background: #0c0c18; border: 1px solid #1e1e3a; border-radius: 10px;
    padding: 12px; height: 280px; overflow-y: auto; font-family: 'Cascadia Code',
    'Fira Code', monospace; font-size: 0.78em; line-height: 1.6; }
  .log-box::-webkit-scrollbar { width: 6px; }
  .log-box::-webkit-scrollbar-thumb { background: #333; border-radius: 3px; }
  .log-line { color: #8b8ba0; }
  .log-line.found { color: #6ee7b7; }
  .log-line.done { color: #34d399; }
  .log-line.fail { color: #fca5a5; }
  .log-line.crawl { color: #93c5fd; }
  .log-line.nas { color: #fbbf24; }
  .log-line.trickle { color: #c084fc; }

  @media (max-width: 700px) {
    .stats { grid-template-columns: repeat(3, 1fr); }
    .options { flex-direction: column; }
  }
  @media (max-width: 450px) {
    .stats { grid-template-columns: repeat(2, 1fr); }
  }
</style>
</head>
<body>

<div class="header">
  <h1>DeckDock Crawler</h1>
  <p>Enter a URL, sit back, watch it work</p>
</div>

<div class="form-section">
  <div class="input-group">
    <label>Target URL</label>
    <input type="text" id="urlInput" class="url-input"
           placeholder="https://example.com/files/" autofocus>
  </div>
  <div class="input-group">
    <label>Destination System</label>
    <select id="systemInput" class="opt-input">
      <option value="auto">Auto-detect from file type</option>
      <option value="psx">PlayStation (PSX)</option>
      <option value="ps2">PlayStation 2</option>
      <option value="ps3">PlayStation 3</option>
      <option value="psp">PSP</option>
      <option value="nes">NES</option>
      <option value="snes">SNES</option>
      <option value="n64">Nintendo 64</option>
      <option value="gb">Game Boy</option>
      <option value="gbc">Game Boy Color</option>
      <option value="gba">Game Boy Advance</option>
      <option value="nds">Nintendo DS</option>
      <option value="3ds">Nintendo 3DS</option>
      <option value="gc">GameCube</option>
      <option value="wii">Wii</option>
      <option value="switch">Nintendo Switch</option>
      <option value="genesis">Sega Genesis</option>
      <option value="saturn">Sega Saturn</option>
      <option value="dreamcast">Dreamcast</option>
      <option value="segacd">Sega CD</option>
      <option value="mastersystem">Master System</option>
      <option value="gamegear">Game Gear</option>
      <option value="arcade">Arcade / MAME</option>
      <option value="pcengine">PC Engine / TG16</option>
      <option value="atari2600">Atari 2600</option>
      <option value="atari7800">Atari 7800</option>
      <option value="atarilynx">Atari Lynx</option>
      <option value="xbox">Xbox</option>
      <option value="scummvm">ScummVM</option>
      <option value="other">Other / Unsorted</option>
    </select>
  </div>
  <div class="options">
    <div class="input-group">
      <label>Crawl Depth</label>
      <input type="number" id="depthInput" class="opt-input" value="DEPTH_PLACEHOLDER" min="1" max="10">
    </div>
    <div class="input-group">
      <label>Delay Between Downloads (seconds)</label>
      <input type="number" id="delayInput" class="opt-input" value="DELAY_PLACEHOLDER" min="1" max="60">
    </div>
  </div>
  <div class="input-group" style="margin-top:4px">
    <label style="display:inline-flex; align-items:center; gap:8px; cursor:pointer; color:#ccc">
      <input type="checkbox" id="jsMode" style="width:18px;height:18px;accent-color:#7c3aed">
      JavaScript Mode &mdash; renders pages in a headless browser (slower, but handles dynamic sites)
    </label>
  </div>
  <div class="btn-row">
    <button class="btn btn-primary" id="startBtn" onclick="startCrawl()">Start Crawl</button>
    <button class="btn btn-danger" id="stopBtn" onclick="stopCrawl()" disabled>Stop</button>
    <button class="btn btn-secondary" id="nasBtn" onclick="pushNAS()" disabled>Push to NAS</button>
  </div>
</div>

<div class="progress-section">
  <div style="text-align:center; margin-bottom: 12px;">
    <span class="phase-badge phase-idle" id="phaseBadge">Idle</span>
  </div>

  <div class="stats">
    <div class="stat-card">
      <div class="stat-val" id="statPages">0</div>
      <div class="stat-label">Pages Crawled</div>
    </div>
    <div class="stat-card">
      <div class="stat-val" id="statFound">0</div>
      <div class="stat-label">Files Found</div>
    </div>
    <div class="stat-card">
      <div class="stat-val" id="statDone">0</div>
      <div class="stat-label">Downloaded</div>
    </div>
    <div class="stat-card">
      <div class="stat-val" id="statFailed">0</div>
      <div class="stat-label">Failed</div>
    </div>
    <div class="stat-card">
      <div class="stat-val" id="statDupes">0</div>
      <div class="stat-label">Dupes Skipped</div>
    </div>
  </div>

  <div class="current-dl" id="currentDl" style="display:none">
    <div class="filename" id="dlFilename">&mdash;</div>
    <div class="progress-bar-wrap">
      <div class="progress-bar" id="dlProgress" style="width: 0%"></div>
    </div>
    <div class="dl-meta">
      <span id="dlPercent">0%</span>
      <span id="dlSpeed">&mdash;</span>
    </div>
  </div>
</div>

<div class="log-section">
  <div class="log-box" id="logBox"></div>
</div>

<script>
let polling = null;
let lastLogLen = 0;

function startCrawl() {
  const url = document.getElementById('urlInput').value.trim();
  if (!url) { alert('Enter a URL first'); return; }

  const depth = document.getElementById('depthInput').value;
  const delay = document.getElementById('delayInput').value;
  const system = document.getElementById('systemInput').value;
  const jsMode = document.getElementById('jsMode').checked;

  document.getElementById('startBtn').disabled = true;
  document.getElementById('stopBtn').disabled = false;
  document.getElementById('nasBtn').disabled = true;
  document.getElementById('logBox').innerHTML = '';
  lastLogLen = 0;

  fetch('/api/start', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({url, depth: parseInt(depth), delay: parseInt(delay), system, js_mode: jsMode})
  }).then(r => r.json()).then(data => {
    if (data.error) { alert(data.error); resetButtons(); return; }
    if (data.status === 'restarting') {
      document.getElementById('phaseBadge').textContent = 'Reloading\u2026';
      document.getElementById('phaseBadge').className = 'phase-badge phase-crawling';
      (function waitForRestart() {
        setTimeout(() => {
          fetch('/api/status').then(r => r.json()).then(d => {
            if (d.status === 'crawling' || d.status === 'downloading') {
              startPolling();
            } else { waitForRestart(); }
          }).catch(() => waitForRestart());
        }, 800);
      })();
      return;
    }
    startPolling();
  }).catch(e => { alert('Failed: ' + e); resetButtons(); });
}

function stopCrawl() {
  fetch('/api/stop', {method: 'POST'}).then(r => r.json());
  document.getElementById('stopBtn').disabled = true;
}

function pushNAS() {
  document.getElementById('nasBtn').disabled = true;
  document.getElementById('nasBtn').textContent = 'Pushing...';
  fetch('/api/push-nas', {method: 'POST'}).then(r => r.json()).then(() => {
    setTimeout(() => {
      document.getElementById('nasBtn').textContent = 'Push to NAS';
      document.getElementById('nasBtn').disabled = false;
    }, 5000);
  });
}

function startPolling() {
  if (polling) clearInterval(polling);
  polling = setInterval(updateStatus, 800);
}

function updateStatus() {
  fetch('/api/status').then(r => r.json()).then(data => {
    // Stats
    document.getElementById('statPages').textContent = data.pages_crawled || 0;
    document.getElementById('statFound').textContent = data.files_found || 0;
    document.getElementById('statDone').textContent = data.files_downloaded || 0;
    document.getElementById('statFailed').textContent = data.files_failed || 0;
    document.getElementById('statDupes').textContent = data.dupes_skipped || 0;

    // Phase badge
    const badge = document.getElementById('phaseBadge');
    badge.textContent = data.phase || data.status || 'Idle';
    badge.className = 'phase-badge phase-' + (data.status || 'idle');

    // Current download
    const dlBox = document.getElementById('currentDl');
    if (data.current_file && data.status === 'downloading') {
      dlBox.style.display = 'block';
      document.getElementById('dlFilename').textContent = data.current_file;
      document.getElementById('dlProgress').style.width = data.current_progress + '%';
      document.getElementById('dlPercent').textContent = data.current_progress + '%';
      document.getElementById('dlSpeed').textContent = data.current_speed || '\u2014';
    } else {
      dlBox.style.display = 'none';
    }

    // Log
    if (data.log && data.log.length > lastLogLen) {
      const logBox = document.getElementById('logBox');
      const newLines = data.log.slice(lastLogLen);
      for (const line of newLines) {
        const div = document.createElement('div');
        div.className = 'log-line';
        if (line.includes('Found:')) div.className += ' found';
        else if (line.includes('Done:')) div.className += ' done';
        else if (line.includes('FAIL:')) div.className += ' fail';
        else if (line.includes('Crawl:')) div.className += ' crawl';
        else if (line.includes('[NAS]')) div.className += ' nas';
        else if (line.includes('[TRICKLE]')) div.className += ' trickle';
        div.textContent = line;
        logBox.appendChild(div);
      }
      logBox.scrollTop = logBox.scrollHeight;
      lastLogLen = data.log.length;
    }

    // Job complete?
    if (data.status === 'complete' || data.status === 'stopped' || data.status === 'error') {
      clearInterval(polling);
      resetButtons();
      if (data.status === 'complete' && data.files_downloaded > 0) {
        document.getElementById('nasBtn').disabled = false;
      }
    }
  }).catch(() => {});
}

function resetButtons() {
  document.getElementById('startBtn').disabled = false;
  document.getElementById('stopBtn').disabled = true;
}

// Check if a job is already running on page load
fetch('/api/status').then(r => r.json()).then(data => {
  if (data.status === 'crawling' || data.status === 'downloading') {
    document.getElementById('startBtn').disabled = true;
    document.getElementById('stopBtn').disabled = false;
    startPolling();
  } else if (data.status === 'complete' && data.files_downloaded > 0) {
    document.getElementById('nasBtn').disabled = false;
    // Show final stats
    updateStatus();
  }
});
</script>
</body></html>"""

# Inject config defaults into the HTML template
HTML = HTML.replace("DEPTH_PLACEHOLDER", str(DEFAULT_DEPTH))
HTML = HTML.replace("DELAY_PLACEHOLDER", str(DEFAULT_DELAY))


if __name__ == "__main__":
    autostart_json = os.environ.pop("CRAWLER_AUTOSTART", None)
    if autostart_json:
        print(f"DeckDock Crawler GUI restarted (code reload)")
    else:
        print(f"DeckDock Crawler GUI running at http://localhost:{PORT}")
    print(f"Open in your browser to start crawling.")
    print(f"Downloads save to {STAGING_BASE}/<system>/")
    if TRICKLE_PUSH:
        print(f"Trickle push: ENABLED (files auto-push to NAS after download)")
    else:
        print(f"Trickle push: disabled (use 'Push to NAS' button for batch push)")

    class ReusableServer(http.server.HTTPServer):
        allow_reuse_address = True

    server = ReusableServer(("0.0.0.0", PORT), GUIHandler)

    # If restarting with autostart params, kick off the job immediately
    if autostart_json:
        try:
            params = json.loads(autostart_json)
            url = params.get("url", "").strip()
            if url:
                if not url.startswith("http"):
                    url = "https://" + url
                depth = int(params.get("depth", DEFAULT_DEPTH))
                delay = int(params.get("delay", DEFAULT_DELAY))
                system = params.get("system", "auto")
                js_mode = bool(params.get("js_mode", False))
                current_job = CrawlJob(
                    url, STAGING_BASE, max_depth=depth, delay=delay,
                    system=system, js_mode=js_mode,
                )
                def run_job():
                    try:
                        current_job.run()
                    except Exception as e:
                        current_job.status = "error"
                        current_job._log(f"ERROR: {e}")
                job_thread = threading.Thread(target=run_job, daemon=True)
                job_thread.start()
                print(f"Auto-started crawl: {url}")
        except Exception as e:
            print(f"Autostart failed: {e}")

    server.serve_forever()
