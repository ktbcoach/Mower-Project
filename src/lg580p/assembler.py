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

    def push(self, sentence: str) -> Optional[GnssReading]:
        """Consume one sentence; return a GnssReading when an epoch completes."""
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
            reading = GnssReading(sources=list(self._epoch_sources), **self._latest)
            self._epoch_sources = []
            return reading
        return None
