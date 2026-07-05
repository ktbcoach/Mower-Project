<#
Windows launcher for the rover-status dashboard (tools/rover_display.py).

Reads the $PRSTAT telemetry line that start_base.ps1 mirrors from the rover
(over the radio link) into rover-status.txt and shows the RTK fix state, sats,
position/heading, and link health in a Tkinter window. It only reads the status
file - no serial access - so it runs alongside the bridge.

Tkinter ships with the standard python.org Windows installer. Run by
double-clicking rover_status.bat, or:
    powershell -ExecutionPolicy Bypass -File scripts\rover_status.ps1
Pass -Fullscreen for a kiosk-style display.
#>
[CmdletBinding()]
param(
    [switch]$Fullscreen
)

$ErrorActionPreference = 'Stop'
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$AppDir = Split-Path -Parent $ScriptDir
. (Join-Path $ScriptDir '_win_lib.ps1')

$py = Find-Python -AppDir $AppDir
$cfg = Import-DotEnv -Path (Join-Path $ScriptDir 'ntrip-base.env')
$StatusF = Get-Setting 'STATUS_FILE' $cfg (Join-Path $AppDir 'rover-status.txt')

$display = Join-Path $AppDir 'tools\rover_display.py'
$argList = @($display, '--status-file', $StatusF)
if ($Fullscreen) { $argList += '--fullscreen' }

& $py.Exe @($py.Pre + $argList)
