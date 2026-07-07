"""Error-state Kalman filter (ESKF) fusing a 6-axis IMU with LG580P GNSS.

Pure ``numpy``; no hardware imports, so this module is fully unit-testable on a
dev machine. It implements the standard multiplicative ESKF (Sola, *Quaternion
kinematics for the error-state Kalman filter*) in a **local ENU tangent frame**
with a **body-frame (local) orientation error**.

Nominal state (carried explicitly):
    p   ENU position (m from the tangent-plane origin)
    v   ENU velocity (m/s)
    q   attitude quaternion, body -> ENU  (w, x, y, z)
    b_a accelerometer bias (body frame, m/s^2)
    b_g gyroscope bias (body frame, rad/s)

Error state (15, the quantity the covariance ``P`` tracks):
    dp(3) dv(3) dtheta(3) db_a(3) db_g(3)

Prediction is driven by the IMU (specific force + angular rate). Measurement
updates come from GNSS: absolute position (GGA), ground velocity (RMC/VTG), and
dual-antenna heading (PQTMTAR). Each update takes its own measurement-noise ``R``
so the caller can inflate it for RTK-float / DGPS fixes (see :mod:`fusion`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import atan2, cos, radians, degrees, sin, sqrt
from typing import Optional

import numpy as np

GRAVITY = 9.80665                     # m/s^2 (WGS84 mean)
_G_ENU = np.array([0.0, 0.0, -GRAVITY])  # gravity vector, ENU (z up)

# WGS84 ellipsoid (for the flat-earth ENU <-> geodetic mapping).
_WGS84_A = 6378137.0
_WGS84_E2 = 6.69437999014e-3


# --------------------------------------------------------------------------- #
# Quaternion / rotation helpers (w, x, y, z convention)
# --------------------------------------------------------------------------- #
def quat_normalize(q: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(q)
    return q / n if n > 0 else np.array([1.0, 0.0, 0.0, 0.0])


def quat_mult(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    aw, ax, ay, az = a
    bw, bx, by, bz = b
    return np.array([
        aw * bw - ax * bx - ay * by - az * bz,
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
    ])


def quat_to_rot(q: np.ndarray) -> np.ndarray:
    """Rotation matrix R (body -> ENU) from quaternion (w, x, y, z)."""
    w, x, y, z = q
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - w * z),     2 * (x * z + w * y)],
        [2 * (x * y + w * z),     1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
        [2 * (x * z - w * y),     2 * (y * z + w * x),     1 - 2 * (x * x + y * y)],
    ])


def quat_from_axis_angle(v: np.ndarray) -> np.ndarray:
    """Exp map: rotation-vector ``v`` (rad) -> unit quaternion (w, x, y, z)."""
    theta = float(np.linalg.norm(v))
    if theta < 1e-12:
        return np.array([1.0, 0.5 * v[0], 0.5 * v[1], 0.5 * v[2]])
    axis = v / theta
    half = 0.5 * theta
    return np.concatenate(([cos(half)], axis * sin(half)))


def rot_to_quat(R: np.ndarray) -> np.ndarray:
    """Rotation matrix -> unit quaternion (w, x, y, z). Shepperd's method."""
    tr = R[0, 0] + R[1, 1] + R[2, 2]
    if tr > 0:
        s = 2.0 * sqrt(tr + 1.0)
        w = 0.25 * s
        x = (R[2, 1] - R[1, 2]) / s
        y = (R[0, 2] - R[2, 0]) / s
        z = (R[1, 0] - R[0, 1]) / s
    elif R[0, 0] > R[1, 1] and R[0, 0] > R[2, 2]:
        s = 2.0 * sqrt(1.0 + R[0, 0] - R[1, 1] - R[2, 2])
        w = (R[2, 1] - R[1, 2]) / s
        x = 0.25 * s
        y = (R[0, 1] + R[1, 0]) / s
        z = (R[0, 2] + R[2, 0]) / s
    elif R[1, 1] > R[2, 2]:
        s = 2.0 * sqrt(1.0 + R[1, 1] - R[0, 0] - R[2, 2])
        w = (R[0, 2] - R[2, 0]) / s
        x = (R[0, 1] + R[1, 0]) / s
        y = 0.25 * s
        z = (R[1, 2] + R[2, 1]) / s
    else:
        s = 2.0 * sqrt(1.0 + R[2, 2] - R[0, 0] - R[1, 1])
        w = (R[1, 0] - R[0, 1]) / s
        x = (R[0, 2] + R[2, 0]) / s
        y = (R[1, 2] + R[2, 1]) / s
        z = 0.25 * s
    return quat_normalize(np.array([w, x, y, z]))


