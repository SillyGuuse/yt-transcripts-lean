# Optional Windows watchdog: ensures supervisor.py and download_audio.py
# stay alive across crashes / log-outs / reboots. Register with Task Scheduler:
#
#   $dir    = "C:\path\to\vimeo"
#   $action = New-ScheduledTaskAction -Execute "powershell.exe" `
#             -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$dir\watchdog.ps1`""
#   $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddSeconds(30) `
#              -RepetitionInterval (New-TimeSpan -Minutes 5)
#   $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
#               -DontStopIfGoingOnBatteries -StartWhenAvailable
#   Register-ScheduledTask -TaskName "VimeoTranscribeWatchdog" `
#       -Action $action -Trigger $trigger -Settings $settings -Force
#
# This script unregisters its own scheduled task once the work is done.

$dir = $PSScriptRoot
Set-Location $dir

# Are we done? Ask transcribe_whisper in dry-run mode.
$dry = & python transcribe_whisper.py 2>&1 | Out-String
if ($dry -match "Whisper-needed: 0" -or $dry -match "Nothing to transcribe") {
    Write-Output "$(Get-Date -Format o): nothing to do; unregistering watchdog."
    schtasks /Delete /TN "VimeoTranscribeWatchdog" /F | Out-Null
    exit 0
}

function Ensure-PythonScript {
    param([string]$ScriptName, [string]$OutLog, [string]$ErrLog,
          [string[]]$Args = @())
    $running = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.CommandLine -like "*$ScriptName*" }
    if ($running) {
        Write-Output "$(Get-Date -Format o): $ScriptName alive (PID $($running.ProcessId))."
        return
    }
    Write-Output "$(Get-Date -Format o): $ScriptName not running; relaunching."
    $argList = @($ScriptName) + $Args
    Start-Process -FilePath "python" -ArgumentList $argList `
        -WorkingDirectory $dir -WindowStyle Hidden `
        -RedirectStandardOutput $OutLog -RedirectStandardError $ErrLog
}

Ensure-PythonScript -ScriptName "supervisor.py" `
    -OutLog "$dir\supervisor.out.log" -ErrLog "$dir\supervisor.err.log"

# Pass the showcase URL via env var $env:VIMEO_SHOWCASE_URL when running watchdog.
if ($env:VIMEO_SHOWCASE_URL) {
    Ensure-PythonScript -ScriptName "download_audio.py" `
        -OutLog "$dir\downloader.out.log" -ErrLog "$dir\downloader.err.log" `
        -Args @($env:VIMEO_SHOWCASE_URL)
}
