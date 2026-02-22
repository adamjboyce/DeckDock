#!/usr/bin/env python3
"""
DeckDock Resort-Other — Re-classify files stuck in the 'other/' directory.

Three-layer classification:
  Layer 1: Curated title-systems.json database (fast, offline)
  Layer 2: IGDB API fallback (if credentials configured)
  Layer 3: Binary header analysis (reads magic bytes from disc images/ROMs)

Modes:
  --dry-run   (default) List proposed moves without touching files
  --execute   Actually move files to their system directories
  --nas       Operate on NAS via SSH instead of local staging

Usage:
  python3 resort-other.py                          # dry-run on local staging
  python3 resort-other.py --nas                    # dry-run on NAS
  python3 resort-other.py --nas --execute          # move files on NAS
  python3 resort-other.py /path/to/other/          # dry-run on custom path
"""

import argparse
import json
import lzma
import os
import re
import struct
import subprocess
import sys
import time
import zlib
from pathlib import Path

import requests

# ============================================================================
# Config loader (same as crawler-gui.py)
# ============================================================================

def load_config():
    config = {}
    config_path = os.environ.get("DECKDOCK_CONFIG")
    if not config_path:
        script_dir = Path(__file__).resolve().parent
        config_path = script_dir.parent / "config.env"
        if not config_path.exists():
            config_path = Path.cwd() / "config.env"
    config_path = Path(config_path)
    if not config_path.exists():
        return config
    with open(config_path, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]
            value = value.replace("$HOME", os.path.expanduser("~"))
            value = os.path.expanduser(value)
            config[key] = value
    return config


_CONFIG = load_config()


def cfg(key, default=None):
    return _CONFIG.get(key, default)


# ============================================================================
# Title classification (shared logic with crawler-gui.py)
# ============================================================================

IGDB_CLIENT_ID = cfg("IGDB_CLIENT_ID", "")
IGDB_CLIENT_SECRET = cfg("IGDB_CLIENT_SECRET", "")

_IGDB_PLATFORM_MAP = {
    7: "psx", 8: "ps2", 21: "gc", 29: "genesis", 32: "saturn",
    23: "dreamcast", 78: "segacd", 50: "3do", 117: "cdi",
    62: "atarijaguar", 61: "atarilynx", 11: "xbox",
    18: "nes", 19: "snes", 4: "n64", 5: "wii",
    38: "psp", 52: "arcade", 86: "pcengine",
}

_MAPPED_SYSTEMS = set(_IGDB_PLATFORM_MAP.values())

_TITLE_DB_PATH = Path(__file__).resolve().parent / "title-systems.json"

_igdb_token = None
_igdb_token_expires = 0


def load_title_database():
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
        if system.startswith("_") or not isinstance(titles, list):
            continue
        for title in titles:
            pairs.append((title.lower(), system))
    pairs.sort(key=lambda p: len(p[0]), reverse=True)
    return pairs


def load_no_match_cache():
    if not _TITLE_DB_PATH.exists():
        return set()
    try:
        with open(_TITLE_DB_PATH, "r") as f:
            db = json.load(f)
        return set(db.get("_no_match", []))
    except (json.JSONDecodeError, OSError):
        return set()


def save_title_to_database(title_lower, system):
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


def save_no_match(title_lower):
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


def igdb_authenticate():
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


def igdb_lookup(title):
    token = igdb_authenticate()
    if not token:
        return None
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
        print(f"  IGDB lookup failed for '{title}': {e}")
        return None
    if not results:
        return None
    matched_systems = set()
    for game in results:
        for pid in game.get("platforms", []):
            slug = _IGDB_PLATFORM_MAP.get(pid)
            if slug:
                matched_systems.add(slug)
    if len(matched_systems) == 1:
        return matched_systems.pop()
    return None


def classify_title(filename, title_db, no_match_cache):
    """Classify a single file by title. Returns (system, source) or (None, None)."""
    stem = Path(filename).stem
    clean = re.sub(r'\s*\([^)]*\)', '', stem).strip()
    title_lower = clean.lower()

    if not title_lower:
        return None, None

    # Layer 1: Curated database
    for pattern, system in title_db:
        if pattern in title_lower:
            return system, "curated"

    # Layer 2: IGDB
    if not IGDB_CLIENT_ID or not IGDB_CLIENT_SECRET:
        return None, None
    if title_lower in no_match_cache:
        return None, None

    system = igdb_lookup(clean)
    if system:
        save_title_to_database(title_lower, system)
        return system, "igdb"
    else:
        save_no_match(title_lower)
        no_match_cache.add(title_lower)
        return None, None


