"""Build a private offline snapshot from the user's own MCHOSE HUB cache."""
from __future__ import annotations

import argparse
import gzip
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit, urljoin
import zlib

HERE = Path(__file__).resolve().parent
ENTRY = '"use strict";\nrequire("./offline-bootstrap.cjs");\n'
INDEX_KEY = 'cdn.mchose.com.cn/customPage/prod/index.html'


def public_key(url: str) -> str | None:
    """Allow only known static/public routes, never API or account responses."""
    u = urlsplit(url)
    if u.scheme != 'https' or u.port not in (None, 443) or u.username or u.password:
        return None
    routes = {
        'cdn.mchose.com.cn': ('/customPage/', '/configCenter/', '/OTA/PC/prod/otaFile/'),
        'www.mchose.com.cn': ('/assets/', '/cizhou/'),
    }
    if not (u.path.startswith(routes.get(u.hostname, ('\0',))) or
            (u.hostname == 'www.mchose.com.cn' and u.path == '/tray.html')):
        return None
    if any(part in ('.', '..') for part in u.path.split('/')) or '\\' in u.path:
        return None
    allowed = ('.html', '.js', '.css', '.json', '.svg', '.png', '.jpg', '.jpeg',
               '.webp', '.gif', '.ico', '.woff', '.woff2', '.ttf', '.webm', '.mp4')
    if not u.path.lower().endswith(allowed):
        return None
    return u.hostname + u.path


class Cache:
    """Chromium blockfile cache reader; no Cookies or profile databases are read."""
    def __init__(self, directory: Path):
        self.directory = directory
        self.blocks = {}

    def read(self, address: int, size: int) -> bytes:
        if not address or size <= 0:
            return b''
        kind = (address >> 28) & 7
        if kind == 0:
            return (self.directory / f'f_{address & 0xfffffff:06x}').read_bytes()[:size]
        block_size = {1: 36, 2: 256, 3: 1024, 4: 4096}.get(kind)
        if not block_size:
            raise ValueError('Unsupported cache address')
        number = (address >> 16) & 255
        if number not in self.blocks:
            self.blocks[number] = (self.directory / f'data_{number}').read_bytes()
        start = 8192 + (address & 65535) * block_size
        return self.blocks[number][start:start + size]

    def entries(self):
        b = (self.directory / 'data_1').read_bytes()
        for off in range(8192, len(b) - 255, 256):
            size, longkey = struct.unpack_from('<iI', b, off + 32)
            if not 0 < size < 16000:
                continue
            try:
                key = (self.read(longkey, size) if longkey else b[off + 96:off + 96 + size]).decode()
                sizes = struct.unpack_from('<4i', b, off + 40)
                addresses = struct.unpack_from('<4I', b, off + 56)
                body = self.read(addresses[1], sizes[1])
                if len(body) != sizes[1] or not body:
                    continue
                headers = self.read(addresses[0], sizes[0])
                yield key, headers, body
            except (OSError, ValueError, UnicodeError, struct.error):
                continue


def write_resource(out: Path, manifest: dict, key: str, body: bytes, mime: str = ''):
    target = out / key
    if not target.resolve().is_relative_to(out.resolve()):
        raise ValueError('Resource path escapes snapshot')
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(body)
    manifest[key] = {'file': key, 'size': len(body), 'type': mime}


def extract(cache_dir: Path, out: Path) -> dict:
    try:
        import brotli
    except ImportError:
        raise ValueError('Install the cache decoder first: py -3 -m pip install -r requirements.txt')
    manifest, ranges = {}, {}
    for rawkey, headers, body in Cache(cache_dir).entries():
        partial = re.fullmatch(r'Range_1/0/(https://.*):[0-9a-f]+:(\d+)', rawkey)
        if partial:
            key = public_key(partial[1])
            if key:
                ranges.setdefault(key, {})[int(partial[2])] = body
            continue
        if not rawkey.startswith('1/0/https://'):
            continue
        key = public_key(rawkey[4:])
        if not key:
            continue
        encoding = re.search(rb'content-encoding:([^\0\r\n]+)', headers, re.I)
        try:
            if encoding:
                enc = encoding.group(1).strip().lower()
                if enc == b'gzip':
                    body = gzip.decompress(body)
                elif enc == b'deflate':
                    body = zlib.decompress(body)
                elif enc == b'br':
                    body = brotli.decompress(body)
                else:
                    continue
        except (ValueError, OSError, zlib.error, brotli.error):
            continue
        mime = re.search(rb'content-type:([^\0\r\n]+)', headers, re.I)
        write_resource(out, manifest, key, body, mime.group(1).decode(errors='replace').strip() if mime else '')
    for key, chunks in ranges.items():
        # Only reconstruct a contiguous video with a short final 1 MiB chunk.
        if sorted(chunks) == list(range(len(chunks))) and len(chunks[max(chunks)]) < 1048576:
            write_resource(out, manifest, key, b''.join(chunks[i] for i in sorted(chunks)), 'video/webm')
    return manifest


