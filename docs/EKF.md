# IMU + GNSS sensor fusion (`fuse` command)

Fuses a **SparkFun LSM6DSO** 6-axis IMU (over SPI) with the LG580P RTK GNSS
using an error-state Kalman filter (ESKF), producing a smoothed **50 Hz**
solution that **coasts through RTK-float dropouts** on inertial dead-reckoning
instead of chasing noisy float positions.

It runs alongside the raw `collect` logger — nothing in the existing path
changes. New code: `imu.py` (driver), `ekf.py` (filter), `fusion.py` (runner),
plus a `FusedCsvLogger` in `logger.py` and the `fuse` CLI subcommand.

## Hardware

| | |
|---|---|
| IMU | SparkFun LSM6DSO (STMicro, 6-axis, `WHO_AM_I` = `0x6C`) |
| Interface | Pi **SPI0** — MOSI GPIO10, MISO GPIO9, SCLK GPIO11, CE0 GPIO8 (CE1 GPIO7) |
| SPI mode | **3** (CPOL=1, CPHA=1), up to ~10 MHz |
| Config | accel ODR 208 Hz ±4 g · gyro ODR 208 Hz ±500 dps · BDU + auto-increment |
| Coexistence | The Multi-IO HAT uses only I2C + UART5, so SPI0 is free |

Enable SPI once on the Pi:

```bash
sudo raspi-config nonint do_spi 0     # or add dtparam=spi=on to /boot/firmware/config.txt
sudo reboot
ls /dev/spidev0.*                     # spidev0.0 (CE0), spidev0.1 (CE1)
```

Install the driver dependency (into the same venv as the logger):

```bash
pip install -e ".[pi]"                # pulls spidev + SMmultiio, or: pip install spidev
```

## Body frame, axis remap, and lever arm

The EKF works in a body frame of **x = forward, y = left, z = up** and a local
ENU tangent plane (origin = first GNSS fix). Two mounting-dependent settings map
your hardware into that frame — set them on the command line, no code changes:

- `--axis-remap` — rotates the *sensor* axes into the body frame. Each term is
  the body axis's source sensor axis with an optional sign, e.g. the default
  `x,y,z` (identity) or `-y,x,z` (sensor rotated 90°). Body order is fwd,left,up.
- `--lever-arm x,y,z` — offset in **body metres** from the IMU to the GNSS
  **antenna phase centre**. Corrects the position measurement and removes the
  rotation-induced velocity error when turning. Default `0,0,0`.

Get the axis remap right by watching the live roll/pitch while tilting the rover:
nose-up should read positive pitch, right-side-down positive roll.

### This rover's geometry (the defaults)

The antennas are mounted **side-to-side** (lateral baseline): **primary on the
left, secondary 1 m to its right**. The IMU's **Y+ is parallel to the baseline,
pointing at the primary** (= vehicle left) and Z+ is up, so sensor X+ = forward
and the sensor is already body-aligned → **`--axis-remap x,y,z`** (identity).

The **primary antenna** (where GGA position is reported) is **17.5" left** and
**0.5" forward** of the IMU, same height → lever arm **`0.0127,0.4445,0`** m
(`--lever-arm`). The fused position (`fused_lat/lon`) is therefore the **IMU**
location, ~17.5" right of the primary antenna; zero the lever arm to log the
antenna position instead.

**Heading offset — important.** Because the baseline is lateral, the receiver's
`PQTMTAR` heading follows the baseline, not vehicle forward. With the primary on
the left, vehicle heading ≈ `PQTMTAR − 90°`, applied via **`--heading-offset`
(default −90)**. The exact sign depends on the receiver's ANT1→ANT2 convention,
so **verify it in the field**: drive a straight line and compare the fused
heading against the GNSS course-over-ground (RMC/VTG track); if they differ by
180°, flip to `+90`. All three are the `fuse` defaults for this build.

> Also set the receiver's dual-antenna baseline to the real 1 m spacing so
> `PQTMTAR` heading is accurate: `python -m lg580p config set-baseline 1.0`.

> With a lateral baseline the `PQTMTAR` *pitch* field actually reflects vehicle
> **roll**; the EKF does not fuse it (roll/pitch come from the accelerometer),
> so there is no conflict.

## Coasting & the RTK-float policy

Every GNSS position is still fused, but the **measurement noise scales with fix
quality** (`fusion.NoisePolicy` / `sigma_for_quality`):