# ============================================================================
# NAS operations via SSH
# ============================================================================

NAS_HOST = cfg("NAS_HOST", "")
NAS_USER = cfg("NAS_USER", "root")
NAS_EXPORT = cfg("NAS_EXPORT", "")
NAS_ROM_SUBDIR = cfg("NAS_ROM_SUBDIR", "roms")


def nas_list_other():
    """List files in the NAS other/ directory via SSH."""
    remote_path = f"{NAS_EXPORT}/{NAS_ROM_SUBDIR}/other"
    result = subprocess.run(
        ["ssh", "-n", f"{NAS_USER}@{NAS_HOST}", f"ls -1 \"{remote_path}\""],
        capture_output=True, text=True, timeout=15,
    )
    if result.returncode != 0:
        print(f"ERROR: Failed to list NAS other/: {result.stderr.strip()}")
        return []
    return [f.strip() for f in result.stdout.strip().split("\n") if f.strip()]


def nas_move_file(filename, system):
    """Move a file from other/ to system/ on the NAS via SSH."""
    remote_other = f"{NAS_EXPORT}/{NAS_ROM_SUBDIR}/other"
    remote_dest = f"{NAS_EXPORT}/{NAS_ROM_SUBDIR}/{system}"
    cmds = (
        f'mkdir -p "{remote_dest}" && '
        f'mv "{remote_other}/{filename}" "{remote_dest}/{filename}"'
    )
    result = subprocess.run(
        ["ssh", "-n", f"{NAS_USER}@{NAS_HOST}", cmds],
        capture_output=True, text=True, timeout=30,
    )
    return result.returncode == 0


# ============================================================================
# Binary Header Analysis (Layer 3)
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

    # Root directory record starts at PVD offset 0x9C (156 bytes into PVD)
    # Directory record layout:
    #   byte 0: length of record
    #   bytes 2-5: extent LBA (uint32 LE)
    #   bytes 10-13: data length (uint32 LE)
    root_record = pvd[0x9C:0x9C + 34]
    if len(root_record) < 34:
        return None

    root_lba = struct.unpack("<I", root_record[2:6])[0]
    root_len = struct.unpack("<I", root_record[10:14])[0]

    # Root directory is at root_lba * 2048
    root_offset = root_lba * 2048
    if root_offset + root_len > len(data):
        return None

    root_dir = data[root_offset:root_offset + root_len]

    # Scan directory entries for SYSTEM.CNF
    pos = 0
    while pos < len(root_dir):
        entry_len = root_dir[pos]
        if entry_len == 0:
            # Padding — skip to next sector boundary
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

        # File identifier length at byte 32, identifier starts at byte 33
        id_len = entry[32]
        if id_len > 0 and pos + 33 + id_len <= pos + entry_len:
            file_id = entry[33:33 + id_len].decode("ascii", errors="ignore")
            # ISO 9660 filenames end with ";1" (version number)
            file_name = file_id.split(";")[0].strip()

            if file_name.upper() == "SYSTEM.CNF":
                # Found it — read the file contents
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
    # Definitive check: read SYSTEM.CNF
    system_cnf = _read_system_cnf(data)
    if system_cnf:
        # PS2: "BOOT2 = cdrom0:\\..."
        # PSX: "BOOT = cdrom:\\..."
        if "BOOT2" in system_cnf:
            return "ps2"
        if "BOOT" in system_cnf:
            return "psx"

    # Fallback: PVD field heuristics (less reliable, but works with less data)
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
    # NES: iNES header "NES\x1a"
    if data[:4] == b"NES\x1a":
        return "nes"

    # N64: big-endian magic at offset 0 (native, byteswapped, or little-endian)
    if len(data) >= 4:
        n64_magic = struct.unpack(">I", data[0:4])[0]
        if n64_magic in (0x80371240, 0x37804012, 0x40123780):
            return "n64"

    # Genesis: "SEGA" at offset 0x100 with "GENESIS" or "MEGA DRIVE" in header
    if len(data) >= 0x120 and data[0x100:0x104] == b"SEGA":
        header_text = data[0x100:0x120]
        if b"GENESIS" in header_text or b"MEGA DRIVE" in header_text:
            return "genesis"

    # GameCube: magic 0xC2339F3D at offset 0x1C (big-endian)
    if len(data) >= 0x20:
        gc_magic = struct.unpack(">I", data[0x1C:0x20])[0]
        if gc_magic == 0xC2339F3D:
            return "gc"

    # Wii: magic 0x5D1C9EA3 at offset 0x18 (big-endian)
    if len(data) >= 0x1C:
        wii_magic = struct.unpack(">I", data[0x18:0x1C])[0]
        if wii_magic == 0x5D1C9EA3:
            return "wii"

    # --- Disc-based systems: sector 0 checks ---
    # Sega Saturn: "SEGA SEGASATURN" at offset 0
    if data[:15] == b"SEGA SEGASATURN":
        return "saturn"

    # Sega CD: "SEGADISCSYSTEM" or "SEGA DISCSYSTEM" at offset 0
    if data[:14] == b"SEGADISCSYSTEM" or data[:15] == b"SEGA DISCSYSTEM":
        return "segacd"

    # Dreamcast: "SEGA SEGAKATANA" or "SEGA SEGADC" at offset 0
    if data[:15] == b"SEGA SEGAKATANA":
        return "dreamcast"
    if len(data) >= 11 and data[:11] == b"SEGA SEGADC":
        return "dreamcast"

    # 3DO: first 4 bytes 0x01000000, "CD-ROM" at 0x28
    if len(data) >= 0x2E:
        if data[0:4] == b"\x01\x00\x00\x00" and data[0x28:0x2E] == b"CD-ROM":
            return "3do"

    # --- Sector 16 (0x8000) — ISO 9660 Primary Volume Descriptor ---
    pvd_offset = 0x8000
    if len(data) > pvd_offset + 0x240:
        pvd = data[pvd_offset:]

        # Standard PVD: type=1, id="CD001"
        if pvd[0:1] == b"\x01" and pvd[1:6] == b"CD001":
            system_id = pvd[8:40].decode("ascii", errors="ignore").strip()

            if "PLAYSTATION" in system_id:
                return _playstation_version(data)

        # CD-i: Volume Descriptor type 0xFF at sector 16
        if pvd[0:1] == b"\xff":
            return "cdi"

    # --- PC Engine CD: look for "PC Engine CD-ROM SYSTEM" ---
    if len(data) >= 0x20:
        if b"PC Engine" in data[:0x100] or b"PC-ENGINE" in data[:0x100]:
            return "pcengine"

    return None


