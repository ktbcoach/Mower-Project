"""SparkFun LSM6DSO 6-axis IMU driver (SPI) for the rover.

STMicro LSM6DSO: 3-axis accel + 3-axis gyro, SPI mode 0 (also supports mode 3;
mode 0 is used by default as it works on both SPI0 and the aux SPI1). Wired to
the Pi's SPI bus (the Multi-IO HAT uses only I2C + UART5, so SPI0 is free). This
driver configures the sensor, applies the datasheet sensitivity to yield SI
units, and remaps the sensor axes into the vehicle body frame.

Body-frame convention (matches :mod:`lg580p.ekf`): x = forward, y = left,
z = up. Set ``axis_remap`` to whatever rotates the physical mounting into that.

Like :mod:`serial_io`, the hardware dependency (``spidev``) is imported lazily so
the module can be imported (and unit-tested with an injected transfer function)
on a machine without it.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np

# -- register map ----------------------------------------------------------- #
_WHO_AM_I = 0x0F
_CTRL1_XL = 0x10
_CTRL2_G = 0x11
_CTRL3_C = 0x12
_STATUS_REG = 0x1E
_OUTX_L_G = 0x22          # gyro X..Z (6 bytes) then accel X..Z (6 bytes)

_WHO_AM_I_VAL = 0x6C      # LSM6DSO identity
_READ = 0x80             # SPI read bit (OR'd into the register address)

# CTRL1_XL: ODR 208 Hz (0b0101<<4) | FS +/-4g (0b10<<2)  -> 0x58
_CTRL1_XL_208_4G = 0x58
# CTRL2_G:  ODR 208 Hz (0b0101<<4) | FS +/-500 dps (0b01<<2) -> 0x54
_CTRL2_G_208_500DPS = 0x54
# CTRL3_C:  BDU (0x40) | IF_INC auto address increment (0x04) -> 0x44
_CTRL3_C_BDU_IFINC = 0x44

# Sensitivity at the configured full scales.
_ACCEL_MG_PER_LSB = 0.122               # +/-4 g
_GYRO_MDPS_PER_LSB = 17.50              # +/-500 dps
_ACCEL_SCALE = _ACCEL_MG_PER_LSB * 1e-3 * 9.80665          # LSB -> m/s^2
_GYRO_SCALE = np.radians(_GYRO_MDPS_PER_LSB * 1e-3)        # LSB -> rad/s

# ODR code (bits 7:4 of CTRL1/CTRL2) -> Hz, for --imu-odr selection.
_ODR_CODES = {12: 0x1, 26: 0x2, 52: 0x3, 104: 0x4, 208: 0x5, 416: 0x6, 833: 0x7}


def parse_axis_remap(spec: str) -> np.ndarray:
    """Parse e.g. ``"x,y,z"`` or ``"-y,x,z"`` into a 3x3 signed permutation.

    The result ``M`` maps a raw sensor vector to the body frame: ``v_body = M @ v_raw``.
    """
    axis_idx = {"x": 0, "y": 1, "z": 2}
    M = np.zeros((3, 3))
    parts = [t.strip().lower() for t in spec.split(",")]
    if len(parts) != 3:
        raise ValueError(f"axis remap needs 3 comma-separated terms, got {spec!r}")
    for row, term in enumerate(parts):
        sign = -1.0 if term.startswith("-") else 1.0
        letter = term.lstrip("+-")
        if letter not in axis_idx:
            raise ValueError(f"bad axis term {term!r} in remap {spec!r}")
        M[row, axis_idx[letter]] = sign
    return M


def _to_int16(hi: int, lo: int) -> int:
    val = (hi << 8) | lo
    return val - 0x10000 if val & 0x8000 else val


@dataclass
class ImuSample:
    t_mono: float
    accel: np.ndarray   # m/s^2, body frame
    gyro: np.ndarray    # rad/s, body frame


class Lsm6dso:
    """LSM6DSO over SPI. ``transfer`` is an ``xfer2``-style callable for testing."""

    def __init__(
        self,
        bus: int = 0,
        cs: int = 0,
        odr_hz: int = 208,
        axis_remap: str = "x,y,z",
        speed_hz: int = 8_000_000,
        spi_mode: int = 0,
        transfer: Optional[Callable[[list[int]], list[int]]] = None,
    ):
        self.odr_hz = odr_hz
        self.remap = parse_axis_remap(axis_remap)
        self._spi = None
        if transfer is not None:
            self._transfer = transfer
        else:
            self._spi = _open_spi(bus, cs, speed_hz, spi_mode)
            self._transfer = self._spi.xfer2

    # -- low-level SPI ----------------------------------------------------- #
    def _write(self, reg: int, val: int) -> None:
        self._transfer([reg & 0x7F, val & 0xFF])

    def _read(self, reg: int, n: int = 1) -> list[int]:
        resp = self._transfer([(reg & 0x7F) | _READ] + [0x00] * n)
        return list(resp[1:])

    # -- lifecycle --------------------------------------------------------- #
    def open(self) -> "Lsm6dso":
        who = self._read(_WHO_AM_I)[0]
        if who != _WHO_AM_I_VAL:
            raise RuntimeError(
                f"LSM6DSO WHO_AM_I mismatch: got 0x{who:02X}, expected 0x{_WHO_AM_I_VAL:02X} "
                f"(check SPI wiring/CS/bus, --spi-mode, and that this is an LSM6DSO)"
            )
        code = _ODR_CODES.get(self.odr_hz)
        if code is None:
            raise ValueError(f"unsupported ODR {self.odr_hz}; choose one of {sorted(_ODR_CODES)}")
        ctrl1 = (code << 4) | (_CTRL1_XL_208_4G & 0x0F)
        ctrl2 = (code << 4) | (_CTRL2_G_208_500DPS & 0x0F)
        self._write(_CTRL3_C, _CTRL3_C_BDU_IFINC)
        self._write(_CTRL1_XL, ctrl1)
        self._write(_CTRL2_G, ctrl2)
        time.sleep(0.05)   # let the first samples settle
        return self

    def close(self) -> None:
        if self._spi is not None:
            self._spi.close()
            self._spi = None

    def __enter__(self) -> "Lsm6dso":
        return self.open()

    def __exit__(self, *exc) -> None:
        self.close()

    # -- data -------------------------------------------------------------- #
    def data_ready(self) -> bool:
        return bool(self._read(_STATUS_REG)[0] & 0x03)   # XLDA | GDA

    def read_sample(self) -> ImuSample:
        """Read one gyro+accel sample, scaled to SI and remapped to body frame."""
        raw = self._read(_OUTX_L_G, 12)
        t = time.monotonic()
        gyro_raw = np.array([
            _to_int16(raw[1], raw[0]),
            _to_int16(raw[3], raw[2]),
            _to_int16(raw[5], raw[4]),
        ], dtype=float)
        accel_raw = np.array([
            _to_int16(raw[7], raw[6]),
            _to_int16(raw[9], raw[8]),
            _to_int16(raw[11], raw[10]),
        ], dtype=float)
        gyro = self.remap @ (gyro_raw * _GYRO_SCALE)
        accel = self.remap @ (accel_raw * _ACCEL_SCALE)
        return ImuSample(t_mono=t, accel=accel, gyro=gyro)

    # -- calibration ------------------------------------------------------- #
    def calibrate(self, seconds: float = 5.0) -> tuple[np.ndarray, np.ndarray, float]:
        """Average a stationary window. Returns (gyro_bias, mean_accel, n_used).

        ``gyro_bias`` (rad/s, body) seeds the EKF gyro bias. ``mean_accel`` is the
        gravity direction used to level (roll/pitch) the initial attitude.
        """
        gyros: list[np.ndarray] = []
        accels: list[np.ndarray] = []
        t_end = time.monotonic() + seconds
        period = 1.0 / max(self.odr_hz, 1)
        while time.monotonic() < t_end:
            s = self.read_sample()
            gyros.append(s.gyro)
            accels.append(s.accel)
            time.sleep(period)
        if not gyros:
            return np.zeros(3), np.array([0.0, 0.0, 9.80665]), 0.0
        return (np.mean(gyros, axis=0), np.mean(accels, axis=0), float(len(gyros)))


def level_from_accel(mean_accel: np.ndarray) -> tuple[float, float]:
    """Roll/pitch (rad) from a stationary gravity sample (body x=fwd, y=left, z=up)."""
    ax, ay, az = float(mean_accel[0]), float(mean_accel[1]), float(mean_accel[2])
    roll = np.arctan2(-ay, az)
    pitch = np.arctan2(ax, np.hypot(ay, az))
    return roll, pitch


def _open_spi(bus: int, cs: int, speed_hz: int, mode: int = 0):
    try:
        import spidev  # type: ignore
    except ImportError as exc:  # pragma: no cover - only without spidev
        raise ImportError(
            "spidev is required to talk to the LSM6DSO over SPI. Install it with:\n"
            "    pip install spidev   (and enable SPI: dtparam=spi=on)"
        ) from exc
    spi = spidev.SpiDev()
    spi.open(bus, cs)
    spi.max_speed_hz = speed_hz
    # LSM6DSO supports SPI mode 0 (CPOL=CPHA=0) and mode 3. Mode 0 is the safe
    # default: it works on SPI0 and on the Pi's aux SPI (SPI1), which is flaky
    # with mode 3 (CPHA=1). Override with --spi-mode if needed.
    spi.mode = mode & 0b11
    return spi
