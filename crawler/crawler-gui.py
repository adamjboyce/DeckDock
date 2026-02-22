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
import os
import re
import subprocess
import sys
import threading
import time
import urllib.parse
import zipfile
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
NAS_EXPORT = cfg("NAS_EXPORT", "")
NAS_MOUNT = cfg("NAS_MOUNT", "/tmp/nas-roms")
NAS_ROM_SUBDIR = cfg("NAS_ROM_SUBDIR", "roms")

# Trickle push from config
TRICKLE_PUSH = cfg("TRICKLE_PUSH", "false").lower() == "true"


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
    ".lnx": "atarilynx", ".jag": "atarijaguar",
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
        self._device_reachable = None  # None = unknown, True/False = cached result
        self._device_check_time = 0    # timestamp of last reachability check
        self._nas_mounted = False      # whether we've mounted the NAS this run

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

    def _normalize_url(self, url, page_url):
        if url.startswith("//"):
            url = f"{self.scheme}:{url}"
        elif url.startswith("/"):
            url = f"{self.scheme}://{self.domain}{url}"
        elif not url.startswith("http"):
            url = urllib.parse.urljoin(page_url, url)
        parsed = urllib.parse.urlparse(url)
        path = parsed.path.rstrip("/") or "/"
        return f"{parsed.scheme}://{parsed.netloc}{path}"

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
        """After downloading an archive, peek inside to reclassify if it landed in 'other'."""
        if filepath.parent.name != "other":
            return filepath  # Already classified

        ext = filepath.suffix.lower()
        if ext not in (".zip", ".7z", ".rar"):
            return filepath  # Not a peekable archive

        new_system = self._peek_archive_system(filepath)

        if not new_system:
            return filepath  # Can't determine, leave in "other"

        # Move to the correct system directory
        new_dir = self.output_dir / new_system
        new_dir.mkdir(parents=True, exist_ok=True)
        new_path = new_dir / filepath.name

        if new_path.exists():
            # Already exists in target — don't overwrite
            self._log(f"  Reclassify: {filepath.name} -> {new_system}/ (already exists, kept in other/)")
            return filepath

        filepath.rename(new_path)
        self._log(f"  Reclassify: {filepath.name} -> {new_system}/ (detected from archive contents)")
        return new_path

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

    def _is_device_reachable(self):
        """Check if the device is reachable via SSH. Caches result for 60 seconds."""
        now = time.time()
        if self._device_reachable is not None and (now - self._device_check_time) < 60:
            return self._device_reachable

        if not DEVICE_HOST:
            self._device_reachable = False
            self._device_check_time = now
            return False

        try:
            result = subprocess.run(
                ["ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes",
                 DEVICE_HOST, "echo ok"],
                capture_output=True, text=True, timeout=10,
            )
            self._device_reachable = (result.returncode == 0)
        except Exception:
            self._device_reachable = False

        self._device_check_time = now
        if not self._device_reachable:
            self._log("[TRICKLE] Device not reachable, skipping push")
        return self._device_reachable

    def _ensure_nas_mounted(self):
        """Mount the NAS via the device if not already mounted. Returns True on success."""
        if self._nas_mounted:
            return True

        if not DEVICE_HOST or not NAS_HOST or not NAS_EXPORT:
            self._log("[TRICKLE] NAS config incomplete, cannot mount")
            return False

        self._log("[TRICKLE] Mounting NAS on device...")
        try:
            result = subprocess.run(
                ["ssh", DEVICE_HOST,
                 f"mkdir -p {NAS_MOUNT} && sudo mount -t nfs "
                 f"{NAS_HOST}:{NAS_EXPORT} {NAS_MOUNT} -o hard,intr,nolock,timeo=600"],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0:
                self._nas_mounted = True
                self._log("[TRICKLE] NAS mounted successfully")
                return True
            else:
                # Mount may have already been active (already mounted is not an error)
                if "already mounted" in result.stderr.lower() or "busy" in result.stderr.lower():
                    self._nas_mounted = True
                    self._log("[TRICKLE] NAS was already mounted")
                    return True
                self._log(f"[TRICKLE] NAS mount failed: {result.stderr[:120]}")
                return False
        except Exception as e:
            self._log(f"[TRICKLE] NAS mount error: {e}")
            return False

    def _unmount_nas(self):
        """Unmount the NAS from the device. Called during run() cleanup."""
        if not self._nas_mounted or not DEVICE_HOST:
            return

        self._log("[TRICKLE] Unmounting NAS...")
        try:
            subprocess.run(
                ["ssh", DEVICE_HOST, f"sudo umount {NAS_MOUNT}"],
                capture_output=True, text=True, timeout=10,
            )
            self._nas_mounted = False
            self._log("[TRICKLE] NAS unmounted")
        except Exception as e:
            self._log(f"[TRICKLE] Unmount error: {e}")

    def _trickle_push(self, filepath):
        """Push a single downloaded file to the NAS via the device.

        - Checks device reachability (cached for 60s)
        - Mounts NAS if not already mounted
        - Uses rsync to push the file, preserving parent directory structure
          relative to the staging directory
        - Deletes the local file on success
        - Logs success/failure

        Returns True if the file was pushed and deleted, False otherwise.
        """
        if not self._trickle_enabled:
            return False

        if not filepath.exists():
            return False

        # Check device reachability
        if not self._is_device_reachable():
            return False

        # Mount NAS
        if not self._ensure_nas_mounted():
            return False

        # Compute the relative path from staging dir
        # e.g., filepath = /home/user/nas-staging/snes/game.7z
        #        rel_path = snes/game.7z
        try:
            rel_path = filepath.relative_to(self.output_dir)
        except ValueError:
            self._log(f"[TRICKLE] Cannot compute relative path for {filepath}")
            return False

        # Target path on NAS: NAS_MOUNT/NAS_ROM_SUBDIR/snes/game.7z
        target_dir = f"{NAS_MOUNT}/{NAS_ROM_SUBDIR}/{rel_path.parent}"

        self._log(f"[TRICKLE] Pushing {rel_path}...")

        try:
            # Ensure target directory exists
            subprocess.run(
                ["ssh", DEVICE_HOST, f"mkdir -p '{target_dir}'"],
                capture_output=True, text=True, timeout=10,
            )

            # Rsync the single file (--no-group avoids chgrp failures on NFS)
            result = subprocess.run(
                ["rsync", "-az", "--no-group", "--ignore-existing",
                 str(filepath),
                 f"{DEVICE_HOST}:{target_dir}/"],
                capture_output=True, text=True, timeout=300,
            )

            if result.returncode == 0:
                # Delete local file on success
                filepath.unlink()
                self._log(f"[TRICKLE] Pushed and cleaned: {rel_path}")
                return True
            else:
                self._log(f"[TRICKLE] rsync failed: {result.stderr[:120]}")
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
                elif link.startswith(self.base_url) or link.startswith(url):
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

        # For POST form downloads, we don't know the filepath until we start
        is_form_download = url.startswith("POST|")

        if not is_form_download:
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

            # For form downloads, determine filepath now from server response
            if is_form_download:
                if server_filename:
                    fname = server_filename
                else:
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

                system = self._get_system_for_file(fname, url=referer)
                filepath = self.output_dir / system / fname
                filepath.parent.mkdir(parents=True, exist_ok=True)
                name = filepath.name
                self.current_file = name
                self._log(f"  Filename: {name}")

                # Dedup check for form downloads
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
            self._unmount_nas()
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
                    self._unmount_nas()
                    return

                self.phase = f"Mop-up {i}/{len(remaining)}"

                if self.download_file(url):
                    self.files_downloaded += 1
                else:
                    self.files_failed += 1

                if i < len(remaining) and not self.stop_requested:
                    time.sleep(self.delay)

        # Cleanup: unmount NAS if trickle push was used
        self._unmount_nas()

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

            # Try the shared nas-push.sh script first
            script_dir = Path(__file__).resolve().parent
            nas_push_script = script_dir.parent / "nas" / "nas-push.sh"
            if nas_push_script.exists():
                log("[NAS] Using nas-push.sh script...")
                try:
                    env = os.environ.copy()
                    env["STAGING_DIR"] = str(staging)
                    env["DEVICE_HOST"] = DEVICE_HOST
                    env["NAS_HOST"] = NAS_HOST
                    env["NAS_EXPORT"] = NAS_EXPORT
                    env["NAS_MOUNT"] = NAS_MOUNT
                    env["NAS_ROM_SUBDIR"] = NAS_ROM_SUBDIR
                    result = subprocess.run(
                        ["bash", str(nas_push_script)],
                        capture_output=True, text=True, timeout=600,
                        env=env,
                    )
                    for line in result.stdout.strip().split("\n"):
                        if line.strip():
                            log(f"[NAS] {line}")
                    if result.returncode != 0:
                        for line in result.stderr.strip().split("\n"):
                            if line.strip():
                                log(f"[NAS] ERROR: {line}")
                    else:
                        log("[NAS] Push complete via nas-push.sh")
                    return
                except Exception as e:
                    log(f"[NAS] nas-push.sh failed ({e}), falling back to built-in push")

            # Fallback: built-in push logic
            if not DEVICE_HOST:
                log("[NAS] No DEVICE_HOST configured. Cannot push to NAS.")
                return

            # Check device is reachable
            log("[NAS] Connecting to device...")
            rc = subprocess.run(
                ["ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes",
                 DEVICE_HOST, "echo ok"],
                capture_output=True, timeout=10
            ).returncode
            if rc != 0:
                log("[NAS] Can't reach device. Is it on?")
                return

            if not NAS_HOST or not NAS_EXPORT:
                log("[NAS] NAS_HOST or NAS_EXPORT not configured. Cannot mount NAS.")
                return

            # Mount NAS on device
            log("[NAS] Mounting NAS on device...")
            rc = subprocess.run(
                ["ssh", DEVICE_HOST,
                 f"mkdir -p {NAS_MOUNT} && sudo mount -t nfs "
                 f"{NAS_HOST}:{NAS_EXPORT} {NAS_MOUNT} -o hard,intr,nolock,timeo=600"],
                capture_output=True, timeout=30
            ).returncode
            if rc != 0:
                log("[NAS] Failed to mount NAS")
                return

            # Push each system directory
            total_files = 0
            for sdir in sorted(system_dirs):
                system = sdir.name
                file_count = sum(1 for f in sdir.iterdir() if f.is_file()
                                and not f.name.endswith(".part")
                                and f.name != ".crawler-state.json")
                if file_count == 0:
                    continue

                log(f"[NAS] Pushing {file_count} files to {NAS_ROM_SUBDIR}/{system}/")

                # Ensure target dir exists on NAS
                subprocess.run(
                    ["ssh", DEVICE_HOST,
                     f"mkdir -p {NAS_MOUNT}/{NAS_ROM_SUBDIR}/{system}"],
                    capture_output=True, timeout=10
                )

                # Rsync this system folder (--ignore-existing = never overwrite NAS files)
                result = subprocess.run(
                    ["rsync", "-avz", "--progress",
                     "--ignore-existing",
                     "--exclude=*.part",
                     "--exclude=.crawler-state.json",
                     f"{sdir}/",
                     f"{DEVICE_HOST}:{NAS_MOUNT}/{NAS_ROM_SUBDIR}/{system}/"],
                    capture_output=True, text=True, timeout=600
                )
                if result.returncode == 0:
                    total_files += file_count
                    log(f"[NAS]   Done: {NAS_ROM_SUBDIR}/{system}/ ({file_count} files)")
                else:
                    log(f"[NAS]   Error on {system}: {result.stderr[:200]}")

            # Unmount
            subprocess.run(
                ["ssh", DEVICE_HOST, f"sudo umount {NAS_MOUNT}"],
                capture_output=True, timeout=10
            )

            log(f"[NAS] Push complete: {total_files} files to NAS")
            log(f"[NAS] Files are at {NAS_ROM_SUBDIR}/<system>/ on NAS")

            # Check if device needs new emulators for these systems
            pushed_systems = [d.name for d in system_dirs
                             if any(f.is_file() and not f.name.endswith(".part")
                                    for f in d.iterdir())]
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
    server.serve_forever()
