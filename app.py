#!/usr/bin/env python3
"""Music Speaks web app for Render."""

from __future__ import annotations

import datetime as dt
import email.encoders
import email.mime.base
import email.mime.multipart
import email.mime.text
import hashlib
import hmac
import json
import mimetypes
import os
import re
import secrets
import shutil
import smtplib
import subprocess
import threading
import time
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import boto3
from botocore.config import Config as BotoConfig

HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "5050"))
MMX_BIN = shutil.which("mmx") or "/Users/yuantao/.npm-global/bin/mmx"
MMX_PATH_HINTS = [
    "/opt/homebrew/bin",
    str(Path.home() / ".npm-global" / "bin"),
    "/usr/local/bin",
    "/usr/bin",
    "/bin",
    "/usr/sbin",
    "/sbin",
]
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", str(Path.home() / "terry_music_outputs")))
JOBS_DB = OUTPUT_DIR / "jobs.json"
DRAFTS_DB = OUTPUT_DIR / "drafts.json"
PLAYLISTS_DB = OUTPUT_DIR / "playlists.json"
MAX_BODY_BYTES = 1024 * 1024


def legacy_local_config(name: str) -> str:
    legacy_path = Path.home() / "Downloads" / "minimax_music_tool.py"
    if not legacy_path.exists():
        return ""
    try:
        text = legacy_path.read_text(encoding="utf-8")
    except OSError:
        return ""
    match = re.search(rf"^{re.escape(name)}\s*=\s*(['\"])(.*?)\1", text, re.MULTILINE)
    return match.group(2) if match else ""


MINIMAX_API_KEY = (
    os.getenv("MINIMAX_API_KEY")
    or os.getenv("MINIMAX_API_TOKEN")
    or legacy_local_config("MINIMAX_API_KEY")
    or legacy_local_config("MINIMAX_API_TOKEN")
)
MINIMAX_API_TOKEN = MINIMAX_API_KEY
ADMIN_KEY = (
    os.getenv("ADMIN_KEY")
    or legacy_local_config("ADMIN_KEY")
    or (hashlib.sha256(f"terry-admin:{MINIMAX_API_KEY}".encode("utf-8")).hexdigest()[:24] if MINIMAX_API_KEY else "")
)
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "465"))
SMTP_USER = os.getenv("SMTP_USER") or legacy_local_config("SMTP_USER")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD") or legacy_local_config("SMTP_PASSWORD")

# ── Cloudflare R2 Storage ──────────────────────────────────────────────────────
R2_ACCOUNT_ID = os.getenv("R2_ACCOUNT_ID", "")
R2_ACCESS_KEY = os.getenv("R2_ACCESS_KEY", "")
R2_SECRET_KEY = os.getenv("R2_SECRET_KEY", "")
R2_BUCKET = os.getenv("R2_BUCKET", "terry-music")
R2_PUBLIC_URL = os.getenv("R2_PUBLIC_URL", "")  # e.g. https://pub-xxx.r2.dev

def is_r2_configured() -> bool:
    """Check if R2 storage is properly configured."""
    return bool(R2_ACCOUNT_ID and R2_ACCESS_KEY and R2_SECRET_KEY and R2_BUCKET)


def upload_to_r2(file_path: str | Path) -> str | None:
    """
    Upload a file to Cloudflare R2 and return the public URL.
    Returns None if R2 is not configured or upload fails.
    """
    if not is_r2_configured():
        return None
    try:
        path = Path(file_path)
        if not path.exists():
            print(f"[r2] file not found: {file_path}")
            return None
        object_key = f"music/{dt.datetime.now().strftime('%Y/%m/%d')}/{path.name}"
        client = boto3.client(
            "s3",
            endpoint_url=f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
            aws_access_key_id=R2_ACCESS_KEY,
            aws_secret_access_key=R2_SECRET_KEY,
            config=BotoConfig(signature_version="s3v4"),
        )
        client.upload_file(str(path), R2_BUCKET, object_key, ExtraArgs={"ContentType": "audio/mpeg"})
        if R2_PUBLIC_URL:
            public_url = f"{R2_PUBLIC_URL.rstrip('/')}/{object_key}"
        else:
            public_url = f"https://{R2_BUCKET}.{R2_ACCOUNT_ID}.r2.cloudflarestorage.com/{object_key}"
        print(f"[r2] uploaded: {public_url}")
        return public_url
    except Exception as exc:
        print(f"[r2] upload failed: {exc}")
        return None


JOBS: dict[str, dict[str, Any]] = {}
JOBS_LOCK = threading.RLock()
DRAFTS: dict[str, dict[str, Any]] = {}
DRAFTS_LOCK = threading.RLock()
PLAYLISTS: dict[str, dict[str, Any]] = {}
PLAYLISTS_LOCK = threading.RLock()
ADMIN_LOGS: list[dict[str, Any]] = []
ADMIN_LOGS_LOCK = threading.RLock()
FEEDBACK: dict[str, dict[str, Any]] = {}
FEEDBACK_LOCK = threading.RLock()
FEEDBACK_DB = OUTPUT_DIR / "feedback.json"


# ── Rate Limiting ─────────────────────────────────────────────────────────────
_RATE_LIMIT_WINDOW = 60  # seconds
_RATE_LIMIT_MAX = 30     # max requests per window per IP
_RATE_IP_REQUESTS: dict[str, list[float]] = {}
_RATE_IP_LOCK = threading.Lock()

# ── Security Headers ───────────────────────────────────────────────────────────
_CORS_ORIGINS = os.getenv("CORS_ORIGINS", "")  # comma-separated list, empty = no CORS
_ALLOWED_ORIGINS = [o.strip() for o in _CORS_ORIGINS.split(",") if o.strip()] if _CORS_ORIGINS else []
_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "X-XSS-Protection": "1; mode=block",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "microphone=(self), camera=()",
}


def _rate_limit_ip(client_ip: str) -> bool:
    """Return True if the IP is within rate limits, False if blocked."""
    now = time.time()
    with _RATE_IP_LOCK:
        if client_ip not in _RATE_IP_REQUESTS:
            _RATE_IP_REQUESTS[client_ip] = []
        timestamps = _RATE_IP_REQUESTS[client_ip]
        # Remove timestamps outside the window
        cutoff = now - _RATE_LIMIT_WINDOW
        timestamps[:] = [t for t in timestamps if t > cutoff]
        if len(timestamps) >= _RATE_LIMIT_MAX:
            return False
        timestamps.append(now)
        return True


def log_admin_action(action: str, target: str, detail: str = "") -> None:
    """Record an admin operation in the admin log."""
    entry = {
        "timestamp": now_iso(),
        "action": action,
        "target": target,
        "detail": detail,
    }
    with ADMIN_LOGS_LOCK:
        ADMIN_LOGS.append(entry)
        # Keep last 500 entries
        if len(ADMIN_LOGS) > 500:
            ADMIN_LOGS[:] = ADMIN_LOGS[-500:]


def _get_client_ip(handler: "MusicHandler") -> str:
    """Extract client IP from request, checking X-Forwarded-For header."""
    xff = handler.headers.get("X-Forwarded-For", "")
    if xff:
        return xff.split(",")[0].strip()
    return handler.address_string().split(":")[0] if ":" in handler.address_string() else handler.address_string()

INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Music Speaks</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=Space+Grotesk:wght@500;600;700&display=swap" rel="stylesheet">
  <style>
    :root {
      color-scheme: dark;
      --bg-primary: #0a0a0f;
      --bg-secondary: #12121a;
      --bg-tertiary: #1a1a25;
      --bg-elevated: #222230;
      --accent: #1db954;
      --accent-hover: #1ed760;
      --accent-dim: rgba(29, 185, 84, 0.15);
      --text-primary: #ffffff;
      --text-secondary: #b3b3b3;
      --text-muted: #727272;
      --border: #282830;
      --border-light: #3a3a45;
      --danger: #ff5252;
      --warning: #ffab00;
      --gradient-green: linear-gradient(135deg, #1db954, #1ed760);
      --shadow-sm: 0 2px 8px rgba(0,0,0,0.3);
      --shadow-md: 0 4px 16px rgba(0,0,0,0.4);
      --shadow-lg: 0 8px 32px rgba(0,0,0,0.5);
      --radius-sm: 6px;
      --radius-md: 10px;
      --radius-lg: 16px;
      --transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
    }
    [data-theme="light"] {
      color-scheme: light;
      --bg-primary: #f5f5f7;
      --bg-secondary: #ffffff;
      --bg-tertiary: #e8e8ed;
      --bg-elevated: #ffffff;
      --accent: #1db954;
      --accent-hover: #1ed760;
      --accent-dim: rgba(29, 185, 84, 0.12);
      --text-primary: #1d1d1f;
      --text-secondary: #6e6e73;
      --text-muted: #aeaeb2;
      --border: #d2d2d7;
      --border-light: #e5e5ea;
      --danger: #ff3b30;
      --warning: #ff9500;
      --shadow-sm: 0 2px 8px rgba(0,0,0,0.08);
      --shadow-md: 0 4px 16px rgba(0,0,0,0.12);
      --shadow-lg: 0 8px 32px rgba(0,0,0,0.16);
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    html, body { height: 100%; }
    body {
      background: var(--bg-primary);
      color: var(--text-primary);
      font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      font-size: 14px;
      line-height: 1.5;
      overflow: hidden;
    }
    /* App Layout */
    .app { display: flex; flex-direction: column; height: 100vh; }
    .app-header { display: flex; align-items: center; justify-content: space-between; padding: 0 24px; height: 64px; background: var(--bg-secondary); border-bottom: 1px solid var(--border); flex-shrink: 0; }
    .logo { display: flex; align-items: center; gap: 10px; font-family: 'Space Grotesk', sans-serif; font-size: 20px; font-weight: 700; color: var(--text-primary); text-decoration: none; }
    .logo-icon { width: 36px; height: 36px; background: var(--gradient-green); border-radius: 10px; display: flex; align-items: center; justify-content: center; font-size: 18px; animation: glow 3s ease-in-out infinite; }
    .header-actions { display: flex; gap: 8px; align-items: center; }
    .header-btn { display: flex; align-items: center; justify-content: center; width: 40px; height: 40px; border: none; border-radius: 50%; background: var(--bg-tertiary); color: var(--text-secondary); cursor: pointer; font-size: 18px; transition: var(--transition); }
    .header-btn:hover { background: var(--bg-elevated); color: var(--text-primary); transform: scale(1.05); }
    .lang-toggle { width: auto; padding: 0 14px; border-radius: 20px; font-size: 13px; font-weight: 600; }
    /* Theme Dropdown */
    .theme-wrapper { position: relative; }
    .theme-btn { position: relative; }
    .theme-menu { display: none; position: absolute; top: calc(100% + 8px); right: 0; background: var(--bg-elevated); border: 1px solid var(--border-light); border-radius: var(--radius-md); box-shadow: var(--shadow-lg); min-width: 160px; z-index: 1000; overflow: hidden; }
    .theme-menu.open { display: block; }
    .theme-menu-item { display: flex; align-items: center; gap: 10px; padding: 10px 14px; cursor: pointer; font-size: 13px; font-weight: 500; color: var(--text-secondary); transition: var(--transition); }
    .theme-menu-item:hover { background: var(--bg-tertiary); color: var(--text-primary); }
    .theme-menu-item.active { color: var(--accent); background: var(--accent-dim); }
    .theme-menu-item-icon { font-size: 16px; width: 20px; text-align: center; }
    /* Main Layout */
    .app-body { display: flex; flex: 1; overflow: hidden; }
    /* Sidebar */
    .sidebar { width: 280px; background: var(--bg-secondary); border-right: 1px solid var(--border); display: flex; flex-direction: column; flex-shrink: 0; }
    .sidebar-nav { padding: 16px 12px; }
    .nav-item { display: flex; align-items: center; gap: 12px; padding: 12px 16px; border-radius: var(--radius-md); color: var(--text-secondary); text-decoration: none; font-weight: 500; cursor: pointer; transition: var(--transition); }
    .nav-item:hover { background: var(--bg-tertiary); color: var(--text-primary); }
    .nav-item.active { background: var(--accent-dim); color: var(--accent); }
    .nav-icon { font-size: 20px; width: 24px; text-align: center; }
    .sidebar-section { padding: 8px 12px; }
    .sidebar-section-title { padding: 8px 16px; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px; color: var(--text-muted); }
    .playlist-item { display: flex; align-items: center; gap: 10px; padding: 8px 16px; border-radius: var(--radius-sm); color: var(--text-secondary); cursor: pointer; transition: var(--transition); }
    .playlist-item:hover { color: var(--text-primary); background: var(--bg-tertiary); }
    .playlist-item:hover { color: var(--text-primary); }
    /* Main Content */
    .main-content { flex: 1; overflow-y: auto; padding: 32px 40px 120px; background: var(--bg-primary); }
    .page-header { margin-bottom: 32px; }
    .page-title { font-size: 32px; font-weight: 800; color: var(--text-primary); margin-bottom: 8px; }
    .page-desc { color: var(--text-secondary); font-size: 14px; }
    /* Create Form */
    .create-form { background: var(--bg-secondary); border: 1px solid var(--border); border-radius: var(--radius-lg); padding: 28px; max-width: 900px; }
    .form-section { margin-bottom: 24px; }
    .form-section:last-child { margin-bottom: 0; }
    .form-label { display: block; font-size: 13px; font-weight: 700; color: var(--text-primary); margin-bottom: 8px; }
    .form-hint { font-size: 12px; color: var(--text-muted); margin-top: 6px; }
    /* Character counter */
    .char-counter { display: flex; justify-content: flex-end; align-items: center; gap: 6px; margin-top: 6px; font-size: 11px; color: var(--text-muted); transition: color 0.2s; }
    .char-counter.warning { color: var(--warning); }
    .char-counter.danger { color: var(--danger); font-weight: 600; }
    .char-counter .counter-bar { width: 40px; height: 3px; background: var(--border); border-radius: 2px; overflow: hidden; }
    .char-counter .counter-fill { height: 100%; background: var(--accent); transition: width 0.2s, background 0.2s; }
    .char-counter.warning .counter-fill { background: var(--warning); }
    .char-counter.danger .counter-fill { background: var(--danger); }
    /* Input with counter */
    .input-with-counter { position: relative; }
    .input-with-counter .form-input { padding-bottom: 24px; }
    .input-with-counter .char-counter { position: absolute; bottom: 8px; right: 12px; }
    .form-input { width: 100%; padding: 12px 16px; background: var(--bg-tertiary); border: 1px solid var(--border); border-radius: var(--radius-md); color: var(--text-primary); font-size: 14px; transition: var(--transition); }
    .form-input:focus { outline: none; border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-dim); }
    .form-input::placeholder { color: var(--text-muted); }
    textarea.form-input { min-height: 120px; resize: vertical; line-height: 1.6; }
    /* Template Grid */
    .template-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin-top: 12px; }
    .template-btn { padding: 14px 12px; background: var(--bg-tertiary); border: 1px solid var(--border); border-radius: var(--radius-md); color: var(--text-secondary); font-size: 13px; font-weight: 500; cursor: pointer; transition: var(--transition); text-align: center; }
    .template-btn:hover { border-color: var(--accent); background: var(--accent-dim); color: var(--accent); transform: translateY(-1px); }
    .template-btn.active { border-color: var(--accent); background: var(--accent-dim); color: var(--accent); }
    /* Checkboxes */
    .checkbox-grid { display: flex; gap: 12px; flex-wrap: wrap; }
    .checkbox-item { display: flex; align-items: center; gap: 8px; padding: 10px 16px; background: var(--bg-tertiary); border: 1px solid var(--border); border-radius: var(--radius-md); cursor: pointer; transition: var(--transition); }
    .checkbox-item:hover { border-color: var(--border-light); }
    .checkbox-item input { width: 18px; height: 18px; accent-color: var(--accent); }
    .checkbox-item span { font-size: 13px; font-weight: 500; color: var(--text-primary); }
    .checkbox-item small { display: block; font-size: 11px; color: var(--text-muted); }
    /* Voice Clone */
    .voice-section { background: var(--bg-tertiary); border: 1px solid var(--border); border-radius: var(--radius-md); padding: 16px; }
    .voice-top-row { display: flex; align-items: center; gap: 12px; margin-bottom: 12px; }
    .voice-status { font-size: 13px; color: var(--text-secondary); }
    .voice-status.success { color: var(--accent); }
    .voice-status.error { color: var(--danger); }
    /* Advanced Parameters */
    .advanced-toggle { display: flex; align-items: center; justify-content: space-between; padding: 14px 16px; background: var(--bg-tertiary); border: 1px solid var(--border); border-radius: var(--radius-md); cursor: pointer; margin-top: 16px; }
    .advanced-toggle:hover { border-color: var(--border-light); }
    .advanced-toggle span { font-size: 13px; font-weight: 600; color: var(--text-primary); }
    .advanced-toggle-icon { color: var(--text-muted); transition: transform 0.2s; }
    .advanced-panel { margin-top: 12px; padding: 20px; background: var(--bg-tertiary); border: 1px solid var(--border); border-radius: var(--radius-md); display: none; }
    .advanced-panel.open { display: block; }
    .param-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; }
    .param-field { display: flex; flex-direction: column; gap: 6px; cursor: grab; transition: transform 0.2s, opacity 0.2s; }
    .param-field:active { cursor: grabbing; }
    .param-field.dragging { opacity: 0.5; transform: scale(0.98); }
    .param-field.drag-over { border: 2px dashed var(--accent); border-radius: var(--radius-sm); background: var(--accent-dim); }
    .param-field .drag-handle { display: none; align-items: center; justify-content: center; width: 20px; height: 20px; color: var(--text-muted); font-size: 12px; opacity: 0; transition: opacity 0.2s; }
    .param-field:hover .drag-handle { opacity: 1; }
    .param-field label { font-size: 12px; font-weight: 600; color: var(--text-secondary); display: flex; align-items: center; gap: 6px; }
    .param-field input { padding: 10px 12px; background: var(--bg-secondary); border: 1px solid var(--border); border-radius: var(--radius-sm); color: var(--text-primary); font-size: 13px; }
    .param-field input:focus { outline: none; border-color: var(--accent); }
    .param-grid-sortable .param-field { position: relative; padding: 8px; border: 1px solid transparent; border-radius: var(--radius-sm); }
    .param-grid-sortable .param-field:hover { border-color: var(--border); background: var(--bg-secondary); }
    /* Reference Audio */
    .ref-audio-section { background: var(--bg-secondary); border: 1px solid var(--border); border-radius: var(--radius-md); padding: 16px; margin-bottom: 16px; }
    .ref-audio-section h4 { font-size: 13px; font-weight: 600; color: var(--text-primary); margin: 0 0 12px 0; }
    .ref-audio-dropzone { border: 2px dashed var(--border); border-radius: var(--radius-sm); padding: 24px; text-align: center; cursor: pointer; transition: var(--transition); }
    .ref-audio-dropzone:hover, .ref-audio-dropzone.dragover { border-color: var(--accent); background: rgba(29,185,84,0.05); }
    .ref-audio-dropzone p { margin: 0; font-size: 13px; color: var(--text-muted); }
    .ref-audio-dropzone .icon { font-size: 28px; margin-bottom: 8px; }
    .ref-audio-info { display: flex; align-items: center; gap: 12px; margin-top: 12px; }
    .ref-audio-info audio { flex: 1; height: 36px; }
    .ref-audio-mode { display: flex; gap: 12px; margin-top: 12px; flex-wrap: wrap; }
    .ref-audio-mode label { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--text-secondary); cursor: pointer; padding: 8px 12px; background: var(--bg-tertiary); border: 1px solid var(--border); border-radius: var(--radius-sm); transition: var(--transition); }
    .ref-audio-mode label:hover { border-color: var(--border-light); }
    .ref-audio-mode label input { accent-color: var(--accent); }
    .ref-audio-mode label input:checked ~ span { color: var(--accent); font-weight: 600; }
    .ref-audio-mode label input:checked + span { color: var(--accent); }
    /* Actions */
    .form-actions { display: flex; gap: 12px; margin-top: 24px; flex-wrap: wrap; }
    .btn-primary { flex: 1; padding: 14px 24px; background: var(--accent); border: none; border-radius: var(--radius-md); color: #000; font-size: 14px; font-weight: 700; cursor: pointer; transition: var(--transition); display: flex; align-items: center; justify-content: center; gap: 8px; }
    .btn-primary:hover { background: var(--accent-hover); transform: translateY(-1px); }
    .btn-primary:disabled { opacity: 0.6; cursor: not-allowed; transform: none; }
    .btn-secondary { padding: 14px 20px; background: var(--bg-tertiary); border: 1px solid var(--border); border-radius: var(--radius-md); color: var(--text-primary); font-size: 13px; font-weight: 600; cursor: pointer; transition: var(--transition); }
    .btn-secondary:hover { background: var(--bg-elevated); border-color: var(--border-light); }
    .btn-voice { padding: 12px 16px; background: var(--bg-elevated); border: 1px solid var(--border); border-radius: var(--radius-md); color: var(--text-primary); font-size: 13px; font-weight: 600; cursor: pointer; transition: var(--transition); display: flex; align-items: center; gap: 8px; }
    .btn-voice:hover { border-color: var(--accent); color: var(--accent); }
    .error-text { color: var(--danger); font-size: 13px; min-height: 20px; margin-top: 12px; }
    .error-alert { display: flex; align-items: flex-start; gap: 10px; padding: 14px 16px; background: rgba(255,82,82,0.12); border: 1px solid var(--danger); border-radius: var(--radius-md); margin-top: 12px; animation: slide-in-right 0.3s ease-out; }
    .error-alert-icon { font-size: 18px; flex-shrink: 0; }
    .error-alert-content { flex: 1; }
    .error-alert-message { color: var(--danger); font-size: 13px; font-weight: 500; line-height: 1.4; }
    .error-alert-close { background: none; border: none; color: var(--danger); cursor: pointer; font-size: 16px; padding: 0; opacity: 0.7; }
    .error-alert-close:hover { opacity: 1; }
    /* Jobs Panel */
    .jobs-panel { background: var(--bg-secondary); border: 1px solid var(--border); border-radius: var(--radius-lg); padding: 20px; margin-top: 24px; max-width: 900px; }
    .jobs-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
    .jobs-title { font-size: 16px; font-weight: 700; color: var(--text-primary); }
    .jobs-list { display: flex; flex-direction: column; gap: 10px; max-height: 400px; overflow-y: auto; }
    .job-card { display: flex; align-items: center; gap: 14px; padding: 14px 16px; background: var(--bg-tertiary); border: 1px solid var(--border); border-radius: var(--radius-md); transition: var(--transition); cursor: pointer; }
    .job-card:hover { border-color: var(--accent); transform: translateY(-2px); box-shadow: var(--shadow-md); }
    .job-art { width: 56px; height: 56px; background: var(--gradient-green); border-radius: var(--radius-sm); display: flex; align-items: center; justify-content: center; font-size: 24px; flex-shrink: 0; transition: var(--transition); }
    .job-card:hover .job-art { transform: scale(1.05); }
    .job-info { flex: 1; min-width: 0; }
    .job-title { font-size: 14px; font-weight: 600; color: var(--text-primary); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; margin-bottom: 4px; }
    .job-meta { display: flex; gap: 8px; font-size: 12px; color: var(--text-muted); }
    .job-badge { padding: 3px 8px; border-radius: 999px; font-size: 11px; font-weight: 700; text-transform: uppercase; }
    .job-badge.queued { background: rgba(255, 171, 0, 0.15); color: var(--warning); }
    .job-badge.running { background: rgba(255, 171, 0, 0.15); color: var(--warning); }
    .job-badge.completed { background: var(--accent-dim); color: var(--accent); }
    .job-badge.error { background: rgba(255, 82, 82, 0.15); color: var(--danger); }
    .job-actions { display: flex; gap: 8px; }
    .job-action-btn { padding: 8px 12px; background: var(--bg-elevated); border: 1px solid var(--border); border-radius: var(--radius-sm); color: var(--text-secondary); font-size: 12px; font-weight: 600; cursor: pointer; transition: var(--transition); }
    .job-action-btn:hover { border-color: var(--accent); color: var(--accent); }
    .job-action-btn.download { background: var(--accent); color: #000; border: none; }
    .job-action-btn.download:hover { background: var(--accent-hover); }
    .job-empty { text-align: center; padding: 40px 20px; color: var(--text-muted); }
    .job-progress { display: flex; align-items: center; gap: 10px; }
    .progress-bar { flex: 1; height: 4px; background: var(--border); border-radius: 2px; overflow: hidden; }
    .progress-fill { height: 100%; background: var(--accent); transition: width 0.3s; }
    /* Jobs Toolbar */
    .jobs-toolbar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 12px; }
    .jobs-toolbar .search-wrap { position: relative; flex: 1; min-width: 160px; }
    .jobs-toolbar .search-wrap .search-icon { position: absolute; left: 10px; top: 50%; transform: translateY(-50%); color: var(--text-muted); font-size: 13px; pointer-events: none; }
    .jobs-toolbar input[type="text"], .jobs-toolbar select { padding: 7px 10px; background: var(--bg-elevated); border: 1px solid var(--border); border-radius: var(--radius-sm); color: var(--text-primary); font-size: 12px; outline: none; transition: var(--transition); }
    .jobs-toolbar input[type="text"]:focus, .jobs-toolbar select:focus { border-color: var(--accent); }
    .jobs-toolbar input[type="text"] { padding-left: 30px; width: 100%; }
    .jobs-toolbar select { cursor: pointer; min-width: 90px; }
    .jobs-toolbar .date-input { padding: 7px 10px; background: var(--bg-elevated); border: 1px solid var(--border); border-radius: var(--radius-sm); color: var(--text-primary); font-size: 12px; outline: none; transition: var(--transition); }
    .jobs-toolbar .date-input:focus { border-color: var(--accent); }
    .jobs-toolbar .clear-btn { padding: 7px 12px; background: transparent; border: 1px solid var(--border); border-radius: var(--radius-sm); color: var(--text-muted); font-size: 12px; cursor: pointer; transition: var(--transition); }
    .jobs-toolbar .clear-btn:hover { border-color: var(--danger); color: var(--danger); }
    /* Batch actions bar */
    .batch-bar { display: none; align-items: center; gap: 10px; padding: 10px 14px; background: var(--accent-dim); border: 1px solid var(--accent); border-radius: var(--radius-md); margin-bottom: 12px; font-size: 13px; color: var(--accent); }
    .batch-bar.active { display: flex; }
    .batch-bar span { flex: 1; }
    .batch-btn { padding: 6px 14px; border-radius: var(--radius-sm); font-size: 12px; font-weight: 600; cursor: pointer; transition: var(--transition); border: none; }
    .batch-btn.delete { background: var(--danger); color: #fff; }
    .batch-btn.delete:hover { background: #e04444; }
    .batch-btn.download { background: var(--accent); color: #000; }
    .batch-btn.download:hover { background: var(--accent-hover); }
    .batch-btn.cancel { background: transparent; border: 1px solid var(--border); color: var(--text-secondary); }
    .batch-btn.cancel:hover { border-color: var(--text-secondary); }
    /* Job extra info */
    .job-extra { display: flex; gap: 8px; font-size: 11px; color: var(--text-muted); flex-wrap: wrap; }
    .job-extra span { background: var(--bg-elevated); padding: 2px 6px; border-radius: 4px; }
    .job-fav-btn { background: none; border: none; cursor: pointer; font-size: 16px; padding: 4px; color: var(--text-muted); transition: var(--transition); }
    .job-fav-btn:hover, .job-fav-btn.active { color: #f59e0b; }
    /* Pagination */
    .jobs-pagination { display: flex; align-items: center; justify-content: center; gap: 8px; margin-top: 12px; }
    .jobs-pagination button { padding: 6px 12px; background: var(--bg-elevated); border: 1px solid var(--border); border-radius: var(--radius-sm); color: var(--text-secondary); font-size: 12px; cursor: pointer; transition: var(--transition); }
    .jobs-pagination button:hover:not(:disabled) { border-color: var(--accent); color: var(--accent); }
    .jobs-pagination button:disabled { opacity: 0.4; cursor: not-allowed; }
    .jobs-pagination .page-info { font-size: 12px; color: var(--text-muted); }
    /* Bottom Player */
    .player { position: fixed; bottom: 0; left: 0; right: 0; min-height: 90px; background: var(--bg-secondary); border-top: 1px solid var(--border); display: flex; align-items: center; padding: 0 24px; gap: 20px; z-index: 100; transition: min-height 0.3s ease; }
    .player.expanded { min-height: 240px; flex-wrap: wrap; padding-bottom: 24px; }
    .player-track { display: flex; align-items: center; gap: 14px; width: 280px; flex-shrink: 0; }
    .player-art { width: 56px; height: 56px; background: var(--gradient-green); border-radius: var(--radius-sm); display: flex; align-items: center; justify-content: center; font-size: 24px; transition: transform 0.3s; }
    .player-art.playing { animation: spin 8s linear infinite; }
    .player-info { min-width: 0; }
    .player-title { font-size: 14px; font-weight: 600; color: var(--text-primary); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .player-artist { font-size: 12px; color: var(--text-muted); }
    .player-controls { flex: 1; display: flex; flex-direction: column; align-items: center; gap: 8px; }
    .player-buttons { display: flex; align-items: center; gap: 16px; }
    .player-btn { background: none; border: none; color: var(--text-secondary); cursor: pointer; font-size: 20px; padding: 8px; transition: var(--transition); }
    .player-btn:hover { color: var(--text-primary); }
    .player-btn.active { color: var(--accent); }
    .player-btn.play { width: 40px; height: 40px; background: var(--text-primary); color: var(--bg-primary); border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 18px; }
    .player-btn.play:hover { transform: scale(1.05); }
    .player-progress { display: flex; align-items: center; gap: 10px; width: 100%; max-width: 600px; }
    .player-time { font-size: 11px; color: var(--text-muted); min-width: 40px; text-align: center; }
    .player-bar { flex: 1; height: 4px; background: var(--border); border-radius: 2px; cursor: pointer; position: relative; }
    .player-bar-fill { height: 100%; background: var(--accent); border-radius: 2px; width: 0%; transition: width 0.1s; }
    .player-bar:hover .player-bar-fill { background: var(--accent-hover); }
    .player-bar-thumb { position: absolute; top: 50%; left: 0%; transform: translate(-50%, -50%) scale(0); width: 12px; height: 12px; background: var(--accent); border-radius: 50%; transition: transform 0.2s; }
    .player-bar:hover .player-bar-thumb { transform: translate(-50%, -50%) scale(1); }
    .player-volume { display: flex; align-items: center; gap: 8px; width: 140px; flex-shrink: 0; }
    .volume-icon { color: var(--text-muted); font-size: 18px; cursor: pointer; transition: color 0.2s; }
    .volume-icon:hover { color: var(--text-primary); }
    .volume-icon.muted { color: var(--danger); }
    .volume-slider { flex: 1; height: 4px; background: var(--border); border-radius: 2px; cursor: pointer; position: relative; }
    .volume-fill { height: 100%; background: var(--text-muted); border-radius: 2px; width: 70%; transition: background 0.2s; }
    .volume-slider:hover .volume-fill { background: var(--accent); }
    .player-lyrics { flex: 1; max-width: 500px; overflow: hidden; text-align: center; padding: 0 20px; }
    .lyrics-text { font-size: 14px; color: var(--text-secondary); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; transition: color 0.3s; }
    .lyrics-text.playing { color: var(--accent); }
    .player-mode-btn { font-size: 16px; }
    .player-expand { display: none; position: absolute; top: -20px; left: 50%; transform: translateX(-50%); background: var(--bg-secondary); border: 1px solid var(--border); border-bottom: none; border-radius: 8px 8px 0 0; padding: 4px 12px; cursor: pointer; font-size: 12px; color: var(--text-muted); }
    .player:hover .player-expand { display: flex; align-items: center; gap: 4px; }
    .player-expand:hover { color: var(--text-primary); }
    .player-waveform { display: none; width: 100%; height: 60px; margin-top: 12px; background: var(--bg-tertiary); border-radius: var(--radius-sm); overflow: hidden; }
    .player.expanded .player-waveform { display: block; }
    .waveform-canvas { width: 100%; height: 100%; }
    .player-extras { display: none; width: 100%; gap: 16px; justify-content: center; margin-top: 12px; }
    .player.expanded .player-extras { display: flex; }
    .player-action-btn { background: var(--bg-tertiary); border: 1px solid var(--border); border-radius: var(--radius-sm); padding: 8px 16px; font-size: 12px; color: var(--text-secondary); cursor: pointer; transition: var(--transition); display: flex; align-items: center; gap: 6px; }
    .player-action-btn:hover { background: var(--bg-elevated); color: var(--text-primary); border-color: var(--accent); }
    .player-action-btn.active { color: var(--accent); border-color: var(--accent); }
    /* Recording Modal */
    .modal-overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.8); z-index: 1000; display: flex; align-items: center; justify-content: center; }
    .modal-content { background: var(--bg-secondary); border: 1px solid var(--border); border-radius: var(--radius-lg); width: min(520px, 95vw); max-height: 90vh; overflow-y: auto; }
    .modal-header { display: flex; justify-content: space-between; align-items: center; padding: 20px 24px; border-bottom: 1px solid var(--border); }
    .modal-title { font-size: 18px; font-weight: 700; color: var(--text-primary); }
    .modal-close { width: 32px; height: 32px; background: var(--bg-tertiary); border: none; border-radius: 50%; color: var(--text-muted); cursor: pointer; font-size: 16px; }
    .modal-close:hover { background: var(--bg-elevated); color: var(--text-primary); }
    .modal-body { padding: 24px; }
    .rec-progress { margin-bottom: 20px; }
    .rec-step { font-size: 14px; font-weight: 700; color: var(--accent); margin-bottom: 10px; }
    .rec-bar { height: 4px; background: var(--border); border-radius: 2px; overflow: hidden; }
    .rec-bar-fill { height: 100%; background: var(--accent); transition: width 0.4s ease; }
    .rec-script { background: var(--bg-tertiary); border: 1px solid var(--border); border-radius: var(--radius-md); padding: 20px; margin: 16px 0; text-align: center; }
    .rec-script-text { font-size: 18px; line-height: 1.6; color: var(--text-primary); }
    .rec-countdown { font-size: 48px; font-weight: 800; color: var(--accent); text-align: center; margin: 20px 0; }
    .rec-instruction { font-size: 13px; color: var(--text-muted); text-align: center; margin-bottom: 16px; }
    .rec-controls { display: flex; gap: 12px; justify-content: center; }
    .rec-btn { padding: 12px 24px; border: none; border-radius: var(--radius-md); font-size: 14px; font-weight: 600; cursor: pointer; transition: var(--transition); }
    .rec-btn-record { background: var(--danger); color: #fff; }
    .rec-btn-record:hover { opacity: 0.9; }
    .rec-btn-stop { background: var(--bg-tertiary); color: var(--text-primary); border: 1px solid var(--border); }
    .rec-btn-next { background: var(--accent); color: #000; }
    .rec-done { text-align: center; padding: 30px; color: var(--accent); font-size: 16px; }
    /* Responsive */
    @media (max-width: 1024px) {
      .sidebar { width: 220px; }
      .template-grid { grid-template-columns: repeat(2, 1fr); }
      .param-grid { grid-template-columns: repeat(2, 1fr); }
    }
    @media (max-width: 768px) {
      .sidebar { display: none; }
      .main-content { padding: 16px 12px 100px; }
      .page-title { font-size: 24px; }
      .page-desc { font-size: 13px; }
      .create-form { padding: 16px; }
      .template-grid { grid-template-columns: repeat(2, 1fr); gap: 8px; }
      .template-btn { padding: 10px 8px; font-size: 12px; }
      .checkbox-grid { flex-direction: column; }
      .checkbox-item { width: 100%; }
      .player { padding: 0 12px; gap: 8px; }
      .player-track { width: auto; }
      .player-volume { display: none; }
      .player-lyrics { display: none; }
      .player-controls { gap: 4px; }
      .player-buttons { gap: 8px; }
      .player-btn { font-size: 16px; padding: 4px; }
      .form-actions { flex-direction: column; }
      .btn-primary, .btn-secondary { width: 100%; }
      .param-grid { grid-template-columns: 1fr; }
      .advanced-panel { padding: 12px; }
      .error-alert { padding: 10px 12px; }
      .app-header { padding: 0 12px; height: 56px; }
      .logo { font-size: 16px; }
      .logo-icon { width: 30px; height: 30px; font-size: 14px; }
      .header-btn { width: 36px; height: 36px; font-size: 16px; }
      .nav-item { padding: 10px 12px; }
      .job-card { padding: 10px 12px; }
      .job-actions { gap: 6px; }
      .job-action-btn { padding: 6px 10px; font-size: 11px; }
      #toast-container { top: 70px; right: 10px; left: 10px; }
      #toast-container > div { max-width: none; width: 100%; }
    }
    @media (max-width: 480px) {
      .template-grid { grid-template-columns: 1fr 1fr; }
      .ref-audio-mode { flex-direction: column; }
      .ref-audio-mode label { width: 100%; }
    }
    /* Scrollbar */
    ::-webkit-scrollbar { width: 8px; }
    ::-webkit-scrollbar-track { background: transparent; }
    ::-webkit-scrollbar-thumb { background: var(--border); border-radius: 4px; }
    ::-webkit-scrollbar-thumb:hover { background: var(--border-light); }

    /* Animations */
    @keyframes spin { to { transform: rotate(360deg); } }
    @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.5; } }
    @keyframes bounce-in { 0% { transform: scale(0.8); opacity: 0; } 50% { transform: scale(1.05); } 100% { transform: scale(1); opacity: 1; } }
    @keyframes shake { 0%, 100% { transform: translateX(0); } 25% { transform: translateX(-4px); } 75% { transform: translateX(4px); } }
    @keyframes fade-in { from { opacity: 0; } to { opacity: 1; } }
    @keyframes slide-up { from { transform: translateY(20px); opacity: 0; } to { transform: translateY(0); opacity: 1; } }
    @keyframes slide-down { from { transform: translateY(-20px); opacity: 0; } to { transform: translateY(0); opacity: 1; } }
    @keyframes glow { 0%, 100% { box-shadow: 0 0 5px var(--accent); } 50% { box-shadow: 0 0 20px var(--accent), 0 0 30px var(--accent-dim); } }
    @keyframes ripple { to { transform: scale(4); opacity: 0; } }
    @keyframes beat { 0% { transform: scale(1); } 15% { transform: scale(1.15); } 30% { transform: scale(1); } 45% { transform: scale(1.1); } 60% { transform: scale(1); } }
    @keyframes eq1 { 0%, 100% { height: 4px; } 50% { height: 16px; } }
    @keyframes eq2 { 0%, 100% { height: 8px; } 50% { height: 20px; } }
    @keyframes eq3 { 0%, 100% { height: 12px; } 50% { height: 24px; } }
    @keyframes eq4 { 0%, 100% { height: 6px; } 50% { height: 18px; } }
    @keyframes loading-dots { 0%, 20% { opacity: 0; } 40%, 100% { opacity: 1; } }
    @keyframes morph { 0% { border-radius: 50%; transform: scale(0.8); } 50% { border-radius: 40%; transform: scale(1.1); } 100% { border-radius: 50%; transform: scale(0.8); } }
    @keyframes gradient-shift { 0% { background-position: 0% 50%; } 50% { background-position: 100% 50%; } 100% { background-position: 0% 50%; } }
    @keyframes float { 0%, 100% { transform: translateY(0); } 50% { transform: translateY(-6px); } }
    @keyframes slide-in-right { from { transform: translateX(20px); opacity: 0; } to { transform: translateX(0); opacity: 1; } }

    .animate-spin { animation: spin 1s linear infinite; }
    .animate-pulse { animation: pulse 1.5s ease-in-out infinite; }
    .animate-bounce-in { animation: bounce-in 0.5s ease-out forwards; }
    .animate-shake { animation: shake 0.4s ease-in-out; }
    .animate-fade-in { animation: fade-in 0.3s ease-out forwards; }
    .animate-slide-up { animation: slide-up 0.4s ease-out forwards; }
    .animate-slide-down { animation: slide-down 0.4s ease-out forwards; }
    .animate-glow { animation: glow 2s ease-in-out infinite; }
    .animate-beat { animation: beat 1s ease-in-out; }
    .animate-morph { animation: morph 1.2s ease-in-out infinite; }
    .animate-float { animation: float 2s ease-in-out infinite; }

    /* Enhanced Loading spinners */
    .spinner {
      width: 18px;
      height: 18px;
      border: 2px solid rgba(0,0,0,0.2);
      border-top-color: currentColor;
      border-radius: 50%;
      animation: spin 0.8s linear infinite;
      display: inline-block;
      vertical-align: middle;
    }
    .spinner-white {
      border-color: rgba(255,255,255,0.2);
      border-top-color: #fff;
    }
    /* Music Equalizer Loading */
    .eq-loader { display: inline-flex; align-items: center; gap: 3px; height: 20px; }
    .eq-loader span { width: 4px; background: var(--accent); border-radius: 2px; animation: eq1 0.8s ease-in-out infinite; }
    .eq-loader span:nth-child(1) { animation-name: eq1; }
    .eq-loader span:nth-child(2) { animation-name: eq2; animation-delay: 0.1s; }
    .eq-loader span:nth-child(3) { animation-name: eq3; animation-delay: 0.2s; }
    .eq-loader span:nth-child(4) { animation-name: eq4; animation-delay: 0.3s; }
    .eq-loader span:nth-child(5) { animation-name: eq2; animation-delay: 0.15s; }
    /* Pulsing dots loading */
    .loading-dots { display: inline-flex; gap: 4px; align-items: center; }
    .loading-dots span { width: 6px; height: 6px; background: var(--accent); border-radius: 50%; animation: loading-dots 1.4s ease-in-out infinite; }
    .loading-dots span:nth-child(1) { animation-delay: 0s; }
    .loading-dots span:nth-child(2) { animation-delay: 0.2s; }
    .loading-dots span:nth-child(3) { animation-delay: 0.4s; }
    /* Morphing loader */
    .morph-loader { width: 24px; height: 24px; background: var(--accent); animation: morph 1.2s ease-in-out infinite; display: inline-block; }
    /* Gradient animated loader */
    .gradient-loader { width: 24px; height: 24px; background: linear-gradient(135deg, var(--accent), var(--accent-hover), var(--accent)); background-size: 200% 200%; animation: gradient-shift 1.5s ease infinite; border-radius: 6px; display: inline-block; }

    /* Sound toggle */
    .sound-toggle { position: relative; }
    .sound-toggle.on .sound-icon { opacity: 1; }
    .sound-toggle.off .sound-icon { opacity: 0.4; }
  </style>
</head>
<body>
  <div class="app">
    <header class="app-header">
      <button class="mobile-nav-toggle" id="mobileNavToggle" aria-label="Toggle navigation">☰</button>
      <a href="/" class="logo">
        <div class="logo-icon">🎵</div>
        <span>Music Speaks</span>
      </a>
      <div class="header-actions">
        <button id="soundBtn" class="header-btn sound-toggle on" title="Toggle sound" onclick="toggleSound()">🔊</button>
        <button id="notifyBtn" class="header-btn notify-toggle off" title="Toggle notifications" onclick="toggleNotifications()">🔕</button>
        <div class="theme-wrapper">
          <button id="themeBtn" class="header-btn theme-btn" title="Toggle theme">🌙</button>
          <div id="themeMenu" class="theme-menu">
            <div class="theme-menu-item" data-theme-value="">
              <span class="theme-menu-item-icon">💻</span>
              <span data-i18n="themeSystem">System</span>
            </div>
            <div class="theme-menu-item" data-theme-value="light">
              <span class="theme-menu-item-icon">☀️</span>
              <span data-i18n="themeLight">Light Mode</span>
            </div>
            <div class="theme-menu-item" data-theme-value="dark">
              <span class="theme-menu-item-icon">🌙</span>
              <span data-i18n="themeDark">Dark Mode</span>
            </div>
          </div>
        </div>
        <button id="langBtn" class="header-btn lang-toggle">中文</button>
      </div>
    </header>
    <div class="mobile-nav-overlay" id="mobileNavOverlay"></div>
    <div class="app-body">
      <aside class="sidebar">
        <nav class="sidebar-nav">
          <a class="nav-item active" data-view="create">
            <span class="nav-icon">✨</span>
            <span data-i18n="navCreate">Create</span>
          </a>
          <a class="nav-item" data-view="library">
            <span class="nav-icon">📚</span>
            <span data-i18n="navLibrary">Library</span>
          </a>
          <a class="nav-item" data-view="favorites">
            <span class="nav-icon">❤️</span>
            <span data-i18n="navFavorites">Favorites</span>
          </a>
          <a class="nav-item" data-view="history">
            <span class="nav-icon">🕐</span>
            <span data-i18n="navHistory">History</span>
          </a>
        </nav>
        <div class="sidebar-section">
          <div class="sidebar-section-title" data-i18n="navPlaylists">Playlists</div>
          <div class="playlist-item"><span>🎧</span><span data-i18n="playlistAll">All Songs</span></div>
          <div class="playlist-item"><span>🔥</span><span data-i18n="playlistRecent">Recently Played</span></div>
        </div>
      </aside>
      <main class="main-content">
        <!-- Create View -->
        <div id="view-create">
          <div class="page-header">
            <h1 class="page-title" data-i18n="createTitle">Create Music</h1>
            <p class="page-desc" data-i18n="createDesc">Write a feeling, story, lyric, or style. Music Speaks turns it into a downloadable song.</p>
          </div>
          <form id="jobForm" class="create-form">
            <!-- Email -->
            <div class="form-section">
              <label class="form-label" data-i18n="emailLabel">Email Address (optional)</label>
              <input id="email" type="email" class="form-input" data-i18n-placeholder="emailPlaceholder" placeholder="your@email.com">
              <div class="form-hint" data-i18n="emailHint">Optional. Download button is the main way to get your MP3.</div>
            </div>
            <!-- Song Title -->
            <div class="form-section">
              <label class="form-label" data-i18n="titleLabel">Song Title (optional)</label>
              <input id="songTitle" type="text" maxlength="120" class="form-input" data-i18n-placeholder="titlePlaceholder" placeholder="Leave empty and AI will name the song">
            </div>
            <!-- Prompt -->
            <div class="form-section">
              <label class="form-label" data-i18n="promptLabel">Music Style Prompt</label>
              <input id="prompt" type="text" maxlength="2000" required class="form-input" data-i18n-placeholder="promptPlaceholder" placeholder="Cinematic electronic pop, confident and bright, polished production, strong hook">
              <div class="form-hint" data-i18n="promptHint">Include style, mood, instruments, tempo, and any references.</div>
            </div>
            <!-- Templates -->
            <div class="form-section">
              <label class="form-label" data-i18n="templates">Prompt Templates</label>
              <div class="template-grid">
                <button class="template-btn" type="button" data-template="upbeat_pop" data-i18n="templateUpbeatPop">🎵 Upbeat Pop</button>
                <button class="template-btn" type="button" data-template="chill_ambient" data-i18n="templateChillAmbient">🌙 Chill Ambient</button>
                <button class="template-btn" type="button" data-template="rock_anthem" data-i18n="templateRockAnthem">🎸 Rock Anthem</button>
                <button class="template-btn" type="button" data-template="acoustic_story" data-i18n="templateAcousticStory">🎸 Acoustic Story</button>
                <button class="template-btn" type="button" data-template="electronic_dream" data-i18n="templateElectronicDream">💫 Electronic Dream</button>
                <button class="template-btn" type="button" data-template="hiphop_beats" data-i18n="templateHiphopBeats">🎤 Hip-Hop Beats</button>
                <button class="template-btn" type="button" data-template="cinematic_epic" data-i18n="templateCinematicEpic">🎬 Cinematic Epic</button>
                <button class="template-btn" type="button" data-template="lofi_chill" data-i18n="templateLofiChill">☕ Lo-Fi Chill</button>
              </div>
            </div>
            <!-- Lyrics Idea -->
            <div class="form-section">
              <label class="form-label" data-i18n="lyricsIdeaLabel">Lyrics Brief for AI (optional)</label>
              <textarea id="lyricsIdea" maxlength="2500" class="form-input" data-i18n-placeholder="lyricsIdeaPlaceholder" placeholder="Tell the story, feelings, images, language, chorus idea, or fragments you want in the lyrics."></textarea>
              <div class="char-counter" id="lyricsIdeaCounter"><span class="counter-bar"><span class="counter-fill" style="width:0%"></span></span><span class="counter-text">0 / 2500</span></div>
              <div class="form-hint" data-i18n="lyricsIdeaHint">If finished lyrics are empty, Music Speaks will ask AI to write lyrics from this brief.</div>
              <div style="margin-top:12px;display:flex;align-items:center;gap:12px;">
                <button id="generateLyricsBtn" class="btn-secondary" type="button" data-i18n="generateLyrics">Generate Lyrics</button>
                <span id="lyricsAssistMessage" style="font-size:13px;color:var(--text-muted);"></span>
              </div>
            </div>
            <!-- Finished Lyrics -->
            <div class="form-section">
              <label class="form-label" data-i18n="lyricsLabel">Finished Lyrics (optional)</label>
              <textarea id="lyrics" maxlength="3500" class="form-input" data-i18n-placeholder="lyricsPlaceholder" placeholder="[Verse]&#10;Your lyrics here...&#10;[Hook]&#10;Your chorus..."></textarea>
              <div class="char-counter" id="lyricsCounter"><span class="counter-bar"><span class="counter-fill" style="width:0%"></span></span><span class="counter-text">0 / 3500</span></div>
              <div class="form-hint" data-i18n="lyricsHint">Paste exact lyrics here if you already have them. Exact lyrics take priority.</div>
            </div>
            <!-- Options -->
            <div class="form-section">
              <div class="checkbox-grid">
                <label class="checkbox-item">
                  <input id="instrumental" type="checkbox">
                  <span><span data-i18n="instrumental">Instrumental</span><small data-i18n="instrumentalHint">No vocals. Lyrics ignored.</small></span>
                </label>
                <label class="checkbox-item">
                  <input id="lyricsOptimizer" type="checkbox">
                  <span><span data-i18n="autoLyrics">Auto-generate Lyrics</span><small data-i18n="autoLyricsHint">AI writes lyrics from prompt.</small></span>
                </label>
              </div>
            </div>
            <!-- Voice Clone -->
            <div class="form-section">
              <label class="form-label" data-i18n="voiceCloneLabel">Voice Clone (optional)</label>
              <div class="voice-section">
                <div class="voice-top-row">
                  <button id="voiceRecordBtn" class="btn-voice" type="button">
                    <span>🎤</span>
                    <span data-i18n="voiceRecordBtn">Record My Voice</span>
                  </button>
                  <span id="voiceStatus" class="voice-status"></span>
                </div>
                <div id="voicePreviewRow" style="display:none;margin-top:12px;align-items:center;gap:12px;">
                  <button id="voicePreviewBtn" class="btn-secondary" type="button" data-i18n="voicePreviewBtn">Preview Voice</button>
                  <audio id="voicePreviewAudio" controls style="height:36px;"></audio>
                </div>
                <div class="form-hint" data-i18n="voiceCloneHint">Record 5 passages. Takes ~30s. Voice expires in 7 days.</div>
              </div>
            </div>
            <!-- Advanced Parameters -->
            <div class="form-section">
              <div class="advanced-toggle" id="advancedToggle">
                <span data-i18n="advanced">More Parameters</span>
                <span class="advanced-toggle-icon">▼</span>
              </div>
              <div class="advanced-panel" id="advancedPanel">
                <!-- Reference Audio -->
                <div class="ref-audio-section" id="refAudioSection">
                  <h4 data-i18n="refAudioTitle">Reference Audio (Audio-to-Audio)</h4>
                  <div class="ref-audio-dropzone" id="refAudioDropzone">
                    <div class="icon">🎵</div>
                    <p data-i18n="refAudioDrop">Drop audio file here or click to upload (MP3, WAV, 6s-6min)</p>
                  </div>
                  <input type="file" id="refAudioFile" accept="audio/*" style="display:none;">
                  <div id="refAudioInfo" class="ref-audio-info" style="display:none;">
                    <audio id="refAudioPreview" controls style="flex:1;height:36px;"></audio>
                    <button id="refAudioRemove" class="btn-secondary" type="button" data-i18n="refAudioRemove">Remove</button>
                  </div>
                  <div class="ref-audio-mode" id="refAudioMode" style="display:none;">
                    <label><input type="radio" name="refMode" value="style" checked><span data-i18n="refModeStyle">Style Transfer</span></label>
                    <label><input type="radio" name="refMode" value="keep_vocals"><span data-i18n="refModeKeepVocals">Keep Vocals</span></label>
                    <label><input type="radio" name="refMode" value="remix"><span data-i18n="refModeRemix">Remix</span></label>
                  </div>
                  <div class="form-hint" data-i18n="refAudioHint">Upload reference audio to generate music with similar style. Style Transfer uses the reference as style inspiration.</div>
                </div>
                <div class="param-grid">
                  <div class="param-field"><label data-i18n="genre">Genre</label><input id="genre" data-i18n-placeholder="genrePlaceholder" placeholder="pop, reggae, jazz"></div>
                  <div class="param-field"><label data-i18n="mood">Mood</label><input id="mood" data-i18n-placeholder="moodPlaceholder" placeholder="warm, bright, intense"></div>
                  <div class="param-field"><label data-i18n="instruments">Instruments</label><input id="instruments" data-i18n-placeholder="instrumentsPlaceholder" placeholder="piano, guitar, drums"></div>
                  <div class="param-field"><label data-i18n="tempo">Tempo</label><input id="tempo" data-i18n-placeholder="tempoPlaceholder" placeholder="fast, slow, moderate"></div>
                  <div class="param-field"><label data-i18n="bpm">BPM</label><input id="bpm" type="number" min="40" max="240" data-i18n-placeholder="bpmPlaceholder" placeholder="85"></div>
                  <div class="param-field"><label data-i18n="key">Key</label><input id="key" data-i18n-placeholder="keyPlaceholder" placeholder="C major, A minor"></div>
                  <div class="param-field" style="grid-column:1/-1;"><label data-i18n="vocals">Vocal Style</label><input id="vocals" data-i18n-placeholder="vocalsPlaceholder" placeholder="warm male vocal, bright female vocal, duet"></div>
                  <div class="param-field" style="grid-column:1/-1;"><label data-i18n="structure">Song Structure</label><input id="structure" data-i18n-placeholder="structurePlaceholder" placeholder="verse-chorus-verse-bridge-chorus"></div>
                  <div class="param-field" style="grid-column:1/-1;"><label data-i18n="references">References</label><input id="references" data-i18n-placeholder="referencesPlaceholder" placeholder="similar to..."></div>
                  <div class="param-field" style="grid-column:1/-1;"><label data-i18n="avoid">Avoid</label><input id="avoid" data-i18n-placeholder="avoidPlaceholder" placeholder="explicit content, auto-tune"></div>
                  <div class="param-field"><label data-i18n="duration">Duration</label><select id="duration" class="form-input">
                    <option value="30" data-i18n="duration30s">30 seconds (default)</option>
                    <option value="60" data-i18n="duration1m">1 minute</option>
                    <option value="120" data-i18n="duration2m">2 minutes</option>
                    <option value="180" data-i18n="duration3m">3 minutes</option>
                    <option value="300" data-i18n="duration5m">5 minutes</option>
                    <option value="600" data-i18n="duration10m">10 minutes</option>
                  </select></div>
                  <div class="param-field" style="grid-column:1/-1;"><div class="form-hint" data-i18n="durationHint">MiniMax generates ~30s per call. Longer durations require multiple generations and will take more time.</div></div>
                </div>
              </div>
            </div>
            <!-- Actions -->
            <div class="form-actions">
              <button id="submitBtn" class="btn-primary" type="submit" data-i18n="submit">Generate Music</button>
              <button id="clearDraftBtn" class="btn-secondary" type="button" data-i18n="clearDraft">Clear Draft</button>
            </div>
            <div id="formError" class="error-alert" style="display:none;">
              <span class="error-alert-icon">&#9888;</span>
              <div class="error-alert-content">
                <div class="error-alert-message" id="formErrorMessage"></div>
              </div>
              <button class="error-alert-close" onclick="this.parentElement.style.display='none'">&#x2715;</button>
            </div>
            <div id="draftStatus" style="margin-top:12px;font-size:12px;color:var(--text-muted);"></div>
          </form>
          <!-- Jobs Panel -->
          <div class="jobs-panel">
            <div class="jobs-header">
              <h3 class="jobs-title" data-i18n="jobsTitle">Generation Jobs</h3>
            </div>
            <div class="jobs-toolbar" id="jobs-toolbar">
              <div class="search-wrap">
                <span class="search-icon">&#128269;</span>
                <input type="text" id="jobs-search" data-i18n-placeholder="jobsSearchPlaceholder" placeholder="Search title...">
              </div>
              <select id="jobs-status-filter">
                <option value="" data-i18n="jobsFilterAll">All</option>
                <option value="completed" data-i18n="completed">Done</option>
                <option value="running" data-i18n="running">Generating</option>
                <option value="queued" data-i18n="queued">Queued</option>
                <option value="error" data-i18n="error">Error</option>
              </select>
              <input type="date" class="date-input" id="jobs-date-from" data-i18n-placeholder="jobsDateFrom" title="From date">
              <span style="color:var(--text-muted);font-size:12px;">—</span>
              <input type="date" class="date-input" id="jobs-date-to" data-i18n-placeholder="jobsDateTo" title="To date">
              <button class="clear-btn" id="jobs-clear-filters" data-i18n="jobsClearFilters">Clear</button>
            </div>
            <div class="batch-bar" id="jobs-batch-bar">
              <span id="jobs-batch-count">0 selected</span>
              <button class="batch-btn download" id="jobs-batch-download" data-i18n="batchDownload">Download</button>
              <button class="batch-btn delete" id="jobs-batch-delete" data-i18n="batchDelete">Delete</button>
              <button class="batch-btn cancel" id="jobs-batch-cancel" data-i18n="cancel">Cancel</button>
            </div>
            <div id="jobs" class="jobs-list"></div>
            <div class="jobs-pagination" id="jobs-pagination" style="display:none;">
              <button id="jobs-page-prev">&#8592;</button>
              <span class="page-info" id="jobs-page-info"></span>
              <button id="jobs-page-next">&#8594;</button>
            </div>
          </div>
        </div>
        <!-- Library View -->
        <div id="view-library" style="display:none;">
          <div class="page-header">
            <h1 class="page-title" data-i18n="navLibrary">Library</h1>
            <p class="page-desc" data-i18n="libraryDesc">All your generated songs in one place.</p>
          </div>
          <div class="jobs-toolbar" id="lib-toolbar">
            <div class="search-wrap">
              <span class="search-icon">&#128269;</span>
              <input type="text" id="lib-search" data-i18n-placeholder="jobsSearchPlaceholder" placeholder="Search title...">
            </div>
            <select id="lib-status-filter">
              <option value="" data-i18n="jobsFilterAll">All</option>
              <option value="completed" data-i18n="completed">Done</option>
              <option value="error" data-i18n="error">Error</option>
            </select>
            <input type="date" class="date-input" id="lib-date-from" title="From date">
            <span style="color:var(--text-muted);font-size:12px;">—</span>
            <input type="date" class="date-input" id="lib-date-to" title="To date">
            <button class="clear-btn" id="lib-clear-filters" data-i18n="jobsClearFilters">Clear</button>
          </div>
          <div class="batch-bar" id="lib-batch-bar">
            <span id="lib-batch-count">0 selected</span>
            <button class="batch-btn download" id="lib-batch-download" data-i18n="batchDownload">Download</button>
            <button class="batch-btn delete" id="lib-batch-delete" data-i18n="batchDelete">Delete</button>
            <button class="batch-btn cancel" id="lib-batch-cancel" data-i18n="cancel">Cancel</button>
          </div>
          <div id="library-list" class="jobs-list"></div>
          <div class="jobs-pagination" id="lib-pagination" style="display:none;">
            <button id="lib-page-prev">&#8592;</button>
            <span class="page-info" id="lib-page-info"></span>
            <button id="lib-page-next">&#8594;</button>
          </div>
        </div>
        <!-- Favorites View -->
        <div id="view-favorites" style="display:none;">
          <div class="page-header">
            <h1 class="page-title" data-i18n="navFavorites">Favorites</h1>
            <p class="page-desc" data-i18n="favoritesDesc">Your liked and saved songs.</p>
          </div>
          <div id="favorites-list" class="jobs-list"></div>
        </div>
        <!-- History View -->
        <div id="view-history" style="display:none;">
          <div class="page-header">
            <h1 class="page-title" data-i18n="navHistory">History</h1>
            <p class="page-desc" data-i18n="historyDesc">Recently generated songs.</p>
          </div>
          <div id="history-list" class="jobs-list"></div>
        </div>
      </main>
    </div>
    <!-- Bottom Player -->
    <div class="player" id="player" style="display:none;">
      <div class="player-track">
        <div class="player-art">🎵</div>
        <div class="player-info">
          <div class="player-title" id="playerTitle">Song Title</div>
          <div class="player-artist" id="playerArtist">Music Speaks</div>
        </div>
      </div>
      <div class="player-controls">
        <div class="player-buttons">
          <button class="player-btn" id="playerPrev">⏮</button>
          <button class="player-btn play" id="playerPlay">▶</button>
          <button class="player-btn" id="playerNext">⏭</button>
        </div>
        <div class="player-progress">
          <span class="player-time" id="playerCurrentTime">0:00</span>
          <div class="player-bar" id="playerBar"><div class="player-bar-fill" id="playerBarFill"></div></div>
          <span class="player-time" id="playerDuration">0:00</span>
        </div>
      </div>
      <div class="player-lyrics" id="playerLyrics">
        <div class="lyrics-text" id="lyricsText">♪ Lyrics ♪</div>
      </div>
      <div class="player-volume">
        <span class="volume-icon" id="volumeIcon">🔊</span>
        <div class="volume-slider" id="volumeSlider"><div class="volume-fill" id="volumeFill"></div></div>
      </div>
    </div>
  </div>
  <!-- Recording Modal -->
  <div id="recModal" class="modal-overlay" style="display:none;">
    <div class="modal-content">
      <div class="modal-header">
        <h3 class="modal-title" data-i18n="recModalTitle">Record Your Voice</h3>
      </div>
      <div class="modal-body" id="recModalBody">
        <div class="rec-progress">
          <div class="rec-step" id="recStep">Step 1 of 5</div>
          <div class="rec-bar"><div class="rec-bar-fill" id="recBarFill" style="width:20%"></div></div>
        </div>
        <div class="rec-script">
          <div class="rec-script-text" id="recScriptText">The rain falls softly on the windowpane...</div>
        </div>
        <div class="rec-countdown" id="recCountdown" style="display:none;"></div>
        <div class="rec-instruction" id="recInstruction">Click Record to start recording this passage</div>
        <div class="rec-controls">
          <button class="rec-btn rec-btn-record" id="recRecordBtn">⏺ Record</button>
          <button class="rec-btn rec-btn-stop" id="recStopBtn" style="display:none;">⏹ Stop</button>
          <button class="rec-btn rec-btn-next" id="recNextBtn" style="display:none;">Next →</button>
        </div>
      </div>
    </div>
  </div>
  <script>
    // ── Sound Effects System (Web Audio API) ──────────────────────
    const SoundSystem = {
      ctx: null,
      enabled: true,
      init() {
        try {
          this.ctx = new (window.AudioContext || window.webkitAudioContext)();
        } catch(e) { console.warn("Web Audio API not supported"); }
      },
      play(type) {
        if (!this.enabled || !this.ctx) return;
        if (this.ctx.state === "suspended") this.ctx.resume();
        const now = this.ctx.currentTime;
        const osc = this.ctx.createOscillator();
        const gain = this.ctx.createGain();
        osc.connect(gain);
        gain.connect(this.ctx.destination);
        switch(type) {
          case "click":
            osc.frequency.setValueAtTime(800, now);
            osc.frequency.exponentialRampToValueAtTime(400, now + 0.05);
            gain.gain.setValueAtTime(0.1, now);
            gain.gain.exponentialRampToValueAtTime(0.001, now + 0.05);
            osc.start(now); osc.stop(now + 0.05);
            break;
          case "success":
            osc.frequency.setValueAtTime(523.25, now);
            osc.frequency.setValueAtTime(659.25, now + 0.1);
            osc.frequency.setValueAtTime(783.99, now + 0.2);
            gain.gain.setValueAtTime(0.15, now);
            gain.gain.exponentialRampToValueAtTime(0.001, now + 0.4);
            osc.start(now); osc.stop(now + 0.4);
            break;
          case "error":
            osc.frequency.setValueAtTime(200, now);
            osc.frequency.setValueAtTime(150, now + 0.1);
            gain.gain.setValueAtTime(0.12, now);
            gain.gain.exponentialRampToValueAtTime(0.001, now + 0.2);
            osc.start(now); osc.stop(now + 0.2);
            break;
          case "complete":
            osc.frequency.setValueAtTime(523.25, now);
            osc.frequency.setValueAtTime(659.25, now + 0.08);
            osc.frequency.setValueAtTime(783.99, now + 0.16);
            osc.frequency.setValueAtTime(1046.50, now + 0.24);
            gain.gain.setValueAtTime(0.12, now);
            gain.gain.exponentialRampToValueAtTime(0.001, now + 0.5);
            osc.start(now); osc.stop(now + 0.5);
            break;
          case "startup":
            osc.frequency.setValueAtTime(440, now);
            osc.frequency.setValueAtTime(554.37, now + 0.1);
            osc.frequency.setValueAtTime(659.25, now + 0.2);
            gain.gain.setValueAtTime(0.08, now);
            gain.gain.exponentialRampToValueAtTime(0.001, now + 0.35);
            osc.start(now); osc.stop(now + 0.35);
            break;
          case "record":
            osc.type = "sawtooth";
            osc.frequency.setValueAtTime(300, now);
            gain.gain.setValueAtTime(0.08, now);
            gain.gain.exponentialRampToValueAtTime(0.001, now + 0.15);
            osc.start(now); osc.stop(now + 0.15);
            break;
        }
      },
      toggle() {
        this.enabled = !this.enabled;
        return this.enabled;
      }
    };
    SoundSystem.init();

    const I18N = {
      en: {
        subtitle: "When words fall short, let music speak. Give your inner world a sound of its own.",
        createTitle: "Create Music", createDesc: "Write a feeling, story, lyric, or style. Music Speaks turns it into a downloadable song.",
        emailLabel: "Email Address (optional)", emailHint: "Optional. The download button is the main way to get your MP3.",
        emailPlaceholder: "your@email.com",
        titleLabel: "Song Title (optional)", titleHint: "If empty, Music Speaks will create a title from the lyrics before saving the MP3.",
        titlePlaceholder: "Leave empty and AI will name the song",
        promptLabel: "Music Style Prompt", promptHint: "Include style, mood, instruments, tempo, and any references.",
        promptPlaceholder: "Cinematic electronic pop, confident and bright, polished production, strong hook",
        lyricsIdeaLabel: "Lyrics Brief for AI (optional)", lyricsIdeaHint: "If finished lyrics are empty, Music Speaks will ask AI to write lyrics from this brief.",
        lyricsIdeaPlaceholder: "Tell the story, feelings, images, language, chorus idea, or fragments you want in the lyrics.",
        generateLyrics: "Generate Lyrics", generatingLyrics: "Generating lyrics...", lyricsGenerated: "Lyrics added below. You can edit them before generating music.",
        lyricsAssistNeedBrief: "Add a lyrics brief or music style prompt first.", lyricsAssistFailed: "Lyrics generation failed.",
        lyricsLabel: "Finished Lyrics (optional)", lyricsHint: "Paste exact lyrics here if you already have them. Exact lyrics take priority over the lyrics brief.",
        lyricsPlaceholder: "[Verse]\nYour lyrics here...\n[Hook]\nYour chorus...",
        instrumental: "Instrumental", instrumentalHint: "No vocals. Lyrics will be ignored.",
        autoLyrics: "Auto-generate Lyrics", autoLyricsHint: "AI writes lyrics from your prompt.",
        voiceCloneLabel: "Voice Clone (optional)", voiceRecordBtn: "Record My Voice", voiceCloneHint: "Record 5 short passages covering different tones and styles. Takes about 30 seconds. Cloned voice expires in 7 days.",
        voicePreviewBtn: "Preview Voice", voiceUploading: "Cloning your voice...", voiceReady: "Voice cloned! Use Preview to listen.",
        voiceError: "Voice clone failed.", voicePreviewGenerating: "Generating preview...", voicePreviewReady: "Preview ready.", voicePreviewError: "Preview failed.",
        recModalTitle: "Record Your Voice",
        templates: "Prompt Templates",
        advanced: "More Parameters", genre: "Genre", mood: "Mood", instruments: "Instruments", tempo: "Tempo Feel", bpm: "BPM", key: "Musical Key",
        vocals: "Vocal Style", structure: "Song Structure", references: "References", avoid: "Avoid", useCase: "Use Case", extra: "Extra Details",
        refAudioTitle: "Reference Audio (Audio-to-Audio)", refAudioDrop: "Drop audio file here or click to upload (MP3, WAV, 6s-6min)",
        refAudioRemove: "Remove", refAudioHint: "Upload reference audio to generate music with similar style. Style Transfer uses the reference as style inspiration.",
        refModeStyle: "Style Transfer", refModeKeepVocals: "Keep Vocals", refModeRemix: "Remix",
        duration: "Duration", durationHint: "MiniMax generates ~30s per call. Longer durations require multiple generations and will take more time.",
        duration30s: "30 seconds (default)", duration1m: "1 minute", duration2m: "2 minutes", duration3m: "3 minutes", duration5m: "5 minutes", duration10m: "10 minutes",
        genrePlaceholder: "pop, reggae, jazz", moodPlaceholder: "warm, bright, intense", instrumentsPlaceholder: "piano, guitar, drums",
        tempoPlaceholder: "fast, slow, moderate", bpmPlaceholder: "85", keyPlaceholder: "C major, A minor",
        vocalsPlaceholder: "warm male vocal, bright female vocal, duet", structurePlaceholder: "verse-chorus-verse-bridge-chorus",
        referencesPlaceholder: "similar to...", avoidPlaceholder: "explicit content, auto-tune", useCasePlaceholder: "video background, theme song",
        extraPlaceholder: "Any additional notes",
        submit: "Generate Music", jobsTitle: "Jobs", jobsDesc: "Real-time status. Download appears when the MP3 is ready.",
        clearDraft: "Clear Draft", clearDraftConfirm: "Clear the current draft? This will not delete generated music.",
        draftSaved: "Draft saved", draftRestored: "Draft restored", draftCleared: "Draft cleared", draftRestoreFailed: "Could not restore server draft.",
        empty: "No jobs yet. Fill in the form to start creating.", queued: "Queued", running: "Generating", completed: "Done", error: "Error", unknown: "Unknown", untitled: "Untitled",
        download: "Download MP3", delete: "Delete", sent: "Sent to", instrumentalMode: "Instrumental", vocalMode: "Vocal", deleteConfirm: "Delete this job?", deleteFailed: "Delete failed",
        navCreate: "Create", navLibrary: "Library", navFavorites: "Favorites", navHistory: "History", navPlaylists: "Playlists", playlistAll: "All Songs", playlistRecent: "Recently Played",
        libraryDesc: "All your generated songs in one place.", favoritesDesc: "Your liked and saved songs.", historyDesc: "Recently generated songs.",
        toastMusicStarted: "Music generation started!", toastMusicReady: "Music ready: ", toastLyricsSuccess: "Lyrics generated successfully!", toastLyricsError: "Lyrics generation failed.", toastVoiceCloneSuccess: "Voice cloned successfully!", toastVoiceCloneError: "Voice clone failed.",
        stemSplit: "Split Audio", stemSplitting: "Splitting...", stemDone: "Split Complete", stemError: "Split Failed",
        stemDrums: "Drums", stemBass: "Bass", stemVocals: "Vocals", stemOther: "Instrumental",
        stemDownload: "Download", stemModalTitle: "Split Audio Stems", stemModalDesc: "Download individual tracks from your song.",
        themeLight: "Light Mode", themeDark: "Dark Mode", themeSystem: "System"
        notificationsEnabled: "Notifications enabled", notificationsDisabled: "Notifications disabled", songReadyNotification: "Your song \"{title}\" is ready!",
        playBtn: "▶ Play", untitled: "Untitled", audioFileRequired: "Please select an audio file.",
        langBtnLabel: "EN",
        templateUpbeatPop: "Upbeat Pop", templateChillAmbient: "Chill Ambient", templateRockAnthem: "Rock Anthem",
        templateAcousticStory: "Acoustic Story", templateElectronicDream: "Electronic Dream", templateHiphopBeats: "Hip-Hop Beats",
        templateCinematicEpic: "Cinematic Epic", templateLofiChill: "Lo-Fi Chill"
      },
      zh: {
        subtitle: "当语言无法抵达时，让音乐替你表达。给你的内心世界一种属于自己的声音。",
        createTitle: "创建音乐", createDesc: "写下感受、故事、歌词或风格，Music Speaks 会把它变成一首可以下载的歌。",
        emailLabel: "邮箱地址（可选）", emailHint: "可不填写。下载按钮是获取 MP3 的主要方式。",
        emailPlaceholder: "你的邮箱（可选）",
        titleLabel: "歌名（可选）", titleHint: "不填写时，Music Speaks 会根据歌词分析生成歌名，并用作 MP3 文件名。",
        titlePlaceholder: "留空时，AI 会自动起歌名",
        promptLabel: "音乐风格描述", promptHint: "写清风格、情绪、乐器、速度和参考对象。",
        promptPlaceholder: "例如：明亮自信的电子流行，制作精致，副歌有记忆点",
        lyricsIdeaLabel: "歌词需求描述（可选）", lyricsIdeaHint: "如果没有填写完整歌词，Music Speaks 会让 AI 根据这里的故事、感受、片段或概念生成歌词。",
        lyricsIdeaPlaceholder: "写下你想要的故事、情绪、画面、语言、某句副歌，或零散歌词片段。",
        generateLyrics: "生成歌词", generatingLyrics: "正在生成歌词...", lyricsGenerated: "歌词已填入下方，你可以编辑后再生成音乐。",
        lyricsAssistNeedBrief: "请先填写歌词需求描述或音乐风格。", lyricsAssistFailed: "歌词生成失败。",
        lyricsLabel: "完整歌词（可选）", lyricsHint: "如果你已经有确定歌词，粘贴在这里。完整歌词会优先于歌词需求描述。",
        lyricsPlaceholder: "[主歌]\n在这里写歌词...\n[副歌]\n在这里写副歌...",
        instrumental: "纯音乐", instrumentalHint: "无人声，歌词会被忽略。",
        autoLyrics: "自动生成歌词", autoLyricsHint: "AI 根据描述写歌词。",
        voiceCloneLabel: "声纹复刻（可选）", voiceRecordBtn: "录制我的声音", voiceCloneHint: "录制5段不同音调和风格的短句，约30秒。复刻声音有效期7天。",
        voicePreviewBtn: "预览声音", voiceUploading: "正在复刻你的声音...", voiceReady: "声音复刻完成！点击预览试听。",
        voiceError: "声音复刻失败。", voicePreviewGenerating: "正在生成预览...", voicePreviewReady: "预览已生成。", voicePreviewError: "预览生成失败。",
        recModalTitle: "录制您的声音",
        templates: "风格模板",
        advanced: "更多参数", genre: "流派", mood: "情绪", instruments: "乐器", tempo: "节奏感", bpm: "BPM", key: "调性",
        vocals: "人声风格", structure: "歌曲结构", references: "参考对象", avoid: "避免元素", useCase: "使用场景", extra: "其他细节",
        refAudioTitle: "参考音频（Audio-to-Audio）", refAudioDrop: "拖拽音频文件到此处或点击上传（MP3、WAV，6秒-6分钟）",
        refAudioRemove: "移除", refAudioHint: "上传参考音频以生成相似风格的音乐。风格迁移会将参考音频作为风格灵感。",
        refModeStyle: "风格迁移", refModeKeepVocals: "保留人声", refModeRemix: "混音",
        duration: "时长", durationHint: "MiniMax 每次生成约30秒。更长时长需要多次生成，耗时更久。",
        duration30s: "30秒（默认）", duration1m: "1分钟", duration2m: "2分钟", duration3m: "3分钟", duration5m: "5分钟", duration10m: "10分钟",
        genrePlaceholder: "流行、雷鬼、爵士", moodPlaceholder: "温暖、明亮、强烈", instrumentsPlaceholder: "钢琴、吉他、鼓",
        tempoPlaceholder: "快、中速、慢", bpmPlaceholder: "85", keyPlaceholder: "C 大调、A 小调",
        vocalsPlaceholder: "温暖男声、明亮女声、男女对唱", structurePlaceholder: "主歌-副歌-主歌-桥段-副歌",
        referencesPlaceholder: "参考某首歌、某位歌手或某种感觉", avoidPlaceholder: "避免露骨内容、避免过重电音修音",
        useCasePlaceholder: "视频背景、主题曲、朋友生日歌", extraPlaceholder: "其他补充要求",
        submit: "生成音乐", jobsTitle: "生成任务", jobsDesc: "实时状态。MP3 准备好后会出现下载按钮。",
        clearDraft: "清空草稿", clearDraftConfirm: "清空当前草稿？这不会删除已经生成的音乐。",
        draftSaved: "草稿已保存", draftRestored: "已恢复上次草稿", draftCleared: "草稿已清空", draftRestoreFailed: "无法恢复服务器草稿。",
        empty: "暂无任务，填写表单开始创作。", queued: "排队中", running: "生成中", completed: "完成", error: "错误", unknown: "未知", untitled: "无标题",
        download: "下载 MP3", delete: "删除", sent: "已发送到", instrumentalMode: "纯音乐", vocalMode: "有人声", deleteConfirm: "删除此任务？", deleteFailed: "删除失败",
        navCreate: "创建", navLibrary: "曲库", navFavorites: "收藏", navHistory: "历史", navPlaylists: "播放列表", playlistAll: "全部歌曲", playlistRecent: "最近播放",
        libraryDesc: "你生成的所有歌曲。", favoritesDesc: "你喜欢的歌曲。", historyDesc: "最近生成的歌曲。",
        toastMusicStarted: "音乐生成已开始！", toastMusicReady: "音乐完成：", toastLyricsSuccess: "歌词生成成功！", toastLyricsError: "歌词生成失败。", toastVoiceCloneSuccess: "声音复刻成功！", toastVoiceCloneError: "声音复刻失败。",
        stemSplit: "分离音轨", stemSplitting: "分离中...", stemDone: "分离完成", stemError: "分离失败",
        stemDrums: "鼓", stemBass: "贝斯", stemVocals: "人声", stemOther: "器乐",
        stemDownload: "下载", stemModalTitle: "分离音频音轨", stemModalDesc: "下载歌曲的各个音轨。",
        themeLight: "浅色模式", themeDark: "深色模式", themeSystem: "跟随系统"
        notificationsEnabled: "通知已开启", notificationsDisabled: "通知已关闭", songReadyNotification: "你的歌曲「{title}」已准备就绪！",
        playBtn: "▶ 播放", untitled: "未命名", audioFileRequired: "请选择一个音频文件。",
        langBtnLabel: "中文",
        templateUpbeatPop: "流行活力", templateChillAmbient: "氛围 Chill", templateRockAnthem: "摇滚圣歌",
        templateAcousticStory: "民谣故事", templateElectronicDream: "电子梦境", templateHiphopBeats: "嘻哈节拍",
        templateCinematicEpic: "电影史诗", templateLofiChill: "Lo-Fi 放松"
      },
      ja: {
        subtitle: "言葉が足りないとき、音楽が代わりに語る。あなたの内なる世界に音を。",
        createTitle: "音楽を作成", createDesc: "気分、物語、歌詞、スタイルを入力してください。Music Speaksがダウンロード可能な曲にします。",
        emailLabel: "メールアドレス（任意）", emailHint: "任意。ダウンロードボタンがMP3を受け取る主な方法です。",
        emailPlaceholder: "your@email.com",
        titleLabel: "曲名（任意）", titleHint: "空欄の場合、Music Speaksが歌詞から曲名を作成し、MP3のファイル名にします。",
        titlePlaceholder: "空欄でAIが曲名をつける",
        promptLabel: "音楽スタイルのプロンプト", promptHint: "スタイル、ムード、楽器、テンポ、参考曲を入れてください。",
        promptPlaceholder: "例：明亮で自信のあるエレクトロニックポップ、制作精良、フック印象深刻",
        lyricsIdeaLabel: "歌詞브리핑（任意）", lyricsIdeaHint: "完全な歌詞がない場合、Music Speaksはこのストーリーズ、感情、片段からAIに歌詞を書かせます。",
        lyricsIdeaPlaceholder: "伝えたいストーリー、感情、画面感、言語、副歌のアイデア、歌詞の断片を入力。",
        generateLyrics: "歌詞を生成", generatingLyrics: "歌詞を生成中...", lyricsGenerated: "歌詞が追加されました。音楽生成前に編集できます。",
        lyricsAssistNeedBrief: "先に歌詞ブリーフまたは音楽スタイルを入力してください。", lyricsAssistFailed: "歌詞生成に失敗しました。",
        lyricsLabel: "完全な歌詞（任意）", lyricsHint: "すでに確定した歌詞がある場合はここに貼り付けてください。完全な歌詞がブリーフより優先されます。",
        lyricsPlaceholder: "[主歌]
ここに歌詞...
[副歌]
ここに副歌...",
        instrumental: " инструментал", instrumentalHint: "ボーカルなし。歌詞は無視されます。",
        autoLyrics: "自動歌詞生成", autoLyricsHint: "AIが描述から歌詞を書きます。",
        voiceCloneLabel: "声紋クローン（任意）", voiceRecordBtn: "声を録音", voiceCloneHint: "異なる音調とスタイルの5つの短いセンテンスを録音してください。約30秒。クローン声は7日間有効です。",
        voicePreviewBtn: "声をプレビュー", voiceUploading: "声をクローン中...", voiceReady: "声のクローン完了！プレビューで试听。",
        voiceError: "声のクローンに失敗しました。", voicePreviewGenerating: "プレビューを生成中...", voicePreviewReady: "プレビュー生成完了。", voicePreviewError: "プレビュー生成に失敗しました。",
        recModalTitle: "声を録音",
        templates: "スタイルテンプレート",
        advanced: "更多パラメータ", genre: "ジャンル", mood: "ムード", instruments: "楽器", tempo: "テンポ感", bpm: "BPM", key: "調性",
        vocals: "ボーカルスタイル", structure: "曲構造", references: "参考", avoid: "避ける", useCase: "使用シーン", extra: "其他詳細",
        refAudioTitle: "参考オーディオ（Audio-to-Audio）", refAudioDrop: "オーディオファイルをここにドラッグまたはクリックしてアップロード（MP3、WAV、6秒-6分）",
        refAudioRemove: "移除", refAudioHint: "似たスタイルの音楽を生成するために参考オーディオをアップロードしてください。スタイル転送は参考オーディオをスタイルのインスピレーションとして使用します。",
        refModeStyle: "スタイル転送", refModeKeepVocals: "ボーカル保持", refModeRemix: "リミックス",
        duration: "長さ", durationHint: "MiniMaxは1回あたり約30秒生成します。より長い長さは複数回の生成が必要で、時間がかかります。",
        duration30s: "30秒（デフォルト）", duration1m: "1分", duration2m: "2分", duration3m: "3分", duration5m: "5分", duration10m: "10分",
        genrePlaceholder: "ポップ、レゲエ、ジャズ", moodPlaceholder: "温かい、明るい、激しい", instrumentsPlaceholder: "ピアノ、吉他、鼓",
        tempoPlaceholder: "速い、中程度、遅い", bpmPlaceholder: "85", keyPlaceholder: "C major、A minor",
        vocalsPlaceholder: "温かい男性ボーカル、明るい女性ボーカル、デュオ", structurePlaceholder: "主歌-副歌-主歌-橋-副歌",
        referencesPlaceholder: "ある曲、ある歌手、または某种感觉参考", avoidPlaceholder: "露骨な内容、重いディストーション避ける",
        useCasePlaceholder: "動画背景、テーマ曲、友人への誕生日歌", extraPlaceholder: "其他補足要件",
        submit: "音楽を生成", jobsTitle: "生成ジョブ", jobsDesc: "リアルタイム状態。MP3準備完了後、ダウンロードボタンが表示されます。",
        clearDraft: "下書きをクリア", clearDraftConfirm: "現在の下書きをクリアしますか？生成された音楽は削除されません。",
        draftSaved: "下書き保存済み", draftRestored: "前回の下書きを復元しました", draftCleared: "下書きをクリアしました", draftRestoreFailed: "サーバー下書きを復元できませんでした。",
        empty: "ジョブなし。フォームに記入して創作を開始してください。", queued: "待機中", running: "生成中", completed: "完了", error: "エラー", unknown: "不明",
        download: "MP3をダウンロード", delete: "削除", sent: "送信先", instrumentalMode: "インスト", vocalMode: "ボーカル", deleteConfirm: "このジョブを削除しますか？", deleteFailed: "削除に失敗しました",
        navCreate: "作成", navLibrary: "ライブラリ", navFavorites: "お気に入り", navHistory: "履歴", navPlaylists: "プレイリスト", playlistAll: "全曲", playlistRecent: "最近再生",
        libraryDesc: "生成したすべての音楽。", favoritesDesc: "好きな音楽。", historyDesc: "最近生成した音楽。",
        toastMusicStarted: "音楽生成が開始されました！", toastMusicReady: "音楽準備完了：", toastLyricsSuccess: "歌詞生成成功！", toastLyricsError: "歌詞生成に失敗しました。", toastVoiceCloneSuccess: "声紋クローン成功！", toastVoiceCloneError: "声紋クローンに失敗しました。",
        stemSplit: "オーディオを分離", stemSplitting: "分離中...", stemDone: "分離完了", stemError: "分離失敗",
        stemDrums: "鼓", stemBass: "ベース", stemVocals: "ボーカル", stemOther: "器楽",
        stemDownload: "ダウンロード", stemModalTitle: "オーディオステムを分離", stemModalDesc: "曲の各トラックをダウンロード。",
        notificationsEnabled: "通知有効", notificationsDisabled: "通知無効", songReadyNotification: "曲「{title}」の準備ができました！",
        playBtn: "▶ 再生", untitled: "無題", audioFileRequired: "オーディオファイルを選択してください。",
        langBtnLabel: "日本語",
        templateUpbeatPop: "ポップ活力", templateChillAmbient: "氛围 Chill", templateRockAnthem: "ロックanthem",
        templateAcousticStory: "アコースティックストーリー", templateElectronicDream: "電子の夢", templateHiphopBeats: "ヒップホップ节拍",
        templateCinematicEpic: "シネマティックエピック", templateLofiChill: "Lo-Fi リラックス"
      },
      ko: {
        subtitle: "말이 부족할 때, 음악이 대신 말한다. 당신의 내면 세계에属于自己的 소리를.",
        createTitle: "음악 만들기", createDesc: "느낌, 이야기, 가사, 스타일을 적어주세요. Music Speaks가 다운로드 가능한 노래로 만들어줍니다.",
        emailLabel: "이메일 주소 (선택)", emailHint: "선택사항. 다운로드 버튼이 MP3를 받는 주된 방법입니다.",
        emailPlaceholder: "your@email.com",
        titleLabel: "노래 제목 (선택)", titleHint: "비워두면 Music Speaks가 가사에서 제목을 만들어 MP3 파일명으로 사용합니다.",
        titlePlaceholder: "비워두면 AI가 제목을 붙여줍니다",
        promptLabel: "뮤직 스타일 프롬프트", promptHint: "스타일, 무드, 악기, 템포, 참고 곡을 넣어주세요.",
        promptPlaceholder: "예: 밝고 자신감 있는 일렉트로닉 팝, 정교한 프로덕션, 기억할 만한 후크",
        lyricsIdeaLabel: "가사 브리프 (선택)", lyricsIdeaHint: "완전한 가사가 비어 있으면 Music Speaks가 여기서 이야기, 느낌,片段 또는 개념으로 AI에게 가사를 쓰게 합니다.",
        lyricsIdeaPlaceholder: "원하는 이야기, 느낌, 이미지, 언어, 후크 아이디어, 가사片段을 적어주세요.",
        generateLyrics: "가사 생성", generatingLyrics: "가사 생성 중...", lyricsGenerated: "가사가 아래에 추가되었습니다. 음악 생성 전에 편집할 수 있습니다.",
        lyricsAssistNeedBrief: "먼저 가사 브리프 또는 음악 스타일을 입력하세요.", lyricsAssistFailed: "가사 생성에 실패했습니다.",
        lyricsLabel: "완전한 가사 (선택)", lyricsHint: "이미 확정된 가사가 있으면 여기에 붙여넣으세요. 완전한 가사가 브리프보다 우선합니다.",
        lyricsPlaceholder: "[verse]
여기에 가사...
[후크]
여기에 후크...",
        instrumental: " инструментал", instrumentalHint: "보컬 없음. 가사는 무시됩니다.",
        autoLyrics: "자동 가사 생성", autoLyricsHint: "AI가 설명에서 가사를씁니다.",
        voiceCloneLabel: "음성 클론 (선택)", voiceRecordBtn: "내 목소리 녹음", voiceCloneHint: "다양한 음조와 스타일의 5개 짧은 문장을 녹음하세요. 약 30초. 클론 목소리는 7일 동안 유효합니다.",
        voicePreviewBtn: "목소리 미리보기", voiceUploading: "목소리 클론 중...", voiceReady: "목소리 클론 완료! 미리보기로 들어보세요.",
        voiceError: "목소리 클론에 실패했습니다.", voicePreviewGenerating: "미리보기 생성 중...", voicePreviewReady: "미리보기 생성 완료.", voicePreviewError: "미리보기 생성에 실패했습니다.",
        recModalTitle: "목소리 녹음",
        templates: "스타일 템플릿",
        advanced: "추가 파라미터", genre: "장르", mood: "무드", instruments: "악기", tempo: "템포 느낌", bpm: "BPM", key: "조성",
        vocals: "보컬 스타일", structure: "노래 구조", references: "참고", avoid: "피하기", useCase: "사용 케이스", extra: "기타 세부",
        refAudioTitle: "참考 오디오 (Audio-to-Audio)", refAudioDrop: "오디오 파일을 여기에 드래그하거나 클릭하여 업로드 (MP3, WAV, 6초-6분)",
        refAudioRemove: "제거", refAudioHint: "유사한 스타일의 음악을 생성하려면 참고 오디오를 업로드하세요. 스타일 전송은 참고 오디오를 스타일 영감으로 사용합니다.",
        refModeStyle: "스타일 전송", refModeKeepVocals: "보컬 유지", refModeRemix: "리믹스",
        duration: "길이", durationHint: "MiniMax는 통화당 약 30초를 생성합니다. 더 긴 길이는 여러 생성이 필요하며 시간이 더 오래 걸립니다.",
        duration30s: "30초 (기본)", duration1m: "1분", duration2m: "2분", duration3m: "3분", duration5m: "5분", duration10m: "10분",
        genrePlaceholder: "팝, 레게, 재즈", moodPlaceholder: "따뜻한, 밝은, 강한", instrumentsPlaceholder: "피아노, 기타, 드럼",
        tempoPlaceholder: "빠른, 중간, 느린", bpmPlaceholder: "85", keyPlaceholder: "C major, A minor",
        vocalsPlaceholder: "따뜻한 남성 보컬, 밝은 여성 보컬, 듀엣", structurePlaceholder: " verse-후크-verse-브릿지-후크",
        referencesPlaceholder: "어떤 노래, 가수 또는 느낌 참고", avoidPlaceholder: "노골적인 내용, 무거운 디스토션 피하기",
        useCasePlaceholder: "비디오 배경, 테마 송, 친구 생일 노래", extraPlaceholder: "기타 추가 요청",
        submit: "음악 생성", jobsTitle: "생성 작업", jobsDesc: "실시간 상태. MP3 준비 완료 시 다운로드 버튼이 나타납니다.",
        clearDraft: "초안 지우기", clearDraftConfirm: "현재 초안을 지울까요? 생성된 음악은 삭제되지 않습니다.",
        draftSaved: "초안 저장됨", draftRestored: "이전 초안 복원됨", draftCleared: "초안 지워짐", draftRestoreFailed: "서버 초안을 복원할 수 없습니다.",
        empty: "작업 없음. 양식을 작성하여 만들기 시작하세요.", queued: "대기 중", running: "생성 중", completed: "완료", error: "오류", unknown: "알 수 없음",
        download: "MP3 다운로드", delete: "삭제", sent: "보낸 사람", instrumentalMode: "음악", vocalMode: "보컬", deleteConfirm: "이 작업을 삭제할까요?", deleteFailed: "삭제 실패",
        navCreate: "만들기", navLibrary: "라이브러리", navFavorites: "즐겨찾기", navHistory: "기록", navPlaylists: "재생목록", playlistAll: "모든 노래", playlistRecent: "최근 재생",
        libraryDesc: "생성한 모든 노래.", favoritesDesc: "좋아하는 노래.", historyDesc: "최근 생성한 노래.",
        toastMusicStarted: "음악 생성 시작!", toastMusicReady: "음악 준비 완료: ", toastLyricsSuccess: "가사 생성 성공!", toastLyricsError: "가사 생성 실패.", toastVoiceCloneSuccess: "음성 클론 성공!", toastVoiceCloneError: "음성 클론 실패.",
        stemSplit: "오디오 분할", stemSplitting: "분할 중...", stemDone: "분할 완료", stemError: "분할 실패",
        stemDrums: "드럼", stemBass: "베이스", stemVocals: "보컬", stemOther: "악기",
        stemDownload: "다운로드", stemModalTitle: "오디오 스텝 분할", stemModalDesc: "노래의 각 트랙을 다운로드.",
        notificationsEnabled: "알림 활성화", notificationsDisabled: "알림 비활성화", songReadyNotification: "노래 "{title}"이(가) 준비되었습니다!",
        playBtn: "▶ 재생", untitled: "제목 없음", audioFileRequired: "오디오 파일을 선택해 주세요.",
        langBtnLabel: "한국어",
        templateUpbeatPop: "팝 热舞", templateChillAmbient: "Chill 环境", templateRockAnthem: "摇滚 圣歌",
        templateAcousticStory: "原声 故事", templateElectronicDream: "电子 梦", templateHiphopBeats: "嘻哈 节拍",
        templateCinematicEpic: "电影 史诗", templateLofiChill: "Lo-Fi 放松"
      },
      es: {
        subtitle: "Cuando las palabras fallan, que la música hable. Dale a tu mundo interior su propio sonido.",
        createTitle: "Crear Música", createDesc: "Escribe un sentimiento, historia, letra o estilo. Music Speaks lo convierte en una canción descargable.",
        emailLabel: "Correo electrónico (opcional)", emailHint: "Opcional. El botón de descarga es la forma principal de obtener tu MP3.",
        emailPlaceholder: "tu@email.com",
        titleLabel: "Título de la canción (opcional)", titleHint: "Si está vacío, Music Speaks creará un título a partir de las letras antes de guardar el MP3.",
        titlePlaceholder: "Déjalo vacío y la IA nombrará la canción",
        promptLabel: "Prompt de Estilo Musical", promptHint: "Incluye estilo, estado de ánimo, instrumentos, tempo y referencias.",
        promptPlaceholder: "Ej: Pop electrónico cinematográfico, confiado y brillante, producción pulida, gancho fuerte",
        lyricsIdeaLabel: "Brief de Letras para la IA (opcional)", lyricsIdeaHint: "Si las letras finales están vacías, Music Speaks pedirá a la IA que escriba letras basadas en este brief.",
        lyricsIdeaPlaceholder: "Cuéntanos la historia, sentimientos, imágenes, idioma, idea del coro o fragmentos que quieras en las letras.",
        generateLyrics: "Generar Letras", generatingLyrics: "Generando letras...", lyricsGenerated: "Letras añadidas abajo. Puedes editarlas antes de generar la música.",
        lyricsAssistNeedBrief: "Añade primero un brief de letras o un prompt de estilo musical.", lyricsAssistFailed: "La generación de letras ha fallado.",
        lyricsLabel: "Letras Finales (opcional)", lyricsHint: "Pega aquí las letras que ya tengas. Las letras exactas tienen prioridad sobre el brief de letras.",
        lyricsPlaceholder: "[Verso]
Tus letras aquí...
[Estribillo]
Tu estribillo...",
        instrumental: "Instrumental", instrumentalHint: "Sin voces. Las letras serán ignoradas.",
        autoLyrics: "Auto-generar Letras", autoLyricsHint: "La IA escribe letras a partir de tu descripción.",
        voiceCloneLabel: "Clon de Voz (opcional)", voiceRecordBtn: "Grabar Mi Voz", voiceCloneHint: "Graba 5 pasajes cortos cubriendo diferentes tonos y estilos. Tarda unos 30 segundos. La voz clonada caduca en 7 días.",
        voicePreviewBtn: "Vista Previa de Voz", voiceUploading: "Clonando tu voz...", voiceReady: "¡Voz clonada! Usa Vista Previa para escuchar.",
        voiceError: "El clon de voz ha fallado.", voicePreviewGenerating: "Generando vista previa...", voicePreviewReady: "Vista previa lista.", voicePreviewError: "La vista previa ha fallado.",
        recModalTitle: "Graba Tu Voz",
        templates: "Plantillas de Estilo",
        advanced: "Más Parámetros", genre: "Género", mood: "Estado de ánimo", instruments: "Instrumentos", tempo: "Sensación de Tempo", bpm: "BPM", key: "Tonalidad",
        vocals: "Estilo Vocal", structure: "Estructura de la Canción", references: "Referencias", avoid: "Evitar", useCase: "Caso de Uso", extra: "Detalles Extra",
        refAudioTitle: "Audio de Referencia (Audio-a-Audio)", refAudioDrop: "Arrastra el archivo de audio aquí o haz clic para subir (MP3, WAV, 6s-6min)",
        refAudioRemove: "Eliminar", refAudioHint: "Sube audio de referencia para generar música de estilo similar. La Transferencia de Estilo usa la referencia como inspiración.",
        refModeStyle: "Transferencia de Estilo", refModeKeepVocals: "Mantener Voces", refModeRemix: "Remix",
        duration: "Duración", durationHint: "MiniMax genera ~30s por llamada. Duraciones más largas requieren múltiples generaciones y tardarán más.",
        duration30s: "30 segundos (por defecto)", duration1m: "1 minuto", duration2m: "2 minutos", duration3m: "3 minutos", duration5m: "5 minutos", duration10m: "10 minutos",
        genrePlaceholder: "pop, reggae, jazz", moodPlaceholder: "cálido, brillante, intenso", instrumentsPlaceholder: "piano, guitarra, batería",
        tempoPlaceholder: "rápido, lento, moderado", bpmPlaceholder: "85", keyPlaceholder: "Do mayor, La menor",
        vocalsPlaceholder: "vocal masculina cálida, vocal femenina brillante, dúo", structurePlaceholder: "verso-estribillo-verso-puente-estribillo",
        referencesPlaceholder: "similar a...", avoidPlaceholder: "contenido explícito, auto-tune",
        useCasePlaceholder: "fondo de video, canción temática", extraPlaceholder: "Notas adicionales",
        submit: "Generar Música", jobsTitle: "Trabajos", jobsDesc: "Estado en tiempo real. El botón de descarga aparece cuando el MP3 está listo.",
        clearDraft: "Borrar Borrador", clearDraftConfirm: "¿Borrar el borrador actual? Esto no eliminará la música generada.",
        draftSaved: "Borrador guardado", draftRestored: "Borrador anterior restaurado", draftCleared: "Borrador borrado", draftRestoreFailed: "No se pudo restaurar el borrador del servidor.",
        empty: "Sin trabajos aún. Completa el formulario para empezar a crear.", queued: "En cola", running: "Generando", completed: "Hecho", error: "Error", unknown: "Desconocido",
        download: "Descargar MP3", delete: "Eliminar", sent: "Enviado a", instrumentalMode: "Instrumental", vocalMode: "Vocal", deleteConfirm: "¿Eliminar este trabajo?", deleteFailed: "Eliminar fallido",
        navCreate: "Crear", navLibrary: "Biblioteca", navFavorites: "Favoritos", navHistory: "Historial", navPlaylists: "Listas", playlistAll: "Todas las Canciones", playlistRecent: "Reproducido Recientemente",
        libraryDesc: "Todas tus canciones generadas en un solo lugar.", favoritesDesc: "Tus canciones liked y guardadas.", historyDesc: "Canciones generadas recientemente.",
        toastMusicStarted: "¡Generación de música iniciada!", toastMusicReady: "Música lista: ", toastLyricsSuccess: "¡Letras generadas con éxito!", toastLyricsError: "Generación de letras fallida.", toastVoiceCloneSuccess: "¡Voz clonada con éxito!", toastVoiceCloneError: "Clon de voz fallido.",
        stemSplit: "Dividir Audio", stemSplitting: "Dividiendo...", stemDone: "División Completa", stemError: "División Fallida",
        stemDrums: "Batería", stemBass: "Bajo", stemVocals: "Voces", stemOther: "Instrumental",
        stemDownload: "Descargar", stemModalTitle: "Dividir Stems de Audio", stemModalDesc: "Descarga las pistas individuales de tu canción.",
        notificationsEnabled: "Notificaciones activadas", notificationsDisabled: "Notificaciones desactivadas", songReadyNotification: "¡Tu canción "{title}" está lista!",
        playBtn: "▶ Reproducir", untitled: "Sin título", audioFileRequired: "Por favor selecciona un archivo de audio.",
        langBtnLabel: "ES",
        templateUpbeatPop: "Pop Animado", templateChillAmbient: "Ambient Relajado", templateRockAnthem: "Rock Épico",
        templateAcousticStory: "Acústico Narrativo", templateElectronicDream: "Dream Electrónico", templateHiphopBeats: "Beats Hip-Hop",
        templateCinematicEpic: "Épico Cinemático", templateLofiChill: "Lo-Fi Relajado"
      },
      fr: {
        subtitle: "Quand les mots ne suffisent pas, laissez la musique parler. Donnez à votre monde intérieur sa propre voix.",
        createTitle: "Créer de la Musique", createDesc: "Écrivez un sentiment, une histoire, des paroles ou un style. Music Speaks les transforme en chanson téléchargeable.",
        emailLabel: "Adresse e-mail (facultatif)", emailHint: "Facultatif. Le bouton de téléchargement est la principale façon d'obtenir votre MP3.",
        emailPlaceholder: "votre@email.com",
        titleLabel: "Titre de la chanson (facultatif)", titleHint: "Si vide, Music Speaks créera un titre à partir des paroles avant d'enregistrer le MP3.",
        titlePlaceholder: "Laissez vide et l'IA nommera la chanson",
        promptLabel: "Prompt de Style Musical", promptHint: "Incluez le style, l'ambiance, les instruments, le tempo et les références.",
        promptPlaceholder: "Ex: Pop électronique cinématographique, confiant et brillant, production soignée, refrain accrocheur",
        lyricsIdeaLabel: "Brief de Paroles pour l'IA (facultatif)", lyricsIdeaHint: "Si les paroles finales sont vides, Music Speaks demandera à l'IA d'écrire des paroles à partir de ce brief.",
        lyricsIdeaPlaceholder: "Parlez de l'histoire, des sentiments, des images, de la langue, de l'idée du refrain ou des fragments que vous voulez dans les paroles.",
        generateLyrics: "Générer les Paroles", generatingLyrics: "Génération des paroles...", lyricsGenerated: "Paroles ajoutées ci-dessous. Vous pouvez les modifier avant de générer la musique.",
        lyricsAssistNeedBrief: "Ajoutez d'abord un brief de paroles ou un prompt de style musical.", lyricsAssistFailed: "La génération des paroles a échoué.",
        lyricsLabel: "Paroles Finales (facultatif)", lyricsHint: "Collez ici les paroles que vous avez déjà. Les paroles exactes ont la priorité sur le brief.",
        lyricsPlaceholder: "[Couplet]
Vos paroles ici...
[Refrain]
Votre refrain...",
        instrumental: "Instrumental", instrumentalHint: "Sans voix. Les paroles seront ignorées.",
        autoLyrics: "Auto-générer les Paroles", autoLyricsHint: "L'IA écrit des paroles à partir de votre description.",
        voiceCloneLabel: "Clone Vocal (facultatif)", voiceRecordBtn: "Enregistrer Ma Voix", voiceCloneHint: "Enregistrez 5 courts passages couvrant différents tons et styles. Prend environ 30 secondes. La voix clonée expire dans 7 jours.",
        voicePreviewBtn: "Aperçu Vocal", voiceUploading: "Clonage de votre voix...", voiceReady: "Voix clonée ! Utilisez Aperçu pour écouter.",
        voiceError: "Le clone vocal a échoué.", voicePreviewGenerating: "Génération de l'aperçu...", voicePreviewReady: "Aperçu prêt.", voicePreviewError: "L'aperçu a échoué.",
        recModalTitle: "Enregistrez Votre Voix",
        templates: "Modèles de Style",
        advanced: "Plus de Paramètres", genre: "Genre", mood: "Ambiance", instruments: "Instruments", tempo: "Sensation de Tempo", bpm: "BPM", key: "Tonalité",
        vocals: "Style Vocal", structure: "Structure de la Chanson", references: "Références", avoid: "À Éviter", useCase: "Cas d'Usage", extra: "Détails Supplémentaires",
        refAudioTitle: "Audio de Référence (Audio-à-Audio)", refAudioDrop: "Déposez le fichier audio ici ou cliquez pour télécharger (MP3, WAV, 6s-6min)",
        refAudioRemove: "Supprimer", refAudioHint: "Téléchargez un audio de référence pour générer de la musique de style similaire. Le Transfert de Style utilise la référence comme inspiration.",
        refModeStyle: "Transfert de Style", refModeKeepVocals: "Garder les Voix", refModeRemix: "Remix",
        duration: "Durée", durationHint: "MiniMax génère ~30s par appel. Les durées plus longues nécessitent plusieurs générations et prendront plus de temps.",
        duration30s: "30 secondes (défaut)", duration1m: "1 minute", duration2m: "2 minutes", duration3m: "3 minutes", duration5m: "5 minutes", duration10m: "10 minutes",
        genrePlaceholder: "pop, reggae, jazz", moodPlaceholder: "chaud, brillant, intense", instrumentsPlaceholder: "piano, guitare, batterie",
        tempoPlaceholder: "rapide, lent, modéré", bpmPlaceholder: "85", keyPlaceholder: "Do majeur, La mineur",
        vocalsPlaceholder: "vocal masculin chaud, vocal féminin brillant, duo", structurePlaceholder: "couplet-refrain-couplet-bridge-refrain",
        referencesPlaceholder: "similaire à...", avoidPlaceholder: "contenu explicite, auto-tune",
        useCasePlaceholder: "fond sonore vidéo, chanson thème", extraPlaceholder: "Notes supplémentaires",
        submit: "Générer la Musique", jobsTitle: "Tâches", jobsDesc: "Statut en temps réel. Le bouton de téléchargement apparaît quand le MP3 est prêt.",
        clearDraft: "Effacer le Brouillon", clearDraftConfirm: "Effacer le brouillon actuel ? Cela ne supprimera pas la musique générée.",
        draftSaved: "Brouillon enregistré", draftRestored: "Brouillon précédent restauré", draftCleared: "Brouillon effacé", draftRestoreFailed: "Impossible de restaurer le brouillon du serveur.",
        empty: "Pas encore de tâches. Remplissez le formulaire pour commencer à créer.", queued: "En attente", running: "En cours", completed: "Terminé", error: "Erreur", unknown: "Inconnu",
        download: "Télécharger MP3", delete: "Supprimer", sent: "Envoyé à", instrumentalMode: "Instrumental", vocalMode: "Vocal", deleteConfirm: "Supprimer cette tâche ?", deleteFailed: "Suppression échouée",
        navCreate: "Créer", navLibrary: "Bibliothèque", navFavorites: "Favoris", navHistory: "Historique", navPlaylists: "Playlists", playlistAll: "Toutes les Chansons", playlistRecent: "Joué Récemment",
        libraryDesc: "Toutes vos chansons générées au même endroit.", favoritesDesc: "Vos chansons aimées et sauvegardées.", historyDesc: "Chansons récemment générées.",
        toastMusicStarted: "Génération de musique lancée !", toastMusicReady: "Musique prête : ", toastLyricsSuccess: "Paroles générées avec succès !", toastLyricsError: "Échec de la génération des paroles.", toastVoiceCloneSuccess: "Clone vocal réussi !", toastVoiceCloneError: "Échec du clone vocal.",
        stemSplit: "Séparer l'Audio", stemSplitting: "Séparation...", stemDone: "Séparation Terminée", stemError: "Échec de Séparation",
        stemDrums: "Batterie", stemBass: "Basse", stemVocals: "Voix", stemOther: "Instrumental",
        stemDownload: "Télécharger", stemModalTitle: "Séparer les Stems Audio", stemModalDesc: "Téléchargez les pistes individuelles de votre chanson.",
        notificationsEnabled: "Notifications activées", notificationsDisabled: "Notifications désactivées", songReadyNotification: "Votre chanson "{title}" est prête !",
        playBtn: "▶ Lire", untitled: "Sans titre", audioFileRequired: "Veuillez sélectionner un fichier audio.",
        langBtnLabel: "FR",
        templateUpbeatPop: "Pop Animée", templateChillAmbient: "Ambient Détendue", templateRockAnthem: "Rock Hymne",
        templateAcousticStory: "Acoustique Narrative", templateElectronicDream: "Dream Électronique", templateHiphopBeats: "Beats Hip-Hop",
        templateCinematicEpic: "Épique Cinématique", templateLofiChill: "Lo-Fi Détendue"
      }
    };
    };

    const TEMPLATES = {
      upbeat_pop: { prompt: "Upbeat pop song with catchy melody, bright synthesizer, driving drum beat, feel-good energy, modern production, radio-ready", genre: "pop", mood: "happy, energetic", instruments: "synth, drums, bass, guitar" },
      chill_ambient: { prompt: "Chill ambient electronic music, soft pad drones, gentle arpeggios, relaxed atmosphere, meditative, soundscape", genre: "ambient, electronic", mood: "calm, peaceful", instruments: "synth pads, soft percussion" },
      rock_anthem: { prompt: "Epic rock anthem with powerful guitar riffs, driving bass, dynamic drums, anthemic choruses, stadium-ready energy", genre: "rock", mood: "powerful, energetic", instruments: "electric guitar, bass, drums" },
      acoustic_story: { prompt: "Acoustic folk ballad with warm guitar, gentle fingerpicking, intimate storytelling, heartfelt vocals, organic feel", genre: "folk, acoustic", mood: "warm, intimate, storytelling", instruments: "acoustic guitar, soft drums, harmonica" },
      electronic_dream: { prompt: "Dreamy electronic with lush synthesizers, ethereal pads, pulsating bass, futuristic textures, immersive atmosphere", genre: "electronic, synthwave", mood: "dreamy, futuristic", instruments: "synth, electronic drums, bass" },
      hiphop_beats: { prompt: "Modern hip-hop beat with punchy drums, deep 808 bass, atmospheric keys, laid-back groove, club-ready", genre: "hip-hop", mood: "cool, confident", instruments: "808 drums, synth, piano" },
      cinematic_epic: { prompt: "Cinematic epic orchestral with sweeping strings, powerful brass, epic percussion, emotional buildup, movie soundtrack quality", genre: "cinematic, orchestral", mood: "epic, dramatic", instruments: "orchestra, strings, brass, percussion" },
      lofi_chill: { prompt: "Lo-fi chillhop with vinyl crackle, mellow piano loops, laid-back drums, cozy atmosphere, study music vibes", genre: "lo-fi, chillhop", mood: "relaxed, cozy", instruments: "piano, vinyl, soft drums" }
    };

    let lang = "en";
    let lastJobs = [];
    // Set default prompt value if empty
    const promptEl = document.getElementById("prompt");
    if (!promptEl.value.trim()) {
      promptEl.value = "Upbeat pop song with catchy melody, bright synthesizer, driving drum beat";
    }
    const jobsBox = document.getElementById("jobs");
    const form = document.getElementById("jobForm");
    const submitBtn = document.getElementById("submitBtn");
    let submitBtnOriginalText = submitBtn.textContent;
    const clearDraftBtn = document.getElementById("clearDraftBtn");
    const formError = document.getElementById("formError");
    const draftStatus = document.getElementById("draftStatus");
    const instrumental = document.getElementById("instrumental");
    const lyricsOptimizer = document.getElementById("lyricsOptimizer");
    const lyrics = document.getElementById("lyrics");
    const lyricsIdea = document.getElementById("lyricsIdea");
    const generateLyricsBtn = document.getElementById("generateLyricsBtn");
    const lyricsAssistMessage = document.getElementById("lyricsAssistMessage");
    const voiceRecordBtn = document.getElementById("voiceRecordBtn");
    const voiceStatus = document.getElementById("voiceStatus");
    const voicePreviewRow = document.getElementById("voicePreviewRow");
    const voicePreviewBtn = document.getElementById("voicePreviewBtn");
    const voicePreviewAudio = document.getElementById("voicePreviewAudio");
    let clonedVoiceId = localStorage.getItem("terry_music_voice_id") || "";
    let voiceCloneExpires = localStorage.getItem("terry_music_voice_expires") || "";
    let refAudioFile = null;
    const clientId = (() => {
      const key = "terry_music_client_id";
      let id = localStorage.getItem(key);
      if (!id) {
        id = crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
        localStorage.setItem(key, id);
      }
      return id;
    })();
    const draftId = (() => {
      const key = "terry_music_draft_id";
      const params = new URLSearchParams(location.search);
      let id = params.get("draft") || localStorage.getItem(key);
      if (!/^[A-Za-z0-9._:-]{8,160}$/.test(id || "")) {
        id = crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
      }
      localStorage.setItem(key, id);
      if (params.get("draft") !== id) {
        const url = new URL(location.href);
        url.searchParams.set("draft", id);
        history.replaceState(null, "", url);
      }
      return id;
    })();
    const draftStorageKey = `terry_music_form_draft_${draftId}`;
    let draftTimer = null;
    let restoringDraft = false;

    function t(key) { return I18N[lang][key] || key; }
    function headers(extra = {}) { return {"X-Client-Id": clientId, ...extra}; }
    function escapeHtml(value) {
      return String(value ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#039;");
    }
    function applyLang() {
      document.documentElement.lang = lang;
      document.getElementById("langBtn").textContent = t("langBtnLabel");
      document.querySelectorAll("[data-i18n]").forEach(el => { el.textContent = t(el.dataset.i18n); });
      document.querySelectorAll("[data-i18n-placeholder]").forEach(el => { el.placeholder = t(el.dataset.i18nPlaceholder); });
      submitBtnOriginalText = submitBtn.textContent; // Update original text when language changes
      renderJobs(lastJobs);
    }
    function statusLabel(status) {
      return status === "completed" ? t("completed") : status === "running" ? t("running") : status === "queued" ? t("queued") : status === "error" ? t("error") : t("unknown");
    }
    function formatDate(value) {
      const date = new Date(value);
      if (Number.isNaN(date.getTime())) return "";
      return date.toLocaleString(lang === "en" ? "en-GB" : "zh-CN", {month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit"});
    }
    function renderJobs(jobs) {
      lastJobs = jobs || [];
      if (!lastJobs.length) {
        jobsBox.innerHTML = `<div class="job-empty">${t("empty")}</div>`;
        return;
      }
      jobsBox.innerHTML = lastJobs.map((job, idx) => {
        const status = escapeHtml(job.status || "unknown");
        const fileName = escapeHtml(job.file_name || "terry-music.mp3");
        const title = escapeHtml(job.song_title || job.prompt || t("untitled"));
        const mode = job.is_instrumental ? t("instrumentalMode") : t("vocalMode");
        const downloadUrl = job.download_url ? `${escapeHtml(job.download_url)}?client_id=${encodeURIComponent(clientId)}` : "";
        const isRunning = job.status === "running" || job.status === "queued";
        const completedClass = job.status === "completed" ? "animate-bounce-in" : "";
        const actions = job.status === "completed" && job.download_url
          ? `<button class="job-action-btn download" onclick="playJob('${escapeHtml(job.id)}')">${t("playBtn")}</button><a class="job-action-btn download" href="${downloadUrl}" download="${fileName}">${t("download")}</a>`
          : isRunning ? `<span style="font-size:12px;color:var(--text-muted);"><span class="spinner" style="width:12px;height:12px;border-width:1.5px;"></span> ${statusLabel(status)}...</span>` : "";
        return `<div class="job-card ${completedClass}" data-job-id="${escapeHtml(job.id)}" style="animation-delay:${idx * 50}ms">
          <div class="job-art">${job.status === "completed" ? "✅" : job.status === "error" ? "❌" : "🎵"}</div>
          <div class="job-info">
            <div class="job-title">${title}</div>
            <div class="job-meta"><span class="job-badge ${status}">${statusLabel(status)}</span><span>${mode}</span><span>${formatDate(job.created_at)}</span></div>
          </div>
          <div class="job-actions">${actions}</div>
        </div>`;
      }).join("");
    }
    function playJob(id) {
      const job = lastJobs.find(j => j.id === id);
      if (!job || !job.download_url) return;
      const url = job.download_url + (job.download_url.includes('?') ? '&' : '?') + 'client_id=' + encodeURIComponent(clientId);
      const lyrics = job.lyrics || "";
      currentTrack = { id: job.id, title: job.song_title || job.prompt || t('untitled'), url: url, lyrics: lyrics };
      audioPlayer.src = url;
      audioPlayer.play();
      updatePlayerUI();
    }
    async function loadJobs() {
      try {
        const res = await fetch("/api/jobs", {headers: headers(), cache: "no-store"});
        const data = await res.json();
        const prevJobs = window._prevJobs || {};
        const newJobs = data.jobs || [];
        // Check if jobs actually changed to avoid unnecessary DOM rebuilds
        const prevIds = new Set(Object.keys(prevJobs));
        const sameLength = prevIds.size === newJobs.length;
        let changed = !sameLength;
        if (sameLength) {
          for (const job of newJobs) {
            const prev = prevJobs[job.id];
            if (!prev || prev.status !== job.status || prev.updated_at !== job.updated_at) {
              changed = true;
              break;
            }
          }
        }
        if (!changed) return;
        // Play completion sound, show toast, and send browser notification when a job transitions to completed
        newJobs.forEach(job => {
          const prev = prevJobs[job.id];
          if (prev && prev.status !== "completed" && job.status === "completed") {
            SoundSystem.play("complete");
            showToast(t("toastMusicReady") + (job.song_title || job.prompt || t("untitled")), "success", 5000);
            notifySongReady(job.song_title || job.prompt || t("untitled"));
          }
        });
        window._prevJobs = Object.fromEntries(newJobs.map(j => [j.id, j]));
        renderJobs(newJobs);
      } catch {
        renderJobs([]);
      }
    }
    async function deleteJob(id) {
      if (!confirm(t("deleteConfirm"))) return;
      const res = await fetch(`/api/jobs/${encodeURIComponent(id)}`, {method: "DELETE", headers: headers()});
      if (!res.ok) alert(t("deleteFailed"));
      await loadJobs();
    }
    function collectPayload() {
      const get = id => { const el = document.getElementById(id); return el ? el.value.trim() : ""; };
      return {
        email: get("email"), song_title: get("songTitle"), prompt: get("prompt"), lyrics: get("lyrics"), lyrics_idea: get("lyricsIdea"),
        is_instrumental: instrumental.checked, lyrics_optimizer: lyricsOptimizer.checked,
        genre: get("genre"), mood: get("mood"), instruments: get("instruments"), tempo: get("tempo"), bpm: get("bpm"), key: get("key"),
        vocals: get("vocals"), structure: get("structure"), references: get("references"), avoid: get("avoid"), use_case: get("useCase"), extra: get("extra"),
        duration: get("duration"),
        voice_id: clonedVoiceId,
      };
    }
    function restorePayload(payload = {}) {
      const set = (id, value) => { const el = document.getElementById(id); if (el) el.value = value || ""; };
      set("email", payload.email);
      set("songTitle", payload.song_title);
      set("prompt", payload.prompt);
      set("lyricsIdea", payload.lyrics_idea);
      set("lyrics", payload.lyrics);
      set("genre", payload.genre);
      set("mood", payload.mood);
      set("instruments", payload.instruments);
      set("tempo", payload.tempo);
      set("bpm", payload.bpm);
      set("key", payload.key);
      set("vocals", payload.vocals);
      set("structure", payload.structure);
      set("references", payload.references);
      set("avoid", payload.avoid);
      set("useCase", payload.use_case);
      set("extra", payload.extra);
      instrumental.checked = Boolean(payload.is_instrumental);
      lyricsOptimizer.checked = Boolean(payload.lyrics_optimizer);
      syncInstrumentalFields();
    }
    function setDraftStatus(message) {
      draftStatus.textContent = message;
    }
    // Toast notification system
    function showToast(message, type = "info", duration = 3000) {
      const existing = document.getElementById("toast-container");
      if (existing) existing.remove();
      const container = document.createElement("div");
      container.id = "toast-container";
      container.style.cssText = "position:fixed;top:80px;right:20px;z-index:9999;display:flex;flex-direction:column;gap:8px;pointer-events:none;";
      const toast = document.createElement("div");
      const colors = { success: "var(--accent)", error: "var(--danger)", warning: "var(--warning)", info: "var(--text-secondary)" };
      const bgColors = { success: "rgba(29,185,84,0.15)", error: "rgba(255,82,82,0.15)", warning: "rgba(255,171,0,0.15)", info: "var(--bg-elevated)" };
      toast.style.cssText = `padding:12px 20px;background:${bgColors[type] || bgColors.info};border:1px solid ${colors[type] || colors.info};border-radius:var(--radius-md);color:${colors[type] || colors.info};font-size:13px;font-weight:600;animation:slide-down 0.3s ease-out;pointer-events:auto;max-width:300px;`;
      toast.textContent = message;
      container.appendChild(toast);
      document.body.appendChild(container);
      setTimeout(() => { toast.style.opacity = "0"; toast.style.transition = "opacity 0.3s"; setTimeout(() => container.remove(), 300); }, duration);
    }
    function saveDraftLocal(payload = collectPayload()) {
      localStorage.setItem(draftStorageKey, JSON.stringify({updated_at: new Date().toISOString(), draft: payload}));
    }
    async function saveDraftRemote(payload = collectPayload()) {
      await fetch(`/api/drafts/${encodeURIComponent(draftId)}`, {
        method: "POST",
        headers: headers({"Content-Type": "application/json"}),
        body: JSON.stringify(payload)
      });
    }
    function saveDraftSoon() {
      if (restoringDraft) return;
      const payload = collectPayload();
      saveDraftLocal(payload);
      clearTimeout(draftTimer);
      draftTimer = setTimeout(async () => {
        try {
          await saveDraftRemote(payload);
          setDraftStatus(t("draftSaved"));
        } catch {
          setDraftStatus(t("draftSaved"));
        }
      }, 1000); // Increased debounce to 1000ms to reduce server load
    }
    async function loadDraft() {
      restoringDraft = true;
      try {
        const local = JSON.parse(localStorage.getItem(draftStorageKey) || "null");
        if (local && local.draft) {
          restorePayload(local.draft);
          setDraftStatus(t("draftRestored"));
        }
      } catch {
        localStorage.removeItem(draftStorageKey);
      }
      try {
        const res = await fetch(`/api/drafts/${encodeURIComponent(draftId)}`, {headers: headers(), cache: "no-store"});
        const data = await res.json().catch(() => ({}));
        if (res.ok && data.draft) {
          restorePayload(data.draft);
          saveDraftLocal(data.draft);
          setDraftStatus(t("draftRestored"));
        }
      } catch {
        if (!draftStatus.textContent) setDraftStatus(t("draftRestoreFailed"));
      } finally {
        restoringDraft = false;
      }
    }
    function setLyricsAssistMessage(message, isError = false) {
      lyricsAssistMessage.textContent = message;
      lyricsAssistMessage.style.color = isError ? "var(--danger)" : "var(--muted)";
    }
    function syncInstrumentalFields() {
      const off = instrumental.checked;
      lyrics.disabled = off;
      lyricsIdea.disabled = off;
      lyricsOptimizer.disabled = off;
      generateLyricsBtn.disabled = off;
      if (off) lyricsOptimizer.checked = false;
    }
    instrumental.addEventListener("change", syncInstrumentalFields);
    generateLyricsBtn.addEventListener("click", async () => {
      SoundSystem.play("click");
      setLyricsAssistMessage("");
      const payload = collectPayload();
      if (!payload.prompt && !payload.lyrics_idea) {
        setLyricsAssistMessage(t("lyricsAssistNeedBrief"), true);
        return;
      }
      generateLyricsBtn.disabled = true;
      generateLyricsBtn.classList.add("animate-pulse");
      generateLyricsBtn.innerHTML = '<span class="spinner"></span> ' + t("generatingLyrics");
      try {
        const res = await fetch("/api/lyrics", {method: "POST", headers: headers({"Content-Type": "application/json"}), body: JSON.stringify(payload)});
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          const errMsg = typeof data.error === "string" ? data.error : data.error?.message || data.error?.error || t("lyricsAssistFailed");
          throw new Error(errMsg);
        }
        lyrics.value = data.lyrics || "";
        saveDraftSoon();
        setLyricsAssistMessage(t("lyricsGenerated"));
        showToast(t("toastLyricsSuccess"), "success");
        generateLyricsBtn.classList.remove("animate-pulse");
        generateLyricsBtn.classList.add("animate-bounce-in");
        setTimeout(() => generateLyricsBtn.classList.remove("animate-bounce-in"), 500);
      } catch (error) {
        setLyricsAssistMessage(error.message || t("lyricsAssistFailed"), true);
        showToast(t("toastLyricsError"), "error");
        generateLyricsBtn.classList.remove("animate-pulse");
        generateLyricsBtn.classList.add("animate-shake");
        setTimeout(() => generateLyricsBtn.classList.remove("animate-shake"), 400);
        SoundSystem.play("error");
      } finally {
        generateLyricsBtn.textContent = t("generateLyrics");
        generateLyricsBtn.disabled = instrumental.checked;
      }
    });
    voicePreviewBtn.addEventListener("click", async () => {
      const currentLyrics = lyrics.value.trim();
      if (!currentLyrics) {
        voiceStatus.textContent = t("lyricsAssistNeedBrief");
        voiceStatus.style.color = "var(--danger)";
        return;
      }
      if (!clonedVoiceId) {
        voiceStatus.textContent = t("voiceError");
        voiceStatus.style.color = "var(--danger)";
        return;
      }
      voicePreviewBtn.disabled = true;
      voicePreviewBtn.textContent = t("voicePreviewGenerating");
      try {
        const res = await fetch("/api/voice/sing", {
          method: "POST",
          headers: headers({"Content-Type": "application/json"}),
          body: JSON.stringify({lyrics: currentLyrics, voice_id: clonedVoiceId}),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          const errMsg = typeof data.error === "string" ? data.error : data.error?.message || t("voicePreviewError");
          throw new Error(errMsg);
        }
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        voicePreviewAudio.src = url;
        voicePreviewAudio.style.display = "inline-block";
        voiceStatus.textContent = t("voicePreviewReady");
        voiceStatus.style.color = "var(--accent)";
      } catch (err) {
        voiceStatus.textContent = err.message || t("voicePreviewError");
        voiceStatus.style.color = "var(--danger)";
      } finally {
        voicePreviewBtn.textContent = t("voicePreviewBtn");
        voicePreviewBtn.disabled = false;
      }
    });
    if (clonedVoiceId && voiceCloneExpires && parseInt(voiceCloneExpires) > Date.now()) {
      voicePreviewRow.style.display = "flex";
      voiceStatus.textContent = t("voiceReady");
      voiceStatus.style.color = "var(--accent)";
    }
    document.getElementById("langBtn").addEventListener("click", () => {
      lang = lang === "en" ? "zh" : "en";
      applyLang();
    });
    const themeBtn = document.getElementById("themeBtn");
    const themeMenu = document.getElementById("themeMenu");
    const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;

    function getEffectiveTheme(saved) {
      if (saved === "light" || saved === "dark") return saved;
      return prefersDark ? "dark" : "light";
    }

    function setTheme(theme) {
      const effective = theme === "" ? (prefersDark ? "dark" : "light") : theme;
      document.documentElement.setAttribute("data-theme", theme === "" ? effective : theme);
      localStorage.setItem("terry_music_theme", theme);
      themeBtn.textContent = theme === "light" ? "☀️" : (theme === "dark" ? "🌙" : "💻");
      document.querySelectorAll(".theme-menu-item").forEach(item => {
        item.classList.toggle("active", item.dataset.themeValue === theme);
      });
    }

    const savedTheme = localStorage.getItem("terry_music_theme") ?? "";
    setTheme(savedTheme);

    themeBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      SoundSystem.play("click");
      themeMenu.classList.toggle("open");
    });

    document.addEventListener("click", () => themeMenu.classList.remove("open"));

    document.querySelectorAll(".theme-menu-item").forEach(item => {
      item.addEventListener("click", (e) => {
        e.stopPropagation();
        SoundSystem.play("click");
        setTheme(item.dataset.themeValue);
        themeMenu.classList.remove("open");
      });
    });
    // Sound toggle
    function toggleSound() {
      const enabled = SoundSystem.toggle();
      document.getElementById("soundBtn").textContent = enabled ? "🔊" : "🔇";
      document.getElementById("soundBtn").className = "header-btn sound-toggle " + (enabled ? "on" : "off");
    }
    function toggleNotifications() {
      if (!("Notification" in window)) { showToast(t("notificationsDisabled"), "warning"); return; }
      const btn = document.getElementById("notifyBtn");
      if (Notification.permission === "granted") {
        const enabled = localStorage.getItem("terry_music_notifications") !== "false";
        localStorage.setItem("terry_music_notifications", String(!enabled));
        updateNotifyBtn(!enabled);
        showToast(!enabled ? t("notificationsDisabled") : t("notificationsEnabled"), "success");
      } else if (Notification.permission === "denied") {
        showToast(t("notificationsDisabled"), "warning");
      } else {
        Notification.requestPermission().then(perm => {
          if (perm === "granted") {
            localStorage.setItem("terry_music_notifications", "true");
            updateNotifyBtn(true);
            showToast(t("notificationsEnabled"), "success");
          }
        });
      }
    }
    function updateNotifyBtn(enabled) {
      const btn = document.getElementById("notifyBtn");
      if (!btn) return;
      btn.textContent = enabled ? "🔔" : "🔕";
      btn.className = "header-btn notify-toggle " + (enabled ? "on" : "off");
    }
    function notifySongReady(title) {
      if (!("Notification" in window)) return;
      if (localStorage.getItem("terry_music_notifications") === "false") return;
      if (Notification.permission !== "granted") return;
      if (document.hasFocus()) return;
      const body = t("songReadyNotification").replace("{title}", title);
      new Notification("Music Speaks", { body: body, icon: "/favicon.ico" });
    }
    // Advanced panel toggle
    const advancedToggle = document.getElementById("advancedToggle");
    const advancedPanel = document.getElementById("advancedPanel");
    advancedToggle.addEventListener("click", () => {
      advancedPanel.classList.toggle("open");
      const icon = advancedToggle.querySelector(".advanced-toggle-icon");
      icon.textContent = advancedPanel.classList.contains("open") ? "▲" : "▼";
    });
    // Navigation
    document.querySelectorAll(".nav-item").forEach(item => {
      item.addEventListener("click", (e) => {
        e.preventDefault();
        SoundSystem.play("click");
        const view = item.dataset.view;
        document.querySelectorAll(".nav-item").forEach(n => n.classList.remove("active"));
        item.classList.add("active");
        document.querySelectorAll("[id^='view-']").forEach(v => v.style.display = "none");
        const viewEl = document.getElementById("view-" + view);
        if (viewEl) viewEl.style.display = "block";
        if (view === "library" || view === "favorites" || view === "history") {
          loadJobs();
        }
      });
    });
    // Player
    const audioPlayer = new Audio();
    let currentTrack = null;
    const player = document.getElementById("player");
    const playerTitle = document.getElementById("playerTitle");
    const playerArtist = document.getElementById("playerArtist");
    const playerPlay = document.getElementById("playerPlay");
    const playerBar = document.getElementById("playerBar");
    const playerBarFill = document.getElementById("playerBarFill");
    const playerCurrentTime = document.getElementById("playerCurrentTime");
    const playerDuration = document.getElementById("playerDuration");
    const volumeFill = document.getElementById("volumeFill");
    const lyricsText = document.getElementById("lyricsText");
    function updatePlayerUI() {
      if (!currentTrack) { player.style.display = "none"; return; }
      player.style.display = "flex";
      playerTitle.textContent = currentTrack.title;
      playerArtist.textContent = "Music Speaks";
      playerPlay.textContent = audioPlayer.paused ? "▶" : "⏸";
      lyricsText.textContent = currentTrack.lyrics ? "♪ " + currentTrack.lyrics.split("\n")[0] + " ♪" : "♪ Lyrics ♪";
      lyricsText.className = audioPlayer.paused ? "lyrics-text" : "lyrics-text playing";
    }
    audioPlayer.addEventListener("timeupdate", () => {
      if (!audioPlayer.duration) return;
      const pct = (audioPlayer.currentTime / audioPlayer.duration) * 100;
      playerBarFill.style.width = pct + "%";
      playerCurrentTime.textContent = formatTime(audioPlayer.currentTime);
      playerDuration.textContent = formatTime(audioPlayer.duration);
      // Update lyrics based on progress
      if (currentTrack && currentTrack.lyrics) {
        const lines = currentTrack.lyrics.split("\n");
        const lineIndex = Math.floor((audioPlayer.currentTime / audioPlayer.duration) * lines.length);
        const safeIndex = Math.max(0, Math.min(lineIndex, lines.length - 1));
        const line = lines[safeIndex].replace(/\[.*?\]/g, "").trim() || "♪ " + lines[safeIndex] + " ♪";
        lyricsText.textContent = line;
      }
    });
    audioPlayer.addEventListener("ended", () => { playerPlay.textContent = "▶"; lyricsText.className = "lyrics-text"; });
    playerPlay.addEventListener("click", () => {
      if (audioPlayer.paused) audioPlayer.play(); else audioPlayer.pause();
      playerPlay.textContent = audioPlayer.paused ? "▶" : "⏸";
      lyricsText.className = audioPlayer.paused ? "lyrics-text" : "lyrics-text playing";
    });
    playerBar.addEventListener("click", (e) => {
      if (!audioPlayer.duration) return;
      const rect = playerBar.getBoundingClientRect();
      const pct = (e.clientX - rect.left) / rect.width;
      audioPlayer.currentTime = pct * audioPlayer.duration;
    });
    volumeFill.style.width = "70%";
    audioPlayer.volume = 0.7;
    function formatTime(secs) {
      if (!secs || isNaN(secs)) return "0:00";
      const m = Math.floor(secs / 60);
      const s = Math.floor(secs % 60);
      return m + ":" + s.toString().padStart(2, "0");
    }
    document.querySelectorAll(".template-btn").forEach(btn => {
      btn.addEventListener("click", () => {
        const key = btn.dataset.template;
        const tmpl = TEMPLATES[key];
        if (!tmpl) return;
        document.getElementById("prompt").value = tmpl.prompt;
        if (tmpl.genre) document.getElementById("genre").value = tmpl.genre;
        if (tmpl.mood) document.getElementById("mood").value = tmpl.mood;
        if (tmpl.instruments) document.getElementById("instruments").value = tmpl.instruments;
        document.querySelectorAll(".template-btn").forEach(b => b.classList.remove("active"));
        btn.classList.add("active");
        saveDraftSoon();
      });
    });
    form.addEventListener("input", saveDraftSoon);
    form.addEventListener("change", saveDraftSoon);
    clearDraftBtn.addEventListener("click", async () => {
      if (!confirm(t("clearDraftConfirm"))) return;
      clearTimeout(draftTimer);
      form.reset();
      formError.textContent = "";
      setLyricsAssistMessage("");
      localStorage.removeItem(draftStorageKey);
      try {
        await fetch(`/api/drafts/${encodeURIComponent(draftId)}`, {method: "DELETE", headers: headers()});
      } catch {}
      applyLang();
      syncInstrumentalFields();
      setDraftStatus(t("draftCleared"));
    });
    form.addEventListener("submit", async event => {
      event.preventDefault();
      SoundSystem.play("click");
      formError.textContent = "";
      submitBtn.disabled = true;
      const startTime = Date.now();
      let elapsed = 0;
      submitBtn.classList.add("animate-pulse");
      submitBtn.innerHTML = '<span class="spinner"></span> Generating... 0s';
      const payload = collectPayload();
      if (parseInt(payload.duration || "30") > 30) {
        showToast(lang === "en" ? "Extended duration selected — generation will take longer than usual." : "已选择延长时长 — 生成时间会比平时更长。", "info", 5000);
      }
      let endpoint = clonedVoiceId ? "/api/jobs/voice" : "/api/jobs";
      let useFormData = false;
      if (refAudioFile) {
        endpoint = "/api/jobs/audio-to-audio";
        useFormData = true;
      }
      let currentJobId = null;
      // Progress updater: update button text with elapsed time and poll job status
      const progressTimer = setInterval(async () => {
        elapsed = Math.round((Date.now() - startTime) / 1000);
        submitBtn.innerHTML = `<span class="spinner"></span> Generating... ${elapsed}s`;
        if (currentJobId) {
          try {
            const r = await fetch(`/api/jobs/${currentJobId}`, {headers: headers()});
            if (r.ok) {
              const j = await r.json();
              if (j.status === "completed") {
                clearInterval(progressTimer);
                submitBtn.classList.remove("animate-pulse");
                submitBtn.classList.add("animate-bounce-in");
                setTimeout(() => submitBtn.classList.remove("animate-bounce-in"), 500);
                submitBtn.disabled = false;
                submitBtn.innerHTML = submitBtnOriginalText;
                loadJobs();
                return;
              }
              if (j.status === "error") {
                clearInterval(progressTimer);
                submitBtn.classList.remove("animate-pulse");
                submitBtn.classList.add("animate-shake");
                setTimeout(() => submitBtn.classList.remove("animate-shake"), 400);
                submitBtn.disabled = false;
                submitBtn.innerHTML = submitBtnOriginalText;
                return;
              }
            }
          } catch {}
        }
      }, 2000);
      try {
        let res;
        if (useFormData) {
          const fd = new FormData();
          fd.append("audio", refAudioFile, refAudioFile.name);
          const refModeInput = document.querySelector("input[name='refMode']:checked");
          fd.append("ref_mode", refModeInput ? refModeInput.value : "style");
          fd.append("payload", JSON.stringify(payload));
          res = await fetch(endpoint, {method: "POST", headers: headers(), body: fd});
        } else {
          res = await fetch(endpoint, {method: "POST", headers: headers({"Content-Type": "application/json"}), body: JSON.stringify(payload)});
        }
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          clearInterval(progressTimer);
          const errMsg = typeof data.error === "string" ? data.error : data.error?.message || data.error?.error || `HTTP ${res.status}`;
          throw new Error(errMsg);
        }
        currentJobId = data.job?.id || null;
        saveDraftLocal(payload);
        await saveDraftRemote(payload).catch(() => {});
        setDraftStatus(t("draftSaved"));
        showToast(t("toastMusicStarted"), "success");
        applyLang();
        syncInstrumentalFields();
        await loadJobs();
        SoundSystem.play("success");
      } catch (error) {
        clearInterval(progressTimer);
        formError.textContent = error.message;
        showToast(error.message, "error");
        submitBtn.classList.remove("animate-pulse");
        submitBtn.classList.add("animate-shake");
        setTimeout(() => submitBtn.classList.remove("animate-shake"), 400);
        SoundSystem.play("error");
        submitBtn.disabled = false;
        submitBtn.innerHTML = submitBtnOriginalText;
      }
    });
    applyLang();
    loadDraft();
    loadJobs();
    setInterval(loadJobs, 5000);
    // Play startup sound on first load (user interaction required for audio)
    document.addEventListener("click", function startupSound() {
      SoundSystem.play("startup");
      document.removeEventListener("click", startupSound);
    }, { once: true });

    const VOICE_SEGMENTS_EN = [
      { label: "Low Voice", desc: "Speak in a calm, deep, low voice." },
      { label: "Normal Speech", desc: "Speak naturally at your normal pitch and pace." },
      { label: "High Pitch", desc: "Raise your voice and speak in a bright, high tone." },
      { label: "Whisper", desc: "Speak very softly — a quiet, intimate whisper." },
      { label: "Natural Close", desc: "Speak your natural closing words, relaxed and clear." },
    ];
    const VOICE_SEGMENTS_ZH = [
      { label: "低音", desc: "用平静、低沉的声音说话。" },
      { label: "正常念白", desc: "用正常的音高和语速自然说话。" },
      { label: "高音", desc: "提高音量，用明亮高亢的声调说话。" },
      { label: "小声低语", desc: "非常轻柔地说话——像悄悄话。" },
      { label: "自然收尾", desc: "用放松自然的声音说结束的句子。" },
    ];
    const SEGMENT_SCRIPTS_EN = [
      "Hello, my name is Alex. I speak in a calm, low, and steady voice.",
      "Today is a beautiful day and I feel really happy and grateful.",
      "Can you hear me all the way in the back of the room?",
      "This is a secret between us, please don't tell anyone.",
      "Thank you for listening. This is my voice, unique and real.",
    ];
    const SEGMENT_SCRIPTS_ZH = [
      "你好，我的名字是阿明，我用平静低沉的声音说话。",
      "今天是美好的一天，我感到非常开心和感恩。",
      "在后排的你能听到我说话吗？",
      "这是我们之间的秘密，请不要告诉任何人。",
      "感谢聆听。这就是我的声音，独一无二，真实自然。",
    ];

    let mediaRecorder = null;
    let recordedChunks = [];
    let recordedSegments = [];
    let currentSegment = -1;
    let segmentStream = null;
    let recordingTimer = null;
    let countdownInterval = null;
    const SEGMENT_DURATION = 5000; // 5s per segment

    function getSegments() {
      return lang === "zh" ? VOICE_SEGMENTS_ZH : VOICE_SEGMENTS_EN;
    }
    function getScripts() {
      return lang === "zh" ? SEGMENT_SCRIPTS_ZH : SEGMENT_SCRIPTS_EN;
    }

    function openVoiceRecorder() {
      recordedSegments = [];
      currentSegment = -1;
      const segs = getSegments();
      const scrs = getScripts();
      const container = document.getElementById("recModalBody");
      container.innerHTML = `<div class="rec-progress"><div class="rec-step">${lang === "en" ? "Preparing..." : "准备中..."}</div></div><div class="rec-script-box"></div><div class="rec-controls-row"><button id="recModalClose" class="secondary-btn" type="button">${lang === "en" ? "Cancel" : "取消"}</button></div>`;
      document.getElementById("recModal").style.display = "flex";
      document.getElementById("recModalClose").addEventListener("click", closeVoiceRecorder);
      setTimeout(() => showSegment(0), 300);
    }

    function closeVoiceRecorder() {
      if (mediaRecorder && mediaRecorder.state !== "inactive") mediaRecorder.stop();
      if (segmentStream) { segmentStream.getTracks().forEach(t => t.stop()); segmentStream = null; }
      clearTimeout(recordingTimer);
      recordingTimer = null;
      if (countdownInterval) { clearInterval(countdownInterval); countdownInterval = null; }
      document.getElementById("recModal").style.display = "none";
    }

    function showSegment(idx) {
      // Clean up any existing timers and media streams before starting new segment
      if (countdownInterval) { clearInterval(countdownInterval); countdownInterval = null; }
      if (recordingTimer) { clearTimeout(recordingTimer); recordingTimer = null; }
      if (mediaRecorder && mediaRecorder.state !== "inactive") mediaRecorder.stop();
      if (segmentStream) { segmentStream.getTracks().forEach(t => t.stop()); segmentStream = null; }
      currentSegment = idx;
      const segs = getSegments();
      const scrs = getScripts();
      const seg = segs[idx];
      const script = scrs[idx];
      const total = segs.length;
      const progress = ((idx + 1) / total) * 100;
      const body = document.getElementById("recModalBody");
      body.innerHTML = `
        <div class="rec-progress">
          <div class="rec-step">${lang === "en" ? "Segment" : "段落"} ${idx + 1} / ${total} — ${seg.label}</div>
          <div class="rec-bar"><div class="rec-bar-fill" style="width:${progress}%"></div></div>
        </div>
        <div class="rec-script-box">
          <div class="rec-instruction">${seg.desc}</div>
          <div class="rec-script">"${script}"</div>
        </div>
        <div class="rec-countdown" id="recCountdown">${lang === "en" ? "Starting in 3..." : "3秒后开始..."}</div>
        <div class="rec-controls-row">
          <button id="recStartSeg" class="secondary-btn" type="button">${lang === "en" ? "Start Recording" : "开始录制"}</button>
          <button id="recModalClose" class="ghost" type="button">${lang === "en" ? "Cancel" : "取消"}</button>
        </div>
      `;
      document.getElementById("recStartSeg").addEventListener("click", () => showCountdownAndRecord(idx));
      document.getElementById("recModalClose").addEventListener("click", closeVoiceRecorder);
    }

    function showCountdownAndRecord(idx) {
      let count = 3;
      const countdownEl = document.getElementById("recCountdown");
      countdownInterval = setInterval(() => {
        count--;
        if (count > 0) {
          countdownEl.textContent = (lang === "en" ? `Starting in ${count}...` : `${count}秒后开始...`);
        } else {
          clearInterval(countdownInterval);
          countdownInterval = null;
          countdownEl.textContent = "";
          startRecordingSegment(idx);
        }
      }, 1000);
    }

    async function startSegmentRecording(idx) {
      try {
        segmentStream = await navigator.mediaDevices.getUserMedia({ audio: true, sampleRate: 16000 });
        // Always use audio/webm — it is the most reliable cross-browser format for MediaRecorder
        const mimeType = "audio/webm";
        mediaRecorder = new MediaRecorder(segmentStream, { mimeType });
        recordedChunks = [];
        // Use timeslice to fire ondataavailable every 100ms for reliable data collection
        mediaRecorder.ondataavailable = e => { if (e.data.size > 0) recordedChunks.push(e.data); };
        mediaRecorder.onstop = async () => {
          if (recordedChunks.length === 0) {
            alert(lang === "en" ? "Recording failed — no audio data captured. Please try again." : "录音失败 — 未捕获到音频数据，请重试。");
            closeVoiceRecorder();
            return;
          }
          const rawBlob = new Blob(recordedChunks, { type: mimeType });
          if (rawBlob.size < 1000) {
            alert(lang === "en" ? "Recording too small — check microphone. Please try again." : "录音文件过小 — 请检查麦克风后重试。");
            closeVoiceRecorder();
            return;
          }
          const wavBlob = await convertToWav(rawBlob);
          recordedSegments[idx] = wavBlob;
          segmentStream.getTracks().forEach(t => t.stop());
          segmentStream = null;
          if (idx + 1 < getSegments().length) {
            showReview(idx, wavBlob);
          } else {
            showAllDone();
          }
        };
        mediaRecorder.start(100); // timeslice=100ms ensures regular data events
        document.getElementById("recStartSeg").disabled = true;
        document.getElementById("recStartSeg").textContent = lang === "en" ? "Recording..." : "录制中...";
        const countdownEl = document.getElementById("recCountdown");
        let remaining = 5;
        countdownEl.textContent = lang === "en" ? `Recording... ${remaining}s` : `录制中... ${remaining}s`;
        recordingTimer = setInterval(() => {
          remaining--;
          if (remaining > 0) {
            countdownEl.textContent = lang === "en" ? `Recording... ${remaining}s` : `录制中... ${remaining}s`;
          }
        }, 1000);
        setTimeout(() => { if (mediaRecorder.state === "recording") mediaRecorder.stop(); }, SEGMENT_DURATION);
      } catch (err) {
        alert(lang === "en" ? "Microphone access denied. Please allow microphone access." : "麦克风访问被拒绝，请允许麦克风权限。");
        closeVoiceRecorder();
      }
    }

    async function convertToWav(blob) {
      const arrayBuffer = await blob.arrayBuffer();
      const audioCtx = new AudioContext({ sampleRate: 16000 });
      const audioBuffer = await audioCtx.decodeAudioData(arrayBuffer);
      const numChannels = 1;
      const sampleRate = 16000;
      const bitsPerSample = 16;
      const bytesPerSample = bitsPerSample / 8;
      const blockAlign = numChannels * bytesPerSample;
      const byteRate = sampleRate * blockAlign;
      const dataLength = Math.ceil(audioBuffer.length) * numChannels * bytesPerSample;
      const headerLength = 44;
      const totalLength = headerLength + dataLength;
      const buffer = new ArrayBuffer(totalLength);
      const view = new DataView(buffer);
      const writeStr = (offset, str) => { for (let i = 0; i < str.length; i++) view.setUint8(offset + i, str.charCodeAt(i)); };
      writeStr(0, "RIFF"); view.setUint32(4, totalLength - 8, true); writeStr(8, "WAVE");
      writeStr(12, "fmt "); view.setUint32(16, 16, true); view.setUint16(20, 1, true);
      view.setUint16(22, numChannels, true); view.setUint32(24, sampleRate, true);
      view.setUint32(28, byteRate, true); view.setUint16(32, blockAlign, true);
      view.setUint16(34, bitsPerSample, true);
      writeStr(36, "data"); view.setUint32(40, dataLength, true);
      const channelData = audioBuffer.getChannelData(0);
      let offset = 44;
      for (let i = 0; i < audioBuffer.length; i++) {
        const s = Math.max(-1, Math.min(1, channelData[i]));
        view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7FFF, true);
        offset += 2;
      }
      audioCtx.close();
      return new Blob([buffer], { type: "audio/wav" });
    }

    function startRecordingSegment(idx) {
      startSegmentRecording(idx);
    }

    function showReview(idx, blob) {
      const segs = getSegments();
      const scrs = getScripts();
      const seg = segs[idx];
      const script = scrs[idx];
      const url = URL.createObjectURL(blob);
      const body = document.getElementById("recModalBody");
      body.innerHTML = `
        <div class="rec-progress">
          <div class="rec-step">${lang === "en" ? "Segment" : "段落"} ${idx + 1} / ${segs.length} — ${seg.label} ✓</div>
          <div class="rec-bar"><div class="rec-bar-fill" style="width:${((idx + 1) / segs.length) * 100}%"></div></div>
        </div>
        <div class="rec-script-box">
          <div class="rec-instruction">${seg.desc}</div>
          <div class="rec-script">"${script}"</div>
        </div>
        <div class="rec-review-audio"><audio src="${url}" controls style="height:40px; width:100%;"></audio></div>
        <div class="rec-controls-row">
          <button id="recRerecord" class="ghost" type="button">${lang === "en" ? "🔄 Re-record" : "🔄 重新录制"}</button>
          <button id="recNext" class="secondary-btn" type="button">${lang === "en" ? "Next →" : "下一个 →"}</button>
        </div>
      `;
      document.getElementById("recRerecord").addEventListener("click", () => showSegment(idx));
      document.getElementById("recNext").addEventListener("click", () => showSegment(idx + 1));
    }

    async function showAllDone() {
      const body = document.getElementById("recModalBody");
      body.innerHTML = `<div class="rec-done">${lang === "en" ? "All recordings complete! Merging..." : "全部录制完成！正在合并..."}</div>`;
      try {
        const combined = await mergeAudioBlobs(recordedSegments);
        const fd = new FormData();
        fd.append("audio", combined, "voice_sample.wav");
        voiceStatus.textContent = lang === "en" ? "Uploading & cloning..." : "上传中并复刻声音...";
        voiceStatus.style.color = "var(--muted)";
        const res = await fetch("/api/voice/clone", { method: "POST", headers: headers(), body: fd });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          const errMsg = typeof data.error === "string" ? data.error : data.error?.message || (lang === "en" ? "Clone failed." : "声音复刻失败。");
          throw new Error(errMsg);
        }
        clonedVoiceId = data.voice_id || "";
        const expiresHours = data.expires_in_hours || 168;
        const expiresAt = Date.now() + expiresHours * 3600 * 1000;
        localStorage.setItem("terry_music_voice_id", clonedVoiceId);
        localStorage.setItem("terry_music_voice_expires", String(expiresAt));
        if (data.voice_wav_path) localStorage.setItem("terry_music_voice_wav", data.voice_wav_path);
        voicePreviewRow.style.display = "flex";
        closeVoiceRecorder();
        voiceStatus.textContent = lang === "en" ? "Voice cloned! Use Preview to listen." : "声音复刻完成！点击预览试听。";
        voiceStatus.style.color = "var(--accent)";
        voiceStatus.classList.add("animate-bounce-in");
        SoundSystem.play("success");
        showToast(t("toastVoiceCloneSuccess"), "success");
      } catch (err) {
        body.innerHTML = `<div class="rec-done rec-error">${lang === "en" ? "Clone failed: " : "复刻失败："}${err.message}</div><div class="rec-controls-row"><button id="recModalClose2" class="secondary-btn" type="button">${lang === "en" ? "Close" : "关闭"}</button></div>`;
        SoundSystem.play("error");
        showToast(t("toastVoiceCloneError") + " " + err.message, "error");
        document.getElementById("recModalClose2").addEventListener("click", closeVoiceRecorder);
      }
    }

    async function mergeAudioBlobs(blobs) {
      const SAMPLE_RATE = 16000;
      const NUM_CHANNELS = 1;
      const BITS_PER_SAMPLE = 16;
      const BYTES_PER_SAMPLE = BITS_PER_SAMPLE / 8;
      let totalSamples = 0;
      const pcmBuffers = [];
      for (const blob of blobs) {
        const ab = await blob.arrayBuffer();
        const view = new DataView(ab);
        let offset = 0;
        while (offset + 44 <= ab.byteLength) {
          const tag = String.fromCharCode(view.getUint8(offset), view.getUint8(offset + 1), view.getUint8(offset + 2), view.getUint8(offset + 3));
          if (tag !== "RIFF") break;
          const chunkSize = view.getUint32(offset + 4, true);
          const wave = String.fromCharCode(view.getUint8(offset + 8), view.getUint8(offset + 9), view.getUint8(offset + 10), view.getUint8(offset + 11));
          if (wave !== "WAVE") break;
          let dataOffset = offset + 12;
          while (dataOffset + 8 < offset + 8 + chunkSize) {
            const subTag = String.fromCharCode(view.getUint8(dataOffset), view.getUint8(dataOffset + 1), view.getUint8(dataOffset + 2), view.getUint8(dataOffset + 3));
            const subSize = view.getUint32(dataOffset + 4, true);
            if (subTag === "data") {
              const pcmStart = dataOffset + 8;
              const pcmEnd = Math.min(pcmStart + subSize, ab.byteLength);
              const pcmBytes = new Uint8Array(ab).slice(pcmStart, pcmEnd);
              pcmBuffers.push(pcmBytes);
              totalSamples += (pcmEnd - pcmStart) / BYTES_PER_SAMPLE;
              dataOffset = pcmEnd;
            } else {
              dataOffset += 8 + subSize;
            }
          }
          break;
        }
      }
      const dataLength = totalSamples * NUM_CHANNELS * BYTES_PER_SAMPLE;
      const totalLength = 44 + dataLength;
      const out = new ArrayBuffer(totalLength);
      const v = new DataView(out);
      const ws = (o, s) => { for (let i = 0; i < s.length; i++) v.setUint8(o + i, s.charCodeAt(i)); };
      ws(0, "RIFF"); v.setUint32(4, totalLength - 8, true); ws(8, "WAVE"); ws(12, "fmt ");
      v.setUint32(16, 16, true); v.setUint16(20, 1, true);
      v.setUint16(22, NUM_CHANNELS, true); v.setUint32(24, SAMPLE_RATE, true);
      v.setUint32(28, SAMPLE_RATE * NUM_CHANNELS * BYTES_PER_SAMPLE, true);
      v.setUint16(32, NUM_CHANNELS * BYTES_PER_SAMPLE, true);
      v.setUint16(34, BITS_PER_SAMPLE, true); ws(36, "data"); v.setUint32(40, dataLength, true);
      let offset = 44;
      for (const buf of pcmBuffers) {
        new Uint8Array(out).set(buf, offset);
        offset += buf.byteLength;
      }
      return new Blob([out], { type: "audio/wav" });
    }

    document.getElementById("voiceRecordBtn").addEventListener("click", () => {
      SoundSystem.play("click");
      if (clonedVoiceId && voiceCloneExpires && parseInt(voiceCloneExpires) > Date.now()) {
        if (confirm(lang === "en" ? "Re-record voice? This will create a new voice clone." : "重新录制？这将创建新的声音复刻。")) {
          localStorage.removeItem("terry_music_voice_id");
          localStorage.removeItem("terry_music_voice_expires");
          clonedVoiceId = "";
          openVoiceRecorder();
        }
      } else {
        openVoiceRecorder();
      }
    });

    // Reference Audio handling
    const refAudioDropzone = document.getElementById("refAudioDropzone");
    const refAudioFileInput = document.getElementById("refAudioFile");
    const refAudioInfo = document.getElementById("refAudioInfo");
    const refAudioPreview = document.getElementById("refAudioPreview");
    const refAudioRemove = document.getElementById("refAudioRemove");
    const refAudioMode = document.getElementById("refAudioMode");

    function handleRefAudioFile(file) {
      if (!file || !file.type.startsWith("audio/")) {
        showToast(t("audioFileRequired"), "error");
        return;
      }
      refAudioFile = file;
      const url = URL.createObjectURL(file);
      refAudioPreview.src = url;
      refAudioDropzone.style.display = "none";
      refAudioInfo.style.display = "flex";
      refAudioMode.style.display = "flex";
    }

    refAudioDropzone.addEventListener("click", () => refAudioFileInput.click());
    refAudioDropzone.addEventListener("dragover", (e) => {
      e.preventDefault();
      refAudioDropzone.classList.add("dragover");
    });
    refAudioDropzone.addEventListener("dragleave", () => refAudioDropzone.classList.remove("dragover"));
    refAudioDropzone.addEventListener("drop", (e) => {
      e.preventDefault();
      refAudioDropzone.classList.remove("dragover");
      const file = e.dataTransfer.files[0];
      handleRefAudioFile(file);
    });
    refAudioFileInput.addEventListener("change", () => {
      const file = refAudioFileInput.files[0];
      handleRefAudioFile(file);
    });
    refAudioRemove.addEventListener("click", () => {
      refAudioFile = null;
      refAudioFileInput.value = "";
      refAudioPreview.src = "";
      refAudioDropzone.style.display = "block";
      refAudioInfo.style.display = "none";
      refAudioMode.style.display = "none";
    });
  <!-- Stem Separation Modal -->
  <div id="stemsModal" class="modal-overlay" style="display:none;">
    <div class="modal-content">
      <div class="modal-header">
        <h3 class="modal-title" id="stemsModalTitle" data-i18n="stemModalTitle">Split Audio Stems</h3>
        <button class="modal-close" onclick="closeStemsModal()">✕</button>
      </div>
      <div class="modal-body" id="stemsModalBody">
        <p style="color:var(--text-secondary);font-size:13px;margin-bottom:16px;" id="stemsModalDesc" data-i18n="stemModalDesc">Download individual tracks from your song.</p>
        <div id="stemsStatus" style="margin-bottom:16px;"></div>
        <div id="stemsList" class="stems-grid"></div>
        <div id="stemsError" style="color:var(--danger);font-size:13px;margin-top:12px;display:none;"></div>
      </div>
    </div>
  </div>
  <style>
    .stems-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px; }
    .stem-card { display: flex; align-items: center; gap: 12px; padding: 14px 16px; background: var(--bg-tertiary); border: 1px solid var(--border); border-radius: var(--radius-md); }
    .stem-card:hover { border-color: var(--accent); }
    .stem-icon { font-size: 24px; }
    .stem-info { flex: 1; }
    .stem-name { font-size: 14px; font-weight: 600; color: var(--text-primary); }
    .stem-label { font-size: 12px; color: var(--text-muted); }
    .stem-download { padding: 8px 14px; background: var(--accent); border: none; border-radius: var(--radius-sm); color: #000; font-size: 12px; font-weight: 700; cursor: pointer; }
    .stem-download:hover { background: var(--accent-hover); }
    .stem-waiting { padding: 8px 14px; background: var(--bg-elevated); border: 1px solid var(--border); border-radius: var(--radius-sm); color: var(--text-muted); font-size: 12px; }
    .stem-progress { display: flex; align-items: center; gap: 8px; font-size: 13px; color: var(--text-secondary); }
  </style>

    // ── Stem Separation UI ─────────────────────────────────────
    let currentStemsJobId = null;
    let stemsPollTimer = null;

    function openStemsModal(jobId) {
      currentStemsJobId = jobId;
      const job = lastJobs.find(j => j.id === jobId);
      if (!job) { closeStemsModal(); return; }
      document.getElementById("stemsModalTitle").textContent = t("stemModalTitle");
      document.getElementById("stemsModalDesc").textContent = t("stemModalDesc");
      document.getElementById("stemsError").style.display = "none";
      document.getElementById("stemsModal").style.display = "flex";
      renderStemsModal(job);
      if (job.stems_status === "running") {
        startStemsPoll(jobId);
      }
    }

    function closeStemsModal() {
      document.getElementById("stemsModal").style.display = "none";
      currentStemsJobId = null;
      if (stemsPollTimer) { clearInterval(stemsPollTimer); stemsPollTimer = null; }
    }

    function renderStemsModal(job) {
      const list = document.getElementById("stemsList");
      const status = document.getElementById("stemsStatus");
      const errorEl = document.getElementById("stemsError");
      const stemInfo = [
        { key: "vocals", label: t("stemVocals"), icon: "🎤" },
        { key: "drums", label: t("stemDrums"), icon: "🥁" },
        { key: "bass", label: t("stemBass"), icon: "🎸" },
        { key: "other", label: t("stemOther"), icon: "🎹" },
      ];
      if (job.stems_status === "running") {
        status.innerHTML = `<div class="stem-progress"><span class="spinner" style="width:14px;height:14px;border-width:1.5px;"></span> ${t("stemSplitting")}</div>`;
        list.innerHTML = stemInfo.map(s => `<div class="stem-card"><span class="stem-icon">${s.icon}</span><div class="stem-info"><div class="stem-name">${s.label}</div></div><span class="stem-waiting">...</span></div>`).join("");
        return;
      }
      if (job.stems_status === "error") {
        status.innerHTML = "";
        errorEl.style.display = "block";
        errorEl.textContent = job.stems_error || t("stemError");
        list.innerHTML = "";
        return;
      }
      if (job.stems_status === "done") {
        status.innerHTML = `<div style="color:var(--accent);font-size:13px;font-weight:600;margin-bottom:8px;">✅ ${t("stemDone")}</div>`;
        errorEl.style.display = "none";
        list.innerHTML = stemInfo.map(s => {
          const dlUrl = `/api/stems/${encodeURIComponent(job.id)}/${s.key}?client_id=${encodeURIComponent(clientId)}`;
          return `<div class="stem-card">
            <span class="stem-icon">${s.icon}</span>
            <div class="stem-info"><div class="stem-name">${s.label}</div></div>
            <a class="stem-download" href="${dlUrl}" download="${s.key}.mp3">${t("stemDownload")}</a>
          </div>`;
        }).join("");
        return;
      }
      // Not started yet - show buttons to start
      status.innerHTML = "";
      errorEl.style.display = "none";
      list.innerHTML = stemInfo.map(s => {
        return `<div class="stem-card">
          <span class="stem-icon">${s.icon}</span>
          <div class="stem-info"><div class="stem-name">${s.label}</div></div>
          <button class="btn-secondary" style="padding:8px 14px;font-size:12px;" onclick="startStemsSeparation('${escapeHtml(job.id)}')">${t("stemSplit")}</button>
        </div>`;
      }).join("");
    }

    async function startStemsSeparation(jobId) {
      const job = lastJobs.find(j => j.id === jobId);
      if (!job) return;
      try {
        const res = await fetch("/api/stems", {
          method: "POST",
          headers: { "Content-Type": "application/json", ...headers() },
          body: JSON.stringify({ job_id: jobId }),
        });
        const data = await res.json();
        if (!res.ok) { alert(data.error || "Failed to start separation"); return; }
        // Update local job status
        job.stems_status = "running";
        renderStemsModal(job);
        startStemsPoll(jobId);
      } catch(e) {
        alert("Failed to start separation");
      }
    }

    function startStemsPoll(jobId) {
      if (stemsPollTimer) clearInterval(stemsPollTimer);
      stemsPollTimer = setInterval(async () => {
        try {
          const res = await fetch("/api/jobs", {headers: headers(), cache: "no-store"});
          const data = await res.json();
          const updated = (data.jobs || []).find(j => j.id === jobId);
          if (!updated) { clearInterval(stemsPollTimer); return; }
          const job = lastJobs.find(j => j.id === jobId);
          if (job) { job.stems_status = updated.stems_status; job.stems_error = updated.stems_error; }
          if (updated.stems_status !== "running") {
            clearInterval(stemsPollTimer);
            stemsPollTimer = null;
          }
          if (jobId === currentStemsJobId) renderStemsModal(updated);
        } catch {}
      }, 3000);
    }

    // Close modal on overlay click
    document.addEventListener("click", function(e) {
      if (e.target.classList.contains("modal-overlay") && e.target.id === "stemsModal") {
        closeStemsModal();
      }
    });

  </script>
</body>
</html>
"""

ADMIN_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Music Speaks Admin</title>
  <style>
    :root { color-scheme: dark; --bg:#0b0d0c; --panel:#141716; --line:#2d3430; --text:#f4f7f1; --muted:#a7b0aa; --accent:#50d890; --danger:#ff756d; }
    * { box-sizing: border-box; }
    body { margin:0; min-height:100vh; background:var(--bg); color:var(--text); font-family:Inter, ui-sans-serif, system-ui, -apple-system, sans-serif; font-size:16px; }
    main { width:min(1180px, calc(100% - 28px)); margin:0 auto; padding:24px 0 56px; }
    header { display:flex; justify-content:space-between; gap:14px; align-items:center; margin-bottom:18px; }
    h1 { margin:0; font-size:28px; }
    .muted { color:var(--muted); }
    button, a.button { border:0; border-radius:8px; background:var(--accent); color:#06100b; padding:10px 13px; font-weight:800; cursor:pointer; text-decoration:none; display:inline-flex; align-items:center; }
    .grid { display:grid; gap:12px; }
    .card { border:1px solid var(--line); border-radius:8px; background:var(--panel); padding:14px; }
    .row { display:flex; justify-content:space-between; gap:12px; align-items:flex-start; flex-wrap:wrap; }
    .title { margin:0 0 8px; font-weight:800; line-height:1.35; }
    .meta { display:flex; gap:10px; flex-wrap:wrap; color:var(--muted); font-size:13px; }
    .badge { border:1px solid var(--line); border-radius:8px; padding:4px 8px; font-size:12px; font-weight:800; text-transform:uppercase; }
    .completed { color:var(--accent); } .error { color:var(--danger); }
    details { margin-top:10px; }
    summary { cursor:pointer; color:var(--muted); }
    pre { white-space:pre-wrap; overflow-wrap:anywhere; color:var(--muted); line-height:1.45; font-family:inherit; margin:8px 0 0; }
    .empty { border:1px dashed var(--line); border-radius:8px; padding:18px; color:var(--muted); }
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>Music Speaks Admin</h1>
        <div id="summary" class="muted">Loading all jobs...</div>
      </div>
      <button id="refresh" type="button">Refresh</button>
    </header>
    <section id="jobs" class="grid"></section>
  </main>
  <script>
    const params = new URLSearchParams(location.search);
    const adminKey = params.get("key") || "";
    const jobsBox = document.getElementById("jobs");
    const summary = document.getElementById("summary");
    function escapeHtml(value) {
      return String(value ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#039;");
    }
    function formatDate(value) {
      const date = new Date(value);
      return Number.isNaN(date.getTime()) ? "" : date.toLocaleString("en-GB", {dateStyle:"medium", timeStyle:"short"});
    }
    function render(jobs) {
      summary.textContent = `${jobs.length} total job${jobs.length === 1 ? "" : "s"}`;
      if (!jobs.length) {
        jobsBox.innerHTML = `<div class="empty">No generated tracks yet.</div>`;
        return;
      }
      jobsBox.innerHTML = jobs.map(job => {
        const download = job.download_url ? `<a class="button" href="${escapeHtml(job.download_url)}" download="${escapeHtml(job.file_name || "terry-music.mp3")}">Download MP3</a>` : "";
        const title = escapeHtml(job.song_title || job.prompt || t("untitled"));
        const details = [
          job.prompt ? `<details><summary>Music prompt</summary><pre>${escapeHtml(job.prompt)}</pre></details>` : "",
          job.lyrics_idea ? `<details><summary>Lyrics brief</summary><pre>${escapeHtml(job.lyrics_idea)}</pre></details>` : "",
          job.lyrics ? `<details><summary>Finished lyrics</summary><pre>${escapeHtml(job.lyrics)}</pre></details>` : "",
          job.error ? `<details open><summary>Error</summary><pre>${escapeHtml(job.error)}</pre></details>` : ""
        ].join("");
        return `<article class="card">
          <div class="row">
            <div>
              <p class="title">${title}</p>
              <div class="meta">
                <span class="badge ${escapeHtml(job.status)}">${escapeHtml(job.status || "unknown")}</span>
                <span>${formatDate(job.created_at)}</span>
                <span>${job.is_instrumental ? "Instrumental" : "Vocal"}</span>
                <span>${escapeHtml(job.email || "No email")}</span>
                <span title="${escapeHtml(job.owner_id || "")}">Client ${escapeHtml(String(job.owner_id || "").slice(0, 12))}</span>
              </div>
            </div>
            ${download}
          </div>
          ${details}
        </article>`;
      }).join("");
    }
    async function load() {
      summary.textContent = "Loading all jobs...";
      try {
        const res = await fetch(`/api/admin/jobs?key=${encodeURIComponent(adminKey)}`, {cache:"no-store"});
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          const errMsg = typeof data.error === "string" ? data.error : data.error?.message || `HTTP ${res.status}`;
          throw new Error(errMsg);
        }
        render(data.jobs || []);
      } catch (error) {
        summary.textContent = "Unable to load admin data";
        jobsBox.innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`;
      }
    }
    document.getElementById("refresh").addEventListener("click", load);
    load();
    setInterval(load, 5000);
  </script>
</body>
</html>
"""


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def normalize_client_id(value: str | None) -> str:
    text = (value or "").strip()
    if re.fullmatch(r"[A-Za-z0-9._:-]{8,160}", text):
        return text
    return "anonymous"


def normalize_draft_id(value: str | None) -> str:
    text = (value or "").strip()
    if re.fullmatch(r"[A-Za-z0-9._:-]{8,160}", text):
        return text
    return ""


def safe_name(value: str, fallback: str = "terry-music") -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip().lower()).strip("-._")
    return (text or fallback)[:80]


