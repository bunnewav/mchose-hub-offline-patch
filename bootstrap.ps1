# Only process-local environment changes. No system Python installer or PATH edits.
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$forward = @($args | Where-Object { $_ -ne '--force-private-python' -and $_ -ne '--setup-only' })
$forcePrivate = $args -contains '--force-private-python'
$setupOnly = $args -contains '--setup-only'

function Test-Interpreter([string]$Executable) {
    if (-not (Test-Path -LiteralPath $Executable -PathType Leaf)) { return $false }
    $probe = "import sys,sysconfig;sys.exit(not(sys.implementation.name=='cpython' and (3,10)<=sys.version_info[:2]<(3,15) and sysconfig.get_platform() in ('win-amd64','win32','win-arm64') and not sysconfig.get_config_var('Py_GIL_DISABLED') and not sysconfig.get_config_var('Py_DEBUG')))"
    try {
        & $Executable -I -c $probe 2>$null
        return $LASTEXITCODE -eq 0
    } catch { return $false }
}

function Find-Interpreter {
    foreach ($name in @('python', 'python3')) {
        $command = Get-Command $name -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        # Do not invoke Microsoft Store execution aliases.
        if ($command -and $command.Source -notmatch '\\Microsoft\\WindowsApps\\python[^\\]*\.exe$') {
            if (Test-Interpreter $command.Source) { return $command.Source }
        }
    }
    $launcher = Get-Command py -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($launcher) {
        # List installed interpreters; never ask the launcher to install Python.
        try {
            $installed = & $launcher.Source -0p 2>$null
            foreach ($line in $installed) {
                if ($line -match '([A-Za-z]:\\.*python(?:[0-9.]*)?\.exe)\s*$') {
                    $candidate = $Matches[1].Trim()
                    if (Test-Interpreter $candidate) { return $candidate }
                }
            }
        } catch { }
    }
    return $null
}

try {
    if (-not $env:LOCALAPPDATA) { throw 'LOCALAPPDATA is unavailable. Run setup from your normal Windows account.' }
    $runtimeRoot = Join-Path $env:LOCALAPPDATA 'MCHOSEHubOfflinePatch'
    $lock = Get-Content -LiteralPath (Join-Path $root 'runtime-lock.json') -Raw | ConvertFrom-Json
    Write-Host 'MCHOSE HUB Offline Patch setup'
    Write-Host 'Keep internet connected during setup. Fully quit MCHOSE HUB from its tray menu.'
    Write-Host 'Python and packages outside the patch runtime will not be modified.'
    $python = $null
    if (-not $forcePrivate) { $python = Find-Interpreter }
    if (-not $python) {
        $machine = if ($env:PROCESSOR_ARCHITEW6432) { $env:PROCESSOR_ARCHITEW6432 } else { $env:PROCESSOR_ARCHITECTURE }
        $arch = switch ($machine.ToUpperInvariant()) { 'AMD64' { 'amd64' }; 'ARM64' { 'arm64' }; 'X86' { 'win32' }; default { throw "Unsupported architecture: $machine" } }
        $version = $lock.python.version
        $expected = $lock.python.sha256.$arch
        $pythonDir = Join-Path $runtimeRoot "python-$version-$arch"
        $python = Join-Path $pythonDir 'python.exe'
        $stamp = Join-Path $pythonDir '.archive-sha256'
        $ready = (Test-Path -LiteralPath $stamp) -and ((Get-Content -LiteralPath $stamp -Raw).Trim() -eq $expected) -and (Test-Interpreter $python)
        if (-not $ready) {
            New-Item -ItemType Directory -Force -Path $runtimeRoot | Out-Null
            if (Test-Path -LiteralPath $pythonDir) { throw "Private Python cache is incomplete. Rename this folder and retry: $pythonDir" }
            $stage = Join-Path $runtimeRoot ('download-' + [Guid]::NewGuid().ToString('N'))
            New-Item -ItemType Directory -Path $stage | Out-Null
            $archive = Join-Path $stage 'python.zip'
            $url = $lock.python.source + "python-$version-embed-$arch.zip"
            Write-Host "Downloading private Python $version from python.org..."
            [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
            Invoke-WebRequest -Uri $url -OutFile $archive -UseBasicParsing -TimeoutSec 120
            $stream = [IO.File]::OpenRead($archive)
            $sha = [Security.Cryptography.SHA256]::Create()
            try { $actual = ([BitConverter]::ToString($sha.ComputeHash($stream))).Replace('-', '').ToLowerInvariant() }
            finally { $stream.Dispose(); $sha.Dispose() }
            if ($actual -ne $expected) { throw 'Python archive checksum mismatch. Setup stopped without running the downloaded runtime.' }
            $unpacked = Join-Path $stage 'python'
            $null = [Reflection.Assembly]::LoadWithPartialName('System.IO.Compression.FileSystem')
            [IO.Compression.ZipFile]::ExtractToDirectory($archive, $unpacked)
            Set-Content -LiteralPath (Join-Path $unpacked '.archive-sha256') -Value $expected -Encoding ASCII
            # Verify both move targets and the temporary directory before mutations.
            $safeRoot = [IO.Path]::GetFullPath($runtimeRoot).TrimEnd('\') + '\'
            $safeStage = [IO.Path]::GetFullPath($stage)
            $safePython = [IO.Path]::GetFullPath($pythonDir)
            if (-not $safeStage.StartsWith($safeRoot, [StringComparison]::OrdinalIgnoreCase) -or -not $safePython.StartsWith($safeRoot, [StringComparison]::OrdinalIgnoreCase)) { throw 'Unexpected runtime directory.' }
            Move-Item -LiteralPath $unpacked -Destination $safePython
            Remove-Item -LiteralPath $safeStage -Recurse -Force
            if (-not (Test-Interpreter $python)) { throw 'The private Python runtime could not start on this computer.' }
        }
        Write-Host "Using private Python: $python"
    } else { Write-Host "Using existing Python (read-only): $python" }
    $arguments = @('-I', (Join-Path $root 'bootstrap.py'))
    if ($setupOnly) { $arguments += '--setup-only' }
    $arguments += $forward
    & $python @arguments
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    exit 0
} catch {
    Write-Host ('ERROR: ' + $_.Exception.Message) -ForegroundColor Red
    exit 1
}
