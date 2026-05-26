"""DeckDock Controller Mapper — Decky plugin backend.

Reads shortcuts.vdf to enumerate non-Steam shortcuts tagged "Windows" (added
by DeckDock's add-roms-to-steam.py), and writes per-game personal controller
configs that the frontend then activates via Steam's internal IPC.
"""
from __future__ import annotations

import os
import shutil
import struct

import decky


# --- shortcuts.vdf parsing -------------------------------------------------
# Format reference: undocumented binary VDF used by Steam for non-Steam game
# shortcuts. Fields we care about per entry:
#   0x02 "appid" <uint32>
#   0x01 "appname" <cstr>
#   0x01 "exe" <cstr>
#   sub-map "tags" with string-indexed entries (we look for "Windows")


def _read_cstr(buf: bytes, offset: int) -> tuple[str, int]:
    """Read null-terminated string from buf starting at offset.
    Returns (string, position after the null byte)."""
    end = buf.index(b"\x00", offset)
    return buf[offset:end].decode("utf-8", errors="replace"), end + 1


def _read_key(buf: bytes, offset: int) -> tuple[str, int]:
    """Read the key (a cstring after a 1-byte type marker has been consumed)."""
    return _read_cstr(buf, offset)


def parse_shortcuts_vdf(path: str) -> list[dict]:
    """Return a list of shortcut dicts. Each dict has keys: appid, appname,
    exe, StartDir, LaunchOptions, tags (list of str)."""
    if not os.path.exists(path):
        return []
    with open(path, "rb") as f:
        buf = f.read()

    entries: list[dict] = []
    i = 0
    # File header: \x00 "shortcuts" \x00
    if buf[:1] == b"\x00":
        _, i = _read_cstr(buf, 1)  # consume "shortcuts"

    # Each shortcut: \x00 "<idx>" \x00 ...fields... \x08 \x08
    while i < len(buf):
        t = buf[i]
        if t == 0x08:  # end of shortcuts map
            break
        if t != 0x00:
            # Unexpected — skip
            i += 1
            continue
        # Start of a new shortcut entry
        _, i = _read_cstr(buf, i + 1)  # consume the index string
        entry: dict = {"tags": []}
        # Read fields until we hit the closing \x08
        while i < len(buf) and buf[i] != 0x08:
            ft = buf[i]
            i += 1
            key, i = _read_key(buf, i)
            if ft == 0x01:  # string
                val, i = _read_cstr(buf, i)
                entry[key] = val
            elif ft == 0x02:  # int32 LE
                val = struct.unpack_from("<I", buf, i)[0]
                i += 4
                entry[key] = val
            elif ft == 0x00:  # nested map
                # Read sub-entries until \x08
                sub: dict = {}
                while i < len(buf) and buf[i] != 0x08:
                    sft = buf[i]
                    i += 1
                    skey, i = _read_key(buf, i)
                    if sft == 0x01:
                        sval, i = _read_cstr(buf, i)
                        sub[skey] = sval
                    elif sft == 0x02:
                        sval = struct.unpack_from("<I", buf, i)[0]
                        i += 4
                        sub[skey] = sval
                    else:
                        # Unknown sub-type, abort sub-parse
                        break
                i += 1  # consume the \x08 closing the sub-map
                if key == "tags":
                    entry["tags"] = list(sub.values())
                else:
                    entry[key] = sub
            else:
                # Unknown type, bail on this entry
                break
        # Consume the closing \x08 of the entry
        if i < len(buf) and buf[i] == 0x08:
            i += 1
        entries.append(entry)
    return entries


def _steam_userdata_root() -> str:
    home = os.path.expanduser("~")
    return os.path.join(home, ".local", "share", "Steam", "userdata")


def _steam_user_id() -> str | None:
    root = _steam_userdata_root()
    if not os.path.isdir(root):
        return None
    for entry in sorted(os.listdir(root)):
        if entry.isdigit():
            return entry
    return None


