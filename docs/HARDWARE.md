# Hardware: Watson DMS-SGP02 → RS232 pHAT → Raspberry Pi 4

Reference notes distilled from the *DMS-SGP02 Owner's Manual* (Rev K, 03/22/2018).
Keep the PDF handy — this is a summary of the parts that matter for data collection.

## What the unit is

The DMS-SGP02 is **not a plain GPS receiver**. It's a Dynamic Measurement
System: a solid-state gyro/accelerometer inertial unit **plus** a dual-antenna
GPS that gives **true-north heading even while stationary**. It outputs heading,
attitude (bank/elevation), angular rates, accelerations, and GPS position over
RS-232.

## Power ⚠️

| Spec | Value |
|------|-------|
| Input voltage | 10–35 VDC (best at **12 V** or 24 V) |
| Current | ~410 mA @ 12 V (≈4.9 W) |
| Power connector | 25-pin **male** D-Sub |

**The Pi cannot power this unit.** Supply 12 V to pin 2 (+) / pin 1 (Power
Ground) of the 25-pin connector from a separate supply or the vehicle battery.
Power ground and signal ground are isolated. Over-voltage or miswiring will
damage the unit.

## Serial wiring (the part that reaches the Pi)

The unit talks RS-232 on a **9-pin female** D-Sub. The RS232 pHAT's MAX3232
converts those ±12 V levels to the Pi's 3.3 V UART on GPIO14/15 → `/dev/serial0`.

DMS-SGP02 9-pin serial connector (manual, Figure 3):

| Pin | Signal | Direction | Connect to |
|-----|--------|-----------|------------|
| 2 | TXD (unit transmits) | DMS → Pi | pHAT RX |
| 3 | RXD (unit receives) | Pi → DMS | pHAT TX |
| 5 | Signal ground | — | pHAT GND |
| 4↔6, 7↔8 | internal loopback | — | leave as-is |

Only pins **2, 3, 5** matter. Pin 3 (Pi→DMS) is only needed to send
configuration commands; logging works with just pins 2 and 5.

> **Straight vs. null-modem:** the pHAT presents a standard DB9. If you capture
> nothing but the `detect` sweep shows activity, or you see garbage at every
> baud, try swapping TX/RX (a null-modem adapter). Confirm empirically with
> `python -m watson_dms capture`.

## Serial parameters

| Setting | Default |
|---------|---------|
| Baud | **9600** (also supports 4800 / 19200 / 38400) |
| Data bits | 8 |
| Parity | None |
| Stop bits | 1 |
| Frame rate | up to 71.11 frames/s (depends on baud + channel count) |
| Startup | ~5 s data, up to 5 min for first satellite acquisition |

## Default output string

```
G 161409.9 -000.8 +00.1 273.4 +028.9 +44.86405 -091.46836 00894 <CR>
```

| # | Field | Notes |
|---|-------|-------|
| — | Label | `G` GPS true-north · `T` track · `I` relative · `R` reference; **lowercase = over-range error** |
| 1 | UTC `HHMMSS.S` | `******.*` when invalid |
| 2 | Bank (roll) ±179.9° | |
| 3 | Elevation (pitch) ±89.9° | |
| 4 | Heading 0–359.9° | |
| 5 | Velocity ±399.9 km/h | `****.*` when invalid |
| 6 | Latitude ±89.99999° | `+**.*****` when invalid |
| 7 | Longitude ±179.99999° | `+***.*****` when invalid |
| 8 | Altitude (feet, 0–21500) | `*****` when invalid; MSL by default |

GPS position accuracy: **±2.5 m** standalone, **±0.6 m** with DGPS. Heading
accuracy depends on antenna spacing (0.5° at 0.5 m → 0.07° at 5 m).

## Antenna mounting (affects heading accuracy)

- "Fore" antenna **ahead** of "Aft" on the vehicle's fore-aft axis; default
  spacing **0.5 m** (configurable 0.3–5.0 m on Rev I+; larger = more accurate
  heading but more multipath risk).
- Each antenna needs a clear sky view and a **≥6"×6" ground plane**; nothing
  nearby higher than the ground plane.
- Latitude/longitude are reported at the **aft** antenna.

## Command mode (changing baud, channels, heading mode)

Most settings live in EEPROM and are changed over the same serial link in
**Command "double-spacebar" mode**:

1. Open a terminal at the current baud (`minicom -D /dev/serial0 -b 9600`).
2. Power-cycle the unit; during the ~5 s init window, **press the spacebar
   twice** in quick succession.
3. After init completes, the `&` key opens the settings menu:
   `1` time constants · `2` output channels · `3` list channels ·
   `4` heading source · `5` velocity source · `6` antenna spacing ·
   `7` altitude correction · `8` baud rate.
4. Make a change permanent by sending a quote `"` character at the new setting;
   otherwise it reverts on the next power-up.

Useful single-key commands (no command mode needed): `!`+space reinitialize,
`F` free mode, `H` hold mode, `K` clear free/hold.

For this project, the **factory defaults (9600 8N1, standard channel string,
GPS true-north heading, MSL altitude) are exactly what `watson_dms` expects** —
you shouldn't need command mode unless you want a faster baud or fewer channels.
