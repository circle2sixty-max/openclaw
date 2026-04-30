"""Unit tests for Music Speaks utility functions."""

import pytest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import (
    normalize_client_id,
    normalize_draft_id,
    safe_name,
    download_file_name,
    ascii_header_file_name,
    clean_song_title,
    normalize_generated_song_title,
    fallback_song_title,
    clean_generated_lyrics,
    clean_draft_payload,
    public_job,
    admin_job,
    refresh_job_lyric_timing,
)


class TestNormalizeClientId:
    def test_valid_id_unchanged(self):
        assert normalize_client_id("abc123DEF_.-") == "abc123DEF_.-"

    def test_valid_8_chars(self):
        assert normalize_client_id("abcdefgh") == "abcdefgh"

    def test_valid_160_chars(self):
        id_160 = "a" * 160
        assert normalize_client_id(id_160) == id_160

    def test_invalid_too_short(self):
        assert normalize_client_id("abc") == "anonymous"

    def test_invalid_special_chars(self):
        assert normalize_client_id("abc@def!") == "anonymous"

    def test_none_input(self):
        assert normalize_client_id(None) == "anonymous"

    def test_empty_string(self):
        assert normalize_client_id("") == "anonymous"

    def test_whitespace_stripped(self):
        assert normalize_client_id("  abcdefgh  ") == "abcdefgh"


class TestNormalizeDraftId:
    def test_valid_id_unchanged(self):
        assert normalize_draft_id("abc123DEF_.-") == "abc123DEF_.-"

    def test_invalid_too_short(self):
        assert normalize_draft_id("abc") == ""

    def test_none_input(self):
        assert normalize_draft_id(None) == ""

    def test_empty_string(self):
        assert normalize_draft_id("") == ""


class TestSafeName:
    def test_alphanumeric_preserved(self):
        assert safe_name("MySong_v2.mp3") == "mysong_v2.mp3"

    def test_spaces_to_dashes(self):
        assert safe_name("My Song Title") == "my-song-title"

    def test_fallback_used_when_empty(self):
        assert safe_name("   ") == "terry-music"

    def test_truncated_to_80_chars(self):
        long_name = "a" * 200
        result = safe_name(long_name)
        assert len(result) == 80

    def test_special_chars_removed(self):
        assert safe_name("song@#$%test") == "song-test"

    def test_custom_fallback(self):
        assert safe_name("   ", fallback="custom") == "custom"


class TestDownloadFileName:
    def test_adds_mp3_extension(self):
        assert download_file_name("My Song") == "My Song.mp3"

    def test_preserves_existing_mp3(self):
        assert download_file_name("My Song.mp3") == "My Song.mp3"

    def test_fallback_when_empty(self):
        assert download_file_name("") == "terry-music.mp3"

    def test_truncates_long_names(self):
        long_name = "a" * 200
        result = download_file_name(long_name)
        assert len(result) <= 124  # 120 + .mp3

    def test_removes_control_chars(self):
        assert download_file_name("song\x00title") == "song-title.mp3"


class TestAsciiHeaderFileName:
    def test_basic_usage(self):
        assert ascii_header_file_name("My Song") == "my-song.mp3"

    def test_with_mp3(self):
        assert ascii_header_file_name("My Song.mp3") == "my-song.mp3"


class TestCleanSongTitle:
    def test_removes_ansi_codes(self):
        assert clean_song_title("\x1b[32mGreen Title\x1b[0m") == "Green Title"

    def test_removes_markdown_code_blocks(self):
        assert clean_song_title("```markdown\nSong Title\n```") == "Song Title"

    def test_strips_prefixes(self):
        assert clean_song_title("Title: My Song") == "My Song"

    def test_strips_chinese_prefixes(self):
        assert clean_song_title("歌名：我的歌") == "我的歌"

    def test_takes_first_line(self):
        assert clean_song_title("Song Title\nSecond Line") == "Song Title"

    def test_removes_mp3_suffix(self):
        assert clean_song_title("My Song.mp3") == "My Song"

    def test_truncates_to_120(self):
        long_title = "a" * 200
        result = clean_song_title(long_title)
        assert len(result) == 120


