# MCHOSE HUB Offline Patch

## START HERE: INSTALL IN THIS ORDER

**No manual Python installation. No pip commands. Internet is required during the first setup.**

1. **Open the original MCHOSE HUB while online.** Connect your mouse and visit its settings tabs, including DPI and Performance. This fills the local interface cache.
2. **Fully quit MCHOSE HUB from its system tray menu.** Closing the window may only hide it. Quit any other running copies too.
3. **Download and extract this patch ZIP to a normal folder.** Do not run the installer from inside the ZIP.
4. **Double-click `install.cmd` and keep the internet connected.** It automatically prepares a private Python runtime if needed, downloads its private dependency, and applies the patch. You do not need to open a terminal.
5. **If asked, paste the folder containing `MCHOSE HUB.exe`.** The installer normally finds it automatically. If access is denied because the application is in Program Files, quit setup and run `install.cmd` as administrator under the same Windows account.
6. **Start your usual `MCHOSE HUB.exe`.** Disconnect the internet and verify the mouse settings you use.

---

An unofficial patch that loads the existing MCHOSE HUB interface from a local snapshot. Tested with an L7 Ultra connected through its 2.4 GHz receiver: detection, settings display, DPI changes, persistence after restarting, and interface reload with Chromium networking disabled.

This repository contains **no MCHOSE application binaries, interface assets, drivers, firmware, or user profile**. Each user creates a private snapshot from their own application cache. Back up your application and profile before modifying them.

## What automatic setup changes

- A discoverable compatible CPython 3.10-3.14 is used only to run the script, in isolated mode. Its installed packages are not changed.
- If no supported interpreter is found, setup downloads the official **Python 3.14.10 embeddable distribution** from python.org into `%LOCALAPPDATA%\MCHOSEHubOfflinePatch`.
- The pinned **Brotli 1.2.1** wheel is downloaded from PyPI into the same private runtime area. Setup verifies SHA-256 hashes from `runtime-lock.json` before using downloaded files.
- No system Python installation or upgrade, global pip commands, PATH edits, file associations, Python launcher registration, or changes to developer virtual environments are performed. Python's embeddable distribution is used without pip.
- `install.cmd` invokes PowerShell with a process-only execution policy; it does not persistently change the computer's PowerShell policy.
- Runtime downloads happen only when the private files are missing. Once prepared, setup can reuse them without internet, unless you explicitly request missing vendor resources.

The runtime directory is separate from both your source checkout and your existing Python projects. You can remove `%LOCALAPPDATA%\MCHOSEHubOfflinePatch` when setup is not running; the patched MCHOSE application does not need Python to run. A later setup will download its private files again.