def dependencies(out: Path, manifest: dict) -> set[str]:
    """Static imports and Vite dependency lists in the saved renderer build."""
    found = set()
    for key, item in list(manifest.items()):
        if not key.startswith('cdn.mchose.com.cn/customPage/prod/') or not key.endswith(('.js', '.css', '.html')):
            continue
        text = (out / item['file']).read_text('utf-8', errors='replace')
        for ref in re.findall(r'''["']((?:\.?\.?/|https://)[^"'\s]+\.(?:js|css|woff2?)(?:\?[^"']*)?)["']''', text):
            target = public_key(urljoin('https://' + key, ref))
            if target and '/assets/config/' not in target:
                found.add(target)
    return found - manifest.keys()


def download_missing(out: Path, manifest: dict) -> list[str]:
    """One-time optional public-resource download, no cookies/authentication."""
    if not shutil.which('curl.exe'):
        raise ValueError('curl.exe is required for --download-missing (included in recent Windows).')
    attempted, failed = set(), []
    while True:
        pending = dependencies(out, manifest) - attempted
        if not pending:
            return failed
        for key in sorted(pending):
            attempted.add(key)
            target = out / key
            target.parent.mkdir(parents=True, exist_ok=True)
            result = subprocess.run(['curl.exe', '-q', '-4', '--fail', '--silent', '--show-error',
                                     '--retry', '1', '--connect-timeout', '8', '--max-time', '30',
                                     'https://' + key, '-o', str(target)], capture_output=True)
            if result.returncode:
                target.unlink(missing_ok=True)
                failed.append(key)
            else:
                write_resource(out, manifest, key, target.read_bytes())


def valid_app(path: Path) -> bool:
    main = path / 'resources/app/out/main'
    return (path / 'MCHOSE HUB.exe').is_file() and all((main / n).is_file() for n in ('index.js', 'index.jsc', 'bytecode-loader.cjs'))


