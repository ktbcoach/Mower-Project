# Windows base station (NTRIP → radio bridge)

The base station pulls the VTrans RTN RTCM3 correction stream from the NTRIP
caster and forwards it out a serial radio to the rover. On the Pi this runs as a
systemd service; on a Windows box it's the same Python — `tools/ntrip_to_serial.py`
is already cross-platform — wrapped in double-click launchers instead.

```
VTrans RTN (NTRIP) → [Windows box: start_base.bat] → base radio ))) RTCM ))) rover radio → LG580P
   rover-status display ← rover-status.txt ← [start_base.bat] ((( $PRSTAT ((( rover
```

The only things that differ from the Pi are the **serial port name** (`COM3`
instead of `/dev/ttyUSB0`) and the **startup wrapper** (a `.bat` you double-click
instead of systemd).

## One-time setup

1. **Install Python 3** (python.org). Tick **“Add python.exe to PATH.”** The
   standard installer already includes Tkinter, which the status display needs.

2. **Install pyserial** (the only dependency):

   ```powershell
   pip install pyserial
   ```

   (Or, to match the Pi exactly, make a venv in the repo root — `python -m venv
   .venv` then `.venv\Scripts\pip install -r requirements.txt`; the launchers
   prefer `.venv\Scripts\python.exe` automatically if it exists.)

3. **Plug in the base radio** (MaxStream XStream on a USB-serial adapter) and
   find its COM port:

   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts\start_base.ps1 -ListPorts
   ```

   Note the `COMx` number (e.g. `COM3`). It shows up under **Ports (COM & LPT)**
   in Device Manager too.

4. **Create the credentials file.** Double-click **`scripts\start_base.bat`**
   once — it writes a template `scripts\ntrip-base.env` and stops. Open it in
   Notepad and fill in your VTrans login:

   ```ini
   NTRIP_USER=your_user
   NTRIP_PASSWORD=your_pass
   # Optional overrides (defaults shown):
   # NTRIP_MOUNTPOINT=VCAP_RTCM3
   # BASE_SERIAL=COM3
   # BASE_SERIAL_BAUD=19200
   # GGA_FROM_ROVER=1     (VRS GGA tracks the rover; set 0 for a fixed LAT/LON)
   ```

   Set `BASE_SERIAL` to the COM port from step 3 if it isn’t `COM3`. This file is
   gitignored — the credentials never get committed. Same `KEY=VALUE` format as
   the Pi's `ntrip-base.env`.

   For a **VRS/network-RTK** mountpoint the bridge, by default (`GGA_FROM_ROVER=1`),
   builds the position it sends the caster from the rover's own `$PRSTAT`
   telemetry, so the virtual base follows the rover — no `LAT`/`LON` needed. A
   `LAT`/`LON` you do set is used only to seed corrections until the rover's first
   fix comes back. Set `GGA_FROM_ROVER=0` to send a fixed `LAT`/`LON` instead.

## Running it

**Double-click `scripts\start_base.bat`.** A console window opens and starts
streaming corrections; it prints a byte count and the RTCM message types it sees
every few seconds. Leave the window open — closing it stops corrections. It
reconnects automatically if the caster or network drops. On a reboot, just
double-click it again (you chose a manual launcher over an auto-start service).

Useful before going live:

```powershell
# list the caster's mountpoints (pick an RTCM 3.x one, NOT CMRx):
powershell -ExecutionPolicy Bypass -File scripts\start_base.ps1 -ListMountpoints

# list COM ports:
powershell -ExecutionPolicy Bypass -File scripts\start_base.ps1 -ListPorts
```

> Pick an **RTCM 3.x** mountpoint. `VCAP_RTCM3` (nearest single-base station)
> needs no position. A **VRS / network-RTK** mountpoint needs an approximate
> position — set both `LAT` and `LON` (decimal degrees) in `ntrip-base.env`.
> CMRx mountpoints are Trimble-proprietary and the LG580P can't decode them.

## Rover-status display

The bridge mirrors the rover's `$PRSTAT` telemetry (read back over the same
radio) to `rover-status.txt`. **Double-click `scripts\rover_status.bat`** for the
Tkinter dashboard: a colour-coded fix-state banner, sats + signal, position and
heading, and a **LINK LOST** flag if telemetry goes stale. It only reads the file
(no serial), so it runs alongside the bridge. Pass `-Fullscreen` for kiosk mode.

## Files

| File | Purpose |
|------|---------|
| `scripts\start_base.bat` | Double-click launcher for the NTRIP→radio bridge |
| `scripts\start_base.ps1` | The bridge logic (`-ListPorts`, `-ListMountpoints`) |
| `scripts\rover_status.bat` | Double-click launcher for the status display |
| `scripts\rover_status.ps1` | Display logic (`-Fullscreen`) |
| `scripts\_win_lib.ps1` | Shared helpers (Python discovery, `.env` parsing) |
| `scripts\ntrip-base.env` | Your credentials + overrides (gitignored) |

## Troubleshooting

- **`No Python found`** — install Python 3 with “Add to PATH,” or make a `.venv`
  in the repo root.
- **`pyserial required`** — `pip install pyserial`.
- **`could not open port 'COM3'`** — wrong port (run `-ListPorts`) or another
  program (u-center, PuTTY, a previous window) is holding it. Close the other.
- **Window flashes and closes** — run it from a PowerShell window instead of
  double-clicking to read the error: `powershell -ExecutionPolicy Bypass -File
  scripts\start_base.ps1`.
- **No RTCM bytes / caster rejects** — check `NTRIP_USER`/`NTRIP_PASSWORD` and
  that the mountpoint exists (`-ListMountpoints`).
