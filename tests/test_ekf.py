"""Tests for the error-state Kalman filter (pure numpy, no hardware).

These drive the EKF with synthetic IMU + GNSS to check that the mechanization,
measurement updates, and coasting behave as designed.
"""

import math

import numpy as np
import pytest

from lg580p.ekf import (
    EkfConfig,
    EnuOrigin,
    ErrorStateKF,
    quat_yaw,
    wrap_pi,
)

GRAVITY = 9.80665


def _level_ekf(cfg=None) -> ErrorStateKF:
    """An EKF sitting level and stationary, heading due East (yaw=90deg)."""
    ekf = ErrorStateKF(cfg)
    ekf.set_attitude(0.0, 0.0, math.radians(90.0))
    return ekf


def test_stationary_level_stays_put():
    ekf = _level_ekf()
    # Level & still: accel reads +g on body up-axis, gyro reads zero.
    accel = np.array([0.0, 0.0, GRAVITY])
    gyro = np.zeros(3)
    for _ in range(500):
        ekf.predict(0.01, accel, gyro)
    # No net acceleration -> position/velocity barely move.
    assert np.linalg.norm(ekf.p) < 0.05
    assert np.linalg.norm(ekf.v) < 0.05


def test_constant_acceleration_forward():
    ekf = _level_ekf()
    # Heading East (yaw=90): body +x maps to ENU +East. Push 1 m/s^2 forward.
    a_fwd = 1.0
    accel = np.array([a_fwd, 0.0, GRAVITY])
    gyro = np.zeros(3)
    t = 0.0
    for _ in range(1000):
        ekf.predict(0.01, accel, gyro)
        t += 0.01
    # x = 0.5 a t^2 along East.
    expected = 0.5 * a_fwd * t * t
    assert ekf.p[0] == pytest.approx(expected, rel=1e-3)
    assert abs(ekf.p[1]) < 1e-3   # no northward drift
    assert ekf.v[0] == pytest.approx(a_fwd * t, rel=1e-3)


def test_position_update_pulls_state():
    ekf = _level_ekf()
    R = np.eye(3) * 0.01 ** 2
    target = np.array([5.0, 3.0, 0.0])
    for _ in range(20):
        ekf.update_position(target, R)
    assert np.allclose(ekf.p, target, atol=0.05)
    assert ekf.pos_sigma < 0.1


def test_float_downweight_moves_less_than_fixed():
    """A tight (fixed) update should move the state far more than a loose (float) one."""
    err = np.array([10.0, 0.0, 0.0])   # 10 m position error to correct

    tight = _level_ekf()
    tight.update_position(err, np.eye(3) * 0.02 ** 2)

    loose = _level_ekf()
    loose.update_position(err, np.eye(3) * 0.8 ** 2)

    # Fixed corrects noticeably harder and ends more certain than float.
    assert loose.p[0] < 0.8 * tight.p[0]
    assert loose.pos_sigma > tight.pos_sigma


def test_heading_update_and_wrap():
    ekf = _level_ekf()   # starts at yaw=90deg
    for _ in range(10):
        ekf.update_heading(math.radians(80.0), math.radians(1.0))
    yaw = math.degrees(quat_yaw(ekf.q))
    assert yaw == pytest.approx(80.0, abs=0.5)

    # Wrap: measuring -179 vs current +179 is a 2-degree move, not 358.
    e = ErrorStateKF()
    e.set_attitude(0, 0, math.radians(179.0))
    e.update_heading(math.radians(-179.0), math.radians(0.5))
    yaw = math.degrees(quat_yaw(e.q))
    assert abs(wrap_pi(math.radians(yaw) - math.radians(-179.0))) < math.radians(1.0)


def test_coast_grows_covariance_without_updates():
    ekf = _level_ekf()
    ekf.update_position(np.zeros(3), np.eye(3) * 0.02 ** 2)
    sigma0 = ekf.pos_sigma
    accel = np.array([0.0, 0.0, GRAVITY])
    for _ in range(250):   # 5 s of pure inertial coast at 50 Hz
        ekf.predict(0.02, accel, np.zeros(3))
    assert ekf.pos_sigma > sigma0   # uncertainty must grow while coasting


def test_enu_roundtrip():
    origin = EnuOrigin(44.4201841, -72.9836642, 100.0)
    enu = origin.to_enu(44.4211841, -72.9826642, 105.0)
    lat, lon, alt = origin.to_geodetic(enu)
    assert lat == pytest.approx(44.4211841, abs=1e-7)
    assert lon == pytest.approx(-72.9826642, abs=1e-7)
    assert alt == pytest.approx(105.0, abs=1e-3)
    # ~111 m north for 0.001 deg latitude.
    assert enu[1] == pytest.approx(111.0, abs=2.0)


def test_gyro_integrates_heading():
    ekf = _level_ekf()   # heading 90deg (East)
    # +z body rate = counter-clockwise-from-above = a LEFT turn, so the
    # clockwise-from-North heading *decreases*: 90 -> 80 over 1 s at 10 deg/s.
    rate = math.radians(10.0)
    gyro = np.array([0.0, 0.0, rate])
    accel = np.array([0.0, 0.0, GRAVITY])
    for _ in range(100):        # 1 s
        ekf.predict(0.01, accel, gyro)
    yaw = math.degrees(quat_yaw(ekf.q))
    assert yaw == pytest.approx(80.0, abs=1.0)
