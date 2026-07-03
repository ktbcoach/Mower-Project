"""Tests for the LG580P CSV logger, focused on the signal (C/N0) columns."""

import csv

from lg580p.logger import CSV_FIELDS, CsvLogger
from lg580p.reading import GnssReading


def _read_rows(path):
    with path.open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def test_csv_has_signal_columns():
    for col in ("sats_tracked", "cn0_max", "cn0_avg"):
        assert col in CSV_FIELDS


def test_csv_writes_signal_values(tmp_path):
    path = tmp_path / "out.csv"
    r = GnssReading(
        fix_quality=4, num_sats=18, sats_tracked=22, cn0_max=47.0, cn0_avg=43.5,
        latitude_deg=44.4201841, longitude_deg=-72.9836642,
    )
    with CsvLogger(path) as log:
        log.write(r)
    row = _read_rows(path)[0]
    assert row["sats_tracked"] == "22"
    assert row["cn0_max"] == "47.0"
    assert row["cn0_avg"] == "43.5"


def test_csv_blank_signal_when_absent(tmp_path):
    path = tmp_path / "out.csv"
    with CsvLogger(path) as log:
        log.write(GnssReading(fix_quality=1, num_sats=8))
    row = _read_rows(path)[0]
    assert row["sats_tracked"] == ""
    assert row["cn0_max"] == ""
    assert row["cn0_avg"] == ""