def _parse_chd_header(chd_header_bytes):
    """Parse a CHD v5 file header to extract hunk size, map offset, and compression.

    Returns dict with keys: version, hunk_bytes, hunk_count, map_offset,
    compressors, logical_bytes, or None on failure.
    """
    if len(chd_header_bytes) < 124:
        return None

    # CHD magic: "MComprHD"
    if chd_header_bytes[:8] != b"MComprHD":
        return None

    # Header length and version
    header_len = struct.unpack(">I", chd_header_bytes[8:12])[0]
    version = struct.unpack(">I", chd_header_bytes[12:16])[0]

    if version == 5:
        # CHD v5 header layout (all big-endian):
        # 16-19: compressor[0] (4 bytes)
        # 20-23: compressor[1]
        # 24-27: compressor[2]
        # 28-31: compressor[3]
        # 32-39: logical_bytes (uint64)
        # 40-47: map_offset (uint64)
        # 48-55: meta_offset (uint64)
        # 56-59: hunk_bytes (uint32)
        # 60-63: unit_bytes (uint32)
        # 64-83: raw_sha1 (20 bytes)
        # 84-103: sha1 (20 bytes)
        # 104-123: parent_sha1 (20 bytes)
        compressors = []
        for i in range(4):
            c = chd_header_bytes[16 + i * 4:20 + i * 4]
            if c != b"\x00\x00\x00\x00":
                compressors.append(c)

        logical_bytes = struct.unpack(">Q", chd_header_bytes[32:40])[0]
        map_offset = struct.unpack(">Q", chd_header_bytes[40:48])[0]
        hunk_bytes = struct.unpack(">I", chd_header_bytes[56:60])[0]
        unit_bytes = struct.unpack(">I", chd_header_bytes[60:64])[0]

        hunk_count = (logical_bytes + hunk_bytes - 1) // hunk_bytes if hunk_bytes else 0

        return {
            "version": 5,
            "header_len": header_len,
            "hunk_bytes": hunk_bytes,
            "hunk_count": hunk_count,
            "unit_bytes": unit_bytes,
            "map_offset": map_offset,
            "compressors": compressors,
            "logical_bytes": logical_bytes,
        }

    # Unsupported CHD version
    return None