def download_file_name(title: str, fallback: str = "terry-music") -> str:
    base = re.sub(r"[\x00-\x1f\x7f<>:\"/\\|?*]+", "-", title).strip(" .-_")
    base = re.sub(r"\s+", " ", base)[:120].strip(" .-_")
    if not base:
        base = fallback
    if not base.lower().endswith(".mp3"):
        base = f"{base}.mp3"
    return base


def ascii_header_file_name(file_name: str) -> str:
    if file_name.lower().endswith(".mp3"):
        stem = file_name[:-4]
    else:
        stem = file_name
    safe_stem = safe_name(stem, "terry-music")
    return f"{safe_stem}.mp3"


def load_jobs() -> None:
    global JOBS
    if not JOBS_DB.exists():
        JOBS = {}
        return
    try:
        data = json.loads(JOBS_DB.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        JOBS = {}
        return
    JOBS = data if isinstance(data, dict) else {}




def load_feedback() -> None:
    global FEEDBACK
    if not FEEDBACK_DB.exists():
        FEEDBACK = {}
        return
    try:
        data = json.loads(FEEDBACK_DB.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        FEEDBACK = {}
        return
    FEEDBACK = data if isinstance(data, dict) else {}


def save_feedback_locked() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    tmp = FEEDBACK_DB.with_suffix(".tmp")
    tmp.write_text(json.dumps(FEEDBACK, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(FEEDBACK_DB)

def save_jobs_locked() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    tmp = JOBS_DB.with_suffix(".tmp")
    tmp.write_text(json.dumps(JOBS, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(JOBS_DB)


def load_drafts() -> None:
    global DRAFTS
    if not DRAFTS_DB.exists():
        DRAFTS = {}
        return
    try:
        data = json.loads(DRAFTS_DB.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        DRAFTS = {}
        return
    DRAFTS = data if isinstance(data, dict) else {}


def save_drafts_locked() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    tmp = DRAFTS_DB.with_suffix(".tmp")
    tmp.write_text(json.dumps(DRAFTS, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(DRAFTS_DB)


def clean_draft_payload(form: dict[str, Any]) -> dict[str, Any]:
    limits = {
        "email": 320,
        "song_title": 120,
        "prompt": 2000,
        "lyrics": 3500,
        "lyrics_idea": 2500,
        "genre": 200,
        "mood": 200,
        "instruments": 300,
        "tempo": 120,
        "bpm": 8,
        "key": 80,
        "vocals": 300,
        "structure": 300,
        "references": 500,
        "avoid": 500,
        "use_case": 300,
        "extra": 800,
    }
    draft = {key: str(form.get(key, "")).strip()[:limit] for key, limit in limits.items()}
    draft["is_instrumental"] = bool(form.get("is_instrumental"))
    draft["lyrics_optimizer"] = bool(form.get("lyrics_optimizer"))
    return draft


def public_job(job: dict[str, Any]) -> dict[str, Any]:
    result = {key: job.get(key) for key in ("id", "status", "created_at", "updated_at", "prompt", "song_title", "generated_title", "title_error", "email", "is_instrumental", "lyrics_optimizer", "file_name", "error", "email_sent", "favorite")}
    if job.get("status") == "completed" and job.get("file_path"):
        result["download_url"] = f"/download/{urllib.parse.quote(str(job['id']))}"
        # duration in seconds
        started = job.get("started_at")
        ended = job.get("completed_at")
        if started and ended:
            result["duration"] = round(ended - started, 1)
        # file size
        fp = job.get("file_path")
        if fp and isinstance(fp, str):
            try:
                result["file_size"] = pathlib.Path(fp).stat().st_size
            except Exception:
                pass
        # params summary
        params_keys = ("genre", "mood", "instruments", "tempo", "bpm", "key", "vocals", "structure", "duration_sec", "extra")
        params = {k: job.get(k) for k in params_keys if job.get(k)}
        if params:
            result["params"] = params
    if job.get("stems_status") in ("done", "error", "running"):
        result["stems_status"] = job["stems_status"]
        result["stems_dir"] = job.get("stems_dir")
        if job.get("stems_error"):
            result["stems_error"] = job["stems_error"]
    # share info
    if job.get("is_shared"):
        result["is_shared"] = True
        result["share_key"] = job.get("share_key")
        result["share_url"] = f"/?share={job['id']}&key={job['share_key']}"
    return result


def admin_job(job: dict[str, Any]) -> dict[str, Any]:
    result = public_job(job)
    file_size = result.get("file_size", 0)
    result.update({
        "owner_id": job.get("owner_id"),
        "lyrics": job.get("lyrics", ""),
        "lyrics_idea": job.get("lyrics_idea", ""),
        "generated_lyrics": bool(job.get("generated_lyrics")),
        "extra": job.get("extra", {}),
        "voice_id": job.get("voice_id"),
        "admin_file_size": file_size,
    })
    if job.get("status") == "completed" and job.get("file_path"):
        result["download_url"] = f"/download/{urllib.parse.quote(str(job['id']))}?admin_key={urllib.parse.quote(ADMIN_KEY)}"
    return result


def clean_generated_lyrics(text: str) -> str:
    cleaned = re.sub(r"\x1b\[[0-9;]*m", "", text).strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    prefixes = ("lyrics:", "song lyrics:", "here are the lyrics:", "以下是歌词：", "歌词：")
    lower = cleaned.lower()
    for prefix in prefixes:
        if lower.startswith(prefix):
            cleaned = cleaned[len(prefix):].strip()
            break
    return cleaned[:3500].strip()


def _minimax_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {MINIMAX_API_KEY}",
        "Content-Type": "application/json",
    }


def _call_minimax_api(method: str, endpoint: str, payload: Any | None = None, files: dict | None = None) -> Any:
    """Make a direct call to MiniMax API, returning parsed JSON."""
    import urllib.request
    import urllib.error

    base = "https://api.minimaxi.com"
    url = f"{base}{endpoint}"
    data = None
    headers: dict[str, str] = {}
    if files:
        boundary = "----FormBoundary" + secrets.token_hex(16)
        parts = []
        for field_name, (filename, file_content, content_type) in files.items():
            parts.append(
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field_name}\"; filename=\"{filename}\"\r\nContent-Type: {content_type}\r\n\r\n".encode()
                + file_content
                + b"\r\n"
            )
        for key, value in (payload or {}).items():
            parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n{value}\r\n".encode())
        parts.append(f"--{boundary}--\r\n".encode())
        data = b"".join(parts)
        headers = {
            "Authorization": f"Bearer {MINIMAX_API_KEY}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        }
    else:
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = _minimax_headers()

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"MiniMax API error {exc.code}: {body}")
    except urllib.error.URLError as exc:
        raise RuntimeError(f"MiniMax network error: {exc.reason}")


def clone_voice(audio_path: Path, custom_voice_id: str) -> dict[str, Any]:
    """Upload audio sample and clone the voice."""
    if not MINIMAX_API_KEY:
        raise RuntimeError("MINIMAX_API_KEY is not configured.")
    audio_path = Path(audio_path)
    if not audio_path.exists():
        raise RuntimeError(f"Audio file not found: {audio_path}")
    file_size = audio_path.stat().st_size
    if file_size > 20 * 1024 * 1024:
        raise RuntimeError("Audio file must be under 20MB.")
    suffix = audio_path.suffix.lower()
    content_type_map = {".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".wav": "audio/wav"}
    content_type = content_type_map.get(suffix, "audio/mpeg")

    upload_resp = _call_minimax_api(
        "POST", "/v1/files/upload",
        files={"file": (audio_path.name, audio_path.read_bytes(), content_type)},
        payload={"purpose": "voice_clone"},
    )
    file_id = (
        upload_resp.get("file", {}).get("file_id")
        or upload_resp.get("data", {}).get("file_id")
        or upload_resp.get("file_id")
    )
    if not file_id:
        raise RuntimeError(f"Failed to upload audio: {upload_resp}")

    clone_resp = _call_minimax_api(
        "POST", "/v1/voice_clone",
        {"file_id": file_id, "voice_id": custom_voice_id, "model": "speech-2.8-hd"},
    )
    return clone_resp


def synthesize_speech(text: str, voice_id: str, output_path: Path, model: str = "speech-2.8-hd") -> Path:
    """Synthesize speech using a cloned or system voice_id, save to output_path."""
    output_path = Path(output_path)
    if not MINIMAX_API_KEY:
        raise RuntimeError("MINIMAX_API_KEY is not configured.")
    resp = _call_minimax_api(
        "POST", "/v1/t2a_v2",
        {
            "model": model,
            "text": text[:5000],
            "stream": False,
            "voice_setting": {"voice_id": voice_id},
            "output_format": "hex",
        },
    )
    audio_hex = (
        resp.get("data", {}).get("audio_file")
        or resp.get("data", {}).get("audio")
        or resp.get("audio_file")
        or resp.get("audio")
    )
    if not audio_hex:
        print(f"[TTS] unexpected resp: {resp}")
        raise RuntimeError(f"No audio in TTS response: {resp}")
    audio_bytes = bytes.fromhex(audio_hex)
    output_path.write_bytes(audio_bytes)
    return output_path


def clean_song_title(text: str) -> str:
    cleaned = re.sub(r"\x1b\[[0-9;]*m", "", text).strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    prefixes = ("title:", "song title:", "歌名：", "标题：")
    lower = cleaned.lower()
    for prefix in prefixes:
        if lower.startswith(prefix):
            cleaned = cleaned[len(prefix):].strip()
            break
    lines = [line.strip(" \t\r\n\"'`“”‘’") for line in cleaned.splitlines() if line.strip()]
    title = lines[0] if lines else ""
    title = re.sub(r"^\s*[-*#]+\s*", "", title).strip(" \t\r\n\"'`“”‘’")
    title = re.sub(r"\s+", " ", title)
    if title.lower().endswith(".mp3"):
        title = title[:-4].strip(" .-_")
    return title[:120].strip()


def compact_title_candidate(text: str, max_words: int = 8, max_chars: int = 36) -> str:
    title = clean_song_title(text)
    if not title:
        return ""
    title = re.sub(r"^\[[^\]]+\]\s*", "", title).strip()
    title = re.sub(r"[,，。.!！?？;；:：]+$", "", title).strip()
    if not title:
        return ""
    words = title.split()
    if len(words) > max_words:
        return " ".join(words[:max_words]).strip()
    if len(title) > max_chars and len(words) <= 1:
        return title[:max_chars].strip()
    return title[:120].strip()


def fallback_song_title(job: dict[str, Any], lyrics: str) -> str:
    for line in lyrics.splitlines():
        line = line.strip()
        if not line or re.fullmatch(r"\[[^\]]+\]", line):
            continue
        title = compact_title_candidate(line)
        if title:
            return title
    for key in ("lyrics_idea", "prompt"):
        title = compact_title_candidate(str(job.get(key, "")))
        if title:
            return title
    extra = job.get("extra", {}) if isinstance(job.get("extra"), dict) else {}
    for key in ("use_case", "mood", "genre"):
        title = compact_title_candidate(str(extra.get(key, "")))
        if title:
            return title
    return "Music Speaks"


def generate_lyrics_from_text_model(job: dict[str, Any]) -> str:
    prompt = str(job.get("prompt", "")).strip()
    lyrics_idea = str(job.get("lyrics_idea", "")).strip()
    extra = job.get("extra", {}) if isinstance(job.get("extra"), dict) else {}
    context = {
        "music_style_prompt": prompt,
        "lyrics_brief": lyrics_idea or "No separate lyrics brief was provided. Infer a complete lyric concept from the music style prompt.",
        "genre": extra.get("genre", ""),
        "mood": extra.get("mood", ""),
        "vocal_style": extra.get("vocals", ""),
        "structure": extra.get("structure", ""),
        "avoid": extra.get("avoid", ""),
        "use_case": extra.get("use_case", ""),
        "extra_details": extra.get("extra", ""),
    }
    system = (
        "You are a professional songwriter. Write complete, singable lyrics only. "
        "Output only the lyrics, with no explanation, no markdown fences, and no notes. "
        "Use structure tags such as [Verse], [Pre-Chorus], [Chorus], [Bridge], and [Outro] where natural. "
        "Write in the same language as the lyrics brief unless the user explicitly requests another language. "
        "Respect the requested story, feelings, fragments, mood, and imagery. Avoid unsafe or explicit content if requested."
    )
    message = (
        "Create finished song lyrics for MiniMax music generation.\n\n"
        f"{json.dumps(context, ensure_ascii=False, indent=2)}\n\n"
        "Requirements:\n"
        "- Output only lyrics.\n"
        "- Include clear section tags.\n"
        "- Make the chorus memorable and repeatable.\n"
        "- Keep the lyrics under 3,500 characters.\n"
        "- Do not describe what you are doing."
    )
    output = run_mmx([
        "text", "chat",
        "--model", "lyrics_generation",
        "--system", system,
        "--message", message,
        "--max-tokens", "1600",
        "--temperature", "0.75",
        "--non-interactive",
        "--quiet",
        "--output", "text",
    ], timeout=180)
    lyrics = clean_generated_lyrics(output)
    if not lyrics:
        raise RuntimeError("MiniMax lyrics_generation model returned empty lyrics.")
    return lyrics


def generate_title_from_text_model(job: dict[str, Any], lyrics: str) -> str:
    prompt = str(job.get("prompt", "")).strip()
    lyrics_idea = str(job.get("lyrics_idea", "")).strip()
    extra = job.get("extra", {}) if isinstance(job.get("extra"), dict) else {}
    context = {
        "music_style_prompt": prompt,
        "lyrics": lyrics,
        "lyrics_brief": lyrics_idea,
        "genre": extra.get("genre", ""),
        "mood": extra.get("mood", ""),
        "vocal_style": extra.get("vocals", ""),
        "use_case": extra.get("use_case", ""),
    }
    system = (
        "You are a music editor naming a song. Create exactly one concise song title. "
        "Output only the title, with no explanation, no quotes, and no markdown. "
        "If lyrics are provided, infer the title from the lyrics. If there are no lyrics, infer it from the music prompt. "
        "Use the same language as the lyrics when possible."
    )
    output = run_mmx([
        "text", "chat",
        "--system", system,
        "--message", json.dumps(context, ensure_ascii=False, indent=2),
        "--max-tokens", "80",
        "--temperature", "0.65",
        "--non-interactive",
        "--quiet",
        "--output", "text",
    ], timeout=120)
    title = clean_song_title(output)
    if not title:
        raise RuntimeError("MiniMax text model returned an empty song title.")
    return title


def mark_job(job_id: str, **updates: Any) -> None:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return
        job.update(updates)
        job["updated_at"] = now_iso()
        save_jobs_locked()


def run_mmx(args: list[str], timeout: int = 900) -> str:
    if not MINIMAX_API_KEY:
        raise RuntimeError("MINIMAX_API_KEY is not configured.")
    env = os.environ.copy()
    path_parts = [part for part in env.get("PATH", "").split(os.pathsep) if part]
    for path_hint in reversed(MMX_PATH_HINTS):
        if path_hint not in path_parts:
            path_parts.insert(0, path_hint)
    env["PATH"] = os.pathsep.join(path_parts)
    env["MINIMAX_API_KEY"] = MINIMAX_API_KEY
    env["MINIMAX_API_TOKEN"] = MINIMAX_API_KEY
    result = subprocess.run([MMX_BIN] + args, capture_output=True, text=True, env=env, timeout=timeout)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "Unknown mmx error").strip()
        # Try to extract meaningful error message from mmx output
        try:
            err_json = json.loads(detail)
            if isinstance(err_json, dict):
                if isinstance(err_json.get("error"), dict):
                    err_msg = err_json["error"].get("message") or err_json["error"].get("error") or str(err_json["error"])
                elif isinstance(err_json.get("error"), str):
                    err_msg = err_json["error"]
                else:
                    err_msg = err_json.get("message") or str(err_json)
            else:
                err_msg = str(err_json)
        except (json.JSONDecodeError, TypeError):
            err_msg = detail
        raise RuntimeError(err_msg)
    return result.stdout.strip()


def send_email(to_email: str, file_path: Path, prompt: str) -> bool:
    if not SMTP_USER or not SMTP_PASSWORD:
        print("[email] SMTP_USER or SMTP_PASSWORD missing")
        return False
    try:
        content_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
        main_type, sub_type = content_type.split("/", 1)
        msg = email.mime.multipart.MIMEMultipart()
        msg["From"] = SMTP_USER
        msg["To"] = to_email
        msg["Subject"] = "Music Speaks - Your Generated Track"
        body = f"Hi! Your Music Speaks track is ready.\n\nPrompt: {prompt}\nFile: {file_path.name}\n\nEnjoy!\n"
        msg.attach(email.mime.text.MIMEText(body, "plain", "utf-8"))
        attachment = email.mime.base.MIMEBase(main_type, sub_type)
        attachment.set_payload(file_path.read_bytes())
        email.encoders.encode_base64(attachment)
        attachment.add_header("Content-Disposition", "attachment", filename=file_path.name)
        msg.attach(attachment)
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=45) as server:
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(SMTP_USER, to_email, msg.as_string())
        return True
    except Exception as exc:
        print(f"[email] failed to send to {to_email}: {exc}")
        return False


def generate_music(job_id: str) -> None:
    with JOBS_LOCK:
        job = dict(JOBS[job_id])
    mark_job(job_id, status="running", error=None)
    try:
        prompt = str(job["prompt"])
        lyrics = str(job.get("lyrics", "")).strip()
        lyrics_idea = str(job.get("lyrics_idea", "")).strip()
        song_title = clean_song_title(str(job.get("song_title", "")).strip())
        if not job.get("is_instrumental") and not lyrics and (lyrics_idea or job.get("lyrics_optimizer")):
            lyrics = generate_lyrics_from_text_model(job)
            mark_job(job_id, lyrics=lyrics, generated_lyrics=True)
        if not song_title:
            try:
                song_title = generate_title_from_text_model(job, lyrics)
                mark_job(job_id, song_title=song_title, generated_title=True, title_error=None)
            except Exception as exc:
                song_title = fallback_song_title(job, lyrics)
                mark_job(job_id, song_title=song_title, generated_title=False, title_error=str(exc))
        else:
            mark_job(job_id, song_title=song_title, generated_title=False, title_error=None)
        file_name = download_file_name(song_title)
        stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = OUTPUT_DIR / f"terry_music_{stamp}_{safe_name(song_title)}_{job_id[:8]}.mp3"
        args = ["music", "generate", "--prompt", prompt, "--out", str(out_path), "--non-interactive"]
        if job.get("is_instrumental"):
            args.append("--instrumental")
        elif lyrics:
            args.extend(["--lyrics", lyrics])
        elif job.get("lyrics_optimizer"):
            args.append("--lyrics-optimizer")
        option_map = {
            "genre": "--genre", "mood": "--mood", "instruments": "--instruments", "tempo": "--tempo",
            "bpm": "--bpm", "key": "--key", "vocals": "--vocals", "structure": "--structure",
            "references": "--references", "avoid": "--avoid", "use_case": "--use-case", "extra": "--extra",
        }
        for key, flag in option_map.items():
            value = str(job.get("extra", {}).get(key, "")).strip()
            if value:
                args.extend([flag, value])
        run_mmx(args)
        # Upload to R2 if configured
        r2_url = upload_to_r2(out_path)
        final_path = r2_url if r2_url else str(out_path)
        mark_job(job_id, status="completed", file_name=file_name, file_path=final_path, r2_url=r2_url)
        if job.get("email") and out_path.exists():
            ok = send_email(str(job["email"]), out_path, prompt)
            mark_job(job_id, email_sent=ok)
    except Exception as exc:
        mark_job(job_id, status="error", error=str(exc))


def generate_music_with_voice(job_id: str) -> None:
    """Generate music using a cloned voice as reference audio for music cover."""
    with JOBS_LOCK:
        job = dict(JOBS[job_id])
    mark_job(job_id, status="running", error=None)
    voice_id = str(job.get("voice_id", "")).strip()
    if not voice_id:
        mark_job(job_id, status="error", error="No voice_id for voice music job.")
        return
    try:
        prompt = str(job["prompt"])
        lyrics = str(job.get("lyrics", "")).strip()
        lyrics_idea = str(job.get("lyrics_idea", "")).strip()
        song_title = clean_song_title(str(job.get("song_title", "")).strip())
        if not job.get("is_instrumental") and not lyrics and (lyrics_idea or job.get("lyrics_optimizer")):
            lyrics = generate_lyrics_from_text_model(job)
            mark_job(job_id, lyrics=lyrics, generated_lyrics=True)
        if not song_title:
            try:
                song_title = generate_title_from_text_model(job, lyrics)
                mark_job(job_id, song_title=song_title, generated_title=True, title_error=None)
            except Exception as exc:
                song_title = fallback_song_title(job, lyrics)
                mark_job(job_id, song_title=song_title, generated_title=False, title_error=str(exc))
        else:
            mark_job(job_id, song_title=song_title, generated_title=False, title_error=None)
        file_name = download_file_name(song_title)
        stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = OUTPUT_DIR / f"terry_music_{stamp}_{safe_name(song_title)}_{job_id[:8]}.mp3"
        voice_wav = job.get("voice_wav_path")
        if not voice_wav or not Path(voice_wav).exists():
            raise RuntimeError("Voice recording not found. Please re-record your voice.")
        args = ["music", "cover", "--prompt", prompt, "--audio-file", str(voice_wav), "--out", str(out_path), "--non-interactive"]
        if lyrics:
            args.extend(["--lyrics", lyrics])
        option_map = {
            "genre": "--genre", "mood": "--mood", "instruments": "--instruments", "tempo": "--tempo",
            "bpm": "--bpm", "key": "--key", "vocals": "--vocals", "structure": "--structure",
            "references": "--references", "avoid": "--avoid", "use_case": "--use-case", "extra": "--extra",
        }
        for key, flag in option_map.items():
            value = str(job.get("extra", {}).get(key, "")).strip()
            if value:
                args.extend([flag, value])
        run_mmx(args)
        # Upload to R2 if configured
        r2_url = upload_to_r2(out_path)
        final_path = r2_url if r2_url else str(out_path)
        mark_job(job_id, status="completed", file_name=file_name, file_path=final_path, r2_url=r2_url)
        if job.get("email") and out_path.exists():
            ok = send_email(str(job["email"]), out_path, prompt)
            mark_job(job_id, email_sent=ok)
    except Exception as exc:
        mark_job(job_id, status="error", error=str(exc))


def generate_music_audio_to_audio(job_id: str) -> None:
    """Generate music using a reference audio file for audio-to-audio cover."""
    with JOBS_LOCK:
        job = dict(JOBS[job_id])
    mark_job(job_id, status="running", error=None)
    audio_path = str(job.get("ref_audio_path", "")).strip()
    if not audio_path or not Path(audio_path).exists():
        mark_job(job_id, status="error", error="Reference audio file not found.")
        return
    try:
        prompt = str(job["prompt"])
        lyrics = str(job.get("lyrics", "")).strip()
        lyrics_idea = str(job.get("lyrics_idea", "")).strip()
        song_title = clean_song_title(str(job.get("song_title", "")).strip())
        ref_mode = str(job.get("ref_mode", "style")).strip()
        if not job.get("is_instrumental") and not lyrics and (lyrics_idea or job.get("lyrics_optimizer")):
            lyrics = generate_lyrics_from_text_model(job)
            mark_job(job_id, lyrics=lyrics, generated_lyrics=True)
        if not song_title:
            try:
                song_title = generate_title_from_text_model(job, lyrics)
                mark_job(job_id, song_title=song_title, generated_title=True, title_error=None)
            except Exception as exc:
                song_title = fallback_song_title(job, lyrics)
                mark_job(job_id, song_title=song_title, generated_title=False, title_error=str(exc))
        else:
            mark_job(job_id, song_title=song_title, generated_title=False, title_error=None)
        file_name = download_file_name(song_title)
        stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = OUTPUT_DIR / f"terry_music_{stamp}_{safe_name(song_title)}_{job_id[:8]}.mp3"
        # Build the prompt based on ref_mode
        enhanced_prompt = prompt
        if ref_mode == "keep_vocals":
            enhanced_prompt = f"Keep the vocals from the reference, {prompt}"
        elif ref_mode == "remix":
            enhanced_prompt = f"Remix style, blend with {prompt}"
        # Use music cover with reference audio
        args = ["music", "cover", "--prompt", enhanced_prompt, "--audio-file", audio_path, "--out", str(out_path), "--non-interactive"]
        if lyrics:
            args.extend(["--lyrics", lyrics])
        option_map = {
            "genre": "--genre", "mood": "--mood", "instruments": "--instruments", "tempo": "--tempo",
            "bpm": "--bpm", "key": "--key", "vocals": "--vocals", "structure": "--structure",
            "references": "--references", "avoid": "--avoid", "use_case": "--use-case", "extra": "--extra",
        }
        for key, flag in option_map.items():
            value = str(job.get("extra", {}).get(key, "")).strip()
            if value:
                args.extend([flag, value])
        run_mmx(args)
        # Upload to R2 if configured
        r2_url = upload_to_r2(out_path)
        final_path = r2_url if r2_url else str(out_path)
        mark_job(job_id, status="completed", file_name=file_name, file_path=final_path, r2_url=r2_url)
        if job.get("email") and out_path.exists():
            ok = send_email(str(job["email"]), out_path, prompt)
            mark_job(job_id, email_sent=ok)
    except Exception as exc:
        mark_job(job_id, status="error", error=str(exc))


class MusicHandler(BaseHTTPRequestHandler):
    server_version = "MusicSpeaks/1.0"
    STEM_STEMS = ("drums", "bass", "vocals", "other")

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"{self.log_date_time_string()} {self.address_string()} {fmt % args}")

    def is_admin_request(self, parsed: urllib.parse.ParseResult | None = None) -> bool:
        key = self.headers.get("X-Admin-Key", "")
        if parsed is not None:
            query = urllib.parse.parse_qs(parsed.query)
            key = key or (query.get("key") or query.get("admin_key") or [""])[0]
        return bool(ADMIN_KEY and key and hmac.compare_digest(key, ADMIN_KEY))

    def rate_limit_check(self) -> bool:
        """Check rate limit for client IP. Returns True if allowed, False if blocked."""
        ip = _get_client_ip(self)
        if not _rate_limit_ip(ip):
            self.send_json({"error": "Too many requests. Please slow down."}, HTTPStatus.TOO_MANY_REQUESTS)
            return False
        return True

    def send_security_headers(self) -> None:
        """Add security headers to all responses."""
        for name, value in _SECURITY_HEADERS.items():
            self.send_header(name, value)

    def add_cors_headers(self) -> None:
        """Add CORS headers if configured."""
        if not _ALLOWED_ORIGINS:
            return
        origin = self.headers.get("Origin", "")
        if origin in _ALLOWED_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Client-Id, X-Admin-Key")
            self.send_header("Access-Control-Max-Age", "86400")

    # Compress buffer for gzip responses (class-level to reuse across requests)
    _gzip_buf: bytearray | None = None

    def send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK, cacheable: bool = False) -> None:
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        accept_encoding = self.headers.get("Accept-Encoding", "")
        if "gzip" in accept_encoding and len(data) > 512:
            import gzip, io
            buf = bytearray()
            with gzip.GzipFile(fileobj=io.BytesIO(buf), mode="wb") as gz:
                gz.write(data)
            data = bytes(buf)
            self.send_response(status)
            self.send_header("Content-Encoding", "gzip")
        else:
            self.send_response(status)
        self.send_security_headers()
        self.add_cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        if cacheable:
            etag = hashlib.md5(data[:64]).hexdigest()
            self.send_header("ETag", f'"{etag}"')
            self.send_header("Cache-Control", "private, max-age=5")
            if_none = self.headers.get("If-None-Match", "")
            if if_none and hmac.compare_digest(if_none, f'"{etag}"'):
                self.send_header("Content-Length", "0")
                self.end_headers()
                self.wfile.write(b"")
                return
        else:
            self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def send_text(self, text: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = text.encode("utf-8")
        self.send_response(status)
        self.send_security_headers()
        self.add_cors_headers()
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _etag_for(self, data: bytes) -> str:
        return f'"{hashlib.md5(data[:64]).hexdigest()}"'

    def _check_etag(self, etag: str) -> bool:
        if_none = self.headers.get("If-None-Match", "")
        return bool(if_none and hmac.compare_digest(if_none, etag))

    def do_GET(self) -> None:
        if not self.rate_limit_check():
            return
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path == "/":
            data = INDEX_HTML.encode("utf-8")
            etag = self._etag_for(data)
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", "private, max-age=10")
            if self._check_etag(etag):
                self.send_header("Content-Length", "0")
                self.end_headers()
                self.wfile.write(b"")
                return
            self.end_headers()
            self.wfile.write(data)
            return
        if path == "/admin":
            data = ADMIN_HTML.encode("utf-8")
            etag = self._etag_for(data)
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", "private, max-age=10")
            if self._check_etag(etag):
                self.send_header("Content-Length", "0")
                self.end_headers()
                self.wfile.write(b"")
                return
            self.end_headers()
            self.wfile.write(data)
            return
        if path == "/api/health":
            self.send_json({
                "ok": True,
                "minimax_configured": bool(MINIMAX_API_KEY),
                "admin_configured": bool(ADMIN_KEY),
                "title_fallback": True,
                "drafts": True,
                "smtp_configured": bool(SMTP_USER and SMTP_PASSWORD),
                "smtp_host": SMTP_HOST,
                "smtp_port": SMTP_PORT,
            }, cacheable=True)
            return
        if path == "/api/admin/jobs":
            if not self.is_admin_request(parsed):
                self.send_json({"error": "Unauthorized"}, HTTPStatus.UNAUTHORIZED)
                return
            with JOBS_LOCK:
                jobs = sorted(
                    [admin_job(job) for job in JOBS.values()],
                    key=lambda item: str(item.get("created_at", "")),
                    reverse=True,
                )
            self.send_json({"jobs": jobs})
            return
        if path == "/api/jobs":
            client_id = normalize_client_id(self.headers.get("X-Client-Id"))
            status_filter = parsed.query.get("status", "")
            search_filter = parsed.query.get("search", "")
            date_from = parsed.query.get("date_from", "")
            date_to = parsed.query.get("date_to", "")
            favorites_only = parsed.query.get("favorites") == "1"
            page = max(1, int(parsed.query.get("page", 1)))
            per_page = min(100, max(1, int(parsed.query.get("per_page", 50))))
            with JOBS_LOCK:
                all_jobs = [public_job(job) for job in JOBS.values() if job.get("owner_id") == client_id]
                if status_filter:
                    statuses = [s.strip() for s in status_filter.split(",")]
                    all_jobs = [j for j in all_jobs if j.get("status") in statuses]
                if favorites_only:
                    all_jobs = [j for j in all_jobs if j.get("favorite")]
                if date_from:
                    all_jobs = [j for j in all_jobs if (j.get("created_at") or "") >= date_from]
                if date_to:
                    all_jobs = [j for j in all_jobs if (j.get("created_at") or "") <= date_to]
                if search_filter:
                    q = search_filter.lower()
                    all_jobs = [j for j in all_jobs if q in (j.get("song_title") or "").lower() or q in (j.get("prompt") or "").lower()]
                all_jobs.sort(key=lambda j: str(j.get("created_at", "")), reverse=True)
                total = len(all_jobs)
                start = (page - 1) * per_page
                jobs = all_jobs[start:start + per_page]
            self.send_json({"jobs": jobs, "total": total, "page": page, "per_page": per_page})
            return
        # PATCH /api/jobs/{id}/favorite
        if parsed.path.startswith("/api/jobs/") and parsed.path.endswith("/favorite"):
            job_id = urllib.parse.unquote(parsed.path.removeprefix("/api/jobs/").removesuffix("/favorite"))
            client_id = normalize_client_id(self.headers.get("X-Client-Id"))
            with JOBS_LOCK:
                job = JOBS.get(job_id)
                if not job or job.get("owner_id") != client_id:
                    self.send_json({"error": "Job not found"}, HTTPStatus.NOT_FOUND)
                    return
                job["favorite"] = not job.get("favorite")
                save_jobs_locked()
                self.send_json({"job": public_job(job)})
            return
        # DELETE /api/jobs/batch
        if path == "/api/jobs/batch" and self.command == "DELETE":
            client_id = normalize_client_id(self.headers.get("X-Client-Id"))
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length) if length else b"{}"
                ids = json.loads(body.decode("utf-8")).get("ids", [])
            except Exception:
                ids = []
            deleted = 0
            with JOBS_LOCK:
                for jid in ids:
                    if JOBS.get(jid, {}).get("owner_id") == client_id:
                        JOBS.pop(jid, None)
                        deleted += 1
                save_jobs_locked()
            self.send_json({"deleted": deleted})
            return
        # POST /api/jobs/batch/download
        if path == "/api/jobs/batch/download" and self.command == "POST":
            client_id = normalize_client_id(self.headers.get("X-Client-Id"))
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length) if length else b"{}"
                ids = json.loads(body.decode("utf-8")).get("ids", [])
            except Exception:
                ids = []
            import io, zipfile
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                with JOBS_LOCK:
                    for jid in ids:
                        job = JOBS.get(jid)
                        if not job or job.get("owner_id") != client_id:
                            continue
                        fp = job.get("file_path")
                        if fp and pathlib.Path(fp).exists():
                            zf.write(fp, pathlib.Path(fp).name)
            buf.seek(0)
            data = buf.read()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Disposition", "attachment; filename=terry-music-batch.zip")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if parsed.path.startswith("/api/jobs/"):
            job_id = urllib.parse.unquote(parsed.path.removeprefix("/api/jobs/"))
            client_id = normalize_client_id(self.headers.get("X-Client-Id"))
            with JOBS_LOCK:
                job = JOBS.get(job_id)
                if not job or job.get("owner_id") != client_id:
                    self.send_json({"error": "Job not found"}, HTTPStatus.NOT_FOUND)
                    return
                self.send_json(public_job(job))
            return
        if path.startswith("/api/drafts/"):
            self.handle_get_draft(path.removeprefix("/api/drafts/"))
            return
        if path == "/api/voice":
            self.handle_get_voices()
            return
        if path.startswith("/download/"):
            self.handle_download(path.removeprefix("/download/"), parsed.query)
            return
        if path.startswith("/api/stems/"):
            self.handle_stems_download(path, parsed.query)
            return
        if path == "/favicon.ico":
            self.send_response(HTTPStatus.NO_CONTENT)
            self.end_headers()
            return
        self.send_text("Not found", HTTPStatus.NOT_FOUND)


    def handle_stems_request(self) -> None:
        """POST /api/stems — start stem separation for a completed job."""
        try:
            form = self.read_json_body()
        except ValueError as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        job_id = str(form.get("job_id", "")).strip()
        if not job_id:
            self.send_json({"error": "job_id is required"}, HTTPStatus.BAD_REQUEST)
            return
        client_id = normalize_client_id(self.headers.get("X-Client-Id"))
        with JOBS_LOCK:
            job = JOBS.get(job_id)
            if not job or job.get("owner_id") != client_id:
                self.send_json({"error": "Job not found"}, HTTPStatus.NOT_FOUND)
                return
            if job.get("status") != "completed" or not job.get("file_path"):
                self.send_json({"error": "Job is not completed"}, HTTPStatus.BAD_REQUEST)
                return
            if job.get("is_instrumental"):
                self.send_json({"error": "Instrumental tracks have no vocals to separate"}, HTTPStatus.BAD_REQUEST)
                return
            if job.get("stems_status") == "running":
                self.send_json({"error": "Stem separation already in progress"}, HTTPStatus.CONFLICT)
                return
        mark_job(job_id, stems_status="running", stems_error=None)
        threading.Thread(target=separate_stems, args=(job_id,), daemon=True).start()
        self.send_json({"ok": True, "message": "Stem separation started"}, HTTPStatus.ACCEPTED)

    def handle_stems_download(self, path: str, query_string: str) -> None:
        """GET /api/stems/<job_id>/<stem> — download a separated stem."""
        parts = path.removeprefix("/api/stems/").split("/", 1)
        if len(parts) < 2:
            self.send_text("Stem name required", HTTPStatus.BAD_REQUEST)
            return
        job_id = urllib.parse.unquote(parts[0])
        stem_name = parts[1]
        if stem_name not in self.STEM_STEMS:
            self.send_text("Invalid stem name", HTTPStatus.BAD_REQUEST)
            return
        query = urllib.parse.parse_qs(query_string)
        admin_key = (query.get("admin_key") or query.get("key") or [""])[0]
        admin_ok = bool(ADMIN_KEY and admin_key and hmac.compare_digest(admin_key, ADMIN_KEY))
        client_id = normalize_client_id(self.headers.get("X-Client-Id") or (query.get("client_id") or [""])[0])
        with JOBS_LOCK:
            job = JOBS.get(job_id)
            if not job or (not admin_ok and job.get("owner_id") != client_id):
                self.send_text("Job not found", HTTPStatus.NOT_FOUND)
                return
            if job.get("stems_status") != "done":
                self.send_text("Stems not ready", HTTPStatus.BAD_REQUEST)
                return
            stems_dir = job.get("stems_dir")
            if not stems_dir:
                self.send_text("Stems directory not found", HTTPStatus.NOT_FOUND)
                return
            stem_path = Path(stems_dir) / f"{stem_name}.mp3"
        try:
            stem_path = stem_path.resolve(strict=True)
        except OSError:
            self.send_text("Stem file not found", HTTPStatus.NOT_FOUND)
            return
        content_type = "audio/mpeg"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(stem_path.stat().st_size))
        ascii_name = ascii_header_file_name(f"{stem_name}.mp3")
        quoted = urllib.parse.quote(f"{stem_name}.mp3")
        self.send_header("Content-Disposition", f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quoted}")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        with stem_path.open("rb") as f:
            while chunk := f.read(1024 * 256):
                self.wfile.write(chunk)

    def read_json_body(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            raise ValueError("Invalid request length.")
        if length <= 0 or length > MAX_BODY_BYTES:
            raise ValueError("Request body is empty or too large.")
        try:
            form = json.loads(self.rfile.read(length).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError("Invalid JSON request body.") from exc
        if not isinstance(form, dict):
            raise ValueError("Expected a JSON object.")
        return form

    def handle_lyrics_request(self) -> None:
        try:
            form = self.read_json_body()
            prompt = str(form.get("prompt", "")).strip()
            lyrics_idea = str(form.get("lyrics_idea", "")).strip()
            if not prompt and not lyrics_idea:
                raise ValueError("Lyrics brief or music style prompt is required.")
            if len(prompt) > 2000:
                raise ValueError("Prompt must be 2000 characters or fewer.")
            if len(lyrics_idea) > 2500:
                raise ValueError("Lyrics brief must be 2500 characters or fewer.")
            extra = {key: str(form.get(key, "")).strip() for key in ("genre", "mood", "instruments", "tempo", "bpm", "key", "vocals", "structure", "references", "avoid", "use_case", "extra")}
            lyrics = generate_lyrics_from_text_model({"prompt": prompt, "lyrics_idea": lyrics_idea, "extra": extra})
        except ValueError as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        except Exception as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_GATEWAY)
            return
        self.send_json({"lyrics": lyrics})

    def handle_jobs_voice(self) -> None:
        """Handle POST /api/jobs/voice — create a music job that uses a cloned voice."""
        try:
            form = self.read_json_body()
            prompt = str(form.get("prompt", "")).strip()
            raw_song_title = str(form.get("song_title", "")).strip()
            song_title = clean_song_title(raw_song_title)
            email_addr = str(form.get("email", "")).strip()
            lyrics = str(form.get("lyrics", "")).strip()
            lyrics_idea = str(form.get("lyrics_idea", "")).strip()
            voice_id = str(form.get("voice_id", "")).strip()
            is_instrumental = bool(form.get("is_instrumental"))
            lyrics_optimizer = bool(form.get("lyrics_optimizer") or lyrics_idea) and not is_instrumental
            if not voice_id:
                raise ValueError("voice_id is required for voice music job.")
            if not prompt:
                raise ValueError("Prompt is required.")
            if len(prompt) > 2000:
                raise ValueError("Prompt must be 2000 characters or fewer.")
            if len(raw_song_title) > 120:
                raise ValueError("Song title must be 120 characters or fewer.")
            if len(lyrics) > 3500:
                raise ValueError("Lyrics must be 3500 characters or fewer.")
            if len(lyrics_idea) > 2500:
                raise ValueError("Lyrics brief must be 2500 characters or fewer.")
            if not is_instrumental and not lyrics and not lyrics_optimizer:
                raise ValueError("Lyrics, a lyrics brief, or auto lyrics are required for vocal tracks.")
            extra = {key: str(form.get(key, "")).strip() for key in ("genre", "mood", "instruments", "tempo", "bpm", "key", "vocals", "structure", "references", "avoid", "use_case", "extra")}
            client_id = normalize_client_id(self.headers.get("X-Client-Id"))
            # Find the voice WAV file saved during clone (named voice_wav_{client_id}.wav)
            voice_wav = OUTPUT_DIR / f"voice_wav_{client_id[:16]}.wav"
            if not voice_wav.exists():
                raise RuntimeError("Voice recording not found. Please re-record your voice before generating.")
            voice_wav_path = str(voice_wav)
        except ValueError as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        except Exception as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_GATEWAY)
            return
        job_id = secrets.token_urlsafe(12)
        job = {
            "id": job_id,
            "owner_id": client_id,
            "status": "queued",
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "prompt": prompt,
            "song_title": song_title,
            "generated_title": False,
            "title_error": None,
            "email": email_addr,
            "lyrics": lyrics,
            "lyrics_idea": lyrics_idea,
            "is_instrumental": is_instrumental,
            "lyrics_optimizer": lyrics_optimizer,
            "generated_lyrics": False,
            "file_name": None,
            "file_path": None,
            "error": None,
            "email_sent": False,
            "voice_id": voice_id,
            "voice_wav_path": voice_wav_path,
            "extra": extra,
        }
        with JOBS_LOCK:
            JOBS[job_id] = job
            save_jobs_locked()
        threading.Thread(target=generate_music_with_voice, args=(job_id,), daemon=True).start()
        self.send_json({"job": public_job(job)}, HTTPStatus.ACCEPTED)

    def handle_jobs_audio_to_audio(self) -> None:
        """Handle POST /api/jobs/audio-to-audio — create a music job that uses a reference audio file."""
        try:
            content_type = self.headers.get("Content-Type", "")
            if "multipart/form-data" not in content_type:
                raise ValueError("Content-Type must be multipart/form-data.")
            length = int(self.headers.get("Content-Length") or "0")
            if length <= 0 or length > 60 * 1024 * 1024:
                raise ValueError("File too large or missing (max 50MB).")
            body = self.rfile.read(length)
            client_id = normalize_client_id(self.headers.get("X-Client-Id"))
            boundary_match = re.search(r"boundary=(.+)", content_type)
            if not boundary_match:
                raise ValueError("Missing multipart boundary.")
            boundary = boundary_match.group(1).strip('"').encode()
            parts = {}
            for chunk in body.split(b"--" + boundary):
                chunk = chunk.strip()
                if not chunk or chunk.startswith(b"--") or chunk.startswith(b"\r\n--"):
                    continue
                hdr_end = chunk.find(b"\r\n\r\n")
                if hdr_end < 0:
                    continue
                hdr_block = chunk[:hdr_end].decode("latin-1")
                body_data = chunk[hdr_end + 4:]
                name_m = re.search(r'name="([^"]+)"', hdr_block)
                if not name_m:
                    continue
                name = name_m.group(1)
                fn_m = re.search(r'filename="([^"]+)"', hdr_block)
                if fn_m:
                    parts[name] = (fn_m.group(1), body_data.rstrip(b"\r\n"))
                else:
                    parts[name] = body_data.rstrip(b"\r\n").decode("utf-8", errors="replace")
            audio_file = parts.get("audio")
            ref_mode = parts.get("ref_mode", "style")
            if isinstance(ref_mode, tuple):
                ref_mode = ref_mode[1].decode("utf-8", errors="replace") if isinstance(ref_mode[1], bytes) else ref_mode[1]
            payload_json = parts.get("payload")
            if isinstance(payload_json, tuple):
                payload_json = payload_json[1].decode("utf-8", errors="replace") if isinstance(payload_json[1], bytes) else payload_json[1]
            if not audio_file:
                raise ValueError("Audio file is required.")
            if isinstance(audio_file, tuple):
                filename, file_data = audio_file
            else:
                raise ValueError("Audio file is missing.")
            # Save reference audio file
            ref_audio_dir = OUTPUT_DIR / "ref_audio"
            ref_audio_dir.mkdir(exist_ok=True)
            safe_client_id = client_id[:16]
            ref_audio_path = ref_audio_dir / f"ref_{safe_client_id}_{secrets.token_urlsafe(8)}_{filename}"
            ref_audio_path.write_bytes(file_data)
            # Parse payload
            form = json.loads(payload_json) if payload_json else {}
            prompt = str(form.get("prompt", "")).strip()
            raw_song_title = str(form.get("song_title", "")).strip()
            song_title = clean_song_title(raw_song_title)
            email_addr = str(form.get("email", "")).strip()
            lyrics = str(form.get("lyrics", "")).strip()
            lyrics_idea = str(form.get("lyrics_idea", "")).strip()
            is_instrumental = bool(form.get("is_instrumental"))
            lyrics_optimizer = bool(form.get("lyrics_optimizer") or lyrics_idea) and not is_instrumental
            if not prompt:
                raise ValueError("Prompt is required.")
            if len(prompt) > 2000:
                raise ValueError("Prompt must be 2000 characters or fewer.")
            if len(raw_song_title) > 120:
                raise ValueError("Song title must be 120 characters or fewer.")
            if len(lyrics) > 3500:
                raise ValueError("Lyrics must be 3500 characters or fewer.")
            if len(lyrics_idea) > 2500:
                raise ValueError("Lyrics brief must be 2500 characters or fewer.")
            extra = {key: str(form.get(key, "")).strip() for key in ("genre", "mood", "instruments", "tempo", "bpm", "key", "vocals", "structure", "references", "avoid", "use_case", "extra")}
        except ValueError as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        except Exception as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_GATEWAY)
            return
        job_id = secrets.token_urlsafe(12)
        job = {
            "id": job_id,
            "owner_id": client_id,
            "status": "queued",
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "prompt": prompt,
            "song_title": song_title,
            "generated_title": False,
            "title_error": None,
            "email": email_addr,
            "lyrics": lyrics,
            "lyrics_idea": lyrics_idea,
            "is_instrumental": is_instrumental,
            "lyrics_optimizer": lyrics_optimizer,
            "generated_lyrics": False,
            "file_name": None,
            "file_path": None,
            "error": None,
            "email_sent": False,
            "ref_mode": ref_mode,
            "ref_audio_path": str(ref_audio_path),
            "extra": extra,
        }
        with JOBS_LOCK:
            JOBS[job_id] = job
            save_jobs_locked()
        threading.Thread(target=generate_music_audio_to_audio, args=(job_id,), daemon=True).start()
        self.send_json({"job": public_job(job)}, HTTPStatus.ACCEPTED)

    def handle_get_voices(self) -> None:
        """Return a list of available system voices for the TTS voice picker."""
        try:
            output = run_mmx(["speech", "voices", "--output", "json", "--non-interactive", "--quiet"], timeout=30)
            voices = json.loads(output)
        except Exception as exc:
            voices = []
        self.send_json({"voices": voices})

    def handle_voice_clone(self) -> None:
        """Handle POST /api/voice/clone — accepts multipart form with audio file."""
        try:
            content_type = self.headers.get("Content-Type", "")
            if "multipart/form-data" not in content_type:
                raise ValueError("Content-Type must be multipart/form-data.")
            length = int(self.headers.get("Content-Length") or "0")
            if length <= 0 or length > 25 * 1024 * 1024:
                raise ValueError("File too large or missing (max 20MB).")
            body = self.rfile.read(length)
            client_id = normalize_client_id(self.headers.get("X-Client-Id"))
            boundary_match = re.search(r"boundary=(.+)", content_type)
            if not boundary_match:
                raise ValueError("Missing multipart boundary.")
            boundary = boundary_match.group(1).strip('"').encode()
            parts = {}
            for chunk in body.split(b"--" + boundary):
                chunk = chunk.strip()
                if not chunk or chunk.startswith(b"--") or chunk.startswith(b"\r\n--"):
                    continue
                hdr_end = chunk.find(b"\r\n\r\n")
                if hdr_end < 0:
                    continue
                hdr_block = chunk[:hdr_end].decode("latin-1")
                body_data = chunk[hdr_end + 4:]
                name_m = re.search(r'name="([^"]+)"', hdr_block)
                if not name_m:
                    continue
                name = name_m.group(1)
                fn_m = re.search(r'filename="([^"]+)"', hdr_block)
                if fn_m:
                    parts[name] = (fn_m.group(1), body_data.rstrip(b"\r\n"))
                else:
                    parts[name] = body_data.rstrip(b"\r\n").decode("utf-8", errors="replace")
            audio_bytes = parts.get("audio")
            if not audio_bytes:
                raise ValueError("No audio field in form data.")
            filename_audio = parts.get("audio", (None,))[0] or ""
            suffix = ".webm"
            if filename_audio.lower().endswith(".mp3"):
                suffix = ".mp3"
            elif filename_audio.lower().endswith(".m4a"):
                suffix = ".m4a"
            elif filename_audio.lower().endswith(".wav"):
                suffix = ".wav"
            voice_id = f"user_{client_id[:16]}"
            audio_data = audio_bytes[1] if isinstance(audio_bytes, tuple) else audio_bytes
            # Save WAV with client_id prefix so we can find it later by client_id
            voice_wav_path = OUTPUT_DIR / f"voice_wav_{client_id[:16]}.wav"
            voice_wav_path.write_bytes(audio_data)
            tmp_path = OUTPUT_DIR / f"voice_sample_{secrets.token_hex(8)}{suffix}"
            tmp_path.write_bytes(audio_data)
            try:
                result = clone_voice(tmp_path, voice_id)
            finally:
                tmp_path.unlink(missing_ok=True)
            voice_id_out = result.get("data", {}).get("voice_id", voice_id)
            self.send_json({"ok": True, "voice_id": voice_id_out, "expires_in_hours": 168})
        except ValueError as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        except Exception as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_GATEWAY)
            return

    def handle_voice_sing(self) -> None:
        """Handle POST /api/voice/sing — synthesize singing audio from lyrics using a voice_id."""
        try:
            form = self.read_json_body()
            lyrics = str(form.get("lyrics", "")).strip()
            voice_id = str(form.get("voice_id", "")).strip()
            if not lyrics:
                raise ValueError("Lyrics are required.")
            if not voice_id:
                raise ValueError("voice_id is required.")
            tmp_sing = OUTPUT_DIR / f"sing_preview_{secrets.token_hex(8)}.mp3"
            synthesize_speech(lyrics, voice_id, tmp_sing)
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "audio/mpeg")
            self.send_header("Content-Length", str(tmp_sing.stat().st_size))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            with tmp_sing.open("rb") as f:
                while chunk := f.read(1024 * 256):
                    self.wfile.write(chunk)
            tmp_sing.unlink(missing_ok=True)
            return
        except ValueError as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        except Exception as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_GATEWAY)
            return

    def handle_get_draft(self, encoded_draft_id: str) -> None:
        draft_id = normalize_draft_id(urllib.parse.unquote(encoded_draft_id))
        if not draft_id:
            self.send_json({"error": "Draft not found"}, HTTPStatus.NOT_FOUND)
            return
        with DRAFTS_LOCK:
            draft = DRAFTS.get(draft_id)
        if not draft:
            self.send_json({"draft": None})
            return
        self.send_json({"draft": draft.get("draft", {}), "updated_at": draft.get("updated_at")})

    def handle_save_draft(self, encoded_draft_id: str) -> None:
        draft_id = normalize_draft_id(urllib.parse.unquote(encoded_draft_id))
        if not draft_id:
            self.send_json({"error": "Invalid draft id"}, HTTPStatus.BAD_REQUEST)
            return
        try:
            form = self.read_json_body()
            draft = clean_draft_payload(form)
        except ValueError as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        with DRAFTS_LOCK:
            DRAFTS[draft_id] = {
                "id": draft_id,
                "owner_id": normalize_client_id(self.headers.get("X-Client-Id")),
                "updated_at": now_iso(),
                "draft": draft,
            }
            save_drafts_locked()
        self.send_json({"ok": True})

    def handle_delete_draft(self, encoded_draft_id: str) -> None:
        draft_id = normalize_draft_id(urllib.parse.unquote(encoded_draft_id))
        if not draft_id:
            self.send_json({"error": "Invalid draft id"}, HTTPStatus.BAD_REQUEST)
            return
        with DRAFTS_LOCK:
            DRAFTS.pop(draft_id, None)
            save_drafts_locked()
        self.send_json({"ok": True})

    def do_POST(self) -> None:
        if not self.rate_limit_check():
            return
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/lyrics":
            self.handle_lyrics_request()
            return
        if parsed.path.startswith("/api/drafts/"):
            self.handle_save_draft(parsed.path.removeprefix("/api/drafts/"))
            return
        if parsed.path == "/api/voice/clone":
            self.handle_voice_clone()
            return
        if parsed.path == "/api/voice/sing":
            self.handle_voice_sing()
            return
        if parsed.path == "/api/jobs/audio-to-audio":
            self.handle_jobs_audio_to_audio()
            return
        if parsed.path == "/api/jobs/voice":
            self.handle_jobs_voice()
            return
        if parsed.path == "/api/stems":
            self.handle_stems_request()
            return
        if parsed.path != "/api/jobs":
            self.send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
            return
        try:
            form = self.read_json_body()
            prompt = str(form.get("prompt", "")).strip()
            raw_song_title = str(form.get("song_title", "")).strip()
            song_title = clean_song_title(raw_song_title)
            email_addr = str(form.get("email", "")).strip()
            lyrics = str(form.get("lyrics", "")).strip()
            lyrics_idea = str(form.get("lyrics_idea", "")).strip()
            is_instrumental = bool(form.get("is_instrumental"))
            lyrics_optimizer = bool(form.get("lyrics_optimizer") or lyrics_idea) and not is_instrumental
            if not prompt:
                raise ValueError("Prompt is required.")
            if len(prompt) > 2000:
                raise ValueError("Prompt must be 2000 characters or fewer.")
            if len(raw_song_title) > 120:
                raise ValueError("Song title must be 120 characters or fewer.")
            if len(lyrics) > 3500:
                raise ValueError("Lyrics must be 3500 characters or fewer.")
            if len(lyrics_idea) > 2500:
                raise ValueError("Lyrics brief must be 2500 characters or fewer.")
            if not is_instrumental and not lyrics and not lyrics_optimizer:
                raise ValueError("Lyrics, a lyrics brief, or auto lyrics are required for vocal tracks.")
            extra = {key: str(form.get(key, "")).strip() for key in ("genre", "mood", "instruments", "tempo", "bpm", "key", "vocals", "structure", "references", "avoid", "use_case", "extra")}
        except ValueError as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        job_id = secrets.token_urlsafe(12)
        job = {
            "id": job_id,
            "owner_id": normalize_client_id(self.headers.get("X-Client-Id")),
            "status": "queued",
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "prompt": prompt,
            "song_title": song_title,
            "generated_title": False,
            "title_error": None,
            "email": email_addr,
            "lyrics": lyrics,
            "lyrics_idea": lyrics_idea,
            "is_instrumental": is_instrumental,
            "lyrics_optimizer": lyrics_optimizer,
            "generated_lyrics": False,
            "file_name": None,
            "file_path": None,
            "error": None,
            "email_sent": False,
            "extra": extra,
        }
        with JOBS_LOCK:
            JOBS[job_id] = job
            save_jobs_locked()
        threading.Thread(target=generate_music, args=(job_id,), daemon=True).start()
        self.send_json({"job": public_job(job)}, HTTPStatus.ACCEPTED)

    def do_DELETE(self) -> None:
        if not self.rate_limit_check():
            return
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path.startswith("/api/drafts/"):
            self.handle_delete_draft(parsed.path.removeprefix("/api/drafts/"))
            return
        if not parsed.path.startswith("/api/jobs/"):
            self.send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
            return
        job_id = urllib.parse.unquote(parsed.path.removeprefix("/api/jobs/"))
        client_id = normalize_client_id(self.headers.get("X-Client-Id"))
        with JOBS_LOCK:
            job = JOBS.get(job_id)
            if not job or job.get("owner_id") != client_id:
                self.send_json({"error": "Job not found"}, HTTPStatus.NOT_FOUND)
                return
            del JOBS[job_id]
            save_jobs_locked()
        self.send_json({"ok": True})

    def handle_download(self, encoded_job_id: str, query_string: str) -> None:
        job_id = urllib.parse.unquote(encoded_job_id)
        query = urllib.parse.parse_qs(query_string)
        admin_key = (query.get("admin_key") or query.get("key") or [""])[0]
        admin_ok = bool(ADMIN_KEY and admin_key and hmac.compare_digest(admin_key, ADMIN_KEY))
        client_id = normalize_client_id(self.headers.get("X-Client-Id") or (query.get("client_id") or [""])[0])
        with JOBS_LOCK:
            job = JOBS.get(job_id)
            if not job or (not admin_ok and job.get("owner_id") != client_id):
                self.send_text("Job not found", HTTPStatus.NOT_FOUND)
                return
            if job.get("status") != "completed" or not job.get("file_path"):
                self.send_text("Job is not completed", HTTPStatus.BAD_REQUEST)
                return
            file_path = Path(str(job["file_path"]))
        try:
            file_path = file_path.resolve(strict=True)
            output_root = OUTPUT_DIR.resolve(strict=True)
        except OSError:
            self.send_text("Generated file is missing", HTTPStatus.NOT_FOUND)
            return
        if output_root not in file_path.parents and file_path != output_root:
            self.send_text("Invalid file path", HTTPStatus.BAD_REQUEST)
            return
        file_name = download_file_name(str(job.get("file_name") or file_path.name))
        ascii_file_name = ascii_header_file_name(file_name)
        content_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
        quoted = urllib.parse.quote(file_name)
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(file_path.stat().st_size))
        self.send_header("Content-Disposition", f"attachment; filename=\"{ascii_file_name}\"; filename*=UTF-8''{quoted}")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        with file_path.open("rb") as file_obj:
            while chunk := file_obj.read(1024 * 256):
                self.wfile.write(chunk)


