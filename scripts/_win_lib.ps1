# Shared helpers for the Windows base-station launchers (dot-sourced, not run
# directly). Keeps start_base.ps1 / rover_status.ps1 short and consistent.
# Windows PowerShell 5.1 compatible (no ternary / null-coalescing / && chains).

# Resolve a Python interpreter, preferring the repo venv, then a PATH `python`,
# then the `py` launcher. Returns @{ Exe = <string>; Pre = <string[]> } so the
# caller can splat:  & $py.Exe @($py.Pre + @('script.py','--flag'))
function Find-Python {
    param([Parameter(Mandatory = $true)][string]$AppDir)

    $venv = Join-Path $AppDir '.venv\Scripts\python.exe'
    if (Test-Path $venv) {
        return @{ Exe = $venv; Pre = @() }
    }
    $onPath = Get-Command python -ErrorAction SilentlyContinue
    if ($onPath) {
        return @{ Exe = $onPath.Source; Pre = @() }
    }
    $launcher = Get-Command py -ErrorAction SilentlyContinue
    if ($launcher) {
        return @{ Exe = $launcher.Source; Pre = @('-3') }
    }
    throw "No Python found. Install Python 3 from python.org (check 'Add to PATH'), or create a .venv in the repo root."
}

# Parse a KEY=VALUE .env file (same format the Pi's ntrip-base.env uses) and
# export every entry into the current process environment so a child Python
# inherits it. Blank lines and `#` comments are skipped; surrounding quotes on
# a value are stripped. Returns a hashtable of the parsed values too.
function Import-DotEnv {
    param([Parameter(Mandatory = $true)][string]$Path)

    $values = @{}
    if (-not (Test-Path $Path)) {
        return $values
    }
    foreach ($raw in Get-Content -LiteralPath $Path) {
        $line = $raw.Trim()
        if ($line.Length -eq 0 -or $line.StartsWith('#')) {
            continue
        }
        $eq = $line.IndexOf('=')
        if ($eq -lt 1) {
            continue
        }
        $key = $line.Substring(0, $eq).Trim()
        $val = $line.Substring($eq + 1).Trim()
        if ($val.Length -ge 2) {
            $first = $val.Substring(0, 1)
            $last = $val.Substring($val.Length - 1, 1)
            if (($first -eq '"' -and $last -eq '"') -or ($first -eq "'" -and $last -eq "'")) {
                $val = $val.Substring(1, $val.Length - 2)
            }
        }
        $values[$key] = $val
        Set-Item -Path ("Env:" + $key) -Value $val
    }
    return $values
}

# First value that is set and non-empty, in order: env var, .env file, default.
function Get-Setting {
    param(
        [string]$Name,
        [hashtable]$FromFile,
        [string]$Default
    )
    $envVal = [Environment]::GetEnvironmentVariable($Name, 'Process')
    if ($envVal) { return $envVal }
    if ($FromFile.ContainsKey($Name) -and $FromFile[$Name]) { return $FromFile[$Name] }
    return $Default
}
