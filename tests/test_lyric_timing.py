"""Unit tests for lyric timing helpers."""

from pathlib import Path

from music_speaks.lyric_timing import (
    build_duration_weighted_timestamps,
    build_lyric_timestamps,
    parse_embedded_timestamps,
    parse_lyric_rows,
)


def test_parse_lyric_rows_strips_multiple_prefix_tags():
    rows = parse_lyric_rows("[Verse]\n[00:01.20][00:04.10]Hello neon night")
    assert rows == [
        {"index": 0, "text": "[Verse]", "is_section": True},
        {"index": 1, "text": "Hello neon night", "is_section": False},
    ]


def test_parse_embedded_timestamps_keeps_row_index_for_multi_stamp_line():
    timestamps = parse_embedded_timestamps("[00:01.20][00:04.10]Hello neon night")
    assert [round(item["time"], 2) for item in timestamps] == [1.2, 4.1]
    assert all(item["row_index"] == 0 for item in timestamps)
    assert all(item["text"] == "Hello neon night" for item in timestamps)


def test_build_duration_weighted_timestamps_uses_line_lengths():
    timestamps = build_duration_weighted_timestamps("short\nthis is much longer", duration=12)
    assert len(timestamps) == 2
    assert timestamps[0]["row_index"] == 0
    assert timestamps[1]["row_index"] == 1
    assert timestamps[0]["time"] == 0
    assert timestamps[1]["time"] > 2
    assert timestamps[-1]["end"] == 12


def test_build_lyric_timestamps_prefers_embedded_lrc_without_audio_probe():
    timestamps, source = build_lyric_timestamps("[00:00.50]Start\n[00:05.00]Glow", audio_path=None)
    assert source == "embedded-lrc"
    assert [item["row_index"] for item in timestamps] == [0, 1]
    assert timestamps[0]["end"] == 5.0


def test_build_lyric_timestamps_uses_audio_segments_when_available(monkeypatch):
    monkeypatch.setattr("music_speaks.lyric_timing.probe_audio_duration", lambda _: 9.0)
    monkeypatch.setattr(
        "music_speaks.lyric_timing.detect_voiced_segments",
        lambda *_: [(0.0, 1.8), (2.4, 4.2), (5.1, 8.8)],
    )
    audio_ref = Path(__file__)
    timestamps, source = build_lyric_timestamps("first line\nsecond line\nthird line", audio_path=audio_ref)
    assert source == "audio-segment"
    assert len(timestamps) == 3
    assert timestamps[1]["time"] >= 2.3
    assert timestamps[2]["time"] >= 5.0
