"""Tests for the fusion glue: noise policy, coast labels, and heading offset."""

import math

from lg580p.ekf import EnuOrigin, ErrorStateKF, quat_yaw
from lg580p.fusion import NoisePolicy, _apply_gnss, _label, sigma_for_quality
from lg580p.reading import GnssReading


def test_sigma_table_ranks_by_quality():
    p = NoisePolicy()
    fixed = sigma_for_quality(4, 0.8, p)
    flt = sigma_for_quality(5, 0.8, p)
    dgps = sigma_for_quality(2, 0.8, p)
    assert fixed < flt < dgps          # float down-weighted, worse still looser
    assert flt / fixed == 40.0         # hdop scales multiplicatively, ratio preserved
    assert sigma_for_quality(0, None, p) is None   # no fix -> skip (coast)


def test_coast_labels():
    assert _label(0.2, "rtk_fixed", 5.0) == "rtk_fixed"
    assert _label(3.0, "rtk_fixed", 5.0) == "coast"
    assert _label(6.0, "rtk_fixed", 5.0) == "coast_stale"


def test_heading_offset_rotates_baseline_to_forward():
    """Lateral baseline: PQTMTAR heading 90deg with a -90 offset -> vehicle North."""
    origin = EnuOrigin(44.42, -72.98, 100.0)
    ekf = ErrorStateKF()
    ekf.set_attitude(0.0, 0.0, math.radians(45.0))   # deliberately wrong start
    r = GnssReading(heading_deg=90.0, heading_quality=4, heading_accuracy_deg=0.4)
    for _ in range(20):
        _apply_gnss(ekf, r, origin, NoisePolicy(), heading_offset_rad=math.radians(-90.0))
    assert math.degrees(quat_yaw(ekf.q)) == __import__("pytest").approx(0.0, abs=0.5)


def test_position_update_accepted_flag():
    origin = EnuOrigin(44.42, -72.98, 100.0)
    ekf = ErrorStateKF()
    fixed = GnssReading(latitude_deg=44.4201, longitude_deg=-72.9801, altitude_m=101.0,
                        fix_quality=4, hdop=0.7)
    nofix = GnssReading(fix_quality=0)
    assert _apply_gnss(ekf, fixed, origin, NoisePolicy()) is True
    assert _apply_gnss(ekf, nofix, origin, NoisePolicy()) is False
