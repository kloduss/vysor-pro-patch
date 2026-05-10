"""
Rebuild a patched Vysor `app.asar` that thinks it is licensed.

Usage (from the directory containing this file):

    python patch.py

Requirements:
  - Python 3.10+
  - Node.js + npm  (the @electron/asar tool is fetched on the fly via npx)

The script:
  1. Locates the most recent installed Vysor (%LOCALAPPDATA%\\vysor\\app-*).
  2. Extracts its app.asar (and the unpacked native files) into ./_extracted.
  3. Applies the patches: force the local renderer (USE_OFFLINE), force
     `licenseInfo.isLicensed = true` everywhere, drop the bitrate /
     resolution caps and the "Upgrade to Vysor Pro" upsell.
  4. Recomputes the SRI hash for app.<hash>.js inside index.html.
  5. Repacks everything into ./app.asar with `--unpack-dir native`.

If your Vysor build ships a different renderer bundle hash, edit
APP_JS_GLOB below.
"""
from __future__ import annotations

import base64
import glob
import hashlib
import os
import re
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
EXTRACTED = os.path.join(ROOT, '_extracted')
OUT_ASAR = os.path.join(ROOT, 'app.asar')

# Filename glob for the renderer bundle inside the asar. Vysor ships it as
# `js/app.<8 hex>.js` produced by webpack. We pick the first match.
APP_JS_GLOB = 'js/app.*.js'


def find_install() -> tuple[str, str]:
    """Return (asar_path, version)."""
    local = os.environ.get('LOCALAPPDATA')
    if not local:
        sys.exit('LOCALAPPDATA is not set; not running on Windows?')
    candidates = sorted(glob.glob(os.path.join(local, 'vysor', 'app-*')),
                        reverse=True)
    if not candidates:
        sys.exit(f'No Vysor install found under {local}\\vysor\\app-*')
    install = candidates[0]
    asar = os.path.join(install, 'resources', 'app.asar')
    bak = asar + '.bak'
    if not os.path.isfile(asar):
        sys.exit(f'app.asar not found at {asar}')
    src = bak if os.path.isfile(bak) else asar
    version = os.path.basename(install).removeprefix('app-')
    return src, version


def run(cmd: list[str]) -> None:
    print('  $', ' '.join(cmd))
    subprocess.check_call(cmd, shell=False)


def extract(asar_path: str) -> None:
    """Extract `asar_path` into EXTRACTED, then copy in the unpacked
    native dir (which lives next to the asar)."""
    if os.path.exists(EXTRACTED):
        shutil.rmtree(EXTRACTED)
    os.makedirs(EXTRACTED)
    npx = 'npx.cmd' if os.name == 'nt' else 'npx'
    # Some unpacked native files won't exist inside the asar itself; the
    # extract command exits non-zero but still extracts everything that
    # is actually packed. We swallow the error.
    try:
        run([npx, '--yes', '@electron/asar', 'extract', asar_path,
             EXTRACTED])
    except subprocess.CalledProcessError:
        print('  (extract reported missing unpacked files - ok)')

    unpacked = asar_path + '.unpacked'
    if os.path.isdir(unpacked):
        for root, _dirs, files in os.walk(unpacked):
            rel = os.path.relpath(root, unpacked)
            dst_root = os.path.join(EXTRACTED, rel) if rel != '.' \
                else EXTRACTED
            os.makedirs(dst_root, exist_ok=True)
            for f in files:
                src = os.path.join(root, f)
                dst = os.path.join(dst_root, f)
                if not os.path.isfile(dst):
                    shutil.copy2(src, dst)


def find_app_js() -> str:
    matches = glob.glob(os.path.join(EXTRACTED, APP_JS_GLOB))
    if not matches:
        sys.exit(f'No file matching {APP_JS_GLOB} under {EXTRACTED}')
    if len(matches) > 1:
        # take the smallest 8-hex one which is the actual app bundle
        matches = [m for m in matches if re.search(r'app\.[0-9a-f]{8}\.js$',
                                                   m)]
    return matches[0]


def patch_background_js() -> None:
    bg = os.path.join(EXTRACTED, 'dist_electron', 'bundled', 'main',
                      'background.js')
    src = open(bg, encoding='utf-8').read()
    old = "if (require('process').env.USE_OFFLINE) {"
    new = "if (true) { // patched: force local renderer"
    if old not in src:
        sys.exit('background.js: USE_OFFLINE branch not found')
    if src.count(old) != 1:
        sys.exit('background.js: expected exactly one USE_OFFLINE branch')
    open(bg, 'w', encoding='utf-8', newline='').write(src.replace(old, new))
    print(f'  patched {os.path.relpath(bg, EXTRACTED)}')