def skew(v: np.ndarray) -> np.ndarray:
    return np.array([
        [0.0, -v[2], v[1]],
        [v[2], 0.0, -v[0]],
        [-v[1], v[0], 0.0],
    ])


def quat_yaw(q: np.ndarray) -> float:
    """Heading (rad, from ENU North, clockwise-positive) of the body x-axis."""
    R = quat_to_rot(q)
    east, north = R[0, 0], R[1, 0]   # body +x expressed in ENU
    return atan2(east, north)


def wrap_pi(a: float) -> float:
    """Wrap an angle to (-pi, pi]."""
    return (a + np.pi) % (2 * np.pi) - np.pi


# --------------------------------------------------------------------------- #
# Local tangent-plane (ENU) <-> geodetic
# --------------------------------------------------------------------------- #
def _earth_radii(lat_rad: float) -> tuple[float, float]:
    s = sin(lat_rad)
    denom = 1.0 - _WGS84_E2 * s * s
    r_n = _WGS84_A / sqrt(denom)                 # prime vertical
    r_m = _WGS84_A * (1.0 - _WGS84_E2) / (denom ** 1.5)  # meridian
    return r_n, r_m


@dataclass
class EnuOrigin:
    """Tangent-plane origin; flat-earth mapping good for local (sub-km) work."""

    lat0: float
    lon0: float
    alt0: float
    _r_n: float = field(init=False)
    _r_m: float = field(init=False)
    _clat: float = field(init=False)

    def __post_init__(self) -> None:
        lat_rad = radians(self.lat0)
        self._r_n, self._r_m = _earth_radii(lat_rad)
        self._clat = cos(lat_rad)

    def to_enu(self, lat: float, lon: float, alt: float) -> np.ndarray:
        east = radians(lon - self.lon0) * (self._r_n + self.alt0) * self._clat
        north = radians(lat - self.lat0) * (self._r_m + self.alt0)
        return np.array([east, north, alt - self.alt0])

    def to_geodetic(self, enu: np.ndarray) -> tuple[float, float, float]:
        east, north, up = float(enu[0]), float(enu[1]), float(enu[2])
        lat = self.lat0 + degrees(north / (self._r_m + self.alt0))
        lon = self.lon0 + degrees(east / ((self._r_n + self.alt0) * self._clat))
        return lat, lon, self.alt0 + up


# --------------------------------------------------------------------------- #
# Tuning
# --------------------------------------------------------------------------- #
@dataclass
class EkfConfig:
    """Process-noise and initial-uncertainty tuning (SI units).

    Defaults are seeded from LSM6DSO datasheet noise densities at 208 Hz and are
    meant as a sane starting point — refine against a recorded drive.
    """

    # Continuous-time IMU noise (density). accel: (m/s^2)/sqrt(Hz); gyro: (rad/s)/sqrt(Hz).
    accel_noise: float = 0.02
    gyro_noise: float = 0.002
    # Bias random-walk. accel: (m/s^2)/s/sqrt(Hz); gyro: (rad/s)/s/sqrt(Hz).
    accel_bias_rw: float = 1e-4
    gyro_bias_rw: float = 1e-5
    # Initial 1-sigma uncertainties.
    init_pos_sigma: float = 1.0        # m
    init_vel_sigma: float = 0.5        # m/s
    init_att_sigma: float = radians(3.0)   # rad (roll/pitch/yaw)
    init_accel_bias_sigma: float = 0.1     # m/s^2
    init_gyro_bias_sigma: float = radians(1.0)  # rad/s

    # Lever arm: IMU origin -> GNSS antenna phase centre, expressed in body frame (m).
    lever_arm: tuple[float, float, float] = (0.0, 0.0, 0.0)