def _decompress_chd_hunk(data, offset, length, compressor):
    """Decompress a single CHD hunk using the specified compressor.

    Args:
        data: Raw file data containing the compressed hunk
        offset: Byte offset where compressed data starts
        length: Compressed length in bytes
        compressor: 4-byte compressor tag (e.g., b"lzma", b"zlib", b"flac")

    Returns:
        Decompressed bytes or None on failure.
    """
    compressed = data[offset:offset + length]
    if not compressed:
        return None

    tag = compressor.decode("ascii", errors="ignore").strip("\x00")

    try:
        if tag == "zlib" or tag == "cdzl":
            return zlib.decompress(compressed, -15)
        elif tag == "lzma" or tag == "cdlz":
            return lzma.decompress(compressed)
        elif tag == "none":
            return compressed
        elif tag == "zstd":
            try:
                import zstandard
                d = zstandard.ZstdDecompressor()
                return d.decompress(compressed)
            except ImportError:
                return None
    except Exception:
        # Decompression failed — compressed data might span different format
        pass

    # Try raw inflate as fallback (many CHD files use raw deflate)
    try:
        return zlib.decompress(compressed, -15)
    except Exception:
        pass
    try:
        return zlib.decompress(compressed)
    except Exception:
        pass

    return None


def _read_chd_sector_data(filepath_or_bytes, max_read=512 * 1024):
    """Extract the first ~64KB of disc data from a CHD file.

    For disc images, we need sector 0 (offset 0) and sector 16 (offset 0x8000)
    of the actual disc data, which is compressed inside the CHD container.

    Args:
        filepath_or_bytes: Path to CHD file, or raw bytes of CHD file start
        max_read: How many bytes of the CHD file to read (default 512KB)

    Returns:
        Raw disc data bytes (at least 64KB if successful) or None.
    """
    if isinstance(filepath_or_bytes, (str, Path)):
        try:
            with open(filepath_or_bytes, "rb") as f:
                raw = f.read(max_read)
        except OSError:
            return None
    else:
        raw = filepath_or_bytes

    info = _parse_chd_header(raw)
    if not info or info["version"] != 5:
        return None

    hunk_bytes = info["hunk_bytes"]
    map_offset = info["map_offset"]
    hunk_count = info["hunk_count"]
    compressors = info["compressors"]

    if not hunk_bytes or not compressors:
        # Uncompressed CHD — data starts right after the map
        # Map is hunk_count * 4 bytes for uncompressed
        return None

    # We need enough disc data to cover sector 16 PVD (0x8000 + 2KB = ~34KB)
    # One or two hunks should cover it (typical hunk size is 4-8 sectors * 2352 bytes)
    needed_bytes = 0x20000  # 128KB: sector 16 PVD + root dir + SYSTEM.CNF

    # CHD v5 compressed map: each entry is 12 bytes
    # [0-2]: compression type (3 bits) + length (24 bits) packed in first 3 bytes
    # Actually: each map entry = 12 bytes for v5 compressed
    #   Bytes 0-3: offset high bits / compression info
    #   Actually the v5 map format is:
    #     For compressed hunks: 12 bytes per entry
    #     uint8  compression (index into compressor list, or special values)
    #     uint24 complength
    #     uint48 offset
    #     uint16 crc16
    map_entry_size = 12
    map_end = map_offset + hunk_count * map_entry_size

    # Make sure we have enough raw data for the map + first few hunks
    if len(raw) < map_offset + map_entry_size:
        return None

    result = bytearray()
    hunks_needed = (needed_bytes + hunk_bytes - 1) // hunk_bytes

    for hunk_idx in range(min(hunks_needed, hunk_count)):
        entry_offset = map_offset + hunk_idx * map_entry_size

        if entry_offset + map_entry_size > len(raw):
            break

        entry = raw[entry_offset:entry_offset + map_entry_size]

        # Parse v5 map entry:
        # byte 0: compression type
        #   0-3 = index into compressors array
        #   4 = uncompressed (COMPRESSION_NONE)
        #   5 = self-referencing (COMPRESSION_SELF)
        #   6 = parent-referencing (COMPRESSION_PARENT)
        comp_type = entry[0]
        comp_length = (entry[1] << 16) | (entry[2] << 8) | entry[3]
        hunk_offset = struct.unpack(">Q", b"\x00\x00" + entry[4:10])[0]
        # crc16 = struct.unpack(">H", entry[10:12])[0]

        if hunk_offset + comp_length > len(raw):
            # Not enough raw data — would need more of the file
            break

        if comp_type == 4:
            # Uncompressed
            result.extend(raw[hunk_offset:hunk_offset + hunk_bytes])
        elif comp_type == 5:
            # Self-referencing — points to another hunk (skip)
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