def patch_app_js(path: str) -> None:
    src = open(path, encoding='utf-8').read()

    replacements = [
        # initial license observable: start as licensed, with a stub source
        # so templates that touch licenseInfo.source.managementUrl don't NPE.
        ('observable({isLicensed:!1,fromCache:!0})',
         'observable({isLicensed:!0,fromCache:!0,'
         'source:{managementUrl:"https://billing.vysor.io",'
         'managementText:"Account Management"}})'),
        # cache + server check error catch -> still treat as licensed
        ('{source:o,isLicensed:!1,error:r}',
         '{source:o,isLicensed:!0,error:r}'),
        # remove-license / logout path: don't reset to false
        ('le.isLicensed=!1,le.fromCache=!0',
         'le.isLicensed=!0,le.fromCache=!0'),
        # bitrate cap inside sendBitrate(): always 16 Mbps
        ('t=this.licenseInfo.isLicensed?16e6:e', 't=16e6'),
        # resolution cap inside sendResolution(): always max
        ('e=this.licenseInfo.isLicensed?1:0', 'e=1'),
        # bitrate-watcher upsell: never trigger
        ('!le.isLicensed&&this.deviceSettings.bitrate>e.bitrate&&'
         '(this.showUpsell="Upgrade to Vysor Pro for higher video bitrates.",'
         'this.deviceSettings.bitrate=e.bitrate)',
         '!1&&this.deviceSettings.bitrate>e.bitrate&&'
         '(this.showUpsell="Upgrade to Vysor Pro for higher video bitrates.",'
         'this.deviceSettings.bitrate=e.bitrate)'),
        # resolution-watcher upsell: never trigger
        ('!le.isLicensed&&this.deviceSettings.resolution>e.resolution&&'
         '(this.showUpsell="Upgrade to Vysor Pro for better video resolutions.",'
         'this.deviceSettings.resolution=e.resolution)',
         '!1&&this.deviceSettings.resolution>e.resolution&&'
         '(this.showUpsell="Upgrade to Vysor Pro for better video resolutions.",'
         'this.deviceSettings.resolution=e.resolution)'),
    ]
    for old, new in replacements:
        if old not in src:
            sys.exit(f'pattern not found in app.js: {old[:60]!r}')
        src = src.replace(old, new)

    # Blanket: any *read* of licenseInfo.isLicensed -> true literal.
    for old, new in [
        ('e.licenseInfo.isLicensed', '!0'),
        ('this.licenseInfo.isLicensed', '!0'),
    ]:
        src = src.replace(old, new)

    open(path, 'w', encoding='utf-8', newline='').write(src)
    print(f'  patched {os.path.relpath(path, EXTRACTED)}')


def update_index_html(app_js_path: str) -> None:
    """Rewrite the SRI hash in index.html for the modified app.js so that
    Chromium does not refuse to execute it."""
    html_path = os.path.join(EXTRACTED, 'index.html')
    html = open(html_path, encoding='utf-8').read()

    data = open(app_js_path, 'rb').read()
    sri = ('sha256-' + base64.b64encode(hashlib.sha256(data).digest()).decode()
           + ' sha384-'
           + base64.b64encode(hashlib.sha384(data).digest()).decode())

    bundle = os.path.basename(app_js_path)
    pattern = re.compile(
        r'(["\'/]js/' + re.escape(bundle) + r'[^>]*?integrity=)"[^"]*"'
    )
    new_html, n = pattern.subn(lambda m: f'{m.group(1)}"{sri}"', html)
    if n == 0:
        pattern = re.compile(
            r'(integrity=)"[^"]*"([^>]*["\'/]js/'
            + re.escape(bundle) + r')'
        )
        new_html, n = pattern.subn(
            lambda m: f'{m.group(1)}"{sri}"{m.group(2)}', html
        )
    if n == 0:
        sys.exit(f'could not update SRI for {bundle} in index.html')

    open(html_path, 'w', encoding='utf-8', newline='').write(new_html)
    print(f'  rewrote {n} integrity attribute(s) in index.html')


def repack() -> None:
    if os.path.exists(OUT_ASAR):
        os.remove(OUT_ASAR)
    npx = 'npx.cmd' if os.name == 'nt' else 'npx'
    run([npx, '--yes', '@electron/asar', 'pack', EXTRACTED, OUT_ASAR,
         '--unpack-dir', 'native'])
    size = os.path.getsize(OUT_ASAR)
    print(f'  wrote {OUT_ASAR} ({size} bytes)')


def main() -> None:
    asar, version = find_install()
    print(f'==> Using Vysor v{version}: {asar}')

    print('==> Extracting')
    extract(asar)

    print('==> Patching background.js')
    patch_background_js()

    print('==> Patching app.<hash>.js')
    app_js = find_app_js()
    patch_app_js(app_js)

    print('==> Updating index.html SRI')
    update_index_html(app_js)

    print('==> Repacking')
    repack()

    print()
    print('Done. Patched archive is at:')
    print(' ', OUT_ASAR)
    print()
    print('Drop it on top of:')
    print(f'  {asar}')
    print('after backing it up. Then clear %APPDATA%\\vysor caches.')


if __name__ == '__main__':
    main()
