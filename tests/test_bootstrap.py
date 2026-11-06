import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

spec = importlib.util.spec_from_file_location('bootstrap', Path(__file__).parents[1] / 'bootstrap.py')
bootstrap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootstrap)


def wheel_bytes(name, contents):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as wheel:
        wheel.writestr(name, contents)
    return stream.getvalue()


class BootstrapTests(unittest.TestCase):
    def test_corrupted_download_is_rejected_before_use(self):
        expected = hashlib.sha256(b'original').hexdigest()
        with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
            bootstrap.verify_digest(b'changed', expected)

    def test_wheel_cannot_write_outside_private_packages(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / 'private'
            target.mkdir()
            with self.assertRaisesRegex(ValueError, 'unsafe path'):
                bootstrap.unpack_wheel(wheel_bytes('../outside.py', 'untrusted'), target)
            self.assertFalse((Path(temp) / 'outside.py').exists())

    def test_packaging_hooks_are_not_executed_or_installed(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError, 'packaging hook'):
                bootstrap.unpack_wheel(wheel_bytes('startup.pth', 'import untrusted'), Path(temp))

    def test_ready_private_dependency_is_reused_without_network(self):
        with tempfile.TemporaryDirectory() as temp:
            runtime = Path(temp)
            lock = {'brotli': {'version': '1.2.1', 'wheels': {'cp313-win_amd64': {'sha256': 'test-hash'}}}}
            packages = runtime / 'brotli-1.2.1-cp313-win_amd64'
            packages.mkdir()
            (packages / '.wheel-sha256').write_text('test-hash')
            with patch.object(bootstrap, 'wheel_tag', return_value='cp313-win_amd64'), patch.object(bootstrap.urllib.request, 'urlopen') as network:
                self.assertEqual(bootstrap.ensure_dependency(runtime, lock), packages)
                network.assert_not_called()

    def test_lock_covers_supported_windows_interpreters(self):
        lock = json.loads((Path(__file__).parents[1] / 'runtime-lock.json').read_text('utf8'))
        for minor in range(10, 15):
            for arch in ('win32', 'win_amd64', 'win_arm64'):
                with self.subTest(minor=minor, arch=arch):
                    entry = lock['brotli']['wheels'][f'cp3{minor}-{arch}']
                    self.assertTrue(entry['url'].startswith('https://files.pythonhosted.org/'))
                    self.assertRegex(entry['sha256'], r'^[0-9a-f]{64}$')


if __name__ == '__main__':
    unittest.main()
