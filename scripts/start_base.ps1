<#
Windows base-station NTRIP -> radio bridge launcher.

The Windows equivalent of scripts/start_base.sh: pulls the VTrans RTN RTCM3
stream from the NTRIP caster and forwards it out a COM port (the transparent
base radio that carries corrections to the rover). The heavy lifting is done by
tools/ntrip_to_serial.py, which is already cross-platform.

Run it by double-clicking start_base.bat, or from a PowerShell window:
    powershell -ExecutionPolicy Bypass -File scripts\start_base.ps1

Credentials + overrides come from scripts\ntrip-base.env (gitignored), same
KEY=VALUE format as the Pi. On first run this script writes a template there
and stops so you can fill in NTRIP_USER / NTRIP_PASSWORD.

Handy switches:
    -ListPorts        list available COM ports and exit
    -ListMountpoints  print the caster's sourcetable (mountpoints) and exit
#>
[CmdletBinding()]
param(
    [switch]$ListPorts,
    [switch]$ListMountpoints
)

$ErrorActionPreference = 'Stop'
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$AppDir = Split-Path -Parent $ScriptDir
. (Join-Path $ScriptDir '_win_lib.ps1')

$py = Find-Python -AppDir $AppDir

# --- quick COM-port listing (no creds needed) -------------------------------
if ($ListPorts) {
    Write-Host "# Available COM ports:"
    & $py.Exe @($py.Pre + @('-m', 'serial.tools.list_ports', '-v'))
    return
}

# --- credentials / overrides -------------------------------------------------
$EnvFile = Get-Setting 'ENV_FILE' @{} (Join-Path $ScriptDir 'ntrip-base.env')
if (-not (Test-Path $EnvFile)) {
    Write-Host "Creating credential file template: $EnvFile"
    @(
        '# VTrans NTRIP credentials (gitignored - never commit).'
        'NTRIP_USER='
        'NTRIP_PASSWORD='
        '# Optional overrides (defaults shown):'
        '# NTRIP_HOST=20.185.11.35'
        '# NTRIP_PORT=2101'
        '# NTRIP_MOUNTPOINT=VCAP_RTCM3'
        '# BASE_SERIAL=COM3'
        '# BASE_SERIAL_BAUD=19200'
        '# LAT=          (set BOTH lat+lon for VRS/network-RTK mountpoints)'
        '# LON='
    ) | Set-Content -LiteralPath $EnvFile -Encoding ASCII
    Write-Host "  -> edit it, fill in NTRIP_USER / NTRIP_PASSWORD, then run again." -ForegroundColor Yellow
    exit 1
}

$cfg = Import-DotEnv -Path $EnvFile

$Host_    = Get-Setting 'NTRIP_HOST'       $cfg '20.185.11.35'
$Port     = Get-Setting 'NTRIP_PORT'       $cfg '2101'
$Mount    = Get-Setting 'NTRIP_MOUNTPOINT' $cfg 'VCAP_RTCM3'
$Serial   = Get-Setting 'BASE_SERIAL'      $cfg 'COM3'
$Baud     = Get-Setting 'BASE_SERIAL_BAUD' $cfg '19200'
$StatusF  = Get-Setting 'STATUS_FILE'      $cfg (Join-Path $AppDir 'rover-status.txt')
$Lat      = Get-Setting 'LAT'              $cfg ''
$Lon      = Get-Setting 'LON'              $cfg ''
$Follow   = Get-Setting 'GGA_FROM_ROVER'   $cfg '1'   # VRS GGA tracks the rover; set 0 to use a fixed LAT/LON

$bridge = Join-Path $AppDir 'tools\ntrip_to_serial.py'

# --- mountpoint sourcetable listing -----------------------------------------
if ($ListMountpoints) {
    & $py.Exe @($py.Pre + @($bridge, '--host', $Host_, '--port', $Port, '--list'))
    return
}

# --- validation --------------------------------------------------------------
if (-not $env:NTRIP_USER -or -not $env:NTRIP_PASSWORD) {
    Write-Host "NTRIP_USER / NTRIP_PASSWORD not set. Fill them into $EnvFile." -ForegroundColor Red
    exit 1
}

$ggaArgs = @()
if ($Lat -and $Lon) {
    $ggaArgs = @('--lat', $Lat, '--lon', $Lon)
} elseif ($Lat -or $Lon) {
    Write-Host "Set BOTH LAT and LON (or neither) - only one was given." -ForegroundColor Red
    exit 1
}
$following = $Follow -and ($Follow -ne '0')
if ($following) { $ggaArgs += '--gga-from-rover' }

$ggaNote = ''
if ($following) {
    $seedNote = if ($Lat -and $Lon) { " seeded $Lat,$Lon" } else { '' }
    $ggaNote = "  (GGA follows rover$seedNote)"
} elseif ($ggaArgs.Count -gt 0) {
    $ggaNote = "  (GGA $Lat,$Lon)"
}
Write-Host "# base bridge: ${Host_}:${Port}/${Mount} -> $Serial @ $Baud$ggaNote"

# ntrip_to_serial.py reads NTRIP_USER / NTRIP_PASSWORD from the environment
# (Import-DotEnv already exported them), so they never hit the command line.
$argList = @(
    $bridge,
    '--host', $Host_,
    '--port', $Port,
    '--mountpoint', $Mount,
    '--serial', $Serial,
    '--serial-baud', $Baud,
    '--status-file', $StatusF
) + $ggaArgs

& $py.Exe @($py.Pre + $argList)
