# DeckDock — Project Context

## What Is This

DeckDock is a tool suite for setting up a retro gaming library on Linux handhelds (Steam Deck, Legion Go). It handles:
- **Web crawler** (`crawler/crawler-gui.py`) — Browser-based GUI that crawls ROM sites, downloads, auto-sorts, compresses, and pushes to NAS
- **Device scripts** (`device/`) — On-device services for ROM sorting, NAS streaming, save backups, emulator setup, Steam shortcut generation
- **Unified setup** (`setup.sh`) — Single PC-side script that configures everything over SSH (11 phases)
- **Quick deploy** (`deploy.sh`) — Fast re-deploy of scripts to device via SCP (for development)

## Architecture

- **PC side (WSL2)**: Crawler GUI, unified setup, deploy script, NAS push utilities
- **Device side (SteamOS/Linux)**: Systemd services for sorting, syncing, backup. Emulators via flatpak/AppImage. `emu-setup.sh` as device-side fallback
- **NAS (UniFi Drive)**: Central ROM storage. All access via SSH/SCP (never NFS/SSHFS — causes D-state processes)

## Setup Flow

**Primary:** Run `./setup.sh` on PC. Does everything over SSH in 11 phases:
1. Config wizard → 2. SSH keys → 3. Device folders → 4. Push scripts → 5. EmuDeck → 6. Core services → 7. Xbox Cloud → 8. Tailscale → 9. NAS library → 10. Verify → 11. Summary

CLI: `--phase N` (resume from phase), `--skip-config` (reuse config.env), `--verify` (health check only)

**Fallback:** Run `device/emu-setup.sh` directly on the device in Desktop Mode (same 11 steps, runs locally).

**Development:** Run `./deploy.sh` for quick script re-deploy after code changes (no setup, just push + verify).

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

Scripts deploy to device at `~/Emulation/tools/`. Launchers to `~/Emulation/tools/launchers/`. Systemd-referenced scripts to `~/DeckDock/device/`. Service files to `~/.config/systemd/user/`. Device config (subset) to both `~/DeckDock/config.env` and `~/Emulation/tools/config.env`.

**Config split:** PC config.env has all vars (NAS, device, crawler, IGDB). Device config.env has only the subset needed on-device (NAS_HOST, NAS_USER, NAS_EXPORT, NAS_MOUNT, NAS_ROM_SUBDIR, NAS_SAVE_SUBDIR, BACKUP_KEEP).

## File Layout

```
DeckDock/
  config.env              # Local config (gitignored) — full PC set
  config.example.env      # Template with all vars
  setup.sh                # Unified PC-side setup (11 phases, all via SSH)
  deploy.sh               # Quick re-deploy for development
  crawler/
    crawler-gui.py        # Main crawler + web GUI
    title-systems.json    # Curated title→system database
    resort-other.py       # Re-sort utility for other/ files
    generate-m3u.py       # M3U playlist generator + disc audit
    chd-identify.py       # CHD metadata reader (runs on NAS)
    requirements.txt      # Python deps
  device/
    emu-setup.sh          # Device-side setup fallback (runs locally)
    rom-sorter.sh         # Auto-sort ROMs by extension
    add-roms-to-steam.py  # Generate Steam shortcuts (direct launch)
    save-backup.sh        # Save file backup on sleep
    save-restore.sh       # Save restore GUI (from NAS backups)
    bios-check.sh         # BIOS file verification
    nas-mount.sh          # SSHFS NAS mount
    nas-library-sync.sh   # NAS library sync + launcher patching
    deckdock-nas-hook.sh  # NAS streaming hook (on-demand download)
    deckdock-launcher.sh  # Launcher wrapper
    deckdock-storage-manager.sh  # Storage cleanup GUI
    deckdock-preload.sh   # Preload helper
    deckdock-azahar.sh    # 3DS launcher (zip extraction)
    launch-appimage.sh    # AppImage wrapper (Steam reaper fix)
    sleep-watcher.sh      # Sleep event watcher (triggers backup)
    drop-cleaner.sh       # Drop folder cleanup
    fetch-boxart.py       # Box art fetcher
    *.service, *.timer    # Systemd unit files
  nas/                    # NAS-related scripts
  docker/                 # Container configs
  docs/                   # Documentation
  .mal/                   # Project memory (gitignored)
```
