# DeckDock

**Turn your Steam Deck (or any Linux handheld) into a retro gaming powerhouse.**

DeckDock is a set of tools that automates the tedious parts of setting up a retro gaming library on your handheld device. It handles downloading, organizing, compressing, and backing up your games — so you can spend your time playing instead of fiddling with folders.

---

## What It Does

| Feature | What It Means For You |
|---------|----------------------|
| **Web Crawler** | Point it at a ROM site, walk away. It finds and downloads everything. |
| **Auto-Sorting** | Drop files anywhere. They get sorted into the right emulator folders automatically. |
| **Smart Compression** | Disc images convert to CHD, cartridge ROMs pack into 7z. Same games, way less space. |
| **NAS Support** | Own a network drive? Games push there automatically as they download. |
| **Trickle Push** | Don't have much local storage? Each file pushes to your NAS right after it downloads. |
| **Save Backups** | Every time you put your device to sleep, your save files get backed up. Never lose progress. |
| **EmuDeck Setup** | One script gets your device set up with emulators, folders, and services. |
| **Xbox Cloud Gaming** | Stream Xbox games on your handheld over Wi-Fi. No downloads needed. |
| **Steam ROM Manager** | Adds all your retro games to Steam with artwork — browse them in Gaming Mode. |
| **Tailscale** | Access your device from anywhere. Push games, stream, or SSH — even away from home. |

---

## Quick Start

### What You Need

- A **Linux handheld** (Steam Deck, Legion Go, ROG Ally running Linux, etc.) in Desktop Mode
- A **PC** running Linux or WSL2 (this is where the crawler runs)
- Optional: A **NAS** (network drive) for centralized storage

### 1. Get the Code

```bash
git clone https://github.com/adamjboyce/DeckDock.git
cd DeckDock
```

### 2. Run the Setup Wizard

```bash
bash setup.sh
```

This walks you through everything:
- Where your NAS is (if you have one)
- Your device's network address
- Where to store downloads
- Whether to install Python dependencies

It creates a `config.env` file with your settings. You can re-run it anytime to change things.

### 3. Start the Crawler

```bash
cd crawler
python3 crawler-gui.py
```

Open your browser to **http://localhost:7072**. Paste a URL. Hit Start. Watch it work.

### 4. Set Up Your Device

Copy the `device/` folder to your handheld and run:

```bash
bash device/emu-setup.sh
```

This installs EmuDeck, creates your folder structure, enables automatic ROM sorting and save backups, and optionally connects to your NAS.

---

## How It All Fits Together

```
Your PC                          Your NAS                    Your Handheld
┌──────────────┐               ┌──────────────┐           ┌──────────────┐
│              │   trickle     │              │           │              │
│  Crawler GUI │──── push ────>│  ROM Library │<── mount ─│  Emulators   │
│  (browser)   │               │  Save Backup │           │  ROM Sorter  │
│              │               │              │           │  Save Backup │
└──────────────┘               └──────────────┘           └──────────────┘
      │                                                          │
      └──── direct push via SSH (if no NAS) ─────────────────────┘
```

**With a NAS:** Crawler downloads to your PC, pushes to NAS. Device mounts NAS to play.

**Without a NAS:** Crawler downloads to your PC, pushes directly to your device over SSH.

---

## The Crawler

The crawler is a browser-based tool that runs on your PC. It:

1. Visits the URL you give it
2. Follows links to find downloadable game files
3. Downloads them, skipping duplicates
4. Identifies what system each game belongs to (NES, SNES, PS1, etc.)
5. Compresses files to save space (disc images → CHD, ROMs → 7z)
6. Optionally pushes each file to your NAS as soon as it's downloaded

### Using It

Open **http://localhost:7072** after starting the crawler. You'll see:

- **URL** — Paste the address of the page to crawl
- **System** — Leave on "Auto-detect" unless you know better
- **Depth** — How many pages deep to follow links (default 3 is fine for most sites)
- **Delay** — Seconds between downloads (be polite to the server)

Hit **Start** and watch the progress. You can stop and restart anytime — it remembers what it already downloaded.

### Supported File Formats

The crawler recognizes ROMs for: NES, SNES, Game Boy, GBA, N64, GameCube, Wii, Switch, PS1, PS2, PSP, Dreamcast, Saturn, Genesis, Game Gear, Master System, 32X, Atari (2600/5200/7800), PC Engine, Neo Geo Pocket, WonderSwan, MAME, and more.

It handles: `.zip`, `.7z`, `.rar`, `.iso`, `.bin/.cue`, `.chd`, `.rvz`, `.cso`, `.pbp`, and all common ROM extensions.

---

## Device Setup

Run `device/emu-setup.sh` on your handheld in Desktop Mode. Every feature is optional — you choose what to enable:

1. **EmuDeck** — installs RetroArch, DuckStation, PCSX2, Dolphin, and other emulators with good defaults
2. **Folder structure** — creates `~/Emulation/roms/<system>/` for every supported system, plus `drop/`, `saves/`, and `backups/`
3. **ROM Sorter** — background service that watches your `drop/` folder and auto-sorts files into the right system folder
4. **Save Backup** — backs up your game saves every time you put the device to sleep (no more lost progress)
5. **Xbox Cloud Gaming** — sets up a browser shortcut to stream Xbox games (requires Game Pass Ultimate)
6. **Steam ROM Manager** — scans your ROM folders and adds every game to your Steam library with artwork (auto-add or manual preview)
7. **Tailscale** — access your device from anywhere, not just your home network (free for personal use)
8. **NAS Connection** — connects to your network drive for remote save storage
9. **SSH Access** — sets up passwordless access so your PC can send files to the device
10. **BIOS Check** — scans your BIOS folder and tells you exactly which files are present, missing, or wrong

### After Setup

- **Drop games** into `~/Emulation/drop/` — they sort themselves
- **Play games** — they're in `~/Emulation/roms/<system>/`
- **Sleep your device** — saves back up automatically
- **Xbox Cloud** — open from your app menu or Steam to stream Xbox games
- **Remote access** — SSH or push games from anywhere via Tailscale

---

## NAS Push

After the crawler finishes (or if you have trickle push off), push everything to your NAS:

```bash
bash nas/nas-push.sh
```

This syncs your staged downloads to the NAS and cleans up local files after a successful transfer. It's safe to run multiple times — it won't re-push files that are already there.

---

## Docker (Optional)

If you'd rather not install dependencies on your PC, run the crawler in Docker:

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
| `NAS_EXPORT` | Shared folder path on the NAS | _(none)_ |
| `DEVICE_HOST` | SSH address of your handheld (`user@ip`) | _(none)_ |
| `STAGING_DIR` | Where downloads go on your PC | `~/nas-staging` |
| `NAS_MOUNT` | Temporary mount point on the device | `/tmp/nas-roms` |
| `CRAWLER_PORT` | Port for the crawler web UI | `7072` |
| `DEFAULT_DELAY` | Seconds between downloads | `5` |
| `DEFAULT_DEPTH` | How deep the crawler follows links | `3` |
| `BACKUP_KEEP` | How many rolling save backups to keep | `10` |
| `TRICKLE_PUSH` | Push each file to NAS right after download | `true` |

---

## File Structure

```
DeckDock/
├── setup.sh                  # Interactive setup wizard
├── config.example.env        # Template configuration
├── config.env                # Your configuration (not in git)
│
├── crawler/
│   ├── crawler-gui.py        # The web crawler + UI
│   ├── requirements.txt      # Python dependencies
│   └── crawler-gui.service   # systemd service (optional)
│
├── device/
│   ├── emu-setup.sh          # First-time device setup
│   ├── rom-sorter.sh         # Sorts ROMs into system folders
│   ├── drop-cleaner.sh       # Cleans processed files from drop folder
│   ├── bios-check.sh         # Checks which BIOS files you have/need
│   ├── save-backup.sh        # Backs up emulator save files
│   ├── sleep-watcher.sh      # Triggers backup on device sleep
│   ├── rom-sorter.service    # systemd service for ROM sorting
│   ├── rom-sorter.timer      # Timer for periodic sorting
│   └── save-backup-watcher.service  # systemd service for sleep backup
│
├── nas/
│   └── nas-push.sh           # Push staged files to NAS
│
├── docker/
│   ├── Dockerfile            # Container build
│   └── docker-compose.yml    # One-command launch
│
└── docs/
    └── architecture.md       # Technical deep-dive
```

---

## Compression Standards

DeckDock automatically compresses games to save storage space while keeping perfect quality:

| File Type | Format | Why |
|-----------|--------|-----|
| Disc images (.iso, .bin/.cue, .gdi) | **CHD** | Lossless compression designed for disc images. Saves 30-60% space. |
| Cartridge ROMs (.nes, .snes, .gba, etc.) | **7z (ultra)** | LZMA2 compression at maximum settings. Saves 20-50% space. |
| Already optimal (.chd, .rvz, .cso, .pbp) | **Unchanged** | These are already compressed well. We don't touch them. |

All compression is lossless — your games are byte-for-byte identical when they're decompressed to play.

---

## Troubleshooting

**Crawler can't download from a site**
Some sites have bot protection. The crawler will automatically switch to a headless browser (Playwright) to handle this. Make sure Playwright is installed: `playwright install chromium`

**NAS push fails with permission errors**
Make sure your NAS export allows the user to write. For NFS, check that your PC's IP is in the allowed list and that `no_root_squash` or appropriate UID mapping is set.

**Save backups aren't running**
Check the service: `systemctl --user status save-backup-watcher.service`. The sleep watcher needs D-Bus access, which is available in Desktop Mode but may not work in Gaming Mode on all devices.

**ROM Sorter puts files in the wrong folder**
The sorter uses file extensions to guess the system. If a file has an ambiguous extension (like `.bin`), it may need manual sorting. Files it can't classify go to an `other/` folder.

---

## License

MIT. Do whatever you want with it.
