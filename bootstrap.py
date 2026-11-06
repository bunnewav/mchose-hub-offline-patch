"""Install a locked wheel in the patch cache, then run the installer in isolation.

No pip, global packages, Python installer, registry changes or PATH changes.
Works with both an existing CPython and the official embeddable distribution.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import os
from pathlib import Path
import runpy
import shutil
import sys
import sysconfig
import tempfile
from urllib.parse import urlsplit
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parent


def wheel_tag() -> str:
    if sys.implementation.name != 'cpython' or not (3, 10) <= sys.version_info[:2] < (3, 15):
        raise ValueError('Use install.cmd to obtain a supported private Python runtime.')
    if sysconfig.get_config_var('Py_GIL_DISABLED') or sysconfig.get_config_var('Py_DEBUG'):
        raise ValueError('Use install.cmd --force-private-python instead of a debug/free-threaded interpreter.')
    platform = sysconfig.get_platform().replace('-', '_')
    return f'cp{sys.version_info.major}{sys.version_info.minor}-{platform}'


def verify_digest(data: bytes, expected: str):
    if hashlib.sha256(data).hexdigest() != expected:
        raise ValueError('Dependency checksum mismatch. Downloaded code will not be used.')


def unpack_wheel(data: bytes, destination: Path):
    import io
    with zipfile.ZipFile(io.BytesIO(data)) as wheel:
        for member in wheel.infolist():
            if not (destination / member.filename).resolve().is_relative_to(destination.resolve()):
                raise ValueError('Dependency archive contains an unsafe path.')
            # A plain Brotli wheel needs no scripts, .pth files or installer hooks.
            if member.filename.endswith('.pth') or '.data/' in member.filename or '\\' in member.filename:
                raise ValueError('Unexpected executable packaging hook in dependency wheel.')
        wheel.extractall(destination)


def ensure_dependency(runtime: Path, lock: dict) -> Path:
    tag = wheel_tag()
    info = lock['brotli']['wheels'].get(tag)
    if not info:
        raise ValueError('No pinned Brotli wheel for this interpreter; use install.cmd --force-private-python.')
    packages = runtime / f"brotli-{lock['brotli']['version']}-{tag}"
    ready = packages / '.wheel-sha256'
    if ready.is_file() and ready.read_text('ascii').strip() == info['sha256']:
        return packages
    if packages.exists():
        raise ValueError(f'Incomplete private dependency cache. Rename this directory and retry: {packages}')
    u = urlsplit(info['url'])
    if u.scheme != 'https' or u.hostname != 'files.pythonhosted.org' or u.username or u.password:
        raise ValueError('Unexpected dependency download host.')
    print(f"Downloading private Brotli {lock['brotli']['version']} from PyPI...", flush=True)
    runtime.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(info['url'], headers={'User-Agent': 'MCHOSEHubOfflinePatch-Setup'})
    with urllib.request.urlopen(request, timeout=60) as response:
        data = response.read(20 * 1024 * 1024 + 1)
    if len(data) > 20 * 1024 * 1024:
        raise ValueError('Dependency download exceeds expected size.')
    verify_digest(data, info['sha256'])
    with tempfile.TemporaryDirectory(prefix='brotli-download-', dir=runtime) as temp:
        staged = Path(temp) / 'packages'
        staged.mkdir()
        unpack_wheel(data, staged)
        (staged / '.wheel-sha256').write_text(info['sha256'], encoding='ascii')
        # Destination was checked above: never replace another Python environment.
        if not all(p.resolve().is_relative_to(runtime.resolve()) for p in (staged, packages)):
            raise ValueError('Unexpected private dependency directory.')
        shutil.move(str(staged), str(packages))
    return packages


def main():
    if not os.environ.get('LOCALAPPDATA'):
        raise ValueError('LOCALAPPDATA is unavailable. Run setup from your normal Windows account.')
    lock = json.loads((ROOT / 'runtime-lock.json').read_text('utf-8'))
    packages = ensure_dependency(Path(os.environ['LOCALAPPDATA']) / 'MCHOSEHubOfflinePatch', lock)
    sys.path.insert(0, str(packages))
    importlib.invalidate_caches()
    import brotli
    if not Path(brotli.__file__).resolve().is_relative_to(packages.resolve()):
        raise ValueError('Brotli was loaded from outside the private dependency directory.')
    if brotli.__version__ != lock['brotli']['version']:
        raise ValueError('Private Brotli version does not match runtime-lock.json.')
    if brotli.decompress(brotli.compress(b'patch dependency check')) != b'patch dependency check':
        raise ValueError('Private Brotli self-check failed.')
    print('Private dependencies are ready. Existing Python packages were not changed.', flush=True)
    if '--setup-only' in sys.argv[1:]:
        return
    sys.argv = [str(ROOT / 'install.py'), '--interactive', *sys.argv[1:]]
    runpy.run_path(str(ROOT / 'install.py'), run_name='__main__')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, ImportError, zipfile.BadZipFile) as error:
        print('ERROR:', error, file=sys.stderr)
        sys.exit(1)