class TestGeneratedSongTitle:
    def test_rejects_english_first_line_title(self):
        lyrics = "I carry my dreams through the quiet night\nChasing every spark until morning light"
        assert normalize_generated_song_title("I carry my dreams through the quiet night", lyrics, "en") == ""

    def test_rejects_chinese_first_line_title(self):
        lyrics = "清晨的路上我背着旧行囊\n追逐梦想穿过风雨"
        assert normalize_generated_song_title("清晨的路上我背着旧行囊", lyrics, "zh") == ""

    def test_fallback_chinese_title_uses_theme(self):
        lyrics = (
            "[Verse]\n"
            "清晨的路上我背着旧行囊\n"
            "追逐梦想穿过风雨\n"
            "就算跌倒也永不放弃\n\n"
            "[Chorus]\n"
            "奔向星光，奔向远方\n"
            "把希望唱到天亮"
        )
        job = {"prompt": "upbeat pop, happy feelings", "lyrics_idea": "关于追逐梦想，永不放弃的故事"}
        title = fallback_song_title(job, lyrics)
        assert title != "清晨的路上我背着旧行囊"
        assert 4 <= len(title) <= 12
        assert title == "追梦不止"

    def test_fallback_english_title_uses_imagery(self):
        lyrics = (
            "[Verse]\n"
            "I watch the streetlights bending in the window\n"
            "Rain keeps tracing memories across the glass\n\n"
            "[Chorus]\n"
            "Every drop reminds me where we started\n"
            "Every echo pulls me gently back"
        )
        title = fallback_song_title({"prompt": "melancholic piano ballad"}, lyrics)
        assert title != "I watch the streetlights bending in the window"
        assert 2 <= len(title.split()) <= 6
        assert title == "Window in the Rain"


class TestCleanGeneratedLyrics:
    def test_removes_ansi_codes(self):
        assert clean_generated_lyrics("\x1b[33mVerse one\x1b[0m") == "Verse one"

    def test_removes_markdown_blocks(self):
        assert clean_generated_lyrics("```\nlyrics here\n```") == "lyrics here"

    def test_strips_lyrics_prefix(self):
        assert clean_generated_lyrics("Lyrics:\nVerse one") == "Verse one"

    def test_strips_chinese_prefix(self):
        assert clean_generated_lyrics("歌词：第一节") == "第一节"

    def test_truncates_to_6000(self):
        long_lyrics = "a" * 7000
        result = clean_generated_lyrics(long_lyrics)
        assert len(result) == 6000


class TestCleanDraftPayload:
    def test_strips_and_truncates_fields(self):
        form = {"prompt": "  test prompt  ", "email": "a" * 500}
        result = clean_draft_payload(form)
        assert result["prompt"] == "test prompt"
        assert len(result["email"]) == 320

    def test_boolean_conversion(self):
        # bool("false") is True since it's a non-empty string.
        # The app uses bool(form.get("field")) which checks truthiness.
        form = {"is_instrumental": "0", "lyrics_optimizer": ""}
        result = clean_draft_payload(form)
        assert result["is_instrumental"] is True  # "0" is truthy
        assert result["lyrics_optimizer"] is False  # empty string is falsy

    def test_missing_fields_get_empty_string(self):
        form = {}
        result = clean_draft_payload(form)
        assert result["prompt"] == ""
        assert result["email"] == ""

    def test_preserves_lyrics_extra(self):
        form = {"lyrics_extra": "  darker bridge, longer chorus  "}
        result = clean_draft_payload(form)
        assert result["lyrics_extra"] == "darker bridge, longer chorus"


class TestPublicJob:
    def test_excludes_sensitive_fields(self):
        job = {
            "id": "job1",
            "status": "pending",
            "owner_id": "secret-owner",
            "lyrics": "secret lyrics",
            "email": "hidden@example.com",
        }
        result = public_job(job)
        assert "owner_id" not in result
        assert "lyrics" not in result
        assert result["email"] == "hidden@example.com"

    def test_includes_lyric_timing_when_requested(self):
        job = {
            "id": "job1",
            "status": "completed",
            "lyrics": "hello",
            "lyric_timestamps": [{"time": 0.0, "end": 3.0, "row_index": 0, "text": "hello"}],
            "lyric_timing_source": "audio-segment",
            "file_path": "/tmp/demo.mp3",
        }
        result = public_job(job, include_lyrics=True)
        assert result["lyric_timestamps"][0]["row_index"] == 0
        assert result["lyric_timing_source"] == "audio-segment"

    def test_adds_download_url_when_completed(self):
        job = {
            "id": "job1",
            "status": "completed",
            "file_path": "/path/to/file.mp3",
        }
        result = public_job(job)
        assert "download_url" in result
        assert "job1" in result["download_url"]

    def test_no_download_url_when_pending(self):
        job = {"id": "job1", "status": "pending"}
        result = public_job(job)
        assert "download_url" not in result


class TestAdminJob:
    def test_includes_owner_id(self):
        job = {"id": "job1", "owner_id": "owner123"}
        result = admin_job(job)
        assert result["owner_id"] == "owner123"


class TestRefreshJobLyricTiming:
    def test_skips_non_completed_jobs(self):
        assert refresh_job_lyric_timing({"status": "queued"}) is False

    def test_includes_lyrics(self):
        job = {"id": "job1", "lyrics": "test lyrics"}
        result = admin_job(job)
        assert result["lyrics"] == "test lyrics"
