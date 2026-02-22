# DeckDock — Project Context

## What Is This

DeckDock is a tool suite for setting up a retro gaming library on Linux handhelds (Steam Deck, Legion Go). It handles:
- **Web crawler** (`crawler/crawler-gui.py`) — Browser-based GUI that crawls ROM sites, downloads, auto-sorts, compresses, and pushes to NAS
- **Device scripts** (`device/`) — On-device services for ROM sorting, NAS streaming, save backups, emulator setup, Steam shortcut generation
- **Deployment** (`deploy.sh`) — Pushes scripts from PC to device via SCP

## Architecture

- **PC side (WSL2)**: Crawler GUI, deploy script, NAS push utilities
- **Device side (SteamOS/Linux)**: Systemd services for sorting, syncing, backup. Emulators via flatpak/AppImage
- **NAS (UniFi Drive)**: Central ROM storage. All access via SSH/SCP (never NFS/SSHFS — causes D-state processes)

## Key Conventions

- **Config**: `config.env` (gitignored). Template: `config.example.env`. Bash-sourceable and Python-readable
- **NAS comms**: Always SSH/SCP, never NFS mounts. See `.mal/decisions.md`
- **Bash scripts**: `set -uo pipefail`, no `set -e`. `ssh -n` in loops. `setsid` for Steam reaper escape
- **System slugs**: Must match across crawler, rom-sorter, emu-setup, and NAS directory names. Authoritative list: `EXT_TO_SYSTEM` in crawler-gui.py + `SYSTEM_CHOICES` dropdown
- **ROM compression**: Disc images → CHD, cartridge ROMs → .zip (RetroArch can't handle LZMA2 .7z)

## Network

| Host | IP | User | Role |
|------|-----|------|------|
| NAS (UniFi Drive) | 192.168.1.76 | root | Storage |
| Device (Legion Go) | 192.168.1.160 | deck | Handheld |
| PC (WSL2) | localhost | jolly | Dev/crawler |

NAS export path: `/volume/d101c9d2-02df-47dd-98b8-b47406bbe90c/.srv/.unifi-drive/DeckDock/.data`

## ROM Classification (3 layers)

Three-layer system for classifying ROM files that lack system-identifying extensions or URL paths:

1. **Layer 1 — Curated JSON** (`crawler/title-systems.json`): ~265 well-known game titles mapped to system slugs. Checked first. Substring match, longest pattern wins.
2. **Layer 2 — IGDB API** (optional): Queries IGDB when curated list misses. Results cached back to `title-systems.json`. Disambiguation: if IGDB returns multiple matching systems, file stays in `other/` (false negatives > false positives).
3. **Layer 3 — Binary Header Analysis**: Reads magic bytes from disc images/CHD files to identify the system. Handles CHD v5 container format (parses header, decompresses first hunks to read sector 0 + sector 16 PVD). Detects: NES, N64, Genesis, GameCube, Wii, Saturn, Sega CD, Dreamcast, 3DO, PSX, PS2, CD-i, PC Engine.

IGDB credentials in `config.env`: `IGDB_CLIENT_ID`, `IGDB_CLIENT_SECRET`. Offline mode works with Layer 1 only.

Resort utility: `crawler/resort-other.py` — re-classifies existing files in `other/`. All three layers. Modes: `--dry-run` (default), `--execute`, `--nas`.

## Multi-Disc Handling

**Crawler** (`crawler/crawler-gui.py`): When a Disc 1 file is downloaded, the crawler searches `discovered_files` for sibling discs and tries URL manipulation (Disc 1 → Disc 2, etc.). After all discs are present, generates a `.m3u` playlist and trickle-pushes it to NAS.

**M3U Generator** (`crawler/generate-m3u.py`): Standalone utility to scan NAS or local staging for multi-disc games and generate `.m3u` playlists. Modes: `--dry-run` (default), `--execute`, `--audit` (report incomplete sets), `--nas`.

**M3U format**: Plain text, one disc filename per line, no paths, sorted by disc number. Device-side code already supports `.m3u`: launcher downloads companion discs, add-roms-to-steam skips individual discs when `.m3u` exists, storage manager cleans up companions, fetch-boxart skips disc art.

**Disc detection regex**: `r"^(.+?)\s*\(Disc\s*(\d+)\)"` (case-insensitive) — shared across crawler, generate-m3u, add-roms-to-steam, fetch-boxart.

## Deployment Targets

Scripts deploy to device at `~/Emulation/tools/`. Service files to `~/.config/systemd/user/`.

## File Layout

```
DeckDock/
  config.env              # Local config (gitignored)
  config.example.env      # Template
  deploy.sh               # PC → device deployment
  setup.sh                # Interactive setup wizard
  crawler/
    crawler-gui.py        # Main crawler + web GUI
    title-systems.json    # Curated title→system database
    resort-other.py       # Re-sort utility for other/ files
    generate-m3u.py       # M3U playlist generator + disc audit
    requirements.txt      # Python deps
  device/
    emu-setup.sh          # Device emulator/folder setup
    rom-sorter.sh         # Auto-sort ROMs by extension
    add-roms-to-steam.py  # Generate Steam shortcuts
    save-backup.sh        # Save file backup on sleep
    nas-library-sync.sh   # NAS library sync
    deckdock-nas-hook.sh  # NAS streaming hook
    deckdock-storage-manager.sh  # Storage cleanup GUI
    ...
  nas/                    # NAS-related scripts
  docker/                 # Container configs
  docs/                   # Documentation
  .mal/                   # Project memory (gitignored)
```