def _per_appid_config_dir(appid: int) -> str:
    home = os.path.expanduser("~")
    uid = _steam_user_id() or "0"
    return os.path.join(
        home, ".local", "share", "Steam",
        "steamapps", "common", "Steam Controller Configs",
        uid, "config", str(appid),
    )


def _per_game_personal_config_dir(appname: str) -> str:
    """Per-game personal config directory Steam looks up at game launch.

    For non-Steam shortcuts Steam uses the appname (with spaces) as the
    directory name, not the appid. Confirmed empirically 2026-05-26.
    """
    home = os.path.expanduser("~")
    uid = _steam_user_id() or "0"
    return os.path.join(
        home, ".local", "share", "Steam",
        "steamapps", "common", "Steam Controller Configs",
        uid, "config", appname,
    )


# Template basename Steam loads via SetSelectedConfigForApp. The Legion Go S
# reports as controller_type=controller_legion_go_s, so the file Steam looks
# for in the per-game personal config dir is controller_legion_go_s.vdf —
# this name is fixed by Steam, not configurable per-game.
DECKDOCK_TEMPLATE_FILENAME = "controller_legion_go_s_deckdock_rpgmaker.vdf"
PERSONAL_CONFIG_FILENAME = "controller_legion_go_s.vdf"


def _template_path() -> str:
    home = os.path.expanduser("~")
    return os.path.join(
        home, ".local", "share", "Steam",
        "controller_base", "templates", DECKDOCK_TEMPLATE_FILENAME,
    )


class Plugin:
    """RPC surface for the frontend."""

    async def _main(self):
        decky.logger.info("DeckDock Controller Mapper starting up")

    async def _unload(self):
        decky.logger.info("DeckDock Controller Mapper unloading")

    async def list_windows_games(self) -> list[dict]:
        """Return [{appid, name, has_config}, ...] for shortcuts tagged 'Windows'."""
        uid = _steam_user_id()
        if not uid:
            decky.logger.warning("no Steam user found in userdata/")
            return []
        vdf = os.path.join(
            _steam_userdata_root(), uid, "config", "shortcuts.vdf"
        )
        all_shortcuts = parse_shortcuts_vdf(vdf)
        windows = []
        for s in all_shortcuts:
            tags = s.get("tags") or []
            if not any(t == "Windows" for t in tags):
                continue
            appid = s.get("appid")
            if not isinstance(appid, int):
                continue
            cfg_dir = _per_appid_config_dir(appid)
            has_config = any(
                fname.startswith("controller_") and fname.endswith(".vdf")
                for fname in (os.listdir(cfg_dir) if os.path.isdir(cfg_dir) else [])
            )
            windows.append({
                "appid": appid,
                "name": s.get("appname") or "(unnamed)",
                "has_config": has_config,
            })
        decky.logger.info(f"list_windows_games → {len(windows)} entries")
        return windows

    async def log(self, message: str) -> None:
        """Pipe a frontend log message into Decky's plugin log so I can read it."""
        decky.logger.info(f"[frontend] {message}")

    async def install_personal_config(self, appname: str) -> bool:
        """Copy the DeckDock template into the per-game personal config slot.

        SetSelectedConfigForApp tells Steam *which* template to remember, but
        Steam doesn't auto-promote templates into the per-game personal
        config slot it consults at game launch. We have to write that file
        ourselves. Steam then loads it via 'Local Selection Path' on launch.
        """
        template = _template_path()
        if not os.path.isfile(template):
            decky.logger.error(f"template missing: {template}")
            return False
        dst_dir = _per_game_personal_config_dir(appname)
        try:
            os.makedirs(dst_dir, exist_ok=True)
            dst = os.path.join(dst_dir, PERSONAL_CONFIG_FILENAME)
            shutil.copyfile(template, dst)
            decky.logger.info(f"installed personal config: {dst}")
            return True
        except OSError as e:
            decky.logger.error(f"copy failed for {appname}: {e}")
            return False

