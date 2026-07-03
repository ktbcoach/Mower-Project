"""Tests for GSV signal aggregation and the $PRSTAT rover telemetry wire format."""

from lg580p.assembler import GnssAssembler
from lg580p.nmea import parse_gsv
from lg580p.reading import GnssReading
from lg580p.telemetry import build_status_sentence, parse_status_sentence

# Real LG580P captures (valid checksums).
GGA = "$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*47"
GPGSV = "$GPGSV,3,1,09,03,28,242,23,16,62,210,33,26,81,072,32,29,20,048,25,1*68"
GAGSV = "$GAGSV,1,1,02,04,19,134,30,09,71,144,36,7*73"


# --- GSV parsing -------------------------------------------------------------

def test_parse_gsv_extracts_sats_and_cn0():
    sats = parse_gsv(GPGSV)
    assert sats == [
        ("GP", 3, 28, 23), ("GP", 16, 62, 33),
        ("GP", 26, 81, 32), ("GP", 29, 20, 25),
    ]  # trailing signal-id field is dropped


def test_parse_gsv_rejects_bad_checksum_and_non_gsv():
    assert parse_gsv(GPGSV[:-2] + "ZZ") == []
    assert parse_gsv(GGA) == []


# --- assembler signal summary ------------------------------------------------

def test_assembler_aggregates_signal_across_constellations():
    asm = GnssAssembler(emit_on="GGA")
    assert asm.push(GPGSV) is None      # cn0: 23, 33, 32, 25
    assert asm.push(GAGSV) is None      # cn0: 30, 36
    reading = asm.push(GGA)             # GGA closes the epoch
    assert reading is not None
    assert reading.sats_tracked == 6
    assert reading.cn0_max == 36.0
    assert reading.cn0_avg == round((23 + 33 + 32 + 25 + 30 + 36) / 6, 1)  # 29.8


def test_assembler_resets_signal_each_epoch():
    asm = GnssAssembler(emit_on="GGA")
    asm.push(GPGSV)
    asm.push(GGA)
    second = asm.push(GGA)              # no GSV this epoch
    assert second.cn0_max is None
    assert second.sats_tracked is None


# --- $PRSTAT round trip ------------------------------------------------------

def test_status_sentence_round_trip():
    r = GnssReading(
        fix_quality=4, num_sats=18, sats_tracked=22, cn0_max=47.0, cn0_avg=43.5,
        latitude_deg=44.4201841, longitude_deg=-72.9836642, altitude_m=201.3,
        heading_deg=311.9, heading_quality=5, hdop=0.8, speed_kph=1.2,
    )
    st = parse_status_sentence(build_status_sentence(r, 42, True, True))
    assert st is not None
    assert st.seq == 42
    assert st.fix_quality == 4 and st.rtk_fixed is True
    assert st.fix_quality_name == "rtk_fixed"
    assert st.sats_used == 18 and st.sats_tracked == 22
    assert st.cn0_max == 47.0 and st.cn0_avg == 43.5
    assert st.latitude_deg == 44.4201841
    assert st.longitude_deg == -72.9836642
    assert st.heading_deg == 311.9 and st.heading_quality == 5
    assert st.hdop == 0.8 and st.speed_kph == 1.2
    assert st.logging is True and st.corr is True


def test_status_sentence_handles_no_reading():
    st = parse_status_sentence(build_status_sentence(None, 0, False, False))
    assert st is not None
    assert st.fix_quality is None and st.fix_quality_name is None
    assert st.latitude_deg is None
    assert st.logging is False and st.corr is False


def test_parse_status_rejects_bad_and_foreign_lines():
    good = build_status_sentence(None, 1, False, False)
    body = good[1:good.rfind("*")]
    assert parse_status_sentence(f"${body}*ZZ") is None   # bad checksum
    assert parse_status_sentence(GGA) is None             # not a PRSTAT line