def _ssh_read_bytes(remote_path, byte_count, timeout=60):
    """Read raw bytes from a file on NAS via SSH head -c."""
    try:
        result = subprocess.run(
            ["ssh", "-n", f"{NAS_USER}@{NAS_HOST}",
             f"head -c {byte_count} \"{remote_path}\""],
            capture_output=True, timeout=timeout,
        )
        if result.returncode != 0 or not result.stdout:
            return None
        return result.stdout
    except (subprocess.TimeoutExpired, OSError):
        return None


def _ssh_unzip_first_file(remote_path, byte_count=0x20000):
    """Extract the first file from a remote zip and return its header bytes.

    Uses 'busybox unzip -p' over SSH to stream the first file's contents to stdout,
    then reads up to byte_count bytes for header analysis.
    """
    # First, list the zip contents to find the first real file
    try:
        result = subprocess.run(
            ["ssh", "-n", f"{NAS_USER}@{NAS_HOST}",
             f"busybox unzip -l \"{remote_path}\" 2>/dev/null"],
            capture_output=True, text=True, timeout=15,
        )
        # busybox unzip returns 1 for large/zip64 files but still outputs listing
        if not result.stdout:
            return None, None
    except (subprocess.TimeoutExpired, OSError):
        return None, None

    # Parse busybox unzip -l output to find the first ROM/disc file
    # Format: "  12345  2024-01-01 12:00   filename with spaces.ext"
    line_re = re.compile(r"^\s*\d+\s+\d{2}-\d{2}-\d{4}\s+\d{2}:\d{2}\s{3}(.+)$")
    inner_name = None
    for line in result.stdout.splitlines():
        m = line_re.match(line)
        if not m:
            continue
        candidate = m.group(1).strip()
        if candidate.endswith("/") or not candidate:
            continue
        # Check if it looks like a ROM/disc file
        cext = Path(candidate).suffix.lower()
        if cext in (".bin", ".iso", ".img", ".cue", ".gdi",
                     ".nes", ".smc", ".sfc", ".n64", ".z64", ".v64",
                     ".md", ".gen", ".smd", ".32x", ".gg", ".sms",
                     ".gb", ".gbc", ".gba", ".nds", ".3ds", ".cia",
                     ".a26", ".a52", ".a78", ".lnx", ".jag", ".j64",
                     ".pce", ".ngp", ".col", ".sg", ".ws", ".wsc",
                     ".chd", ".pbp", ".cso", ".ciso"):
            inner_name = candidate
            break

    if not inner_name:
        return None, None

    # Extract that file to stdout and read header bytes
    try:
        result = subprocess.run(
            ["ssh", "-n", f"{NAS_USER}@{NAS_HOST}",
             f"busybox unzip -p \"{remote_path}\" \"{inner_name}\" 2>/dev/null | head -c {byte_count}"],
            capture_output=True, timeout=30,
        )
        if result.returncode not in (0, 141) or not result.stdout:
            # 141 = SIGPIPE from head, that's fine
            return None, None
        return result.stdout, inner_name
    except (subprocess.TimeoutExpired, OSError):
        return None, None


def _read_nas_header(filename, use_nas):
    """Read header bytes from a file on NAS for system identification.

    Handles:
    - CHD files: reads 512KB of container, decompresses to get sector data
    - Raw disc images (.iso, .bin, .img): reads first 128KB directly
    - ZIP archives: extracts first ROM file inside, reads its header bytes

    Returns raw disc/ROM data bytes or None.
    """
    ext = Path(filename).suffix.lower()
    remote_path = f"{NAS_EXPORT}/{NAS_ROM_SUBDIR}/other/{filename}"

    if ext == ".chd":
        raw = _ssh_read_bytes(remote_path, 512 * 1024)
        if not raw:
            return None
        return _read_chd_sector_data(raw)

    elif ext in (".iso", ".bin", ".img"):
        return _ssh_read_bytes(remote_path, 0x20000)

    elif ext == ".zip":
        data, inner_name = _ssh_unzip_first_file(remote_path)
        if data and inner_name:
            inner_ext = Path(inner_name).suffix.lower()
            # If the inner file is a CHD, we'd need full CHD parsing
            # (unlikely — CHDs aren't typically zipped)
            if inner_ext == ".chd":
                return _read_chd_sector_data(data)
            # CISO: decompress container to get raw disc data
            if inner_ext == ".ciso":
                return _decompress_ciso_header(data)
            return data
        return None

    return None


