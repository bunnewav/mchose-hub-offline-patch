import gzip
import importlib.util
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('installer', Path(__file__).parents[1] / 'install.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class InstallerTests(unittest.TestCase):
    def test_private_and_untrusted_urls_are_rejected(self):
        for url in ['https://www.maicong.cn/mchose/user', 'https://chat.maicong.cn/register/client/node',
                    'https://example.com/assets/a.js', 'https://cdn.mchose.com.cn.evil.test/customPage/a.js',
                    'https://cdn.mchose.com.cn/customPage/../secret.js',
                    'https://www.mchose.com.cn/newBin/firmware.bin']:
            with self.subTest(url=url):
                self.assertIsNone(installer.public_key(url))
        self.assertEqual(installer.public_key('https://cdn.mchose.com.cn/customPage/prod/assets/a.js?v=1'),
                         'cdn.mchose.com.cn/customPage/prod/assets/a.js')

    def test_extract_compressed_cache_without_account_responses(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            cache, out = base / 'cache', base / 'out'
            cache.mkdir()
            entries = bytearray(8192 + 512)
            cases = [
                ('https://' + installer.INDEX_KEY, b'<html>local</html>'),
                ('https://www.maicong.cn/mchose/user', b'{"token":"private-test-token"}'),
            ]
            for i, (url, body) in enumerate(cases):
                off = 8192 + i * 256
                key = ('1/0/' + url).encode()
                headers = b'HTTP/1.1 200 OK\0content-encoding:gzip\0content-type:text/html\0'
                packed = gzip.compress(body)
                header_addr, body_addr = 0x80000001 + i * 2, 0x80000002 + i * 2
                struct.pack_into('<iI', entries, off + 32, len(key), 0)
                struct.pack_into('<4i4I', entries, off + 40, len(headers), len(packed), 0, 0, header_addr, body_addr, 0, 0)
                entries[off + 96:off + 96 + len(key)] = key
                (cache / f'f_{header_addr & 0xfffffff:06x}').write_bytes(headers)
                (cache / f'f_{body_addr & 0xfffffff:06x}').write_bytes(packed)
            (cache / 'data_1').write_bytes(entries)
            manifest = installer.extract(cache, out)
            self.assertEqual(list(manifest), [installer.INDEX_KEY])
            self.assertEqual((out / installer.INDEX_KEY).read_bytes(), cases[0][1])
            self.assertNotIn(b'private-test-token', b''.join(p.read_bytes() for p in out.rglob('*') if p.is_file()))

    def test_dry_run_install_and_uninstall_preserve_profile(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            app, profile = base / 'Application With Spaces', base / 'Roaming' / 'MCHOSEHUB'
            main = app / 'resources/app/out/main'
            main.mkdir(parents=True)
            original = '"use strict";\nrequire("./bytecode-loader.cjs");\nrequire("./index.jsc");\n'
            (main / 'index.js').write_text(original)
            for name in ('index.jsc', 'bytecode-loader.cjs'):
                (main / name).write_bytes(b'test placeholder')
            (app / 'MCHOSE HUB.exe').write_bytes(b'test placeholder')
            (profile / 'Cache/Cache_Data').mkdir(parents=True)
            (profile / 'Cache/Cache_Data/data_1').touch()
            (profile / 'private-settings.json').write_text('{"token":"do-not-copy"}')

            def fake_extract(cache, out):
                manifest = {}
                installer.write_resource(out, manifest, installer.INDEX_KEY, b'<html>test</html>')
                return manifest

            args = ['--app-dir', str(app)]
            with patch.dict(os.environ, {'APPDATA': str(profile.parent)}), patch.object(installer, 'extract', side_effect=fake_extract), patch.object(installer, 'running_hub', return_value=False):
                installer.main(args + ['--dry-run'])
                self.assertEqual((main / 'index.js').read_text(), original)
                self.assertFalse((app / 'resources/offline').exists())
                installer.main(args)
                self.assertEqual((main / 'index.js').read_text(), installer.ENTRY)
                self.assertFalse((app / 'resources/offline/offline-config.json').exists())
                self.assertFalse((app / 'resources/offline/private-settings.json').exists())
                self.assertEqual((main / 'index.js.offline-original').read_text(), original)
                installer.main(args + ['--uninstall'])
                self.assertEqual((main / 'index.js').read_text(), original)
                self.assertEqual(json.loads((profile / 'private-settings.json').read_text())['token'], 'do-not-copy')

    def test_running_app_is_not_patched(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Path(temp)
            main = app / 'resources/app/out/main'
            main.mkdir(parents=True)
            for name in ('index.js', 'index.jsc', 'bytecode-loader.cjs'):
                (main / name).write_text('untouched')
            (app / 'MCHOSE HUB.exe').touch()
            with patch.object(installer, 'running_hub', return_value=True):
                with self.assertRaisesRegex(ValueError, 'is running'):
                    installer.main(['--app-dir', str(app)])
            self.assertEqual((main / 'index.js').read_text(), 'untouched')
            self.assertFalse((main / 'index.js.offline-original').exists())


if __name__ == '__main__':
    unittest.main()
