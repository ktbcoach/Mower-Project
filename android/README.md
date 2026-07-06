# Android base station (Pydroid 3)

A phone/tablet version of the base station — the Windows/Pi NTRIP→radio bridge
**and** the rover-status dashboard combined into one Kivy app
([`base_station.py`](base_station.py)) that runs in **Pydroid 3**. It pulls the
VTrans RTN RTCM3 stream over Wi-Fi/cellular and forwards it out the phone's
**USB-C** port to the USB-serial converter driving the base radio, while showing
the same fix-state dashboard as the desktop display.

```
VTrans RTN (NTRIP) → [phone: base_station.py] → USB-C → USB-serial → base radio ))) )))
                          dashboard ← $PRSTAT telemetry ← (same radio, full-duplex)
```

## Why this differs from the Pi/Windows version

| | Pi / Windows | Android |
|---|---|---|
| Bridge + display | two processes, share `rover-status.txt` | **one app**, share state in memory |
| GUI toolkit | Tkinter | **Kivy** (Tkinter isn't usable on Android) |
| Serial | `pyserial` on `/dev/ttyUSB0` / `COMx` | **`usbserial4a`** on the USB-C device |
| Start/stop | systemd / double-click `.bat` | **Start/Stop button** in the app |

The RTCM/NTRIP logic and the `$PRSTAT` telemetry parser are ported verbatim from
`tools/ntrip_to_serial.py` and `src/lg580p/telemetry.py` (verified byte-identical),
so the wire behavior matches the other base stations exactly.

## Setup

1. **Install Pydroid 3** from the Play Store.
2. In Pydroid: **Menu → Pip**, install **`kivy`** and **`usbserial4a`**
   (`usb4a` installs automatically as its dependency).
3. Copy **`base_station.py`** onto the device (Pydroid's file browser, cloud, or
   a USB copy into Downloads).
4. Get a **USB-C OTG adapter** and plug the USB-serial radio into the phone.
5. Open `base_station.py` in Pydroid and press **▶ Run**.
6. Tap **Settings**, enter your VTrans **NTRIP user / password** (and mountpoint /
   baud if not the defaults — `VCAP_RTCM3` @ `19200`), then **Save**.
7. Tap **Start**. Android shows a **USB permission** dialog the first time —
   accept it, and the bridge connects automatically.

Settings persist to `base_config.json` beside the script. That file holds your
credentials — keep it on the device, don't commit it (it's gitignored).

## Using it

- The **banner** shows the rover's RTK fix state (green = RTK-fixed) or **LINK
  LOST** if telemetry goes stale (>5 s), or the bridge state (connecting/…)
  before telemetry arrives.
- The **status line** under the banner shows the NTRIP connection, byte count,
  the RTCM message types seen (e.g. `1005:2 1074:120`), the USB radio, and any
  error.
- Cells mirror the desktop dashboard: sats used/view, C/N0 max/avg (colour-coded
  by signal), HDOP, lat/lon, heading, speed, corrections, logging.
- **Stop** cleanly closes the caster socket and the USB port.

## VRS / network-RTK mountpoints

A single-base mountpoint (e.g. `VCAP_RTCM3`) needs no position. For a **VRS /
network-RTK** mountpoint, set both **lat** and **lon** (decimal degrees) in
Settings — the app then sends a periodic GGA so the caster knows where you are.

**Quick-fill presets.** The Settings screen has a "Quick fill" row that sets the
mountpoint + position in one tap:

| Preset | Mountpoint | Position |
|--------|-----------|----------|
| **Current (VRS)** | `VRS_RTCM3` | 44.585979, −71.947149 |
| **Perim site (VRS)** | `VRS_RTCM3` | 44.420137, −72.983771 |
| **Single-base** | `VCAP_RTCM3` | (cleared — no GGA) |

Tap a preset, then **Save**. The VRS mountpoint name (`VRS_RTCM3`) matches the
docs; if the caster rejects it, list the real mountpoints and edit the field —
on the Windows box run `start_base.ps1 -ListMountpoints`.

## Develop/test on the Windows box first

The same file runs on desktop with **`pip install kivy pyserial`**. On desktop it
skips the USB-permission flow and opens the COM port from the **`serial_port`**
setting (e.g. `COM3`) — so you can point it at the real radio on the Windows base
box and verify end-to-end before moving to the phone.

## Troubleshooting

- **No USB device found** — check the OTG adapter; some phones need
  "OTG"/"USB" enabled in Settings. Confirm the adapter shows the serial chip.
- **Permission dialog never appears / bridge won't open** — unplug/replug the
  adapter, tap Start again. `usbserial4a` supports FTDI, CP210x, CH34x, PL2303,
  and CDC-ACM chips; an exotic chip may be unsupported.
- **Writes acked but no traffic on the radio (PL2303)** — newer Prolific "HXN"
  chips (PL2303GC/GT/GL, still `067b:2303`) reject the legacy init sequence
  usbserial4a sends, leaving the UART unconfigured while writes are silently
  swallowed. The app ships its own HXN-aware PL2303 driver; the radio label
  shows `Pl2303FixedSerial/HXN` when the fix is active.
- **Multi-port adapter (writes acked, both directions dead)** — a 4-port
  adapter is an internal hub with one serial chip per jack, so Android sees
  several identical USB devices and the app may open a chip whose jack is
  empty. The radio label shows the pick as `dev K/N`; change **USB device #**
  in Settings (0..N-1, Stop/Start between tries) until the radio LEDs blink.
  A paperclip loopback across DB9 pins 2-3 on the target jack also works:
  the right index makes `in` count up in step with `out`.
- **Caster rejected / no bytes** — check user/password and that the mountpoint
  exists and is RTCM 3.x (not CMRx). Confirm Wi-Fi/cellular is up.
- **App won't launch in Pydroid** — make sure `kivy` finished installing (first
  import can take a minute); restart Pydroid after the pip install.
