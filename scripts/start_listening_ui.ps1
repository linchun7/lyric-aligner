param([string]$PackDir = '')
$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$PythonPath = (Get-Command python -ErrorAction Stop).Source
$LauncherPath = Join-Path $PSScriptRoot 'v4_listen.py'
$LogRoot = Join-Path $RepoRoot 'private/listening/logs'
New-Item -ItemType Directory -Path $LogRoot -Force | Out-Null
$Stamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
$Arguments = @('-X', 'utf8', ('"' + $LauncherPath + '"'))
if ($PackDir) {
    $ResolvedPack = (Resolve-Path -LiteralPath $PackDir).Path
    $Arguments += @('--pack-dir', ('"' + $ResolvedPack + '"'), '--set-default')
}
# A quick preflight makes a broken pointer visible instead of hiding an error.
$CheckArgs = @('-X', 'utf8', $LauncherPath, '--check')
if ($PackDir) { $CheckArgs += @('--pack-dir', $ResolvedPack) }
& $PythonPath @CheckArgs | Out-Null
if ($LASTEXITCODE -ne 0) {
    Add-Type -AssemblyName PresentationFramework
    [System.Windows.MessageBox]::Show('UI 3.2 could not validate its review pack. Run scripts/v4_listen.py --check for details.', 'Lyric Aligner UI 3.2') | Out-Null
    exit 2
}
$ErrorLog = Join-Path $LogRoot "$Stamp.stderr.log"
$Process = Start-Process -WindowStyle Hidden -FilePath $PythonPath -ArgumentList $Arguments -WorkingDirectory $RepoRoot -RedirectStandardOutput (Join-Path $LogRoot "$Stamp.stdout.log") -RedirectStandardError $ErrorLog -PassThru
# Windows PowerShell 5 can lose ExitCode for redirected children unless the
# native handle is retained before a timed wait (notably the venv launcher).
$ProcessHandle = $Process.Handle
# Report an occupied/stale UI instead of silently leaving its old page open.
if ($Process.WaitForExit(3000) -and $Process.ExitCode -ne 0) {
    $Detail = Get-Content -LiteralPath $ErrorLog -Raw
    Write-Error -Message ("UI 3.2 startup failed. " + $Detail) -ErrorAction Continue
    Add-Type -AssemblyName PresentationFramework
    [System.Windows.MessageBox]::Show("UI 3.2 startup failed. Close the previous project review server, then retry. Details: $ErrorLog", 'Lyric Aligner UI 3.2') | Out-Null
    exit 2
}
