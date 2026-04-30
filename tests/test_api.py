"""Integration tests for Music Speaks API endpoints.

Run with: python -m pytest tests/test_api.py -v
Or: python -m unittest tests.test_api -v

For full testing, set environment variables:
- MINIMAX_API_KEY: skip if not configured (lyrics/voice tests will be skipped)
"""

import pytest
import sys
import os
import json
import time
import threading
import urllib.request
import urllib.error
from http import HTTPStatus

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------------------------------------------------------------------------
# Test server lifecycle
# ---------------------------------------------------------------------------

import app as music_app
from http.server import ThreadingHTTPServer


@pytest.fixture(scope="module")
def base_url():
    """Start the app in a background thread and return the base URL."""
    # Reset global state
    music_app.JOBS.clear()
    music_app.DRAFTS.clear()

    # Use a random free port
    server = ThreadingHTTPServer(("127.0.0.1", 0), music_app.MusicHandler)
    port = server.server_address[1]

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.2)  # let server start

    yield f"http://127.0.0.1:{port}"

    server.shutdown()
    music_app.JOBS.clear()
    music_app.DRAFTS.clear()


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def api_get(path: str, base_url: str, headers: dict | None = None) -> tuple[int, dict | str]:
    headers = headers or {}
    req = urllib.request.Request(f"{base_url}{path}", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = resp.read()
            content_type = resp.headers.get("Content-Type", "")
            if "application/json" in content_type:
                return resp.status, json.loads(body)
            return resp.status, body.decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        body = e.read()
        content_type = e.headers.get("Content-Type", "")
        if "application/json" in content_type:
            return e.code, json.loads(body) if body else {}
        return e.code, body.decode("utf-8", errors="replace") if body else {}


def api_post(path: str, base_url: str, data: dict, headers: dict | None = None) -> tuple[int, dict | str]:
    headers = dict(headers or {})
    headers.setdefault("Content-Type", "application/json")
    body = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(f"{base_url}{path}", data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            content_type = resp.headers.get("Content-Type", "")
            if "application/json" in content_type:
                return resp.status, json.loads(resp.read())
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        body = e.read()
        content_type = e.headers.get("Content-Type", "")
        if "application/json" in content_type:
            return e.code, json.loads(body) if body else {}
        return e.code, body.decode("utf-8", errors="replace") if body else {}


def api_delete(path: str, base_url: str, headers: dict | None = None) -> tuple[int, dict | str]:
    headers = headers or {}
    req = urllib.request.Request(f"{base_url}{path}", headers=headers, method="DELETE")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            content_type = resp.headers.get("Content-Type", "")
            if "application/json" in content_type:
                return resp.status, json.loads(resp.read())
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        body = e.read()
        content_type = e.headers.get("Content-Type", "")
        if "application/json" in content_type:
            return e.code, json.loads(body) if body else {}
        return e.code, body.decode("utf-8", errors="replace") if body else {}


def api_head(path: str, base_url: str, headers: dict | None = None) -> tuple[int, dict]:
    headers = headers or {}
    req = urllib.request.Request(f"{base_url}{path}", headers=headers, method="HEAD")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers)


CLIENT_ID = "test-client-12345"


# ---------------------------------------------------------------------------
# Health endpoint
# ---------------------------------------------------------------------------

class TestHealthEndpoint:
    def test_health_returns_ok(self, base_url):
        status, body = api_get("/api/health", base_url)
        assert status == HTTPStatus.OK
        assert body["ok"] is True

    def test_health_has_config_flags(self, base_url):
        _, body = api_get("/api/health?key=test-admin-key", base_url)
        assert "minimax_configured" in body
        assert "admin_configured" in body
        assert "smtp_configured" in body
        assert "drafts" in body
        assert "smtp_host" in body
        assert "smtp_port" in body


# ---------------------------------------------------------------------------
# Jobs endpoints
# ---------------------------------------------------------------------------

class TestJobsEndpoint:
    def test_get_jobs_empty(self, base_url):
        status, body = api_get("/api/jobs", base_url, {"X-Client-Id": CLIENT_ID})
        assert status == HTTPStatus.OK
        assert isinstance(body, dict)
        assert body["jobs"] == []

    def test_get_jobs_requires_client_id(self, base_url):
        status, body = api_get("/api/jobs", base_url)
        assert status == HTTPStatus.BAD_REQUEST
        assert "error" in body

    def test_create_job_requires_lyrics_for_vocal(self, base_url):
        """Vocal track (no instrumental flag) without lyrics/idea returns 400."""
        payload = {"prompt": "A calming piano melody"}
        status, body = api_post("/api/jobs", base_url, payload, {"X-Client-Id": CLIENT_ID})
        assert status == HTTPStatus.BAD_REQUEST
        assert "error" in body

    def test_create_job_minimal_instrumental(self, base_url):
        """Instrumental track requires only prompt."""
        payload = {
            "prompt": "A calming piano melody",
            "is_instrumental": True,
        }
        status, body = api_post("/api/jobs", base_url, payload, {"X-Client-Id": CLIENT_ID})
        assert status == HTTPStatus.ACCEPTED
        assert isinstance(body, dict)
        assert "job" in body
        assert body["job"]["status"] in ("queued", "pending")

    def test_create_job_with_lyrics(self, base_url):
        """POST /api/jobs with lyrics."""
        payload = {
            "prompt": "Upbeat dance track",
            "lyrics": "Walking on the dance floor all night long",
        }
        status, body = api_post("/api/jobs", base_url, payload, {"X-Client-Id": CLIENT_ID})
        assert status == HTTPStatus.ACCEPTED
        assert isinstance(body, dict)
        assert "job" in body
        assert body["job"]["lyrics"] == "Walking on the dance floor all night long"

    def test_create_job_with_lyrics_idea(self, base_url):
        """POST /api/jobs with only lyrics_idea (auto lyrics generation)."""
        payload = {
            "prompt": "A song about spring",
            "lyrics_idea": "A happy spring morning",
        }
        status, body = api_post("/api/jobs", base_url, payload, {"X-Client-Id": CLIENT_ID})
        assert status == HTTPStatus.ACCEPTED
        assert "job" in body

    def test_get_job_by_id(self, base_url):
        # create
        _, created = api_post("/api/jobs", base_url,
            {"prompt": "test", "is_instrumental": True}, {"X-Client-Id": CLIENT_ID})
        job_id = created["job"]["id"]
        # get
        status, body = api_get(f"/api/jobs/{job_id}", base_url, {"X-Client-Id": CLIENT_ID})
        assert status == HTTPStatus.OK
        assert body["id"] == job_id

    def test_get_job_not_found(self, base_url):
        status, body = api_get("/api/jobs/nonexistent-id", base_url, {"X-Client-Id": CLIENT_ID})
        assert status == HTTPStatus.NOT_FOUND

    def test_get_job_wrong_client(self, base_url):
        # create with one client
        _, created = api_post("/api/jobs", base_url,
            {"prompt": "secret", "is_instrumental": True}, {"X-Client-Id": CLIENT_ID})
        job_id = created["job"]["id"]
        # access with another client
        status, _ = api_get(f"/api/jobs/{job_id}", base_url, {"X-Client-Id": "other-client-xx"})
        assert status == HTTPStatus.NOT_FOUND

    def test_delete_job(self, base_url):
        _, created = api_post("/api/jobs", base_url,
            {"prompt": "to-delete", "is_instrumental": True}, {"X-Client-Id": CLIENT_ID})
        job_id = created["job"]["id"]
        status, _ = api_delete(f"/api/jobs/{job_id}", base_url, {"X-Client-Id": CLIENT_ID})
        assert status == HTTPStatus.NO_CONTENT

    def test_delete_job_not_found(self, base_url):
        status, _ = api_delete("/api/jobs/nonexistent-id", base_url, {"X-Client-Id": CLIENT_ID})
        assert status == HTTPStatus.NOT_FOUND


# ---------------------------------------------------------------------------
# Drafts endpoints
# ---------------------------------------------------------------------------

class TestDraftsEndpoint:
    def test_save_and_get_draft(self, base_url):
        draft_data = {
            "prompt": "A draft prompt",
            "song_title": "Draft Song",
            "email": "draft@example.com",
            "genre": "pop",
        }
        # save draft via POST
        status1, _ = api_post("/api/drafts/test-draft-001", base_url, draft_data, {"X-Client-Id": CLIENT_ID})
        # Note: the app uses JSON for draft save (not form-encoded)
        # GET /api/drafts/<id>
        status2, body2 = api_get("/api/drafts/test-draft-001", base_url, {"X-Client-Id": CLIENT_ID})
        assert status2 == HTTPStatus.OK
        assert body2["draft"]["prompt"] == "A draft prompt"

    def test_get_draft_not_found(self, base_url):
        status, body = api_get("/api/drafts/nonexistent-id", base_url, {"X-Client-Id": CLIENT_ID})
        # App returns 200 with {"draft": null} for not found
        assert status == HTTPStatus.OK
        assert body["draft"] is None


# ---------------------------------------------------------------------------
# Lyrics generation endpoint
# ---------------------------------------------------------------------------

class TestLyricsEndpoint:
    def test_generate_lyrics_requires_lyrics_idea(self, base_url):
        """Without lyrics_idea, should return 400."""
        status, body = api_post("/api/lyrics", base_url, {})
        assert status in (HTTPStatus.BAD_REQUEST, HTTPStatus.INTERNAL_SERVER_ERROR)

    def test_generate_lyrics_minimal(self, base_url, monkeypatch):
        """Send minimal lyrics_idea payload."""
        monkeypatch.setattr(music_app, "generate_lyrics_from_text_model", lambda job, timeout=180: "la " * 500)
        monkeypatch.setattr(music_app, "generate_title_from_text_model", lambda job, lyrics, timeout=180: "Spring Song")
        status, body = api_post("/api/lyrics", base_url, {"lyrics_idea": "a song about spring"})
        assert status == HTTPStatus.OK
        assert isinstance(body, dict)
        assert "lyrics" in body

    def test_generate_lyrics_returns_title_before_music_generation(self, base_url, monkeypatch):
        """Lyrics helper should create/fill a song title before POST /api/jobs."""
        monkeypatch.setattr(music_app, "generate_lyrics_from_text_model", lambda job, timeout=180: "Spring morning light\nCarries hope across the sky")
        monkeypatch.setattr(music_app, "generate_title_from_text_model", lambda job, lyrics, timeout=180: "Spring Morning")
        status, body = api_post("/api/lyrics", base_url, {"lyrics_idea": "a song about spring", "prompt": "bright pop"})
        assert status == HTTPStatus.OK
        assert body["lyrics"] == "Spring morning light\nCarries hope across the sky"
        assert body["song_title"] == "Spring Morning"
        assert body["generated_title"] is True

    def test_generate_lyrics_keeps_user_supplied_title(self, base_url, monkeypatch):
        """If user already entered a title, lyrics helper must not replace it."""
        monkeypatch.setattr(music_app, "generate_lyrics_from_text_model", lambda job, timeout=180: "Moonlit code sings softly")
        status, body = api_post("/api/lyrics", base_url, {"lyrics_idea": "night song", "song_title": "My Own Title"})
        assert status == HTTPStatus.OK
        assert body["song_title"] == "My Own Title"
        assert body["generated_title"] is False


# ---------------------------------------------------------------------------
# Voice endpoints
# ---------------------------------------------------------------------------

class TestVoiceEndpoints:
    def test_get_voices_returns_list(self, base_url):
        """GET /api/voice returns list of voices."""
        status, body = api_get("/api/voice", base_url, {"X-Client-Id": CLIENT_ID})
        assert status == HTTPStatus.OK
        assert isinstance(body, dict)
        assert isinstance(body.get("voices"), list)

    def test_get_voices_returns_voice_metadata(self, base_url):
        status, body = api_get("/api/voice", base_url, {"X-Client-Id": CLIENT_ID})
        assert status == HTTPStatus.OK
        assert isinstance(body.get("voice_meta"), dict)
        if body.get("voices"):
            meta = body["voice_meta"][body["voices"][0]]
            assert "language" in meta
            assert "display_name" in meta
            assert "preview_supported" in meta

    def test_voice_preview_returns_audio(self, base_url, monkeypatch, tmp_path):
        def fake_synthesize_speech(text, voice_id, output_path, model="speech-2.8-hd"):
            output_path.write_bytes(b"ID3fake-preview")
            return output_path

        monkeypatch.setattr(music_app, "synthesize_speech", fake_synthesize_speech)
        req = urllib.request.Request(
            f"{base_url}/api/voice/preview?voice_id=Chinese%20%28Mandarin%29_Reliable_Executive",
            headers={"Accept": "audio/mpeg"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = resp.read()
            assert resp.status == HTTPStatus.OK
            assert resp.headers.get("Content-Type") == "audio/mpeg"
            assert body.startswith(b"ID3")

    def test_voice_sing_requires_api_key(self, base_url):
        """Without MINIMAX_API_KEY, voice/sing fails gracefully."""
        status, body = api_post("/api/voice/sing", base_url, {"lyrics": "test", "voice_id": "test"})
        assert status == HTTPStatus.BAD_REQUEST


# ---------------------------------------------------------------------------
# Admin endpoint
# ---------------------------------------------------------------------------

class TestAdminEndpoint:
    def test_admin_jobs_requires_key(self, base_url):
        status, _ = api_get("/api/admin/jobs", base_url)
        assert status == HTTPStatus.UNAUTHORIZED

    def test_admin_jobs_with_empty_key(self, base_url):
        status, _ = api_get("/api/admin/jobs?key=", base_url)
        assert status == HTTPStatus.UNAUTHORIZED


# ---------------------------------------------------------------------------
# Static pages
# ---------------------------------------------------------------------------

class TestStaticPages:
    def test_root_returns_html(self, base_url):
        status, body = api_get("/", base_url)
        assert status == HTTPStatus.OK
        assert isinstance(body, str)
        assert "Music Speaks" in body

    def test_root_head_returns_ok(self, base_url):
        status, headers = api_head("/", base_url)
        assert status == HTTPStatus.OK
        assert "Content-Length" in headers

    def test_admin_page_returns_html(self, base_url):
        status, body = api_get("/admin", base_url)
        assert status == HTTPStatus.OK
        assert isinstance(body, str)
        assert "Music Speaks Admin" in body

    def test_favicon_returns_no_content(self, base_url):
        status, body = api_get("/favicon.ico", base_url)
        assert status == HTTPStatus.NO_CONTENT

    def test_unknown_path_returns_404(self, base_url):
        status, body = api_get("/nonexistent/path", base_url)
        assert status == HTTPStatus.NOT_FOUND
