"""Tests for the DMS-SGP02 decimal ASCII parser.

The canonical example comes straight from the owner's manual (p.8):

    G 161409.9 -000.8 +00.1 273.4 +028.9 +44.86405 -091.46836 00894
"""

import math

import pytest

from watson_dms.parser import (
    DEFAULT_CHANNELS,
    ParseError,
    is_data_line,
    parse_line,
)

MANUAL_EXAMPLE = "G 161409.9 -000.8 +00.1 273.4 +028.9 +44.86405 -091.46836 00894"


def test_manual_example():
    r = parse_line(MANUAL_EXAMPLE)
    assert r is not None
    assert r.label == "G"
    assert r.heading_mode == "gps_true_north"
    assert r.over_range is False
    assert r.utc == "16:14:09.9"
    assert r.utc_seconds == pytest.approx(58449.9)
    assert r.bank_deg == pytest.approx(-0.8)
    assert r.elevation_deg == pytest.approx(0.1)
    assert r.heading_deg == pytest.approx(273.4)
    assert r.velocity_kph == pytest.approx(28.9)
    assert r.latitude_deg == pytest.approx(44.86405)
    assert r.longitude_deg == pytest.approx(-91.46836)
    assert r.altitude_ft == pytest.approx(894)
    assert r.has_gps_fix is True


def test_altitude_conversion():
    r = parse_line(MANUAL_EXAMPLE)
    assert r.altitude_m == pytest.approx(894 / 3.280839895, rel=1e-6)


def test_trailing_cr_is_stripped():
    r = parse_line(MANUAL_EXAMPLE + "\r\n")
    assert r is not None
    assert r.heading_deg == pytest.approx(273.4)


@pytest.mark.parametrize(
    "label,mode,over",
    [
        ("G", "gps_true_north", False),
        ("T", "gps_track", False),
        ("I", "relative", False),
        ("R", "reference", False),
        ("g", "gps_true_north", True),
        ("t", "gps_track", True),
        ("i", "relative", True),
        ("r", "reference", True),
    ],
)
def test_labels_and_over_range(label, mode, over):
    line = label + " 161409.9 -000.8 +00.1 273.4 +028.9 +44.86405 -091.46836 00894"
    r = parse_line(line)
    assert r.heading_mode == mode
    assert r.over_range is over


def test_invalid_fields_become_none():
    # GPS not yet locked: UTC, velocity, lat, lon, altitude all asterisks.
    line = "I ******.* -000.8 +00.1 273.4 ****.* +**.***** +***.***** *****"
    r = parse_line(line)
    assert r is not None
    assert r.utc is None
    assert r.utc_seconds is None
    assert r.velocity_kph is None
    assert r.latitude_deg is None
    assert r.longitude_deg is None
    assert r.altitude_ft is None
    assert r.altitude_m is None
    assert r.has_gps_fix is False
    # Inertial fields are still valid.
    assert r.bank_deg == pytest.approx(-0.8)
    assert r.heading_deg == pytest.approx(273.4)


def test_header_line_returns_none():
    # The power-on identification banner is not a data frame.
    assert parse_line("Watson Industries DMS-SGP02 S/N 1234 Rev K") is None
    assert parse_line("") is None
    assert parse_line("   ") is None


def test_field_count_mismatch_returns_none():
    assert parse_line("G 161409.9 -000.8") is None


def test_strict_raises_on_bad_line():
    with pytest.raises(ParseError):
        parse_line("garbage that is not a frame", strict=True)
    with pytest.raises(ParseError):
        parse_line("G 161409.9 -000.8", strict=True)


def test_is_data_line():
    assert is_data_line(MANUAL_EXAMPLE) is True
    assert is_data_line("g 1 2 3") is True
    assert is_data_line("Watson DMS-SGP02") is False
    assert is_data_line("") is False


def test_custom_channels_subset():
    # If the unit is reconfigured to output only heading + lat + lon.
    channels = ("heading", "latitude", "longitude")
    r = parse_line("G 273.4 +44.86405 -091.46836", channels=channels)
    assert r.heading_deg == pytest.approx(273.4)
    assert r.latitude_deg == pytest.approx(44.86405)
    assert r.longitude_deg == pytest.approx(-91.46836)
    assert r.bank_deg is None  # not in the configured channel set


def test_default_channels_length_matches_standard_string():
    assert len(DEFAULT_CHANNELS) == len(MANUAL_EXAMPLE.split()) - 1
