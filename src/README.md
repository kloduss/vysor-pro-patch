# Source patcher

`patch.py` rebuilds the patched `app.asar` from any installed Vysor.

## Requirements

- Python 3.10+
- Node.js + npm (`@electron/asar` is fetched on the fly via `npx`)
- Network access to `https://electron.vysor.io`

## Usage

```powershell
# from the repo root
cd src
python patch.py
```

The script:

1. Extracts the live `app.asar` (or `app.asar.bak`) from
   `%LOCALAPPDATA%\vysor\app-*\resources\` (the most recent version) into
   `./_extracted/`.
2. Downloads the **current** renderer from `https://electron.vysor.io`
   (`index.html`, `app.<hash>.js`, `chunk-vendors.*`, workers, css, the current
   `Vysor-release.<hash>.apk`) and removes the stale bundled files. This is
   important: Vysor pushes renderer updates server-side without bumping the
   desktop version, and the old bundled renderer/APK no longer work on modern
   Android.
3. Forces the local renderer (`USE_OFFLINE` → `if (true)`) in `background.js`.
4. Applies the Pro patches described in the top-level README to the current
   `app.<hash>.js`.
5. Recomputes the SRI hash in `index.html`.
6. Repacks `./_extracted/` back into `./app.asar` with `--unpack-dir native`.

The resulting `./app.asar` can be dropped over the original
`%LOCALAPPDATA%\vysor\app-*\resources\app.asar` (back it up first!), and the
current APK must be copied into `app.asar.unpacked\native\android\` —
`install.ps1` at the repo root does all of that for you.

If Vysor changes the minified license code in `app.<hash>.js`, update the
pattern list in `patch_app_js()` — the script fails loudly on the first
missing pattern instead of producing a silently broken build.
