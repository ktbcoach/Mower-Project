"""Assemble multi-sentence NMEA output into one :class:`GnssReading` per epoch.

Feed every incoming sentence to :meth:`GnssAssembler.push`. Field values are
kept as a running "latest" state; a completed reading is emitted when the
trigger sentence (GGA by default — it carries position + fix quality) arrives,
snapshotting the most recent value of every field.
"""

from __future__ import annotations

from dataclasses import fields
from typing import Optional

from . import nmea
from .reading import GnssReading

# Reading fields that are populated from sentence data (everything but sources).
_READING_FIELDS = {f.name for f in fields(GnssReading)} - {"sources"}


class GnssAssembler:
    def __init__(self, emit_on: str = "GGA"):
        self._emit_on = emit_on
        self._latest: dict = {}
        self._epoch_sources: list[str] = []
        self._signal: dict = {}  # (constellation, prn) -> best C/N0 seen this epoch
        self._last_signal: dict = {}  # carried forward on epochs with no fresh GSV

    def push(self, sentence: str) -> Optional[GnssReading]:
        """Consume one sentence; return a GnssReading when an epoch completes."""
        typ = nmea.sentence_type(sentence)

        if typ == "GSV":
            for constellation, prn, _elev, cn0 in nmea.parse_gsv(sentence):
                if cn0 is not None and cn0 > 0:
                    key = (constellation, prn)
                    if cn0 > self._signal.get(key, 0):
                        self._signal[key] = cn0
            if "GSV" not in self._epoch_sources:
                self._epoch_sources.append("GSV")
            return None

        partial = nmea.parse(sentence)
        if partial is None:
            return None
        typ = partial.pop("type")
        for key, value in partial.items():
            if value is not None and key in _READING_FIELDS:
                self._latest[key] = value
        if typ not in self._epoch_sources:
            self._epoch_sources.append(typ)

        if typ == self._emit_on:
            # GSV often arrives slower than GGA; carry the last signal summary
            # forward so C/N0 stays populated between GSV rounds (it changes slowly).
            summary = self._signal_summary()
            if summary:
                self._last_signal = summary
            reading = GnssReading(
                sources=list(self._epoch_sources),
                **self._latest,
                **(summary or self._last_signal),
            )
            self._epoch_sources = []
            self._signal = {}
            return reading
        return None

    def _signal_summary(self) -> dict:
        """Collapse this epoch's per-satellite C/N0 into a compact summary."""
        vals = list(self._signal.values())
        if not vals:
            return {}
        return {
            "sats_tracked": len(vals),
            "cn0_max": float(max(vals)),
            "cn0_avg": round(sum(vals) / len(vals), 1),
        }
