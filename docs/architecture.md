# DeckDock Architecture

## System Overview

DeckDock is a set of extensibility tools for SteamOS-based handhelds (e.g. Lenovo Legion Go, Steam Deck) and home infrastructure. It automates the full lifecycle of downloading, processing, staging, and distributing ROM files across a home network, and provides continuous save-game backup from the handheld to a NAS.

```
 ┌─────────────────────────────────────────────────────────────────┐
 │                        PC / Server                              │
 │                                                                 │
 │   ┌──────────────┐    ┌──────────────┐    ┌────────────────┐   │
 │   │ Crawler GUI  │───▶│  Compression │───▶│  Staging Dir   │   │
 │   │ (port 7072)  │    │   Pipeline   │    │ ~/nas-staging  │   │
 │   └──────────────┘    └──────────────┘    └───────┬────────┘   │
 │         │                                         │            │
 │         │  Playwright + requests                  │ rsync/scp  │
 │         ▼                                         ▼            │
 │   ┌──────────┐                             ┌───────────┐       │
 │   │  Source   │                             │  NAS Push │       │
 │   │  Sites   │                             │  (SSH)    │       │
 │   └──────────┘                             └─────┬─────┘       │
 └──────────────────────────────────────────────────┼─────────────┘
                                                    │
                         LAN (SSH / NFS)            │
                                                    ▼
 ┌──────────────────────────────────────────────────────────────┐
 │                           NAS                                │
 │                                                              │
 │   /volume/shared/roms/         /volume/shared/saves/         │
 │   ├── snes/                    ├── backup-2026-02-21.tar     │
 │   ├── psx/                     ├── backup-2026-02-20.tar     │
 │   ├── dreamcast/               └── ...                       │
 │   └── ...                                                    │
 └────────────────────────────────┬─────────────────────────────┘
                                  │
                         NFS mount (on demand)
                                  │
                                  ▼
 ┌──────────────────────────────────────────────────────────────┐
 │                   Device (Legion / Deck)                     │
 │                                                              │
 │   ┌──────────────────┐    ┌──────────────────┐              │
 │   │  Emulators /     │◀───│  NAS mount       │              │
 │   │  RetroArch       │    │  /tmp/nas-roms   │              │
 │   └────────┬─────────┘    └──────────────────┘              │
 │            │                                                 │
 │            │  D-Bus suspend signal                           │
 │            ▼                                                 │
 │   ┌──────────────────┐                                      │
 │   │  Save Backup     │──── tar + push to NAS ──────────▶    │
 │   │  Service          │                                      │
 │   └──────────────────┘                                      │
 └──────────────────────────────────────────────────────────────┘
```

---

## Components

### Crawler GUI (`crawler/crawler-gui.py`)

A web-based interface (served on port 7072) for discovering and downloading ROM files from source sites. The GUI lets you:

- Enter a target URL and configure crawl depth and delay.
- Browse discovered links in a navigable tree.
- Select files for download, with automatic format detection.
- Monitor download progress and view a history of completed jobs.

Under the hood the crawler uses **Playwright** (headless Chromium) for JavaScript-rendered pages and **requests / BeautifulSoup** for static HTML. This dual approach handles both simple directory listings and sites that require client-side rendering.

### Compression Pipeline

After a file is downloaded, the pipeline classifies its archive format and runs the appropriate extraction and conversion steps:

1. **Classify** — Identify the archive type by extension and magic bytes (`.7z`, `.zip`, `.rar`, `.chd`, `.iso`, `.bin/.cue`, etc.).
2. **Extract** — Decompress using the appropriate tool:
   - `py7zr` or system `7z` for `.7z` archives.
   - `unrar` for `.rar` archives.
   - Native `zipfile` for `.zip` archives.
3. **Convert / Repack** — If the target system benefits from a different format:
   - Disc images (`.iso`, `.bin/.cue`, `.gdi`) are converted to `.chd` using `chdman createcd` or `chdman createdvd` for better compression and single-file convenience.
   - Files that are already in the optimal format pass through unchanged.
4. **Stage** — The final processed file is placed in the staging directory (`~/nas-staging`) organized by system subdirectory.

### NAS Push

Processed files in the staging directory are pushed to the NAS over SSH (rsync or scp). This can happen in two modes:

- **Trickle push** (`TRICKLE_PUSH=true`) — Each file is pushed immediately after processing. Useful when bandwidth is not a concern.
- **Batch push** (`TRICKLE_PUSH=false`) — Files accumulate in staging and are pushed manually or on a schedule.

### Device NAS Mount

