"""Real-time IMU + GNSS fusion runner: LSM6DSO ESKF -> 50 Hz logged solution.

Mirrors :func:`lg580p.collect.collect` but drives an :class:`~lg580p.ekf.ErrorStateKF`
instead of logging raw epochs. A background thread reads the serial NMEA and
pushes assembled :class:`~lg580p.reading.GnssReading` objects onto a queue; the
main loop is paced by the IMU (predict every sample) and drains the queue to
apply GNSS updates, emitting a fused solution at the requested output rate.

RTK-float / DGPS / single fixes are still fused but with an inflated
measurement noise (see :func:`sigma_for_quality`), so the inertial solution
carries through float dropouts instead of chasing noisy positions. A ``coast_age``
counter tracks time since the last accepted position update and labels each row.
"""

from __future__ import annotations

import datetime as _dt
import math
import queue
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np

from . import serial_io
from .assembler import GnssAssembler
from .collect import _make_injector, _log
from .ekf import EkfConfig, EnuOrigin, ErrorStateKF
from .imu import Lsm6dso, level_from_accel
from .logger import FusedCsvLogger, GpxLogger
from .reading import GnssReading

_FLUSH_INTERVAL_S = 2.0
_RECENT_FIX_S = 1.0        # within this of the last fix -> label with its quality


# --------------------------------------------------------------------------- #
# Measurement-noise policy
# --------------------------------------------------------------------------- #
@dataclass
class NoisePolicy:
    """Per-fix-quality position sigma (m) and the RTK-float down-weight factor."""

    rtk_fixed: float = 0.02
    rtk_float_scale: float = 40.0   # float sigma = rtk_fixed * this (~0.8 m)
    dgps: float = 1.0
    gps: float = 3.0
    hdop_gain: float = 0.1          # sigma *= (1 + hdop_gain * hdop)
    vel_sigma: float = 0.15         # m/s (ground velocity)
    heading_sigma_deg: float = 2.0  # fallback when PQTMTAR accuracy is absent


def sigma_for_quality(quality: Optional[int], hdop: Optional[float],
                      pol: NoisePolicy) -> Optional[float]:
    """Position 1-sigma (m) for a GGA quality code, or None to skip the update.

    HDOP scales the base *multiplicatively* so poor geometry inflates the noise
    without erasing the fixed-vs-float distinction (an additive term would swamp
    the 2 cm RTK-fixed base).
    """
    base = {
        4: pol.rtk_fixed,
        5: pol.rtk_fixed * pol.rtk_float_scale,
        2: pol.dgps,
        1: pol.gps,
    }.get(quality)
    if base is None:
        return None
    if hdop is not None:
        base *= 1.0 + pol.hdop_gain * hdop
    return base


# --------------------------------------------------------------------------- #
# Serial reader thread
# --------------------------------------------------------------------------- #
class _GnssSource(threading.Thread):
    """Read NMEA in the background; push (GnssReading, t_mono) onto a queue."""

    def __init__(self, port: str, baud: int, emit_on: str,
                 rtcm_source: Optional[str], rtcm_baud: int):
        super().__init__(daemon=True)
        self._port, self._baud, self._emit_on = port, baud, emit_on
        self._rtcm_source, self._rtcm_baud = rtcm_source, rtcm_baud
        self.queue: "queue.Queue[tuple[GnssReading, float]]" = queue.Queue()
        self.injector = None
        self._stop = threading.Event()
        self.error: Optional[BaseException] = None

    def run(self) -> None:
        asm = GnssAssembler(emit_on=self._emit_on)
        try:
            with serial_io.open_port(self._port, self._baud, timeout=0.25) as ser:
                self.injector = _make_injector(ser, self._rtcm_source, self._rtcm_baud)
                for line in serial_io.read_lines(ser, idle_tick=True):
                    if self._stop.is_set():
                        break
                    if line is None:
                        continue
                    reading = asm.push(line)
                    if reading is not None:
                        self.queue.put((reading, time.monotonic()))
        except BaseException as exc:  # surface to the main loop
            self.error = exc
        finally:
            if self.injector:
                self.injector.stop()

    def stop(self) -> None:
        self._stop.set()