Download references: [Python release files](https://www.python.org/ftp/python/3.14.10/), [Python embeddable distribution documentation](https://docs.python.org/3/using/windows.html#the-embeddable-package), [Brotli release on PyPI](https://pypi.org/project/brotli/1.2.1/). SHA-256 values for Python archives were taken from their published Sigstore bundles; wheel hashes come from PyPI release metadata. This project does not bundle those third-party downloads.

## Compatibility and limits

- Windows 10/11 with a supported MCHOSE HUB installation. Automatic private runtimes are available for x64, ARM64 and x86; the vendor application must itself support your system.
- Expected application layout: `resources/app/out/main/index.js`, `index.jsc`, and `bytecode-loader.cjs`. Tested interface version: 1.1.26 under `customPage/prod`.
- The cache reader supports Chromium blockfile caches containing `Cache/Cache_Data/data_1`. Different cache formats and packaged `app.asar` layouts are refused rather than overwritten.
- Macros, import/export, wired connections, and other devices have not been fully tested. Future versions may need changes to the patch.
- Cloud profiles, account login, community features, new firmware and application updates are unavailable offline. Their buttons may remain visible in the original interface.

## Application and profile detection

The profile defaults to **`%APPDATA%\MCHOSEHUB`**. The Windows username, drive and roaming-profile location are not hard-coded. At runtime the default follows the current Windows user's application-data directory. Settings are used in the real profile; the installer does not copy it into the repository.

The application directory is detected through uninstall registry entries and common installation directories. If there are multiple installations or discovery fails, the interactive installer asks for the correct folder.

For a shared application installation, the interface snapshot is shared, while the default profile is selected separately for each Windows user. An explicit `--profile` override pins one profile instead. Do not run setup as a different Windows user, since this changes APPDATA and LOCALAPPDATA.

## Advanced options

These commands are optional. The normal installation procedure is the six steps at the top.

Run from this source folder in PowerShell:

```powershell
# Prepare Python/dependencies only; do not patch the application.
.\install.cmd --setup-only

# Always use the private embedded Python, even if another Python exists.
.\install.cmd --force-private-python --setup-only

# Validate cache extraction without changing application files or user settings.
.\install.cmd --dry-run

# Select a nonstandard application location.
.\install.cmd --app-dir "D:\Apps\MCHOSE HUB"

# Select a nonstandard user profile.
.\install.cmd --app-dir "D:\Apps\MCHOSE HUB" --profile "D:\Profiles\MCHOSEHUB"
```

`--dry-run` still prepares the private Python runtime/dependency if needed. It does not patch MCHOSE HUB. An explicit profile path is saved only in the local `resources/offline/offline-config.json`; do not upload that file.

### If cached files are missing

Prefer opening the unmodified application online and visiting the required tabs before installing. The cache cannot contain a page that has never been loaded.

To make a one-time attempt to download missing public JS/CSS/font dependencies from the manufacturer's servers:

```powershell
.\install.cmd --app-dir "D:\Apps\MCHOSE HUB" --download-missing --dry-run
.\install.cmd --app-dir "D:\Apps\MCHOSE HUB" --download-missing
```

This uses Windows `curl.exe`, without application Cookies or tokens. Curl configuration files are disabled with `-q`; proxy environment variables are still respected. This option does not archive every image or every device. Warnings about missing optional/lazy modules may refer to other devices. Required entry scripts are checked separately. If a required tab fails to load, its snapshot is incomplete.

## How the patch works

- Backs up the original `index.js` to `index.js.offline-original` and loads `offline-bootstrap.cjs`. Original bytecode, native drivers and preload code are not modified.
- Extracts only allowlisted public/static MCHOSE routes from the browser cache. The installer does not read Cookies, Local Storage, IndexedDB, account configuration or application logs.
- Serves renderer HTTP/HTTPS through local handlers. Node HTTP/HTTPS clients use a loopback-only server bound to `127.0.0.1` on a random port.
- Missing resources return a local 404; there is no network fallback. This is an offline transport layer, not an operating-system firewall for every possible native component.
- Disables application-update actions and opening external websites. Local Windows settings can still open.

## Uninstall

Fully quit MCHOSE HUB, then run:

```powershell
.\install.cmd --uninstall
# Or, for a nonstandard installation:
.\install.cmd --app-dir "D:\Apps\MCHOSE HUB" --uninstall
```

Only the original entry point is restored; the user profile and generated snapshot are preserved. Installing over an existing snapshot/backup is intentionally refused. Use a clean application backup for a new installation. If another version has already changed the entry point, setup refuses to overwrite it.

## Troubleshooting

- **Application still running:** quit it through its tray menu, then press Enter in setup. Enter Q to cancel.
- **Application not found:** paste the directory containing `MCHOSE HUB.exe` when prompted.
- **Cache/index missing:** open the unmodified application online, visit the mouse settings, fully quit, then retry.
- **Download/checksum failure:** keep internet connected and retry. Never disable checksum verification. An incomplete private runtime cache can be renamed or removed while setup is closed.
- **Access denied:** fully quit every copy. For a protected installation directory, run setup as administrator under the same Windows account. Setup does not elevate itself or change account permissions.
- **No suitable existing Python:** automatic private Python setup is expected. Use `--force-private-python` if you want to avoid interpreter discovery entirely.
- **Corporate script/network restrictions:** setup reports the failure; it does not change persistent system policies.

Patch log: `%APPDATA%\MCHOSEHUB\offline-patch.log`, or the explicitly selected profile. `LOCAL` means found, `MISS` means absent, and `ERROR` indicates an error. Review logs before sharing: exceptions can contain local paths.

## Source development

The automatic installer never uses global pip. If you prefer manual development setup, create your own virtual environment:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

For bootstrap validation without application changes, run `install.cmd --setup-only`, then `install.cmd --dry-run`. Both interpreter discovery and the private-runtime path can be tested using the options above.

## Publishing and license

Publish **only the source files in this folder**. Do not add an installed application, AppData, `resources/offline`, generated manifests, runtime downloads, logs, or a ZIP of your working MCHOSE build. `.gitignore` is an additional safeguard; it does not remove files already committed to Git history.

This project is not affiliated with or endorsed by MCHOSE. The name identifies compatibility. Original application assets remain the property of their respective owners, and this project grants no permission to redistribute them. Cloud authentication is not bypassed; cloud features remain unavailable.

The patch source is licensed under MIT; see `LICENSE`. That license does not cover the original vendor application or extracted resources. Third-party runtime/dependency licenses accompany their separate downloads.
