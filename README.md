# vysor-pro-patch

One-click patcher for **Vysor 5.0.7** (Windows) that unlocks the Pro features
(higher video bitrate, full resolution, Pro-only toggles) by patching the
local `app.asar` bundle. No license, no login, no network calls to the
billing servers — the renderer is just told it is already licensed.

## Why the old patch broke (important)

Vysor serves its renderer from `https://electron.vysor.io` and pushes updates
**without bumping the desktop version**. The 5.0.7 asar bundles an old
renderer (`js/app.1f260ea3.js`, protocol `vysor-io-117`) and an old Vysor APK
that crashes on modern Android — its daemon dies with `Killed` right after
`Starting daemon`, port 53518 never opens, and the PC reports
*"Unable to connect to control socket."*.

The current remote renderer (`js/app.3c101571.js`, protocol `vysor-io-130`)
ships a NEW Vysor APK whose daemon works on Android 15. So this patcher does
not patch the stale bundled renderer. Instead it:

1. **Syncs the web renderer from `https://electron.vysor.io`** — the current
   `index.html`, `app.<hash>.js`, `chunk-vendors.*`, workers, css and the
   current `Vysor-release.<hash>.apk` — and drops the stale bundled files.
2. Forces the local renderer (`USE_OFFLINE` → `if (true)` in `background.js`),
   so the renderer is loaded from the local `app://` protocol instead of the
   remote URL (otherwise the license patches are silently ignored).
3. Patches the current `app.<hash>.js` so the app thinks it is Pro.
4. Recomputes the SRI hash for the patched bundle in `index.html`.
5. Deploys the current APK into `app.asar.unpacked/native/android/`.

## What it does

| File | Patch |
|---|---|
| `dist_electron/bundled/main/background.js` | `if (require('process').env.USE_OFFLINE)` → `if (true)` so the renderer is loaded from the local `app://` protocol instead of `https://electron.vysor.io`. |
| `js/app.<hash>.js` (current remote bundle) | Initial license observable starts as `{isLicensed:!0,fromCache:!0,source:{...}}`. Both license-check `catch` blocks keep `isLicensed:!0`. The logout / "remove license" path stops resetting `isLicensed`. `sendBitrate()` uses `16e6` unconditionally, `sendResolution()` uses max resolution, the bitrate/resolution watchers never pop the "Upgrade to Vysor Pro" upsell, and every read of `licenseInfo.isLicensed` is rewritten to `!0`. |
| `index.html` | The `sha256`/`sha384` Subresource-Integrity hash for the patched `app.<hash>.js` is recomputed so Chromium does not refuse to execute it. |
| `app.asar.unpacked/native/android/` | Current `Vysor-release.<hash>.apk` (protocol `vysor-io-130`) is deployed so the bundled daemon matches the renderer. |

The original `app.asar` is saved next to the live one as `app.asar.bak`, so
the install step is fully reversible.

## Supported version

Tested against **Vysor 5.0.7** (the Squirrel build that lives at
`%LOCALAPPDATA%\vysor\app-5.0.7\resources\app.asar`). Because the renderer is
fetched from the server at build time, the same 5.0.7 shell keeps working even
after Vysor pushes new renderer bundles (as long as the minified license
patterns in `app.<hash>.js` don't change — see
[Building](#building-for-another-version)).

## Install (one command)

Open **PowerShell** (no admin needed) and run:

```powershell
iwr https://raw.githubusercontent.com/kloduss/vysor-pro-patch/main/install.ps1 -UseBasicParsing | iex
```

That:
1. Stops any running `Vysor.exe`.
2. Backs up the current `app.asar` to `app.asar.bak` (only if no backup exists yet).
3. Downloads the patched `app.asar` from this repo.
4. Downloads the current `Vysor-release.apk` and drops it into
   `resources\app.asar.unpacked\native\android\` (removing any older one).
5. Wipes the Vysor service-worker / HTTP / V8 caches under `%APPDATA%\vysor\`
   so the renderer has to re-load the patched bundle from disk on the next launch.

Or, if you cloned the repo, just double-click `install.bat`.

## Uninstall

```powershell
iwr https://raw.githubusercontent.com/kloduss/vysor-pro-patch/main/uninstall.ps1 -UseBasicParsing | iex
```

Or double-click `uninstall.bat` from a clone. The script restores
`app.asar.bak` over `app.asar` and clears the same caches.

## Building for another version

```powershell
# requires Python 3 and Node.js (for the @electron/asar tool)
git clone https://github.com/kloduss/vysor-pro-patch.git
cd vysor-pro-patch\src
python patch.py
```

`src/patch.py` extracts the live `app.asar` (or its `.bak`), downloads the
current renderer from `https://electron.vysor.io`, applies the patches,
recomputes the SRI hash and repacks. If Vysor ever changes the minified
license code in `app.<hash>.js`, the pattern list in `patch_app_js()` will
need to be updated — the script fails loudly on the first missing pattern.

## Disclaimer

For personal / educational use. If you actually rely on Vysor day-to-day,
buy a license from the developer at <https://vysor.io>.