The handheld device mounts the NAS ROM share via NFS at `/tmp/nas-roms` (configurable). Emulators and RetroArch are pointed at this mount. This means:

- No files need to be stored locally on the device's limited internal storage.
- New content appears immediately once pushed to the NAS.
- The mount is on-demand and can be managed via a systemd service.

### Save Backup Service (`device/`)

A systemd user service that runs on the handheld and triggers on suspend (via D-Bus `PrepareForSleep` signal). When the device suspends:

1. The service intercepts the D-Bus signal.
2. It tars the configured save directories.
3. The tar archive is pushed to the NAS saves directory over SSH.
4. Old backups are rotated based on `BACKUP_KEEP` (default: 10 rolling backups).

This ensures save data is backed up every time the user puts the device to sleep, with no manual intervention required.

---

## Data Flow

### Download-to-Playable Path

```
URL (source site)
  │
  ▼
Crawler GUI (Playwright / requests)
  │  download .7z / .rar / .zip / .iso / .bin+cue
  ▼
Compression Pipeline
  │  extract → convert (chdman) → organize by system
  ▼
Staging Directory (~/nas-staging/psx/game.chd)
  │
  ▼
NAS Push (rsync over SSH)
  │
  ▼
NAS (/volume/shared/roms/psx/game.chd)
  │
  ▼
Device NFS mount (/tmp/nas-roms/psx/game.chd)
  │
  ▼
Emulator launches game
```

### Save Backup Path

```
Emulator writes save file
  │
  ▼
Device suspends (lid close / power button)
  │
  ▼
D-Bus PrepareForSleep signal fires
  │
  ▼
Save Backup Service
  │  tar save directories
  ▼
Push tar to NAS over SSH
  │
  ▼
NAS (/volume/shared/saves/backup-YYYY-MM-DD-HHMMSS.tar)
  │
  ▼
Rotate: keep most recent N backups (BACKUP_KEEP)
```

---

## Network Topology

```
┌────────────────┐         ┌────────────────┐         ┌────────────────┐
│   PC / Server  │◀──SSH──▶│      NAS       │◀──NFS──▶│    Device      │
│                │         │                │         │                │
│  Crawler GUI   │ rsync/  │  NFS exports   │  mount  │  Emulators     │
│  Pipeline      │ scp     │  ROM storage   │         │  Save backup   │
│  Staging dir   │────────▶│  Save storage  │◀────────│  service       │
└────────────────┘         └────────────────┘         └────────────────┘
     :7072 (web UI)

All communication happens over the local LAN.
The PC pushes to the NAS.
The Device reads from the NAS (NFS) and pushes saves to the NAS (SSH).
No component needs to reach the internet except the Crawler during downloads.
```

| Link | Protocol | Direction | Purpose |
|------|----------|-----------|---------|
| PC to NAS | SSH (rsync/scp) | PC --> NAS | Push processed ROMs to storage |
| Device to NAS (reads) | NFS | Device <-- NAS | Mount ROM library for emulators |
| Device to NAS (saves) | SSH (scp) | Device --> NAS | Push save backups |
| PC Crawler to Internet | HTTPS | PC --> Internet | Download source files |

---

## Security Considerations

### No Credentials in Configuration

`config.env` stores network addresses and paths only. No passwords, API keys, or tokens are stored in configuration files. Authentication is handled entirely through SSH keys.

### SSH Key Authentication

All SSH operations (NAS push from PC, save push from device) use key-based authentication. Password auth is neither expected nor supported. Keys should be set up before running `setup.sh`:

```bash
# From the PC (for NAS push)
ssh-keygen -t ed25519
ssh-copy-id user@nas-ip

# From the device (for save backup push)
ssh-keygen -t ed25519
ssh-copy-id user@nas-ip
```

### SSL / TLS Handling

The crawler downloads files over HTTPS where available. Certificate verification is enabled by default. For sites with self-signed or expired certificates, the user must explicitly opt in to insecure mode per-download -- the system does not globally disable SSL verification.

### Container Security

The Docker image runs the crawler as a non-root user (`crawler`, UID 1000). The config file is mounted read-only (`:ro`). The staging volume is the only writable mount. No privileged capabilities are required.

### Network Isolation

All inter-component communication stays on the local LAN. The only outbound internet access is from the crawler during active downloads. The NAS and device never need internet access.

---

## Configuration

All configuration is centralized in a single `config.env` file at the project root. This file is:

- Generated interactively by `setup.sh`.
- Bash-sourceable (shell scripts can `source config.env`).
- Python-readable (simple `KEY=VALUE` parsing).
- Mounted read-only into the Docker container at `/config/config.env`.

See `config.example.env` for all available options and their defaults.