def _ensure_chd_identify_on_nas():
    """Copy chd-identify.py to the NAS if not already present."""
    local_script = Path(__file__).parent / "chd-identify.py"
    if not local_script.exists():
        return False
    try:
        # Check if already present
        result = subprocess.run(
            ["ssh", "-n", f"{NAS_USER}@{NAS_HOST}", "test -f /tmp/chd-identify.py"],
            capture_output=True, timeout=10,
        )
        if result.returncode != 0:
            subprocess.run(
                ["scp", str(local_script), f"{NAS_USER}@{NAS_HOST}:/tmp/chd-identify.py"],
                capture_output=True, timeout=15,
            )
        return True
    except (subprocess.TimeoutExpired, OSError):
        return False


def _classify_chd_via_metadata(filename, use_nas):
    """Classify a CHD file by reading its track metadata (CHT2 entries).

    CHD v5 metadata contains track type info:
    - MODE2_RAW track 1 → PSX
    - All AUDIO tracks → 3DO
    - MODE1_RAW → ambiguous (Saturn, Sega CD, Dreamcast, etc.)

    For NAS files, runs chd-identify.py on the NAS via SSH.
    """
    if use_nas:
        remote_path = f"{NAS_EXPORT}/{NAS_ROM_SUBDIR}/other/{filename}"
        _ensure_chd_identify_on_nas()
        try:
            result = subprocess.run(
                ["ssh", "-n", f"{NAS_USER}@{NAS_HOST}",
                 f"python3 /tmp/chd-identify.py \"{remote_path}\""],
                capture_output=True, text=True, timeout=15,
            )
            if not result.stdout.strip():
                return None
        except (subprocess.TimeoutExpired, OSError):
            return None

        # chd-identify.py outputs: "system\tfilename" or "unknown\tfilename"
        line = result.stdout.strip().splitlines()[0]
        system = line.split("\t")[0].strip()
        if system and system not in ("unknown", "unreadable", "error"):
            return system

    return None


def _decompress_ciso_header(data):
    """Extract raw disc data from a CISO (Compressed ISO) container.

    CISO format (PS2/GC variant):
    - Bytes 0-3: "CISO" magic
    - Bytes 4-7: block_size (uint32 LE)
    - Bytes 8+: 1-byte-per-block index (0x01=present, 0x00=absent)
    - Data aligned to 0x8000 offset, blocks stored sequentially

    Returns raw disc data (first block, enough for sector 0 + sector 16) or None.
    """
    if len(data) < 12 or data[:4] != b"CISO":
        return None

    block_size = struct.unpack("<I", data[4:8])[0]
    if block_size < 2048 or block_size > 64 * 1024 * 1024:
        return None

    # Data starts at offset 0x8000 (observed empirically — CISO aligns block
    # data to this offset, with index + padding between header and data)
    data_offset = 0x8000
    if len(data) <= data_offset:
        return None

    # Return raw disc data starting at the data offset
    # First block contains sector 0; if block_size >= 0x10000,
    # it also contains sector 16 (PVD at disc offset 0x8000)
    return data[data_offset:]


