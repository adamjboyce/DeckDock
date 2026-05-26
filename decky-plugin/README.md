# DeckDock Controller Mapper (Decky plugin)

Auto-applies the DeckDock RPG Maker keyboard controller config to Windows-tagged
non-Steam shortcuts added by `device/add-roms-to-steam.py`. Required because
SteamOS does not honor file-based controller config drops for non-Steam games —
the only working programmatic path is to call Steam's internal IPC from inside
its UI process, which is exactly where Decky plugins run.

## What it does

For each Windows-tagged non-Steam shortcut:

1. Writes the deckdock template to the per-game personal config slot
   (`Steam Controller Configs/<steamid>/config/<appname>/controller_legion_go_s.vdf`).
   Steam looks here at game launch — without this file, Steam silently falls
   back to its default Switch Pro gamepad template.
2. Calls `SteamClient.Input.SetSelectedConfigForApp(appid, 0,
   "template://controller_legion_go_s_deckdock_rpgmaker.vdf", false, false)`
   to tell Steam which config to remember.

Both steps are required. See
`~/.claude/projects/-home-jolly/memory/reference_steamclient_set_selected_config.md`
for the iterative-probe methodology that landed the signature.

## Architecture

- **`main.py`** — Python backend. Parses `shortcuts.vdf` to enumerate Windows
  shortcuts; exposes `list_windows_games`, `install_personal_config(appname)`,
  and `log` RPCs.
- **`src/index.tsx`** — TypeScript/React frontend running inside Steam's UI
  process. Holds the IPC apply logic (`applyDeckDockConfig`). The frontend
  is the only place where `SteamClient` is reachable.

## Prerequisites on the device

`device/add-roms-to-steam.py` must have run at least once to install the
deckdock template into `~/.local/share/Steam/controller_base/templates/`.
Templates are written CRLF (Steam's parser rejects LF-only VDFs).

## Build

```bash
pnpm install
pnpm run build
```

## Deploy to device

```bash
tar czf /tmp/deckdock-plugin.tgz dist main.py package.json
scp /tmp/deckdock-plugin.tgz deck@<device>:/tmp/
ssh deck@<device> '
  sudo tar -C ~/homebrew/plugins/deckdock-controller-mapper/ -xzf /tmp/deckdock-plugin.tgz
  sudo systemctl restart plugin_loader.service
'
```

## Use

Open Decky's Quick Access Menu, find the gamepad icon, tap **Apply DeckDock
Config to All**. Each game's keyboard mapping applies on next launch.
