"""
Rebuild a patched Vysor `app.asar` that thinks it is licensed.

Usage (from the directory containing this file):

    python patch.py

Requirements:
  - Python 3.10+
  - Node.js + npm  (the @electron/asar tool is fetched on the fly via npx)
  - network access to https://electron.vysor.io

Background
----------
Vysor 5.0.7 ships an OLD renderer bundle (`js/app.1f260ea3.js`, protocol
`vysor-io-117`) in its asar, but in production the desktop app loads the
renderer from https://electron.vysor.io, where Vysor pushes updates WITHOUT
bumping the desktop version. The remote renderer today is
`js/app.3c101571.js` (protocol `vysor-io-130`) and bundles a NEW Vysor APK
that works on modern Android (the old bundled APK's daemon dies with
"Killed" on Android 13+ -> "Unable to connect to control socket").

So the patcher:
  1. Locates the most recent installed Vysor (%LOCALAPPDATA%\\vysor\\app-*).
  2. Extracts its app.asar (and the unpacked native files) into ./_extracted.
  3. Syncs the web renderer from https://electron.vysor.io (index.html, app /
     chunk-vendors bundles, workers, css, the current Vysor APK), removing the
     stale bundled files. This is what makes the patch work on current phones.
  4. Forces the local renderer (USE_OFFLINE) in background.js.
  5. Patches the current app.<hash>.js so `licenseInfo.isLicensed` is always
     true (license observable, cache/server error paths, logout path, bitrate
     and resolution caps, "Upgrade to Vysor Pro" upsells, every read of
     licenseInfo.isLicensed).
  6. Recomputes the SRI hash for app.<hash>.js inside index.html.
  7. Repacks everything into ./app.asar with `--unpack-dir native`.
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
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
EXTRACTED = os.path.join(ROOT, '_extracted')
OUT_ASAR = os.path.join(ROOT, 'app.asar')
REMOTE = 'https://electron.vysor.io'

APP_JS_GLOB = 'js/app.*.js'
VENDORS_JS_GLOB = 'js/chunk-vendors.*.js'
VENDORS_CSS_GLOB = 'css/chunk-vendors.*.css'
WORKER_GLOBS = ['js/CanvasRenderer.worker.*.js',
                'js/H264NALDecoder.worker.*.js',
                'js/decoder.worker.*.js']
APK_GLOB = 'native/android/Vysor-release.*.apk'
# fixed-name renderer files that change over time; always re-sync from remote
TOP_SYNC = ['index.html', 'index.html.sig', 'manifest.json', 'check.html',
            'robots.txt', 'favicon.ico', 'firebase-messaging-sw.js',
            'service-worker.js', 'vysor-service-worker.js',
            'precache-manifest.*.js']


# ---------------------------------------------------------------- helpers

def run(cmd: list[str]) -> None:
    print('  $', ' '.join(cmd))
    subprocess.check_call(cmd, shell=False)


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def rel(path: str) -> str:
    return os.path.relpath(path, EXTRACTED).replace(os.sep, '/')


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


def extract(asar_path: str) -> None:
    if os.path.exists(EXTRACTED):
        shutil.rmtree(EXTRACTED)
    os.makedirs(EXTRACTED)
    npx = 'npx.cmd' if os.name == 'nt' else 'npx'
    try:
        run([npx, '--yes', '@electron/asar', 'extract', asar_path,
             EXTRACTED])
    except subprocess.CalledProcessError:
        print('  (extract reported missing unpacked files - ok)')
    # native files live next to the asar as app.asar.unpacked. When building
    # from the .bak we still want the live install's native dir (the app is
    # never repacked without it).
    unpacked = asar_path + '.unpacked'
    if not os.path.isdir(unpacked):
        live = os.path.join(os.path.dirname(asar_path), 'app.asar.unpacked')
        if os.path.isdir(live):
            unpacked = live
    if os.path.isdir(unpacked):
        for root, _dirs, files in os.walk(unpacked):
            dst_root = os.path.join(EXTRACTED, os.path.relpath(root, unpacked))
            os.makedirs(dst_root, exist_ok=True)
            for f in files:
                src = os.path.join(root, f)
                dst = os.path.join(dst_root, f)
                if not os.path.isfile(dst):
                    shutil.copy2(src, dst)


def first_match(globs) -> str:
    for g in globs:
        m = glob.glob(os.path.join(EXTRACTED, g))
        if m:
            return m[0]
    sys.exit(f'no file matching {globs} under {EXTRACTED}')


# ---------------------------------------------------------------- renderer sync

def download(relpath: str) -> None:
    """Fetch REMOTE/<relpath> into EXTRACTED/<relpath>."""
    data = fetch(REMOTE + '/' + relpath)
    p = _p(relpath)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, 'wb') as f:
        f.write(data)
    print(f'  synced {relpath}')


def _p(relpath: str) -> str:
    return os.path.join(EXTRACTED, *relpath.split('/'))


def sync_renderer() -> str:
    """Download the current renderer from electron.vysor.io and swap the
    stale bundled files for them. Returns the current app.<hash>.js name."""
    index = fetch(REMOTE + '/index.html').decode('utf-8')

    def find_bundle(pattern: str) -> str:
        m = re.search(pattern, index)
        if not m:
            sys.exit(f'could not parse bundle name ({pattern}) from index.html')
        return m.group(1)

    app_js = find_bundle(r'/js/(app\.[0-9a-f]{8}\.js)')
    vendors_js = find_bundle(r'/js/(chunk-vendors\.[0-9a-f]{8}\.js)')
    vendors_css = find_bundle(r'/css/(chunk-vendors\.[0-9a-f]{8}\.css)')
    print(f'  current renderer bundle: {app_js}')

    download('index.html')  # SRI for app.<hash>.js is fixed later
    downloaded = {'index.html'}
    sync_urls = ['index.html.sig', 'manifest.json', 'check.html', 'robots.txt',
                 'favicon.ico', 'firebase-messaging-sw.js',
                 'service-worker.js', 'vysor-service-worker.js',
                 'css/' + vendors_css, 'js/' + app_js, 'js/' + vendors_js]

    # workers / worklets / APK referenced by the app bundle
    app_src = fetch(REMOTE + '/js/' + app_js).decode('utf-8')
    sync_urls += re.findall(
        r'js/(?:CanvasRenderer\.worker|H264NALDecoder\.worker|decoder\.worker)'
        r'\.[0-9a-f]{8}\.worker\.js', app_src)
    sync_urls += ['js/audio-worklet.js', 'js/audio-worklet-16.js']
    apk_rel = re.search(r'/native/android/(Vysor-release\.[0-9a-f]+\.apk)',
                        app_src)
    if not apk_rel:
        sys.exit('could not find Vysor APK reference in app bundle')
    apk_new = apk_rel.group(1)
    sync_urls += ['native/android/' + apk_new]

    for u in sorted(set(sync_urls)):
        download(u)
    downloaded |= set(sync_urls)

    # precache manifest referenced by the service worker
    sw = open(_p('service-worker.js'), encoding='utf-8').read()
    pm = re.search(r'(precache-manifest\.[0-9a-f]+\.js)', sw)
    if pm:
        download(pm.group(1))
        downloaded.add(pm.group(1))

    # root packaged APK (fallback used by getPackagedVysorApkPath flow)
    shutil.copy2(_p('native/android/' + apk_new), _p('Vysor-release.apk'))
    print('  replaced Vysor-release.apk (root)')

    # remove stale renderer files that no longer match the current build
    kept = {os.path.normpath(_p(p)) for p in downloaded}
    kept |= {os.path.normpath(_p('Vysor-release.apk'))}
    for g in ([APP_JS_GLOB, VENDORS_JS_GLOB, VENDORS_CSS_GLOB] + WORKER_GLOBS
              + [APK_GLOB] + TOP_SYNC):
        for p in glob.glob(os.path.join(EXTRACTED, g)):
            if os.path.normpath(p) not in kept:
                os.remove(p)
                print(f'  removed stale {rel(p)}')
    return app_js


# ---------------------------------------------------------------- patches

def patch_background_js() -> None:
    bg = _p('dist_electron/bundled/main/background.js')
    src = open(bg, encoding='utf-8').read()
    old = "if (require('process').env.USE_OFFLINE) {"
    new = "if (true) { // patched: force local renderer"
    if src.count(old) != 1:
        sys.exit('background.js: expected exactly one USE_OFFLINE branch')
    open(bg, 'w', encoding='utf-8', newline='').write(src.replace(old, new))
    print(f'  patched {rel(bg)}')


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
            sys.exit(f'pattern not found in {os.path.basename(path)}: '
                     f'{old[:60]!r}')
        src = src.replace(old, new)

    # Blanket: any *read* of licenseInfo.isLicensed -> true literal.
    for old, new in [
        ('e.licenseInfo.isLicensed', '!0'),
        ('this.licenseInfo.isLicensed', '!0'),
    ]:
        src = src.replace(old, new)

    open(path, 'w', encoding='utf-8', newline='').write(src)
    print(f'  patched {rel(path)}')


def update_index_html(app_js_path: str) -> None:
    html_path = _p('index.html')
    html = open(html_path, encoding='utf-8').read()

    data = open(app_js_path, 'rb').read()
    sri = ('sha256-' + base64.b64encode(hashlib.sha256(data).digest()).decode()
           + ' sha384-'
           + base64.b64encode(hashlib.sha384(data).digest()).decode())

    bundle = os.path.basename(app_js_path)
    pattern = re.compile(
        r'(["\']/js/' + re.escape(bundle) + r'[^>]*?integrity=)"[^"]*"'
    )
    new_html, n = pattern.subn(lambda m: f'{m.group(1)}"{sri}"', html)
    if n == 0:
        pattern = re.compile(
            r'(integrity=)"[^"]*"([^>]*["\']/js/'
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

    print('==> Syncing renderer from electron.vysor.io')
    app_js = sync_renderer()

    print('==> Patching background.js')
    patch_background_js()

    print('==> Patching app.<hash>.js')
    app_js_path = _p('js/' + app_js)
    patch_app_js(app_js_path)

    print('==> Updating index.html SRI')
    update_index_html(app_js_path)

    print('==> Repacking')
    repack()

    print()
    print('Done. Patched archive is at:')
    print(' ', OUT_ASAR)
    print()
    print('Drop it on top of:')
    print(f'  {asar}')
    print('and copy the current APK into app.asar.unpacked/native/android/ '
          '(install.ps1 does this for you). Then clear %APPDATA%\\vysor caches.')


if __name__ == '__main__':
    main()