# --------------------------------------------------------------------------- #
# GNSS update application
# --------------------------------------------------------------------------- #
def _apply_gnss(ekf: ErrorStateKF, r: GnssReading, origin: EnuOrigin,
                pol: NoisePolicy, heading_offset_rad: float = 0.0) -> bool:
    """Apply position/velocity/heading updates from one epoch. Returns True if a
    position update was accepted (used to reset the coast timer)."""
    pos_accepted = False

    if r.has_gps_fix:
        sigma = sigma_for_quality(r.fix_quality, r.hdop, pol)
        if sigma is not None:
            alt = r.altitude_m if r.altitude_m is not None else origin.alt0
            enu = origin.to_enu(r.latitude_deg, r.longitude_deg, alt)
            R = np.diag([sigma ** 2, sigma ** 2, (3.0 * sigma) ** 2])  # loose vertical
            ekf.update_position(enu, R)
            pos_accepted = True

    # Ground velocity from track (COG) — only meaningful once moving.
    if r.speed_kph is not None and r.course_deg is not None and r.speed_kph > 1.0:
        spd = r.speed_kph / 3.6
        crs = math.radians(r.course_deg)
        vel = np.array([spd * math.sin(crs), spd * math.cos(crs), 0.0])
        Rv = np.diag([pol.vel_sigma ** 2, pol.vel_sigma ** 2, 10.0 ** 2])
        ekf.update_velocity(vel, Rv)

    # Dual-antenna heading (PQTMTAR) — absolute yaw, valid even when stationary.
    # heading_offset_rad rotates the baseline heading into vehicle-forward
    # heading (this rig's antennas are mounted laterally, so the offset is ~-90deg).
    if r.heading_deg is not None and r.heading_quality in (4, 5):
        acc = r.heading_accuracy_deg
        sigma_deg = acc if (acc is not None and acc > 0) else pol.heading_sigma_deg
        if r.heading_quality == 5:
            sigma_deg *= 4.0   # down-weight float heading too
        ekf.update_heading(math.radians(r.heading_deg) + heading_offset_rad,
                           math.radians(sigma_deg))

    return pos_accepted


def _label(coast_age: float, last_quality: Optional[str], coast_max: float) -> str:
    if last_quality is not None and coast_age <= _RECENT_FIX_S:
        return last_quality
    if coast_age <= coast_max:
        return "coast"
    return "coast_stale"


def _build_row(ekf: ErrorStateKF, origin: EnuOrigin, last_reading: Optional[GnssReading],
               source: str, coast_age: float, imu_count: int,
               host_time: _dt.datetime) -> dict:
    lat, lon, alt = origin.to_geodetic(ekf.p)
    roll, pitch, yaw = ekf.roll_pitch_yaw
    heading = (math.degrees(yaw)) % 360.0
    r = last_reading
    return {
        "host_time": host_time.isoformat(),
        "utc": (r.utc if r else "") or "",
        "solution_source": source,
        "coast_age_s": f"{coast_age:.2f}",
        "fused_lat": f"{lat:.7f}",
        "fused_lon": f"{lon:.7f}",
        "fused_alt_m": f"{alt:.3f}",
        "vel_e": f"{ekf.v[0]:.3f}",
        "vel_n": f"{ekf.v[1]:.3f}",
        "vel_u": f"{ekf.v[2]:.3f}",
        "speed_mps": f"{float(np.linalg.norm(ekf.v[:2])):.3f}",
        "fused_heading_deg": f"{heading:.2f}",
        "roll_deg": f"{math.degrees(roll):.2f}",
        "pitch_deg": f"{math.degrees(pitch):.2f}",
        "pos_sigma_m": f"{ekf.pos_sigma:.3f}",
        "gyro_bias_x": f"{ekf.b_g[0]:.5f}",
        "gyro_bias_y": f"{ekf.b_g[1]:.5f}",
        "gyro_bias_z": f"{ekf.b_g[2]:.5f}",
        "accel_bias_x": f"{ekf.b_a[0]:.4f}",
        "accel_bias_y": f"{ekf.b_a[1]:.4f}",
        "accel_bias_z": f"{ekf.b_a[2]:.4f}",
        "fix_quality": "" if not r or r.fix_quality is None else r.fix_quality,
        "fix_quality_name": (r.fix_quality_name if r else "") or "",
        "num_sats": "" if not r or r.num_sats is None else r.num_sats,
        "hdop": "" if not r or r.hdop is None else f"{r.hdop:.2f}",
        "imu_count": imu_count,
    }