def running_hub() -> bool:
    if sys.platform != 'win32':
        return False
    result = subprocess.run(['tasklist.exe', '/FI', 'IMAGENAME eq MCHOSE HUB.exe', '/FO', 'CSV', '/NH'],
                            capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:
        raise ValueError('Could not check running processes. Close MCHOSE HUB and retry.')
    return b'"mchose hub.exe"' in result.stdout.lower()


def discover_apps() -> list[Path]:
    candidates = []
    for variable, suffix in [('LOCALAPPDATA', 'Programs/MCHOSE HUB'), ('LOCALAPPDATA', 'MCHOSE HUB'),
                             ('ProgramFiles', 'MCHOSE HUB'), ('ProgramFiles(x86)', 'MCHOSE HUB')]:
        if os.environ.get(variable):
            candidates.append(Path(os.environ[variable]) / suffix)
    if sys.platform == 'win32':
        import winreg
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
                try:
                    with winreg.OpenKey(hive, r'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall', 0, winreg.KEY_READ | view) as parent:
                        for i in range(winreg.QueryInfoKey(parent)[0]):
                            with winreg.OpenKey(parent, winreg.EnumKey(parent, i)) as item:
                                try:
                                    name = winreg.QueryValueEx(item, 'DisplayName')[0]
                                    if 'MCHOSE' not in name.upper():
                                        continue
                                    try:
                                        candidates.append(Path(winreg.QueryValueEx(item, 'InstallLocation')[0]))
                                    except OSError:
                                        pass
                                    try:
                                        icon = winreg.QueryValueEx(item, 'DisplayIcon')[0].split(',')[0].strip('"')
                                        candidates.append(Path(icon).parent)
                                    except OSError:
                                        pass
                                except OSError:
                                    pass
                except OSError:
                    pass
    return sorted({p.resolve() for p in candidates if valid_app(p)}, key=str)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app-dir', type=Path, help='Folder containing MCHOSE HUB.exe')
    parser.add_argument('--profile', type=Path, help='Default: %%APPDATA%%/MCHOSEHUB')
    parser.add_argument('--download-missing', action='store_true', help='Download missing public JS/CSS/font dependencies once')
    parser.add_argument('--dry-run', action='store_true', help='Extract and validate in a temporary folder; do not patch')
    parser.add_argument('--uninstall', action='store_true', help='Restore original entry point; preserve snapshot and profile')
    parser.add_argument('--interactive', action='store_true', help='Ask for the application folder if discovery fails (used by install.cmd)')
    args = parser.parse_args(argv)
    apps = [args.app_dir.resolve()] if args.app_dir else discover_apps()
    if args.interactive and (len(apps) != 1 or not valid_app(apps[0])):
        print('Select the folder containing MCHOSE HUB.exe.')
        for i, candidate in enumerate(apps, 1):
            print(f'  {i}. {candidate}')
        while True:
            answer = input('Paste the folder path, enter a number above, or Q to cancel: ').strip().strip('"')
            if answer.lower() == 'q' or not answer:
                raise ValueError('Setup cancelled. Application files were not changed.')
            choice = apps[int(answer) - 1] if answer.isdigit() and 1 <= int(answer) <= len(apps) else Path(answer).expanduser().resolve()
            if valid_app(choice):
                apps = [choice]
                break
            print('MCHOSE HUB.exe and supported application files were not found in that folder. Try again.')
    if len(apps) != 1:
        raise ValueError('Specify --app-dir "C:\\path\\to\\MCHOSE HUB". Candidates: ' + ', '.join(map(str, apps)))
    app_dir = apps[0]
    if not valid_app(app_dir):
        raise ValueError('Unsupported installation: expected resources/app/out/main/index.jsc and index.js.')
    main_dir = app_dir / 'resources/app/out/main'
    entry, backup = main_dir / 'index.js', main_dir / 'index.js.offline-original'
    original = entry.read_text('utf-8-sig')
    if not args.dry_run:
        while running_hub():
            if not args.interactive:
                raise ValueError('MCHOSE HUB is running. Fully quit all copies using the tray menu, then retry.')
            print('MCHOSE HUB is still running. Fully quit every copy using its tray menu.')
            if input('Press Enter after closing it, or enter Q to cancel: ').strip().lower() == 'q':
                raise ValueError('Setup cancelled. Application files were not changed.')
    if args.uninstall:
        if not backup.exists() or original != ENTRY:
            raise ValueError('No matching installed patch/backup. Nothing changed.')
        if not args.dry_run:
            shutil.copyfile(backup, entry)
        print('Would restore original entry point.' if args.dry_run else
              'Original entry point restored. Snapshot and user settings were preserved.')
        return
    if original != ENTRY and not re.fullmatch(r'\s*["\']use strict["\'];\s*require\(["\']\./bytecode-loader\.cjs["\']\);\s*require\(["\']\./index\.jsc["\']\);\s*', original):
        raise ValueError('Unknown entry point; refusing to overwrite a different version or another patch.')
    profile = args.profile.resolve() if args.profile else Path(os.environ.get('APPDATA', '')) / 'MCHOSEHUB'
    if not args.profile and not os.environ.get('APPDATA'):
        raise ValueError('APPDATA is unavailable; specify --profile explicitly.')
    cache = profile / 'Cache/Cache_Data'
    if not (cache / 'data_1').is_file():
        raise ValueError('Supported Chromium cache not found. Open the original app online, visit the mouse settings, fully quit it, then retry.')
    print('Application:', app_dir)
    print('Profile:', profile)
    with tempfile.TemporaryDirectory(prefix='mchose-offline-') as temporary:
        snapshot = Path(temporary) / 'offline'
        snapshot.mkdir()
        manifest = extract(cache, snapshot)
        if INDEX_KEY not in manifest:
            raise ValueError('Renderer index is missing from cache. Open the original app online first. Nothing changed.')
        failures = download_missing(snapshot, manifest) if args.download_missing else []
        missing = sorted(dependencies(snapshot, manifest))
        if missing:
            print('Optional/lazy dependencies absent:', len(missing))
        for key in failures:
            print('Download unavailable:', key)
        # Required entry modules must exist; optional modules are reported above.
        html = (snapshot / INDEX_KEY).read_text('utf-8')
        required = [public_key(urljoin('https://' + INDEX_KEY, ref)) for ref in
                    re.findall(r'(?:src|href)=["\']([^"\']+\.js)["\']', html)]
        if any(k not in manifest for k in required if k):
            raise ValueError('Required entry script missing. Retry with --download-missing. Nothing changed.')
        (snapshot / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
        # Default follows the current Windows user at runtime, even if installation is shared.
        if args.profile:
            (snapshot / 'offline-config.json').write_text(json.dumps({'profile': str(profile)}), encoding='utf-8')
        print('Snapshot:', len(manifest), 'public resources; optional dependencies absent:', len(missing))
        if args.dry_run:
            print('Dry run complete. Installation and profile were not changed.')
            return
        destination = app_dir / 'resources/offline'
        if destination.exists() or backup.exists() or original == ENTRY:
            raise ValueError('Existing patch/snapshot/backup found. Keep it intact; use a clean copy for a new installation.')
        # All extraction/validation happens before touching the installation.
        shutil.copyfile(entry, backup)
        try:
            shutil.copytree(snapshot, destination)
            shutil.copyfile(HERE / 'patch/offline-bootstrap.cjs', main_dir / 'offline-bootstrap.cjs')
            entry.write_text(ENTRY, encoding='utf-8')
        except Exception:
            shutil.copyfile(backup, entry)
            raise
        print('Installed. Start the original MCHOSE HUB.exe. Do not publish the generated snapshot or profile.')


if __name__ == '__main__':
    try:
        main()
    except PermissionError as error:
        print('ERROR: Access denied. Fully quit MCHOSE HUB; if the installation is protected, run under the same Windows account with administrator rights. Details:', error, file=sys.stderr)
        sys.exit(1)
    except (ValueError, OSError, EOFError, KeyboardInterrupt) as error:
        print('ERROR:', error, file=sys.stderr)
        sys.exit(1)
