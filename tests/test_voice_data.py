"""Tests for voice metadata helpers."""

from music_speaks.voice_data import (
    build_voice_metadata_map,
    infer_voice_use_case,
    normalize_voice_display_name,
)


def test_normalize_voice_display_name_strips_language_prefix():
    assert normalize_voice_display_name("Chinese (Mandarin)_Reliable_Executive", "Chinese (Mandarin)") == "Reliable Executive"


def test_infer_voice_use_case_detects_broadcast_role():
    assert infer_voice_use_case("Chinese (Mandarin)_News_Anchor", "News Anchor") == "Broadcast / host"


def test_build_voice_metadata_map_includes_preview_and_language_source():
    meta = build_voice_metadata_map(["English_Trustworthy_Man", "Arnold"], fallback=False)
    assert meta["English_Trustworthy_Man"]["language"] == "English"
    assert meta["English_Trustworthy_Man"]["language_source"] == "prefix"
    assert meta["English_Trustworthy_Man"]["preview_supported"] is True
    assert meta["Arnold"]["language"] == "English"
    assert meta["Arnold"]["language_source"] == "default"
