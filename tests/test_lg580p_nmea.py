"""Tests for the LG580P NMEA parser and epoch assembler.

Uses canonical NMEA 0183 example sentences (with known-correct checksums).
"""

import pytest

from lg580p.assembler import GnssAssembler
from lg580p.nmea import checksum_ok, parse, parse_coord, sentence_type
from lg580p.reading import GnssReading

GGA = "$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*47"
RMC = "$GPRMC,123519,A,4807.038,N,01131.000,E,022.4,084.4,230394,003.1,W*6A"
VTG = "$GPVTG,054.7,T,034.4,M,005.5,N,010.2,K*48"


# --- checksum / helpers ------------------------------------------------------

def test_checksum_ok():
    assert checksum_ok(GGA) is True
    assert checksum_ok(RMC) is True
    assert checksum_ok(VTG) is True


def test_checksum_rejects_corruption():
    corrupt = GGA.replace("545.4", "545.5")   # body changed, checksum now wrong
    assert checksum_ok(corrupt) is False
    assert checksum_ok("not a sentence") is False
    assert checksum_ok("$GPGGA,no,star") is False


def test_sentence_type_is_talker_agnostic():
    assert sentence_type("$GNGGA,...*00") == "GGA"
    assert sentence_type("$GPRMC,...*00") == "RMC"
    assert sentence_type("$PQTMTAR,...*00") == "PQTMTAR"


def test_parse_coord():
    assert parse_coord("4807.038", "N", 2) == pytest.approx(48.1173, abs=1e-4)
    assert parse_coord("01131.000", "E", 3) == pytest.approx(11.516667, abs=1e-5)
    assert parse_coord("4807.038", "S", 2) == pytest.approx(-48.1173, abs=1e-4)
    assert parse_coord("", "N", 2) is None


# --- per-sentence parsing ----------------------------------------------------

def test_parse_gga():
    d = parse(GGA)
    assert d["type"] == "GGA"
    assert d["utc"] == "12:35:19.00"
    assert d["latitude_deg"] == pytest.approx(48.1173, abs=1e-4)
    assert d["longitude_deg"] == pytest.approx(11.516667, abs=1e-5)
    assert d["fix_quality"] == 1
    assert d["num_sats"] == 8
    assert d["hdop"] == pytest.approx(0.9)
    assert d["altitude_m"] == pytest.approx(545.4)


def test_parse_rmc():
    d = parse(RMC)
    assert d["type"] == "RMC"
    assert d["latitude_deg"] == pytest.approx(48.1173, abs=1e-4)
    assert d["longitude_deg"] == pytest.approx(11.516667, abs=1e-5)
    assert d["speed_kph"] == pytest.approx(22.4 * 1.852)
    assert d["course_deg"] == pytest.approx(84.4)
    assert d["date"] == "2094-03-23"


def test_parse_vtg():
    d = parse(VTG)
    assert d["type"] == "VTG"
    assert d["course_deg"] == pytest.approx(54.7)
    assert d["speed_kph"] == pytest.approx(10.2)


def test_parse_rejects_bad_checksum():
    assert parse(GGA.replace("545.4", "999.9")) is None


# --- assembler ---------------------------------------------------------------

def test_assembler_emits_on_gga():
    asm = GnssAssembler()
    assert asm.push(VTG) is None          # accumulates, no emit
    assert asm.push(RMC) is None
    r = asm.push(GGA)                     # GGA completes the epoch
    assert isinstance(r, GnssReading)
    assert r.latitude_deg == pytest.approx(48.1173, abs=1e-4)
    assert r.fix_quality == 1
    assert r.fix_quality_name == "gps"
    assert r.has_gps_fix is True
    assert r.rtk_fixed is False
    assert r.num_sats == 8
    # velocity merged in from RMC/VTG before the GGA
    assert r.speed_kph is not None
    assert r.course_deg is not None
    assert "GGA" in r.sources


def test_assembler_rtk_fixed_quality():
    gga_rtk = "$GPGGA,123519,4807.038,N,01131.000,E,4,12,0.6,545.4,M,46.9,M,1.0,0000"
    # append correct checksum
    body = gga_rtk[1:]
    cksum = 0
    for ch in body:
        cksum ^= ord(ch)
    line = f"{gga_rtk}*{cksum:02X}"
    r = GnssAssembler().push(line)
    assert r is not None
    assert r.fix_quality == 4
    assert r.rtk_fixed is True
    assert r.fix_quality_name == "rtk_fixed"


def test_reading_altitude_ft():
    r = GnssAssembler().push(GGA)
    assert r.altitude_ft == pytest.approx(545.4 / 0.3048, rel=1e-6)
