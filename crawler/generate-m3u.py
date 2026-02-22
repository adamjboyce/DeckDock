#!/usr/bin/env python3
"""
DeckDock M3U Generator — Scan for multi-disc games and generate .m3u playlists.

Scans ROM directories for files with (Disc N) naming, groups them by game,
and generates .m3u playlist files for complete sets.

Modes:
  --dry-run   (default) Show what would be generated, don't create files
  --execute   Actually create .m3u files
  --audit     Report incomplete disc sets (missing discs)
  --nas       Operate on NAS via SSH instead of local staging
  --local     Operate on local staging directory (default)

Usage:
  python3 generate-m3u.py --nas --dry-run          # preview m3u generation on NAS
  python3 generate-m3u.py --nas --execute           # create m3u files on NAS
  python3 generate-m3u.py --nas --audit             # show incomplete disc sets
  python3 generate-m3u.py --local /path/to/roms     # scan custom local path
"""

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path


# ============================================================================
# Config loader (same as crawler-gui.py / resort-other.py)
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
# NAS config
# ============================================================================

NAS_HOST = cfg("NAS_HOST", "")
NAS_USER = cfg("NAS_USER", "root")
NAS_EXPORT = cfg("NAS_EXPORT", "")
NAS_ROM_SUBDIR = cfg("NAS_ROM_SUBDIR", "roms")

# Disc-based systems that can have multi-disc games
DISC_SYSTEMS = ["psx", "ps2", "saturn", "dreamcast", "segacd", "3do", "cdi", "pcengine"]

# Pattern for detecting disc numbers in filenames
DISC_RE = re.compile(r"^(.+?)\s*\(Disc\s*(\d+)\)", re.IGNORECASE)


# ============================================================================
# File listing
# ============================================================================