def _peek_nas_archive_system(filename):
    """Peek inside a remote archive to detect system from contained extensions.

    Like _peek_archive_system in crawler-gui.py but works over SSH.
    Strategy 1: Match inner file extensions to known system mappings.
    Strategy 2: Extract header bytes from .iso/.ciso inside the zip and
                run binary header analysis.
    Returns system slug or None.
    """
    ext = Path(filename).suffix.lower()
    remote_path = f"{NAS_EXPORT}/{NAS_ROM_SUBDIR}/other/{filename}"

    if ext == ".zip":
        try:
            result = subprocess.run(
                ["ssh", "-n", f"{NAS_USER}@{NAS_HOST}",
                 f"busybox unzip -l \"{remote_path}\" 2>/dev/null"],
                capture_output=True, text=True, timeout=15,
            )
            # busybox unzip returns 1 for large/zip64 files but still outputs listing
            if not result.stdout:
                return None
        except (subprocess.TimeoutExpired, OSError):
            return None

        # Import EXT_TO_SYSTEM equivalent for extension matching
        ext_map = {
            ".nes": "nes", ".unf": "nes",
            ".sfc": "snes", ".smc": "snes",
            ".gb": "gb", ".gbc": "gbc", ".gba": "gba",
            ".nds": "nds", ".3ds": "3ds", ".cia": "3ds", ".cci": "3ds",
            ".n64": "n64", ".z64": "n64", ".v64": "n64",
            ".gcm": "gc", ".gcz": "gc", ".rvz": "gc",
            ".wbfs": "wii", ".wad": "wii",
            ".nsp": "switch", ".xci": "switch",
            ".md": "genesis", ".smd": "genesis", ".gen": "genesis",
            ".gg": "gamegear", ".sms": "mastersystem",
            ".32x": "sega32x",
            ".a26": "atari2600", ".a78": "atari7800",
            ".lnx": "atarilynx", ".lyx": "atarilynx",
            ".jag": "atarijaguar", ".j64": "atarijaguar",
            ".pce": "pcengine",
            ".ngp": "neogeopocket", ".ngc": "neogeopocket",
            ".ws": "wonderswan", ".wsc": "wonderswan",
            ".col": "colecovision",
        }

        # Strategy 1: extension matching + special file detection
        # Parse busybox unzip -l format:
        #   "  1234  10-25-2024 21:22   filename with spaces.ext"
        line_re = re.compile(r"^\s*(\d+)\s+\d{2}-\d{2}-\d{4}\s+\d{2}:\d{2}\s{3}(.+)$")
        disc_image_name = None
        disc_image_size = 0
        for line in result.stdout.splitlines():
            m = line_re.match(line)
            if not m:
                continue
            file_size_str, candidate = m.group(1), m.group(2)
            candidate = candidate.strip()

            if candidate == "PS3_DISC.SFB":
                return "ps3"

            if candidate.endswith("/") or not candidate:
                continue

            inner_ext = Path(candidate).suffix.lower()
            system = ext_map.get(inner_ext)
            if system:
                return system

            # Track .iso/.ciso files for Strategy 2
            if inner_ext in (".iso", ".ciso"):
                disc_image_name = candidate
                try:
                    disc_image_size = int(file_size_str)
                except ValueError:
                    pass

        # Strategy 2: extract disc header bytes from .iso/.ciso inside zip
        if disc_image_name:
            inner_ext = Path(disc_image_name).suffix.lower()
            # For .ciso: need enough data for CISO header + first block (at 0x8000+)
            # For .iso: need first 128KB (sector 0 + sector 16 PVD + root dir)
            extract_size = 0x20000 if inner_ext == ".ciso" else 0x20000
            try:
                extract_result = subprocess.run(
                    ["ssh", "-n", f"{NAS_USER}@{NAS_HOST}",
                     f"busybox unzip -p \"{remote_path}\" \"{disc_image_name}\""
                     f" 2>/dev/null | head -c {extract_size}"],
                    capture_output=True, timeout=30,
                )
                raw_data = extract_result.stdout
                if raw_data and len(raw_data) > 256:
                    if inner_ext == ".ciso":
                        disc_data = _decompress_ciso_header(raw_data)
                    else:
                        disc_data = raw_data

                    if disc_data:
                        system = _system_from_header_bytes(disc_data)
                        if system:
                            # Size-based PS2 override: disc images > 1GB
                            # can't be PSX (CD-only, max ~700MB)
                            if system == "psx" and disc_image_size > 1_000_000_000:
                                return "ps2"
                            return system
            except (subprocess.TimeoutExpired, OSError):
                pass

    return None


def classify_header(filename, use_nas, local_dir=None):
    """Classify a file by reading its binary header (Layer 3).

    For NAS: reads headers via SSH for CHD/ISO/BIN/ZIP files.
    For local: reads headers directly from disk.
    Also peeks inside remote archives for extension-based detection.

    Returns (system, source_tag) or (None, None).
    """
    ext = Path(filename).suffix.lower()
    supported = (".chd", ".iso", ".bin", ".img", ".cue", ".zip",
                 ".nes", ".smc", ".sfc", ".n64", ".z64", ".v64",
                 ".md", ".gen", ".32x")
    if ext not in supported:
        return None, None

    if use_nas:
        # For zips: first try extension peek (fast), then header analysis (slower)
        if ext == ".zip":
            system = _peek_nas_archive_system(filename)
            if system:
                return system, "archive-peek"

        # For CHDs: try track metadata classification first (works for CD CHDs
        # where hunk decompression fails due to cdlz/cdzl/cdfl compressors)
        if ext == ".chd":
            system = _classify_chd_via_metadata(filename, use_nas=True)
            if system:
                return system, "chd-metadata"

        data = _read_nas_header(filename, use_nas=True)
    elif local_dir:
        filepath = Path(local_dir) / filename
        if not filepath.exists():
            return None, None
        if ext == ".chd":
            data = _read_chd_sector_data(filepath)
        elif ext in (".iso", ".bin", ".img"):
            try:
                with open(filepath, "rb") as f:
                    data = f.read(0x20000)
            except OSError:
                return None, None
        else:
            return None, None
    else:
        return None, None

    if not data:
        return None, None

    system = _system_from_header_bytes(data)
    if system:
        return system, "header"
    return None, None


