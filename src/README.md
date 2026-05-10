# Source patcher

`patch.py` rebuilds the patched `app.asar` from any installed Vysor.

## Requirements

- Python 3.10+
- Node.js + npm (`@electron/asar` is fetched on the fly via `npx`)

## Usage

```powershell
# from the repo root
cd src
python patch.py
```

The script:

1. Extracts the live `app.asar` from `%LOCALAPPDATA%\vysor\app-*\resources\`
   (the most recent version) into `./_extracted/`.
2. Applies the JS / HTML / background.js patches described in the top-level
   README.
3. Recomputes the SRI hashes for `index.html`.
4. Repacks `./_extracted/` back into `./app.asar` with `--unpack-dir native`.

The resulting `./app.asar` can be dropped over the original
`%LOCALAPPDATA%\vysor\app-*\resources\app.asar` (back it up first!).

If your Vysor build ships different bundle file names (e.g.
`app.<otherhash>.js`), edit the `APP_JS` constant near the top of
`patch.py` accordingly.