def list_nas_system_files(system):
    """List all files in a NAS system directory via SSH."""
    remote_path = f"{NAS_EXPORT}/{NAS_ROM_SUBDIR}/{system}"
    try:
        result = subprocess.run(
            ["ssh", "-n", f"{NAS_USER}@{NAS_HOST}",
             f"ls -1 \"{remote_path}\" 2>/dev/null"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            return []
        return [f.strip() for f in result.stdout.strip().split("\n") if f.strip()]
    except (subprocess.TimeoutExpired, OSError):
        return []


def list_local_system_files(base_dir, system):
    """List all files in a local system directory."""
    system_dir = Path(base_dir) / system
    if not system_dir.exists():
        return []
    return [f.name for f in system_dir.iterdir() if f.is_file()]


# ============================================================================
# Disc grouping
# ============================================================================

def group_discs(files):
    """Group files by game base name and disc number.

    Returns:
        dict of {base_name: {disc_num: filename, ...}}
        Also includes non-disc files for .m3u detection.
    """
    games = {}
    m3u_files = set()

    for filename in files:
        if filename.lower().endswith(".m3u"):
            # Track existing .m3u files
            stem = Path(filename).stem
            m3u_files.add(stem.lower())
            continue

        stem = Path(filename).stem
        m = DISC_RE.match(stem)
        if not m:
            continue

        base_name = m.group(1).strip()
        disc_num = int(m.group(2))

        if base_name not in games:
            games[base_name] = {}
        games[base_name][disc_num] = filename

    return games, m3u_files


def analyze_disc_set(disc_map):
    """Analyze a disc set for completeness.

    Returns:
        (is_complete, max_disc, missing_discs)
    """
    if not disc_map:
        return False, 0, []

    max_disc = max(disc_map.keys())
    missing = [n for n in range(1, max_disc + 1) if n not in disc_map]
    return len(missing) == 0, max_disc, missing


# ============================================================================
# M3U generation
# ============================================================================

def generate_m3u_content(base_name, disc_map):
    """Generate .m3u file content for a complete disc set.

    Returns the playlist content as a string (one filename per line).
    """
    lines = [disc_map[n] for n in sorted(disc_map.keys())]
    return "\n".join(lines) + "\n"


def write_m3u_nas(system, base_name, content):
    """Write an .m3u file to the NAS via SSH."""
    remote_path = f"{NAS_EXPORT}/{NAS_ROM_SUBDIR}/{system}/{base_name}.m3u"
    try:
        result = subprocess.run(
            ["ssh", "-n", f"{NAS_USER}@{NAS_HOST}",
             f"cat > \"{remote_path}\""],
            input=content, capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            # Set permissions for SSHFS visibility
            subprocess.run(
                ["ssh", "-n", f"{NAS_USER}@{NAS_HOST}",
                 f"chmod a+r \"{remote_path}\""],
                capture_output=True, text=True, timeout=10,
            )
            return True
    except (subprocess.TimeoutExpired, OSError):
        pass
    return False


def write_m3u_local(base_dir, system, base_name, content):
    """Write an .m3u file to a local directory."""
    m3u_path = Path(base_dir) / system / f"{base_name}.m3u"
    try:
        m3u_path.write_text(content, encoding="utf-8")
        return True
    except OSError:
        return False


# ============================================================================
# Main
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Generate .m3u playlists for multi-disc games."
    )
    parser.add_argument("path", nargs="?", default=None,
                        help="Path to ROM base directory (default: ~/nas-staging/)")
    parser.add_argument("--nas", action="store_true",
                        help="Operate on NAS via SSH")
    parser.add_argument("--local", action="store_true",
                        help="Operate on local staging directory (default)")
    parser.add_argument("--execute", action="store_true",
                        help="Actually create .m3u files (default is dry-run)")
    parser.add_argument("--audit", action="store_true",
                        help="Report incomplete disc sets")
    parser.add_argument("--systems", type=str, default=None,
                        help="Comma-separated list of systems to scan (default: all disc systems)")
    args = parser.parse_args()

    if args.nas and not NAS_HOST:
        print("ERROR: NAS_HOST not configured in config.env")
        sys.exit(1)

    systems = args.systems.split(",") if args.systems else DISC_SYSTEMS
    base_dir = args.path or cfg("STAGING_DIR", os.path.expanduser("~/nas-staging"))
    mode = "NAS" if args.nas else f"local ({base_dir})"

    print(f"Scanning {mode} for multi-disc games...")
    print(f"Systems: {', '.join(systems)}\n")

    total_complete = 0
    total_incomplete = 0
    total_existing = 0
    total_created = 0

    complete_sets = []
    incomplete_sets = []

    for system in systems:
        if args.nas:
            files = list_nas_system_files(system)
        else:
            files = list_local_system_files(base_dir, system)

        if not files:
            continue

        games, existing_m3u = group_discs(files)

        for base_name in sorted(games):
            disc_map = games[base_name]
            is_complete, max_disc, missing = analyze_disc_set(disc_map)

            # Check if .m3u already exists
            has_m3u = base_name.lower() in existing_m3u

            if has_m3u:
                total_existing += 1
                continue

            if is_complete:
                complete_sets.append((system, base_name, disc_map))
                total_complete += 1
            else:
                incomplete_sets.append((system, base_name, disc_map, missing))
                total_incomplete += 1

    # Report complete sets
    if complete_sets:
        print(f"{'=' * 70}")
        action = "CREATING" if args.execute else "WOULD CREATE"
        print(f"COMPLETE SETS — {action} .m3u ({len(complete_sets)} games)")
        print(f"{'=' * 70}")

        by_system = {}
        for system, base_name, disc_map in complete_sets:
            by_system.setdefault(system, []).append((base_name, disc_map))

        for system in sorted(by_system):
            print(f"\n  {system}/")
            for base_name, disc_map in sorted(by_system[system]):
                n_discs = len(disc_map)
                print(f"    {base_name}.m3u  [{n_discs} discs]")

                if args.execute:
                    content = generate_m3u_content(base_name, disc_map)
                    if args.nas:
                        ok = write_m3u_nas(system, base_name, content)
                    else:
                        ok = write_m3u_local(base_dir, system, base_name, content)

                    if ok:
                        print(f"      -> created")
                        total_created += 1
                    else:
                        print(f"      -> FAILED")

    # Report incomplete sets (always shown in audit mode, shown as info otherwise)
    if incomplete_sets and (args.audit or not args.execute):
        print(f"\n{'=' * 70}")
        print(f"INCOMPLETE SETS — missing discs ({len(incomplete_sets)} games)")
        print(f"{'=' * 70}")

        by_system = {}
        for system, base_name, disc_map, missing in incomplete_sets:
            by_system.setdefault(system, []).append((base_name, disc_map, missing))

        for system in sorted(by_system):
            print(f"\n  {system}/")
            for base_name, disc_map, missing in sorted(by_system[system]):
                have = sorted(disc_map.keys())
                miss = sorted(missing)
                print(f"    {base_name}")
                print(f"      has: Disc {', '.join(str(d) for d in have)}")
                print(f"      missing: Disc {', '.join(str(d) for d in miss)}")

    # Summary
    print(f"\n{'=' * 70}")
    print(f"Summary")
    print(f"{'=' * 70}")
    print(f"  Complete sets (ready for .m3u): {total_complete}")
    print(f"  Incomplete sets (missing discs): {total_incomplete}")
    print(f"  Existing .m3u files (skipped):  {total_existing}")
    if args.execute:
        print(f"  .m3u files created:             {total_created}")
    elif total_complete > 0:
        print(f"\n  Dry run — use --execute to create .m3u files.")


if __name__ == "__main__":
    main()