# ============================================================================
# Main
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Re-classify files in the 'other/' directory (title + header analysis)."
    )
    parser.add_argument("path", nargs="?", default=None,
                        help="Path to the other/ directory (default: ~/nas-staging/other/)")
    parser.add_argument("--nas", action="store_true",
                        help="Operate on NAS via SSH instead of local files")
    parser.add_argument("--execute", action="store_true",
                        help="Actually move files (default is dry-run)")
    args = parser.parse_args()

    title_db = load_title_database()
    no_match_cache = load_no_match_cache()

    print(f"Title database: {len(title_db)} patterns loaded")
    if IGDB_CLIENT_ID and IGDB_CLIENT_SECRET:
        print("IGDB API: configured (Layer 2 fallback)")
    else:
        print("IGDB API: not configured (Layer 1 only)")
    print("Header analysis: enabled (Layer 3 for disc images/CHD)")
    print()

    # Get file list
    if args.nas:
        if not NAS_HOST:
            print("ERROR: NAS_HOST not configured in config.env")
            sys.exit(1)
        print(f"Reading NAS other/ directory via SSH ({NAS_USER}@{NAS_HOST})...")
        files = nas_list_other()
    else:
        other_dir = Path(args.path) if args.path else Path(cfg("STAGING_DIR", os.path.expanduser("~/nas-staging"))) / "other"
        if not other_dir.exists():
            print(f"ERROR: Directory not found: {other_dir}")
            sys.exit(1)
        print(f"Reading local directory: {other_dir}")
        files = [f.name for f in other_dir.iterdir() if f.is_file()]

    if not files:
        print("No files found in other/.")
        return

    files.sort()
    print(f"Found {len(files)} files\n")

    # Classify each file (Layer 1: curated, Layer 2: IGDB, Layer 3: header)
    moves = []
    unmatched = []
    local_dir = None if args.nas else str(other_dir)
    for filename in files:
        # Layers 1+2: title-based classification
        system, source = classify_title(filename, title_db, no_match_cache)
        if not system:
            # Layer 3: binary header analysis
            print(f"  Header scan: {filename}...", end="", flush=True)
            system, source = classify_header(filename, use_nas=args.nas, local_dir=local_dir)
            if system:
                print(f" -> {system}")
            else:
                print(" no match")
        if system:
            moves.append((filename, system, source))
        else:
            unmatched.append(filename)

    # Report
    if moves:
        print(f"{'=' * 70}")
        print(f"PROPOSED MOVES ({len(moves)} files)")
        print(f"{'=' * 70}")
        # Group by system
        by_system = {}
        for filename, system, source in moves:
            by_system.setdefault(system, []).append((filename, source))
        for system in sorted(by_system):
            print(f"\n  {system}/")
            for filename, source in sorted(by_system[system]):
                tag = f" [{source}]" if source in ("igdb", "header", "archive-peek") else ""
                print(f"    {filename}{tag}")
    else:
        print("No files could be classified.")

    if unmatched:
        print(f"\n{'=' * 70}")
        print(f"REMAINING IN OTHER/ ({len(unmatched)} files)")
        print(f"{'=' * 70}")
        for f in unmatched:
            print(f"  {f}")

    print(f"\nSummary: {len(moves)} classified, {len(unmatched)} unmatched")

    if not moves:
        return

    # Execute moves if requested
    if not args.execute:
        print("\nDry run — no files were moved. Use --execute to apply.")
        return

    print(f"\nMoving {len(moves)} files...")
    success = 0
    failed = 0
    for filename, system, source in moves:
        if args.nas:
            ok = nas_move_file(filename, system)
        else:
            src = other_dir / filename
            dest_dir = other_dir.parent / system
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / filename
            try:
                src.rename(dest)
                ok = True
            except OSError as e:
                print(f"  FAILED: {filename} -> {system}/: {e}")
                ok = False

        if ok:
            print(f"  {filename} -> {system}/")
            success += 1
        else:
            failed += 1

    print(f"\nDone: {success} moved, {failed} failed")


if __name__ == "__main__":
    main()
