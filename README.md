# DeckDock

**Turn your Steam Deck (or any Linux handheld) into a retro gaming powerhouse.**

DeckDock automates the tedious parts of building a retro gaming library on your handheld device. It downloads, organizes, compresses, and backs up your games — so you spend your time playing instead of fiddling with folders.

---

## What It Does

| Feature | What It Means For You |
|---------|----------------------|
| **Web Crawler** | Point it at a ROM site, walk away. It finds and downloads everything. |
| **Auto-Sorting** | Drop files anywhere. They get sorted into the right emulator folders automatically. |
| **Smart Compression** | Disc images convert to CHD, cartridge ROMs pack into zip. Same games, less space. |
| **Game Identification** | Automatically identifies what system a game is for — even renamed or ambiguous files. |
| **Multi-Disc Support** | Multi-disc games get grouped with `.m3u` playlists. Swap discs without leaving the game. |
| **BIOS Push** | Point it at a folder of BIOS files. It identifies, renames, and pushes them to your device. |
| **NAS Support** | Own a network drive? Games push there automatically as they download. |
| **Save Backups** | Every time you put your device to sleep, your save files get backed up. Never lose progress. |
| **Steam Integration** | Adds all your games to Steam with box art — browse and launch from Gaming Mode. |
| **Xbox Cloud Gaming** | Stream Xbox games on your handheld over Wi-Fi. No downloads needed. |
| **Tailscale** | Access your device from anywhere. Push games, stream, or SSH — even away from home. |

---

## Quick Start

### What You Need

- A **Linux handheld** (Steam Deck, Legion Go, ROG Ally running Linux, etc.) in Desktop Mode
- A **PC** running Linux or WSL2 (this is where the crawler and setup scripts run)
- Optional: A **NAS** (network drive) for centralized storage

### 1. Get the Code

**Option A — Git clone:**
```bash
git clone https://github.com/adamjboyce/DeckDock.git
cd DeckDock
```

**Option B — Download tarball (no git required):**
```bash
curl -L https://github.com/adamjboyce/DeckDock/archive/refs/heads/main.tar.gz | tar xz
cd DeckDock-main
```

### 2. Run Setup

```bash
bash setup.sh
```

This runs from your PC and configures everything over SSH. It walks you through:

- Your device's network address (SSH connection)
- Where your NAS is (if you have one)
- Where to store downloads locally on your PC
- Which emulator systems to install
- EmuDeck, background services, and all device-side tooling

It creates a `config.env` file with your settings. Re-run anytime to change things, or use `--phase N` to resume from a specific step.

### 3. Start the Crawler

```bash
cd crawler
python3 crawler-gui.py
```

Open your browser to **http://localhost:7072**. Paste a URL. Hit Start. Watch it work.

Downloaded files are organized by system in your staging directory (`~/nas-staging/` by default). If you have a NAS and trickle push enabled, each file is pushed to the NAS as soon as it downloads.

### 4. Push BIOS Files

Many emulators need BIOS files (copyrighted firmware you provide). Gather your BIOS files into a folder, then:

```bash
bash bios-push.sh /path/to/your/bios/folder
```

This scans your folder, identifies BIOS files by MD5 hash and filename (catches renamed files), and pushes them to the correct locations on your device. Use `--dry-run` to preview what it finds without pushing.

---

## How It All Fits Together

```
Your PC                          Your NAS                    Your Handheld
+----------------+               +----------------+         +----------------+
|                |   trickle     |                |         |                |
|  Crawler GUI   |---- push ---->|  ROM Library   |<- SSH ->|  Emulators     |
|  Setup Scripts |               |  Save Backups  |         |  ROM Sorter    |
|  BIOS Push     |               |                |         |  Save Backup   |
+----------------+               +----------------+         +----------------+
      |                                                            |
      +---- rom-push.sh (direct push via SSH) ---------------------+
```

**With a NAS:** Crawler downloads to your PC, pushes to NAS. Device streams games from NAS on demand.

**Without a NAS:** Crawler downloads to your PC in the staging directory — your local ROM library, organized by system (`~/nas-staging/nes/`, `~/nas-staging/psx/`, etc.). When you're ready to play, use `rom-push.sh` to bulk-push games to your device. Files stay on your PC so you always have a local copy.

---

## The Crawler

The crawler is a browser-based tool that runs on your PC. It:

1. Visits the URL you give it
2. Follows links to find downloadable game files
3. Downloads them, skipping duplicates
4. Identifies what system each game belongs to (NES, SNES, PS1, etc.)
5. Compresses files to save space (disc images to CHD, cartridge ROMs to zip)
6. Groups multi-disc games and generates `.m3u` playlists
7. Optionally pushes each file to your NAS as soon as it's downloaded

### Using It