| Fix quality | Position 1σ (default) |
|---|---|
| 4 RTK fixed | 0.02 m |
| 5 RTK float | 0.02 × `--float-scale` (default 40) ≈ **0.8 m** |
| 2 DGPS | 1.0 m |
| 1 GPS/single | 3.0 m |
| 0 no fix | *skipped* — pure inertial coast |

HDOP scales these multiplicatively. Because float is down-weighted ~40×, the
IMU dominates during a float stretch and the track stays smooth; the filter
still nudges toward the float position rather than going fully blind.

Each output row carries `solution_source` + `coast_age_s`:

- `rtk_fixed` / `rtk_float` / `dgps` / `gps` — last accepted position was this
  quality and recent (≤ 1 s ago).
- `coast` — no position accepted for 1 s … `--coast-max` (default **5 s**).
- `coast_stale` — coasted longer than `--coast-max`; still emitted, but flagged
  so downstream can distinguish trustworthy dead-reckoning from drift.

## Running

```bash
# Stationary bring-up (hold still ~5 s for gyro-bias calibration):
python -m lg580p fuse --gyro-cal 5 --rate 50

# With RTK corrections from the RS232 radio and a mounting offset:
python -m lg580p fuse \
    --rtcm-source /dev/ttyAMA5 --rtcm-baud 57600 \
    --axis-remap -y,x,z --lever-arm 0.30,0,0.15 \
    --log-dir logs

# Output: logs/lg580p-fused-<ts>.csv  and  .gpx (fused track)
```

Startup sequence: open IMU → calibrate gyro bias & level from gravity → wait for
the first GNSS fix (sets the ENU origin + seeds heading from `PQTMTAR`) → fuse.

### Field switch (Multi-IO HAT dry-contact + LEDs)

`--switch` mirrors `collect --switch`: the same flags (`--hat-stack`,
`--gps-led`, `--logging-led`, `--contact-channel`, `--contact-invert`) gate
**logging**, not fusion — the IMU calibrates once and the EKF runs
continuously from launch (so it's warm the instant the switch flips ON), and
each ON period writes its own `lg580p-fused-<ts>.csv`/`.gpx` file set.

```bash
python -m lg580p fuse --switch --rtcm-source /dev/ttyAMA5
sudo bash scripts/install_lg580p_service.sh   # if you want this at boot instead of `collect`
```

Only run one of `collect --switch` / `fuse --switch` at a time — both grab the
serial port and the HAT.

### Verifying without RTK corrections (standalone GPS)

`fuse` needs no `--rtcm-source` to run — the terminal status line is the
verification tool:

```bash
python -m lg580p fuse --gyro-cal 5
```

```
[gps        ] coast= 0.3s lat= 44.4200000 lon= -72.9800000 hdg= 90.00deg spd=0.000m/s q=gps       sd=1.414m
```

With no corrections flowing, `q=` should read **`gps`** (quality 1, standalone)
— it will never reach `dgps`/`rtk_float`/`rtk_fixed` without a correction
source. Confirms: the position sigma (`sd=`) sits around the `NoisePolicy.gps`
default (~3 m, HDOP-scaled) instead of the tight RTK values, heading tracks
`PQTMTAR` if the dual-antenna solution is up, and pulling the antenna cable
should show `q=` drop to `coast`/`coast_stale` after `--coast-max` while the
position keeps moving smoothly on IMU alone.

## Tuning

- **`EkfConfig`** (`ekf.py`) — process noise (`accel_noise`, `gyro_noise`, bias
  random walks) and initial uncertainties. Defaults come from the LSM6DSO
  datasheet noise densities at 208 Hz; refine against a recorded drive.
- **`NoisePolicy`** (`fusion.py`) — the per-quality position σ table above,
  velocity σ, and heading σ (used when `PQTMTAR` accuracy is absent).
- `--float-scale` is the quickest knob: raise it to trust float less (coast
  harder), lower it to follow float more closely.

## Testing

`test_ekf.py` and `test_imu.py` run with no hardware (numpy + a fake SPI):

```bash
python -m pytest tests/test_ekf.py tests/test_imu.py -q
```

They cover the mechanization (stationary stability, constant-acceleration
integration, gyro heading), the GNSS updates (position pull, float down-weight,
heading wrap), coasting (growing covariance), the ENU round-trip, and the driver
(WHO_AM_I, register config, scaling, axis remap).
