"""Tests for the LSM6DSO driver against a fake SPI transfer (no hardware)."""

import math

import numpy as np
import pytest

from lg580p.imu import (
    Lsm6dso,
    _ACCEL_SCALE,
    _GYRO_SCALE,
    level_from_accel,
    parse_axis_remap,
)

_WHO_AM_I = 0x0F
_OUTX_L_G = 0x22
_STATUS_REG = 0x1E


class FakeSpi:
    """Minimal LSM6DSO register model with auto-increment reads/writes."""

    def __init__(self, who=0x6C):
        self.regs = bytearray(256)
        self.regs[_WHO_AM_I] = who
        self.regs[_STATUS_REG] = 0x03   # XLDA | GDA (data ready)
        self.writes: list[tuple[int, int]] = []

    def set_i16(self, reg: int, value: int) -> None:
        v = value & 0xFFFF
        self.regs[reg] = v & 0xFF          # low byte first
        self.regs[reg + 1] = (v >> 8) & 0xFF

    def xfer2(self, data):
        cmd = data[0]
        reg = cmd & 0x7F
        n = len(data) - 1
        if cmd & 0x80:                     # read
            return [0] + [self.regs[(reg + i) & 0xFF] for i in range(n)]
        for i, val in enumerate(data[1:]):  # write
            self.regs[(reg + i) & 0xFF] = val & 0xFF
            self.writes.append((reg + i, val & 0xFF))
        return [0] * len(data)


def test_axis_remap_parsing():
    M = parse_axis_remap("-y,x,z")
    assert np.allclose(M @ np.array([1.0, 2.0, 3.0]), np.array([-2.0, 1.0, 3.0]))


def test_axis_remap_rejects_bad_spec():
    with pytest.raises(ValueError):
        parse_axis_remap("x,y")
    with pytest.raises(ValueError):
        parse_axis_remap("x,q,z")


def test_open_verifies_whoami_and_configures():
    fake = FakeSpi()
    imu = Lsm6dso(transfer=fake.xfer2).open()
    written = dict(fake.writes)
    assert written[0x10] == 0x58   # CTRL1_XL: 208 Hz, +/-4 g
    assert written[0x11] == 0x54   # CTRL2_G: 208 Hz, +/-500 dps
    assert written[0x12] == 0x44   # CTRL3_C: BDU | IF_INC
    assert imu.data_ready() is True


def test_open_rejects_wrong_whoami():
    fake = FakeSpi(who=0x00)
    with pytest.raises(RuntimeError):
        Lsm6dso(transfer=fake.xfer2).open()


def test_read_sample_scales_and_units():
    fake = FakeSpi()
    # gyro raw (100, -200, 300), accel raw (0, 0, 8192).
    fake.set_i16(_OUTX_L_G + 0, 100)
    fake.set_i16(_OUTX_L_G + 2, -200)
    fake.set_i16(_OUTX_L_G + 4, 300)
    fake.set_i16(_OUTX_L_G + 6, 0)
    fake.set_i16(_OUTX_L_G + 8, 0)
    fake.set_i16(_OUTX_L_G + 10, 8192)

    imu = Lsm6dso(transfer=fake.xfer2).open()
    s = imu.read_sample()
    assert s.gyro[0] == pytest.approx(100 * _GYRO_SCALE)
    assert s.gyro[1] == pytest.approx(-200 * _GYRO_SCALE)
    assert s.accel[2] == pytest.approx(8192 * _ACCEL_SCALE)


def test_read_sample_applies_remap():
    fake = FakeSpi()
    fake.set_i16(_OUTX_L_G + 0, 1000)   # gyro x raw
    fake.set_i16(_OUTX_L_G + 2, 2000)   # gyro y raw
    imu = Lsm6dso(transfer=fake.xfer2, axis_remap="-y,x,z").open()
    s = imu.read_sample()
    # body x = -raw_y, body y = raw_x
    assert s.gyro[0] == pytest.approx(-2000 * _GYRO_SCALE)
    assert s.gyro[1] == pytest.approx(1000 * _GYRO_SCALE)


def test_level_from_accel_flat():
    roll, pitch = level_from_accel(np.array([0.0, 0.0, 9.80665]))
    assert roll == pytest.approx(0.0, abs=1e-6)
    assert pitch == pytest.approx(0.0, abs=1e-6)


def test_level_from_accel_pitched_up():
    # Nose-up pitch: gravity leaks onto -x... check the sign is sane.
    roll, pitch = level_from_accel(np.array([0.5, 0.0, 9.79]))
    assert pitch == pytest.approx(math.atan2(0.5, 9.79), abs=1e-6)
    assert roll == pytest.approx(0.0, abs=1e-6)
