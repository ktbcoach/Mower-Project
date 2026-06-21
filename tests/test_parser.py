"""Tests for the DMS-SGP02 decimal ASCII parser.

Covers both the unit's current channel configuration (DEFAULT_CHANNELS) and the
original factory layout (FACTORY_CHANNELS).
"""

import pytest

from watson_dms.parser import (
    DEFAULT_CHANNELS,
    FACTORY_CHANNELS,
    ParseError,
    is_data_line,
    parse_line,
)

# Factory string from the owner's manual (p.8): time, bank, elev, heading,
# velocity, lat, lon, altitude.
FACTORY_EXAMPLE = "G 161409.9 -000.8 +00.1 273.4 +028.9 +44.86405 -091.46836 00894"

# Current config: time, heading, X/Y/Z accel, X/Y/Z rate, heading rate,
# velocity, latitude, longitude, status.
CURRENT_EXAMPLE = (
    "G 161409.9 273.4 +0.01 -0.02 -1.00 +01.5 -00.2 +00.3 +00.0 "
    "+028.9 +44.86405 -091.46836 040"
)


# --- current (default) configuration -----------------------------------------

def test_current_config_parses():
    r = parse_line(CURRENT_EXAMPLE)
    assert r is not None
    assert r.label == "G"
    assert r.heading_mode == "gps_true_north"
    assert r.utc == "16:14:09.9"
    assert r.utc_seconds == pytest.approx(58449.9)
    assert r.heading_deg == pytest.approx(273.4)
    assert r.x_accel_g == pytest.approx(0.01)
    assert r.y_accel_g == pytest.approx(-0.02)
    assert r.z_accel_g == pytest.approx(-1.00)
    assert r.x_rate_dps == pytest.approx(1.5)
    assert r.y_rate_dps == pytest.approx(-0.2)
    assert r.z_rate_dps == pytest.approx(0.3)
    assert r.heading_rate_dps == pytest.approx(0.0)
    assert r.velocity_kph == pytest.approx(28.9)
    assert r.latitude_deg == pytest.approx(44.86405)
    assert r.longitude_deg == pytest.approx(-91.46836)
    assert r.has_gps_fix is True
    # status "040" octal == 32 == bit 5 (Ready) set
    assert r.status_raw == "040"
    assert r.status == 0o040
    assert r.ready is True
    # fields not in this config stay None
    assert r.bank_deg is None
    assert r.altitude_ft is None


def test_current_config_token_count():
    assert len(CURRENT_EXAMPLE.split()) - 1 == len(DEFAULT_CHANNELS)


def test_current_config_no_fix_indoors():
    line = (
        "I ******.* 273.4 +0.01 -0.02 -1.00 +01.5 -00.2 +00.3 +00.0 "
        "****.* +**.***** +***.***** 000"
    )
    r = parse_line(line)
    assert r is not None
    assert r.heading_mode == "relative"
    assert r.utc is None
    assert r.velocity_kph is None
    assert r.latitude_deg is None
    assert r.longitude_deg is None
    assert r.has_gps_fix is False
    # inertial fields still valid
    assert r.heading_deg == pytest.approx(273.4)
    assert r.z_accel_g == pytest.approx(-1.00)
    assert r.status == 0
    assert r.ready is False


# --- factory configuration (explicit channels) -------------------------------

def test_factory_example():
    r = parse_line(FACTORY_EXAMPLE, channels=FACTORY_CHANNELS)
    assert r is not None
    assert r.bank_deg == pytest.approx(-0.8)
    assert r.elevation_deg == pytest.approx(0.1)
    assert r.heading_deg == pytest.approx(273.4)
    assert r.velocity_kph == pytest.approx(28.9)
    assert r.latitude_deg == pytest.approx(44.86405)
    assert r.altitude_ft == pytest.approx(894)
    assert r.altitude_m == pytest.approx(894 / 3.280839895, rel=1e-6)
    assert r.has_gps_fix is True


# --- general behavior --------------------------------------------------------

@pytest.mark.parametrize(
    "label,mode,over",
    [
        ("G", "gps_true_north", False),
        ("T", "gps_track", False),
        ("I", "relative", False),
        ("R", "reference", False),
        ("g", "gps_true_north", True),
        ("i", "relative", True),
    ],
)
def test_labels_and_over_range(label, mode, over):
    line = label + CURRENT_EXAMPLE[1:]
    r = parse_line(line)
    assert r.heading_mode == mode
    assert r.over_range is over


def test_trailing_cr_is_stripped():
    r = parse_line(CURRENT_EXAMPLE + "\r\n")
    assert r is not None
    assert r.heading_deg == pytest.approx(273.4)


def test_header_line_returns_none():
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
    assert is_data_line(CURRENT_EXAMPLE) is True
    assert is_data_line("g 1 2 3") is True
    assert is_data_line("Watson DMS-SGP02") is False
    assert is_data_line("") is False


def test_custom_channels_subset():
    channels = ("heading", "latitude", "longitude")
    r = parse_line("G 273.4 +44.86405 -091.46836", channels=channels)
    assert r.heading_deg == pytest.approx(273.4)
    assert r.latitude_deg == pytest.approx(44.86405)
    assert r.longitude_deg == pytest.approx(-91.46836)
    assert r.bank_deg is None