Open **http://localhost:7072** after starting the crawler. You'll see:

- **URL** -- Paste the address of the page to crawl
- **System** -- Leave on "Auto-detect" unless you know better
- **Depth** -- How many pages deep to follow links (default 3 is fine for most sites)
- **Delay** -- Seconds between downloads (be polite to the server)

Hit **Start** and watch the progress. You can stop and restart anytime -- it remembers what it already downloaded.

### How Games Get Identified

The crawler uses a three-layer system to figure out what system each game belongs to:

1. **File extensions** -- `.nes` is obviously NES, `.gba` is Game Boy Advance, etc.
2. **Curated title database** -- A list of ~265 known game titles mapped to their systems. Catches platform-exclusive titles by name.
3. **IGDB API** -- For unrecognized titles, queries the IGDB game database (optional -- needs API credentials in `config.env`). Results get cached so lookups get smarter over time.
4. **Binary header analysis** -- Reads the actual file bytes to identify disc images. Works on raw ISOs, CHD containers, and CISO files.

Files that can't be identified go to an `other/` folder. Use `crawler/resort-other.py` to re-classify them later using all four methods.

### Supported Systems

NES, SNES, Game Boy, GBA, N64, GameCube, Wii, Nintendo DS, 3DS, Switch, PS1, PS2, PS3, PSP, PS Vita, Dreamcast, Saturn, Sega CD, Genesis, Game Gear, Master System, 32X, Original Xbox, Atari (2600/5200/7800/Jaguar/Lynx), 3DO, CD-i, PC Engine, Neo Geo Pocket, WonderSwan, ColecoVision, Vectrex, MAME, and more.

### Supported File Formats

`.zip`, `.7z`, `.rar`, `.iso`, `.bin/.cue`, `.chd`, `.rvz`, `.cso`, `.pbp`, and all common ROM extensions.

---

## BIOS Files

Many emulators require BIOS files -- copyrighted firmware dumped from real hardware. DeckDock can't include these, but it makes setting them up painless.

### Pushing BIOS Files to Your Device

Gather all your BIOS files into a folder on your PC (any names, any structure), then:

```bash
# Preview what it finds (no changes made)
bash bios-push.sh /path/to/bios/folder --dry-run

# Scan, confirm, and push to device
bash bios-push.sh /path/to/bios/folder
```

The script:

1. **MD5 matching** -- Hashes every file and matches against 14 known BIOS hashes. This catches renamed files -- your `ps1bios.bin` gets identified as `SCPH1001.BIN` and pushed with the correct name.
2. **Name matching** -- For files without known hashes (PS2, Switch keys, Xbox, NDS firmware), matches by filename patterns.
3. **3DS detection** -- Finds and pushes an `azahar/keys` directory if present.
4. **Validation** -- After pushing, runs `bios-check.sh` on the device to verify everything landed correctly.

### Supported BIOS Files

| System | Files | How Identified |
|--------|-------|----------------|
| PS1 | SCPH1001.BIN, scph5500/5501/5502.bin | MD5 hash |
| PS2 | SCPH-70012, SCPH-77001 | Filename pattern |
| Dreamcast | dc_boot.bin, dc_flash.bin | MD5 hash |
| Saturn | sega_101.bin, mpr-17933.bin, saturn_bios.bin | MD5 hash + filename |
| Sega CD | bios_CD_U/E/J.bin | MD5 hash |
| Nintendo DS | bios7.bin, bios9.bin, firmware.bin | MD5 hash + filename |
| Game Boy Advance | gba_bios.bin | MD5 hash |
| Nintendo 3DS | azahar/keys directory | Directory detection |
| Nintendo Switch | prod.keys, title.keys | Filename match |
| Original Xbox | mcpx_1.0.bin, Complex_4627v1.03.bin | Filename match |

### Checking What's Installed

To see what BIOS files are present (or missing) on your device:

```bash
# Run from your PC (over SSH)
ssh deck@<device-ip> "bash ~/Emulation/tools/bios-check.sh"
```

Or run `bios-check.sh` directly on the device in Desktop Mode.

### Where to Get BIOS Files

BIOS files are copyrighted firmware. You can dump them from consoles you own. DeckDock does not provide BIOS files.

---

## Device Setup

The recommended way to set up your device is through `setup.sh` on your PC -- it handles everything over SSH. But you can also run `device/emu-setup.sh` directly on the device in Desktop Mode as a fallback.

Both scripts walk you through:

1. **System selection** -- Pick which console families to set up (Nintendo, PlayStation, Sega, Xbox, etc.). Only selected systems get ROM folders and emulator configs.
2. **EmuDeck** -- Installs RetroArch, DuckStation, PCSX2, Dolphin, and other emulators with good defaults.
3. **Folder structure** -- Creates `~/Emulation/roms/<system>/` for every selected system, plus `drop/`, `saves/`, and `backups/`.
4. **Script deployment** -- Pushes all DeckDock tools to the device (ROM sorter, save backup, launchers, etc.).
5. **ROM Sorter** -- Background service that watches your `drop/` folder and auto-sorts files into the right system folder.
6. **Save Backup** -- Backs up your game saves every time you put the device to sleep.
7. **Xbox Cloud Gaming** -- Browser shortcut to stream Xbox games (requires Game Pass Ultimate and a Chromium browser).
8. **Tailscale** -- VPN access to your device from anywhere (free for personal use).
9. **NAS library** -- Connects to your network drive for streaming games and storing save backups.
10. **Steam shortcuts** -- Generates Steam library entries for all your games with box art. Browse and launch from Gaming Mode.
11. **Verification** -- Checks that all tools, services, and connections are working.

### After Setup

- **Drop games** into `~/Emulation/drop/` -- they sort themselves into the right system folder.
- **Play games** -- Launch from Steam Gaming Mode. All your games have shortcuts with box art.
- **Sleep your device** -- Saves back up automatically to local storage (and NAS if connected).
- **Xbox Cloud** -- Open from Steam to stream Xbox games.
- **Remote access** -- SSH or push games from anywhere via Tailscale.

---

## Pushing ROMs to Your Device

If you don't have a NAS (or just want to push games directly), use `rom-push.sh`:

```bash
# Push all systems from your staging directory
bash rom-push.sh

# Push from a different folder
bash rom-push.sh /path/to/my/roms

# Push only one system
bash rom-push.sh --system psx

# Preview what would be pushed
bash rom-push.sh --dry-run
```

This scans your staging directory for system subdirectories, shows a manifest (file counts + sizes per system), and pushes everything to `~/Emulation/roms/<system>/` on the device via SCP. Files that already exist on the device are skipped. Your local files are **never deleted** — the staging directory is your library.

After pushing, Steam shortcuts are automatically regenerated so new games appear in Gaming Mode.

## NAS Push

If you have trickle push disabled (or want to do a bulk push after crawling), push your staged downloads to the NAS:

```bash
bash nas/nas-push.sh
```

This syncs your staging directory to the NAS and cleans up local files after a successful transfer. Safe to run multiple times -- it won't re-push files that are already there.

All NAS communication uses SSH/SCP. No NFS mounts, no SSHFS -- just reliable file transfers.

---

## Development Workflow

After making changes to device scripts, push updates without re-running full setup:

```bash
bash deploy.sh              # Push all scripts + services, verify, restart
bash deploy.sh --scripts    # Scripts only, skip service restart
bash deploy.sh --verify     # Health check only, no file push
```

This pushes all device scripts, service files, and config to the device via SCP, then verifies system tools, checks NAS connectivity, reports service status, and optionally restarts services and regenerates Steam shortcuts.

---

## Docker (Optional)

If you'd rather not install Python dependencies on your PC, run the crawler in Docker:

```bash
cd docker
docker compose up
```

The crawler UI will be at **http://localhost:7072**. Your `config.env` and staging directory are mounted into the container.

---

## Configuration

All settings live in `config.env` (created by `setup.sh` or copied from `config.example.env`).

| Setting | What It Does | Default |
|---------|-------------|---------|
| `NAS_HOST` | IP address of your NAS | _(none)_ |
| `NAS_USER` | SSH user for NAS access | `root` |
| `NAS_EXPORT` | Shared folder path on the NAS | _(none)_ |
| `NAS_ROM_SUBDIR` | Subdirectory on NAS for ROM storage | `roms` |
| `NAS_SAVE_SUBDIR` | Subdirectory on NAS for save backups | `saves` |
| `DEVICE_HOST` | SSH address of your handheld (`user@ip`) | _(none)_ |
| `STAGING_DIR` | Where downloads go on your PC | `~/nas-staging` |
| `NAS_MOUNT` | Temporary mount point on the device | `/tmp/nas-roms` |
| `CRAWLER_PORT` | Port for the crawler web UI | `7072` |
| `DEFAULT_DELAY` | Seconds between downloads | `5` |
| `DEFAULT_DEPTH` | How deep the crawler follows links | `3` |
| `BACKUP_KEEP` | How many rolling save backups to keep | `10` |
| `TRICKLE_PUSH` | Push each file to NAS right after download | `true` |
| `IGDB_CLIENT_ID` | IGDB API client ID (optional, for game identification) | _(none)_ |
| `IGDB_CLIENT_SECRET` | IGDB API client secret | _(none)_ |

---

## Compression

DeckDock automatically compresses games to save storage space while keeping perfect quality:

| File Type | Output Format | Why |
|-----------|---------------|-----|
| Disc images (.iso, .bin/.cue, .gdi) | **CHD** | Lossless compression designed for disc images. Saves 30-60% space. |
| Cartridge ROMs (.nes, .snes, .gba, etc.) | **zip** | Deflate compression. RetroArch plays directly from zip without extracting. |
| Already optimal (.chd, .rvz, .cso, .pbp) | **Unchanged** | Already compressed well. Left as-is. |

All compression is lossless -- your games are byte-for-byte identical when decompressed.

---

## File Structure

```
DeckDock/
+-- setup.sh                         # PC-side setup wizard (11 phases, all via SSH)
+-- deploy.sh                        # Quick re-deploy scripts to device
+-- bios-push.sh                     # PC-side BIOS transfer (MD5 + name matching)
+-- rom-push.sh                      # PC-side bulk ROM push to device
+-- config.example.env               # Template configuration
+-- config.env                       # Your configuration (not in git)
|
+-- crawler/
|   +-- crawler-gui.py               # Web crawler + browser UI
|   +-- title-systems.json           # Curated game title -> system database
|   +-- resort-other.py              # Re-classify files stuck in other/
|   +-- generate-m3u.py              # M3U playlist generator for multi-disc games
|   +-- chd-identify.py              # CHD metadata reader (runs on NAS)
|   +-- requirements.txt             # Python dependencies
|   +-- crawler-gui.service          # systemd service (optional)
|
+-- device/
|   +-- emu-setup.sh                 # Device-side setup fallback
|   +-- rom-sorter.sh                # Auto-sort ROMs by extension
|   +-- drop-cleaner.sh              # Clean processed files from drop folder
|   +-- bios-check.sh               # BIOS file verification
|   +-- add-roms-to-steam.py         # Generate Steam shortcuts (direct launch)
|   +-- fetch-boxart.py              # Download box art for Steam
|   +-- save-backup.sh               # Back up emulator saves
|   +-- save-restore.sh              # Restore saves from NAS backups (GUI)
|   +-- sleep-watcher.sh             # Trigger backup on device sleep
|   +-- nas-mount.sh                 # NAS SSH mount
|   +-- nas-library-sync.sh          # NAS library sync + launcher patching
|   +-- deckdock-launcher.sh         # Launcher wrapper
|   +-- deckdock-nas-hook.sh         # NAS streaming hook (on-demand download)
|   +-- deckdock-storage-manager.sh  # Storage cleanup GUI
|   +-- deckdock-preload.sh          # Preload helper
|   +-- deckdock-azahar.sh           # 3DS launcher (zip extraction)
|   +-- launch-appimage.sh           # AppImage wrapper (Steam reaper fix)
|   +-- *.service, *.timer           # systemd unit files
|
+-- nas/
|   +-- nas-push.sh                  # Push staged files to NAS
|
+-- docker/
|   +-- Dockerfile                   # Container build
|   +-- docker-compose.yml           # One-command launch
|
+-- docs/
    +-- architecture.md              # Technical deep-dive
```

---

## Troubleshooting

**Crawler can't download from a site**
Some sites have bot protection. If standard downloads fail, the crawler can use a headless browser (Playwright) as a fallback. Install it with: `playwright install chromium`. This is optional -- most sites work without it.

**NAS push fails**
All NAS communication uses SSH/SCP. Make sure you can SSH to your NAS from both your PC and your device: `ssh <user>@<nas-ip>`. If that works but pushes fail, check file permissions on the NAS export path.

**Save backups aren't running**
Check the service: `systemctl --user status save-backup-watcher.service`. The sleep watcher monitors D-Bus for suspend events, which requires a D-Bus session (available in Desktop Mode, may not work in Gaming Mode on all devices).

**ROM Sorter puts files in the wrong folder**
The sorter uses file extensions to determine the system. Files with ambiguous extensions (like `.bin`) may need manual sorting. Unrecognized files go to `other/`. Use `crawler/resort-other.py` to re-classify them using title matching and header analysis.

**BIOS files aren't being detected**
Run `bios-check.sh` on your device to see exactly what's present, missing, or has the wrong MD5. Then use `bios-push.sh` from your PC to push the correct files.

**Steam shortcuts crash on launch**
DeckDock launches emulators directly (not through EmuDeck wrapper scripts). If a shortcut crashes, check that the emulator itself is installed. For 3DS games in `.zip` format, the `deckdock-azahar.sh` launcher extracts them before launching (Azahar can't open zips directly).

**Device services crash-looping**
Check `journalctl --user -u <service-name>`. A crash-looping service can disrupt other system daemons. Stop it with `systemctl --user stop <service-name>`, fix the issue, then restart.

---

## License

MIT. Do whatever you want with it.
