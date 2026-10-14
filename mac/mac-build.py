"""Build Unwip for Mac: Unwip.app for Apple Silicon and for Intel, each zipped, from a release package.

  python3 tools/mac-build.py dist/unwip-app-<version>.zip [--cache DIR]

What it makes (in dist/mac/):
  Unwip-Mac-AppleSilicon.zip   Macs with M1, M2, M3, M4 ... chips (most Macs since late 2020)
  Unwip-Mac-Intel.zip          older Intel Macs

How it's put together:
  - The official Electron engine for Mac (checked against Electron's published fingerprints).
  - Contents/Resources/app holds a small starter (boot.js). The first time Unwip opens, and whenever a newer
    download is opened, it copies Unwip's code to ~/Library/Application Support/Unwip/unwip-app and runs it
    from there. Updates replace that copy, so the signed app is never changed (macOS refuses to open a signed
    app whose files have been changed).
  - Signed ad hoc with rcodesign (no Apple account). With an Apple Developer ID this is where proper signing
    and notarising would go, and the "Open Anyway" step would disappear.
"""
import hashlib, json, os, plistlib, shutil, subprocess, sys, urllib.request, zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ELECTRON = '33.2.1'
pkg_zip = sys.argv[1]
arg = lambda k, d: sys.argv[sys.argv.index(k) + 1] if k in sys.argv else d
cache = arg('--cache', os.path.join(ROOT, 'dist', 'mac-cache'))
OUT = os.path.abspath(arg('--out', os.path.join(ROOT, 'dist', 'mac')))
ICON_SRC = arg('--icon', os.path.join(ROOT, 'brand', 'icon-square-1024.png'))
os.makedirs(cache, exist_ok=True); os.makedirs(OUT, exist_ok=True)

def fetch(url, dest):
    if not os.path.exists(dest):
        print('downloading', url)
        with urllib.request.urlopen(url) as r, open(dest + '.part', 'wb') as f: shutil.copyfileobj(r, f)
        os.rename(dest + '.part', dest)
    return dest

base = f'https://github.com/electron/electron/releases/download/v{ELECTRON}/'
sums = open(fetch(base + 'SHASUMS256.txt', os.path.join(cache, f'SHASUMS256-{ELECTRON}.txt'))).read()
def engine(arch):
    name = f'electron-v{ELECTRON}-darwin-{arch}.zip'
    path = fetch(base + name, os.path.join(cache, name))
    want = next(l.split()[0] for l in sums.splitlines() if l.strip().endswith(name))
    got = hashlib.sha256(open(path, 'rb').read()).hexdigest()
    if want != got: os.remove(path); sys.exit(f'{name} did not match its fingerprint')
    return path

# rcodesign: signs Mac apps from Linux
RCS = os.path.join(cache, 'rcodesign')
if not os.path.exists(RCS):
    t = fetch('https://github.com/indygreg/apple-platform-rs/releases/download/apple-codesign%2F0.29.0/apple-codesign-0.29.0-x86_64-unknown-linux-musl.tar.gz', os.path.join(cache, 'rcodesign.tgz'))
    subprocess.run(['tar', 'xzf', t, '-C', cache], check=True)
    shutil.copy(os.path.join(cache, 'apple-codesign-0.29.0-x86_64-unknown-linux-musl', 'rcodesign'), RCS); os.chmod(RCS, 0o755)

# the Mac icon: Unwip's icon on a rounded square with the margin macOS icons have
ICNS = os.path.join(cache, 'unwip.icns')
from PIL import Image, ImageDraw
src = Image.open(ICON_SRC).convert('RGBA')
S = 1024; pad = 100; inner = S - 2 * pad
canvas = Image.new('RGBA', (S, S), (0, 0, 0, 0))
art = src.resize((inner, inner), Image.LANCZOS)
mask = Image.new('L', (inner, inner), 0); ImageDraw.Draw(mask).rounded_rectangle([0, 0, inner - 1, inner - 1], radius=int(inner * 0.225), fill=255)
canvas.paste(art, (pad, pad), mask)
canvas.save(ICNS, sizes=[(16, 16), (32, 32), (64, 64), (128, 128), (256, 256), (512, 512), (1024, 1024)])

version = None
with zipfile.ZipFile(pkg_zip) as z: version = json.loads(z.read('package.json'))['version']

