"""Tests for the CSV and GPX loggers, incl. the flush() path used by the
switch-gated collection loop."""

from watson_dms.logger import CsvLogger, GpxLogger
from watson_dms.parser import parse_line

FIX = parse_line("G 161409.9 -000.8 +00.1 273.4 +028.9 +44.86405 -091.46836 00894")
NOFIX = parse_line("I ******.* -000.8 +00.1 273.4 ****.* +**.***** +***.***** *****")


def test_csv_logger_write_flush_close(tmp_path):
    path = tmp_path / "out.csv"
    logger = CsvLogger(path)
    logger.write(FIX)
    logger.flush()          # must not raise
    logger.write(NOFIX)
    logger.close()

    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert lines[0].startswith("host_time,")   # header
    assert len(lines) == 3                       # header + 2 rows
    assert "44.864050" in lines[1]


def test_gpx_logger_flush_and_only_fixes(tmp_path):
    path = tmp_path / "track.gpx"
    logger = GpxLogger(path)
    assert logger.write(FIX) is True            # has a fix -> recorded
    logger.flush()                               # must not raise (regression guard)
    assert logger.write(NOFIX) is False          # no fix -> skipped
    logger.close()

    text = path.read_text(encoding="utf-8")
    assert text.startswith("<?xml")
    assert text.count("<trkpt") == 1             # only the fix
    assert "</gpx>" in text                       # footer written on close


def test_gpx_flush_after_close_is_safe(tmp_path):
    logger = GpxLogger(tmp_path / "t.gpx")
    logger.close()
    logger.flush()                               # no-op, must not raise