def _wait_for_first_fix(src: _GnssSource, timeout: float = 120.0) -> GnssReading:
    """Block until the first epoch with a valid position (needed for the origin)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if src.error:
            raise src.error
        try:
            reading, _ = src.queue.get(timeout=0.5)
        except queue.Empty:
            continue
        if reading.has_gps_fix:
            return reading
    raise TimeoutError("no GNSS fix within startup window; check antenna / corrections")


def _read_imu_paced(imu: Lsm6dso):
    """Return one sample, waiting briefly for data-ready to avoid busy-spinning."""
    for _ in range(2000):
        if imu.data_ready():
            break
        time.sleep(0.0002)
    return imu.read_sample()


# --------------------------------------------------------------------------- #
# Main entry
# --------------------------------------------------------------------------- #
def fuse(
    imu: Lsm6dso,
    port: str = serial_io.DEFAULT_PORT,
    baud: int = serial_io.DEFAULT_BAUD,
    csv_path: Optional[str | Path] = None,
    gpx_path: Optional[str | Path] = None,
    rate: float = 50.0,
    coast_max: float = 5.0,
    gyro_cal_s: float = 5.0,
    heading_offset_deg: float = 0.0,
    config: Optional[EkfConfig] = None,
    policy: Optional[NoisePolicy] = None,
    rtcm_source: Optional[str] = None,
    rtcm_baud: int = 57600,
    quiet: bool = False,
) -> None:
    """Run the fusion loop until Ctrl-C. ``imu`` must already be constructed."""
    cfg = config or EkfConfig()
    pol = policy or NoisePolicy()
    out_period = 1.0 / rate
    heading_offset_rad = math.radians(heading_offset_deg)

    src = _GnssSource(port, baud, "GGA", rtcm_source, rtcm_baud)
    csv_logger = FusedCsvLogger(csv_path) if csv_path else None
    gpx_logger = GpxLogger(gpx_path, track_name="LG580P fused track") if gpx_path else None

    src.start()
    try:
        imu.open()
        _log(f"IMU up; calibrating gyro bias ({gyro_cal_s:g}s, hold still)…")
        gyro_bias, mean_accel, n = imu.calibrate(gyro_cal_s)
        roll0, pitch0 = level_from_accel(mean_accel)
        _log(f"gyro bias {np.round(gyro_bias, 4)} rad/s from {int(n)} samples; "
             f"level roll={math.degrees(roll0):.1f} pitch={math.degrees(pitch0):.1f}")

        _log("waiting for first GNSS fix to set the tangent-plane origin…")
        first = _wait_for_first_fix(src)
        origin = EnuOrigin(first.latitude_deg, first.longitude_deg,
                           first.altitude_m if first.altitude_m is not None else 0.0)

        ekf = ErrorStateKF(cfg)
        ekf.set_gyro_bias(gyro_bias)
        if first.heading_deg is not None:
            yaw0 = math.radians(first.heading_deg) + heading_offset_rad
        elif first.course_deg is not None and (first.speed_kph or 0) > 1.0:
            yaw0 = math.radians(first.course_deg)
        else:
            yaw0 = 0.0
        ekf.set_attitude(roll0, pitch0, yaw0)
        ekf.p = origin.to_enu(first.latitude_deg, first.longitude_deg, origin.alt0)
        _log(f"origin set @ {origin.lat0:.7f},{origin.lon0:.7f}; "
             f"initial heading {math.degrees(yaw0):.1f}° — fusing at {rate:g} Hz")

        last_t: Optional[float] = None
        last_pos_t = time.monotonic()
        last_quality: Optional[str] = first.fix_quality_name
        last_reading: Optional[GnssReading] = first
        next_out = time.monotonic() + out_period
        last_flush = time.monotonic()
        imu_count = 0

        while True:
            if src.error:
                raise src.error
            sample = _read_imu_paced(imu)
            if last_t is not None:
                ekf.predict(sample.t_mono - last_t, sample.accel, sample.gyro)
                imu_count += 1
            last_t = sample.t_mono

            # Drain any GNSS epochs that arrived since the last iteration.
            while True:
                try:
                    reading, _t = src.queue.get_nowait()
                except queue.Empty:
                    break
                last_reading = reading
                if _apply_gnss(ekf, reading, origin, pol, heading_offset_rad):
                    last_pos_t = time.monotonic()
                    last_quality = reading.fix_quality_name

            now = time.monotonic()
            if now >= next_out:
                next_out += out_period
                coast_age = now - last_pos_t
                source = _label(coast_age, last_quality, coast_max)
                host_time = _dt.datetime.now(_dt.timezone.utc)
                row = _build_row(ekf, origin, last_reading, source, coast_age,
                                 imu_count, host_time)
                imu_count = 0
                if csv_logger:
                    csv_logger.write(row)
                if gpx_logger:
                    lat, lon, alt = origin.to_geodetic(ekf.p)
                    gpx_logger.write(
                        GnssReading(latitude_deg=lat, longitude_deg=lon,
                                    altitude_m=alt, fix_quality=4), host_time)
                if not quiet:
                    sys.stdout.write(
                        f"\r[{source:<11}] coast={coast_age:4.1f}s "
                        f"hdg={row['fused_heading_deg']:>6}° "
                        f"σ={row['pos_sigma_m']:>5}m")
                    sys.stdout.flush()
                if now - last_flush >= _FLUSH_INTERVAL_S:
                    if csv_logger:
                        csv_logger.flush()
                    if gpx_logger:
                        gpx_logger.flush()
                    last_flush = now
    except KeyboardInterrupt:
        pass
    finally:
        src.stop()
        imu.close()
        if csv_logger:
            csv_logger.close()
        if gpx_logger:
            gpx_logger.close()
        if not quiet:
            _log("fusion stopped")