# --------------------------------------------------------------------------- #
# The filter
# --------------------------------------------------------------------------- #
# Error-state index blocks.
_P, _V, _TH, _BA, _BG = (slice(0, 3), slice(3, 6), slice(6, 9),
                         slice(9, 12), slice(12, 15))


class ErrorStateKF:
    """15-state multiplicative ESKF (IMU prediction, GNSS updates)."""

    def __init__(self, config: Optional[EkfConfig] = None):
        self.cfg = config or EkfConfig()
        self.p = np.zeros(3)
        self.v = np.zeros(3)
        self.q = np.array([1.0, 0.0, 0.0, 0.0])   # body -> ENU
        self.b_a = np.zeros(3)
        self.b_g = np.zeros(3)
        self.lever = np.array(self.cfg.lever_arm, dtype=float)

        P = np.zeros((15, 15))
        c = self.cfg
        P[_P, _P] = np.eye(3) * c.init_pos_sigma ** 2
        P[_V, _V] = np.eye(3) * c.init_vel_sigma ** 2
        P[_TH, _TH] = np.eye(3) * c.init_att_sigma ** 2
        P[_BA, _BA] = np.eye(3) * c.init_accel_bias_sigma ** 2
        P[_BG, _BG] = np.eye(3) * c.init_gyro_bias_sigma ** 2
        self.P = P

    # -- initialization ---------------------------------------------------- #
    def set_attitude(self, roll: float, pitch: float, yaw: float) -> None:
        """Seed the quaternion from roll/pitch/yaw (rad).

        Body frame is x=forward, y=left, z=up; ``yaw`` is heading (clockwise from
        ENU North, so East = +90 deg). Built as R = R_heading @ Ry(pitch) @ Rx(roll)
        and converted to a quaternion so it stays consistent with ``quat_yaw``.
        """
        sy, cy = sin(yaw), cos(yaw)
        sp, cp = sin(pitch), cos(pitch)
        sr, cr = sin(roll), cos(roll)
        # Level heading map: body +x -> [sin h, cos h, 0] (East, North).
        R0 = np.array([[sy, -cy, 0.0], [cy, sy, 0.0], [0.0, 0.0, 1.0]])
        Ry = np.array([[cp, 0.0, sp], [0.0, 1.0, 0.0], [-sp, 0.0, cp]])
        Rx = np.array([[1.0, 0.0, 0.0], [0.0, cr, -sr], [0.0, sr, cr]])
        self.q = rot_to_quat(R0 @ Ry @ Rx)

    def set_gyro_bias(self, b_g: np.ndarray) -> None:
        self.b_g = np.array(b_g, dtype=float)

    # -- prediction -------------------------------------------------------- #
    def predict(self, dt: float, accel_m: np.ndarray, gyro_m: np.ndarray) -> None:
        """Propagate the nominal state and covariance over ``dt`` seconds.

        ``accel_m`` is measured specific force (body, m/s^2); ``gyro_m`` is
        measured angular rate (body, rad/s).
        """
        if dt <= 0:
            return
        a = np.asarray(accel_m, float) - self.b_a       # specific force, body
        w = np.asarray(gyro_m, float) - self.b_g        # angular rate, body
        R = quat_to_rot(self.q)
        a_enu = R @ a + _G_ENU

        # Nominal integration.
        self.p = self.p + self.v * dt + 0.5 * a_enu * dt * dt
        self.v = self.v + a_enu * dt
        self.q = quat_normalize(quat_mult(self.q, quat_from_axis_angle(w * dt)))

        # Error-state transition Fx (local orientation error).
        F = np.eye(15)
        F[_P, _V] = np.eye(3) * dt
        F[_V, _TH] = -R @ skew(a) * dt
        F[_V, _BA] = -R * dt
        F[_TH, _TH] = quat_to_rot(quat_from_axis_angle(w * dt)).T
        F[_TH, _BG] = -np.eye(3) * dt

        # Process noise Q (discretised, first order).
        c = self.cfg
        Q = np.zeros((15, 15))
        Q[_V, _V] = np.eye(3) * (c.accel_noise ** 2) * dt
        Q[_TH, _TH] = np.eye(3) * (c.gyro_noise ** 2) * dt
        Q[_BA, _BA] = np.eye(3) * (c.accel_bias_rw ** 2) * dt
        Q[_BG, _BG] = np.eye(3) * (c.gyro_bias_rw ** 2) * dt

        self.P = F @ self.P @ F.T + Q

    # -- generic measurement update ---------------------------------------- #
    def _update(self, H: np.ndarray, residual: np.ndarray, R_meas: np.ndarray) -> None:
        S = H @ self.P @ H.T + R_meas
        K = self.P @ H.T @ np.linalg.inv(S)
        dx = K @ residual

        self.p = self.p + dx[_P]
        self.v = self.v + dx[_V]
        self.q = quat_normalize(quat_mult(self.q, quat_from_axis_angle(dx[_TH])))
        self.b_a = self.b_a + dx[_BA]
        self.b_g = self.b_g + dx[_BG]

        # Joseph-form covariance update (numerically stable, keeps P symmetric).
        I_KH = np.eye(15) - K @ H
        self.P = I_KH @ self.P @ I_KH.T + K @ R_meas @ K.T

    # -- GNSS measurements ------------------------------------------------- #
    def update_position(self, enu: np.ndarray, R_meas: np.ndarray) -> None:
        """Absolute position (ENU, m). Accounts for the antenna lever arm."""
        Rot = quat_to_rot(self.q)
        predicted = self.p + Rot @ self.lever
        H = np.zeros((3, 15))
        H[:, _P] = np.eye(3)
        H[:, _TH] = -Rot @ skew(self.lever)
        self._update(H, np.asarray(enu, float) - predicted, R_meas)

    def update_velocity(self, vel_enu: np.ndarray, R_meas: np.ndarray) -> None:
        """Ground velocity (ENU, m/s). Jacobian kept to the dominant dv block."""
        H = np.zeros((3, 15))
        H[:, _V] = np.eye(3)
        self._update(H, np.asarray(vel_enu, float) - self.v, R_meas)

    def update_heading(self, yaw_meas: float, sigma_rad: float) -> None:
        """Absolute heading (rad, ENU North, clockwise). PQTMTAR dual-antenna."""
        Rot = quat_to_rot(self.q)
        residual = np.array([wrap_pi(yaw_meas - quat_yaw(self.q))])
        H = np.zeros((1, 15))
        # Heading is clockwise-from-North, i.e. the negative of a right-hand
        # rotation about ENU Up; a body error dtheta rotates the world frame by
        # R@dtheta, so d(heading)/d(dtheta) = -Rot[2, :].
        H[0, _TH] = -Rot[2, :]
        self._update(H, residual, np.array([[sigma_rad ** 2]]))

    # -- outputs ----------------------------------------------------------- #
    @property
    def roll_pitch_yaw(self) -> tuple[float, float, float]:
        R = quat_to_rot(self.q)
        yaw = atan2(R[0, 0], R[1, 0])
        pitch = atan2(-R[2, 0], sqrt(R[2, 1] ** 2 + R[2, 2] ** 2))
        roll = atan2(R[2, 1], R[2, 2])
        return roll, pitch, yaw

    @property
    def pos_sigma(self) -> float:
        """Horizontal position 1-sigma (m), sqrt of the E+N variance trace."""
        return float(sqrt(self.P[0, 0] + self.P[1, 1]))