BOOT = r"""// Unwip for Mac - the starter. The signed app is never changed: Unwip runs from a copy of its code in
// ~/Library/Application Support/Unwip/unwip-app, which is what updates replace.
const { app } = require('electron');
const fs = require('fs'), path = require('path');
const bundled = path.join(__dirname, 'unwip');
const live = path.join(app.getPath('userData'), 'unwip-app');
const ver = d => { try { return JSON.parse(fs.readFileSync(path.join(d, 'package.json'), 'utf8')).version || '0'; } catch { return '0'; } };
const num = v => String(v).split('.').map(n => parseInt(n, 10) || 0);
const newer = (a, b) => { const x = num(a), y = num(b); for (let i = 0; i < 3; i++) if (x[i] !== y[i]) return x[i] > y[i]; return false; };
let dir = bundled;
try {
  // first run, or a newer Unwip.app than the copy: refresh the copy (built beside it, then swapped in)
  if (!fs.existsSync(path.join(live, 'main.js')) || newer(ver(bundled), ver(live))) {
    const tmp = live + '-new';
    fs.rmSync(tmp, { recursive: true, force: true });
    fs.cpSync(bundled, tmp, { recursive: true });
    fs.rmSync(live, { recursive: true, force: true });
    fs.renameSync(tmp, live);
  }
  dir = live;
} catch (e) { console.error('Unwip: running from the app itself', e); dir = bundled; }
process.env.UNWIP_UPDATE_TARGET = live;   // updates always go to the copy, never into the app
require(path.join(dir, 'main.js'));
"""

for arch, label in [('arm64', 'AppleSilicon'), ('x64', 'Intel')]:
    work = os.path.join(cache, 'build-' + arch)
    shutil.rmtree(work, ignore_errors=True); os.makedirs(work)
    subprocess.run(['unzip', '-q', engine(arch), '-d', work], check=True)        # unzip keeps the framework symlinks
    appdir = os.path.join(work, 'Unwip.app')
    os.rename(os.path.join(work, 'Electron.app'), appdir)
    res = os.path.join(appdir, 'Contents', 'Resources')
    for f in ['default_app.asar']:
        if os.path.exists(os.path.join(res, f)): os.remove(os.path.join(res, f))
    for f in ['LICENSE', 'LICENSES.chromium.html']:
        if os.path.exists(os.path.join(work, f)): shutil.copy(os.path.join(work, f), os.path.join(res, f))
    a = os.path.join(res, 'app'); os.makedirs(os.path.join(a, 'unwip'))
    with zipfile.ZipFile(pkg_zip) as z: z.extractall(os.path.join(a, 'unwip'))
    json.dump({'name': 'unwip', 'productName': 'Unwip', 'version': version, 'main': 'boot.js'}, open(os.path.join(a, 'package.json'), 'w'), indent=1)
    open(os.path.join(a, 'boot.js'), 'w').write(BOOT)
    os.remove(os.path.join(res, 'electron.icns')); shutil.copy(ICNS, os.path.join(res, 'unwip.icns'))
    pl = os.path.join(appdir, 'Contents', 'Info.plist')
    info = plistlib.load(open(pl, 'rb'))
    info.update({'CFBundleName': 'Unwip', 'CFBundleDisplayName': 'Unwip', 'CFBundleIdentifier': 'com.treatonoce.unwip',
                 'CFBundleShortVersionString': version, 'CFBundleVersion': version, 'CFBundleIconFile': 'unwip.icns',
                 'LSApplicationCategoryType': 'public.app-category.productivity', 'NSHumanReadableCopyright': 'Unwip - Write to finish',
                 'LSMinimumSystemVersion': info.get('LSMinimumSystemVersion', '10.15')})
    plistlib.dump(info, open(pl, 'wb'))
    # Electron's helper apps don't name their program (macOS assumes the bundle name); the signer needs it spelled out
    fw = os.path.join(appdir, 'Contents', 'Frameworks')
    for h in os.listdir(fw):
        if h.endswith('.app'):
            hp = os.path.join(fw, h, 'Contents', 'Info.plist'); hi = plistlib.load(open(hp, 'rb'))
            if not hi.get('CFBundleExecutable'): hi['CFBundleExecutable'] = h[:-4]; plistlib.dump(hi, open(hp, 'wb'))
    subprocess.run([RCS, 'sign', appdir], check=True, stdout=subprocess.DEVNULL)    # ad hoc: every part, helpers and frameworks included
    out = os.path.join(OUT, f'Unwip-Mac-{label}.zip')
    if os.path.exists(out): os.remove(out)
    subprocess.run(['zip', '-qry9', out, 'Unwip.app'], cwd=work, check=True)        # -y keeps symlinks as symlinks
    print(f'{label}: {out} ({os.path.getsize(out) / 1e6:.0f} MB)')