def separate_stems(job_id: str) -> None:
    """Run Demucs to separate a completed job's audio into stems."""
    with JOBS_LOCK:
        job = dict(JOBS.get(job_id, {}))
    if not job:
        return
    audio_path = job.get("file_path")
    if not audio_path:
        mark_job(job_id, stems_status="error", stems_error="Audio file path not found")
        return
    audio_path = Path(audio_path)
    if not audio_path.exists():
        mark_job(job_id, stems_status="error", stems_error="Audio file not found on disk")
        return

    # Demucs creates: <audio_parent>/htdemucs/<track_name>/*.mp3
    demucs_out = audio_path.parent.resolve()
    track_name = audio_path.stem

    try:
        import torch
        import demucs.pretrained
        import demucs.separate
    except ImportError:
        mark_job(job_id, stems_status="error", stems_error="Demucs is not installed")
        return

    try:
        sys_args = ["-n", "htdemucs", "-o", str(demucs_out), "--mp3", "--quiet", str(audio_path.resolve())]
        demucs.separate.main(sys_args)
    except Exception as exc:
        mark_job(job_id, stems_status="error", stems_error=str(exc))
        return

    stems_base = demucs_out / "htdemucs" / track_name
    found_any = False
    for stem in ("drums", "bass", "vocals", "other"):
        stem_file = stems_base / f"{stem}.mp3"
        if stem_file.exists():
            found_any = True

    if not found_any:
        for stem in ("drums", "bass", "vocals", "other"):
            stem_file = stems_base / f"{stem}.flac"
            if stem_file.exists():
                found_any = True
                break

    if not found_any:
        mark_job(job_id, stems_status="error", stems_error=f"No stem files found in {stems_base}")
        return

    mark_job(job_id, stems_status="done", stems_dir=str(stems_base))



def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    load_jobs()
    load_drafts()
    server = ThreadingHTTPServer((HOST, PORT), MusicHandler)
    print(f"Music Speaks running at http://{HOST}:{PORT}")
    print(f"Output directory: {OUTPUT_DIR}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Music Speaks.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
