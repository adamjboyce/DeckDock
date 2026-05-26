import {
  ButtonItem,
  PanelSection,
  PanelSectionRow,
  staticClasses,
} from "@decky/ui";
import { callable, definePlugin, toaster } from "@decky/api";
import { useEffect, useState } from "react";
import { FaGamepad } from "react-icons/fa";

type WindowsGame = { appid: number; name: string; has_config: boolean };

const listWindowsGames = callable<[], WindowsGame[]>("list_windows_games");
const logToBackend = callable<[message: string], void>("log");
const installPersonalConfig = callable<[appname: string], boolean>("install_personal_config");

type Fn = (...a: unknown[]) => unknown;

function getSteamClient(): Record<string, unknown> | null {
  if (typeof SteamClient === "undefined") return null;
  return SteamClient as unknown as Record<string, unknown>;
}

// Apply the DeckDock RPG Maker controller config to a non-Steam appid.
//
// Two-step process learned through iterative probing 2026-05-26
// (see reference_steamclient_set_selected_config.md):
//
//   1. Backend RPC copies the deckdock template to the per-game personal
//      config slot Steam looks up at game launch:
//        Steam Controller Configs/<uid>/config/<appname>/controller_legion_go_s.vdf
//      Steam does NOT auto-promote templates here — we must write it.
//
//   2. Frontend IPC tells Steam to remember the template URL:
//        Input.SetSelectedConfigForApp(uint appid, uint ctrlIdx, string url,
//                                       bool, bool)
//      URL must end in .vdf or Steam's selector won't resolve it.
//
// Both steps are required. The IPC alone leaves the per-game slot empty
// (Steam falls back to gamepad_joystick template). The file copy alone
// leaves the configset URL pointing at the default (Steam ignores the file).
const TEMPLATE_URL = "template://controller_legion_go_s_deckdock_rpgmaker.vdf";

async function applyDeckDockConfig(appid: number, appname: string): Promise<boolean> {
  // Step 1: write per-game personal config (backend)
  const installed = await installPersonalConfig(appname);
  if (!installed) {
    await logToBackend(`apply(${appid} ${appname}): personal config install failed`);
    return false;
  }

  // Step 2: tell Steam which config to use (frontend IPC)
  const sc = getSteamClient();
  if (!sc) {
    await logToBackend(`apply(${appid}): SteamClient unavailable`);
    return false;
  }
  const Input = sc.Input as Record<string, unknown> | undefined;
  const fn = Input?.SetSelectedConfigForApp as Fn | undefined;
  if (typeof fn !== "function") {
    await logToBackend(`apply(${appid}): SetSelectedConfigForApp missing`);
    return false;
  }
  try {
    fn.apply(Input, [appid, 0, TEMPLATE_URL, false, false]);
  } catch (e) {
    await logToBackend(`apply(${appid}) threw: ${(e as Error).message}`);
    return false;
  }

  // Readback verification. IPC errors are async — return-value polling is
  // the only honest check.
  const get = Input?.GetConfigForAppAndController as Fn | undefined;
  if (typeof get === "function") {
    try {
      const info = (await Promise.resolve(get.apply(Input, [appid, 0]) as unknown)) as
        | { URL?: string }
        | undefined;
      const url = info?.URL ?? "";
      const ok = url === TEMPLATE_URL;
      await logToBackend(`apply(${appid} ${appname}) URL=${url} ok=${ok}`);
      return ok;
    } catch (e) {
      await logToBackend(`apply(${appid}) readback threw: ${(e as Error).message}`);
    }
  }
  return true;
}

function Content() {
  const [games, setGames] = useState<WindowsGame[]>([]);
  const [busy, setBusy] = useState(false);

  const refresh = async () => {
    setBusy(true);
    try {
      setGames(await listWindowsGames());
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    refresh();
  }, []);

  const onApplyAll = async () => {
    setBusy(true);
    try {
      let applied = 0;
      let failed = 0;
      for (const g of games) {
        if (await applyDeckDockConfig(g.appid, g.name)) {
          applied++;
        } else {
          failed++;
        }
      }
      toaster.toast({
        title: "DeckDock",
        body: failed === 0 ? `Applied to ${applied} game(s)` : `Applied ${applied}, failed ${failed}`,
      });
      await refresh();
    } finally {
      setBusy(false);
    }
  };

  return (
    <PanelSection title="DeckDock Controller Mapper">
      <PanelSectionRow>
        <div style={{ fontSize: "12px", opacity: 0.7 }}>
          {games.length === 0
            ? "No Windows-tagged shortcuts found."
            : `${games.length} Windows game(s) detected`}
        </div>
      </PanelSectionRow>
      {games.map((g) => (
        <PanelSectionRow key={g.appid}>
          <div style={{ display: "flex", justifyContent: "space-between", fontSize: "12px" }}>
            <span>{g.name}</span>
          </div>
        </PanelSectionRow>
      ))}
      <PanelSectionRow>
        <ButtonItem layout="below" onClick={refresh} disabled={busy}>
          Refresh
        </ButtonItem>
      </PanelSectionRow>
      <PanelSectionRow>
        <ButtonItem layout="below" onClick={onApplyAll} disabled={busy || games.length === 0}>
          Apply DeckDock Config to All
        </ButtonItem>
      </PanelSectionRow>
    </PanelSection>
  );
}

export default definePlugin(() => ({
  name: "DeckDock Controller Mapper",
  titleView: <div className={staticClasses.Title}>DeckDock Controller</div>,
  alwaysRender: true,
  content: <Content />,
  icon: <FaGamepad />,
  onDismount() {
    // nothing to clean up
  },
}));
