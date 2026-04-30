#!/usr/bin/env python3
"""Music Speaks web app for Render."""

from __future__ import annotations

import datetime as dt
import base64
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
MAX_BODY_BYTES = 1024 * 1024
LYRICS_CHAR_LIMIT = 6000
GENERATED_LYRICS_MIN_CHARS = 1200
GENERATED_LYRICS_TARGET_MIN_CHARS = 1800
GENERATED_LYRICS_TARGET_MAX_CHARS = 3600
GENERATED_LYRICS_MAX_CHARS = 4200
VOICE_CLONE_SINGING_ENDPOINT = os.getenv("MINIMAX_VOICE_CLONE_SINGING_ENDPOINT", "/v1/voice_clone_singing")
VOICE_CLONE_SINGING_MODEL = os.getenv("MINIMAX_VOICE_CLONE_SINGING_MODEL", "music-2.6")
LYRICS_REQUEST_TIMEOUT = float(os.getenv("LYRICS_REQUEST_TIMEOUT", "90"))
VOICE_LIST_TIMEOUT = float(os.getenv("VOICE_LIST_TIMEOUT", "15"))
VOICE_CACHE_TTL_SECONDS = int(os.getenv("VOICE_CACHE_TTL_SECONDS", "900"))
JOB_TIMEOUT_SECONDS = int(os.getenv("JOB_TIMEOUT_SECONDS", "900"))
JOB_RETENTION_SECONDS = int(os.getenv("JOB_RETENTION_SECONDS", "604800"))
LYRIC_TIMING_VERSION = 1

from music_speaks.lyric_timing import build_lyric_timestamps
from music_speaks.voice_data import (
    DEFAULT_SYSTEM_VOICES,
    UI_LANGUAGE_LABELS,
    VOICE_PREVIEW_TEXTS,
    build_voice_metadata_map,
    _detect_lang_from_voice_id,
    _interface_language_label,
    _is_safe_voice_id,
)

import music_speaks.lyrics as lyrics_runtime

from music_speaks.lyrics import (
    VoiceCloneSingingUnavailable,
    build_music_option_args,
    clean_generated_lyrics,
    clone_voice,
    fallback_generated_lyrics,
    generate_lyrics_from_text_model,
    generate_voice_cover_audio,
    synthesize_speech,
    synthesize_voice_clone_singing,
)  # noqa: F401 – re-export for tests

from music_speaks.titles import (
    clean_song_title,
    fallback_song_title,
    normalize_generated_song_title,
)

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

JOBS: dict[str, dict[str, Any]] = {}
JOBS_LOCK = threading.RLock()
DRAFTS: dict[str, dict[str, Any]] = {}
DRAFTS_LOCK = threading.RLock()
VOICE_CACHE: dict[str, Any] = {
    "voices": list(DEFAULT_SYSTEM_VOICES),
    "voice_meta": build_voice_metadata_map(DEFAULT_SYSTEM_VOICES, fallback=True),
    "fallback": True,
    "fetched_at": 0.0,
}
VOICE_CACHE_LOCK = threading.RLock()

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
      --accent-cyan: #67e8f9;
      --accent-pink: #ff4fd8;
      --cyber-grid: rgba(103, 232, 249, 0.08);
      --glass-surface: linear-gradient(145deg, rgba(18,18,26,0.96), rgba(9,14,16,0.94));
      --glass-surface-soft: linear-gradient(180deg, rgba(17,22,31,0.96), rgba(10,10,18,0.92));
      --glass-raised: linear-gradient(135deg, rgba(25,25,36,0.96), rgba(16,19,28,0.92));
      --panel-border: rgba(103,232,249,0.16);
      --panel-outline: rgba(29,185,84,0.08);
      --panel-glow: 0 24px 60px rgba(0,0,0,0.26), 0 0 0 1px rgba(255,255,255,0.03) inset;
      --voice-icon-surface: linear-gradient(135deg, rgba(103,232,249,0.12), rgba(255,255,255,0.08));
      --voice-icon-surface-hover: linear-gradient(135deg, rgba(103,232,249,0.18), rgba(255,255,255,0.12));
      --voice-disabled-surface: rgba(255,255,255,0.06);
      --hero-blur: rgba(103,232,249,0.08);
      --hero-glow: rgba(255,79,216,0.12);
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
      --cyber-grid: rgba(14, 165, 233, 0.08);
      --glass-surface: linear-gradient(145deg, rgba(255,255,255,0.96), rgba(241,247,255,0.94));
      --glass-surface-soft: linear-gradient(180deg, rgba(255,255,255,0.98), rgba(242,247,252,0.95));
      --glass-raised: linear-gradient(135deg, rgba(255,255,255,0.98), rgba(237,243,252,0.95));
      --panel-border: rgba(14,165,233,0.16);
      --panel-outline: rgba(29,185,84,0.05);
      --panel-glow: 0 18px 42px rgba(8,26,62,0.08), 0 0 0 1px rgba(255,255,255,0.45) inset;
      --voice-icon-surface: linear-gradient(135deg, rgba(14,165,233,0.1), rgba(255,255,255,0.92));
      --voice-icon-surface-hover: linear-gradient(135deg, rgba(14,165,233,0.14), rgba(255,255,255,1));
      --voice-disabled-surface: rgba(207,218,233,0.55);
      --hero-blur: rgba(14,165,233,0.08);
      --hero-glow: rgba(236,72,153,0.08);
      --shadow-sm: 0 2px 8px rgba(0,0,0,0.08);
      --shadow-md: 0 4px 16px rgba(0,0,0,0.12);
      --shadow-lg: 0 8px 32px rgba(0,0,0,0.16);
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    html, body { height: 100%; }
    body {
      background:
        radial-gradient(circle at 16% 18%, var(--hero-blur), transparent 26%),
        radial-gradient(circle at 82% 12%, var(--hero-glow), transparent 24%),
        linear-gradient(180deg, rgba(255,255,255,0.02), transparent 24%),
        var(--bg-primary);
      color: var(--text-primary);
      font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      font-size: 14px;
      line-height: 1.5;
      overflow: hidden;
      position: relative;
    }
    body::before,
    body::after {
      content: "";
      position: fixed;
      inset: 0;
      pointer-events: none;
      z-index: 0;
    }
    body::before {
      background:
        linear-gradient(var(--cyber-grid) 1px, transparent 1px),
        linear-gradient(90deg, var(--cyber-grid) 1px, transparent 1px);
      background-size: 24px 24px;
      mask-image: linear-gradient(180deg, rgba(0,0,0,0.48), transparent 82%);
      opacity: 0.5;
    }
    body::after {
      background: radial-gradient(circle at 50% 0%, rgba(103,232,249,0.09), transparent 42%);
      opacity: 0.9;
    }
    /* App Layout */
    .app { position: relative; z-index: 1; display: flex; flex-direction: column; height: 100vh; }
    .app-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 0 24px;
      height: 64px;
      background: var(--glass-surface);
      border-bottom: 1px solid var(--panel-border);
      backdrop-filter: blur(18px);
      -webkit-backdrop-filter: blur(18px);
      box-shadow: 0 14px 36px rgba(0,0,0,0.12);
      flex-shrink: 0;
    }
    .logo { display: flex; align-items: center; gap: 10px; font-family: 'Space Grotesk', sans-serif; font-size: 20px; font-weight: 700; color: var(--text-primary); text-decoration: none; letter-spacing: 0.02em; }
    .ui-icon { width: 1.1em; height: 1.1em; display: inline-block; fill: currentColor; flex: 0 0 auto; }
    .logo-icon { width: 36px; height: 36px; background: linear-gradient(135deg, #1db954 0%, #34d399 42%, var(--accent-cyan) 100%); border-radius: 10px; display: flex; align-items: center; justify-content: center; color: #06100b; animation: glow 3s ease-in-out infinite; box-shadow: 0 0 0 1px rgba(255,255,255,0.1) inset, 0 0 24px rgba(29,185,84,0.18); }
    .logo-icon .ui-icon { width: 20px; height: 20px; }
    .header-actions { display: flex; gap: 8px; align-items: center; }
    .header-btn { display: flex; align-items: center; justify-content: center; width: 40px; height: 40px; border: 1px solid var(--panel-border); border-radius: 50%; background: var(--glass-raised); color: var(--text-secondary); cursor: pointer; font-size: 18px; transition: var(--transition); box-shadow: inset 0 1px 0 rgba(255,255,255,0.06); }
    .header-btn .ui-icon { width: 18px; height: 18px; }
    .header-btn:hover { background: var(--glass-raised); color: var(--text-primary); transform: scale(1.05); border-color: rgba(29,185,84,0.36); }
    .lang-btn-dropdown { position: relative; }
    .lang-toggle { width: auto; padding: 0 14px; border-radius: 20px; font-size: 13px; font-weight: 600; }
    .lang-menu-backdrop { position: fixed; inset: 0; z-index: 1490; background: rgba(5,8,15,0); opacity: 0; pointer-events: none; transition: opacity 0.2s ease, background 0.2s ease; }
    .lang-menu-backdrop.open { opacity: 1; pointer-events: auto; background: rgba(5,8,15,0.16); }
    .lang-menu {
      position: fixed;
      display: none;
      flex-direction: column;
      width: min(280px, calc(100vw - 32px));
      min-width: 220px;
      max-height: min(72vh, 560px);
      border: 1px solid var(--panel-border);
      border-radius: 18px;
      background: var(--glass-surface);
      box-shadow: 0 22px 52px rgba(0,0,0,0.24), 0 0 0 1px rgba(255,255,255,0.05) inset;
      overflow: hidden;
      z-index: 1500;
      backdrop-filter: blur(18px);
      -webkit-backdrop-filter: blur(18px);
    }
    .lang-menu.open { display: flex; }
    .lang-menu.mobile {
      left: 12px !important;
      right: 12px !important;
      top: auto !important;
      bottom: 12px !important;
      width: auto;
      min-width: 0;
      max-height: min(68vh, 560px);
      border-radius: 20px;
    }
    .lang-menu-head {
      padding: 11px 16px;
      font-size: 11px;
      color: var(--text-muted);
      border-bottom: 1px solid var(--panel-border);
      font-weight: 700;
      letter-spacing: 0.06em;
      text-transform: uppercase;
    }
    .lang-menu-list { flex: 1; min-height: 0; overflow-y: auto; -webkit-overflow-scrolling: touch; padding: 6px 0; }
    .lang-menu-section-label { padding: 10px 16px 6px; font-size: 11px; color: var(--text-muted); border-top: 1px solid var(--panel-border); margin-top: 4px; }
    .lang-menu-item { display: flex; align-items: center; justify-content: space-between; padding: 12px 16px; cursor: pointer; font-size: 14px; color: var(--text-secondary); transition: var(--transition); }
    .lang-menu-item:hover { background: var(--bg-tertiary); color: var(--text-primary); }
    .lang-menu-item.active { color: var(--accent); font-weight: 600; }
    .lang-menu-item .lang-check { font-size: 12px; }
    /* Main Layout */
    .app-body { display: flex; flex: 1; overflow: hidden; }
    /* Sidebar */
    .sidebar { width: 280px; background: var(--glass-surface); border-right: 1px solid var(--panel-border); backdrop-filter: blur(18px); -webkit-backdrop-filter: blur(18px); display: flex; flex-direction: column; flex-shrink: 0; box-shadow: inset -1px 0 0 rgba(255,255,255,0.03); }
    .sidebar-nav { padding: 16px 12px; }
    .nav-item { display: flex; align-items: center; gap: 12px; padding: 12px 16px; border: 1px solid transparent; border-radius: var(--radius-md); color: var(--text-secondary); text-decoration: none; font-weight: 500; cursor: pointer; transition: var(--transition); }
    .nav-item:hover { background: var(--glass-raised); color: var(--text-primary); border-color: rgba(103,232,249,0.16); }
    .nav-item.active { background: linear-gradient(135deg, rgba(29,185,84,0.18), rgba(103,232,249,0.08)); color: var(--accent); border-color: rgba(29,185,84,0.34); box-shadow: inset 3px 0 0 var(--accent), 0 12px 28px rgba(29,185,84,0.08); }
    .nav-icon { width: 24px; display: inline-flex; align-items: center; justify-content: center; color: currentColor; }
    .nav-icon .ui-icon { width: 19px; height: 19px; }
    .sidebar-section { padding: 8px 12px; }
    .sidebar-section-title { padding: 8px 16px; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px; color: var(--text-muted); }
    .playlist-item { display: flex; align-items: center; gap: 10px; padding: 8px 16px; border: 1px solid transparent; border-radius: var(--radius-sm); color: var(--text-secondary); cursor: pointer; transition: var(--transition); }
    .playlist-icon { width: 18px; display: inline-flex; align-items: center; justify-content: center; }
    .playlist-icon .ui-icon { width: 17px; height: 17px; }
    .playlist-item:hover { color: var(--text-primary); background: var(--glass-raised); border-color: rgba(103,232,249,0.14); }
    .playlist-item:hover { color: var(--text-primary); }
    /* Main Content */
    .main-content { flex: 1; overflow-y: auto; padding: 32px 40px 120px; background: transparent; position: relative; }
    .page-header { position: relative; margin-bottom: 32px; max-width: 900px; padding: 24px 26px; border: 1px solid var(--panel-border); border-radius: 24px; background: var(--glass-surface); box-shadow: var(--panel-glow); overflow: hidden; }
    .page-header::before { content: ""; position: absolute; inset: 0; pointer-events: none; background: linear-gradient(135deg, rgba(103,232,249,0.12), transparent 30%, transparent 70%, rgba(255,79,216,0.1)); opacity: 0.9; }
    .page-header::after { content: ""; position: absolute; inset: 0; pointer-events: none; background: repeating-linear-gradient(135deg, rgba(103,232,249,0.05) 0 1px, transparent 1px 22px); opacity: 0.45; }
    .page-title { position: relative; font-size: 32px; font-weight: 800; color: var(--text-primary); margin-bottom: 8px; letter-spacing: -0.02em; }
    .page-desc { position: relative; color: var(--text-secondary); font-size: 14px; max-width: 720px; }
    /* Create Form */
    .create-form { position: relative; background: var(--glass-surface); border: 1px solid var(--panel-border); border-radius: 24px; padding: 28px; max-width: 900px; box-shadow: var(--panel-glow); overflow: hidden; }
    .create-form::before { content: ""; position: absolute; inset: 0; pointer-events: none; background: linear-gradient(180deg, rgba(103,232,249,0.05), transparent 26%), radial-gradient(circle at top right, rgba(255,79,216,0.08), transparent 28%); }
    .form-section { margin-bottom: 24px; }
    .form-section:last-child { margin-bottom: 0; }
    .form-label { display: block; font-size: 13px; font-weight: 700; color: var(--text-primary); margin-bottom: 8px; }
    .form-hint { font-size: 12px; color: var(--text-muted); margin-top: 6px; }
    .form-input { width: 100%; padding: 12px 16px; background: var(--glass-raised); border: 1px solid var(--border); border-radius: var(--radius-md); color: var(--text-primary); font-size: 14px; transition: var(--transition); box-shadow: inset 0 1px 0 rgba(255,255,255,0.04); }
    .form-input:focus { outline: none; border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-dim); }
    .form-input::placeholder { color: var(--text-muted); }
    textarea.form-input { min-height: 120px; resize: vertical; line-height: 1.6; }
    /* Template Grid */
    .template-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin-top: 12px; }
    .template-btn { padding: 14px 12px; background: var(--bg-tertiary); border: 1px solid var(--border); border-radius: var(--radius-md); color: var(--text-secondary); font-size: 13px; font-weight: 500; cursor: pointer; transition: var(--transition); text-align: center; display: inline-flex; align-items: center; justify-content: center; gap: 8px; }
    .template-btn .ui-icon { width: 16px; height: 16px; }
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
    .voice-section { background: var(--glass-surface-soft); border: 1px solid var(--panel-border); border-radius: 18px; padding: 16px; box-shadow: inset 0 1px 0 rgba(255,255,255,0.04); }
    .voice-top-row { display: flex; align-items: center; gap: 12px; margin-bottom: 12px; }
    .voice-status { font-size: 13px; color: var(--text-secondary); }
    .voice-status.success { color: var(--accent); }
    .voice-status.error { color: var(--danger); }
    /* Voice Picker */
    .voice-picker-section { margin-bottom: 16px; }
    .voice-picker-label { font-size: 12px; font-weight: 600; color: var(--text-secondary); margin-bottom: 8px; display: flex; align-items: center; justify-content: space-between; gap: 12px; }
    .voice-search-row { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
    .voice-search-box { flex: 1; height: 32px; padding: 0 10px; background: var(--glass-raised); border: 1px solid var(--panel-border); border-radius: 8px; color: var(--text-primary); font-size: 12px; outline: none; transition: var(--transition); }
    .voice-search-box:focus { border-color: var(--accent); }
    .voice-search-box::placeholder { color: var(--text-muted); }
    .voice-filter-chips { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 8px; }
    .voice-filter-chip { padding: 4px 10px; border-radius: 999px; font-size: 10px; font-weight: 700; cursor: pointer; background: var(--bg-tertiary); border: 1px solid var(--border); color: var(--text-secondary); transition: var(--transition); }
    .voice-filter-chip:hover { border-color: var(--accent); color: var(--text-primary); }
    .voice-filter-chip.active { background: var(--accent-dim); border-color: var(--accent); color: var(--accent); }
    .voice-picker-scroll { position: relative; isolation: isolate; height: 308px; overflow: hidden; overscroll-behavior: contain; border: 1px solid var(--panel-border); border-radius: 14px; background: var(--glass-surface); box-shadow: inset 0 1px 0 rgba(255,255,255,0.04), 0 0 0 1px var(--panel-outline), 0 20px 40px rgba(0,0,0,0.18); padding: 8px; }
    .voice-picker-scroll::before { content: ""; position: absolute; inset: 0; pointer-events: none; opacity: 0.58; background: linear-gradient(180deg, rgba(103,232,249,0.08), transparent 22%), repeating-linear-gradient(135deg, rgba(103,232,249,0.06) 0 1px, transparent 1px 18px); }
    .voice-picker-shell { display: grid; grid-template-columns: 156px minmax(0, 1fr); gap: 8px; height: 100%; min-height: 0; }
    .voice-lang-list,
    .voice-option-list { min-height: 0; overflow-y: auto; overscroll-behavior: contain; -webkit-overflow-scrolling: touch; scrollbar-width: thin; scrollbar-color: var(--border-light) transparent; }
    .voice-lang-list::-webkit-scrollbar,
    .voice-option-list::-webkit-scrollbar { width: 6px; height: 6px; }
    .voice-lang-list::-webkit-scrollbar-track,
    .voice-option-list::-webkit-scrollbar-track { background: transparent; }
    .voice-lang-list::-webkit-scrollbar-thumb,
    .voice-option-list::-webkit-scrollbar-thumb { background: var(--border-light); border-radius: 3px; }
    .voice-lang-list::-webkit-scrollbar-thumb:hover,
    .voice-option-list::-webkit-scrollbar-thumb:hover { background: var(--accent); }
    .voice-lang-list { display: flex; flex-direction: column; gap: 6px; padding: 4px; border: 1px solid var(--panel-border); border-radius: 10px; background: var(--glass-surface-soft); }
    .voice-lang-btn { width: 100%; min-height: 34px; display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: center; gap: 8px; padding: 8px 9px; border: 1px solid transparent; border-radius: 8px; background: transparent; color: var(--text-secondary); font-size: 11px; font-weight: 700; text-align: left; cursor: pointer; transition: var(--transition); }
    .voice-lang-btn:hover { color: var(--text-primary); background: var(--bg-tertiary); border-color: var(--border); }
    .voice-lang-btn.active { color: var(--accent); background: linear-gradient(135deg, rgba(29,185,84,0.16), rgba(103,232,249,0.08)); border-color: rgba(29,185,84,0.55); box-shadow: inset 3px 0 0 var(--accent), 0 0 24px rgba(29,185,84,0.12); }
    .voice-lang-name { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .voice-lang-count { display: inline-flex; align-items: center; justify-content: center; min-width: 24px; height: 18px; padding: 0 6px; border-radius: 999px; background: var(--bg-elevated); color: var(--text-muted); font-size: 10px; font-weight: 700; }
    .voice-lang-btn.active .voice-lang-count { background: rgba(29,185,84,0.2); color: var(--accent); }
    .voice-custom-row { margin-top: auto; padding-top: 6px; border-top: 1px solid var(--panel-border); }
    .voice-custom-btn { width: 100%; min-height: 34px; padding: 8px 9px; background: var(--bg-elevated); border: 1px solid var(--border); border-radius: 8px; font-size: 11px; font-weight: 700; color: var(--text-primary); cursor: pointer; transition: var(--transition); text-align: left; display: inline-flex; align-items: center; gap: 7px; }
    .voice-custom-btn .ui-icon { width: 14px; height: 14px; }
    .voice-custom-btn:hover { border-color: var(--accent); color: var(--accent); }
    .voice-custom-btn.active { background: var(--accent-dim); border-color: var(--accent); color: var(--accent); }
    .voice-custom-label { display: block; margin-top: 5px; font-size: 10px; line-height: 1.3; color: var(--text-muted); }
    .voice-option-list { border: 1px solid var(--panel-border); border-radius: 10px; background: var(--glass-surface-soft); }
    .voice-options-head { position: sticky; top: 0; z-index: 2; display: flex; align-items: center; justify-content: space-between; gap: 10px; padding: 8px 10px; background: var(--glass-surface); border-bottom: 1px solid var(--panel-border); backdrop-filter: blur(10px); box-shadow: 0 10px 26px rgba(0,0,0,0.12); }
    .voice-options-title { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 11px; font-weight: 800; color: var(--text-primary); }
    .voice-options-meta { flex: 0 0 auto; color: var(--text-muted); font-size: 10px; font-weight: 700; }
    .voice-items { display: grid; grid-template-columns: repeat(auto-fill, minmax(136px, 1fr)); gap: 7px; padding: 9px; align-content: start; }
    .voice-pill { position: relative; width: 100%; min-width: 0; min-height: 48px; display: grid; grid-template-columns: minmax(0, 1fr) 28px; align-items: center; gap: 10px; padding: 8px 8px 8px 11px; background: var(--glass-raised); border: 1px solid rgba(103,232,249,0.12); border-radius: 10px; font-size: 11px; color: var(--text-secondary); cursor: pointer; transition: var(--transition); line-height: 1.2; text-align: left; overflow: hidden; box-shadow: inset 0 1px 0 rgba(255,255,255,0.04); }
    .voice-pill::before { content: ""; position: absolute; inset: 0; pointer-events: none; opacity: 0; background: linear-gradient(90deg, rgba(29,185,84,0.14), rgba(103,232,249,0.08)); transition: opacity 0.2s ease; }
    .voice-pill:hover { border-color: rgba(29,185,84,0.6); color: var(--text-primary); transform: translateY(-1px); box-shadow: 0 12px 30px rgba(0,0,0,0.14); }
    .voice-pill:hover::before { opacity: 1; }
    .voice-pill.selected { background: linear-gradient(135deg, rgba(29,185,84,0.18), rgba(103,232,249,0.12)); border-color: rgba(29,185,84,0.7); color: var(--accent); box-shadow: inset 0 0 0 1px rgba(29,185,84,0.12), 0 0 30px rgba(29,185,84,0.12); }
    .voice-pill.playing { background: linear-gradient(135deg, rgba(29,185,84,0.2), rgba(103,232,249,0.16)); border-color: var(--accent); color: var(--accent); animation: pulse 1s ease-in-out infinite; }
    .voice-pill-copy { min-width: 0; display: flex; flex-direction: column; gap: 3px; }
    .voice-pill-meta { font-size: 9px; line-height: 1.35; color: var(--text-muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; text-transform: uppercase; letter-spacing: 0.05em; }
    .voice-pill.selected .voice-pill-meta,
    .voice-pill.playing .voice-pill-meta { color: rgba(255,255,255,0.72); }
    .voice-pill .play-icon { width: 28px; height: 28px; display: inline-flex; align-items: center; justify-content: center; border-radius: 50%; background: var(--voice-icon-surface); color: var(--accent); font-size: 9px; box-shadow: 0 2px 8px rgba(0,0,0,0.14), 0 0 0 1px rgba(103,232,249,0.12); transition: var(--transition); }
    .voice-pill:hover .play-icon { background: var(--voice-icon-surface-hover); }
    .voice-pill .play-icon.disabled { color: var(--text-muted); background: var(--voice-disabled-surface); box-shadow: inset 0 0 0 1px rgba(255,255,255,0.08); }
    [data-theme="light"] .voice-pill.selected .voice-pill-meta,
    [data-theme="light"] .voice-pill.playing .voice-pill-meta { color: rgba(29,29,31,0.62); }
    .voice-pill .play-icon svg { width: 11px; height: 11px; fill: currentColor; }
    .voice-pill .voice-name { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-weight: 700; }
    .voice-empty { padding: 18px; text-align: center; color: var(--text-muted); font-size: 12px; }
    /* Advanced Parameters */
    .advanced-toggle { display: flex; align-items: center; justify-content: space-between; padding: 14px 16px; background: var(--bg-tertiary); border: 1px solid var(--border); border-radius: var(--radius-md); cursor: pointer; margin-top: 16px; }
    .advanced-toggle:hover { border-color: var(--border-light); }
    .advanced-toggle span { font-size: 13px; font-weight: 600; color: var(--text-primary); }
    .advanced-toggle-icon { color: var(--text-muted); transition: transform 0.2s; }
    .advanced-panel { margin-top: 12px; padding: 20px; background: var(--bg-tertiary); border: 1px solid var(--border); border-radius: var(--radius-md); display: none; }
    .advanced-panel.open { display: block; }
    .param-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; }
    .param-field { display: flex; flex-direction: column; gap: 6px; }
    .param-field label { font-size: 12px; font-weight: 600; color: var(--text-secondary); }
    .param-field input { padding: 10px 12px; background: var(--bg-secondary); border: 1px solid var(--border); border-radius: var(--radius-sm); color: var(--text-primary); font-size: 13px; }
    .param-field input:focus { outline: none; border-color: var(--accent); }
    /* Actions */
    .form-actions { display: flex; gap: 12px; margin-top: 24px; flex-wrap: wrap; }
    .btn-primary { flex: 1; padding: 14px 24px; background: var(--accent); border: none; border-radius: var(--radius-md); color: #000; font-size: 14px; font-weight: 700; cursor: pointer; transition: var(--transition); display: flex; align-items: center; justify-content: center; gap: 8px; }
    .btn-primary:hover { background: var(--accent-hover); transform: translateY(-1px); }
    .btn-primary:disabled { opacity: 0.6; cursor: not-allowed; transform: none; }
    .btn-secondary { padding: 14px 20px; background: var(--bg-tertiary); border: 1px solid var(--border); border-radius: var(--radius-md); color: var(--text-primary); font-size: 13px; font-weight: 600; cursor: pointer; transition: var(--transition); }
    .btn-secondary:hover { background: var(--bg-elevated); border-color: var(--border-light); }
    .btn-voice { padding: 12px 16px; background: var(--bg-elevated); border: 1px solid var(--border); border-radius: var(--radius-md); color: var(--text-primary); font-size: 13px; font-weight: 600; cursor: pointer; transition: var(--transition); display: flex; align-items: center; gap: 8px; }
    .btn-voice .ui-icon { width: 16px; height: 16px; }
    .btn-voice:hover { border-color: var(--accent); color: var(--accent); }
    .error-text { color: var(--danger); font-size: 13px; min-height: 20px; margin-top: 12px; }
    /* Jobs Panel */
    .jobs-panel { background: var(--glass-surface); border: 1px solid var(--panel-border); border-radius: 22px; padding: 20px; margin-top: 24px; max-width: 900px; box-shadow: var(--panel-glow); }
    .jobs-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
    .jobs-title { font-size: 16px; font-weight: 700; color: var(--text-primary); }
    .jobs-list { display: flex; flex-direction: column; gap: 10px; max-height: 400px; overflow-y: auto; }
    .job-card { position: relative; overflow: hidden; display: flex; align-items: center; gap: 14px; padding: 14px 16px; background: var(--glass-raised); border: 1px solid var(--panel-border); border-radius: var(--radius-md); transition: var(--transition); cursor: pointer; box-shadow: inset 0 1px 0 rgba(255,255,255,0.04); }
    .job-card::before { content: ""; position: absolute; inset: 0; pointer-events: none; opacity: 0.35; background: repeating-linear-gradient(135deg, rgba(103,232,249,0.04) 0 1px, transparent 1px 20px); }
    .job-card::after { content: ""; position: absolute; left: 0; top: 0; bottom: 0; width: 3px; background: rgba(103,232,249,0.18); }
    .job-card:hover { border-color: var(--accent); transform: translateY(-2px); box-shadow: 0 18px 42px rgba(0,0,0,0.24), 0 0 0 1px rgba(29,185,84,0.08); }
    .job-art { width: 56px; height: 56px; background: linear-gradient(135deg, #1DB954 0%, #34d399 48%, var(--accent-cyan) 100%); border-radius: var(--radius-sm); display: flex; align-items: center; justify-content: center; font-size: 24px; flex-shrink: 0; transition: var(--transition); box-shadow: 0 14px 28px rgba(29,185,84,0.18); }
    .job-card:hover .job-art { transform: scale(1.05); }
    .job-art svg { width: 26px; height: 26px; fill: currentColor; color: #fff; }
    .job-info { flex: 1; min-width: 0; }
    .job-title { font-size: 14px; font-weight: 600; color: var(--text-primary); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; margin-bottom: 4px; }
    .job-meta { display: flex; gap: 8px; font-size: 12px; color: var(--text-muted); }
    .job-badge { padding: 3px 8px; border-radius: 999px; font-size: 11px; font-weight: 700; text-transform: uppercase; }
    .job-badge.queued { background: rgba(255, 171, 0, 0.15); color: var(--warning); }
    .job-badge.running { background: rgba(255, 171, 0, 0.15); color: var(--warning); }
    .job-badge.completed { background: var(--accent-dim); color: var(--accent); }
    .job-badge.error { background: rgba(255, 82, 82, 0.15); color: var(--danger); }
    .job-card.status-completed::after { background: linear-gradient(180deg, var(--accent), var(--accent-cyan)); }
    .job-card.status-running::after,
    .job-card.status-queued::after { background: linear-gradient(180deg, var(--warning), var(--accent-cyan)); }
    .job-card.status-error::after { background: linear-gradient(180deg, var(--danger), rgba(255,255,255,0.2)); }
    .job-actions { display: flex; gap: 8px; }
    .job-action-btn { padding: 8px 12px; background: var(--glass-raised); border: 1px solid var(--border); border-radius: var(--radius-sm); color: var(--text-secondary); font-size: 12px; font-weight: 600; cursor: pointer; transition: var(--transition); display: inline-flex; align-items: center; gap: 6px; text-decoration: none; }
    .job-action-btn svg { width: 13px; height: 13px; fill: currentColor; }
    .job-action-btn:hover { border-color: var(--accent); color: var(--accent); }
    .job-action-btn.download { background: var(--accent); color: #000; border: none; }
    .job-action-btn.download:hover { background: var(--accent-hover); }
    .job-empty { text-align: center; padding: 40px 20px; color: var(--text-muted); }
    .job-progress { display: flex; align-items: center; gap: 10px; }
    .progress-bar { flex: 1; height: 4px; background: var(--border); border-radius: 2px; overflow: hidden; }
    .progress-fill { height: 100%; background: var(--accent); transition: width 0.3s; }
    /* Bottom Player */
    .player { position: fixed; bottom: 0; left: 0; right: 0; min-height: 104px; background: rgba(10,10,15,0.92); border-top: 1px solid rgba(103,232,249,0.18); box-shadow: 0 -18px 60px rgba(0,0,0,0.5), 0 -1px 0 rgba(29,185,84,0.08) inset; backdrop-filter: blur(24px); -webkit-backdrop-filter: blur(24px); display: flex; align-items: center; padding: 16px 24px; gap: 20px; z-index: 100; overflow: visible; isolation: isolate; }
    .player::before { content: ""; position: absolute; inset: 0; background: linear-gradient(180deg, rgba(255,255,255,0.14), rgba(255,255,255,0)); pointer-events: none; }
    .player::after { content: ""; position: absolute; left: 0; right: 0; top: 0; height: 1px; background: linear-gradient(90deg, transparent, rgba(103,232,249,0.9), rgba(29,185,84,0.95), transparent); opacity: 0.9; pointer-events: none; }
    .player > * { position: relative; z-index: 1; }
    .player-track { display: flex; align-items: center; gap: 14px; width: 300px; min-width: 0; flex-shrink: 0; }
    .player-art { width: 62px; height: 62px; background: linear-gradient(135deg, #1DB954 0%, #34d399 48%, #0f766e 100%); border-radius: 8px; display: flex; align-items: center; justify-content: center; color: #fff; box-shadow: 0 14px 34px rgba(29,185,84,0.24), inset 0 1px 0 rgba(255,255,255,0.24); }
    .player-art svg { width: 32px; height: 32px; fill: currentColor; filter: drop-shadow(0 6px 12px rgba(0,0,0,0.28)); }
    .player-info { min-width: 0; }
    .player-title { font-size: 14px; font-weight: 800; color: var(--text-primary); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; letter-spacing: -0.01em; }
    .player-artist { font-size: 12px; color: rgba(255,255,255,0.5); margin-top: 3px; }
    .player-controls { flex: 1; min-width: 220px; display: flex; flex-direction: column; align-items: center; gap: 10px; }
    .player-buttons { display: flex; align-items: center; gap: 12px; }
    .player-btn { width: 38px; height: 38px; border: 1px solid rgba(255,255,255,0.08); border-radius: 8px; background: rgba(255,255,255,0.05); color: var(--text-secondary); cursor: pointer; padding: 0; display: inline-flex; align-items: center; justify-content: center; transition: var(--transition); }
    .player-btn svg { width: 17px; height: 17px; fill: currentColor; }
    .player-btn:hover { color: var(--text-primary); background: rgba(255,255,255,0.1); transform: translateY(-1px); }
    .player-btn.play { width: 44px; height: 44px; background: #1DB954; color: #0a0a0f; border: none; border-radius: 50%; box-shadow: 0 4px 16px rgba(29,185,84,0.4), 0 0 0 4px rgba(29,185,84,0.15); transition: all 0.2s cubic-bezier(0.34, 1.56, 0.64, 1); }
    .player-btn.play svg { width: 18px; height: 18px; }
    .player-btn.play:hover { transform: scale(1.08); box-shadow: 0 6px 24px rgba(29,185,84,0.5), 0 0 0 6px rgba(29,185,84,0.2); }
    .player-btn.play:active { transform: scale(0.95); }
    .player-progress { display: flex; align-items: center; gap: 10px; width: 100%; max-width: 620px; }
    .player-time { font-size: 11px; color: var(--text-muted); min-width: 40px; text-align: center; font-variant-numeric: tabular-nums; }
    .player-bar { flex: 1; height: 4px; background: rgba(255,255,255,0.1); border-radius: 4px; cursor: pointer; position: relative; transition: height 0.15s ease; }
    .player-bar:hover { height: 6px; }
    .player-bar-fill { height: 100%; background: linear-gradient(90deg, #1DB954, #34d399); border-radius: 4px; width: 0%; transition: width 0.1s; box-shadow: 0 0 12px rgba(29,185,84,0.5); pointer-events: none; }
    .player-bar:hover .player-bar-fill { background: linear-gradient(90deg, #1ed760, #67e8f9); }
    .player-extra { display: flex; align-items: center; justify-content: flex-end; gap: 14px; width: 300px; flex-shrink: 0; }
    .player-volume { display: flex; align-items: center; gap: 8px; width: 130px; flex-shrink: 0; }
    .volume-icon { width: 18px; height: 18px; color: var(--text-muted); display: inline-flex; align-items: center; justify-content: center; }
    .volume-icon svg { width: 18px; height: 18px; fill: currentColor; }
    .volume-slider { flex: 1; height: 4px; background: rgba(255,255,255,0.1); border-radius: 4px; cursor: pointer; position: relative; transition: height 0.15s ease; }
    .volume-slider:hover { height: 6px; }
    .volume-fill { height: 100%; background: rgba(255,255,255,0.58); border-radius: 5px; width: 70%; }
    .player-lyrics { flex: 1; max-width: 380px; overflow: hidden; text-align: center; padding: 0 12px; }
    .lyrics-text { font-size: 13px; color: var(--text-secondary); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; transition: color 0.3s; }
    .lyrics-text.playing { color: var(--accent); }
    .lyrics-toggle { min-width: 78px; height: 36px; padding: 0 14px; border: 1px solid rgba(255,255,255,0.12); border-radius: 8px; background: rgba(255,255,255,0.06); color: var(--text-secondary); font-size: 12px; font-weight: 800; letter-spacing: 0.01em; cursor: pointer; transition: var(--transition); }
    .lyrics-toggle:hover:not(:disabled), .lyrics-toggle.active { background: rgba(29,185,84,0.18); border-color: rgba(29,185,84,0.52); color: var(--accent); }
    .lyrics-toggle:disabled { opacity: 0.45; cursor: not-allowed; }
    .lyrics-panel { position: fixed; right: 24px; bottom: 120px; width: min(480px, calc(100vw - 48px)); max-height: min(60vh, 560px); display: flex; flex-direction: column; z-index: 101; border: 1px solid rgba(103,232,249,0.18); border-radius: 20px; background: rgba(10,10,15,0.94); backdrop-filter: blur(24px); -webkit-backdrop-filter: blur(24px); box-shadow: 0 24px 80px rgba(0,0,0,0.5), 0 0 0 1px rgba(255,255,255,0.05) inset, 0 0 30px rgba(29,185,84,0.08); overflow: hidden; opacity: 0; transform: translateY(20px) scale(0.96); pointer-events: none; transition: opacity 0.28s cubic-bezier(0.16, 1, 0.3, 1), transform 0.28s cubic-bezier(0.16, 1, 0.3, 1); isolation: isolate; }
    .lyrics-panel.open { opacity: 1; transform: translateY(0) scale(1); pointer-events: auto; }
    .lyrics-panel-header { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 16px 18px; border-bottom: 1px solid rgba(103,232,249,0.14); background: linear-gradient(180deg, rgba(103,232,249,0.08), rgba(255,255,255,0)); }
    .lyrics-panel-title { font-size: 13px; font-weight: 900; color: var(--text-primary); letter-spacing: 0.08em; text-transform: uppercase; }
    .lyrics-panel-close { width: 32px; height: 32px; border: 1px solid rgba(255,255,255,0.1); border-radius: 50%; background: rgba(255,255,255,0.05); color: rgba(255,255,255,0.6); cursor: pointer; display: flex; align-items: center; justify-content: center; transition: all 0.25s cubic-bezier(0.34, 1.56, 0.64, 1); flex-shrink: 0; }
    .lyrics-panel-close:hover { background: rgba(255,255,255,0.1); color: #ffffff; border-color: rgba(255,255,255,0.2); transform: rotate(90deg); }
    .lyrics-panel-close:active { transform: rotate(90deg) scale(0.9); }
    .lyrics-lines { max-height: calc(min(56vh, 540px) - 58px); overflow-y: auto; padding: 18px 20px 24px; scroll-behavior: smooth; }
    .lyrics-line { color: var(--text-secondary); font-size: 15px; line-height: 1.7; padding: 6px 10px; border-radius: 8px; transition: var(--transition); }
    .lyrics-line.section { margin-top: 8px; color: var(--text-muted); font-size: 11px; font-weight: 900; letter-spacing: 0.1em; text-transform: uppercase; }
    .lyrics-line.active { color: #06100b; background: linear-gradient(90deg, #1DB954, #9af7be); box-shadow: 0 10px 28px rgba(29,185,84,0.18); transform: translateX(4px); font-weight: 800; }
    .lyrics-empty { color: var(--text-muted); font-size: 13px; line-height: 1.6; padding: 14px 10px; }
    /* Fullscreen Lyrics Modal */
    .lyrics-fullscreen-btn { width: 36px; height: 36px; border: 1px solid rgba(255,255,255,0.12); border-radius: 8px; background: rgba(255,255,255,0.06); color: var(--text-secondary); font-size: 16px; cursor: pointer; transition: var(--transition); display: flex; align-items: center; justify-content: center; }
    .lyrics-fullscreen-btn:hover { background: rgba(29,185,84,0.18); border-color: rgba(29,185,84,0.52); color: var(--accent); }
    #lyricsFullscreenModal { display: none; position: fixed; inset: 0; z-index: 9999; flex-direction: column; background: radial-gradient(ellipse at center, rgba(29,185,84,0.08) 0%, #0a0a0f 70%); backdrop-filter: blur(40px); -webkit-backdrop-filter: blur(40px); }
    #lyricsFullscreenModal.open { display: flex; animation: lfm-in 0.35s cubic-bezier(0.16, 1, 0.3, 1) forwards; }
    @keyframes lfm-in { from { opacity: 0; transform: scale(1.05); } to { opacity: 1; transform: scale(1); } }
    .lfm-bg { position: absolute; inset: 0; background: linear-gradient(160deg, #0d0d1a 0%, #0a0a14 50%, #06060e 100%); backdrop-filter: blur(40px); -webkit-backdrop-filter: blur(40px); }
    .lfm-header { position: relative; z-index: 1; display: flex; align-items: center; justify-content: space-between; padding: 24px 32px 16px; border-bottom: 1px solid rgba(255,255,255,0.07); }
    .lfm-track-info { flex: 1; min-width: 0; }
    .lfm-title { font-size: 18px; font-weight: 800; color: #fff; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .lfm-artist { font-size: 13px; color: rgba(255,255,255,0.45); margin-top: 2px; }
    .lfm-close { width: 44px; height: 44px; border: 1px solid rgba(255,255,255,0.15); border-radius: 50%; background: rgba(255,255,255,0.06); backdrop-filter: blur(8px); color: rgba(255,255,255,0.7); font-size: 18px; cursor: pointer; display: flex; align-items: center; justify-content: center; transition: all 0.25s cubic-bezier(0.34, 1.56, 0.64, 1); flex-shrink: 0; }
    .lfm-close:hover { background: rgba(255,255,255,0.12); color: #ffffff; border-color: rgba(255,255,255,0.25); transform: scale(1.1) rotate(90deg); }
    .lfm-body { position: relative; z-index: 1; flex: 1; overflow: hidden; display: flex; align-items: center; justify-content: center; }
    .lfm-lines { width: 100%; max-width: 720px; height: 100%; overflow-y: auto; padding: 40px 32px; scroll-behavior: smooth; }
    .lfm-lines::-webkit-scrollbar { width: 4px; }
    .lfm-lines::-webkit-scrollbar-track { background: transparent; }
    .lfm-lines::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.15); border-radius: 2px; }
    .lfm-line { color: rgba(255,255,255,0.3); font-size: clamp(18px, 4vw, 28px); font-weight: 600; line-height: 1.6; padding: 12px 20px; border-radius: 12px; transition: all 0.35s cubic-bezier(0.16, 1, 0.3, 1); text-align: center; }
    .lfm-line.section { font-size: 11px; font-weight: 900; letter-spacing: 0.12em; text-transform: uppercase; color: rgba(255,255,255,0.2); margin-top: 20px; }
    .lfm-line.active { color: #fff; background: linear-gradient(135deg, rgba(29,185,84,0.22), rgba(154,247,190,0.12)); box-shadow: 0 8px 32px rgba(29,185,84,0.18); transform: scale(1.04); font-weight: 800; text-shadow: 0 0 40px rgba(29,185,84,0.4); }
    .lfm-empty { color: rgba(255,255,255,0.35); font-size: 16px; text-align: center; padding: 60px 0; }
    .lfm-footer { position: relative; z-index: 1; padding: 16px 32px 28px; border-top: 1px solid rgba(255,255,255,0.07); }
    .lfm-current-line { text-align: center; font-size: 13px; color: rgba(255,255,255,0.4); margin-top: 10px; min-height: 20px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .lfm-controls { display: flex; align-items: center; justify-content: center; gap: 20px; margin-bottom: 14px; }
    .lfm-btn { width: 44px; height: 44px; border: 1px solid rgba(255,255,255,0.15); border-radius: 50%; background: rgba(255,255,255,0.06); color: rgba(255,255,255,0.7); font-size: 14px; cursor: pointer; display: flex; align-items: center; justify-content: center; transition: var(--transition); }
    .lfm-btn:hover { background: rgba(255,255,255,0.12); color: #fff; }
    .lfm-play { width: 56px; height: 56px; background: var(--accent); border-color: var(--accent); color: #06100b; font-size: 18px; }
    .lfm-play:hover { background: #1ed760; transform: scale(1.06); }
    .lfm-progress-row { display: flex; align-items: center; gap: 12px; }
    .lfm-time { font-size: 12px; color: rgba(255,255,255,0.45); font-variant-numeric: tabular-nums; min-width: 36px; font-family: monospace; }
    .lfm-bar { flex: 1; height: 4px; background: rgba(255,255,255,0.12); border-radius: 2px; cursor: pointer; overflow: hidden; }
    .lfm-bar-fill { height: 100%; background: linear-gradient(90deg, #1DB954, #9af7be); border-radius: 2px; width: 0; transition: width 0.1s linear; }
    .lfm-bar:hover { height: 6px; }
    @media (max-width: 600px) {
      .lfm-header { padding: 16px 20px 12px; }
      .lfm-lines { padding: 24px 20px; }
      .lfm-footer { padding: 12px 20px 20px; }
      .lfm-line { font-size: 20px; padding: 10px 16px; }
    }
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
    .rec-step .ui-icon { width: 14px; height: 14px; vertical-align: -2px; margin-left: 4px; }
    .rec-bar { height: 4px; background: var(--border); border-radius: 2px; overflow: hidden; }
    .rec-bar-fill { height: 100%; background: var(--accent); transition: width 0.4s ease; }
    .rec-script { background: var(--bg-tertiary); border: 1px solid var(--border); border-radius: var(--radius-md); padding: 20px; margin: 16px 0; text-align: center; }
    .rec-script-text { font-size: 18px; line-height: 1.6; color: var(--text-primary); }
    .rec-countdown { font-size: 48px; font-weight: 800; color: var(--accent); text-align: center; margin: 20px 0; }
    .rec-instruction { font-size: 13px; color: var(--text-muted); text-align: center; margin-bottom: 16px; }
    .rec-controls { display: flex; gap: 12px; justify-content: center; }
    .rec-btn { padding: 12px 24px; border: none; border-radius: var(--radius-md); font-size: 14px; font-weight: 600; cursor: pointer; transition: var(--transition); display: inline-flex; align-items: center; justify-content: center; gap: 8px; }
    .rec-btn .ui-icon { width: 14px; height: 14px; }
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
      .voice-picker-shell { grid-template-columns: 146px minmax(0, 1fr); }
      .voice-items { grid-template-columns: repeat(auto-fill, minmax(126px, 1fr)); }
    }
    @media (max-width: 768px) {
      .sidebar { display: none; }
      .main-content { padding: 20px 16px 142px; }
      .lang-menu-backdrop.open { background: rgba(5,8,15,0.52); }
      .lang-menu.mobile { bottom: 16px !important; }
      .lang-menu-item { padding: 13px 16px; }
      .player { min-height: 126px; padding: 12px 16px; gap: 10px; flex-wrap: wrap; }
      .player-track { width: calc(100% - 96px); flex: 1 1 220px; }
      .player-art { width: 52px; height: 52px; border-radius: 8px; }
      .player-controls { order: 3; flex: 1 1 100%; min-width: 0; }
      .player-extra { width: auto; margin-left: auto; }
      .player-volume { display: none; }
      .player-lyrics { max-width: 160px; padding: 0 4px; }
      .lyrics-panel { left: 16px; right: 16px; bottom: 142px; width: auto; max-height: 48vh; }
      .voice-picker-label { align-items: flex-start; gap: 8px; flex-direction: column; }
      .voice-picker-selected { max-width: 100%; }
      .voice-picker-scroll { height: auto; min-height: 360px; }
      .voice-picker-shell { grid-template-columns: 1fr; grid-template-rows: 132px minmax(210px, 1fr); }
      .voice-lang-list { display: grid; grid-template-columns: repeat(auto-fill, minmax(138px, 1fr)); align-content: start; }
      .voice-custom-row { margin-top: 0; grid-column: 1 / -1; }
      .voice-items { grid-template-columns: repeat(auto-fill, minmax(124px, 1fr)); }
    }
    @media (max-width: 420px) {
      .voice-picker-scroll { min-height: 382px; }
      .voice-picker-shell { grid-template-rows: 128px 236px; }
      .voice-lang-list { grid-template-columns: 1fr; }
      .voice-items { grid-template-columns: 1fr; }
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

    .animate-spin { animation: spin 1s linear infinite; }
    .animate-pulse { animation: pulse 1.5s ease-in-out infinite; }
    .animate-bounce-in { animation: bounce-in 0.5s ease-out forwards; }
    .animate-shake { animation: shake 0.4s ease-in-out; }
    .animate-fade-in { animation: fade-in 0.3s ease-out forwards; }
    .animate-slide-up { animation: slide-up 0.4s ease-out forwards; }
    .animate-slide-down { animation: slide-down 0.4s ease-out forwards; }
    .animate-glow { animation: glow 2s ease-in-out infinite; }
    .animate-beat { animation: beat 1s ease-in-out; }

    /* Loading spinner */
    .spinner {
      width: 18px;
      height: 18px;
      border: 2px solid rgba(0,0,0,0.2);
      border-top-color: currentColor;
      border-radius: 50%;
      animation: spin 0.8s linear infinite;
      display: inline-block;
    }
    .spinner-white {
      border-color: rgba(255,255,255,0.2);
      border-top-color: #fff;
    }

    /* Sound toggle */
    .sound-toggle { position: relative; }
    .sound-toggle.on .sound-icon { opacity: 1; }
    .sound-toggle.off .sound-icon { opacity: 0.4; }
    /* ── Splash Screen ─────────────────────────────────────────────── */
    #splash {
      position: fixed; inset: 0; z-index: 9999;
      display: flex; flex-direction: column; align-items: center; justify-content: center;
      background: #000;
      overflow: hidden;
    }
    #splash canvas {
      position: absolute; inset: 0; width: 100%; height: 100%;
    }
    #splash-inner {
      position: relative; z-index: 2; text-align: center;
      animation: splash-fade-in 1.2s cubic-bezier(0.16, 1, 0.3, 1) forwards;
    }
    @keyframes splash-fade-in {
      0% { opacity: 0; transform: translateY(24px); }
      100% { opacity: 1; transform: translateY(0); }
    }
    #splash-wordmark {
      font-family: 'Space Grotesk', 'SF Pro Display', -apple-system, sans-serif;
      font-size: clamp(52px, 10vw, 96px);
      font-weight: 800;
      letter-spacing: -0.03em;
      color: #fff;
      line-height: 1;
      margin-bottom: 16px;
      background: linear-gradient(135deg, #fff 0%, #a3ffc8 50%, #fff 100%);
      background-size: 200%;
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
      background-clip: text;
      animation: wordmark-shimmer 4s ease-in-out infinite;
    }
    @keyframes wordmark-shimmer {
      0%, 100% { background-position: 0% 50%; }
      50% { background-position: 100% 50%; }
    }
    #splash-tagline {
      font-size: clamp(16px, 2.5vw, 22px);
      color: rgba(255,255,255,0.55);
      font-weight: 400;
      letter-spacing: 0.04em;
      margin-bottom: 56px;
    }
    #splash-enter {
      display: inline-flex; align-items: center; gap: 10px;
      padding: 16px 40px;
      background: rgba(255,255,255,0.1);
      border: 1px solid rgba(255,255,255,0.2);
      border-radius: 100px;
      color: rgba(255,255,255,0.9);
      font-size: 15px; font-weight: 600;
      letter-spacing: 0.02em;
      cursor: pointer;
      transition: all 0.3s cubic-bezier(0.16, 1, 0.3, 1);
      backdrop-filter: blur(12px);
      -webkit-backdrop-filter: blur(12px);
    }
    #splash-enter:hover {
      background: rgba(255,255,255,0.18);
      border-color: rgba(255,255,255,0.35);
      transform: scale(1.04);
    }
    #splash-enter .arrow { font-size: 18px; transition: transform 0.3s; }
    #splash-enter:hover .arrow { transform: translateX(4px); }
    #splash-credit {
      position: absolute; bottom: 32px; left: 0; right: 0;
      text-align: center;
      font-size: 11px;
      color: rgba(255,255,255,0.2);
      letter-spacing: 0.08em;
      font-weight: 400;
      animation: splash-fade-in 2s 0.5s cubic-bezier(0.16, 1, 0.3, 1) both;
    }
    /* Ambient orb background */
    .orb {
      position: absolute; border-radius: 50%; filter: blur(80px); opacity: 0.15;
      animation: orb-float 8s ease-in-out infinite;
    }
    .orb-1 { width: 400px; height: 400px; background: #1db954; top: -100px; right: -80px; animation-delay: 0s; }
    .orb-2 { width: 300px; height: 300px; background: #1ed760; bottom: -60px; left: -60px; animation-delay: -3s; }
    .orb-3 { width: 200px; height: 200px; background: #00a854; top: 40%; left: 20%; animation-delay: -5s; }
    @keyframes orb-float {
      0%, 100% { transform: translate(0, 0) scale(1); }
      33% { transform: translate(20px, -30px) scale(1.05); }
      66% { transform: translate(-15px, 15px) scale(0.95); }
    }
  </style>
</head>
<body>
  <svg xmlns="http://www.w3.org/2000/svg" style="display:none" aria-hidden="true">
    <symbol id="icon-music" viewBox="0 0 24 24"><path d="M18 3v12.15A3.5 3.5 0 1 1 16 12V7.05L9 8.45v8.7A3.5 3.5 0 1 1 7 14V6.4L18 3Z"/></symbol>
    <symbol id="icon-volume" viewBox="0 0 24 24"><path d="M4 9v6h4l5 4V5L8 9H4Zm11.5-.7 1.4-1.4A7 7 0 0 1 19 12a7 7 0 0 1-2.1 5.1l-1.4-1.4A5 5 0 0 0 17 12a5 5 0 0 0-1.5-3.7Z"/></symbol>
    <symbol id="icon-volume-off" viewBox="0 0 24 24"><path d="M4 9v6h4l5 4V5L8 9H4Zm12.5.1L18.4 11l1.9-1.9 1.4 1.4-1.9 1.9 1.9 1.9-1.4 1.4-1.9-1.9-1.9 1.9-1.4-1.4 1.9-1.9-1.9-1.9 1.4-1.4Z"/></symbol>
    <symbol id="icon-moon" viewBox="0 0 24 24"><path d="M20.3 15.6A8.4 8.4 0 0 1 8.4 3.7 8.9 8.9 0 1 0 20.3 15.6Z"/></symbol>
    <symbol id="icon-sun" viewBox="0 0 24 24"><path d="M12 4.2 10.7 2h2.6L12 4.2Zm0 15.6 1.3 2.2h-2.6l1.3-2.2ZM4.2 12 2 13.3v-2.6L4.2 12Zm15.6 0 2.2-1.3v2.6L19.8 12ZM6.5 5.1 4 4l1.1 2.5 1.4-1.4Zm12.4 12.4L20 20l-2.5-1.1 1.4-1.4Zm0-12.4 1.1-2.5L17.5 4l1.4 1.1ZM5.1 17.5 4 20l2.5-1.1-1.4-1.4ZM12 7a5 5 0 1 1 0 10 5 5 0 0 1 0-10Z"/></symbol>
    <symbol id="icon-sparkle" viewBox="0 0 24 24"><path d="m12 2 2.1 6.1L20 10l-5.9 1.9L12 18l-2.1-6.1L4 10l5.9-1.9L12 2Zm7 11 1 3 3 1-3 1-1 3-1-3-3-1 3-1 1-3ZM5 14l.8 2.2L8 17l-2.2.8L5 20l-.8-2.2L2 17l2.2-.8L5 14Z"/></symbol>
    <symbol id="icon-library" viewBox="0 0 24 24"><path d="M5 4h3v16H5V4Zm5 0h3v16h-3V4Zm5.2.5 2.9-.8 4.2 15.5-2.9.8-4.2-15.5Z"/></symbol>
    <symbol id="icon-heart" viewBox="0 0 24 24"><path d="M12 21s-7.5-4.5-9.6-9.1C.8 8.4 2.8 5 6.3 5c2 0 3.4 1 4.2 2.2C11.3 6 12.7 5 14.7 5c3.5 0 5.5 3.4 3.9 6.9C16.5 16.5 12 21 12 21Z"/></symbol>
    <symbol id="icon-clock" viewBox="0 0 24 24"><path d="M12 2a10 10 0 1 0 .1 0H12Zm1 5v5.1l4 2.4-1 1.7-5-3V7h2Z"/></symbol>
    <symbol id="icon-headphones" viewBox="0 0 24 24"><path d="M12 3a8 8 0 0 0-8 8v6a3 3 0 0 0 3 3h2v-8H6v-1a6 6 0 0 1 12 0v1h-3v8h2a3 3 0 0 0 3-3v-6a8 8 0 0 0-8-8Z"/></symbol>
    <symbol id="icon-fire" viewBox="0 0 24 24"><path d="M13 2s1 3.4-1.7 6.2C9.2 10.4 7 12.2 7 15a5 5 0 0 0 10 0c0-2.2-1.1-3.6-2.4-5 .1 1.5-.6 2.7-1.7 3.6.4-2.7-.5-5.8-3.1-8.3C9.4 8.5 5 10.6 5 15a7 7 0 0 0 14 0c0-5.4-4.2-7.4-6-13Z"/></symbol>
    <symbol id="icon-guitar" viewBox="0 0 24 24"><path d="M18.7 2.2 21.8 5l-2 2-1.1-1.1-3.8 3.8a4.8 4.8 0 0 1-.7 5.9 5 5 0 0 1-4.2 1.5 4.4 4.4 0 0 1-1.2 2.1 4 4 0 1 1-5.7-5.7A4.4 4.4 0 0 1 5.2 12a5 5 0 0 1 1.5-4.2 4.8 4.8 0 0 1 5.9-.7L16.4 3l-1.1-1.1 2-2 1.4 2.3ZM8.1 15.9a2 2 0 1 0-2.8 2.8 2 2 0 0 0 2.8-2.8Z"/></symbol>
    <symbol id="icon-microphone" viewBox="0 0 24 24"><path d="M12 3a3 3 0 0 0-3 3v6a3 3 0 1 0 6 0V6a3 3 0 0 0-3-3Zm6 8v1a6 6 0 0 1-5 5.9V21h3v2H8v-2h3v-3.1A6 6 0 0 1 6 12v-1h2v1a4 4 0 0 0 8 0v-1h2Z"/></symbol>
    <symbol id="icon-film" viewBox="0 0 24 24"><path d="M4 4h16v16H4V4Zm3 2H6v2h1V6Zm0 5H6v2h1v-2Zm0 5H6v2h1v-2Zm11-10h-1v2h1V6Zm0 5h-1v2h1v-2Zm0 5h-1v2h1v-2ZM9 7v10h6V7H9Z"/></symbol>
    <symbol id="icon-cup" viewBox="0 0 24 24"><path d="M5 5h12v5h1a3 3 0 0 1 0 6h-1.4A6 6 0 0 1 5 14V5Zm12 7v2h1a1 1 0 0 0 0-2h-1ZM4 20h14v2H4v-2Z"/></symbol>
    <symbol id="icon-prev" viewBox="0 0 24 24"><path d="M7 6h2v12H7V6Zm3.5 6 8.5-6v12l-8.5-6Z"/></symbol>
    <symbol id="icon-play" viewBox="0 0 24 24"><path d="M8 5v14l11-7L8 5Z"/></symbol>
    <symbol id="icon-pause" viewBox="0 0 24 24"><path d="M7 5h4v14H7V5Zm6 0h4v14h-4V5Z"/></symbol>
    <symbol id="icon-next" viewBox="0 0 24 24"><path d="M15 6h2v12h-2V6ZM5 18V6l8.5 6L5 18Z"/></symbol>
    <symbol id="icon-record" viewBox="0 0 24 24"><path d="M12 5a7 7 0 1 1 0 14 7 7 0 0 1 0-14Z"/></symbol>
    <symbol id="icon-stop" viewBox="0 0 24 24"><path d="M7 7h10v10H7V7Z"/></symbol>
    <symbol id="icon-check" viewBox="0 0 24 24"><path d="m9.2 16.6-4.1-4.1 1.4-1.4 2.7 2.7 8.3-8.3 1.4 1.4-9.7 9.7Z"/></symbol>
    <symbol id="icon-x" viewBox="0 0 24 24"><path d="m6.4 5 5.6 5.6L17.6 5 19 6.4 13.4 12l5.6 5.6-1.4 1.4-5.6-5.6L6.4 19 5 17.6l5.6-5.6L5 6.4 6.4 5Z"/></symbol>
    <symbol id="icon-refresh" viewBox="0 0 24 24"><path d="M17.7 6.3A8 8 0 0 0 4.1 10H2l3.4 3.4L8.8 10H6.2A6 6 0 0 1 16.3 7.7l1.4-1.4ZM18.6 10 15.2 13.4H17.8a6 6 0 0 1-10.1 2.3l-1.4 1.4A8 8 0 0 0 19.9 14H22L18.6 10Z"/></symbol>
  </svg>
  <!-- Splash Screen -->
  <div id="splash">
    <div class="orb orb-1"></div>
    <div class="orb orb-2"></div>
    <div class="orb orb-3"></div>
    <div id="splash-inner">
      <div id="splash-wordmark">Music Speaks</div>
      <div id="splash-tagline">When words fall short, let music speak.</div>
      <button id="splash-enter" onclick="enterApp()">
        Get Started <span class="arrow">→</span>
      </button>
    </div>
    <div id="splash-credit">Created by Yuan Tao</div>
  </div>
  <script>
    function enterApp() {
      const splash = document.getElementById('splash');
      splash.style.transition = 'opacity 0.6s ease, transform 0.6s ease';
      splash.style.opacity = '0';
      splash.style.transform = 'scale(1.04)';
      splash.style.pointerEvents = 'none';
      setTimeout(() => { splash.style.display = 'none'; }, 600);
    }
  </script>
  <div class="app">
    <header class="app-header">
      <a href="/" class="logo">
        <div class="logo-icon" aria-hidden="true"><svg class="ui-icon"><use href="#icon-music"></use></svg></div>
        <span>Music Speaks</span>
      </a>
      <div class="header-actions">
        <button id="soundBtn" class="header-btn sound-toggle on" title="Toggle sound" aria-label="Mute sounds" onclick="toggleSound()"><svg class="ui-icon"><use href="#icon-volume"></use></svg></button>
        <button id="themeBtn" class="header-btn" title="Switch to theme" aria-label="Switch to light theme"><svg class="ui-icon"><use href="#icon-moon"></use></svg></button>
        <div id="langBtnDropdown" class="lang-btn-dropdown">
          <button id="langBtn" class="header-btn lang-toggle" aria-haspopup="listbox" aria-expanded="false">EN ▾</button>
          <div id="langMenu" class="lang-menu" role="listbox" aria-hidden="true"></div>
        </div>
        <div id="langMenuBackdrop" class="lang-menu-backdrop" aria-hidden="true"></div>
      </div>
    </header>
    <div class="app-body">
      <aside class="sidebar">
        <nav class="sidebar-nav">
          <a class="nav-item active" data-view="create">
            <span class="nav-icon" aria-hidden="true"><svg class="ui-icon"><use href="#icon-sparkle"></use></svg></span>
            <span data-i18n="navCreate">Create</span>
          </a>
          <a class="nav-item" data-view="library">
            <span class="nav-icon" aria-hidden="true"><svg class="ui-icon"><use href="#icon-library"></use></svg></span>
            <span data-i18n="navLibrary">Library</span>
          </a>
          <a class="nav-item" data-view="favorites">
            <span class="nav-icon" aria-hidden="true"><svg class="ui-icon"><use href="#icon-heart"></use></svg></span>
            <span data-i18n="navFavorites">Favorites</span>
          </a>
          <a class="nav-item" data-view="history">
            <span class="nav-icon" aria-hidden="true"><svg class="ui-icon"><use href="#icon-clock"></use></svg></span>
            <span data-i18n="navHistory">History</span>
          </a>
        </nav>
        <div class="sidebar-section">
          <div class="sidebar-section-title" data-i18n="navPlaylists">Playlists</div>
          <div class="playlist-item"><span class="playlist-icon" aria-hidden="true"><svg class="ui-icon"><use href="#icon-headphones"></use></svg></span><span data-i18n="playlistAll">All Songs</span></div>
          <div class="playlist-item"><span class="playlist-icon" aria-hidden="true"><svg class="ui-icon"><use href="#icon-fire"></use></svg></span><span data-i18n="playlistRecent">Recently Played</span></div>
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
                <button class="template-btn" type="button" data-template="upbeat_pop"><svg class="ui-icon" aria-hidden="true"><use href="#icon-music"></use></svg><span>Upbeat Pop</span></button>
                <button class="template-btn" type="button" data-template="chill_ambient"><svg class="ui-icon" aria-hidden="true"><use href="#icon-moon"></use></svg><span>Chill Ambient</span></button>
                <button class="template-btn" type="button" data-template="rock_anthem"><svg class="ui-icon" aria-hidden="true"><use href="#icon-guitar"></use></svg><span>Rock Anthem</span></button>
                <button class="template-btn" type="button" data-template="acoustic_story"><svg class="ui-icon" aria-hidden="true"><use href="#icon-guitar"></use></svg><span>Acoustic Story</span></button>
                <button class="template-btn" type="button" data-template="electronic_dream"><svg class="ui-icon" aria-hidden="true"><use href="#icon-sparkle"></use></svg><span>Electronic Dream</span></button>
                <button class="template-btn" type="button" data-template="hiphop_beats"><svg class="ui-icon" aria-hidden="true"><use href="#icon-microphone"></use></svg><span>Hip-Hop Beats</span></button>
                <button class="template-btn" type="button" data-template="cinematic_epic"><svg class="ui-icon" aria-hidden="true"><use href="#icon-film"></use></svg><span>Cinematic Epic</span></button>
                <button class="template-btn" type="button" data-template="lofi_chill"><svg class="ui-icon" aria-hidden="true"><use href="#icon-cup"></use></svg><span>Lo-Fi Chill</span></button>
              </div>
            </div>
            <!-- Voice Style (determines lyrics language) -->
            <div class="form-section">
              <div class="voice-picker-label">
                <label class="form-label" data-i18n="voicePickerLabel">Voice Style</label>
                <span id="voicePickerSelected" class="voice-picker-selected" data-i18n="voicePickerDefault">Click to select — this sets the lyrics language</span>
              </div>
              <div class="voice-search-row">
                <input type="text" id="voiceSearchBox" class="voice-search-box" placeholder="Search voices..." autocomplete="off">
              </div>
              <div class="voice-filter-chips" id="voiceFilterChips">
                <button type="button" class="voice-filter-chip" data-filter="all">All</button>
                <button type="button" class="voice-filter-chip" data-filter="Warm">Warm</button>
                <button type="button" class="voice-filter-chip" data-filter="Youthful">Youthful</button>
                <button type="button" class="voice-filter-chip" data-filter="Professional">Professional</button>
                <button type="button" class="voice-filter-chip" data-filter="Character">Character</button>
                <button type="button" class="voice-filter-chip" data-filter="calm">Calm</button>
                <button type="button" class="voice-filter-chip" data-filter="energetic">Energetic</button>
              </div>
              <div id="voicePickerScroll" class="voice-picker-scroll">
                <div style="padding:20px;text-align:center;color:var(--text-muted);font-size:13px;" id="voicePickerLoading">
                  <span data-i18n="voicePickerLoading">Loading voices...</span>
                </div>
              </div>
              <input type="hidden" id="vocals" data-i18n-placeholder="vocalsPlaceholder" placeholder="warm male vocal, bright female vocal, duet">
            </div>
            <!-- Lyrics Idea -->
            <div class="form-section">
              <label class="form-label" data-i18n="lyricsIdeaLabel">Lyric Prompt / Instructions (optional)</label>
              <textarea id="lyricsIdea" maxlength="2500" class="form-input" data-i18n-placeholder="lyricsIdeaPlaceholder" placeholder="Describe the story, feelings, images, or fragments you want. You can also write instructions like: 'Translate the above into Korean' or 'Rewrite as a Spanish love song.'"></textarea>
              <div class="form-hint" data-i18n="lyricsIdeaHint">Describe what you want, or give AI a task like "translate into Korean". The selected voice language is used automatically.</div>
              <label class="form-label" style="margin-top:12px;font-size:12px;color:var(--text-muted);" data-i18n="lyricsExtraLabel">Additional Requirements (optional)</label>
              <textarea id="lyricsExtra" maxlength="500" class="form-input" data-i18n-placeholder="lyricsExtraPlaceholder" placeholder="Length: 3-5 min / Emotion: melancholic, hopeful / Style: poetic, conversational / Mood: dark, upbeat / Tempo: fast, slow / Structure: verse-chorus-verse-chorus-bridge-chorus" style="min-height:60px;font-size:12px;"></textarea>
              <div class="form-hint" data-i18n="lyricsExtraHint">Describe extra requirements for the lyrics: desired length, emotional tone, style, mood, tempo, structure, etc. These are附加 requirements separate from the main lyrics content.</div>
              <div style="margin-top:12px;display:flex;align-items:center;gap:12px;">
                <button id="generateLyricsBtn" class="btn-secondary" type="button" data-i18n="generateLyrics">Generate Lyrics</button>
                <span id="lyricsAssistMessage" style="font-size:13px;color:var(--text-muted);"></span>
              </div>
            </div>
            <!-- Finished Lyrics -->
            <div class="form-section">
              <label class="form-label" data-i18n="lyricsLabel">Finished Lyrics (optional)</label>
              <textarea id="lyrics" maxlength="6000" class="form-input" data-i18n-placeholder="lyricsPlaceholder" placeholder="[Verse]&#10;Your lyrics here...&#10;[Hook]&#10;Your chorus..."></textarea>
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
                    <svg class="ui-icon" aria-hidden="true"><use href="#icon-microphone"></use></svg>
                    <span data-i18n="voiceRecordBtn">Record My Voice</span>
                  </button>
                  <span id="voiceStatus" class="voice-status"></span>
                </div>
                <div id="voicePreviewRow" style="display:none;margin-top:12px;align-items:center;gap:12px;">
                  <button id="voicePreviewBtn" class="btn-secondary" type="button" data-i18n="voicePreviewBtn">Preview Voice</button>
                  <audio id="voicePreviewAudio" controls style="height:36px;"></audio>
                </div>
                <label class="checkbox-item" style="margin-top:12px;">
                  <input id="voiceSingingMode" type="checkbox" checked>
                  <span><span data-i18n="voiceSingingMode">Singing synthesis mode</span><small data-i18n="voiceSingingModeHint">Tries voice_clone_singing first, then falls back to voice cover.</small></span>
                </label>
                <div class="form-hint" data-i18n="voiceCloneHint">Record 5 passages. Takes ~30s. Voice expires in 7 days.</div>
              </div>
            </div>
            <!-- Advanced Parameters -->
            <div class="form-section">
              <div class="advanced-toggle" id="advancedToggle" role="button" tabindex="0" aria-expanded="false" onclick="toggleAdvancedPanel(event)" onkeydown="if(event.key==='Enter'||event.key===' '){toggleAdvancedPanel(event);}">
                <span data-i18n="advanced">More Parameters</span>
                <span class="advanced-toggle-icon">▼</span>
              </div>
              <div class="advanced-panel" id="advancedPanel">
                <div class="param-grid">
                  <div class="param-field"><label data-i18n="genre">Genre</label><input id="genre" data-i18n-placeholder="genrePlaceholder" placeholder="pop, reggae, jazz"></div>
                  <div class="param-field"><label data-i18n="mood">Mood</label><input id="mood" data-i18n-placeholder="moodPlaceholder" placeholder="warm, bright, intense"></div>
                  <div class="param-field"><label data-i18n="instruments">Instruments</label><input id="instruments" data-i18n-placeholder="instrumentsPlaceholder" placeholder="piano, guitar, drums"></div>
                  <div class="param-field"><label data-i18n="tempo">Tempo</label><input id="tempo" data-i18n-placeholder="tempoPlaceholder" placeholder="fast, slow, moderate"></div>
                  <div class="param-field"><label data-i18n="bpm">BPM</label><input id="bpm" type="number" min="40" max="240" data-i18n-placeholder="bpmPlaceholder" placeholder="85"></div>
                  <div class="param-field"><label data-i18n="key">Key</label><input id="key" data-i18n-placeholder="keyPlaceholder" placeholder="C major, A minor"></div>
                  <div class="param-field" style="grid-column:1/-1;"><label data-i18n="structure">Song Structure</label><input id="structure" data-i18n-placeholder="structurePlaceholder" placeholder="verse-chorus-verse-bridge-chorus"></div>
                  <div class="param-field" style="grid-column:1/-1;"><label data-i18n="references">References</label><input id="references" data-i18n-placeholder="referencesPlaceholder" placeholder="similar to..."></div>
                  <div class="param-field" style="grid-column:1/-1;"><label data-i18n="avoid">Avoid</label><input id="avoid" data-i18n-placeholder="avoidPlaceholder" placeholder="explicit content, auto-tune"></div>
                </div>
              </div>
            </div>
            <!-- Email (last — optional, for future user registration and MP3 email delivery) -->
            <div class="form-section">
              <label class="form-label" data-i18n="emailLabel">Email Address (optional)</label>
              <input id="email" type="email" class="form-input" data-i18n-placeholder="emailPlaceholder" placeholder="your@email.com">
              <div class="form-hint" data-i18n="emailHint">Optional. Download button is the main way to get your MP3. Fill this to receive your song by email.</div>
            </div>
            <!-- Actions -->
            <div class="form-actions">
              <button id="submitBtn" class="btn-primary" type="submit" data-i18n="submit">Generate Music</button>
              <button id="clearDraftBtn" class="btn-secondary" type="button" data-i18n="clearDraft">Clear Draft</button>
            </div>
            <div id="formError" class="error-text"></div>
            <div id="draftStatus" style="margin-top:12px;font-size:12px;color:var(--text-muted);"></div>
          </form>
          <!-- Jobs Panel -->
          <div class="jobs-panel">
            <div class="jobs-header">
              <h3 class="jobs-title" data-i18n="jobsTitle">Generation Jobs</h3>
            </div>
            <div id="jobs" class="jobs-list"></div>
          </div>
        </div>
        <!-- Library View -->
        <div id="view-library" style="display:none;">
          <div class="page-header">
            <h1 class="page-title" data-i18n="navLibrary">Library</h1>
            <p class="page-desc" data-i18n="libraryDesc">All your generated songs in one place.</p>
          </div>
          <div id="library-list" class="jobs-list"></div>
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
        <div class="player-art" aria-hidden="true"><svg class="ui-icon"><use href="#icon-music"></use></svg></div>
        <div class="player-info">
          <div class="player-title" id="playerTitle">Song Title</div>
          <div class="player-artist" id="playerArtist">Music Speaks</div>
        </div>
      </div>
      <div class="player-controls">
        <div class="player-buttons">
          <button class="player-btn" id="playerPrev" type="button" aria-label="Previous track"><svg class="ui-icon" aria-hidden="true"><use href="#icon-prev"></use></svg></button>
          <button class="player-btn play" id="playerPlay" type="button" aria-label="Play"><svg class="ui-icon" aria-hidden="true"><use href="#icon-play"></use></svg></button>
          <button class="player-btn" id="playerNext" type="button" aria-label="Next track"><svg class="ui-icon" aria-hidden="true"><use href="#icon-next"></use></svg></button>
        </div>
        <div class="player-progress">
          <span class="player-time" id="playerCurrentTime">0:00</span>
          <div class="player-bar" id="playerBar"><div class="player-bar-fill" id="playerBarFill"></div></div>
          <span class="player-time" id="playerDuration">0:00</span>
        </div>
      </div>
      <div class="player-lyrics" id="playerLyrics">
        <div class="lyrics-text" id="lyricsText">Lyrics ready</div>
      </div>
      <div class="player-extra">
        <button class="lyrics-toggle" id="playerLyricsToggle" type="button" aria-expanded="false" aria-controls="lyricsPanel">Lyrics</button>
        <button class="lyrics-fullscreen-btn" id="lyricsFullscreenBtn" type="button" title="Fullscreen lyrics" aria-label="Open fullscreen lyrics">⛶</button>
        <div class="player-volume">
          <span class="volume-icon" id="volumeIcon" aria-hidden="true"><svg class="ui-icon"><use href="#icon-volume"></use></svg></span>
          <div class="volume-slider" id="volumeSlider"><div class="volume-fill" id="volumeFill"></div></div>
        </div>
      </div>
    </div>
    <div class="lyrics-panel" id="lyricsPanel" aria-live="polite">
      <div class="lyrics-panel-header">
        <span class="lyrics-panel-title" id="lyricsPanelTitle">Lyrics</span>
        <button class="lyrics-panel-close" id="lyricsPanelClose" type="button">Close</button>
      </div>
      <div class="lyrics-lines" id="lyricsLines">
        <div class="lyrics-empty">No lyrics available for this track.</div>
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
          <button class="rec-btn rec-btn-record" id="recRecordBtn"><svg class="ui-icon" aria-hidden="true"><use href="#icon-record"></use></svg> Record</button>
          <button class="rec-btn rec-btn-stop" id="recStopBtn" style="display:none;"><svg class="ui-icon" aria-hidden="true"><use href="#icon-stop"></use></svg> Stop</button>
          <button class="rec-btn rec-btn-next" id="recNextBtn" style="display:none;">Next →</button>
        </div>
      </div>
    </div>
  </div>
  <script>
    window.toggleAdvancedPanel = function(event) {
      if (event) {
        event.preventDefault();
        event.stopPropagation();
      }
      const panel = document.getElementById("advancedPanel");
      const toggle = document.getElementById("advancedToggle");
      if (!panel || !toggle) return;
      const isOpen = panel.classList.toggle("open");
      toggle.setAttribute("aria-expanded", isOpen ? "true" : "false");
      const icon = toggle.querySelector(".advanced-toggle-icon");
      if (icon) icon.textContent = isOpen ? "▲" : "▼";
    };

    // ── Sound Effects System (Web Audio API) ──────────────────────
    const SoundSystem = {
      ctx: null,
      enabled: true,
      init() {
        if (this.ctx) return this.ctx;
        try {
          this.ctx = new (window.AudioContext || window.webkitAudioContext)();
        } catch(e) { console.warn("Web Audio API not supported"); }
        return this.ctx;
      },
      play(type) {
        if (!this.enabled) return;
        const ctx = this.ctx || this.init();
        if (!ctx) return;
        if (ctx.state === "suspended") {
          const resume = ctx.resume();
          if (resume && typeof resume.catch === "function") resume.catch(() => {});
        }
        const now = ctx.currentTime;
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.connect(gain);
        gain.connect(ctx.destination);
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
    // AudioContext is created lazily from the first user interaction.

    const I18N = {
      en: {
        subtitle: "When words fall short, let music speak. Give your inner world a sound of its own.",
        createTitle: "Create Music", createDesc: "Write a feeling, story, lyric, or style. Music Speaks turns it into a downloadable song.",
        emailLabel: "Email Address (optional)", emailHint: "Optional. Download is the main way to get your MP3. Fill this to receive your song by email.",
        emailPlaceholder: "your@email.com",
        titleLabel: "Song Title (optional)", titleHint: "If empty, Music Speaks will create a title from the lyrics before saving the MP3.",
        titlePlaceholder: "Leave empty and AI will name the song",
        promptLabel: "Music Style Prompt", promptHint: "Include style, mood, instruments, tempo, and any references.",
        promptPlaceholder: "Cinematic electronic pop, confident and bright, polished production, strong hook",
        lyricsIdeaLabel: "Lyric Prompt / Instructions (optional)", lyricsIdeaHint: "Describe what you want, or give AI a task like 'translate into Korean'. The selected voice language is used automatically.",
        lyricsExtraLabel: "Additional Requirements (optional)", lyricsExtraHint: "Describe extra requirements: desired length, emotional tone, style, mood, tempo, structure. These are附加 requirements separate from the main lyrics content.",
        lyricsExtraPlaceholder: "Length: 3-5 min / Emotion: melancholic, hopeful / Style: poetic / Mood: dark, upbeat / Tempo: fast / Structure: verse-chorus-verse-chorus-bridge-chorus",
        lyricsIdeaPlaceholder: "Describe the story, feelings, images, or fragments you want. You can also write instructions like: 'Translate the above into Korean' or 'Rewrite as a Spanish love song.'",
        generateLyrics: "Generate Lyrics", generatingLyrics: "Generating lyrics...", lyricsGenerated: "Lyrics added below. You can edit them before generating music.",
        lyricsAssistNeedBrief: "Add a lyrics brief or music style prompt first.", lyricsAssistFailed: "Lyrics generation failed.",
        lyricsLabel: "Finished Lyrics (optional)", lyricsHint: "Paste exact lyrics here if you already have them. Exact lyrics take priority over the lyrics brief.",
        lyricsPlaceholder: "[Verse]\nYour lyrics here...\n[Hook]\nYour chorus...",
        instrumental: "Instrumental", instrumentalHint: "No vocals. Lyrics will be ignored.",
        autoLyrics: "Auto-generate Lyrics", autoLyricsHint: "AI writes lyrics from your prompt.",
        voiceCloneLabel: "Voice Clone (optional)", voiceRecordBtn: "Record My Voice", voiceCloneHint: "Record 5 short passages covering different tones and styles. Takes about 30 seconds. Cloned voice expires in 7 days.",
        voicePreviewBtn: "Preview Voice", voiceUploading: "Cloning your voice...", voiceReady: "Voice cloned! Use Preview to listen.",
        voiceError: "Voice clone failed.", voicePreviewGenerating: "Generating preview...", voicePreviewReady: "Preview ready.", voicePreviewError: "Preview failed.",
        voiceSingingMode: "Singing synthesis mode", voiceSingingModeHint: "Tries MiniMax voice_clone_singing first. If unavailable, falls back to voice cover.",
        voicePickerLabel: "Voice Style", voicePickerDefault: "Click to select — this sets the lyrics language", voicePickerLoading: "Loading voices...", voiceShowMore: "Show {count} more",
        voicePreviewSample: "Listen to this voice sample",
        voiceCustomBtn: "My Voice", voiceCustomDesc: "Record and use your own voice",
        recModalTitle: "Record Your Voice",
        templates: "Prompt Templates",
        advanced: "More Parameters", genre: "Genre", mood: "Mood", instruments: "Instruments", tempo: "Tempo Feel", bpm: "BPM", key: "Musical Key",
        vocals: "Vocal Style", structure: "Song Structure", references: "References", avoid: "Avoid", useCase: "Use Case", extra: "Extra Details",
        genrePlaceholder: "pop, reggae, jazz", moodPlaceholder: "warm, bright, intense", instrumentsPlaceholder: "piano, guitar, drums",
        tempoPlaceholder: "fast, slow, moderate", bpmPlaceholder: "85", keyPlaceholder: "C major, A minor",
        vocalsPlaceholder: "warm male vocal, bright female vocal, duet", structurePlaceholder: "verse-chorus-verse-bridge-chorus",
        referencesPlaceholder: "similar to...", avoidPlaceholder: "explicit content, auto-tune", useCasePlaceholder: "video background, theme song",
        extraPlaceholder: "Any additional notes",
        submit: "Generate Music", jobsTitle: "Jobs", jobsDesc: "Real-time status. Download appears when the MP3 is ready.",
        clearDraft: "Clear Draft", clearDraftConfirm: "Clear the current draft? This will not delete generated music.",
        draftSaved: "Draft saved", draftRestored: "Draft restored", draftCleared: "Draft cleared", draftRestoreFailed: "Could not restore server draft.",
        empty: "No jobs yet. Fill in the form to start creating.", queued: "Queued", running: "Generating", completed: "Done", error: "Error", unknown: "Unknown",
        download: "Download MP3", delete: "Delete", sent: "Sent to", instrumentalMode: "Instrumental", vocalMode: "Vocal", deleteConfirm: "Delete this job?", deleteFailed: "Delete failed",
        navCreate: "Create", navLibrary: "Library", navFavorites: "Favorites", navHistory: "History", navPlaylists: "Playlists", playlistAll: "All Songs", playlistRecent: "Recently Played",
        libraryDesc: "All your generated songs in one place.", favoritesDesc: "Your liked and saved songs.", historyDesc: "Recently generated songs.",
        toastMusicStarted: "Music generation started!", toastMusicReady: "Music ready: ", toastLyricsSuccess: "Lyrics generated successfully!", toastLyricsError: "Lyrics generation failed.", toastVoiceCloneSuccess: "Voice cloned successfully!", toastVoiceCloneError: "Voice clone failed.",
        langMenuLabel: "Interface Language", langMismatchWarn: "⚠️ Lyrics language does not match the selected voice language. The lyrics may not sound right with this voice.", langMismatchTitle: "Language Mismatch"
      },
      zh: {
        subtitle: "当语言无法抵达时，让音乐替你表达。给你的内心世界一种属于自己的声音。",
        createTitle: "创建音乐", createDesc: "写下感受、故事、歌词或风格，Music Speaks 会把它变成一首可以下载的歌。",
        emailLabel: "邮箱地址（可选）", emailHint: "可不填写。下载按钮是获取 MP3 的主要方式。填写后可以收到邮件发送的 MP3。",
        emailPlaceholder: "你的邮箱（可选）",
        titleLabel: "歌名（可选）", titleHint: "不填写时，Music Speaks 会根据歌词分析生成歌名，并用作 MP3 文件名。",
        titlePlaceholder: "留空时，AI 会自动起歌名",
        promptLabel: "音乐风格描述", promptHint: "写清风格、情绪、乐器、速度和参考对象。",
        promptPlaceholder: "例如：明亮自信的电子流行，制作精致，副歌有记忆点",
        lyricsIdeaLabel: "歌词指令（可选）", lyricsIdeaHint: "描述你想要的内容，或给 AI 指令如'翻译成韩语'。所选音色的语种会自动应用。",
        lyricsExtraLabel: "附加要求（可选）", lyricsExtraHint: "描述歌词的附加要求：长度、情感基调、风格、情绪、节奏、结构等。这些是独立于歌词内容的附加说明。",
        lyricsExtraPlaceholder: "长度：3-5分钟 / 情感：忧伤的、希望的 / 风格：诗意、口语化 / 情绪：暗黑、明快 / 节奏：快歌、慢歌 / 结构：主歌-副歌-主歌-副歌-桥段-副歌",
        lyricsIdeaPlaceholder: "写下你想要的故事、情绪、画面或零散片段。你也可以写指令，如：'把上面的内容翻译成韩语'或'改写成西班牙语情歌'。",
        generateLyrics: "生成歌词", generatingLyrics: "正在生成歌词...", lyricsGenerated: "歌词已填入下方，你可以编辑后再生成音乐。",
        lyricsAssistNeedBrief: "请先填写歌词需求描述或音乐风格。", lyricsAssistFailed: "歌词生成失败。",
        lyricsLabel: "完整歌词（可选）", lyricsHint: "如果你已经有确定歌词，粘贴在这里。完整歌词会优先于歌词需求描述。",
        lyricsPlaceholder: "[主歌]\n在这里写歌词...\n[副歌]\n在这里写副歌...",
        instrumental: "纯音乐", instrumentalHint: "无人声，歌词会被忽略。",
        autoLyrics: "自动生成歌词", autoLyricsHint: "AI 根据描述写歌词。",
        voiceCloneLabel: "声纹复刻（可选）", voiceRecordBtn: "录制我的声音", voiceCloneHint: "录制5段不同音调和风格的短句，约30秒。复刻声音有效期7天。",
        voicePreviewBtn: "预览声音", voiceUploading: "正在复刻你的声音...", voiceReady: "声音复刻完成！点击预览试听。",
        voiceError: "声音复刻失败。", voicePreviewGenerating: "正在生成预览...", voicePreviewReady: "预览已生成。", voicePreviewError: "预览生成失败。",
        voiceSingingMode: "声纹唱歌合成模式", voiceSingingModeHint: "优先尝试 MiniMax voice_clone_singing；不可用时自动降级为声音翻唱。",
        voicePickerLabel: "人声音色", voicePickerDefault: "点击选择，或在下方录制自己的声音", voicePickerLoading: "加载音色中...", voiceShowMore: "再显示 {count} 个",
        voicePreviewSample: "试听此音色",
        voiceCustomBtn: "我的声音", voiceCustomDesc: "录制并使用自己的声音",
        recModalTitle: "录制您的声音",
        templates: "风格模板",
        advanced: "更多参数", genre: "流派", mood: "情绪", instruments: "乐器", tempo: "节奏感", bpm: "BPM", key: "调性",
        vocals: "人声风格", structure: "歌曲结构", references: "参考对象", avoid: "避免元素", useCase: "使用场景", extra: "其他细节",
        genrePlaceholder: "流行、雷鬼、爵士", moodPlaceholder: "温暖、明亮、强烈", instrumentsPlaceholder: "钢琴、吉他、鼓",
        tempoPlaceholder: "快、中速、慢", bpmPlaceholder: "85", keyPlaceholder: "C 大调、A 小调",
        vocalsPlaceholder: "温暖男声、明亮女声、男女对唱", structurePlaceholder: "主歌-副歌-主歌-桥段-副歌",
        referencesPlaceholder: "参考某首歌、某位歌手或某种感觉", avoidPlaceholder: "避免露骨内容、避免过重电音修音",
        useCasePlaceholder: "视频背景、主题曲、朋友生日歌", extraPlaceholder: "其他补充要求",
        submit: "生成音乐", jobsTitle: "生成任务", jobsDesc: "实时状态。MP3 准备好后会出现下载按钮。",
        clearDraft: "清空草稿", clearDraftConfirm: "清空当前草稿？这不会删除已经生成的音乐。",
        draftSaved: "草稿已保存", draftRestored: "已恢复上次草稿", draftCleared: "草稿已清空", draftRestoreFailed: "无法恢复服务器草稿。",
        empty: "暂无任务，填写表单开始创作。", queued: "排队中", running: "生成中", completed: "完成", error: "错误", unknown: "未知",
        download: "下载 MP3", delete: "删除", sent: "已发送到", instrumentalMode: "纯音乐", vocalMode: "有人声", deleteConfirm: "删除此任务？", deleteFailed: "删除失败",
        navCreate: "创建", navLibrary: "曲库", navFavorites: "收藏", navHistory: "历史", navPlaylists: "播放列表", playlistAll: "全部歌曲", playlistRecent: "最近播放",
        libraryDesc: "你生成的所有歌曲。", favoritesDesc: "你喜欢的歌曲。", historyDesc: "最近生成的歌曲。",
        toastMusicStarted: "音乐生成已开始！", toastMusicReady: "音乐完成：", toastLyricsSuccess: "歌词生成成功！", toastLyricsError: "歌词生成失败。", toastVoiceCloneSuccess: "声音复刻成功！", toastVoiceCloneError: "声音复刻失败。",
        langMenuLabel: "界面语言", langMismatchWarn: "⚠️ 歌词语言与所选音色不匹配，歌词可能与这个音色不协调。", langMismatchTitle: "语言不匹配"
      }
    ,
      yue: {
        subtitle: "當語言無法抵達時，讓音樂替你表達。給你嘅內心世界一種屬於自己嘅聲音。",
        createTitle: "創建音樂", createDesc: "寫低感受、故事、歌詞或風格，Music Speaks 會將佢變成一首可以下載嘅歌。",
        emailLabel: "電郵地址（可選）", emailHint: "可唔填。下載係獲取 MP3 的主要方式。填寫後可以收到電郵發送嘅 MP3。",
        emailPlaceholder: "你的電郵",
        titleLabel: "歌名（可選）", titleHint: "唔填寫時，Music Speaks 會根據歌詞分析生成歌名，並用作 MP3 檔案名。",
        titlePlaceholder: "留空時，AI 會自動起歌名",
        promptLabel: "音樂風格描述", promptHint: "寫清風格、情緒、樂器、速度和參考對象。",
        promptPlaceholder: "例如：明亮自信的電子流行，製作精緻，副歌有記憶點",
        lyricsIdeaLabel: "歌詞指令（可選）", lyricsIdeaHint: "描述你想要的内容，或俾 AI 指令如'翻譯成韓語'。所選音色的語種會自動應用。",
        lyricsExtraLabel: "附加要求（可選）", lyricsExtraHint: "描述歌詞的附加要求：長度、情感基調、風格、情緒、節奏、結構等。",
        lyricsExtraPlaceholder: "長度：3-5分鐘 / 情感：憂傷的 / 風格：詩意、口語化 / 情緒：暗黑",
        lyricsIdeaPlaceholder: "寫下你想要的故事、情緒、画面或零散片段。你也可以寫指令。",
        generateLyrics: "生成歌詞", generatingLyrics: "正在生成歌詞...", lyricsGenerated: "歌詞已填入下方，你可以編輯後再生成音樂。",
        lyricsAssistNeedBrief: "請先填寫歌詞需求描述或音樂風格。", lyricsAssistFailed: "歌詞生成失敗。",
        lyricsLabel: "完整歌詞（可選）", lyricsHint: "如果你已經有確定歌詞，貼在這裡。完整歌詞會優先於歌詞需求描述。",
        lyricsPlaceholder: "[主歌]\n在這裡寫歌詞...\n[副歌]\n在這裡寫副歌...",
        instrumental: "純音樂", instrumentalHint: "無人聲，歌詞會被忽略。",
        autoLyrics: "自動生成歌詞", autoLyricsHint: "AI 根據描述寫歌詞。",
        voiceCloneLabel: "聲紋復刻（可選）", voiceRecordBtn: "錄製我的聲音", voiceCloneHint: "錄製5段不同音調和風格的短句，約30秒。復刻聲音有效期7天。",
        voicePreviewBtn: "預覽聲音", voiceUploading: "正在復刻你的聲音...", voiceReady: "聲音復刻完成！點擊預覽試聽。",
        voiceError: "聲音復刻失敗。", voicePreviewGenerating: "正在生成預覽...", voicePreviewReady: "預覽已生成。", voicePreviewError: "預覽生成失敗。",
        voiceSingingMode: "聲紋唱歌合成模式", voiceSingingModeHint: "優先嘗試 MiniMax voice_clone_singing；不可用時自動降級為聲音翻唱。",
        voicePickerLabel: "人聲音色", voicePickerDefault: "點擊選擇，或在下方錄製自己的聲音", voicePickerLoading: "加載音色中...", voiceShowMore: "再顯示 {count} 個",
        voicePreviewSample: "試聽此音色",
        voiceCustomBtn: "我的聲音", voiceCustomDesc: "錄製並使用自己的聲音",
        recModalTitle: "錄製您的聲音",
        templates: "風格模板",
        advanced: "更多參數", genre: "流派", mood: "情緒", instruments: "樂器", tempo: "節奏感", bpm: "BPM", key: "調性",
        vocals: "人聲風格", structure: "歌曲結構", references: "參考對象", avoid: "避免元素", useCase: "使用場景", extra: "其他細節",
        genrePlaceholder: "流行、雷鬼、爵士", moodPlaceholder: "溫暖、明亮、強烈", instrumentsPlaceholder: "鋼琴、吉他、鼓",
        tempoPlaceholder: "快、中速、慢", bpmPlaceholder: "85", keyPlaceholder: "C 大調、A 小調",
        vocalsPlaceholder: "溫暖男聲、明亮女聲、男女對唱", structurePlaceholder: "主歌-副歌-主歌-橋段-副歌",
        referencesPlaceholder: "參考某首歌、某位歌手或某種感覺", avoidPlaceholder: "避免露骨內容",
        useCasePlaceholder: "視頻背景、主題曲", extraPlaceholder: "其他補充要求",
        submit: "生成音樂", jobsTitle: "生成任務", jobsDesc: "實時狀態。MP3 準備好後會出現下載按鈕。",
        clearDraft: "清空草稿", clearDraftConfirm: "清空當前草稿？這不會刪除已經生成的音樂。",
        draftSaved: "草稿已保存", draftRestored: "已恢復上次草稿", draftCleared: "草稿已清空", draftRestoreFailed: "無法恢復服務器草稿。",
        empty: "暫無任務，填寫表單開始創作。", queued: "排隊中", running: "生成中", completed: "完成", error: "錯誤", unknown: "未知",
        download: "下載 MP3", delete: "刪除", sent: "已發送到", instrumentalMode: "純音樂", vocalMode: "有人聲", deleteConfirm: "刪除此任務？", deleteFailed: "刪除失敗",
        navCreate: "創建", navLibrary: "曲庫", navFavorites: "收藏", navHistory: "歷史", navPlaylists: "播放列表", playlistAll: "全部歌曲", playlistRecent: "最近播放",
        libraryDesc: "你生成的所有歌曲。", favoritesDesc: "你喜歡的歌曲。", historyDesc: "最近生成的歌曲。",
        toastMusicStarted: "音樂生成已開始！", toastMusicReady: "音樂完成：", toastLyricsSuccess: "歌詞生成成功！", toastLyricsError: "歌詞生成失敗。", toastVoiceCloneSuccess: "聲音復刻成功！", toastVoiceCloneError: "聲音復刻失敗。",
        langMenuLabel: "界面語言", langMismatchWarn: "⚠️ 歌詞語言與所選音色不匹配，歌詞可能與這個音色不協調。", langMismatchTitle: "語言不匹配"
      },
      ko: {
        subtitle: "말이 부족할 때, 음악이替你 표현해 드립니다. 당신의 내면 세계에属于自己的 소리를 주세요.",
        createTitle: "음악 만들기", createDesc: "느낌, 이야기, 가사 또는 스타일을 적어주세요. Music Speaks가 노래로 만들어 드립니다.",
        emailLabel: "이메일 주소 (선택)", emailHint: "선택사항. 다운로드가 MP3를 받는 주된 방법입니다.",
        emailPlaceholder: "your@email.com",
        titleLabel: "노래 제목 (선택)", titleHint: "비워두면 Music Speaks가 가사로부터 제목을 만들어 MP3 파일명으로 사용합니다.",
        titlePlaceholder: "비워두면 AI가 제목을 지어줍니다",
        promptLabel: "음악 스타일 설명", promptHint: "스타일, 분위기, 악기, 템포, 참고 대상을 적어주세요.",
        promptPlaceholder: "밝고 자신감 있는 일렉트로닉 팝, 세련된 프로덕션, 강한 후렴",
        lyricsIdeaLabel: "가사 지시 (선택)", lyricsIdeaHint: "원하는 내용을描述하거나 '한국어로 번역'과 같은 지시를 내리세요. 선택한 음성 언어가 자동으로 적용됩니다.",
        lyricsExtraLabel: "추가 요구사항 (선택)", lyricsExtraHint: "길이, 감정基调, 스타일, 분위기, 템포, 구조 등 가사에 대한 추가 요구사항을描述하세요.",
        lyricsExtraPlaceholder: "길이: 3-5분 / 감정: 슬픈, 희망적인 / 스타일: 시적인 / 분위기: 어두운",
        lyricsIdeaPlaceholder: "원하는 이야기, 감정, 장면이나 단편을 적어주세요.",
        generateLyrics: "가사 생성", generatingLyrics: "가사 생성 중...", lyricsGenerated: "가사가 아래에 추가되었습니다. 음악 생성 전 편집할 수 있습니다.",
        lyricsAssistNeedBrief: "먼저 가사 요구사항이나 음악 스타일을 적어주세요.", lyricsAssistFailed: "가사 생성에 실패했습니다.",
        lyricsLabel: "완전한 가사 (선택)", lyricsHint: "이미 완성된 가사가 있으면 여기에 붙여넣으세요. 완성된 가사가 가사 요구사항보다 우선합니다.",
        lyricsPlaceholder: "[ verse]\n여기에 가사...\n[ hook]\n여기에 후렴...",
        instrumental: "반주", instrumentalHint: "보컬 없음. 가사는 무시됩니다.",
        autoLyrics: "자동 가사 생성", autoLyricsHint: "AI가 설명에서 가사를 작성합니다.",
        voiceCloneLabel: "음성 복제 (선택)", voiceRecordBtn: "내 목소리 녹음", voiceCloneHint: "다양한 톤과 스타일의 짧은 구절 5개를 녹음합니다. 약 30초. 복제된 음성은 7일 동안 유효합니다.",
        voicePreviewBtn: "음성 미리보기", voiceUploading: "음성 복제 중...", voiceReady: "음성 복제 완료! 미리보기를 클릭하여 들어보세요.",
        voiceError: "음성 복제에 실패했습니다.", voicePreviewGenerating: "미리보기 생성 중...", voicePreviewReady: "미리보기 준비 완료.", voicePreviewError: "미리보기 생성에 실패했습니다.",
        voiceSingingMode: "가창 합성 모드", voiceSingingModeHint: "우선 MiniMax voice_clone_singing을 시도합니다. 사용할 수 없으면 자동으로 음성 커버로降급합니다.",
        voicePickerLabel: "보컬 스타일", voicePickerDefault: "선택하려면 클릭 - 이것이 가사 언어를 설정합니다", voicePickerLoading: "음성 로딩 중...", voiceShowMore: "{count}개 더 보기",
        voicePreviewSample: "이 음성 샘플 듣기",
        voiceCustomBtn: "내 목소리", voiceCustomDesc: "내 목소리를 녹음하고 사용합니다",
        recModalTitle: "내 목소리 녹음",
        templates: "스타일 템플릿",
        advanced: "추가 매개변수", genre: "장르", mood: "분위기", instruments: "악기", tempo: "템포 느낌", bpm: "BPM", key: "키",
        vocals: "보컬 스타일", structure: "노래 구조", references: "참조", avoid: "피해야 할 것", useCase: "사용 사례", extra: "추가 세부사항",
        genrePlaceholder: "팝, 레게, 재즈", moodPlaceholder: "따뜻한, 밝은, 강렬한", instrumentsPlaceholder: "피아노, 기타, 드럼",
        tempoPlaceholder: "빠른, 느린, 중간", bpmPlaceholder: "85", keyPlaceholder: "C major, A minor",
        vocalsPlaceholder: "따뜻한 남성 보컬, 밝은 여성 보컬", structurePlaceholder: " verse-chorus-verse-bridge-chorus",
        referencesPlaceholder: "어떤 노래, 가수 또는 느낌 참고", avoidPlaceholder: "노골적인 내용, 과도한 오토튜너 피하기",
        useCasePlaceholder: "비디오 배경, 테마 송", extraPlaceholder: "추가 참고 요청사항",
        submit: "음악 생성", jobsTitle: "작업", jobsDesc: "실시간 상태. MP3가 준비되면 다운로드 버튼이 나타납니다.",
        clearDraft: "초안 지우기", clearDraftConfirm: "현재 초안을 지우시겠습니까? 생성된 음악은 삭제되지 않습니다.",
        draftSaved: "초안 저장됨", draftRestored: "이전 초안 복원됨", draftCleared: "초안 지워짐", draftRestoreFailed: "서버 초안을 복원할 수 없습니다.",
        empty: "아직 작업이 없습니다. 양식을 작성하여 시작하세요.", queued: "대기 중", running: "생성 중", completed: "완료", error: "오류", unknown: "알 수 없음",
        download: "MP3 다운로드", delete: "삭제", sent: "보낸 대상", instrumentalMode: "반주", vocalMode: "보컬", deleteConfirm: "이 작업을 삭제하시겠습니까?", deleteFailed: "삭제 실패",
        navCreate: "만들기", navLibrary: "라이브러리", navFavorites: "즐겨찾기", navHistory: "기록", navPlaylists: "재생목록", playlistAll: "모든 노래", playlistRecent: "최근 재생",
        libraryDesc: "생성한 모든 노래가 여기에 있습니다.", favoritesDesc: "좋아하는 노래.", historyDesc: "최근 생성된 노래.",
        toastMusicStarted: "음악 생성 시작!", toastMusicReady: "음악 준비 완료: ", toastLyricsSuccess: "가사 생성 성공!", toastLyricsError: "가사 생성 실패.", toastVoiceCloneSuccess: "음성 복제 성공!", toastVoiceCloneError: "음성 복제 실패.",
        langMenuLabel: "인터페이스 언어", langMismatchWarn: "⚠️ 가사 언어와 선택한 음성 언어가 일치하지 않습니다. 가사가 이 음성과 어울리지 않을 수 있습니다.", langMismatchTitle: "언어 불일치"
      },
      ja: {
        subtitle: "言葉が足りないとき、音楽が替你語ります。あなたの内面の世界に属于自己的音を。",
        createTitle: "音楽作成", createDesc: "気持ち、ストーリー、歌詞、スタイルを書いてください。Music Speaksが曲にしてくれます。",
        emailLabel: "メールアドレス（任意）", emailHint: "任意。ダウンロードがMP3を受け取る主な方法です。",
        emailPlaceholder: "your@email.com",
        titleLabel: "曲名（任意）", titleHint: "空欄の場合、Music Speaksが歌詞から曲名を付けてMP3のファイル名とします。",
        titlePlaceholder: "空欄でAIが曲名を作成",
        promptLabel: "音楽スタイル説明", promptHint: "スタイル、ムード、楽器、テンポ、参考情報を書いてください。",
        promptPlaceholder: "明るくて自信のあるエレクトロニックポップ、精致なプロダクション、記憶に残るフック",
        lyricsIdeaLabel: "歌詞指示（任意）", lyricsIdeaHint: " 원하는 내용을記述するか、'韓国語に翻訳'のような指示を出してください。選択した音声言語が自動的に適用されます。",
        lyricsExtraLabel: "追加要件（任意）", lyricsExtraHint: "歌詞の追加要件：長さ、感情基调、スタイル、ムード、テンポ、構造などを記述してください。",
        lyricsExtraPlaceholder: "長さ: 3-5分 / 感情: 悲しい、希望的な / スタイル: 詩的な / ムード: 暗い",
        lyricsIdeaPlaceholder: "ほしいストーリー、感情、画面や片段を書いてください。",
        generateLyrics: "歌詞生成", generatingLyrics: "歌詞生成中...", lyricsGenerated: "歌詞が 아래に追加されました。音楽生成前に編集できます。",
        lyricsAssistNeedBrief: "まず歌詞 요구事項や音楽スタイルを書いてください。", lyricsAssistFailed: "歌詞生成に失敗しました。",
        lyricsLabel: "完全な歌詞（任意）", lyricsHint: "すでに完全な歌詞があるなら、ここに貼り付けてください。完全な歌詞が歌詞指示より優先されます。",
        lyricsPlaceholder: "[ verse]\nここに歌詞...\n[ hook]\nここにフック...",
        instrumental: "伴奏", instrumentalHint: "ボーカルなし。歌詞は無視されます。",
        autoLyrics: "自動歌詞生成", autoLyricsHint: "AIが説明から歌詞を書きます。",
        voiceCloneLabel: "音声クローン（任意）", voiceRecordBtn: "声を録音", voiceCloneHint: "異なるトーンとスタイルの短い節5つを録音します 約30秒。クローンされた声は7日間有効です。",
        voicePreviewBtn: "声プレビュー", voiceUploading: "声をクローン中...", voiceReady: "声のクローン完了！プレビューをクリックして试听。",
        voiceError: "声のクローンに失敗しました。", voicePreviewGenerating: "プレビュー生成中...", voicePreviewReady: "プレビュー準備完了。", voicePreviewError: "プレビュー生成に失敗しました。",
        voiceSingingMode: "歌唱合成モード", voiceSingingModeHint: "最初にMiniMax voice_clone_singingを試みます 利用できない場合、自動的に声カバーに降級します。",
        voicePickerLabel: "ボーカルスタイル", voicePickerDefault: "クリックして選択 - これにより歌詞言語が設定されます", voicePickerLoading: "声読み込み中...", voiceShowMore: "さらに{count}個表示",
        voicePreviewSample: "この声サンプルを聴く",
        voiceCustomBtn: "私の声", voiceCustomDesc: "自分の声を録音して使用",
        recModalTitle: "声を録音",
        templates: "スタイルテンプレート",
        advanced: "追加パラメータ", genre: "ジャンル", mood: "ムード", instruments: "楽器", tempo: "テンポ感", bpm: "BPM", key: "キー",
        vocals: "ボーカルスタイル", structure: "曲構造", references: "参照", avoid: "避けるもの", useCase: "ユースケース", extra: "追加詳細",
        genrePlaceholder: "ポップ、レゲエジャズ", moodPlaceholder: "温かい、明るい、强烈", instrumentsPlaceholder: "ピアノ、ギター、ドラム",
        tempoPlaceholder: "速い、遅い、中間", bpmPlaceholder: "85", keyPlaceholder: "C major, A minor",
        vocalsPlaceholder: "温かい男性ボーカル、明るい女性ボーカル", structurePlaceholder: "verse-chorus-verse-bridge-chorus",
        referencesPlaceholder: "参考の曲、歌手、感覚", avoidPlaceholder: "露骨なコンテンツ、過度なオートチューンを避ける",
        useCasePlaceholder: "動画背景、テーマ曲", extraPlaceholder: "追加のメモ",
        submit: "音楽生成", jobsTitle: "ジョブ", jobsDesc: "リアルタイム状態。MP3の準備ができるとダウンロードボタンが表示されます。",
        clearDraft: "下書きを消去", clearDraftConfirm: "現在の下書きを消去しますか？生成された音楽は削除されません。",
        draftSaved: "下書き保存済み", draftRestored: "前の下書きが復元されました", draftCleared: "下書き消去済み", draftRestoreFailed: "サーバ了下書きを復元できませんでした。",
        empty: "まだジョブがありません。フォームに記入して開始してください。", queued: "待機中", running: "生成中", completed: "完了", error: "エラー", unknown: "不明",
        download: "MP3をダウンロード", delete: "削除", sent: "送信先", instrumentalMode: "伴奏", vocalMode: "ボーカル", deleteConfirm: "このジョブを削除しますか？", deleteFailed: "削除に失敗しました",
        navCreate: "作成", navLibrary: "ライブラリ", navFavorites: "お気に入り", navHistory: "履歴", navPlaylists: "プレイリスト", playlistAll: "すべての曲", playlistRecent: "最近再生",
        libraryDesc: "生成したすべての曲がここに表示されます。", favoritesDesc: "いいね！した曲。", historyDesc: "最近生成された曲。",
        toastMusicStarted: "音楽生成開始！", toastMusicReady: "音楽準備完了: ", toastLyricsSuccess: "歌詞生成成功！", toastLyricsError: "歌詞生成失敗。", toastVoiceCloneSuccess: "声のクローン成功！", toastVoiceCloneError: "声のクローン失敗。",
        langMenuLabel: "インターフェース言語", langMismatchWarn: "⚠️ 歌詞言語が選択した声の言語と一致しません。歌詞がこの声に合わない可能性があります。", langMismatchTitle: "言語の不一致"
      },
      es: {
        subtitle: "Cuando las palabras no bastan, que la música hable por ti.",
        createTitle: "Crear Música", createDesc: "Escribe un sentimiento, historia, letra o estilo. Music Speaks lo convierte en una canción descargable.",
        emailLabel: "Correo electrónico (opcional)", emailHint: "Opcional. La descarga es la forma principal de obtener tu MP3.",
        emailPlaceholder: "tu@email.com",
        titleLabel: "Título de la canción (opcional)", titleHint: "Si está vacío, Music Speaks creará un título a partir de la letra.",
        titlePlaceholder: "Déjalo vacío y la IA nombrará la canción",
        promptLabel: "Estilo musical", promptHint: "Incluye estilo, estado de ánimo, instrumentos, tempo y referencias.",
        promptPlaceholder: "Pop electrónico brillante y seguro, producción pulida, gancho memorable",
        lyricsIdeaLabel: "Instrucciones de letra (opcional)", lyricsIdeaHint: "Describe lo que quieres o dale a la IA una tarea. El idioma de la voz seleccionada se aplica automáticamente.",
        lyricsExtraLabel: "Requisitos adicionales (opcional)", lyricsExtraHint: "Describe requisitos adicionales: duración, tono emocional, estilo, estado de ánimo, tempo, estructura.",
        lyricsExtraPlaceholder: "Duración: 3-5 min / Emoción: triste, esperanzado / Estilo: poético / Estado de ánimo: oscuro",
        lyricsIdeaPlaceholder: "Escribe la historia, sentimientos, imágenes o fragmentos que quieras.",
        generateLyrics: "Generar letra", generatingLyrics: "Generando letra...", lyricsGenerated: "Letra añadida abajo. Puedes editarla antes de generar música.",
        lyricsAssistNeedBrief: "Añade primero una descripción o estilo musical.", lyricsAssistFailed: "La generación de letra ha fallado.",
        lyricsLabel: "Letra completa (opcional)", lyricsHint: "Pega aquí la letra exacta si ya la tienes. La letra exacta tiene prioridad.",
        lyricsPlaceholder: "[Verso]\nTu letra aquí...\n[Estribillo]\nTu estribillo...",
        instrumental: "Instrumental", instrumentalHint: "Sin vocals. La letra será ignorada.",
        autoLyrics: "Auto-generar letra", autoLyricsHint: "La IA escribe la letra según tu descripción.",
        voiceCloneLabel: "Clon de voz (opcional)", voiceRecordBtn: "Grabar mi voz", voiceCloneHint: "Graba 5 frases cortas de diferentes tonos. Toma unos 30 segundos. La voz clonada expira en 7 días.",
        voicePreviewBtn: "Vista previa de voz", voiceUploading: "Clonando tu voz...", voiceReady: "¡Voz clonada! Usa Vista previa para escuchar.",
        voiceError: "El clon de voz ha fallado.", voicePreviewGenerating: "Generando vista previa...", voicePreviewReady: "Vista previa lista.", voicePreviewError: "Vista previa fallida.",
        voiceSingingMode: "Modo de síntesis de canto", voiceSingingModeHint: "Intenta primero MiniMax voice_clone_singing. Si no está disponible, recurre a voice cover.",
        voicePickerLabel: "Estilo de voz", voicePickerDefault: "Haz clic para seleccionar — esto establece el idioma de la letra", voicePickerLoading: "Cargando voces...", voiceShowMore: "Mostrar {count} más",
        voicePreviewSample: "Escucha esta muestra de voz",
        voiceCustomBtn: "Mi voz", voiceCustomDesc: "Graba y usa tu propia voz",
        recModalTitle: "Graba tu voz",
        templates: "Plantillas de estilo",
        advanced: "Más parámetros", genre: "Género", mood: "Estado de ánimo", instruments: "Instrumentos", tempo: "Tempo", bpm: "BPM", key: "Tonalidad",
        vocals: "Estilo vocal", structure: "Estructura de canción", references: "Referencias", avoid: "Evitar", useCase: "Caso de uso", extra: "Detalles extra",
        genrePlaceholder: "pop, reggae, jazz", moodPlaceholder: "cálido, brillante, intenso", instrumentsPlaceholder: "piano, guitarra, batería",
        tempoPlaceholder: "rápido, lento, moderado", bpmPlaceholder: "85", keyPlaceholder: "Do mayor, La menor",
        vocalsPlaceholder: "vocal masculino cálido, vocal femenino brillante", structurePlaceholder: "verso-estribillo-verso-puente-estribillo",
        referencesPlaceholder: "similar a...", avoidPlaceholder: "contenido explícito, auto-tune excesivo",
        useCasePlaceholder: "fondo de video, canción temática", extraPlaceholder: "Notas adicionales",
        submit: "Generar Música", jobsTitle: "Trabajos", jobsDesc: "Estado en tiempo real. El botón de descarga aparece cuando el MP3 está listo.",
        clearDraft: "Borrar borrador", clearDraftConfirm: "¿Borrar el borrador actual? Esto no eliminará la música generada.",
        draftSaved: "Borrador guardado", draftRestored: "Borrador anterior restaurado", draftCleared: "Borrador borrado", draftRestoreFailed: "No se pudo restaurar el borrador del servidor.",
        empty: "Sin trabajos aún. Completa el formulario para empezar.", queued: "En cola", running: "Generando", completed: "Hecho", error: "Error", unknown: "Desconocido",
        download: "Descargar MP3", delete: "Eliminar", sent: "Enviado a", instrumentalMode: "Instrumental", vocalMode: "Vocal", deleteConfirm: "¿Eliminar este trabajo?", deleteFailed: "Error al eliminar",
        navCreate: "Crear", navLibrary: "Biblioteca", navFavorites: "Favoritos", navHistory: "Historial", navPlaylists: "Listas", playlistAll: "Todas las canciones", playlistRecent: "Reproducido recientemente",
        libraryDesc: "Todas tus canciones generadas en un solo lugar.", favoritesDesc: "Tus canciones favoritas.", historyDesc: "Canciones generadas recientemente.",
        toastMusicStarted: "¡Generación de música iniciada!", toastMusicReady: "Música lista: ", toastLyricsSuccess: "¡Letra generada con éxito!", toastLyricsError: "Generación de letra fallida.", toastVoiceCloneSuccess: "¡Voz clonada con éxito!", toastVoiceCloneError: "Clon de voz fallido.",
        langMenuLabel: "Idioma de interfaz", langMismatchWarn: "⚠️ El idioma de la letra no coincide con el idioma de la voz seleccionada.", langMismatchTitle: "Desajuste de idioma"
      },
      ar: {
        subtitle: "عندما لا تكفي الكلمات، دع الموسيقى تتحدث عنك.",
        createTitle: "إنشاء موسيقى", createDesc: "اكتب شعورًا أو قصة أو كلمات أو أسلوبًا. سيحولها Music Speaks إلى أغنية.",
        emailLabel: "البريد الإلكتروني (اختياري)", emailHint: "اختياري. التنزيل هو الطريقة الرئيسية للحصول على ملف MP3.",
        emailPlaceholder: "your@email.com",
        titleLabel: "عنوان الأغنية (اختياري)", titleHint: "إذا تركته فارغًا، سيقوم Music Speaks بإنشاء عنوان من الكلمات.",
        titlePlaceholder: "اتركه فارغًا وسيسمي الذكاء الاصطناعي الأغنية",
        promptLabel: "وصف الأسلوب الموسيقي", promptHint: "قم بتضمين الأسلوب والمزاج والآلات والسرعة والمراجع.",
        promptPlaceholder: "موسيقى البوب الإلكترونية المشرقة والثقة، إنتاج أنيق، خطاف لا يُنسى",
        lyricsIdeaLabel: "تعليمات الكلمات (اختياري)", lyricsIdeaHint: "صف ما تريد أو أعطِ الذكاء الاصطناعي تعليمات. يتم تطبيق لغة الصوت المحدد تلقائيًا.",
        lyricsExtraLabel: "المتطلبات الإضافية (اختياري)", lyricsExtraHint: "صف المتطلبات الإضافية: المدة والنبرة العاطفية والأسلوب والمزاج والسرعة والهيكل.",
        lyricsExtraPlaceholder: "المدة: 3-5 دقائق / العاطفة: حزين، متفائل / الأسلوب: شعري / المزاج: قاتم",
        lyricsIdeaPlaceholder: "اكتب القصة والمشاعر والصور أو المقاطع التي تريدها.",
        generateLyrics: "إنشاء كلمات", generatingLyrics: "جاري إنشاء الكلمات...", lyricsGenerated: "تمت إضافة الكلمات أدناه. يمكنك تعديلها قبل إنشاء الموسيقى.",
        lyricsAssistNeedBrief: "أضف أولاً وصفًا أو أسلوبًا موسيقيًا.", lyricsAssistFailed: "فشل إنشاء الكلمات.",
        lyricsLabel: "الكلمات الكاملة (اختياري)", lyricsHint: "الصق الكلمات الكاملة هنا إذا كانت متوفرة. الكلمات الكاملة لها الأولوية.",
        lyricsPlaceholder: "[المقطع]\nكلماتك هنا...\n[اللازمة]\nلازمتك هنا...",
        instrumental: "آلي", instrumentalHint: "بدون أصوات. سيتم تجاهل الكلمات.",
        autoLyrics: "إنشاء تلقائي للكلمات", autoLyricsHint: "الذكاء الاصطناعي يكتب الكلمات بناءً على وصفك.",
        voiceCloneLabel: "استنساخ الصوت (اختياري)", voiceRecordBtn: "تسجيل صوتي", voiceCloneHint: "سجل 5 جمل قصيرة بأصوات مختلفة. يستغرق حوالي 30 ثانية. تنتهي صلاحية الصوت المستنسخ بعد 7 أيام.",
        voicePreviewBtn: "معاينة الصوت", voiceUploading: "جاري استنساخ صوتك...", voiceReady: "تم استنساخ الصوت! انقر على المعاينة للاستماع.",
        voiceError: "فشل استنساخ الصوت.", voicePreviewGenerating: "جاري إنشاء المعاينة...", voicePreviewReady: "المعاينة جاهزة.", voicePreviewError: "فشلت المعاينة.",
        voiceSingingMode: "وضع تركيب الغناء", voiceSingingModeHint: "جرب أولاً MiniMax voice_clone_singing. إذا لم يكن متوفرًا، استخدم voice cover.",
        voicePickerLabel: "أسلوب الصوت", voicePickerDefault: "انقر للتحديد - هذا يحدد لغة الكلمات", voicePickerLoading: "جاري تحميل الأصوات...", voiceShowMore: "عرض {count} إضافية",
        voicePreviewSample: "استمع إلى هذه العينة الصوتية",
        voiceCustomBtn: "صوتي", voiceCustomDesc: "سجل واستخدم صوتك الخاص",
        recModalTitle: "سجل صوتك",
        templates: "قوالب الأنماط",
        advanced: "معلمات إضافية", genre: "النوع", mood: "المزاج", instruments: "الآلات", tempo: "الإيقاع", bpm: "BPM", key: "المفتاح",
        vocals: "أسلوب الصوت", structure: "هيكل الأغنية", references: "المراجع", avoid: "تجنب", useCase: "حالة الاستخدام", extra: "تفاصيل إضافية",
        genrePlaceholder: "بوب، ريغي، جاز", moodPlaceholder: "دافئ، مشرق، مكثف", instrumentsPlaceholder: "بيانو، جيتار، طبول",
        tempoPlaceholder: "سريع، بطيء، معتدل", bpmPlaceholder: "85", keyPlaceholder: "دو كبير، لا صغير",
        vocalsPlaceholder: "صوت ذكر دافئ، صوت أنثوي مشرق", structurePlaceholder: "مقطع-لازمة-مقطع-جسر-لازمة",
        referencesPlaceholder: "مشابه لـ...", avoidPlaceholder: "محتوى صريح، ضبط صوتي مفرط",
        useCasePlaceholder: "خلفية فيديو، أغنية موضوعية", extraPlaceholder: "ملاحظات إضافية",
        submit: "إنشاء موسيقى", jobsTitle: "المهام", jobsDesc: "الحالة في الوقت الفعلي. يظهر زر التنزيل عند جاهزية ملف MP3.",
        clearDraft: "مسح المسودة", clearDraftConfirm: "مسح المسودة الحالية؟ لن يؤدي هذا إلى حذف الموسيقى المولدة.",
        draftSaved: "تم حفظ المسودة", draftRestored: "تم استعادة المسودة السابقة", draftCleared: "تم مسح المسودة", draftRestoreFailed: "تعذر استعادة المسودة من الخادم.",
        empty: "لا توجد مهام بعد. أكمل النموذج للبدء.", queued: "في الانتظار", running: "جاري الإنشاء", completed: "تم", error: "خطأ", unknown: "غير معروف",
        download: "تحميل MP3", delete: "حذف", sent: "أرسل إلى", instrumentalMode: "آلي", vocalMode: "صوتي", deleteConfirm: "حذف هذه المهمة؟", deleteFailed: "فشل الحذف",
        navCreate: "إنشاء", navLibrary: "المكتبة", navFavorites: "المفضلة", navHistory: "السجل", navPlaylists: "القوائم", playlistAll: "جميع الأغاني", playlistRecent: "تم تشغيلها مؤخرًا",
        libraryDesc: "جميع أغانيك المولدة في مكان واحد.", favoritesDesc: "أغانيك المفضلة.", historyDesc: "الأغاني المولدة مؤخرًا.",
        toastMusicStarted: "بدأ إنشاء الموسيقى!", toastMusicReady: "الموسيقى جاهزة: ", toastLyricsSuccess: "تم إنشاء الكلمات بنجاح!", toastLyricsError: "فشل إنشاء الكلمات.", toastVoiceCloneSuccess: "تم استنساخ الصوت بنجاح!", toastVoiceCloneError: "فشل استنساخ الصوت.",
        langMenuLabel: "لغة الواجهة", langMismatchWarn: "⚠️ لغة الكلمات لا تتطابق مع لغة الصوت المحدد.", langMismatchTitle: "عدم تطابق اللغة"
      },
      hi: {
        subtitle: "जब शब्द काफी नहीं होते, तो संगीत आपके लिए बोले।",
        createTitle: "संगीत बनाएं", createDesc: "एक भावना, कहानी, गीत या शैली लिखें। Music Speaks इसे एक गाने में बदल देगा।",
        emailLabel: "ईमेल पता (वैकल्पिक)", emailHint: "वैकल्पिक। डाउनलोड आपकी MP3 प्राप्त करने का मुख्य तरीका है।",
        emailPlaceholder: "your@email.com",
        titleLabel: "गाने का शीर्षक (वैकल्पिक)", titleHint: "यदि खाली छोड़ा जाए, तो Music Speaks गीत से शीर्षक बनाएगा।",
        titlePlaceholder: "खाली छोड़ें और AI गाने का नाम देगा",
        promptLabel: "संगीत शैली विवरण", promptHint: "शैली, मूड, वाद्ययंत्र, गति और संदर्भ शामिल करें।",
        promptPlaceholder: "चमकीला और आत्मविश्वासी इलेक्ट्रॉनिक पॉप, परिष्कृत प्रोडक्शन, यादगार हुक",
        lyricsIdeaLabel: "गीत निर्देश (वैकल्पिक)", lyricsIdeaHint: "जो चाहें वह वर्णन करें या AI को निर्देश दें। चयनित ध्वनि की भाषा स्वचालित रूप से लागू होती है।",
        lyricsExtraLabel: "अतिरिक्त आवश्यकताएं (वैकल्पिक)", lyricsExtraHint: "अतिरिक्त आवश्यकताओं का वर्णन करें: अवधि, भावनात्मक स्वर, शैली, मूड, गति, संरचना।",
        lyricsExtraPlaceholder: "अवधि: 3-5 मिनट / भावना: उदास, आशावान / शैली: काव्यात्मक / मूड: अंधेरा",
        lyricsIdeaPlaceholder: "जो कहानी, भावनाएं, दृश्य या टुकड़े चाहते हैं वे लिखें।",
        generateLyrics: "गीत बनाएं", generatingLyrics: "गीत बना रहे हैं...", lyricsGenerated: "गीत नीचे जोड़ा गया। संगीत बनाने से पहले संपादित कर सकते हैं।",
        lyricsAssistNeedBrief: "पहले एक विवरण या संगीत शैली जोड़ें।", lyricsAssistFailed: "गीत बनाना विफल रहा।",
        lyricsLabel: "पूरा गीत (वैकल्पिक)", lyricsHint: "यदि आपके पास पहले से पूरा गीत है तो यहां पेस्ट करें। पूरा गीत प्राथमिकता लेता है।",
        lyricsPlaceholder: "[पहला]\nआपका गीत यहां...\n[कवर]\nआपका कवर यहां...",
        instrumental: "वाद्य", instrumentalHint: "बिना वोकल के। गीत को अनदेखा किया जाएगा।",
        autoLyrics: "स्वतः गीत बनाएं", autoLyricsHint: "AI आपके विवरण के अनुसार गीत लिखता है।",
        voiceCloneLabel: "आवाज़ क्लोन (वैकल्पिक)", voiceRecordBtn: "मेरी आवाज़ रिकॉर्ड करें", voiceCloneHint: "विभिन्न स्वरों में 5 छोटे वाक्य रिकॉर्ड करें। लगभग 30 सेकंड। क्लोन की गई आवाज़ 7 दिनों में समाप्त हो जाती है।",
        voicePreviewBtn: "आवाज़ पूर्वावलोकन", voiceUploading: "आपकी आवाज़ क्लोन हो रही है...", voiceReady: "आवाज़ क्लोन हो गई! सुनने के लिए पूर्वावलोकन पर क्लिक करें।",
        voiceError: "आवाज़ क्लोन विफल।", voicePreviewGenerating: "पूर्वावलोकन बना रहे हैं...", voicePreviewReady: "पूर्वावलोकन तैयार।", voicePreviewError: "पूर्वावलोकन विफल।",
        voiceSingingMode: "गायन संश्लेषण मोड", voiceSingingModeHint: "पहले MiniMax voice_clone_singing आज़माएं। यदि उपलब्ध नहीं है, तो voice cover पर जाएं।",
        voicePickerLabel: "आवाज़ शैली", voicePickerDefault: "चुनने के लिए क्लिक करें - यह गीत की भाषा निर्धारित करता है", voicePickerLoading: "आवाज़ें लोड हो रही हैं...", voiceShowMore: "{count} और दिखाएं",
        voicePreviewSample: "इस आवाज़ नमूने को सुनें",
        voiceCustomBtn: "मेरी आवाज़", voiceCustomDesc: "अपनी आवाज़ रिकॉर्ड करें और उपयोग करें",
        recModalTitle: "अपनी आवाज़ रिकॉर्ड करें",
        templates: "शैली टेम्पलेट",
        advanced: "अतिरिक्त पैरामीटर", genre: "शैली", mood: "मूड", instruments: "वाद्ययंत्र", tempo: "गति", bpm: "BPM", key: "कुंजी",
        vocals: "वोकल शैली", structure: "गाने की संरचना", references: "संदर्भ", avoid: "बचें", useCase: "उपयोग मामला", extra: "अतिरिक्त विवरण",
        genrePlaceholder: "पॉप, रेगे, जैज़", moodPlaceholder: "गर्म, चमकदार, तीव्र", instrumentsPlaceholder: "पियानो, गिटार, ड्रम",
        tempoPlaceholder: "तेज़, धीमा, मध्यम", bpmPlaceholder: "85", keyPlaceholder: "सी मेजर, ए माइनर",
        vocalsPlaceholder: "गर्म पुरुष वोकल, चमकदार महिला वोकल", structurePlaceholder: "वर्स-कवर-वर्स-ब्रिज-कवर",
        referencesPlaceholder: "समान...", avoidPlaceholder: "स्पष्ट सामग्री, अत्यधिक ऑटो-ट्यून",
        useCasePlaceholder: "वीडियो पृष्ठभूमि, थीम गाना", extraPlaceholder: "अतिरिक्त नोट्स",
        submit: "संगीत बनाएं", jobsTitle: "कार्य", jobsDesc: "रीयल-टाइम स्थिति। MP3 तैयार होने पर डाउनलोड बटन दिखाई देता है।",
        clearDraft: "ड्राफ्ट साफ़ करें", clearDraftConfirm: "वर्तमान ड्राफ्ट साफ़ करें? यह उत्पन्न संगीत को नहीं हटाएगा।",
        draftSaved: "ड्राफ्ट सहेजा गया", draftRestored: "पिछला ड्राफ्ट पुनर्स्थापित", draftCleared: "ड्राफ्ट साफ़ किया गया", draftRestoreFailed: "सर्वर से ड्राफ्ट पुनर्स्थापित करने में विफल।",
        empty: "अभी तक कोई कार्य नहीं। शुरू करने के लिए फॉर्म भरें।", queued: "कतार में", running: "बना रहे हैं", completed: "हो गया", error: "त्रुटि", unknown: "अज्ञात",
        download: "MP3 डाउनलोड करें", delete: "हटाएं", sent: "को भेजा गया", instrumentalMode: "वाद्य", vocalMode: "वोकल", deleteConfirm: "इस कार्य को हटाएं?", deleteFailed: "हटाने में विफल",
        navCreate: "बनाएं", navLibrary: "लाइब्रेरी", navFavorites: "पसंदीदा", navHistory: "इतिहास", navPlaylists: "प्लेलिस्ट", playlistAll: "सभी गाने", playlistRecent: "हाल ही में चलाया गया",
        libraryDesc: "आपके सभी उत्पन्न गाने एक ही जगह।", favoritesDesc: "आपके पसंदीदा गाने।", historyDesc: "हाल ही में उत्पन्न गाने।",
        toastMusicStarted: "संगीत निर्माण शुरू!", toastMusicReady: "संगीत तैयार: ", toastLyricsSuccess: "गीत सफलतापूर्वक बना!", toastLyricsError: "गीत बनाना विफल।", toastVoiceCloneSuccess: "आवाज़ क्लोन सफल!", toastVoiceCloneError: "आवाज़ क्लोन विफल।",
        langMenuLabel: "इंटरफ़ेस भाषा", langMismatchWarn: "⚠️ गीत की भाषा चयनित आवाज़ की भाषा से मेल नहीं खाती।", langMismatchTitle: "भाषा बेमेल"
      },
      id: {
        subtitle: "Ketika kata-kata tidak cukup, biarkan musik berbicara untuk Anda.",
        createTitle: "Buat Musik", createDesc: "Tulis perasaan, cerita, lirik, atau gaya. Music Speaks akan mengubahnya menjadi lagu.",
        emailLabel: "Alamat email (opsional)", emailHint: "Opsional. Unduhan adalah cara utama untuk mendapatkan MP3 Anda.",
        emailPlaceholder: "your@email.com",
        titleLabel: "Judul lagu (opsional)", titleHint: "Jika dikosongkan, Music Speaks akan membuat judul dari lirik.",
        titlePlaceholder: "Kosongkan dan AI akan memberi nama lagu",
        promptLabel: "Deskripsi gaya musik", promptHint: "Sertakan gaya, suasana, instrumen, tempo, dan referensi.",
        promptPlaceholder: "Pop elektronik yang ceria dan percaya diri, produksi rapi, hook yang tak terlupakan",
        lyricsIdeaLabel: "Petunjuk lirik (opsional)", lyricsIdeaHint: "Jelaskan apa yang Anda inginkan atau beri tahu AI. Bahasa suara yang dipilih diterapkan secara otomatis.",
        lyricsExtraLabel: "Persyaratan tambahan (opsional)", lyricsExtraHint: "Jelaskan persyaratan tambahan: durasi, nada emosional, gaya, suasana, tempo, struktur.",
        lyricsExtraPlaceholder: "Durasi: 3-5 menit / Emosi: sedih, penuh harapan / Gaya: puitis / Suasana: gelap",
        lyricsIdeaPlaceholder: "Tulis cerita, perasaan, gambar, atau fragmen yang Anda inginkan.",
        generateLyrics: "Buat lirik", generatingLyrics: "Membuat lirik...", lyricsGenerated: "Lirik ditambahkan di bawah. Anda dapat mengeditnya sebelum membuat musik.",
        lyricsAssistNeedBrief: "Tambahkan deskripsi atau gaya musik terlebih dahulu.", lyricsAssistFailed: "Gagal membuat lirik.",
        lyricsLabel: "Lirik lengkap (opsional)", lyricsHint: "Tempelkan lirik lengkap di sini jika sudah ada. Lirik lengkap lebih diutamakan.",
        lyricsPlaceholder: "[ bait]\nLirik Anda di sini...\n[ refrain]\nRefrain Anda di sini...",
        instrumental: "Instrumental", instrumentalHint: "Tanpa vokal. Lirik akan diabaikan.",
        autoLyrics: "Buat lirik otomatis", autoLyricsHint: "AI menulis lirik berdasarkan deskripsi Anda.",
        voiceCloneLabel: "Kloning suara (opsional)", voiceRecordBtn: "Rekam suara saya", voiceCloneHint: "Rekam 5 kalimat pendek dengan nada berbeda. Membutuhkan sekitar 30 detik. Suara yang dikloning akan kedaluwarsa dalam 7 hari.",
        voicePreviewBtn: "Pratinjau suara", voiceUploading: "Mengkloning suara Anda...", voiceReady: "Suara dikloning! Klik Pratinjau untuk mendengarkan.",
        voiceError: "Kloning suara gagal.", voicePreviewGenerating: "Membuat pratinjau...", voicePreviewReady: "Pratinjau siap.", voicePreviewError: "Pratinjau gagal.",
        voiceSingingMode: "Mode sintesis nyanyian", voiceSingingModeHint: "Coba MiniMax voice_clone_singing terlebih dahulu. Jika tidak tersedia, gunakan voice cover.",
        voicePickerLabel: "Gaya suara", voicePickerDefault: "Klik untuk memilih - ini menetapkan bahasa lirik", voicePickerLoading: "Memuat suara...", voiceShowMore: "Tampilkan {count} lagi",
        voicePreviewSample: "Dengarkan sampel suara ini",
        voiceCustomBtn: "Suara saya", voiceCustomDesc: "Rekam dan gunakan suara Anda sendiri",
        recModalTitle: "Rekam suara Anda",
        templates: "Template gaya",
        advanced: "Parameter tambahan", genre: "Genre", mood: "Suasana", instruments: "Instrumen", tempo: "Tempo", bpm: "BPM", key: "Nada",
        vocals: "Gaya vokal", structure: "Struktur lagu", references: "Referensi", avoid: "Hindari", useCase: "Kasus penggunaan", extra: "Detail tambahan",
        genrePlaceholder: "pop, reggae, jazz", moodPlaceholder: "hangat, cerah, intens", instrumentsPlaceholder: "piano, gitar, drum",
        tempoPlaceholder: "cepat, lambat, sedang", bpmPlaceholder: "85", keyPlaceholder: "C mayor, A minor",
        vocalsPlaceholder: "vokal pria yang hangat, vokal wanita yang cerah", structurePlaceholder: "bait-refrain-bait-bridge-refrain",
        referencesPlaceholder: "mirip dengan...", avoidPlaceholder: "konten eksplisit, auto-tune berlebihan",
        useCasePlaceholder: "latar belakang video, lagu tematik", extraPlaceholder: "Catatan tambahan",
        submit: "Buat Musik", jobsTitle: "Pekerjaan", jobsDesc: "Status waktu nyata. Tombol unduh muncul saat MP3 siap.",
        clearDraft: "Hapus draf", clearDraftConfirm: "Hapus draf saat ini? Ini tidak akan menghapus musik yang dihasilkan.",
        draftSaved: "Draf disimpan", draftRestored: "Draf sebelumnya dipulihkan", draftCleared: "Draf dihapus", draftRestoreFailed: "Tidak dapat memulihkan draf dari server.",
        empty: "Belum ada pekerjaan. Isi formulir untuk memulai.", queued: "Dalam antrean", running: "Membuat", completed: "Selesai", error: "Kesalahan", unknown: "Tidak dikenal",
        download: "Unduh MP3", delete: "Hapus", sent: "Dikirim ke", instrumentalMode: "Instrumental", vocalMode: "Vokal", deleteConfirm: "Hapus pekerjaan ini?", deleteFailed: "Gagal menghapus",
        navCreate: "Buat", navLibrary: "Perpustakaan", navFavorites: "Favorit", navHistory: "Riwayat", navPlaylists: "Daftar putar", playlistAll: "Semua lagu", playlistRecent: "Baru diputar",
        libraryDesc: "Semua lagu yang Anda hasilkan di satu tempat.", favoritesDesc: "Lagu favorit Anda.", historyDesc: "Lagu yang baru dihasilkan.",
        toastMusicStarted: "Pembuatan musik dimulai!", toastMusicReady: "Musik siap: ", toastLyricsSuccess: "Lirik berhasil dibuat!", toastLyricsError: "Gagal membuat lirik.", toastVoiceCloneSuccess: "Kloning suara berhasil!", toastVoiceCloneError: "Kloning suara gagal.",
        langMenuLabel: "Bahasa antarmuka", langMismatchWarn: "⚠️ Bahasa lirik tidak cocok dengan bahasa suara yang dipilih.", langMismatchTitle: "Ketidakcocokan bahasa"
      },
      vi: {
        subtitle: "Khi lời nói là không đủ, hãy để âm nhạc thay bạn lên tiếng.",
        createTitle: "Tạo nhạc", createDesc: "Viết một cảm xúc, câu chuyện, lời bài hát hoặc phong cách. Music Speaks sẽ chuyển nó thành một bài hát.",
        emailLabel: "Địa chỉ email (tùy chọn)", emailHint: "Tùy chọn. Tải xuống là cách chính để nhận file MP3 của bạn.",
        emailPlaceholder: "your@email.com",
        titleLabel: "Tiêu đề bài hát (tùy chọn)", titleHint: "Nếu để trống, Music Speaks sẽ tạo tiêu đề từ lời bài hát.",
        titlePlaceholder: "Để trống và AI sẽ đặt tên cho bài hát",
        promptLabel: "Mô tả phong cách âm nhạc", promptHint: "Bao gồm phong cách, tâm trạng, nhạc cụ, tốc độ và tham chiếu.",
        promptPlaceholder: "Pop điện tử sáng sủa và tự tin, sản xuất tinh tế, giai điệu đáng nhớ",
        lyricsIdeaLabel: "Hướng dẫn lời bài hát (tùy chọn)", lyricsIdeaHint: "Mô tả những gì bạn muốn hoặc đưa ra chỉ dẫn cho AI. Ngôn ngữ của giọng nói được chọn sẽ được áp dụng tự động.",
        lyricsExtraLabel: "Yêu cầu bổ sung (tùy chọn)", lyricsExtraHint: "Mô tả các yêu cầu bổ sung: thời lượng, giọng điệu cảm xúc, phong cách, tâm trạng, tốc độ, cấu trúc.",
        lyricsExtraPlaceholder: "Thời lượng: 3-5 phút / Cảm xúc: buồn, lạc quan / Phong cách: thơ ca / Tâm trạng: u ám",
        lyricsIdeaPlaceholder: "Viết câu chuyện, cảm xúc, hình ảnh hoặc đoạn trích bạn muốn.",
        generateLyrics: "Tạo lời bài hát", generatingLyrics: "Đang tạo lời bài hát...", lyricsGenerated: "Lời bài hát đã được thêm bên dưới. Bạn có thể chỉnh sửa trước khi tạo nhạc.",
        lyricsAssistNeedBrief: "Thêm mô tả hoặc phong cách âm nhạc trước.", lyricsAssistFailed: "Tạo lời bài hát thất bại.",
        lyricsLabel: "Lời bài hát đầy đủ (tùy chọn)", lyricsHint: "Dán lời bài hát đầy đủ tại đây nếu bạn đã có. Lời bài hát đầy đủ được ưu tiên.",
        lyricsPlaceholder: "[ verse]\nLời bài hát của bạn ở đây...\n[ hook]\nĐiệp khúc của bạn ở đây...",
        instrumental: "Nhạc không lời", instrumentalHint: "Không có giọng hát. Lời bài hát sẽ bị bỏ qua.",
        autoLyrics: "Tự động tạo lời bài hát", autoLyricsHint: "AI viết lời bài hát dựa trên mô tả của bạn.",
        voiceCloneLabel: "Sao chép giọng nói (tùy chọn)", voiceRecordBtn: "Ghi âm giọng nói của tôi", voiceCloneHint: "Ghi 5 câu ngắn với các giọng điệu khác nhau. Mất khoảng 30 giây. Giọng nói được sao chép sẽ hết hạn sau 7 ngày.",
        voicePreviewBtn: "Xem trước giọng nói", voiceUploading: "Đang sao chép giọng nói của bạn...", voiceReady: "Giọng nói đã được sao chép! Nhấp vào Xem trước để nghe.",
        voiceError: "Sao chép giọng nói thất bại.", voicePreviewGenerating: "Đang tạo xem trước...", voicePreviewReady: "Xem trước đã sẵn sàng.", voicePreviewError: "Xem trước thất bại.",
        voiceSingingMode: "Chế độ tổng hợp hát", voiceSingingModeHint: "Thử MiniMax voice_clone_singing trước. Nếu không có sẵn, hãy sử dụng voice cover.",
        voicePickerLabel: "Phong cách giọng nói", voicePickerDefault: "Nhấp để chọn - điều này đặt ngôn ngữ lời bài hát", voicePickerLoading: "Đang tải giọng nói...", voiceShowMore: "Hiển thị thêm {count}",
        voicePreviewSample: "Nghe mẫu giọng nói này",
        voiceCustomBtn: "Giọng nói của tôi", voiceCustomDesc: "Ghi và sử dụng giọng nói của riêng bạn",
        recModalTitle: "Ghi âm giọng nói của bạn",
        templates: "Mẫu phong cách",
        advanced: "Tham số bổ sung", genre: "Thể loại", mood: "Tâm trạng", instruments: "Nhạc cụ", tempo: "Nhịp độ", bpm: "BPM", key: "Khóa",
        vocals: "Phong cách giọng hát", structure: "Cấu trúc bài hát", references: "Tham chiếu", avoid: "Tránh", useCase: "Trường hợp sử dụng", extra: "Chi tiết bổ sung",
        genrePlaceholder: "pop, reggae, jazz", moodPlaceholder: "ấm áp, rực rỡ, mạnh mẽ", instrumentsPlaceholder: "piano, guitar, trống",
        tempoPlaceholder: "nhanh, chậm, vừa", bpmPlaceholder: "85", keyPlaceholder: "Đô trưởng, La thứ",
        vocalsPlaceholder: "giọng nam ấm áp, giọng nữ rực rỡ", structurePlaceholder: "verse-hook-verse-bridge-hook",
        referencesPlaceholder: "tương tự như...", avoidPlaceholder: "nội dung explicit, auto-tune quá mức",
        useCasePlaceholder: "nhạc nền video, bài hát chủ đề", extraPlaceholder: "Ghi chú bổ sung",
        submit: "Tạo nhạc", jobsTitle: "Công việc", jobsDesc: "Trạng thái thời gian thực. Nút tải xuống xuất hiện khi MP3 sẵn sàng.",
        clearDraft: "Xóa bản nháp", clearDraftConfirm: "Xóa bản nháp hiện tại? Điều này sẽ không xóa nhạc đã tạo.",
        draftSaved: "Bản nháp đã được lưu", draftRestored: "Đã khôi phục bản nháp trước đó", draftCleared: "Đã xóa bản nháp", draftRestoreFailed: "Không thể khôi phục bản nháp từ máy chủ.",
        empty: "Chưa có công việc nào. Điền vào biểu mẫu để bắt đầu.", queued: "Trong hàng đợi", running: "Đang tạo", completed: "Hoàn thành", error: "Lỗi", unknown: "Không xác định",
        download: "Tải xuống MP3", delete: "Xóa", sent: "Đã gửi đến", instrumentalMode: "Nhạc không lời", vocalMode: "Có lời", deleteConfirm: "Xóa công việc này?", deleteFailed: "Xóa thất bại",
        navCreate: "Tạo", navLibrary: "Thư viện", navFavorites: "Yêu thích", navHistory: "Lịch sử", navPlaylists: "Danh sách phát", playlistAll: "Tất cả bài hát", playlistRecent: "Phát gần đây",
        libraryDesc: "Tất cả bài hát bạn đã tạo ở một nơi.", favoritesDesc: "Những bài hát bạn yêu thích.", historyDesc: "Những bài hát được tạo gần đây.",
        toastMusicStarted: "Bắt đầu tạo nhạc!", toastMusicReady: "Nhạc đã sẵn sàng: ", toastLyricsSuccess: "Tạo lời bài hát thành công!", toastLyricsError: "Tạo lời bài hát thất bại.", toastVoiceCloneSuccess: "Sao chép giọng nói thành công!", toastVoiceCloneError: "Sao chép giọng nói thất bại.",
        langMenuLabel: "Ngôn ngữ giao diện", langMismatchWarn: "⚠️ Ngôn ngữ lời bài hát không khớp với ngôn ngữ giọng nói đã chọn.", langMismatchTitle: "Không khớp ngôn ngữ"
      },
      th: {
        subtitle: "เมื่อคำพูดไม่เพียงพอ ให้ดนตรีเป็นตัวแทนพูดแทนคุณ",
        createTitle: "สร้างเพลง", createDesc: "เขียนความรู้สึก เรื่องราว เนื้อเพลง หรือสไตล์ Music Speaks จะแปลงมันเป็นเพลง",
        emailLabel: "อีเมล (ไม่บังคับ)", emailHint: "ไม่บังคับ การดาวน์โหลดเป็นวิธีหลักในการรับไฟล์ MP3 ของคุณ",
        emailPlaceholder: "your@email.com",
        titleLabel: "ชื่อเพลง (ไม่บังคับ)", titleHint: "หากปล่อยว่าง Music Speaks จะสร้างชื่อจากเนื้อเพลง",
        titlePlaceholder: "ปล่อยว่างและ AI จะตั้งชื่อเพลง",
        promptLabel: "คำอธิบายสไตล์เพลง", promptHint: "รวมสไตล์ อารมณ์ เครื่องดนตรี จังหวะ และข้อมูลอ้างอิง",
        promptPlaceholder: "ป็อปอิเล็กทรอนิกส์สดใสมั่นใจ การผลิตที่เรียบร้อย ฮุกที่น่าจดจำ",
        lyricsIdeaLabel: "คำสั่งเนื้อเพลง (ไม่บังคับ)", lyricsIdeaHint: "อธิบายสิ่งที่คุณต้องการหรือสั่งให้ AI ทำ ภาษาของเสียงที่เลือกจะถูกใช้โดยอัตโนมัติ",
        lyricsExtraLabel: "ข้อกำหนดเพิ่มเติม (ไม่บังคับ)", lyricsExtraHint: "อธิบายข้อกำหนดเพิ่มเติม: ระยะเวลา โทนอารมณ์ สไตล์ อารมณ์ จังหวะ โครงสร้าง",
        lyricsExtraPlaceholder: "ระยะเวลา: 3-5 นาที / อารมณ์: เศร้า มีความหวัง / สไตล์: เชิงกวี / อารมณ์: มืดมน",
        lyricsIdeaPlaceholder: "เขียนเรื่องราว ความรู้สึก ภาพ หรือตอนที่คุณต้องการ",
        generateLyrics: "สร้างเนื้อเพลง", generatingLyrics: "กำลังสร้างเนื้อเพลง...", lyricsGenerated: "เนื้อเพลงถูกเพิ่มด้านล่าง คุณสามารถแก้ไขก่อนสร้างเพลง",
        lyricsAssistNeedBrief: "เพิ่มคำอธิบายหรือสไตล์เพลงก่อน", lyricsAssistFailed: "การสร้างเนื้อเพลงล้มเหลว",
        lyricsLabel: "เนื้อเพลงทั้งหมด (ไม่บังคับ)", lyricsHint: "วางเนื้อเพลงที่สมบูรณ์ที่นี่หากคุณมีแล้ว เนื้อเพลงที่สมบูรณ์มีความสำคัญมากกว่า",
        lyricsPlaceholder: "[ verse]\nเนื้อเพลงของคุณที่นี่...\n[ hook]\nเนื้อเพลงส่วน hook ที่นี่...",
        instrumental: "เครื่องดนตรี", instrumentalHint: "ไม่มีเสียงร้อง เนื้อเพลงจะถูกเพิกเฉย",
        autoLyrics: "สร้างเนื้อเพลงอัตโนมัติ", autoLyricsHint: "AI เขียนเนื้อเพลงตามคำอธิบายของคุณ",
        voiceCloneLabel: "โคลนเสียง (ไม่บังคับ)", voiceRecordBtn: "บันทึกเสียงฉัน", voiceCloneHint: "บันทึก 5 ประโยคสั้นด้วยน้ำเสียงที่แตกต่างกัน ใช้เวลาประมาณ 30 วินาที เสียงที่โคลนจะหมดอายุใน 7 วัน",
        voicePreviewBtn: "ฟังตัวอย่างเสียง", voiceUploading: "กำลังโคลนเสียงของคุณ...", voiceReady: "เสียงถูกโคลนแล้ว! คลิกฟังตัวอย่างเพื่อฟัง",
        voiceError: "การโคลนเสียงล้มเหลว", voicePreviewGenerating: "กำลังสร้างตัวอย่าง...", voicePreviewReady: "ตัวอย่างพร้อมแล้ว", voicePreviewError: "ตัวอย่างล้มเหลว",
        voiceSingingMode: "โหมดการสังเคราะห์การร้อง", voiceSingingModeHint: "ลอง MiniMax voice_clone_singing ก่อน หากไม่พร้อมใช้งาน ให้ใช้ voice cover",
        voicePickerLabel: "สไตล์เสียง", voicePickerDefault: "คลิกเพื่อเลือก - สิ่งนี้กำหนดภาษาของเนื้อเพลง", voicePickerLoading: "กำลังโหลดเสียง...", voiceShowMore: "ดูเพิ่มอีก {count}",
        voicePreviewSample: "ฟังตัวอย่างเสียงนี้",
        voiceCustomBtn: "เสียงของฉัน", voiceCustomDesc: "บันทึกและใช้เสียงของคุณเอง",
        recModalTitle: "บันทึกเสียงของคุณ",
        templates: "แม่แบบสไตล์",
        advanced: "พารามิเตอร์เพิ่มเติม", genre: "แนวเพลง", mood: "อารมณ์", instruments: "เครื่องดนตรี", tempo: "จังหวะ", bpm: "BPM", key: "คีย์",
        vocals: "สไตล์การร้อง", structure: "โครงสร้างเพลง", references: "ข้อมูลอ้างอิง", avoid: "หลีกเลี่ยง", useCase: "กรณีการใช้งาน", extra: "รายละเอียดเพิ่มเติม",
        genrePlaceholder: "ป็อป, รีเก้, แจ็ซ", moodPlaceholder: "อบอุ่น, สดใส, ดุดัน", instrumentsPlaceholder: "เปียโน, กีตาร์, กลอง",
        tempoPlaceholder: "เร็ว, ช้า, ปานกลาง", bpmPlaceholder: "85", keyPlaceholder: "C Major, A Minor",
        vocalsPlaceholder: "เสียงร้องชายอบอุ่น, เสียงร้องหญิงสดใส", structurePlaceholder: "verse-hook-verse-bridge-hook",
        referencesPlaceholder: "คล้ายกับ...", avoidPlaceholder: "เนื้อหาที่ชัดเจน, auto-tune มากเกินไป",
        useCasePlaceholder: "เพลงประกอบวิดีโอ, เพลงธีม", extraPlaceholder: "หมายเหตุเพิ่มเติม",
        submit: "สร้างเพลง", jobsTitle: "งาน", jobsDesc: "สถานะแบบเรียลไทม์ ปุ่มดาวน์โหลดจะปรากฏเมื่อ MP3 พร้อม",
        clearDraft: "ล้างแบบร่าง", clearDraftConfirm: "ล้างแบบร่างปัจจุบัน? สิ่งนี้จะไม่ลบเพลงที่สร้างแล้ว",
        draftSaved: "แบบร่างถูกบันทึก", draftRestored: "แบบร่างก่อนหน้าถูกกู้คืน", draftCleared: "แบบร่างถูกล้าง", draftRestoreFailed: "ไม่สามารถกู้คืนแบบร่างจากเซิร์ฟเวอร์",
        empty: "ยังไม่มีงาน กรอกแบบฟอร์มเพื่อเริ่มต้น", queued: "ในคิว", running: "กำลังสร้าง", completed: "เสร็จสิ้น", error: "ข้อผิดพลาด", unknown: "ไม่รู้จัก",
        download: "ดาวน์โหลด MP3", delete: "ลบ", sent: "ส่งถึง", instrumentalMode: "เครื่องดนตรี", vocalMode: "เสียงร้อง", deleteConfirm: "ลบงานนี้?", deleteFailed: "การลบล้มเหลว",
        navCreate: "สร้าง", navLibrary: "คลังเพลง", navFavorites: "รายการโปรด", navHistory: "ประวัติ", navPlaylists: "เพลย์ลิสต์", playlistAll: "ทั้งหมด", playlistRecent: "เล่นล่าสุด",
        libraryDesc: "เพลงทั้งหมดที่คุณสร้างในที่เดียว", favoritesDesc: "เพลงโปรดของคุณ", historyDesc: "เพลงที่สร้างล่าสุด",
        toastMusicStarted: "เริ่มสร้างเพลง!", toastMusicReady: "เพลงพร้อม: ", toastLyricsSuccess: "สร้างเนื้อเพลงสำเร็จ!", toastLyricsError: "การสร้างเนื้อเพลงล้มเหลว", toastVoiceCloneSuccess: "โคลนเสียงสำเร็จ!", toastVoiceCloneError: "การโคลนเสียงล้มเหลว",
        langMenuLabel: "ภาษาอินเตอร์เฟซ", langMismatchWarn: "⚠️ ภาษาของเนื้อเพลงไม่ตรงกับภาษาของเสียงที่เลือก", langMismatchTitle: "ภาษาไม่ตรงกัน"
      },
      tr: {
        subtitle: "Sözcükler yetersiz kaldığında, müzik sizin adınıza konuşsun.",
        createTitle: "Müzik Oluştur", createDesc: "Bir duygu, hikaye, söz veya stil yazın. Music Speaks bunu bir şarkıya dönüştürür.",
        emailLabel: "E-posta adresi (isteğe bağlı)", emailHint: "İsteğe bağlı. İndirme, MP3 dosyanızı almanın ana yoludur.",
        emailPlaceholder: "your@email.com",
        titleLabel: "Şarkı başlığı (isteğe bağlı)", titleHint: "Boş bırakırsanız, Music Speaks sözlerden bir başlık oluşturacaktır.",
        titlePlaceholder: "Boş bırakın ve AI şarkıya isim versin",
        promptLabel: "Müzik stili açıklaması", promptHint: "Stil, ruh hali, enstrümanlar, tempo ve referansları ekleyin.",
        promptPlaceholder: "Kendinden emin, parlak elektronik pop, rafine prodüksiyon, akılda kalıcı hook",
        lyricsIdeaLabel: "Söz yönergesi (isteğe bağlı)", lyricsIdeaHint: "Ne istediğinizi açıklayın veya AI'a talimat verin. Seçilen sesin dili otomatik olarak uygulanır.",
        lyricsExtraLabel: "Ek gereksinimler (isteğe bağlı)", lyricsExtraHint: "Ek gereksinimleri açıklayın: süre, duygusal ton, stil, ruh hali, tempo, yapı.",
        lyricsExtraPlaceholder: "Süre: 3-5 dakika / Duygu: üzgün, umutlu / Stil: şiirsel / Ruh hali: karanlık",
        lyricsIdeaPlaceholder: "İstediğiniz hikayeyi, duyguları, imgeleri veya fragmanları yazın.",
        generateLyrics: "Söz oluştur", generatingLyrics: "Sözler oluşturuluyor...", lyricsGenerated: "Sözler aşağıya eklendi. Müzik oluşturmadan önce düzenleyebilirsiniz.",
        lyricsAssistNeedBrief: "Önce bir açıklama veya müzik stili ekleyin.", lyricsAssistFailed: "Söz oluşturma başarısız oldu.",
        lyricsLabel: "Tam sözler (isteğe bağlı)", lyricsHint: "Zaten tam sözleriniz varsa buraya yapıştırın. Tam sözler önceliklidir.",
        lyricsPlaceholder: "[ verse]\nSözleriniz buraya...\n[ hook]\nHook sözleriniz buraya...",
        instrumental: "Enstrümantal", instrumentalHint: "Vokalsiz. Sözler görmezden gelinecek.",
        autoLyrics: "Otomatik söz oluştur", autoLyricsHint: "AI açıklamalarınıza göre söz yazar.",
        voiceCloneLabel: "Ses klonlama (isteğe bağlı)", voiceRecordBtn: "Sesimi kaydet", voiceCloneHint: "Farklı tonlarda 5 kısa cümle kaydedin. Yaklaşık 30 saniye sürer. Klonlanan ses 7 gün içinde süresi dolacaktır.",
        voicePreviewBtn: "Ses önizleme", voiceUploading: "Sesiniz klonlanıyor...", voiceReady: "Ses klonlandı! Dinlemek için Önizle'ye tıklayın.",
        voiceError: "Ses klonlama başarısız.", voicePreviewGenerating: "Önizleme oluşturuluyor...", voicePreviewReady: "Önizleme hazır.", voicePreviewError: "Önizleme başarısız.",
        voiceSingingMode: "Şan sentezi modu", voiceSingingModeHint: "Önce MiniMax voice_clone_singing deneyin. Kullanılamıyorsa voice cover'a düşün.",
        voicePickerLabel: "Ses stili", voicePickerDefault: "Seçmek için tıklayın - bu, söz dilini belirler", voicePickerLoading: "Sesler yükleniyor...", voiceShowMore: "{count} tane daha göster",
        voicePreviewSample: "Bu ses örneğini dinle",
        voiceCustomBtn: "Sesim", voiceCustomDesc: "Kendi sesinizi kaydedin ve kullanın",
        recModalTitle: "Sesinizi kaydedin",
        templates: "Stil şablonları",
        advanced: "Ek parametreler", genre: "Tür", mood: "Ruh hali", instruments: "Enstrümanlar", tempo: "Tempo", bpm: "BPM", key: "Anahtar",
        vocals: "Vokal stili", structure: "Şarkı yapısı", references: "Referanslar", avoid: "Kaçının", useCase: "Kullanım durumu", extra: "Ek detaylar",
        genrePlaceholder: "pop, reggae, caz", moodPlaceholder: "sıcak, parlak, yoğun", instrumentsPlaceholder: "piyano, gitar, davul",
        tempoPlaceholder: "hızlı, yavaş, orta", bpmPlaceholder: "85", keyPlaceholder: "Do majör, La minör",
        vocalsPlaceholder: "sıcak erkek vokal, parlak kadın vokal", structurePlaceholder: "verse-hook-verse-bridge-hook",
        referencesPlaceholder: "...benzeri", avoidPlaceholder: "açık içerik, aşırı auto-tune",
        useCasePlaceholder: "video arka planı, tema şarkısı", extraPlaceholder: "Ek notlar",
        submit: "Müzik Oluştur", jobsTitle: "İşler", jobsDesc: "Gerçek zamanlı durum. MP3 hazır olduğunda indirme düğmesi görünür.",
        clearDraft: "Taslağı temizle", clearDraftConfirm: "Mevcut taslağı temizle? Bu, oluşturulan müziği silmez.",
        draftSaved: "Taslak kaydedildi", draftRestored: "Önceki taslak geri yüklendi", draftCleared: "Taslak temizlendi", draftRestoreFailed: "Taslak sunucudan geri yüklenemedi.",
        empty: "Henüz iş yok. Başlamak için formu doldurun.", queued: "Sırada", running: "Oluşturuluyor", completed: "Tamamlandı", error: "Hata", unknown: "Bilinmeyen",
        download: "MP3 indir", delete: "Sil", sent: "Gönderildi", instrumentalMode: "Enstrümantal", vocalMode: "Vokal", deleteConfirm: "Bu işi sil?", deleteFailed: "Silme başarısız",
        navCreate: "Oluştur", navLibrary: "Kütüphane", navFavorites: "Favoriler", navHistory: "Geçmiş", navPlaylists: "Çalma listeleri", playlistAll: "Tüm şarkılar", playlistRecent: "Son çalınan",
        libraryDesc: "Tüm oluşturduğunuz şarkılar tek yerde.", favoritesDesc: "Favori şarkılarınız.", historyDesc: "Yeni oluşturulan şarkılar.",
        toastMusicStarted: "Müzik oluşturma başladı!", toastMusicReady: "Müzik hazır: ", toastLyricsSuccess: "Sözler başarıyla oluşturuldu!", toastLyricsError: "Söz oluşturma başarısız.", toastVoiceCloneSuccess: "Ses klonlama başarılı!", toastVoiceCloneError: "Ses klonlama başarısız.",
        langMenuLabel: "Arayüz dili", langMismatchWarn: "⚠️ Sözlerin dili seçilen sesin diliyle eşleşmiyor.", langMismatchTitle: "Dil uyuşmazlığı"
      },
      pl: {
        subtitle: "Gdy słowa nie wystarczają, niech muzyka przemówi w Twoim imieniu.",
        createTitle: "Twórz muzykę", createDesc: "Napisz uczucie, historię, tekst lub styl. Music Speaks zamieni to w piosenkę.",
        emailLabel: "Adres e-mail (opcjonalnie)", emailHint: "Opcjonalnie. Pobieranie to główny sposób na uzyskanie pliku MP3.",
        emailPlaceholder: "your@email.com",
        titleLabel: "Tytuł piosenki (opcjonalnie)", titleHint: "Jeśli pozostawisz puste, Music Speaks utworzy tytuł na podstawie tekstu.",
        titlePlaceholder: "Zostaw puste, a AI nazwie piosenkę",
        promptLabel: "Opis stylu muzycznego", promptHint: "Uwzględnij styl, nastrój, instrumenty, tempo i referencje.",
        promptPlaceholder: "Jasny i pewny siebie elektroniczny pop, dopracowana produkcja, pamiętny hook",
        lyricsIdeaLabel: "Instrukcje tekstu (opcjonalnie)", lyricsIdeaHint: "Opisz, czego chcesz, lub daj instrukcje AI. Język wybranego głosu zostanie automatycznie zastosowany.",
        lyricsExtraLabel: "Dodatkowe wymagania (opcjonalnie)", lyricsExtraHint: "Opisz dodatkowe wymagania: czas trwania, ton emocjonalny, styl, nastrój, tempo, strukturę.",
        lyricsExtraPlaceholder: "Czas trwania: 3-5 min / Emocja: smutna, pełna nadziei / Styl: poetycki / Nastrój: ponury",
        lyricsIdeaPlaceholder: "Napisz historię, uczucia, obrazy lub fragmenty, które chcesz.",
        generateLyrics: "Generuj tekst", generatingLyrics: "Generowanie tekstu...", lyricsGenerated: "Tekst dodany poniżej. Możesz go edytować przed wygenerowaniem muzyki.",
        lyricsAssistNeedBrief: "Najpierw dodaj opis lub styl muzyczny.", lyricsAssistFailed: "Generowanie tekstu nie powiodło się.",
        lyricsLabel: "Pełny tekst (opcjonalnie)", lyricsHint: "Wklej tutaj pełny tekst, jeśli już go masz. Pełny tekst ma pierwszeństwo.",
        lyricsPlaceholder: "[ zwrotka]\nTwój tekst tutaj...\n[ refren]\nTwój refren tutaj...",
        instrumental: "Instrumentalna", instrumentalHint: "Bez wokalu. Tekst będzie ignorowany.",
        autoLyrics: "Automatycznie generuj tekst", autoLyricsHint: "AI pisze tekst na podstawie Twojego opisu.",
        voiceCloneLabel: "Klonowanie głosu (opcjonalnie)", voiceRecordBtn: "Nagraj mój głos", voiceCloneHint: "Nagraj 5 krótkich zdań w różnych tonacjach. Trwa to około 30 sekund. Sklonowany głos wygaśnie po 7 dniach.",
        voicePreviewBtn: "Podgląd głosu", voiceUploading: "Klonowanie Twojego głosu...", voiceReady: "Głos sklonowany! Kliknij Podgląd, aby posłuchać.",
        voiceError: "Klonowanie głosu nie powiodło się.", voicePreviewGenerating: "Generowanie podglądu...", voicePreviewReady: "Podgląd gotowy.", voicePreviewError: "Podgląd nieudany.",
        voiceSingingMode: "Tryb syntezy śpiewu", voiceSingingModeHint: "Najpierw wypróbuj MiniMax voice_clone_singing. Jeśli niedostępny, użyj voice cover.",
        voicePickerLabel: "Styl głosu", voicePickerDefault: "Kliknij, aby wybrać - to ustawia język tekstu", voicePickerLoading: "Ładowanie głosów...", voiceShowMore: "Pokaż {count} więcej",
        voicePreviewSample: "Posłuchaj tego przykładu głosu",
        voiceCustomBtn: "Mój głos", voiceCustomDesc: "Nagraj i używaj własnego głosu",
        recModalTitle: "Nagraj swój głos",
        templates: "Szablony stylów",
        advanced: "Dodatkowe parametry", genre: "Gatunek", mood: "Nastrój", instruments: "Instrumenty", tempo: "Tempo", bpm: "BPM", key: "Tonacja",
        vocals: "Styl wokalny", structure: "Struktura piosenki", references: "Referencje", avoid: "Unikaj", useCase: "Przypadek użycia", extra: "Dodatkowe szczegóły",
        genrePlaceholder: "pop, reggae, jazz", moodPlaceholder: "ciepły, jasny, intensywny", instrumentsPlaceholder: "fortepian, gitara, perkusja",
        tempoPlaceholder: "szybkie, wolne, umiarkowane", bpmPlaceholder: "85", keyPlaceholder: "C-dur, A-moll",
        vocalsPlaceholder: "ciepły wokal męski, jasny wokal żeński", structurePlaceholder: "zwrotka-refren-zwrotka-most-refren",
        referencesPlaceholder: "podobne do...", avoidPlaceholder: "treści explicit, nadmierny auto-tune",
        useCasePlaceholder: "muzyka do wideo, piosenka tematyczna", extraPlaceholder: "Dodatkowe uwagi",
        submit: "Generuj muzykę", jobsTitle: "Zadania", jobsDesc: "Status w czasie rzeczywistym. Przycisk pobierania pojawia się, gdy MP3 jest gotowy.",
        clearDraft: "Wyczyść szkic", clearDraftConfirm: "Wyczyścić bieżący szkic? Nie usunie to wygenerowanej muzyki.",
        draftSaved: "Szkic zapisany", draftRestored: "Poprzedni szkic przywrócony", draftCleared: "Szkic wyczyszczony", draftRestoreFailed: "Nie można przywrócić szkicu z serwera.",
        empty: "Brak zadań. Wypełnij formularz, aby rozpocząć.", queued: "W kolejce", running: "Generowanie", completed: "Gotowe", error: "Błąd", unknown: "Nieznany",
        download: "Pobierz MP3", delete: "Usuń", sent: "Wysłano do", instrumentalMode: "Instrumentalna", vocalMode: "Wokalna", deleteConfirm: "Usunąć to zadanie?", deleteFailed: "Usuwanie nie powiodło się",
        navCreate: "Twórz", navLibrary: "Biblioteka", navFavorites: "Ulubione", navHistory: "Historia", navPlaylists: "Playlisty", playlistAll: "Wszystkie piosenki", playlistRecent: "Ostatnio odtwarzane",
        libraryDesc: "Wszystkie wygenerowane piosenki w jednym miejscu.", favoritesDesc: "Twoje ulubione piosenki.", historyDesc: "Ostatnio wygenerowane piosenki.",
        toastMusicStarted: "Rozpoczęto generowanie muzyki!", toastMusicReady: "Muzyka gotowa: ", toastLyricsSuccess: "Tekst pomyślnie wygenerowany!", toastLyricsError: "Generowanie tekstu nie powiodło się.", toastVoiceCloneSuccess: "Głos pomyślnie sklonowany!", toastVoiceCloneError: "Klonowanie głosu nie powiodło się.",
        langMenuLabel: "Język interfejsu", langMismatchWarn: "⚠️ Język tekstu nie pasuje do języka wybranego głosu.", langMismatchTitle: "Niezgodność języków"
      },
      nl: {
        subtitle: "Wanneer woorden niet volstaan, laat muziek voor je spreken.",
        createTitle: "Muziek maken", createDesc: "Schrijf een gevoel, verhaal, liedtekst of stijl. Music Speaks zet dit om in een lied.",
        emailLabel: "E-mailadres (optioneel)", emailHint: "Optioneel. Downloaden is de belangrijkste manier om je MP3 te ontvangen.",
        emailPlaceholder: "your@email.com",
        titleLabel: "Liedtitel (optioneel)", titleHint: "Als je het leeg laat, maakt Music Speaks een titel op basis van de tekst.",
        titlePlaceholder: "Laat leeg en AI geeft het lied een naam",
        promptLabel: "Muziekstijl beschrijving", promptHint: "Includeer stijl, stemming, instrumenten, tempo en referenties.",
        promptPlaceholder: "Licht en zelfverzekerd elektronisch pop, verfijnde productie, gedenkwaardige hook",
        lyricsIdeaLabel: "Tekst instructies (optioneel)", lyricsIdeaHint: "Beschrijf wat je wilt of geef AI instructies. De taal van de geselecteerde stem wordt automatisch toegepast.",
        lyricsExtraLabel: "Extra vereisten (optioneel)", lyricsExtraHint: "Beschrijf extra vereisten: duur, emotionele toon, stijl, stemming, tempo, structuur.",
        lyricsExtraPlaceholder: "Duur: 3-5 min / Emotie: verdrietig, hoopvol / Stijl: poëtisch / Stemming: donker",
        lyricsIdeaPlaceholder: "Schrijf het verhaal, gevoelens, beelden of fragmenten die je wilt.",
        generateLyrics: "Genereer tekst", generatingLyrics: "Tekst genereren...", lyricsGenerated: "Tekst hieronder toegevoegd. Je kunt deze bewerken voordat je muziek genereert.",
        lyricsAssistNeedBrief: "Voeg eerst een beschrijving of muziekstijl toe.", lyricsAssistFailed: "Tekst genereren mislukt.",
        lyricsLabel: "Volledige tekst (optioneel)", lyricsHint: "Plak de volledige tekst hier als je deze al hebt. Volledige tekst heeft voorrang.",
        lyricsPlaceholder: "[ couplet]\nJe tekst hier...\n[ refrein]\nJe refrein hier...",
        instrumental: "Instrumentaal", instrumentalHint: "Zonder vocalen. De tekst wordt genegeerd.",
        autoLyrics: "Automatisch tekst genereren", autoLyricsHint: "AI schrijft tekst op basis van je beschrijving.",
        voiceCloneLabel: "Stem klonen (optioneel)", voiceRecordBtn: "Neem mijn stem op", voiceCloneHint: "Neem 5 korte zinnen op in verschillende tonen. Duurt ongeveer 30 seconden. De gekloonde stem vervalt na 7 dagen.",
        voicePreviewBtn: "Stem preview", voiceUploading: "Je stem klonen...", voiceReady: "Stem gekloond! Klik op Preview om te luisteren.",
        voiceError: "Stem klonen mislukt.", voicePreviewGenerating: "Preview genereren...", voicePreviewReady: "Preview klaar.", voicePreviewError: "Preview mislukt.",
        voiceSingingMode: "Zang synthese modus", voiceSingingModeHint: "Probeer eerst MiniMax voice_clone_singing. Als niet beschikbaar, gebruik voice cover.",
        voicePickerLabel: "Stemstijl", voicePickerDefault: "Klik om te selecteren - dit stelt de teksttaal in", voicePickerLoading: "Stemmen laden...", voiceShowMore: "Toon {count} meer",
        voicePreviewSample: "Luister naar dit stemmonster",
        voiceCustomBtn: "Mijn stem", voiceCustomDesc: "Neem je eigen stem op en gebruik deze",
        recModalTitle: "Neem je stem op",
        templates: "Stijl templates",
        advanced: "Extra parameters", genre: "Genre", mood: "Stemming", instruments: "Instrumenten", tempo: "Tempo", bpm: "BPM", key: "Toonaard",
        vocals: "Vocale stijl", structure: "Liedstructuur", references: "Referenties", avoid: "Vermijd", useCase: "Gebruik", extra: "Extra details",
        genrePlaceholder: "pop, reggae, jazz", moodPlaceholder: "warm, helder, intens", instrumentsPlaceholder: "piano, gitaar, drums",
        tempoPlaceholder: "snel, langzaam, gematigd", bpmPlaceholder: "85", keyPlaceholder: "C-groot, A-klein",
        vocalsPlaceholder: "warme mannelijke vocal, heldere vrouwelijke vocal", structurePlaceholder: "couplet-refrein-couplet-brug-refrein",
        referencesPlaceholder: "vergelijkbaar met...", avoidPlaceholder: "expliciete inhoud, overmatige auto-tune",
        useCasePlaceholder: "videobackground, themanummer", extraPlaceholder: "Extra notities",
        submit: "Muziek maken", jobsTitle: "Taken", jobsDesc: "Real-time status. Download knop verschijnt wanneer MP3 klaar is.",
        clearDraft: "Concept wissen", clearDraftConfirm: "Huidig concept wissen? Dit verwijdert de gegenereerde muziek niet.",
        draftSaved: "Concept opgeslagen", draftRestored: "Vorig concept hersteld", draftCleared: "Concept gewist", draftRestoreFailed: "Kon concept niet van server herstellen.",
        empty: "Nog geen taken. Vul het formulier in om te beginnen.", queued: "In de wachtrij", running: "Genereren", completed: "Klaar", error: "Fout", unknown: "Onbekend",
        download: "Download MP3", delete: "Verwijder", sent: "Verzonden naar", instrumentalMode: "Instrumentaal", vocalMode: "Vocaal", deleteConfirm: "Deze taak verwijderen?", deleteFailed: "Verwijderen mislukt",
        navCreate: "Maken", navLibrary: "Bibliotheek", navFavorites: "Favorieten", navHistory: "Geschiedenis", navPlaylists: "Afspeellijsten", playlistAll: "Alle liedjes", playlistRecent: "Recent afgespeeld",
        libraryDesc: "Al je gegenereerde liedjes op één plek.", favoritesDesc: "Je favoriete liedjes.", historyDesc: "Recent gegenereerde liedjes.",
        toastMusicStarted: "Muziekgeneratie gestart!", toastMusicReady: "Muziek klaar: ", toastLyricsSuccess: "Tekst succesvol gegenereerd!", toastLyricsError: "Tekst genereren mislukt.", toastVoiceCloneSuccess: "Stem succesvol gekloond!", toastVoiceCloneError: "Stem klonen mislukt.",
        langMenuLabel: "Interface taal", langMismatchWarn: "⚠️ De teksttaal komt niet overeen met de taal van de geselecteerde stem.", langMismatchTitle: "Taal mismatch"
      },
      sv: {
        subtitle: "När ord inte räcker till, låt musiken tala för dig.",
        createTitle: "Skapa musik", createDesc: "Skriv en känsla, historia, låttext eller stil. Music Speaks gör den till en låt.",
        emailLabel: "E-postadress (valfritt)", emailHint: "Valfritt. Nedladdning är huvudvägen att få din MP3.",
        emailPlaceholder: "your@email.com",
        titleLabel: "Låttitel (valfritt)", titleHint: "Om du lämnar tomt skapar Music Speaks en titel från låttexten.",
        titlePlaceholder: "Lämna tomt så namnger AI låten",
        promptLabel: "Musikstil beskrivning", promptHint: "Inkludera stil, stämning, instrument, tempo och referenser.",
        promptPlaceholder: "Ljus och självsäker elektronisk pop, raffinerad produktion, minnesvärd hook",
        lyricsIdeaLabel: "Låttext instruktioner (valfritt)", lyricsIdeaHint: "Beskriv vad du vill eller ge AI instruktioner. Språket för den valda rösten appliceras automatiskt.",
        lyricsExtraLabel: "Extra krav (valfritt)", lyricsExtraHint: "Beskriv extra krav: längd, känslomässig ton, stil, stämning, tempo, struktur.",
        lyricsExtraPlaceholder: "Längd: 3-5 min / Känsla: ledsen, hoppfull / Stil: poetisk / Stämning: mörk",
        lyricsIdeaPlaceholder: "Skriv historien, känslor, bilder eller fragment du vill ha.",
        generateLyrics: "Skapa låttext", generatingLyrics: "Skapar låttext...", lyricsGenerated: "Låttext tillagd nedan. Du kan redigera den innan du skapar musik.",
        lyricsAssistNeedBrief: "Lägg till en beskrivning eller musikstil först.", lyricsAssistFailed: "Skapande av låttext misslyckades.",
        lyricsLabel: "Full låttext (valfritt)", lyricsHint: "Klistra in full låttext här om du har den. Full låttext har förtur.",
        lyricsPlaceholder: "[ vers]\nDin låttext här...\n[ refäng]\nDin refäng här...",
        instrumental: "Instrumental", instrumentalHint: "Utan sång. Låttexten ignoreras.",
        autoLyrics: "Auto-generera låttext", autoLyricsHint: "AI skriver låttext baserat på din beskrivning.",
        voiceCloneLabel: "Röstkloning (valfritt)", voiceRecordBtn: "Spela in min röst", voiceCloneHint: "Spela in 5 korta meningar i olika toner. Tar cirka 30 sekunder. Den klonade rösten går ut efter 7 dagar.",
        voicePreviewBtn: "Röst förhandsvisning", voiceUploading: "Klonar din röst...", voiceReady: "Röst klonad! Klicka på Förhandsvisning för att lyssna.",
        voiceError: "Röstkloning misslyckades.", voicePreviewGenerating: "Skapar förhandsvisning...", voicePreviewReady: "Förhandsvisning klar.", voicePreviewError: "Förhandsvisning misslyckades.",
        voiceSingingMode: "Sång-syntesläge", voiceSingingModeHint: "Försök först MiniMax voice_clone_singing. Om inte tillgänglig, använd voice cover.",
        voicePickerLabel: "Röststil", voicePickerDefault: "Klicka för att välja - detta ställer in låttextspråket", voicePickerLoading: "Laddar röster...", voiceShowMore: "Visa {count} till",
        voicePreviewSample: "Lyssna på detta röstprov",
        voiceCustomBtn: "Min röst", voiceCustomDesc: "Spela in och använd din egen röst",
        recModalTitle: "Spela in din röst",
        templates: "Stilmallar",
        advanced: "Extra parametrar", genre: "Genre", mood: "Stämning", instruments: "Instrument", tempo: "Tempo", bpm: "BPM", key: "Tonart",
        vocals: "Vokal stil", structure: "Låtstruktur", references: "Referenser", avoid: "Undvik", useCase: "Användningsfall", extra: "Extra detaljer",
        genrePlaceholder: "pop, reggae, jazz", moodPlaceholder: "varm, ljus, intensiv", instrumentsPlaceholder: "piano, gitarr, trummor",
        tempoPlaceholder: "snabb, långsam, måttlig", bpmPlaceholder: "85", keyPlaceholder: "C-dur, A-moll",
        vocalsPlaceholder: "varm mansröst, ljus kvinnoröst", structurePlaceholder: "vers-refäng-vers-bro-refäng",
        referencesPlaceholder: "liknande...", avoidPlaceholder: "explicit innehåll, överdriven auto-tune",
        useCasePlaceholder: "videobakgrund, temalåt", extraPlaceholder: "Ytterligare anteckningar",
        submit: "Skapa musik", jobsTitle: "Uppgifter", jobsDesc: "Real-tids status. Nedladdningsknappen visas när MP3 är redo.",
        clearDraft: "Rensa utkast", clearDraftConfirm: "Rensa aktuellt utkast? Detta tar inte bort genererad musik.",
        draftSaved: "Utkast sparat", draftRestored: "Tidigare utkast återställd", draftCleared: "Utkast rensat", draftRestoreFailed: "Kunde inte återställa utkast från server.",
        empty: "Inga uppgifter ännu. Fyll i formuläret för att börja.", queued: "I kö", running: "Skapar", completed: "Klart", error: "Fel", unknown: "Okänd",
        download: "Ladda ner MP3", delete: "Ta bort", sent: "Skickat till", instrumentalMode: "Instrumental", vocalMode: "Vokal", deleteConfirm: "Ta bort denna uppgift?", deleteFailed: "Borttagning misslyckades",
        navCreate: "Skapa", navLibrary: "Bibliotek", navFavorites: "Favoriter", navHistory: "Historik", navPlaylists: "Spellistor", playlistAll: "Alla låtar", playlistRecent: "Nyligen spelad",
        libraryDesc: "Alla dina genererade låtar på ett ställe.", favoritesDesc: "Dina favoritlåtar.", historyDesc: "Nyligen genererade låtar.",
        toastMusicStarted: "Musikgenerering påbörjad!", toastMusicReady: "Musik redo: ", toastLyricsSuccess: "Låttext skapad framgångsrikt!", toastLyricsError: "Skapande av låttext misslyckades.", toastVoiceCloneSuccess: "Röstkloning framgångsrik!", toastVoiceCloneError: "Röstkloning misslyckades.",
        langMenuLabel: "Gränssnittsspråk", langMismatchWarn: "⚠️ Låttextspråket matchar inte språket för den valda rösten.", langMismatchTitle: "Språk mismatch"
      },
      no: {
        subtitle: "Når ord ikke er nok, la musikken snakke for deg.",
        createTitle: "Lag musikk", createDesc: "Skriv en følelse, historie, sangtekst eller stil. Music Speaks gjør den om til en sang.",
        emailLabel: "E-postadresse (valgfritt)", emailHint: "Valgfritt. Nedlasting er hovedveien til å få MP3-filen din.",
        emailPlaceholder: "your@email.com",
        titleLabel: "Sangtittel (valgfritt)", titleHint: "Hvis du lar den stå tom, lager Music Speaks en tittel fra sangteksten.",
        titlePlaceholder: "La den stå tom og AI gir sangen et navn",
        promptLabel: "Musikkstil beskrivelse", promptHint: "Inkluder stil, humør, instrumenter, tempo og referanser.",
        promptPlaceholder: "Lys og selvsikker elektronisk pop, raffinert produksjon, minneverdig hook",
        lyricsIdeaLabel: "Sangtekst instruksjoner (valgfritt)", lyricsIdeaHint: "Beskriv hva du vil eller gi AI instruksjoner. Språket til den valgte stemmen brukes automatisk.",
        lyricsExtraLabel: "Ekstra krav (valgfritt)", lyricsExtraHint: "Beskriv ekstra krav: varighet, emosjonell tone, stil, humør, tempo, struktur.",
        lyricsExtraPlaceholder: "Varighet: 3-5 min / Følelse: trist, håpefull / Stil: poetisk / Humør: mørkt",
        lyricsIdeaPlaceholder: "Skriv historien, følelsene, bildene eller fragmentene du vil ha.",
        generateLyrics: "Generer sangtekst", generatingLyrics: "Genererer sangtekst...", lyricsGenerated: "Sangtekst lagt til nedenfor. Du kan redigere den før du genererer musikk.",
        lyricsAssistNeedBrief: "Legg til en beskrivelse eller musikkstil først.", lyricsAssistFailed: "Generering av sangtekst mislyktes.",
        lyricsLabel: "Full sangtekst (valgfritt)", lyricsHint: "Lim inn full sangtekst her hvis du har den. Full sangtekst har forrang.",
        lyricsPlaceholder: "[ vers]\nDin sangtekst her...\n[ refeng]\nDin refeng her...",
        instrumental: "Instrumental", instrumentalHint: "Uten vokal. Sangteksten ignoreres.",
        autoLyrics: "Auto-generer sangtekst", autoLyricsHint: "AI skriver sangtekst basert på din beskrivelse.",
        voiceCloneLabel: "Stemmekloning (valgfritt)", voiceRecordBtn: "Ta opp stemmen min", voiceCloneHint: "Ta opp 5 korte setninger i forskjellige toner. Tar omtrent 30 sekunder. Den klonede stemmen utløper etter 7 dager.",
        voicePreviewBtn: "Stemme forhåndsvisning", voiceUploading: "Kloner stemmen din...", voiceReady: "Stemme klonet! Klikk på Forhåndsvisning for å lytte.",
        voiceError: "Stemmekloning mislyktes.", voicePreviewGenerating: "Lager forhåndsvisning...", voicePreviewReady: "Forhåndsvisning klar.", voicePreviewError: "Forhåndsvisning mislyktes.",
        voiceSingingMode: "Sang-syntesemodus", voiceSingingModeHint: "Prøv først MiniMax voice_clone_singing. Hvis ikke tilgjengelig, bruk voice cover.",
        voicePickerLabel: "Stemmestil", voicePickerDefault: "Klikk for å velge - dette setter sangtekstspråket", voicePickerLoading: "Laster stemmer...", voiceShowMore: "Vis {count} til",
        voicePreviewSample: "Lytt til dette stemmeeksempelet",
        voiceCustomBtn: "Min stemme", voiceCustomDesc: "Ta opp og bruk din egen stemme",
        recModalTitle: "Ta opp stemmen din",
        templates: "Stilmaler",
        advanced: "Ekstra parametere", genre: "Sjanger", mood: "Humør", instruments: "Instrumenter", tempo: "Tempo", bpm: "BPM", key: "Nøkkel",
        vocals: "Vokal stil", structure: "Sangstruktur", references: "Referanser", avoid: "Unngå", useCase: "Brukstilfelle", extra: "Ekstra detaljer",
        genrePlaceholder: "pop, reggae, jazz", moodPlaceholder: "varm, lys, intens", instrumentsPlaceholder: "piano, gitar, trommer",
        tempoPlaceholder: "rask, langsom, moderat", bpmPlaceholder: "85", keyPlaceholder: "C-dur, A-moll",
        vocalsPlaceholder: "varm mannsstemme, lys kvinnestemme", structurePlaceholder: "vers-refeng-vers-bro-refeng",
        referencesPlaceholder: "lignende...", avoidPlaceholder: "eksplisitt innhold, overdreven auto-tune",
        useCasePlaceholder: "videobakgrunn, temasang", extraPlaceholder: "Ekstra notater",
        submit: "Lag musikk", jobsTitle: "Oppgaver", jobsDesc: "Sanntids status. Nedlastingsknappen vises når MP3 er klar.",
        clearDraft: "Slett utkast", clearDraftConfirm: "Slette nåværende utkast? Dette sletter ikke generert musikk.",
        draftSaved: "Utkast lagret", draftRestored: "Forrige utkast gjenopprettet", draftCleared: "Utkast slettet", draftRestoreFailed: "Kunne ikke gjenopprette utkast fra server.",
        empty: "Ingen oppgaver ennå. Fyll ut skjema for å begynne.", queued: "I kø", running: "Genererer", completed: "Ferdig", error: "Feil", unknown: "Ukjent",
        download: "Last ned MP3", delete: "Slett", sent: "Sendt til", instrumentalMode: "Instrumental", vocalMode: "Vokal", deleteConfirm: "Slette denne oppgaven?", deleteFailed: "Sletting mislyktes",
        navCreate: "Lag", navLibrary: "Bibliotek", navFavorites: "Favoritter", navHistory: "Historikk", navPlaylists: "Spillelister", playlistAll: "Alle sanger", playlistRecent: "Nylig spilt",
        libraryDesc: "Alle sangene dine generert på ett sted.", favoritesDesc: "Dine favorittsanger.", historyDesc: "Nylig genererte sanger.",
        toastMusicStarted: "Musikkgenerering startet!", toastMusicReady: "Musikk klar: ", toastLyricsSuccess: "Sangtekst generert!", toastLyricsError: "Generering av sangtekst mislyktes.", toastVoiceCloneSuccess: "Stemmekloning vellykket!", toastVoiceCloneError: "Stemmekloning mislyktes.",
        langMenuLabel: "Grensesnittspråk", langMismatchWarn: "⚠️ Sangtekstspråket matcher ikke språket til den valgte stemmen.", langMismatchTitle: "Språkmismatch"
      },
      da: {
        subtitle: "Når ord ikke er nok, lad musikken tale for dig.",
        createTitle: "Skab musik", createDesc: "Skriv en følelse, historie, sangtekst eller stil. Music Speaks gør den til en sang.",
        emailLabel: "E-mailadresse (valgfrit)", emailHint: "Valgfrit. Download er hovedvejen til at få din MP3.",
        emailPlaceholder: "your@email.com",
        titleLabel: "Sangtitel (valgfrit)", titleHint: "Hvis du efterlader det tomt, opretter Music Speaks en titel fra sangteksten.",
        titlePlaceholder: "Efterlad tomt og AI giver sangen et navn",
        promptLabel: "Musikstil beskrivelse", promptHint: "Inkluder stil, humør, instrumenter, tempo og referencer.",
        promptPlaceholder: "Lys og selvsikker elektronisk pop, raffineret produktion, mindeværdig hook",
        lyricsIdeaLabel: "Sangtekst instruktioner (valgfrit)", lyricsIdeaHint: "Beskriv hvad du vil eller giv AI instruktioner. Sproget for den valgte stemme anvendes automatisk.",
        lyricsExtraLabel: "Ekstra krav (valgfrit)", lyricsExtraHint: "Beskriv ekstra krav: varighed, følelsesmæssig tone, stil, humør, tempo, struktur.",
        lyricsExtraPlaceholder: "Varighed: 3-5 min / Følelse: trist, håbefuld / Stil: poetisk / Humør: mørkt",
        lyricsIdeaPlaceholder: "Skriv historien, følelserne, billederne eller fragmenterne du vil have.",
        generateLyrics: "Generer sangtekst", generatingLyrics: "Genererer sangtekst...", lyricsGenerated: "Sangtekst tilføjet nedenfor. Du kan redigere den før du genererer musik.",
        lyricsAssistNeedBrief: "Tilføj først en beskrivelse eller musikstil.", lyricsAssistFailed: "Generering af sangtekst mislykkedes.",
        lyricsLabel: "Fuld sangtekst (valgfrit)", lyricsHint: "Indsæt fuld sangtekst her hvis du har den. Fuld sangtekst har forrang.",
        lyricsPlaceholder: "[ vers]\nDin sangtekst her...\n[ omkvæd]\nDit omkvæd her...",
        instrumental: "Instrumental", instrumentalHint: "Uden vokal. Sangteksten ignoreres.",
        autoLyrics: "Auto-generer sangtekst", autoLyricsHint: "AI skriver sangtekst baseret på din beskrivelse.",
        voiceCloneLabel: "Stemmekloning (valgfrit)", voiceRecordBtn: "Optag min stemme", voiceCloneHint: "Optag 5 korte sætninger i forskellige toner. Det tager cirka 30 sekunder. Den klonede stemme udløber efter 7 dage.",
        voicePreviewBtn: "Stemme forhåndsvisning", voiceUploading: "Kloner din stemme...", voiceReady: "Stemme klonet! Klik på Forhåndsvisning for at lytte.",
        voiceError: "Stemmekloning mislykkedes.", voicePreviewGenerating: "Laver forhåndsvisning...", voicePreviewReady: "Forhåndsvisning klar.", voicePreviewError: "Forhåndsvisning mislykkedes.",
        voiceSingingMode: "Sang-syntese tilstand", voiceSingingModeHint: "Prøv først MiniMax voice_clone_singing. Hvis ikke tilgængelig, brug voice cover.",
        voicePickerLabel: "Stemmestil", voicePickerDefault: "Klik for at vælge - dette indstiller sangtekstssproget", voicePickerLoading: "Indlæser stemmer...", voiceShowMore: "Vis {count} mere",
        voicePreviewSample: "Lyt til dette stemmeeksempel",
        voiceCustomBtn: "Min stemme", voiceCustomDesc: "Optag og brug din egen stemme",
        recModalTitle: "Optag din stemme",
        templates: "Stilskabeloner",
        advanced: "Ekstra parametre", genre: "Genre", mood: "Humør", instruments: "Instrumenter", tempo: "Tempo", bpm: "BPM", key: "Nøgle",
        vocals: "Vokal stil", structure: "Sangstruktur", references: "Referencer", avoid: "Undgå", useCase: "Anvendelsesområde", extra: "Ekstra detaljer",
        genrePlaceholder: "pop, reggae, jazz", moodPlaceholder: "varm, lys, intens", instrumentsPlaceholder: "klaver, guitar, trommer",
        tempoPlaceholder: "hurtig, langsom, moderat", bpmPlaceholder: "85", keyPlaceholder: "C-dur, A-mol",
        vocalsPlaceholder: "varm mandlig vokal, lys kvindelig vokal", structurePlaceholder: "vers-omkvæd-vers-bro-omkvæd",
        referencesPlaceholder: "lignende...", avoidPlaceholder: "eksplicit indhold, overdreven auto-tune",
        useCasePlaceholder: "videobaggrund, temasang", extraPlaceholder: "Yderligere noter",
        submit: "Skab musik", jobsTitle: "Opgaver", jobsDesc: "Real-tid status. Download knappen vises når MP3 er klar.",
        clearDraft: "Slet kladde", clearDraftConfirm: "Slete nuværende kladde? Dette sletter ikke genereret musik.",
        draftSaved: "Kladde gemt", draftRestored: "Forrige kladde genoprettet", draftCleared: "Kladde slettet", draftRestoreFailed: "Kunne ikke genoprette kladde fra server.",
        empty: "Ingen opgaver endnu. Udfyld formularen for at begynde.", queued: "I kø", running: "Genererer", completed: "Færdig", error: "Fejl", unknown: "Ukendt",
        download: "Download MP3", delete: "Slet", sent: "Sendt til", instrumentalMode: "Instrumental", vocalMode: "Vokal", deleteConfirm: "Slet denne opgave?", deleteFailed: "Sletning mislykkedes",
        navCreate: "Skab", navLibrary: "Bibliotek", navFavorites: "Favoritter", navHistory: "Historik", navPlaylists: "Afspilningslister", playlistAll: "Alle sange", playlistRecent: "Nyligt afspillet",
        libraryDesc: "Alle dine genererede sange på ét sted.", favoritesDesc: "Dine yndlingssange.", historyDesc: "Nyligt genererede sange.",
        toastMusicStarted: "Musikgenerering startet!", toastMusicReady: "Musik klar: ", toastLyricsSuccess: "Sangtekst genereret!", toastLyricsError: "Generering af sangtekst mislykkedes.", toastVoiceCloneSuccess: "Stemmekloning vellykket!", toastVoiceCloneError: "Stemmekloning mislykkedes.",
        langMenuLabel: "Grænsefladesprog", langMismatchWarn: "⚠️ Sangtekstssproget matcher ikke sproget for den valgte stemme.", langMismatchTitle: "Sprog mismatch"
      },
      fi: {
        subtitle: "Kun sanat eivät riitä, anna musiikin puhua puolestasi.",
        createTitle: "Luo musiikkia", createDesc: "Kirjoita tunne, tarina, sanoitukset tai tyyli. Music Speaks muuttaa ne lauluksi.",
        emailLabel: "Sähköpostiosoite (valinnainen)", emailHint: "Valinnainen. Lataaminen on pääasiallinen tapa saada MP3-tiedostosi.",
        emailPlaceholder: "your@email.com",
        titleLabel: "Laulun otsikko (valinnainen)", titleHint: "Jos jätät tyhjäksi, Music Speaks luo otsikon sanoitusten perusteella.",
        titlePlaceholder: "Jätä tyhjäksi ja AI nimeää laulun",
        promptLabel: "Musiikkityylin kuvaus", promptHint: "Sisällytä tyyli, tunnelma, soittimet, tempo ja viitteet.",
        promptPlaceholder: "Kirkas ja itsevarma elektroninen pop, hienostunut tuotanto, mieleenpainuva hook",
        lyricsIdeaLabel: "Sanoitusohjeet (valinnainen)", lyricsIdeaHint: "Kuvaile mitä haluat tai anna AI:lle ohjeita. Valitun äänen kieli otetaan automaattisesti käyttöön.",
        lyricsExtraLabel: "Lisävaatimukset (valinnainen)", lyricsExtraHint: "Kuvaile lisävaatimukset: kesto, tunneääni, tyyli, tunnelma, tempo, rakenne.",
        lyricsExtraPlaceholder: "Kesto: 3-5 min / Tunne: surullinen, toiveikas / Tyyli: runollinen / Tunnelma: synkkä",
        lyricsIdeaPlaceholder: "Kirjoita haluamasi tarina, tunteet, kuvat tai katkelmat.",
        generateLyrics: "Luo sanoitukset", generatingLyrics: "Luodaan sanoituksia...", lyricsGenerated: "Sanoitukset lisätty alla. Voit muokata niitä ennen musiikin luomista.",
        lyricsAssistNeedBrief: "Lisää ensin kuvaus tai musiikkityyli.", lyricsAssistFailed: "Sanoitusten luominen epäonnistui.",
        lyricsLabel: "Täydet sanoitukset (valinnainen)", lyricsHint: "Liitä täydet sanoitukset tähän, jos sinulla on ne. Täydet sanoitukset ovat etusijalla.",
        lyricsPlaceholder: "[ säkeistö]\nSanoituksesi tässä...\n[ kertosäe]\nKertosäe tässä...",
        instrumental: "Instrumentaali", instrumentalHint: "Ilman laulua. Sanoitukset ohitetaan.",
        autoLyrics: "Automaattiset sanoitukset", autoLyricsHint: "AI kirjoittaa sanoitukset kuvauksesi perusteella.",
        voiceCloneLabel: "Äänen kloonaus (valinnainen)", voiceRecordBtn: "Nauhoita ääneni", voiceCloneHint: "Nauhoita 5 lyhyttä lausetta eri sävyillä. Kestää noin 30 sekuntia. Kloonattu ääni vanhenee 7 päivässä.",
        voicePreviewBtn: "Äänen esikatselu", voiceUploading: "Kloonataan ääntäsi...", voiceReady: "Ääni kloonattu! Klikkaa Esikatselu kuunnellaksesi.",
        voiceError: "Äänen kloonaus epäonnistui.", voicePreviewGenerating: "Luodaan esikatselua...", voicePreviewReady: "Esikatselu valmis.", voicePreviewError: "Esikatselu epäonnistui.",
        voiceSingingMode: "Laulun synteesitila", voiceSingingModeHint: "Kokeile ensin MiniMax voice_clone_singing. Jos ei ole saatavilla, käytä voice coveria.",
        voicePickerLabel: "Äänityyli", voicePickerDefault: "Klikkaa valitaksesi - tämä asettaa sanoitusten kielen", voicePickerLoading: "Ladataan ääniä...", voiceShowMore: "Näytä {count} lisää",
        voicePreviewSample: "Kuuntele tätä ääninäytettä",
        voiceCustomBtn: "Ääneni", voiceCustomDesc: "Nauhoita ja käytä omaa ääntäsi",
        recModalTitle: "Nauhoita äänesi",
        templates: "Tyylimallit",
        advanced: "Lisäparametrit", genre: "Genre", mood: "Tunnelma", instruments: "Soittimet", tempo: "Tempo", bpm: "BPM", key: "Avain",
        vocals: "Laulutyyli", structure: "Laulun rakenne", references: "Viitteet", avoid: "Vältä", useCase: "Käyttötapaus", extra: "Lisätiedot",
        genrePlaceholder: "pop, reggae, jazz", moodPlaceholder: "lämmin, kirkas, intensiivinen", instrumentsPlaceholder: "piano, kitara, rummut",
        tempoPlaceholder: "nopea, hidas, kohtalainen", bpmPlaceholder: "85", keyPlaceholder: "C-duuri, A-molli",
        vocalsPlaceholder: "lämmin miesääni, kirkas naisääni", structurePlaceholder: "säkeistö-kertosäe-säkeistö-silta-kertosäe",
        referencesPlaceholder: "samankaltainen kuin...", avoidPlaceholder: "eksplisiittinen sisältö, liiallinen auto-tune",
        useCasePlaceholder: "videotausta, teemalaulu", extraPlaceholder: "Lisämuistiinpanot",
        submit: "Luo musiikkia", jobsTitle: "Työt", jobsDesc: "Reaaliaikainen tila. Latauspainike ilmestyy, kun MP3 on valmis.",
        clearDraft: "Poista luonnos", clearDraftConfirm: "Poista nykyinen luonnos? Tämä ei poista luotua musiikkia.",
        draftSaved: "Luonnos tallennettu", draftRestored: "Edellinen luonnos palautettu", draftCleared: "Luonnos poistettu", draftRestoreFailed: "Luonnosta ei voitu palauttaa palvelimelta.",
        empty: "Ei töitä vielä. Täytä lomake aloittaaksesi.", queued: "Jonossa", running: "Luodaan", completed: "Valmis", error: "Virhe", unknown: "Tuntematon",
        download: "Lataa MP3", delete: "Poista", sent: "Lähetetty", instrumentalMode: "Instrumentaali", vocalMode: "Laulu", deleteConfirm: "Poista tämä työ?", deleteFailed: "Poisto epäonnistui",
        navCreate: "Luo", navLibrary: "Kirjasto", navFavorites: "Suosikit", navHistory: "Historia", navPlaylists: "Soittolistat", playlistAll: "Kaikki laulut", playlistRecent: "Äskettäin soitettu",
        libraryDesc: "Kaikki luomasi laulut yhdessä paikassa.", favoritesDesc: "Suosikkilaulusi.", historyDesc: "Äskettäin luodut laulut.",
        toastMusicStarted: "Musiikin luonti alkanut!", toastMusicReady: "Musiikki valmis: ", toastLyricsSuccess: "Sanoitukset luotu onnistuneesti!", toastLyricsError: "Sanoitusten luonti epäonnistui.", toastVoiceCloneSuccess: "Äänen kloonaus onnistui!", toastVoiceCloneError: "Äänen kloonaus epäonnistui.",
        langMenuLabel: "Käyttöliittymän kieli", langMismatchWarn: "⚠️ Sanoitusten kieli ei vastaa valitun äänen kieltä.", langMismatchTitle: "Kielten epä-täsmääminen"
      },
      cs: {
        subtitle: "Když slova nestačí, nechť za vás mluví hudba.",
        createTitle: "Vytvořit hudbu", createDesc: "Napište pocit, příběh, text nebo styl. Music Speaks to promění v píseň.",
        emailLabel: "E-mailová adresa (volitelné)", emailHint: "Volitelné. Stažení je hlavní způsob, jak získat váš MP3 soubor.",
        emailPlaceholder: "your@email.com",
        titleLabel: "Název písně (volitelné)", titleHint: "Pokud necháte prázdné, Music Speaks vytvoří název z textu.",
        titlePlaceholder: "Nechte prázdné a AI pojmenuje píseň",
        promptLabel: "Popis hudebního stylu", promptHint: "Zahrňte styl, náladu, nástroje, tempo a reference.",
        promptPlaceholder: "Světlý a sebevědomý elektronický pop, propracovaná produkce, nezapomenutelný hook",
        lyricsIdeaLabel: "Instrukce k textu (volitelné)", lyricsIdeaHint: "Popište, co chcete, nebo dejte AI instrukce. Jazyk vybraného hlasu se automaticky aplikuje.",
        lyricsExtraLabel: "Dodatečné požadavky (volitelné)", lyricsExtraHint: "Popište dodatečné požadavky: délka, emocionální tón, styl, nálada, tempo, struktura.",
        lyricsExtraPlaceholder: "Délka: 3-5 min / Emoce: smutná, optimistická / Styl: poetický / Nálada: temná",
        lyricsIdeaPlaceholder: "Napište příběh, pocity, obrazy nebo útržky, které chcete.",
        generateLyrics: "Generovat text", generatingLyrics: "Generování textu...", lyricsGenerated: "Text přidán níže. Můžete ho upravit před vytvořením hudby.",
        lyricsAssistNeedBrief: "Nejprve přidejte popis nebo hudební styl.", lyricsAssistFailed: "Generování textu selhalo.",
        lyricsLabel: "Plný text (volitelné)", lyricsHint: "Vložte sem plný text, pokud ho máte. Plný text má přednost.",
        lyricsPlaceholder: "[ sloka]\nVáš text zde...\n[ refrén]\nVáš refrén zde...",
        instrumental: "Instrumentální", instrumentalHint: "Bez vokálu. Text bude ignorován.",
        autoLyrics: "Automaticky generovat text", autoLyricsHint: "AI napíše text na základě vašeho popisu.",
        voiceCloneLabel: "Klonování hlasu (volitelné)", voiceRecordBtn: "Nahrát můj hlas", voiceCloneHint: "Nahrajte 5 krátkých vět v různých tónech. Trvá to přibližně 30 sekund. Klonovaný hlas vyprší za 7 dní.",
        voicePreviewBtn: "Náhled hlasu", voiceUploading: "Klonování vašeho hlasu...", voiceReady: "Hlas naklonován! Klikněte na Náhled pro poslech.",
        voiceError: "Klonování hlasu selhalo.", voicePreviewGenerating: "Vytváření náhledu...", voicePreviewReady: "Náhled připraven.", voicePreviewError: "Náhled selhal.",
        voiceSingingMode: "Režim syntézy zpěvu", voiceSingingModeHint: "Nejprve vyzkoušejte MiniMax voice_clone_singing. Pokud není dostupný, použijte voice cover.",
        voicePickerLabel: "Styl hlasu", voicePickerDefault: "Klikněte pro výběr - toto nastaví jazyk textu", voicePickerLoading: "Načítání hlasů...", voiceShowMore: "Zobrazit {count} dalších",
        voicePreviewSample: "Poslechněte si tento hlasový vzorek",
        voiceCustomBtn: "Můj hlas", voiceCustomDesc: "Nahrajte a používejte svůj vlastní hlas",
        recModalTitle: "Nahrát svůj hlas",
        templates: "Šablony stylů",
        advanced: "Dodatečné parametry", genre: "Žánr", mood: "Nálada", instruments: "Nástroje", tempo: "Tempo", bpm: "BPM", key: "Tónina",
        vocals: "Vokální styl", structure: "Struktura písně", references: "Reference", avoid: "Vyhnout se", useCase: "Případ užití", extra: "Dodatečné detaily",
        genrePlaceholder: "pop, reggae, jazz", moodPlaceholder: "teplý, jasný, intenzivní", instrumentsPlaceholder: "piano, kytara, bicí",
        tempoPlaceholder: "rychlé, pomalé, střední", bpmPlaceholder: "85", keyPlaceholder: "C dur, A moll",
        vocalsPlaceholder: "teplý mužský vokál, jasný ženský vokál", structurePlaceholder: "sloka-refrén-sloka-most-refrén",
        referencesPlaceholder: "podobné jako...", avoidPlaceholder: "explicitní obsah, nadměrný auto-tune",
        useCasePlaceholder: "video pozadí, tematická píseň", extraPlaceholder: "Dodatečné poznámky",
        submit: "Vytvořit hudbu", jobsTitle: "Úkoly", jobsDesc: "Stav v reálném čase. Tlačítko stažení se objeví, když je MP3 připraveno.",
        clearDraft: "Smazat koncept", clearDraftConfirm: "Smazat aktuální koncept? Toto neodstraní vytvořenou hudbu.",
        draftSaved: "Koncept uložen", draftRestored: "Předchozí koncept obnoven", draftCleared: "Koncept smazán", draftRestoreFailed: "Koncept se nepodařilo obnovit ze serveru.",
        empty: "Zatím žádné úkoly. Vyplňte formulář pro začátek.", queued: "Ve frontě", running: "Vytváření", completed: "Hotovo", error: "Chyba", unknown: "Neznámý",
        download: "Stáhnout MP3", delete: "Smazat", sent: "Odesláno do", instrumentalMode: "Instrumentální", vocalMode: "Vokální", deleteConfirm: "Smazat tento úkol?", deleteFailed: "Smazání selhalo",
        navCreate: "Vytvořit", navLibrary: "Knihovna", navFavorites: "Oblíbené", navHistory: "Historie", navPlaylists: "Seznamy skladeb", playlistAll: "Všechny písně", playlistRecent: "Nedávno přehrávané",
        libraryDesc: "Všechny vaše vytvořené písně na jednom místě.", favoritesDesc: "Vaše oblíbené písně.", historyDesc: "Nedávno vytvořené písně.",
        toastMusicStarted: "Vytváření hudby zahájeno!", toastMusicReady: "Hudba připravena: ", toastLyricsSuccess: "Text úspěšně vytvořen!", toastLyricsError: "Vytvoření textu selhalo.", toastVoiceCloneSuccess: "Klonování hlasu úspěšné!", toastVoiceCloneError: "Klonování hlasu selhalo.",
        langMenuLabel: "Jazyk rozhraní", langMismatchWarn: "⚠️ Jazyk textu neodpovídá jazyku vybraného hlasu.", langMismatchTitle: "Neshoda jazyků"
      },
      ro: {
        subtitle: "Când cuvintele nu sunt suficiente, lăsați muzica să vorbească pentru dvs.",
        createTitle: "Creează muzică", createDesc: "Scrieți o emoție, poveste, versuri sau stil. Music Speaks le va transforma într-o melodie.",
        emailLabel: "Adresă de e-mail (opțional)", emailHint: "Opțional. Descărcarea este modalitatea principală de a obține fișierul MP3.",
        emailPlaceholder: "your@email.com",
        titleLabel: "Titlul melodiei (opțional)", titleHint: "Dacă îl lăsați gol, Music Speaks va crea un titlu din versuri.",
        titlePlaceholder: "Lăsați gol și AI va numi melodia",
        promptLabel: "Descriere stil muzical", promptHint: "Includeți stilul, dispoziția, instrumentele, tempo-ul și referințele.",
        promptPlaceholder: "Pop electronic luminos și încrezător, producție rafinată, hook memorabil",
        lyricsIdeaLabel: "Instrucțiuni versuri (opțional)", lyricsIdeaHint: "Descrieți ce doriți sau dați instrucțiuni AI. Limba vocii selectate se aplică automat.",
        lyricsExtraLabel: "Cerințe suplimentare (opțional)", lyricsExtraHint: "Descrieți cerințele suplimentare: durată, ton emoțional, stil, dispoziție, tempo, structură.",
        lyricsExtraPlaceholder: "Durată: 3-5 minute / Emoție: trist, plin de speranță / Stil: poetic / Dispoziție: întunecat",
        lyricsIdeaPlaceholder: "Scrieți povestea, sentimentele, imaginile sau fragmentele pe care le doriți.",
        generateLyrics: "Generează versuri", generatingLyrics: "Se generează versurile...", lyricsGenerated: "Versuri adăugate mai jos. Le puteți edita înainte de a genera muzica.",
        lyricsAssistNeedBrief: "Adăugați mai întâi o descriere sau stil muzical.", lyricsAssistFailed: "Generarea versurilor a eșuat.",
        lyricsLabel: "Versuri complete (opțional)", lyricsHint: "Lipiți versurile complete aici dacă le aveți. Versurile complete au prioritate.",
        lyricsPlaceholder: "[ strofă]\nVersurile tale aici...\n[ refren]\nRefrenul tău aici...",
        instrumental: "Instrumental", instrumentalHint: "Fără voce. Versurile vor fi ignorate.",
        autoLyrics: "Generare automată versuri", autoLyricsHint: "AI scrie versuri în funcție de descrierea ta.",
        voiceCloneLabel: "Clonare voce (opțional)", voiceRecordBtn: "Înregistrează-mi vocea", voiceCloneHint: "Înregistrați 5 propoziții scurte cu tonuri diferite. durează aproximativ 30 de secunde. Vocea clonată expiră în 7 zile.",
        voicePreviewBtn: "Previzualizare voce", voiceUploading: "Se clonează vocea ta...", voiceReady: "Voce clonată! Faceți clic pe Previzualizare pentru a asculta.",
        voiceError: "Clonarea vocii a eșuat.", voicePreviewGenerating: "Se creează previzualizarea...", voicePreviewReady: "Previzualizare pregătită.", voicePreviewError: "Previzualizare eșuată.",
        voiceSingingMode: "Mod sinteză cântare", voiceSingingModeHint: "Încercați mai întâi MiniMax voice_clone_singing. Dacă nu este disponibil, folosiți voice cover.",
        voicePickerLabel: "Stil voce", voicePickerDefault: "Faceți clic pentru a selecta - aceasta stabilește limba versurilor", voicePickerLoading: "Se încarcă vocile...", voiceShowMore: "Afișați {count} mai multe",
        voicePreviewSample: "Ascultați această mostră de voce",
        voiceCustomBtn: "Vocea mea", voiceCustomDesc: "Înregistrați și folosiți propria voce",
        recModalTitle: "Înregistrați vocea dvs.",
        templates: "Șabloane de stil",
        advanced: "Parametri suplimentari", genre: "Gen", mood: "Dispoziție", instruments: "Instrumente", tempo: "Tempo", bpm: "BPM", key: "Cheie",
        vocals: "Stil vocal", structure: "Structura melodiei", references: "Referințe", avoid: "Evitați", useCase: "Caz de utilizare", extra: "Detalii suplimentare",
        genrePlaceholder: "pop, reggae, jazz", moodPlaceholder: "căldură, strălucitor, intens", instrumentsPlaceholder: "pian, chitară, tobe",
        tempoPlaceholder: "rapid, lent, moderat", bpmPlaceholder: "85", keyPlaceholder: "Do major, La minor",
        vocalsPlaceholder: "vocal masculin călduros, vocal feminin strălucitor", structurePlaceholder: "strofă-refren-strofă-punte-refren",
        referencesPlaceholder: "similar cu...", avoidPlaceholder: "conținut explicit, auto-tune excesiv",
        useCasePlaceholder: "fundal video, melodie tematică", extraPlaceholder: "Note suplimentare",
        submit: "Creează muzică", jobsTitle: "Activități", jobsDesc: "Stare în timp real. Butonul de descărcare apare când MP3 este pregătit.",
        clearDraft: "Șterge ciornă", clearDraftConfirm: "Ștergeți ciorna actuală? Aceasta nu va șterge muzica generată.",
        draftSaved: "Ciornă salvată", draftRestored: "Ciorna anterioară restaurată", draftCleared: "Ciornă ștearsă", draftRestoreFailed: "Nu s-a putut restaura ciorna de pe server.",
        empty: "Nicio activitate încă. Completați formularul pentru a începe.", queued: "În coadă", running: "Se generează", completed: "Terminat", error: "Eroare", unknown: "Necunoscut",
        download: "Descarcă MP3", delete: "Șterge", sent: "Trimis la", instrumentalMode: "Instrumental", vocalMode: "Vocal", deleteConfirm: "Ștergeți această activitate?", deleteFailed: "Ștergerea a eșuat",
        navCreate: "Creează", navLibrary: "Bibliotecă", navFavorites: "Favorite", navHistory: "Istoric", navPlaylists: "Playlist-uri", playlistAll: "Toate melodiile", playlistRecent: "Redat recent",
        libraryDesc: "Toate melodiile generate într-un singur loc.", favoritesDesc: "Melodiile tale preferate.", historyDesc: "Melodii generate recent.",
        toastMusicStarted: "Generarea muzicii a început!", toastMusicReady: "Muzică pregătită: ", toastLyricsSuccess: "Versuri generate cu succes!", toastLyricsError: "Generarea versurilor a eșuat.", toastVoiceCloneSuccess: "Clonarea vocii cu succes!", toastVoiceCloneError: "Clonarea vocii a eșuat.",
        langMenuLabel: "Limba interfeței", langMismatchWarn: "⚠️ Limba versurilor nu se potrivește cu limba vocii selectate.", langMismatchTitle: "Nepotrivire de limbă"
      },
      hu: {
        subtitle: "Amikor a szavak nem elegendőek, hadd szóljon a zene helyetted.",
        createTitle: "Zene létrehozása", createDesc: "Írj egy érzést, történetet, dalszöveget vagy stílust. A Music Speaks dallá alakítja.",
        emailLabel: "E-mail cím (opcionális)", emailHint: "Opcionális. A letöltés a legfőbb módja az MP3 fájl fogadásának.",
        emailPlaceholder: "your@email.com",
        titleLabel: "Dal címe (opcionális)", titleHint: "Ha üresen hagyod, a Music Speaks a dalszövegből hozza létre a címet.",
        titlePlaceholder: "Hagyd üresen és az AI elnevezi a dalt",
        promptLabel: "Zenei stílus leírása", promptHint: "Add meg a stílust, hangulatot, hangszereket, tempót és referenciákat.",
        promptPlaceholder: "Világos és magabiztos elektronikus pop, kifinomult produkció, felejthetetlen hook",
        lyricsIdeaLabel: "Dalszöveg utasítások (opcionális)", lyricsIdeaHint: "Írd le mit szeretnél, vagy adj utasításokat az AI-nak. A kiválasztott hang nyelve automatikusan alkalmazásra kerül.",
        lyricsExtraLabel: "További követelmények (opcionális)", lyricsExtraHint: "Írd le a további követelményeket: hossz, érzelmi tónus, stílus, hangulat, tempo, struktúra.",
        lyricsExtraPlaceholder: "Hossz: 3-5 perc / Érzés: szomorú, reményteli / Stílus: költői / Hangulat: sötét",
        lyricsIdeaPlaceholder: "Írd meg a kívánt történetet, érzéseket, képeket vagy töredékeket.",
        generateLyrics: "Dalszöveg létrehozása", generatingLyrics: "Dalszöveg létrehozása...", lyricsGenerated: "Dalszöveg hozzáadva lent. Szerkesztheted, mielőtt létrehoznád a zenét.",
        lyricsAssistNeedBrief: "Először adj hozzá egy leírást vagy zenei stílust.", lyricsAssistFailed: "A dalszöveg létrehozása sikertelen.",
        lyricsLabel: "Teljes dalszöveg (opcionális)", lyricsHint: "Illeszd be a teljes dalszöveget ide, ha már megvan. A teljes dalszöveg élvez prioritást.",
        lyricsPlaceholder: "[ versszak]\nA te dalszöveged itt...\n[ kórus]\nA kórusod itt...",
        instrumental: "Instrumentális", instrumentalHint: "Vokál nélkül. A dalszöveg figyelmen kívül lesz hagyva.",
        autoLyrics: "Automatikus dalszöveg", autoLyricsHint: "Az AI a leírásod alapján ír dalszöveget.",
        voiceCloneLabel: "Hang klónozás (opcionális)", voiceRecordBtn: "Rögzítsd a hangom", voiceCloneHint: "Rögzíts 5 rövid mondatot különböző hangszínekkel. Ez kb. 30 másodpercet vesz igénybe. A klónozott hang 7 nap múlva lejár.",
        voicePreviewBtn: "Hang előnézet", voiceUploading: "Hangod klónozása...", voiceReady: "Hang klónozva! Kattints az Előnézetre a meghallgatáshoz.",
        voiceError: "A hang klónozása sikertelen.", voicePreviewGenerating: "Előnézet létrehozása...", voicePreviewReady: "Előnézet kész.", voicePreviewError: "Előnézet sikertelen.",
        voiceSingingMode: "Ének szintézis mód", voiceSingingModeHint: "Először próbáld a MiniMax voice_clone_singing-et. Ha nem elérhető, használd a voice cover-t.",
        voicePickerLabel: "Hang stílus", voicePickerDefault: "Kattints a kiválasztáshoz - ez beállítja a dalszöveg nyelvét", voicePickerLoading: "Hangok betöltése...", voiceShowMore: "Még {count} megjelenítése",
        voicePreviewSample: "Hallgasd meg ezt a hangmintát",
        voiceCustomBtn: "A hangom", voiceCustomDesc: "Rögzítsd és használd a saját hangod",
        recModalTitle: "Rögzítsd a hangod",
        templates: "Stílus sablonok",
        advanced: "További paraméterek", genre: "Műfaj", mood: "Hangulat", instruments: "Hangszerek", tempo: "Tempo", bpm: "BPM", key: "Hangnem",
        vocals: "Vokális stílus", structure: "Dal struktúra", references: "Referenciák", avoid: "Kerülendő", useCase: "Felhasználási eset", extra: "További részletek",
        genrePlaceholder: "pop, reggae, jazz", moodPlaceholder: "meleg, világos, intenzív", instrumentsPlaceholder: "zongora, gitár, dob",
        tempoPlaceholder: "gyors, lassú, közepes", bpmPlaceholder: "85", keyPlaceholder: "C-dúr, A-moll",
        vocalsPlaceholder: "meleg férfi vokál, világos női vokál", structurePlaceholder: "vers-kórus-vers-híd-kórus",
        referencesPlaceholder: "hasonló mint...", avoidPlaceholder: "explicit tartalom, túlzott auto-tune",
        useCasePlaceholder: "videó háttér, tematikus dal", extraPlaceholder: "További jegyzetek",
        submit: "Zene létrehozása", jobsTitle: "Feladatok", jobsDesc: "Valós idejű állapot. A letöltés gomb megjelenik, amikor az MP3 kész.",
        clearDraft: "Vázlat törlése", clearDraftConfirm: "Törlöd a jelenlegi vázlatot? Ez nem törli a létrehozott zenét.",
        draftSaved: "Vázlat mentve", draftRestored: "Előző vázlat visszaállítva", draftCleared: "Vázlat törölve", draftRestoreFailed: "A vázlat visszaállítása a szerverről sikertelen.",
        empty: "Még nincs feladat. Töltsd ki az űrlapot a kezdéshez.", queued: "Sorban", running: "Létrehozás", completed: "Kész", error: "Hiba", unknown: "Ismeretlen",
        download: "MP3 letöltése", delete: "Törlés", sent: "Elküldve", instrumentalMode: "Instrumentális", vocalMode: "Vokális", deleteConfirm: "Törlöd ezt a feladatot?", deleteFailed: "Törlés sikertelen",
        navCreate: "Létrehoz", navLibrary: "Könyvtár", navFavorites: "Kedvencek", navHistory: "Előzmények", navPlaylists: "Lejátszási listák", playlistAll: "Összes dal", playlistRecent: "Nemrég lejátszott",
        libraryDesc: "Az összes létrehozott dalod egy helyen.", favoritesDesc: "A kedvenc dalaid.", historyDesc: "Nemrég létrehozott dalok.",
        toastMusicStarted: "Zene létrehozása megkezdődött!", toastMusicReady: "Zene kész: ", toastLyricsSuccess: "Dalszöveg sikeresen létrehozva!", toastLyricsError: "Dalszöveg létrehozása sikertelen.", toastVoiceCloneSuccess: "Hang klónozás sikeres!", toastVoiceCloneError: "Hang klónozás sikertelen.",
        langMenuLabel: "Felület nyelve", langMismatchWarn: "⚠️ A dalszöveg nyelve nem egyezik a kiválasztott hang nyelvével.", langMismatchTitle: "Nyelv eltérés"
      },
      uk: {
        subtitle: "Коли слів недостатньо, нехай музика говорить замість вас.",
        createTitle: "Створити музику", createDesc: "Напишіть почуття, історію, слова або стиль. Music Speaks перетворить це на пісню.",
        emailLabel: "Електронна пошта (необов'язково)", emailHint: "Необов'язково. Завантаження - основний спосіб отримати ваш MP3.",
        emailPlaceholder: "your@email.com",
        titleLabel: "Назва пісні (необов'язково)", titleHint: "Якщо залишити порожнім, Music Speaks створить назву з тексту пісні.",
        titlePlaceholder: "Залиште порожнім і AI назве пісню",
        promptLabel: "Опис музичного стилю", promptHint: "Вкажіть стиль, настрій, інструменти, темп та посилання.",
        promptPlaceholder: "Яскравий і впевнений електронний поп, вишукана продукція, незабутній хук",
        lyricsIdeaLabel: "Інструкції для тексту (необов'язково)", lyricsIdeaHint: "Опишіть, що ви хочете, або дайте інструкції AI. Мова вибраного голосу застосовується автоматично.",
        lyricsExtraLabel: "Додаткові вимоги (необов'язково)", lyricsExtraHint: "Опишіть додаткові вимоги: тривалість, емоційний тон, стиль, настрій, темп, структуру.",
        lyricsExtraPlaceholder: "Тривалість: 3-5 хв / Емоція: сумна, сповнена надії / Стиль: поетичний / Настрій: темний",
        lyricsIdeaPlaceholder: "Напишіть бажану історію, почуття, образи або уривки.",
        generateLyrics: "Створити текст", generatingLyrics: "Створення тексту...", lyricsGenerated: "Текст додано нижче. Ви можете редагувати його перед створенням музики.",
        lyricsAssistNeedBrief: "Спочатку додайте опис або музичний стиль.", lyricsAssistFailed: "Створення тексту не вдалося.",
        lyricsLabel: "Повний текст (необов'язково)", lyricsHint: "Вставте повний текст сюди, якщо він у вас є. Повний текст має пріоритет.",
        lyricsPlaceholder: "[ куплет]\nВаш текст тут...\n[ приспів]\nВаш приспів тут...",
        instrumental: "Інструментальна", instrumentalHint: "Без вокалу. Текст буде ігноровано.",
        autoLyrics: "Автоматичне створення тексту", autoLyricsHint: "AI пише текст на основі вашого опису.",
        voiceCloneLabel: "Клонування голосу (необов'язково)", voiceRecordBtn: "Записати мій голос", voiceCloneHint: "Запишіть 5 коротких речень різними тонами. Це займає близько 30 секунд. Клонований голос спливає через 7 днів.",
        voicePreviewBtn: "Попередній перегляд голосу", voiceUploading: "Клонування вашого голосу...", voiceReady: "Голос клоновано! Натисніть Попередній перегляд, щоб послухати.",
        voiceError: "Клонування голосу не вдалося.", voicePreviewGenerating: "Створення попереднього перегляду...", voicePreviewReady: "Попередній перегляд готовий.", voicePreviewError: "Попередній перегляд не вдався.",
        voiceSingingMode: "Режим синтезу співу", voiceSingingModeHint: "Спробуйте спочатку MiniMax voice_clone_singing. Якщо недоступний, використайте voice cover.",
        voicePickerLabel: "Стиль голосу", voicePickerDefault: "Натисніть для вибору - це встановлює мову тексту", voicePickerLoading: "Завантаження голосів...", voiceShowMore: "Показати ще {count}",
        voicePreviewSample: "Прослухайте цей зразок голосу",
        voiceCustomBtn: "Мій голос", voiceCustomDesc: "Запишіть і використовуйте власний голос",
        recModalTitle: "Запишіть свій голос",
        templates: "Шаблони стилів",
        advanced: "Додаткові параметри", genre: "Жанр", mood: "Настрій", instruments: "Інструменти", tempo: "Темп", bpm: "BPM", key: "Тональність",
        vocals: "Вокальний стиль", structure: "Структура пісні", references: "Посилання", avoid: "Уникайте", useCase: "Випадок використання", extra: "Додаткові деталі",
        genrePlaceholder: "поп, реггі, джаз", moodPlaceholder: "теплий, яскравий, інтенсивний", instrumentsPlaceholder: "фортепіано, гітара, барабани",
        tempoPlaceholder: "швидкий, повільний, помірний", bpmPlaceholder: "85", keyPlaceholder: "До мажор, Ля мінор",
        vocalsPlaceholder: "теплий чоловічий вокал, яскравий жіночий вокал", structurePlaceholder: "куплет-приспів-куплет-місток-приспів",
        referencesPlaceholder: "схоже на...", avoidPlaceholder: "відвертий вміст, надмірний auto-tune",
        useCasePlaceholder: "відеофон, тематична пісня", extraPlaceholder: "Додаткові нотатки",
        submit: "Створити музику", jobsTitle: "Завдання", jobsDesc: "Стан у реальному часі. Кнопка завантаження з'являється, коли MP3 готовий.",
        clearDraft: "Очистити чернетку", clearDraftConfirm: "Очистити поточну чернетку? Це не видалить створену музику.",
        draftSaved: "Чернетку збережено", draftRestored: "Попередню чернетку відновлено", draftCleared: "Чернетку очищено", draftRestoreFailed: "Не вдалося відновити чернетку з сервера.",
        empty: "Завдань ще немає. Заповніть форму, щоб почати.", queued: "У черзі", running: "Створення", completed: "Готово", error: "Помилка", unknown: "Невідомо",
        download: "Завантажити MP3", delete: "Видалити", sent: "Надіслано до", instrumentalMode: "Інструментальна", vocalMode: "Вокальна", deleteConfirm: "Видалити це завдання?", deleteFailed: "Видалення не вдалося",
        navCreate: "Створити", navLibrary: "Бібліотека", navFavorites: "Обране", navHistory: "Історія", navPlaylists: "Списки відтворення", playlistAll: "Усі пісні", playlistRecent: "Нещодавно відтворене",
        libraryDesc: "Усі ваші створені пісні в одному місці.", favoritesDesc: "Ваші улюблені пісні.", historyDesc: "Нещодавно створені пісні.",
        toastMusicStarted: "Створення музики розпочато!", toastMusicReady: "Музика готова: ", toastLyricsSuccess: "Текст успішно створено!", toastLyricsError: "Створення тексту не вдалося.", toastVoiceCloneSuccess: "Клонування голосу успішне!", toastVoiceCloneError: "Клонування голосу не вдалося.",
        langMenuLabel: "Мова інтерфейсу", langMismatchWarn: "⚠️ Мова тексту не відповідає мові вибраного голосу.", langMismatchTitle: "Невідповідність мови"
      },
    };

    Object.assign(I18N, {
      fr: {
        subtitle: "Quand les mots ne suffisent plus, laissez la musique parler.",
        createTitle: "Créer de la musique", createDesc: "Écrivez une idée, une histoire ou un style. Music Speaks le transforme en chanson.",
        promptLabel: "Style musical", promptHint: "Décrivez le style, l'ambiance, les instruments et le tempo.",
        promptPlaceholder: "Pop électronique lumineuse, production moderne, refrain mémorable",
        lyricsIdeaLabel: "Idée de paroles", lyricsIdeaHint: "Décrivez les paroles souhaitées. La langue vient de la colonne des voix.",
        lyricsIdeaPlaceholder: "Décrivez l'histoire, les émotions ou les images à chanter.",
        generateLyrics: "Créer les paroles", generatingLyrics: "Création des paroles...", lyricsGenerated: "Paroles ajoutées ci-dessous.",
        lyricsAssistNeedBrief: "Ajoutez d'abord une idée ou un style musical.", lyricsAssistFailed: "La création des paroles a échoué.",
        lyricsLabel: "Paroles finales", lyricsHint: "Collez ici vos paroles si vous en avez déjà.",
        instrumental: "Instrumental", instrumentalHint: "Sans voix. Les paroles seront ignorées.",
        autoLyrics: "Paroles automatiques", autoLyricsHint: "L'IA écrit les paroles à partir de votre idée.",
        voicePickerLabel: "Voix", voicePickerDefault: "Choisissez une langue de voix - elle définit la langue des paroles", voicePickerLoading: "Chargement des voix...",
        voicePreviewBtn: "Écouter la voix", voicePreviewReady: "Aperçu prêt.", voicePreviewError: "Aperçu indisponible.",
        voiceCustomBtn: "Ma voix", voiceCustomDesc: "Enregistrer et utiliser votre voix",
        submit: "Créer la musique", clearDraft: "Effacer", jobsTitle: "Créations", empty: "Aucune création pour le moment.",
        queued: "En attente", running: "Création", completed: "Terminé", error: "Erreur", unknown: "Inconnu",
        download: "Télécharger MP3", delete: "Supprimer", deleteConfirm: "Supprimer cette création ?", deleteFailed: "Suppression impossible",
        navCreate: "Créer", navLibrary: "Bibliothèque", navFavorites: "Favoris", navHistory: "Historique",
        libraryDesc: "Toutes vos chansons générées.", favoritesDesc: "Vos chansons favorites.", historyDesc: "Chansons récentes.",
        toastMusicStarted: "Création musicale lancée !", toastMusicReady: "Musique prête : ", toastLyricsSuccess: "Paroles créées !", toastLyricsError: "Échec des paroles.",
        langMenuLabel: "Langue de l'interface"
      },
      de: {
        subtitle: "Wenn Worte nicht reichen, lass die Musik sprechen.",
        createTitle: "Musik erstellen", createDesc: "Schreibe eine Idee, Geschichte oder Stilrichtung. Music Speaks macht daraus einen Song.",
        promptLabel: "Musikstil", promptHint: "Beschreibe Stil, Stimmung, Instrumente und Tempo.",
        promptPlaceholder: "Heller elektronischer Pop, moderne Produktion, starker Refrain",
        lyricsIdeaLabel: "Songtext-Idee", lyricsIdeaHint: "Beschreibe den gewünschten Text. Die Sprache kommt aus der Stimmen-Spalte.",
        lyricsIdeaPlaceholder: "Beschreibe Geschichte, Gefühle oder Bilder für den Song.",
        generateLyrics: "Text erstellen", generatingLyrics: "Text wird erstellt...", lyricsGenerated: "Text wurde unten eingefügt.",
        lyricsAssistNeedBrief: "Füge zuerst eine Idee oder einen Musikstil hinzu.", lyricsAssistFailed: "Texterstellung fehlgeschlagen.",
        lyricsLabel: "Fertiger Text", lyricsHint: "Füge hier vorhandene Texte ein.",
        instrumental: "Instrumental", instrumentalHint: "Ohne Gesang. Texte werden ignoriert.",
        autoLyrics: "Automatischer Text", autoLyricsHint: "Die KI schreibt den Text aus deiner Idee.",
        voicePickerLabel: "Stimme", voicePickerDefault: "Stimmensprache wählen - sie bestimmt die Textsprache", voicePickerLoading: "Stimmen werden geladen...",
        voicePreviewBtn: "Stimme anhören", voicePreviewReady: "Vorschau bereit.", voicePreviewError: "Vorschau nicht verfügbar.",
        voiceCustomBtn: "Meine Stimme", voiceCustomDesc: "Eigene Stimme aufnehmen und verwenden",
        submit: "Musik erstellen", clearDraft: "Entwurf löschen", jobsTitle: "Aufgaben", empty: "Noch keine Aufgaben.",
        queued: "Wartet", running: "Erstellt", completed: "Fertig", error: "Fehler", unknown: "Unbekannt",
        download: "MP3 herunterladen", delete: "Löschen", deleteConfirm: "Diese Aufgabe löschen?", deleteFailed: "Löschen fehlgeschlagen",
        navCreate: "Erstellen", navLibrary: "Bibliothek", navFavorites: "Favoriten", navHistory: "Verlauf",
        libraryDesc: "Alle generierten Songs.", favoritesDesc: "Deine Lieblingssongs.", historyDesc: "Kürzlich generierte Songs.",
        toastMusicStarted: "Musikerstellung gestartet!", toastMusicReady: "Musik bereit: ", toastLyricsSuccess: "Text erstellt!", toastLyricsError: "Texterstellung fehlgeschlagen.",
        langMenuLabel: "Oberflächensprache"
      },
      pt: {
        subtitle: "Quando as palavras não bastam, deixe a música falar.",
        createTitle: "Criar música", createDesc: "Escreva uma ideia, história ou estilo. O Music Speaks transforma isso em canção.",
        promptLabel: "Estilo musical", promptHint: "Descreva estilo, clima, instrumentos e tempo.",
        promptPlaceholder: "Pop eletrônico brilhante, produção moderna, refrão marcante",
        lyricsIdeaLabel: "Ideia de letra", lyricsIdeaHint: "Descreva a letra desejada. O idioma vem da coluna de vozes.",
        lyricsIdeaPlaceholder: "Descreva história, emoções ou imagens para cantar.",
        generateLyrics: "Criar letra", generatingLyrics: "Criando letra...", lyricsGenerated: "Letra adicionada abaixo.",
        lyricsAssistNeedBrief: "Adicione primeiro uma ideia ou estilo musical.", lyricsAssistFailed: "Falha ao criar letra.",
        lyricsLabel: "Letra final", lyricsHint: "Cole aqui sua letra se já tiver uma.",
        instrumental: "Instrumental", instrumentalHint: "Sem voz. A letra será ignorada.",
        autoLyrics: "Letra automática", autoLyricsHint: "A IA escreve a letra a partir da sua ideia.",
        voicePickerLabel: "Voz", voicePickerDefault: "Escolha o idioma da voz - ele define o idioma da letra", voicePickerLoading: "Carregando vozes...",
        voicePreviewBtn: "Ouvir voz", voicePreviewReady: "Prévia pronta.", voicePreviewError: "Prévia indisponível.",
        voiceCustomBtn: "Minha voz", voiceCustomDesc: "Grave e use sua própria voz",
        submit: "Criar música", clearDraft: "Limpar rascunho", jobsTitle: "Tarefas", empty: "Nenhuma tarefa ainda.",
        queued: "Na fila", running: "Criando", completed: "Concluído", error: "Erro", unknown: "Desconhecido",
        download: "Baixar MP3", delete: "Excluir", deleteConfirm: "Excluir esta tarefa?", deleteFailed: "Falha ao excluir",
        navCreate: "Criar", navLibrary: "Biblioteca", navFavorites: "Favoritos", navHistory: "Histórico",
        libraryDesc: "Todas as suas músicas geradas.", favoritesDesc: "Suas músicas favoritas.", historyDesc: "Músicas recentes.",
        toastMusicStarted: "Criação musical iniciada!", toastMusicReady: "Música pronta: ", toastLyricsSuccess: "Letra criada!", toastLyricsError: "Falha ao criar letra.",
        langMenuLabel: "Idioma da interface"
      },
      it: {
        subtitle: "Quando le parole non bastano, lascia parlare la musica.",
        createTitle: "Crea musica", createDesc: "Scrivi un'idea, una storia o uno stile. Music Speaks lo trasforma in una canzone.",
        promptLabel: "Stile musicale", promptHint: "Descrivi stile, atmosfera, strumenti e tempo.",
        promptPlaceholder: "Pop elettronico luminoso, produzione moderna, ritornello forte",
        lyricsIdeaLabel: "Idea per il testo", lyricsIdeaHint: "Descrivi il testo desiderato. La lingua viene dalla colonna delle voci.",
        lyricsIdeaPlaceholder: "Descrivi storia, emozioni o immagini da cantare.",
        generateLyrics: "Crea testo", generatingLyrics: "Creazione testo...", lyricsGenerated: "Testo aggiunto qui sotto.",
        lyricsAssistNeedBrief: "Aggiungi prima un'idea o uno stile musicale.", lyricsAssistFailed: "Creazione testo non riuscita.",
        lyricsLabel: "Testo finale", lyricsHint: "Incolla qui il testo se lo hai già.",
        instrumental: "Strumentale", instrumentalHint: "Senza voce. Il testo verrà ignorato.",
        autoLyrics: "Testo automatico", autoLyricsHint: "L'IA scrive il testo dalla tua idea.",
        voicePickerLabel: "Voce", voicePickerDefault: "Scegli la lingua della voce - definisce la lingua del testo", voicePickerLoading: "Caricamento voci...",
        voicePreviewBtn: "Ascolta voce", voicePreviewReady: "Anteprima pronta.", voicePreviewError: "Anteprima non disponibile.",
        voiceCustomBtn: "La mia voce", voiceCustomDesc: "Registra e usa la tua voce",
        submit: "Crea musica", clearDraft: "Cancella bozza", jobsTitle: "Attività", empty: "Nessuna attività.",
        queued: "In coda", running: "Creazione", completed: "Fatto", error: "Errore", unknown: "Sconosciuto",
        download: "Scarica MP3", delete: "Elimina", deleteConfirm: "Eliminare questa attività?", deleteFailed: "Eliminazione non riuscita",
        navCreate: "Crea", navLibrary: "Libreria", navFavorites: "Preferiti", navHistory: "Cronologia",
        libraryDesc: "Tutte le canzoni generate.", favoritesDesc: "Le tue canzoni preferite.", historyDesc: "Canzoni recenti.",
        toastMusicStarted: "Creazione musica avviata!", toastMusicReady: "Musica pronta: ", toastLyricsSuccess: "Testo creato!", toastLyricsError: "Creazione testo non riuscita.",
        langMenuLabel: "Lingua interfaccia"
      },
      ru: {
        subtitle: "Когда слов не хватает, пусть говорит музыка.",
        createTitle: "Создать музыку", createDesc: "Напишите идею, историю или стиль. Music Speaks превратит это в песню.",
        promptLabel: "Музыкальный стиль", promptHint: "Опишите стиль, настроение, инструменты и темп.",
        promptPlaceholder: "Яркий электронный поп, современное звучание, запоминающийся припев",
        lyricsIdeaLabel: "Идея текста", lyricsIdeaHint: "Опишите желаемый текст. Язык задается колонкой голосов.",
        lyricsIdeaPlaceholder: "Опишите историю, эмоции или образы для песни.",
        generateLyrics: "Создать текст", generatingLyrics: "Создание текста...", lyricsGenerated: "Текст добавлен ниже.",
        lyricsAssistNeedBrief: "Сначала добавьте идею или музыкальный стиль.", lyricsAssistFailed: "Не удалось создать текст.",
        lyricsLabel: "Готовый текст", lyricsHint: "Вставьте сюда готовый текст, если он уже есть.",
        instrumental: "Инструментал", instrumentalHint: "Без вокала. Текст будет проигнорирован.",
        autoLyrics: "Авто-текст", autoLyricsHint: "ИИ пишет текст по вашей идее.",
        voicePickerLabel: "Голос", voicePickerDefault: "Выберите язык голоса - он задает язык текста", voicePickerLoading: "Загрузка голосов...",
        voicePreviewBtn: "Прослушать голос", voicePreviewReady: "Предпросмотр готов.", voicePreviewError: "Предпросмотр недоступен.",
        voiceCustomBtn: "Мой голос", voiceCustomDesc: "Записать и использовать свой голос",
        submit: "Создать музыку", clearDraft: "Очистить черновик", jobsTitle: "Задачи", empty: "Задач пока нет.",
        queued: "В очереди", running: "Создается", completed: "Готово", error: "Ошибка", unknown: "Неизвестно",
        download: "Скачать MP3", delete: "Удалить", deleteConfirm: "Удалить эту задачу?", deleteFailed: "Не удалось удалить",
        navCreate: "Создать", navLibrary: "Библиотека", navFavorites: "Избранное", navHistory: "История",
        libraryDesc: "Все созданные песни.", favoritesDesc: "Ваши любимые песни.", historyDesc: "Недавние песни.",
        toastMusicStarted: "Создание музыки началось!", toastMusicReady: "Музыка готова: ", toastLyricsSuccess: "Текст создан!", toastLyricsError: "Не удалось создать текст.",
        langMenuLabel: "Язык интерфейса"
      }
    });

    Object.assign(I18N, {
      en: { ...I18N.en, recPreparing: "Preparing...", recCancel: "Cancel", recSegment: "Segment", recStartingIn: "Starting in {seconds}...", recStartRecording: "Start Recording", recRecordingFailed: "Recording failed — no audio data captured. Please try again.", recTooSmall: "Recording too small — check microphone. Please try again.", recRecording: "Recording...", recRecordingCountdown: "Recording... {seconds}s", recMicDenied: "Microphone access denied. Please allow microphone access.", recRerecord: "Re-record", recNext: "Next →", recAllDone: "All recordings complete! Merging...", recUploadingCloning: "Uploading & cloning...", recCloneFailed: "Clone failed.", recVoiceReady: "Voice cloned! Use Preview to listen.", recCloneFailedPrefix: "Clone failed: ", recClose: "Close", recRerecordConfirm: "Re-record voice? This will create a new voice clone.", untitled: "Untitled", noLyrics: "No lyrics available.", fullscreenLyrics: "Fullscreen lyrics", closeFullscreenLyrics: "Close fullscreen lyrics", previous: "Previous", next: "Next", playPause: "Play/Pause", artistName: "Music Speaks", muteSounds: "Mute sounds", unmuteSounds: "Unmute sounds" },
      zh: { ...I18N.zh, recPreparing: "准备中...", recCancel: "取消", recSegment: "段落", recStartingIn: "{seconds}秒后开始...", recStartRecording: "开始录制", recRecordingFailed: "录音失败 — 未捕获到音频数据，请重试。", recTooSmall: "录音文件过小 — 请检查麦克风后重试。", recRecording: "录制中...", recRecordingCountdown: "录制中... {seconds}s", recMicDenied: "麦克风访问被拒绝，请允许麦克风权限。", recRerecord: "重新录制", recNext: "下一个 →", recAllDone: "全部录制完成！正在合并...", recUploadingCloning: "上传中并复刻声音...", recCloneFailed: "声音复刻失败。", recVoiceReady: "声音复刻完成！点击预览试听。", recCloneFailedPrefix: "复刻失败：", recClose: "关闭", recRerecordConfirm: "重新录制？这将创建新的声音复刻。", untitled: "未命名", noLyrics: "暂无歌词。", fullscreenLyrics: "全屏歌词", closeFullscreenLyrics: "关闭全屏歌词", previous: "上一首", next: "下一首", playPause: "播放/暂停", artistName: "Music Speaks", muteSounds: "静音", unmuteSounds: "取消静音" },
      yue: { ...I18N.yue, recPreparing: "準備中...", recCancel: "取消", recSegment: "段落", recStartingIn: "{seconds}秒後開始...", recStartRecording: "開始錄製", recRecordingFailed: "錄音失敗 — 未捕獲音訊資料，請重試。", recTooSmall: "錄音檔案太細 — 請檢查咪高峰後重試。", recRecording: "錄製中...", recRecordingCountdown: "錄製中... {seconds}s", recMicDenied: "咪高峰存取被拒，請允許權限。", recRerecord: "重新錄製", recNext: "下一個 →", recAllDone: "全部錄製完成！正在合併...", recUploadingCloning: "上載並復刻聲音中...", recCloneFailed: "聲音復刻失敗。", recVoiceReady: "聲音復刻完成！點擊預覽試聽。", recCloneFailedPrefix: "復刻失敗：", recClose: "關閉", recRerecordConfirm: "重新錄製？這會建立新的聲音復刻。", untitled: "未命名", noLyrics: "暫無歌詞。", fullscreenLyrics: "全屏歌詞", closeFullscreenLyrics: "關閉全屏歌詞", previous: "上一首", next: "下一首", playPause: "播放/暫停", artistName: "Music Speaks", muteSounds: "靜音", unmuteSounds: "取消靜音" },
      ko: { ...I18N.ko, recPreparing: "준비 중...", recCancel: "취소", recSegment: "구간", recStartingIn: "{seconds}초 후 시작...", recStartRecording: "녹음 시작", recRecordingFailed: "녹음 실패 — 오디오가 캡처되지 않았습니다. 다시 시도하세요.", recTooSmall: "녹음 파일이 너무 작습니다 — 마이크를 확인하고 다시 시도하세요.", recRecording: "녹음 중...", recRecordingCountdown: "녹음 중... {seconds}초", recMicDenied: "마이크 접근이 거부되었습니다. 권한을 허용하세요.", recRerecord: "다시 녹음", recNext: "다음 →", recAllDone: "모든 녹음 완료! 병합 중...", recUploadingCloning: "업로드 및 음성 복제 중...", recCloneFailed: "음성 복제 실패.", recVoiceReady: "음성 복제 완료! 미리듣기로 확인하세요.", recCloneFailedPrefix: "복제 실패: ", recClose: "닫기", recRerecordConfirm: "다시 녹음할까요? 새 음성 복제가 생성됩니다.", untitled: "제목 없음", muteSounds: "소리 끄기", unmuteSounds: "소리 켜기" },
      ja: { ...I18N.ja, recPreparing: "準備中...", recCancel: "キャンセル", recSegment: "セグメント", recStartingIn: "{seconds}秒後に開始...", recStartRecording: "録音開始", recRecordingFailed: "録音に失敗しました — 音声データが取得できません。もう一度お試しください。", recTooSmall: "録音が短すぎます — マイクを確認してもう一度お試しください。", recRecording: "録音中...", recRecordingCountdown: "録音中... {seconds}秒", recMicDenied: "マイクへのアクセスが拒否されました。権限を許可してください。", recRerecord: "再録音", recNext: "次へ →", recAllDone: "すべての録音が完了しました！結合中...", recUploadingCloning: "アップロードして音声を複製中...", recCloneFailed: "音声複製に失敗しました。", recVoiceReady: "音声複製完了！プレビューで確認してください。", recCloneFailedPrefix: "複製失敗: ", recClose: "閉じる", recRerecordConfirm: "再録音しますか？新しい音声複製が作成されます。", untitled: "無題", muteSounds: "ミュート", unmuteSounds: "ミュート解除" },
      es: { ...I18N.es, recPreparing: "Preparando...", recCancel: "Cancelar", recSegment: "Segmento", recStartingIn: "Empieza en {seconds}...", recStartRecording: "Iniciar grabación", recRecordingFailed: "La grabación falló: no se capturó audio. Inténtalo de nuevo.", recTooSmall: "La grabación es demasiado pequeña: revisa el micrófono e inténtalo de nuevo.", recRecording: "Grabando...", recRecordingCountdown: "Grabando... {seconds}s", recMicDenied: "Acceso al micrófono denegado. Permite el acceso.", recRerecord: "Volver a grabar", recNext: "Siguiente →", recAllDone: "¡Grabaciones completas! Mezclando...", recUploadingCloning: "Subiendo y clonando voz...", recCloneFailed: "Clonación de voz fallida.", recVoiceReady: "¡Voz clonada! Usa Vista previa para escuchar.", recCloneFailedPrefix: "Clonación fallida: ", recClose: "Cerrar", recRerecordConfirm: "¿Volver a grabar? Esto creará una nueva voz clonada.", untitled: "Sin título", muteSounds: "Silenciar sonidos", unmuteSounds: "Activar sonidos" },
      fr: { ...I18N.fr, recPreparing: "Préparation...", recCancel: "Annuler", recSegment: "Segment", recStartingIn: "Début dans {seconds}...", recStartRecording: "Démarrer l'enregistrement", recRecordingFailed: "Échec de l'enregistrement — aucun audio capturé. Réessayez.", recTooSmall: "Enregistrement trop court — vérifiez le micro et réessayez.", recRecording: "Enregistrement...", recRecordingCountdown: "Enregistrement... {seconds}s", recMicDenied: "Accès au micro refusé. Autorisez l'accès.", recRerecord: "Réenregistrer", recNext: "Suivant →", recAllDone: "Tous les enregistrements sont terminés ! Fusion...", recUploadingCloning: "Téléversement et clonage de voix...", recCloneFailed: "Échec du clonage vocal.", recVoiceReady: "Voix clonée ! Utilisez l’aperçu pour écouter.", recCloneFailedPrefix: "Échec du clonage : ", recClose: "Fermer", recRerecordConfirm: "Réenregistrer ? Cela créera une nouvelle voix clonée.", untitled: "Sans titre", muteSounds: "Couper les sons", unmuteSounds: "Activer les sons" },
      de: { ...I18N.de, recPreparing: "Vorbereitung...", recCancel: "Abbrechen", recSegment: "Segment", recStartingIn: "Start in {seconds}...", recStartRecording: "Aufnahme starten", recRecordingFailed: "Aufnahme fehlgeschlagen — keine Audiodaten erfasst. Bitte erneut versuchen.", recTooSmall: "Aufnahme zu klein — Mikrofon prüfen und erneut versuchen.", recRecording: "Aufnahme läuft...", recRecordingCountdown: "Aufnahme läuft... {seconds}s", recMicDenied: "Mikrofonzugriff verweigert. Bitte Zugriff erlauben.", recRerecord: "Neu aufnehmen", recNext: "Weiter →", recAllDone: "Alle Aufnahmen fertig! Wird zusammengeführt...", recUploadingCloning: "Hochladen und Stimme klonen...", recCloneFailed: "Stimmenklon fehlgeschlagen.", recVoiceReady: "Stimme geklont! Vorschau zum Anhören nutzen.", recCloneFailedPrefix: "Klonen fehlgeschlagen: ", recClose: "Schließen", recRerecordConfirm: "Neu aufnehmen? Dadurch wird ein neuer Stimmenklon erstellt.", untitled: "Ohne Titel", muteSounds: "Töne stummschalten", unmuteSounds: "Töne aktivieren" },
      ru: { ...I18N.ru, recPreparing: "Подготовка...", recCancel: "Отмена", recSegment: "Сегмент", recStartingIn: "Начало через {seconds}...", recStartRecording: "Начать запись", recRecordingFailed: "Запись не удалась — аудио не получено. Попробуйте снова.", recTooSmall: "Запись слишком короткая — проверьте микрофон и попробуйте снова.", recRecording: "Запись...", recRecordingCountdown: "Запись... {seconds}с", recMicDenied: "Доступ к микрофону запрещен. Разрешите доступ.", recRerecord: "Записать заново", recNext: "Далее →", recAllDone: "Все записи готовы! Объединение...", recUploadingCloning: "Загрузка и клонирование голоса...", recCloneFailed: "Клонирование голоса не удалось.", recVoiceReady: "Голос клонирован! Нажмите предпросмотр.", recCloneFailedPrefix: "Ошибка клонирования: ", recClose: "Закрыть", recRerecordConfirm: "Записать заново? Будет создан новый клон голоса.", untitled: "Без названия", muteSounds: "Выключить звуки", unmuteSounds: "Включить звуки" },
    });


    Object.assign(I18N, {
      en: { ...I18N.en, play: "Play", pause: "Pause" }, zh: { ...I18N.zh, play: "播放", pause: "暂停" }, yue: { ...I18N.yue, play: "播放", pause: "暫停" }, ko: { ...I18N.ko, play: "재생" }, ja: { ...I18N.ja, play: "再生" }, es: { ...I18N.es, play: "Reproducir" }, fr: { ...I18N.fr, play: "Lire" }, de: { ...I18N.de, play: "Abspielen" }, pt: { ...I18N.pt, play: "Reproduzir" }, it: { ...I18N.it, play: "Riproduci" }, ru: { ...I18N.ru, play: "Воспроизвести" }, ar: { ...I18N.ar, play: "تشغيل" }, hi: { ...I18N.hi, play: "चलाएँ" }, id: { ...I18N.id, play: "Putar" }, vi: { ...I18N.vi, play: "Phát" }, th: { ...I18N.th, play: "เล่น" }, tr: { ...I18N.tr, play: "Oynat" }, pl: { ...I18N.pl, play: "Odtwórz" }, nl: { ...I18N.nl, play: "Afspelen" }, sv: { ...I18N.sv, play: "Spela" }, no: { ...I18N.no, play: "Spill" }, da: { ...I18N.da, play: "Afspil" }, fi: { ...I18N.fi, play: "Toista" }, cs: { ...I18N.cs, play: "Přehrát" }, ro: { ...I18N.ro, play: "Redă" }, hu: { ...I18N.hu, play: "Lejátszás" }, uk: { ...I18N.uk, play: "Відтворити" }
    });
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
    let _lyricsLanguage = "auto"; // "auto" = match voice language, or an explicit IETF language tag
    let lastJobs = [];
    let _cachedVoices = null;
    let _cachedVoiceMeta = {};
    let _voiceCatalogState = { fallback: false, cached: false, stale: false };
    let _voiceAudio = null;
    let _voicePlayPending = null;
    let _voicePreviewUtterance = null;
    let _selectedVoiceId = "";
    let _activeVoiceLang = "Chinese (Mandarin)";
    let _voiceSearchQuery = "";
    let _voiceStyleFilter = "all";
    // Set default prompt value if empty
    const promptEl = document.getElementById("prompt");
    if (!promptEl.value.trim()) {
      promptEl.value = "Upbeat pop song with catchy melody, bright synthesizer, driving drum beat";
    }
    const jobsBox = document.getElementById("jobs");
    const libraryBox = document.getElementById("library-list");
    const favoritesBox = document.getElementById("favorites-list");
    const historyBox = document.getElementById("history-list");
    let currentView = "create";
    const form = document.getElementById("jobForm");
    const submitBtn = document.getElementById("submitBtn");
    let submitBtnOriginalText = submitBtn.textContent;
    const clearDraftBtn = document.getElementById("clearDraftBtn");
    const formError = document.getElementById("formError");
    const draftStatus = document.getElementById("draftStatus");
    const instrumental = document.getElementById("instrumental");
    const lyricsOptimizer = document.getElementById("lyricsOptimizer");
    const voiceSingingMode = document.getElementById("voiceSingingMode");
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

    function t(key, vars = {}) {
      let text = (I18N[lang] && I18N[lang][key]) || (I18N.en && I18N.en[key]) || key;
      for (const [name, value] of Object.entries(vars || {})) {
        text = String(text).replaceAll(`{${name}}`, String(value));
      }
      return text;
    }
    function headers(extra = {}) { return {"X-Client-Id": clientId, ...extra}; }
    function escapeHtml(value) {
      return String(value ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#039;");
    }
    function iconUse(name) {
      return `<svg class="ui-icon" aria-hidden="true"><use href="#icon-${name}"></use></svg>`;
    }
    const UI_ICONS = {
      play: iconUse("play"),
      pause: iconUse("pause"),
      music: iconUse("music"),
      check: iconUse("check"),
      error: iconUse("x"),
      volume: iconUse("volume"),
      muted: iconUse("volume-off"),
      moon: iconUse("moon"),
      sun: iconUse("sun"),
      microphone: iconUse("microphone"),
      mic: iconUse("microphone"),
      refresh: iconUse("refresh"),
    };
    function statusIcon(status) {
      if (status === "completed") return UI_ICONS.check;
      if (status === "error") return UI_ICONS.error;
      return UI_ICONS.music;
    }
    function applyLang() {
      document.documentElement.lang = lang;
      const label = LANG_LABELS[lang] || lang;
      document.getElementById("langBtn").textContent = label + " ▾";
      document.querySelectorAll("[data-i18n]").forEach(el => { el.textContent = t(el.dataset.i18n); });
      document.querySelectorAll("[data-i18n-placeholder]").forEach(el => { el.placeholder = t(el.dataset.i18nPlaceholder); });
      submitBtnOriginalText = submitBtn.textContent; // Update original text when language changes
      renderJobs(lastJobs);
      if (_cachedVoices) _buildVoicePicker();
      const voicePickerSelected = document.getElementById("voicePickerSelected");
      if (voicePickerSelected && _selectedVoiceId) {
        voicePickerSelected.textContent = _selectedVoiceLabel(_selectedVoiceId);
        voicePickerSelected.style.color = "var(--accent)";
      }
    }
    function statusLabel(status) {
      return status === "completed" ? t("completed") : status === "running" ? t("running") : status === "queued" ? t("queued") : status === "error" ? t("error") : t("unknown");
    }
    function formatDate(value) {
      const date = new Date(value);
      if (Number.isNaN(date.getTime())) return "";
      const dateLocales = {en:"en-GB", zh:"zh-CN", yue:"zh-HK", ko:"ko-KR", ja:"ja-JP", es:"es-ES", fr:"fr-FR", de:"de-DE", pt:"pt-PT", it:"it-IT", ru:"ru-RU", ar:"ar-SA", hi:"hi-IN", id:"id-ID", vi:"vi-VN", th:"th-TH", tr:"tr-TR", pl:"pl-PL", nl:"nl-NL", sv:"sv-SE", no:"nb-NO", da:"da-DK", fi:"fi-FI", cs:"cs-CZ", ro:"ro-RO", hu:"hu-HU", uk:"uk-UA"};
      return date.toLocaleString(dateLocales[lang] || "en-GB", {month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit"});
    }
    function _jobsForView(view, jobs) {
      const list = jobs || [];
      if (view === "favorites") return list.filter(job => job.favorite || job.is_favorite);
      if (view === "history") return list.slice(0, 12);
      return list;
    }
    function _emptyJobsMessage(view) {
      if (view === "favorites") return t("favoritesDesc");
      if (view === "history") return t("historyDesc");
      if (view === "library") return t("libraryDesc");
      return t("empty");
    }
    function _renderJobList(box, jobs, view) {
      if (!box) return;
      const viewJobs = _jobsForView(view, jobs);
      if (!viewJobs.length) {
        box.innerHTML = `<div class="job-empty">${escapeHtml(_emptyJobsMessage(view))}</div>`;
        return;
      }
      box.innerHTML = viewJobs.map((job, idx) => _renderJobCard(job, idx)).join("");
    }
    function _renderJobCard(job, idx) {
      const status = escapeHtml(job.status || "unknown");
      const fileName = escapeHtml(job.file_name || "terry-music.mp3");
      const title = escapeHtml(job.song_title || job.prompt || t("untitled"));
      const mode = job.is_instrumental ? t("instrumentalMode") : t("vocalMode");
      const downloadUrl = job.download_url ? `${escapeHtml(job.download_url)}?client_id=${encodeURIComponent(clientId)}` : "";
      const isRunning = job.status === "running" || job.status === "queued";
      const completedClass = job.status === "completed" ? "animate-bounce-in" : "";
      const deleteAction = !isRunning ? `<button class="job-action-btn" onclick="deleteJob('${escapeHtml(job.id)}')">${UI_ICONS.error}<span>${t("delete")}</span></button>` : "";
      const actions = job.status === "completed" && job.download_url
        ? `<button class="job-action-btn download" onclick="playJob('${escapeHtml(job.id)}')">${UI_ICONS.play}<span>${t("play")}</span></button><a class="job-action-btn download" href="${downloadUrl}" download="${fileName}">${t("download")}</a>${deleteAction}`
        : isRunning ? `<span style="font-size:12px;color:var(--text-muted);"><span class="spinner" style="width:12px;height:12px;border-width:1.5px;"></span> ${statusLabel(status)}...</span>` : deleteAction;
      const statusClass = ["queued", "running", "completed", "error"].includes(job.status) ? `status-${job.status}` : "status-unknown";
      return `<div class="job-card ${statusClass} ${completedClass}" data-job-id="${escapeHtml(job.id)}" style="animation-delay:${idx * 50}ms">
        <div class="job-art">${statusIcon(job.status)}</div>
        <div class="job-info">
          <div class="job-title">${title}</div>
          <div class="job-meta"><span class="job-badge ${status}">${statusLabel(status)}</span><span>${mode}</span><span>${formatDate(job.created_at)}</span></div>
        </div>
        <div class="job-actions">${actions}</div>
      </div>`;
    }
    function renderJobs(jobs) {
      lastJobs = jobs || [];
      _renderJobList(jobsBox, lastJobs, "create");
      _renderJobList(libraryBox, lastJobs, "library");
      _renderJobList(favoritesBox, lastJobs, "favorites");
      _renderJobList(historyBox, lastJobs, "history");
    }
    function playJob(id) {
      const job = lastJobs.find(j => j.id === id);
      if (!job || !job.download_url) return;
      const url = job.download_url + (job.download_url.includes('?') ? '&' : '?') + 'client_id=' + encodeURIComponent(clientId);
      const lyrics = job.lyrics || "";
      currentTrack = {
        id: job.id,
        title: job.song_title || job.prompt || t("untitled"),
        url: url,
        lyrics: lyrics,
        lyric_timestamps: Array.isArray(job.lyric_timestamps) ? job.lyric_timestamps : [],
        lyric_timing_source: job.lyric_timing_source || "",
      };
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
        // Play completion sound and show toast when a job transitions to completed
        newJobs.forEach(job => {
          const prev = prevJobs[job.id];
          if (prev && prev.status !== "completed" && job.status === "completed") {
            SoundSystem.play("complete");
            showToast(t("toastMusicReady") + (job.song_title || job.prompt || t("untitled")), "success", 5000);
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
      // If the deleted job is currently playing, stop the player
      if (currentTrack && currentTrack.id === id) {
        audioPlayer.pause();
        audioPlayer.src = "";
        currentTrack = null;
        updatePlayerUI();
      }
      await loadJobs();
    }
    function collectPayload() {
      const get = id => { const el = document.getElementById(id); return el ? el.value.trim() : ""; };
      return {
        email: get("email"), song_title: get("songTitle"), prompt: get("prompt"), lyrics: get("lyrics"), lyrics_idea: get("lyricsIdea"), lyrics_extra: get("lyricsExtra"),
        is_instrumental: instrumental.checked, lyrics_optimizer: lyricsOptimizer.checked,
        voice_mode: voiceSingingMode && voiceSingingMode.checked ? "voice_clone_singing" : "cover",
        genre: get("genre"), mood: get("mood"), instruments: get("instruments"), tempo: get("tempo"), bpm: get("bpm"), key: get("key"),
        vocals: get("vocals"), structure: get("structure"), references: get("references"), avoid: get("avoid"), use_case: get("useCase"), extra: get("extra"),
        voice_id: clonedVoiceId || _selectedVoiceId || "",
        lyrics_language: _lyricsLanguage || (_activeVoiceLang && _activeVoiceLang !== "__other__" ? _activeVoiceLang : "") || "auto",
        interface_language: lang || "en",
      };
    }
    function restorePayload(payload = {}) {
      const set = (id, value) => { const el = document.getElementById(id); if (el) el.value = value || ""; };
      set("email", payload.email);
      set("songTitle", payload.song_title);
      set("prompt", payload.prompt);
      set("lyricsIdea", payload.lyrics_idea);
      set("lyricsExtra", payload.lyrics_extra);
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
      if (voiceSingingMode) voiceSingingMode.checked = payload.voice_mode !== "cover";
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
      }, 700);
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
        if (data.song_title && !document.getElementById("songTitle").value.trim()) {
          document.getElementById("songTitle").value = data.song_title;
        }
        saveDraftSoon();
        setLyricsAssistMessage(t("lyricsGenerated") + " (" + lyrics.value.length + "字)");
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
          headers: headers({"Content-Type": "application/json", "Accept": "audio/mpeg"}),
          body: JSON.stringify({lyrics: currentLyrics, voice_id: clonedVoiceId, prompt: document.getElementById("prompt").value.trim(), prefer_audio: true}),
        });
        if (!res.ok) {
          const data = await res.json().catch(() => ({}));
          const errMsg = typeof data.error === "string" ? data.error : data.error?.message || t("voicePreviewError");
          throw new Error(errMsg);
        }
        const blob = await res.blob();
        if (!blob.size) throw new Error(t("voicePreviewError"));
        const url = URL.createObjectURL(blob);
        if (voicePreviewAudio.src && voicePreviewAudio.src.startsWith("blob:")) URL.revokeObjectURL(voicePreviewAudio.src);
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
      const savedLang = localStorage.getItem("terry_music_voice_lang");
      if (savedLang) {
        _activeVoiceLang = savedLang;
        _lyricsLanguage = savedLang;
      }
    }
    // ── Language menu (desktop popover + mobile sheet) ───────────────
    const LANG_LABELS = {
      "en": "English", "zh": "中文", "yue": "粤语", "ko": "한국어",
      "ja": "日本語", "es": "Español", "fr": "Français", "de": "Deutsch",
      "pt": "Português", "it": "Italiano", "ru": "Русский", "ar": "العربية",
      "hi": "हिन्दी", "id": "Bahasa Indonesia", "vi": "Tiếng Việt",
      "th": "ไทย", "tr": "Türkçe", "pl": "Polski", "nl": "Nederlands",
      "sv": "Svenska", "no": "Norsk", "da": "Dansk", "fi": "Suomi",
      "cs": "Čeština", "ro": "Română", "hu": "Magyar", "uk": "Українська"
    };
    const ALL_INTERFACE_LANGS = ["en", "zh", "yue", "ko", "ja", "es", "fr", "de", "pt", "it", "ru", "ar", "hi", "id", "vi", "th", "tr", "pl", "nl", "sv", "no", "da", "fi", "cs", "ro", "hu", "uk"];

    // Map voice lang (from API) → IETF tag used in LANG_LABELS
    const VOICE_LANG_TO_IETF = {
      "English": "en", "Chinese (Mandarin)": "zh", "Cantonese": "yue",
      "Korean": "ko", "Japanese": "ja", "Spanish": "es", "French": "fr",
      "German": "de", "Portuguese": "pt", "Italian": "it", "Russian": "ru",
      "Arabic": "ar", "Hindi": "hi", "Indonesian": "id", "Vietnamese": "vi",
      "Thai": "th", "Turkish": "tr", "Polish": "pl", "Dutch": "nl",
      "Swedish": "sv", "Norwegian": "no", "Danish": "da", "Finnish": "fi",
      "Czech": "cs", "Romanian": "ro", "Hungarian": "hu", "Ukrainian": "uk"
    };

    let _langMenuOpen = false;

    function _getAvailableUIVoices() {
      const groups = _voiceGroupsFromCache();
      const langSet = new Set();
      const uniqueLangs = [];
      for (const g of groups) {
        if (!langSet.has(g.lang)) { langSet.add(g.lang); uniqueLangs.push(g.lang); }
      }
      return uniqueLangs;
    }

    function _langMenuUsesMobileLayout() {
      return window.matchMedia("(max-width: 768px)").matches;
    }

    function _buildLangMenu() {
      const menu = document.getElementById("langMenu");
      if (!menu) return;
      const voices = _getAvailableUIVoices();
      const current = lang;
      const voiceOnlyLangs = voices.filter(voiceLang => !VOICE_LANG_TO_IETF[voiceLang]);
      let html = '<div class="lang-menu-head">' + escapeHtml(t("langMenuLabel")) + '</div><div class="lang-menu-list">';
      for (const langCode of ALL_INTERFACE_LANGS) {
        const label = LANG_LABELS[langCode] || langCode;
        const isActive = langCode === current ? " active" : "";
        const check = langCode === current ? '<span class="lang-check">✓</span>' : "";
        html += '<div class="lang-menu-item' + isActive + '" data-lang="' + escapeHtml(langCode) + '" role="option">' + escapeHtml(label) + check + '</div>';
      }
      if (voiceOnlyLangs.length > 0) {
        html += '<div class="lang-menu-section-label">Voice Languages</div>';
        for (const voiceLang of voiceOnlyLangs) {
          html += '<div class="lang-menu-item" data-lang="voice:' + escapeHtml(voiceLang) + '" role="option">' + escapeHtml(voiceLang) + '</div>';
        }
      }
      html += '</div>';
      menu.innerHTML = html;
      menu.querySelectorAll(".lang-menu-item").forEach(item => {
        item.addEventListener("click", event => {
          event.preventDefault();
          event.stopPropagation();
          const value = item.getAttribute("data-lang") || "";
          if (!value) return;
          if (value.startsWith("voice:")) {
            const voiceLang = value.slice(6);
            lang = VOICE_LANG_TO_IETF[voiceLang] || "en";
          } else {
            lang = value;
          }
          applyLang();
          _closeLangMenu();
        });
      });
    }

    function _positionLangMenu() {
      const menu = document.getElementById("langMenu");
      const btn = document.getElementById("langBtn");
      if (!menu || !btn) return;
      menu.classList.remove("mobile");
      menu.style.left = "";
      menu.style.right = "";
      menu.style.top = "";
      menu.style.bottom = "";
      menu.style.width = "";
      if (_langMenuUsesMobileLayout()) {
        menu.classList.add("mobile");
        return;
      }
      const rect = btn.getBoundingClientRect();
      const width = Math.min(280, Math.max(220, Math.round(rect.width + 84)));
      const left = Math.min(window.innerWidth - width - 16, Math.max(16, rect.right - width));
      menu.style.width = width + "px";
      menu.style.left = Math.round(left) + "px";
      menu.style.top = Math.round(rect.bottom + 10) + "px";
    }

    function _handleLangMenuViewportChange() {
      if (_langMenuOpen) _positionLangMenu();
    }

    function _handleLangMenuKeydown(event) {
      if (event.key === "Escape") _closeLangMenu();
    }

    function _openLangMenu() {
      const menu = document.getElementById("langMenu");
      const btn = document.getElementById("langBtn");
      const backdrop = document.getElementById("langMenuBackdrop");
      if (!menu || !btn || !backdrop) return;
      _buildLangMenu();
      _positionLangMenu();
      menu.classList.add("open");
      menu.setAttribute("aria-hidden", "false");
      backdrop.classList.add("open");
      backdrop.setAttribute("aria-hidden", "false");
      btn.setAttribute("aria-expanded", "true");
      _langMenuOpen = true;
      window.addEventListener("resize", _handleLangMenuViewportChange);
      window.addEventListener("orientationchange", _handleLangMenuViewportChange);
      document.addEventListener("keydown", _handleLangMenuKeydown);
      const activeItem = menu.querySelector(".lang-menu-item.active");
      if (activeItem) activeItem.scrollIntoView({ block: "nearest" });
    }

    function _closeLangMenu() {
      const menu = document.getElementById("langMenu");
      const btn = document.getElementById("langBtn");
      const backdrop = document.getElementById("langMenuBackdrop");
      if (menu) {
        menu.classList.remove("open", "mobile");
        menu.setAttribute("aria-hidden", "true");
        menu.style.left = "";
        menu.style.right = "";
        menu.style.top = "";
        menu.style.bottom = "";
        menu.style.width = "";
      }
      if (btn) btn.setAttribute("aria-expanded", "false");
      if (backdrop) {
        backdrop.classList.remove("open");
        backdrop.setAttribute("aria-hidden", "true");
      }
      _langMenuOpen = false;
      window.removeEventListener("resize", _handleLangMenuViewportChange);
      window.removeEventListener("orientationchange", _handleLangMenuViewportChange);
      document.removeEventListener("keydown", _handleLangMenuKeydown);
    }

    document.getElementById("langBtn").addEventListener("click", event => {
      event.preventDefault();
      event.stopPropagation();
      if (_langMenuOpen) _closeLangMenu();
      else _openLangMenu();
    });
    document.getElementById("langMenuBackdrop").addEventListener("click", _closeLangMenu);

    // ── Lyrics language mismatch check ───────────────────────────────
    function _checkLyricsLanguageMismatch(voiceLang) {
      const lyricsEl = document.getElementById("lyrics");
      if (!lyricsEl || !lyricsEl.value.trim()) return; // No lyrics entered yet, no mismatch
      const lyricsText = lyricsEl.value.trim();

      // Ignore very short texts — they're likely boilerplate, not real lyrics
      if (lyricsText.length < 40) return;

      // Count script-specific characters
      const cjkChars = (lyricsText.match(/[\u4e00-\u9fff\u3400-\u4dbf]/g) || []).length;
      const hangulChars = (lyricsText.match(/[\uac00-\ud7af]/g) || []).length;
      const kanaChars = (lyricsText.match(/[\u3040-\u30ff]/g) || []).length;
      // Cantonese-specific characters
      const cantoneseChars = (lyricsText.match(/[睇喺嚟哋唔佢咁啲噶囖]/g) || []).length;

      // Determine detected lyrics language — require a meaningful count to avoid false positives
      let detected = "en";
      if (cantoneseChars >= 2) detected = "yue";
      else if (hangulChars >= 3) detected = "ko";
      else if (kanaChars >= 3) detected = "ja";
      else if (cjkChars >= 5) detected = "zh";

      // Compare with voice lang
      const voiceIetf = VOICE_LANG_TO_IETF[voiceLang] || voiceLang;
      // Special case: Cantonese voice + Mandarin lyrics is still a valid combination (both Chinese)
      const mismatch = detected !== voiceIetf && !(detected === "zh" && voiceLang === "Cantonese");
      if (mismatch) {
        showToast(t("langMismatchWarn"), "warning", 6000);
      }
    }

    // ── Auto-set lyrics language when voice changes ─────────────────
    // Hook into selectVoice — but we need to re-find the function. We'll
    // patch selectVoice to call _checkLyricsLanguageMismatch.
    // Since selectVoice is defined after this point, we intercept it via
    // the existing click handler on voice pills. The real hook is in
    // selectVoice itself. We add a small patch below after selectVoice is defined.
    const themeBtn = document.getElementById("themeBtn");
    function setTheme(theme) {
      document.documentElement.setAttribute("data-theme", theme);
      localStorage.setItem("terry_music_theme", theme);
      themeBtn.innerHTML = theme === "light" ? '<svg class="ui-icon"><use href="#icon-sun"></use></svg>' : '<svg class="ui-icon"><use href="#icon-moon"></use></svg>';
      themeBtn.setAttribute("aria-label", theme === "light" ? "Switch to dark theme" : "Switch to light theme");
    }
    const savedTheme = localStorage.getItem("terry_music_theme");
    if (savedTheme) setTheme(savedTheme);
    themeBtn.addEventListener("click", () => {
      SoundSystem.play("click");
      const current = document.documentElement.getAttribute("data-theme");
      setTheme(current === "light" ? "" : "light");
    });
    // Sound toggle
    function toggleSound() {
      const enabled = SoundSystem.toggle();
      const soundBtn = document.getElementById("soundBtn");
      soundBtn.innerHTML = enabled ? '<svg class="ui-icon"><use href="#icon-volume"></use></svg>' : '<svg class="ui-icon"><use href="#icon-volume-off"></use></svg>';
      soundBtn.setAttribute("aria-label", enabled ? t("muteSounds") : t("unmuteSounds"));
      soundBtn.className = "header-btn sound-toggle " + (enabled ? "on" : "off");
    }
    // Advanced panel toggle
    const advancedToggle = document.getElementById("advancedToggle");
    if (advancedToggle) {
      advancedToggle.addEventListener("click", (event) => {
        if (!event.defaultPrevented) window.toggleAdvancedPanel(event);
      });
    }
    // Navigation
    document.querySelectorAll(".nav-item").forEach(item => {
      item.addEventListener("click", (e) => {
        e.preventDefault();
        SoundSystem.play("click");
        const view = item.dataset.view;
        currentView = view || "create";
        document.querySelectorAll(".nav-item").forEach(n => n.classList.remove("active"));
        item.classList.add("active");
        document.querySelectorAll("[id^='view-']").forEach(v => v.style.display = "none");
        const viewEl = document.getElementById("view-" + view);
        if (viewEl) viewEl.style.display = "block";
        if (view === "library" || view === "favorites" || view === "history") {
          loadJobs();
          renderJobs(lastJobs);
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
    const volumeSlider = document.getElementById("volumeSlider");
    const volumeFill = document.getElementById("volumeFill");
    const lyricsText = document.getElementById("lyricsText");
    const lyricsToggle = document.getElementById("playerLyricsToggle");
    const lyricsPanel = document.getElementById("lyricsPanel");
    const lyricsPanelClose = document.getElementById("lyricsPanelClose");
    const lyricsLines = document.getElementById("lyricsLines");
    const ICON_PLAY = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5v14l11-7L8 5Z"/></svg>';
    const ICON_PAUSE = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 5h3.5v14H7V5Zm6.5 0H17v14h-3.5V5Z"/></svg>';
    let lastActiveLyricIndex = -1;

    function setPlayerPlayIcon() {
      const isPaused = audioPlayer.paused;
      playerPlay.innerHTML = isPaused ? ICON_PLAY : ICON_PAUSE;
      playerPlay.setAttribute("aria-label", isPaused ? t("play") : t("pause"));
    }
    function parseLyrics(rawLyrics) {
      return String(rawLyrics || "")
        .split(/\r?\n/)
        .map(line => line.trim())
        .filter(Boolean)
        .map((line, index) => {
          const isSection = /^\[[^\]]+\]$/.test(line);
          const text = isSection ? line : (line.replace(/^(?:\[(?:\d{2}:\d{2}(?:\.\d{1,3})?|[^\]]+)\]\s*)+/, "").trim() || line);
          return { index, text, isSection };
        });
    }
    function getLyricRows() {
      if (!currentTrack) return [];
      const source = currentTrack.lyrics || "";
      if (currentTrack._lyricsSource !== source) {
        currentTrack._lyricsSource = source;
        currentTrack._lyricsRows = parseLyrics(source);
        currentTrack._lyricsTimeline = null;
        currentTrack._lyricsTimelineKey = "";
      }
      return currentTrack._lyricsRows || [];
    }
    function closeLyricsPanel() {
      lyricsPanel.classList.remove("open");
      lyricsToggle.classList.remove("active");
      lyricsToggle.setAttribute("aria-expanded", "false");
    }
    function renderLyricsPanel() {
      const rows = getLyricRows();
      lastActiveLyricIndex = -1;
      if (!rows.length) {
        lyricsLines.innerHTML = '<div class="lyrics-empty">No lyrics available for this track.</div>';
        lyricsToggle.disabled = true;
        closeLyricsPanel();
        lyricsText.textContent = "No lyrics";
        lyricsText.className = "lyrics-text";
        return;
      }
      lyricsToggle.disabled = false;
      lyricsLines.innerHTML = rows.map(row => {
        const className = row.isSection ? "lyrics-line section" : "lyrics-line";
        return '<div class="' + className + '" data-lyric-index="' + row.index + '">' + escapeHtml(row.text) + '</div>';
      }).join("");
      updateLyricsProgress(true);
    }
    let _lyricTimeIndex = [];
    let _lyricTimeIndexKey = "";

    function _buildLyricTimeIndex(rows, duration) {
      const playableRows = rows.filter(row => !row.isSection && row.text);
      if (!playableRows.length || !duration) return [];
      const totalChars = playableRows.reduce((sum, row) => sum + Math.max(1, row.text.length), 0) || 1;
      let cursor = 0;
      return playableRows.map((row, idx) => {
        const weight = Math.max(1, row.text.length) / totalChars;
        const start = cursor * duration;
        cursor += weight;
        const end = idx === playableRows.length - 1 ? duration : cursor * duration;
        return { time: start, end, row_index: row.index, text: row.text, source: "browser-weighted" };
      });
    }

    function getLyricTimeline(rows) {
      if (!currentTrack) return [];
      const durationKey = Number.isFinite(audioPlayer.duration) ? Math.round(audioPlayer.duration * 1000) : 0;
      const rawKey = currentTrack.lyrics || "";
      const externalTimestamps = Array.isArray(currentTrack.lyric_timestamps) ? currentTrack.lyric_timestamps : [];
      const externalKey = externalTimestamps.length
        ? externalTimestamps.length + ":" + (externalTimestamps[0].time || 0) + ":" + (externalTimestamps[externalTimestamps.length - 1].time || 0) + ":" + (currentTrack.lyric_timing_source || "")
        : "none";
      const cacheKey = rawKey + "::" + externalKey + "::" + durationKey;
      if (currentTrack._lyricsTimelineKey === cacheKey && Array.isArray(currentTrack._lyricsTimeline)) {
        return currentTrack._lyricsTimeline;
      }

      const playableRows = rows.filter(row => !row.isSection && row.text);
      let timeline = externalTimestamps
        .map(entry => ({
          time: Number(entry && entry.time),
          end: Number(entry && entry.end),
          row_index: Number.isInteger(entry && entry.row_index) ? entry.row_index : null,
          text: String((entry && entry.text) || "").trim(),
          source: String((entry && entry.source) || currentTrack.lyric_timing_source || "timestamp"),
        }))
        .filter(entry => Number.isFinite(entry.time));

      if (!timeline.length) {
        timeline = _parseTimestamps(rawKey, rows);
      }
      if (!timeline.length && Number.isFinite(audioPlayer.duration) && audioPlayer.duration > 0) {
        const rowsKey = playableRows.map(row => row.index + ":" + row.text.length).join("|");
        const fallbackKey = (currentTrack.url || "") + "::" + rowsKey + "::" + durationKey;
        if (fallbackKey !== _lyricTimeIndexKey) {
          _lyricTimeIndexKey = fallbackKey;
          _lyricTimeIndex = _buildLyricTimeIndex(rows, audioPlayer.duration);
        }
        timeline = _lyricTimeIndex;
      }

      timeline = timeline
        .map((entry, idx) => {
          let rowIndex = Number.isInteger(entry.row_index) ? entry.row_index : null;
          if (rowIndex === null && entry.text) {
            const match = playableRows.find(row => row.text === entry.text);
            rowIndex = match ? match.index : null;
          }
          return {
            time: Math.max(0, Number(entry.time) || 0),
            end: Number(entry.end),
            row_index: rowIndex,
            text: entry.text || (playableRows[idx] ? playableRows[idx].text : ""),
            source: entry.source || "timestamp",
          };
        })
        .filter(entry => Number.isInteger(entry.row_index))
        .sort((a, b) => a.time - b.time);

      for (let i = 0; i < timeline.length; i++) {
        const next = timeline[i + 1];
        const fallbackEnd = next ? next.time : (Number.isFinite(audioPlayer.duration) ? audioPlayer.duration : timeline[i].time + 4);
        const explicitEnd = Number.isFinite(timeline[i].end) ? timeline[i].end : fallbackEnd;
        timeline[i].end = Math.max(timeline[i].time, explicitEnd, fallbackEnd);
      }

      currentTrack._lyricsTimelineKey = cacheKey;
      currentTrack._lyricsTimeline = timeline;
      return timeline;
    }

    function currentLyricRowIndex(rows) {
      const playableRows = rows.filter(row => !row.isSection && row.text);
      if (!playableRows.length) return rows[0] ? rows[0].index : -1;
      const timeline = getLyricTimeline(rows);
      if (!timeline.length) return playableRows[0].index;
      const t = audioPlayer.currentTime || 0;
      let lo = 0;
      let hi = timeline.length - 1;
      let result = timeline[0].row_index;
      while (lo <= hi) {
        const mid = (lo + hi) >> 1;
        if (timeline[mid].time <= t) {
          result = timeline[mid].row_index;
          lo = mid + 1;
        } else {
          hi = mid - 1;
        }
      }
      return result;
    }
    function updateLyricsProgress(forceScroll = false) {
      const rows = getLyricRows();
      if (!rows.length) return;
      const activeIndex = currentLyricRowIndex(rows);
      const activeRow = rows.find(row => row.index === activeIndex);
      lyricsText.textContent = activeRow ? activeRow.text : "Lyrics";
      lyricsText.className = audioPlayer.paused ? "lyrics-text" : "lyrics-text playing";
      lyricsLines.querySelectorAll(".lyrics-line").forEach(line => {
        line.classList.toggle("active", line.getAttribute("data-lyric-index") === String(activeIndex));
      });
      if ((forceScroll || activeIndex !== lastActiveLyricIndex) && lyricsPanel.classList.contains("open")) {
        const activeEl = lyricsLines.querySelector('[data-lyric-index="' + activeIndex + '"]');
        if (activeEl) activeEl.scrollIntoView({ block: "center", behavior: forceScroll ? "auto" : "smooth" });
      }
      lastActiveLyricIndex = activeIndex;
    }
    function updatePlayerUI() {
      if (!currentTrack) { player.style.display = "none"; return; }
      player.style.display = "flex";
      playerTitle.textContent = currentTrack.title;
      playerArtist.textContent = "Music Speaks";
      setPlayerPlayIcon();
      renderLyricsPanel();
    }
    audioPlayer.addEventListener("timeupdate", () => {
      if (!audioPlayer.duration) return;
      const pct = (audioPlayer.currentTime / audioPlayer.duration) * 100;
      playerBarFill.style.width = pct + "%";
      playerCurrentTime.textContent = formatTime(audioPlayer.currentTime);
      playerDuration.textContent = formatTime(audioPlayer.duration);
      updateLyricsProgress();
    });
    audioPlayer.addEventListener("loadedmetadata", () => {
      playerDuration.textContent = formatTime(audioPlayer.duration || 0);
      updateLyricsProgress(true);
    });
    audioPlayer.addEventListener("play", () => { setPlayerPlayIcon(); lyricsText.className = "lyrics-text playing"; });
    audioPlayer.addEventListener("pause", () => { setPlayerPlayIcon(); lyricsText.className = "lyrics-text"; });
    audioPlayer.addEventListener("ended", () => { setPlayerPlayIcon(); lyricsText.className = "lyrics-text"; });
    playerPlay.addEventListener("click", () => {
      if (audioPlayer.paused) audioPlayer.play(); else audioPlayer.pause();
      setPlayerPlayIcon();
      updateLyricsProgress();
    });
    lyricsToggle.addEventListener("click", () => {
      if (lyricsToggle.disabled) return;
      const isOpen = lyricsPanel.classList.toggle("open");
      lyricsToggle.classList.toggle("active", isOpen);
      lyricsToggle.setAttribute("aria-expanded", isOpen ? "true" : "false");
      if (isOpen) updateLyricsProgress(true);
    });
    lyricsPanelClose.addEventListener("click", closeLyricsPanel);
    playerBar.addEventListener("click", (e) => {
      if (!audioPlayer.duration) return;
      const rect = playerBar.getBoundingClientRect();
      const pct = (e.clientX - rect.left) / rect.width;
      audioPlayer.currentTime = pct * audioPlayer.duration;
      updateLyricsProgress(true);
    });
    volumeFill.style.width = "70%";
    audioPlayer.volume = 0.7;
    function setPlayerVolumeFromPointer(event) {
      if (!volumeSlider) return;
      const rect = volumeSlider.getBoundingClientRect();
      const pct = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width));
      audioPlayer.volume = pct;
      volumeFill.style.width = Math.round(pct * 100) + "%";
    }
    if (volumeSlider) {
      volumeSlider.addEventListener("click", setPlayerVolumeFromPointer);
      volumeSlider.addEventListener("pointerdown", event => {
        event.preventDefault();
        volumeSlider.setPointerCapture(event.pointerId);
        setPlayerVolumeFromPointer(event);
      });
      volumeSlider.addEventListener("pointermove", event => {
        if (event.buttons) setPlayerVolumeFromPointer(event);
      });
    }
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
      // Clear voice clone state — cloned voice should NOT persist after clearing draft
      clonedVoiceId = "";
      voiceCloneExpires = "";
      localStorage.removeItem("terry_music_voice_id");
      localStorage.removeItem("terry_music_voice_expires");
      localStorage.removeItem("terry_music_voice_wav");
      localStorage.removeItem("terry_music_voice_lang");
      _activeVoiceLang = "Chinese (Mandarin)";
      _lyricsLanguage = _activeVoiceLang;
      // Reset voice UI elements
      const voicePreviewRow = document.getElementById("voicePreviewRow");
      const voiceStatus = document.getElementById("voiceStatus");
      const voicePreviewBtn = document.getElementById("voicePreviewBtn");
      const voicePreviewAudio = document.getElementById("voicePreviewAudio");
      const voiceSingingMode = document.getElementById("voiceSingingMode");
      if (voicePreviewRow) voicePreviewRow.style.display = "none";
      if (voiceStatus) { voiceStatus.textContent = ""; voiceStatus.className = "voice-status"; }
      if (voicePreviewBtn) { voicePreviewBtn.disabled = false; voicePreviewBtn.textContent = t("voicePreviewBtn"); }
      if (voicePreviewAudio) { voicePreviewAudio.pause(); voicePreviewAudio.removeAttribute("src"); voicePreviewAudio.load(); }
      if (voiceSingingMode) voiceSingingMode.checked = false;
      // Clear selected system voice from voice picker
      _selectedVoiceId = "";
      _activeVoiceLang = "Chinese (Mandarin)";
      _lyricsLanguage = _activeVoiceLang;
      stopVoicePreview();
      const voicePickerSelected = document.getElementById("voicePickerSelected");
      if (voicePickerSelected) { voicePickerSelected.textContent = t("voicePickerDefault"); voicePickerSelected.style.color = ""; }
      const vocalsInput = document.getElementById("vocals");
      if (vocalsInput) vocalsInput.value = "";
      document.querySelectorAll(".voice-pill.selected").forEach(p => p.classList.remove("selected"));
      const customBtn = document.getElementById("voicePickerCustomBtn");
      if (customBtn) customBtn.classList.remove("active");
      if (_cachedVoices) _buildVoicePicker();
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
        const endpoint = (payload.voice_mode === "voice_clone_singing") && !payload.is_instrumental ? "/api/jobs/voice" : "/api/jobs";
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
        const res = await fetch(endpoint, {method: "POST", headers: headers({"Content-Type": "application/json"}), body: JSON.stringify(payload)});
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
    setInterval(loadJobs, 3000);
    // Play startup sound on first load (user interaction required for audio)
    document.addEventListener("click", function startupSound() {
      SoundSystem.play("startup");
      document.removeEventListener("click", startupSound);
    }, { once: true });

    // =============================================================
    // Voice Picker
    // =============================================================
    const VOICE_LANG_GROUPS = [
      { lang: "Chinese (Mandarin)", label: "普通话", voices: [] },
      { lang: "Cantonese", label: "粤语", voices: [] },
      { lang: "English", label: "English", voices: [] },
      { lang: "Korean", label: "한국어", voices: [] },
      { lang: "Japanese", label: "日本語", voices: [] },
      { lang: "Spanish", label: "Español", voices: [] },
      { lang: "Portuguese", label: "Português", voices: [] },
      { lang: "French", label: "Français", voices: [] },
      { lang: "German", label: "Deutsch", voices: [] },
      { lang: "Indonesian", label: "Bahasa Indonesia", voices: [] },
      { lang: "Russian", label: "Русский", voices: [] },
      { lang: "Italian", label: "Italiano", voices: [] },
      { lang: "Arabic", label: "العربية", voices: [] },
      { lang: "Turkish", label: "Türkçe", voices: [] },
      { lang: "Ukrainian", label: "Українська", voices: [] },
      { lang: "Dutch", label: "Nederlands", voices: [] },
      { lang: "Vietnamese", label: "Tiếng Việt", voices: [] },
    ];

    const VOICE_NAME_ZH = {
      "Reliable Executive": "可靠高管",
      "News Anchor": "新闻主播",
      "Mature Woman": "成熟女性",
      "Sweet Lady": "甜美女声",
      "Lyrical Voice": "抒情声音",
      "Professional Host Female": "专业主持女声",
      "Gentle Lady": "温柔女声",
      "Trustworthy Man": "可信男声",
      "Graceful Lady": "优雅女声",
      "Whispering Girl": "低语女孩",
      "Kind Lady": "亲切女声",
      "Calm Lady": "沉稳女声",
      "Sweet Girl": "甜美女孩",
      "Serene Woman": "宁静女声",
      "Narrator": "旁白",
      "Sentimental Lady": "感性女声",
      "Female News Anchor": "女新闻主播",
      "Male Narrator": "男旁白",
      "Friendly Man": "友好男声",
      "Reliable Man": "可靠男声",
      "Calm Woman": "沉稳女声",
    };

    const VOICE_NAME_YUE = {
      "Reliable Executive": "可靠高管",
      "News Anchor": "新聞主播",
      "Mature Woman": "成熟女性",
      "Sweet Lady": "甜美女士",
      "Lyrical Voice": "抒情聲音",
      "Professional Host Female": "專業主持女聲",
      "Gentle Lady": "溫柔女士",
      "Trustworthy Man": "可信男士",
      "Graceful Lady": "優雅女士",
      "Whispering Girl": "低語女孩",
      "Kind Lady": "親切女士",
      "Calm Lady": "沉穩女士",
      "Sweet Girl": "甜美女孩",
      "Serene Woman": "寧靜女性",
      "Narrator": "旁白",
      "Sentimental Lady": "感性女士",
      "Female News Anchor": "女新聞主播",
      "Male Narrator": "男旁白",
      "Friendly Man": "友好男士",
      "Reliable Man": "可靠男士",
      "Calm Woman": "沉穩女性",
    };

    const VOICE_NAME_KO = {
      "Reliable Executive": "믿음직한 임원",
      "News Anchor": "뉴스 앵커",
      "Mature Woman": "성숙한 여성",
      "Sweet Lady": "달콤한 숙녀",
      "Lyrical Voice": "서정적 목소리",
      "Professional Host Female": "프로 진행자 여성",
      "Gentle Lady": "부드러운 숙녀",
      "Trustworthy Man": "신뢰할 수 있는 남성",
      "Graceful Lady": "우아한 숙녀",
      "Whispering Girl": "속삭이는 소녀",
      "Kind Lady": "친절한 숙녀",
      "Calm Lady": "차분한 숙녀",
      "Sweet Girl": "달콤한 소녀",
      "Serene Woman": "평온한 여성",
      "Narrator": "내레이터",
      "Sentimental Lady": "감성적 숙녀",
      "Female News Anchor": "여성 뉴스 앵커",
      "Male Narrator": "남자 내레이터",
      "Friendly Man": "친근한 남성",
      "Reliable Man": "신뢰할 수 있는 남성",
      "Calm Woman": "차분한 여성",
    };

    const VOICE_NAME_JA = {
      "Reliable Executive": "信頼できる経営幹部",
      "News Anchor": "ニュースアンカー",
      "Mature Woman": "成熟した女性",
      "Sweet Lady": "甘いLady",
      "Lyrical Voice": "叙情的な声",
      "Professional Host Female": "プロ司会者女性",
      "Gentle Lady": "優しいLady",
      "Trustworthy Man": "信頼できる男性",
      "Graceful Lady": "優雅なLady",
      "Whispering Girl": "囁く少女",
      "Kind Lady": "親切なLady",
      "Calm Lady": "穏やかなLady",
      "Sweet Girl": "甘い少女",
      "Serene Woman": "穏やかな女性",
      "Narrator": "ナレーター",
      "Sentimental Lady": "感傷的なLady",
      "Female News Anchor": "女性ニュースアンカー",
      "Male Narrator": "男性ナレーター",
      "Friendly Man": "フレンドリーな男性",
      "Reliable Man": "信頼できる男性",
      "Calm Woman": "穏やかな女性",
    };

    function _t(key) { return t(key); }

    function _voiceCatalogStateLabel() {
      if (_voiceCatalogState.stale) return lang === "zh" ? "缓存目录" : lang === "yue" ? "快取目錄" : "Cached catalog";
      if (_voiceCatalogState.fallback) return lang === "zh" ? "内置目录" : lang === "yue" ? "內置目錄" : "Fallback catalog";
      if (_voiceCatalogState.cached) return lang === "zh" ? "已缓存" : lang === "yue" ? "已快取" : "Cached";
      return lang === "zh" ? "实时目录" : lang === "yue" ? "即時目錄" : "Live catalog";
    }

    function _voiceMetaForId(voiceId) {
      if (!voiceId || voiceId === "__custom__") return null;
      const cached = _cachedVoiceMeta && _cachedVoiceMeta[voiceId];
      if (cached && typeof cached === "object") return cached;
      const group = VOICE_LANG_GROUPS.find(function(item) {
        return voiceId.startsWith(item.lang + "_") || voiceId.startsWith(item.lang);
      });
      return {
        id: voiceId,
        language: group ? group.lang : "English",
        language_source: group ? "prefix" : "default",
        display_name: String(voiceId || ""),
        preview_supported: true,
        use_case: "General music / spoken demo",
        unavailable_reason: "",
        source: "client-fallback",
      };
    }

    function _voiceLanguageForId(voiceId) {
      const meta = _voiceMetaForId(voiceId);
      if (meta && meta.language_source && meta.language_source !== "default") return meta.language;
      for (const group of VOICE_LANG_GROUPS) {
        if (voiceId.startsWith(group.lang + "_") || voiceId.startsWith(group.lang)) return group.lang;
      }
      return "";
    }

    function _voiceSummaryText(voiceId, groupKey) {
      const meta = _voiceMetaForId(voiceId);
      if (!meta) return "";
      const parts = [];
      const language = meta.language || groupKey || _voiceLanguageForId(voiceId);
      if (language && language !== "__other__") parts.push(language);
      if (meta.use_case) parts.push(meta.use_case);
      if (meta.preview_supported === false && meta.unavailable_reason) parts.push(meta.unavailable_reason);
      return parts.filter(Boolean).join(" • ");
    }

    function _selectedVoiceLabel(voiceId) {
      if (!voiceId) return t("voicePickerDefault");
      if (voiceId === "__custom__") return _t("voiceCustomBtn");
      const group = _voiceGroupForId(voiceId);
      const label = _voiceDisplayName(voiceId, group ? group.lang : "");
      const summary = _voiceSummaryText(voiceId, group ? group.lang : "");
      return summary ? label + " · " + summary : label;
    }

    function _markVoicePreviewCapability(voiceId, previewSupported, unavailableReason = "") {
      if (!voiceId || voiceId === "__custom__") return;
      const existing = _voiceMetaForId(voiceId) || { id: voiceId };
      _cachedVoiceMeta[voiceId] = {
        ...existing,
        preview_supported: !!previewSupported && !unavailableReason,
        unavailable_reason: unavailableReason || "",
      };
    }

    function _isPreviewUnavailableError(message) {
      return /not available for preview|voice id not exist|voice is not available|preview unavailable/i.test(String(message || ""));
    }

    function _voiceGroupsFromCache() {
      const groups = VOICE_LANG_GROUPS.map(function(group) {
        return { lang: group.lang, label: group.label, voices: [] };
      });
      const otherVoices = [];
      for (const voice of (_cachedVoices || [])) {
        const detectedLang = _voiceLanguageForId(voice);
        const targetGroup = groups.find(function(group) { return group.lang === detectedLang; });
        if (targetGroup) targetGroup.voices.push(voice);
        else otherVoices.push(voice);
      }
      const visibleGroups = groups.filter(function(group) { return group.voices.length > 0; });
      if (otherVoices.length > 0) visibleGroups.push({ lang: "__other__", label: "Other", voices: otherVoices });
      return visibleGroups;
    }

    function _voiceGroupForId(voiceId, groups) {
      if (!voiceId) return null;
      for (const group of (groups || _voiceGroupsFromCache())) {
        if (group.voices.includes(voiceId)) return group;
      }
      return null;
    }

    function _voiceDisplayName(voice, groupKey) {
      const meta = _voiceMetaForId(voice);
      let name = meta && meta.display_name ? String(meta.display_name) : String(voice || "");
      if (groupKey && groupKey !== "__other__") {
        if (name.startsWith(groupKey + "_")) {
          name = name.slice(groupKey.length + 1);
        } else if (name.startsWith(groupKey)) {
          name = name.slice(groupKey.length).replace(/^[_\s-]+/, "");
        }
      }
      name = name
        .replace(/_/g, " ")
        .replace(/([a-z])([A-Z])/g, "$1 $2")
        .replace(/[（(]\s*F\s*[）)]/gi, " Female")
        .replace(/[（(]\s*M\s*[）)]/gi, " Male")
        .replace(/\s+/g, " ")
        .trim();
      name = name.replace(/\b[a-z]/g, function(ch) { return ch.toUpperCase(); });
      // Translate voice name based on UI language; English shows as-is
      if (lang === "zh") {
        const zhName = VOICE_NAME_ZH[name];
        if (zhName) return zhName;
      } else if (lang === "yue") {
        const yueName = VOICE_NAME_YUE[name];
        if (yueName) return yueName;
      } else if (lang === "ko") {
        const koName = VOICE_NAME_KO[name];
        if (koName) return koName;
      } else if (lang === "ja") {
        const jaName = VOICE_NAME_JA[name];
        if (jaName) return jaName;
      }
      return name || String(voice || "").replace(/_/g, " ");
    }

    function _attachScrollSound(element) {
      if (!element) return;
      let lastScrollSound = 0;
      let lastPosition = element.scrollTop + element.scrollLeft;
      element.addEventListener("scroll", function() {
        const now = Date.now();
        const position = element.scrollTop + element.scrollLeft;
        if (Math.abs(position - lastPosition) < 4) return;
        if (now - lastScrollSound > 90) {
          SoundSystem.play("click");
          lastScrollSound = now;
        }
        lastPosition = position;
      }, { passive: true });
    }

    function _buildVoicePicker() {
      const container = document.getElementById("voicePickerScroll");
      if (!container) return;
      if (!_cachedVoices || _cachedVoices.length === 0) {
        container.innerHTML = '<div style="padding:16px;text-align:center;color:var(--text-muted);font-size:12px;">No voices available. Check MiniMax configuration.</div>';
        return;
      }
      const groups = _voiceGroupsFromCache();
      if (!groups.some(function(group) { return group.lang === _activeVoiceLang; })) {
        _activeVoiceLang = groups[0] ? groups[0].lang : "";
      }
      _lyricsLanguage = (_activeVoiceLang && _activeVoiceLang !== "__other__") ? _activeVoiceLang : (_lyricsLanguage || "auto");
      const activeGroup = groups.find(function(group) { return group.lang === _activeVoiceLang; }) || groups[0];
      let html = '<div class="voice-picker-shell">';
      html += '<div class="voice-lang-list" id="voiceLangList" aria-label="Voice languages">';
      for (const group of groups) {
        const isActive = group.lang === activeGroup.lang ? " active" : "";
        html += '<button type="button" class="voice-lang-btn' + isActive + '" data-lang="' + escapeHtml(group.lang) + '">';
        html += '<span class="voice-lang-name">' + escapeHtml(group.label) + '</span>';
        html += '<span class="voice-lang-count">' + group.voices.length + '</span>';
        html += '</button>';
      }
      html += '<div class="voice-custom-row">';
      html += '<button type="button" class="voice-custom-btn' + (_selectedVoiceId === "__custom__" ? " active" : "") + '" id="voicePickerCustomBtn">' + UI_ICONS.microphone + escapeHtml(_t("voiceCustomBtn")) + "</button>";
      html += '<span class="voice-custom-label">' + escapeHtml(_t("voiceCustomDesc")) + "</span>";
      html += "</div>";
      html += "</div>";
      html += '<div class="voice-option-list" id="voiceOptionList" aria-label="Voice options">';
      html += '<div class="voice-options-head"><span class="voice-options-title">' + escapeHtml(activeGroup.label) + '</span><span class="voice-options-meta">' + activeGroup.voices.length + ' voices · ' + escapeHtml(_voiceCatalogStateLabel()) + '</span></div>';
      html += '<div class="voice-items">';
      for (const voice of activeGroup.voices) {
        const meta = _voiceMetaForId(voice);
        // Filter by search query
        if (_voiceSearchQuery) {
          const searchable = (voice + ' ' + (meta && meta.display_name ? meta.display_name : '') + ' ' + (meta && meta.language ? meta.language : '') + ' ' + (meta && meta.persona ? meta.persona : '') + ' ' + ((meta && Array.isArray(meta.style_tags)) ? meta.style_tags.join(' ') : '')).toLowerCase();
          if (!searchable.includes(_voiceSearchQuery)) continue;
        }
        // Filter by style chip
        if (_voiceStyleFilter && _voiceStyleFilter !== "all") {
          if (!meta) continue;
          const matchFilter = meta.persona === _voiceStyleFilter || meta.mood === _voiceStyleFilter || ((Array.isArray(meta.style_tags) && meta.style_tags.includes(_voiceStyleFilter)));
          if (!matchFilter) continue;
        }
        const displayName = _voiceDisplayName(voice, activeGroup.lang);
        const summary = _voiceSummaryText(voice, activeGroup.lang);
        const isSelected = voice === _selectedVoiceId ? " selected" : "";
        const previewClass = meta && meta.preview_supported === false ? " disabled" : "";
        const titleText = summary ? displayName + ' • ' + summary : displayName;
        html += '<button type="button" class="voice-pill' + isSelected + '" data-voice="' + escapeHtml(voice) + '" title="' + escapeHtml(titleText) + '">';
        html += '<span class="voice-pill-copy"><span class="voice-name">' + escapeHtml(displayName) + '</span>';
        if (summary) html += '<span class="voice-pill-meta">' + escapeHtml(summary) + '</span>';
        html += '</span>';
        html += '<span class="play-icon' + previewClass + '" aria-hidden="true">' + UI_ICONS.play + '</span>';
        html += '</button>';
      }
      html += "</div>";
      html += "</div>";
      html += "</div>";
      container.innerHTML = html;
      _attachScrollSound(document.getElementById("voiceLangList"));
      _attachScrollSound(document.getElementById("voiceOptionList"));
      // Search box
      const searchBox = document.getElementById("voiceSearchBox");
      if (searchBox) {
        searchBox.value = _voiceSearchQuery || "";
        searchBox.addEventListener("input", function() {
          _voiceSearchQuery = searchBox.value.trim().toLowerCase();
          _buildVoicePicker();
        });
      }
      // Filter chips
      document.querySelectorAll(".voice-filter-chip").forEach(function(chip) {
        chip.classList.toggle("active", chip.getAttribute("data-filter") === _voiceStyleFilter);
        chip.addEventListener("click", function() {
          const filter = chip.getAttribute("data-filter");
          _voiceStyleFilter = filter || "all";
          document.querySelectorAll(".voice-filter-chip").forEach(function(c) { c.classList.remove("active"); });
          chip.classList.add("active");
          _buildVoicePicker();
        });
      });
      container.querySelectorAll(".voice-lang-btn").forEach(function(btn) {
        btn.addEventListener("click", function(e) {
          e.stopPropagation();
          const langKey = btn.getAttribute("data-lang");
          if (!langKey || langKey === _activeVoiceLang) return;
          _activeVoiceLang = langKey;
          _lyricsLanguage = langKey;
          const selectedLabel = document.getElementById("voicePickerSelected");
          if (selectedLabel && (!_selectedVoiceId || _voiceGroupForId(_selectedVoiceId)?.lang !== langKey)) {
            selectedLabel.textContent = langKey + (lang === "zh" ? " · 歌词跟随该音色" : lang === "yue" ? " · 歌詞跟隨該音色" : " · lyrics follow this voice");
            selectedLabel.style.color = "var(--accent)";
          }
          const vocalsInput = document.getElementById("vocals");
          if (vocalsInput && (!_selectedVoiceId || _voiceGroupForId(_selectedVoiceId)?.lang !== langKey)) {
            vocalsInput.value = langKey + " vocal";
          }
          SoundSystem.play("click");
          _buildVoicePicker();
        });
      });
      container.querySelectorAll(".voice-pill").forEach(function(btn) {
        btn.addEventListener("click", function(e) {
          e.stopPropagation();
          const voiceId = btn.getAttribute("data-voice");
          if (voiceId) {
            selectVoice(voiceId);
            const meta = _voiceMetaForId(voiceId);
            if (!meta || meta.preview_supported !== false) playVoicePreview(voiceId);
            else showToast(meta.unavailable_reason || t("voicePreviewError"), "warning");
          }
        });
      });
      const customBtn = document.getElementById("voicePickerCustomBtn");
      if (customBtn) {
        customBtn.addEventListener("click", function(e) {
          e.stopPropagation();
          selectVoice("__custom__");
        });
      }
    }

    function selectVoice(voiceId) {
      _selectedVoiceId = voiceId;
      const vocalsInput = document.getElementById("vocals");
      const selectedLabel = document.getElementById("voicePickerSelected");
      if (!vocalsInput) return;
      SoundSystem.play("click");
      if (voiceId === "__custom__") {
        vocalsInput.value = "";
        if (selectedLabel) {
          selectedLabel.textContent = _selectedVoiceLabel(voiceId);
          selectedLabel.style.color = "var(--accent)";
        }
        // Open voice recorder
        if (typeof openVoiceRecorder === "function") openVoiceRecorder();
      } else {
        stopVoicePreview();
        const group = _voiceGroupForId(voiceId);
        const meta = _voiceMetaForId(voiceId);
        const resolvedVoiceLang = meta && meta.language ? meta.language : (group && group.lang !== "__other__" ? group.lang : "");
        if (group) _activeVoiceLang = group.lang;
        else if (resolvedVoiceLang) _activeVoiceLang = resolvedVoiceLang;
        if (resolvedVoiceLang) {
          // Auto-set lyrics language to match voice language
          _lyricsLanguage = resolvedVoiceLang;
          // Check if existing lyrics mismatch
          _checkLyricsLanguageMismatch(resolvedVoiceLang);
        }
        const label = _voiceDisplayName(voiceId, group ? group.lang : "");
        vocalsInput.value = label;
        if (selectedLabel) {
          selectedLabel.textContent = _selectedVoiceLabel(voiceId);
          selectedLabel.style.color = "var(--accent)";
        }
      }
      document.querySelectorAll(".voice-pill").forEach(function(p) {
        p.classList.remove("selected");
        if (p.getAttribute("data-voice") === voiceId) p.classList.add("selected");
      });
      const customBtn = document.getElementById("voicePickerCustomBtn");
      if (customBtn) {
        if (voiceId === "__custom__") customBtn.classList.add("active");
        else customBtn.classList.remove("active");
      }
    }

    const VOICE_PREVIEW_TEXTS_CLIENT = {
      "Chinese (Mandarin)": "你好，这是一段音色试听样本。Music Speaks 把你的文字变成歌曲。",
      "Cantonese": "你好，呢段係音色試聽樣本。Music Speaks 將你嘅文字變成歌曲。",
      "English": "Hello, this is a sample of this voice. Music Speaks turns your words into songs.",
      "Korean": "안녕하세요, 이것은 음성 샘플입니다. Music Speaks가 당신의 말을 노래로 만듭니다.",
      "Japanese": "こんにちは、これは音声サンプルです。Music Speaks が言葉を歌に変えます。",
      "Spanish": "Hola, esta es una muestra de voz. Music Speaks convierte tus palabras en canciones.",
      "Portuguese": "Ola, esta e uma amostra de voz. Music Speaks transforma suas palavras em cancoes.",
      "French": "Bonjour, ceci est un exemple de voix. Music Speaks transforme vos mots en chansons.",
      "German": "Hallo, dies ist eine Stimmprobe. Music Speaks verwandelt Ihre Worte in Lieder.",
      "Indonesian": "Halo, ini adalah contoh suara. Music Speaks mengubah kata-katamu menjadi lagu.",
      "Russian": "Привет, это образец голоса. Music Speaks превращает ваши слова в песни.",
      "Italian": "Ciao, questo e un campione vocale. Music Speaks trasforma le tue parole in canzoni.",
      "Arabic": "مرحبا، هذه عينة صوتية. Music Speaks يحول كلماتك إلى أغان.",
      "Turkish": "Merhaba, bu bir ses ornegidir. Music Speaks sozlerinizi sarkilara donusturur.",
      "Ukrainian": "Привіт, це зразок голосу. Music Speaks перетворює ваші слова на пісні.",
      "Dutch": "Hallo, dit is een stemvoorbeeld. Music Speaks verandert je woorden in liedjes.",
      "Vietnamese": "Xin chao, day la mau giong noi. Music Speaks bien loi cua ban thanh bai hat.",
    };
    const VOICE_LANG_TO_SPEECH_TAG = {
      "Chinese (Mandarin)": "zh-CN", "Cantonese": "zh-HK", "English": "en-US", "Korean": "ko-KR", "Japanese": "ja-JP",
      "Spanish": "es-ES", "Portuguese": "pt-PT", "French": "fr-FR", "German": "de-DE", "Indonesian": "id-ID",
      "Russian": "ru-RU", "Italian": "it-IT", "Arabic": "ar", "Turkish": "tr-TR", "Ukrainian": "uk-UA",
      "Dutch": "nl-NL", "Vietnamese": "vi-VN", "Thai": "th-TH", "Hindi": "hi-IN", "Polish": "pl-PL",
      "Swedish": "sv-SE", "Norwegian": "nb-NO", "Danish": "da-DK", "Finnish": "fi-FI", "Czech": "cs-CZ",
      "Romanian": "ro-RO", "Hungarian": "hu-HU",
    };
    function _voicePreviewLanguage(voiceId) {
      const meta = _voiceMetaForId(voiceId);
      const group = _voiceGroupForId(voiceId);
      if (meta && meta.language) return meta.language;
      return group && group.lang !== "__other__" ? group.lang : (_activeVoiceLang || "English");
    }
    function _isPreviewQuotaError(message) {
      return /no audio|usage|quota|limit|exceed|temporarily unavailable|high demand/i.test(String(message || ""));
    }
    function playBrowserVoicePreview(voiceId, message) {
      if (!("speechSynthesis" in window) || typeof SpeechSynthesisUtterance === "undefined") return false;
      const voiceLang = _voicePreviewLanguage(voiceId);
      const speechTag = VOICE_LANG_TO_SPEECH_TAG[voiceLang] || "en-US";
      const utterance = new SpeechSynthesisUtterance(VOICE_PREVIEW_TEXTS_CLIENT[voiceLang] || VOICE_PREVIEW_TEXTS_CLIENT.English);
      utterance.lang = speechTag;
      utterance.rate = 0.95;
      utterance.pitch = 1;
      const voices = window.speechSynthesis.getVoices ? window.speechSynthesis.getVoices() : [];
      const preferred = voices.find(v => String(v.lang || "").toLowerCase().startsWith(speechTag.toLowerCase().slice(0, 2)));
      if (preferred) utterance.voice = preferred;
      window.speechSynthesis.cancel();
      _voicePreviewUtterance = utterance;
      utterance.onend = utterance.onerror = function() {
        _voicePreviewUtterance = null;
        _voicePlayPending = null;
        document.querySelectorAll(".voice-pill").forEach(function(p) { p.classList.remove("playing"); });
      };
      window.speechSynthesis.speak(utterance);
      showToast("MiniMax preview quota is unavailable, using browser voice preview.", "warning", 5000);
      return true;
    }

    async function playVoicePreview(voiceId) {
      const playId = voiceId;
      stopVoicePreview();
      // Set pending AFTER stopping — stopVoicePreview may null _voicePlayPending
      _voicePlayPending = playId;
      document.querySelectorAll(".voice-pill").forEach(function(p) {
        p.classList.remove("playing");
        if (p.getAttribute("data-voice") === voiceId) p.classList.add("playing");
      });
      try {
        const res = await fetch("/api/voice/preview?voice_id=" + encodeURIComponent(voiceId), { headers: headers({"Accept": "audio/mpeg"}) });
        // Silently ignore if cancelled by user switching voices
        if (_voicePlayPending !== playId) return;
        if (!res.ok) {
          // Try to parse error, but don't show toast for normal cancellation
          const data = await res.json().catch(() => ({}));
          const errMsg = typeof data.error === "string" ? data.error : data.error?.message || t("voicePreviewError");
          if (_voicePlayPending === playId) {
            if (_isPreviewQuotaError(errMsg) && playBrowserVoicePreview(voiceId, errMsg)) return;
            if (_isPreviewUnavailableError(errMsg)) {
              _markVoicePreviewCapability(voiceId, false, errMsg);
              _buildVoicePicker();
            }
            showToast(errMsg, "error");
            SoundSystem.play("error");
          }
          return;
        }
        const blob = await res.blob();
        if (_voicePlayPending !== playId) return;
        if (!blob || blob.size === 0) {
          if (_voicePlayPending === playId) showToast(t("voicePreviewError"), "error");
          return;
        }
        const url = URL.createObjectURL(blob);
        _voiceAudio = new Audio(url);
        _voiceAudio.addEventListener("ended", function() {
          _voicePlayPending = null;
          URL.revokeObjectURL(url);
          document.querySelectorAll(".voice-pill").forEach(function(p) { p.classList.remove("playing"); });
        });
        _voiceAudio.addEventListener("play", function() {
          _markVoicePreviewCapability(voiceId, true, "");
        });
        _voiceAudio.addEventListener("error", function(e) {
          if (_voicePlayPending !== playId) return;
          stopVoicePreview();
          showToast(t("voicePreviewError"), "error");
          SoundSystem.play("error");
        });
        await _voiceAudio.play();
      } catch (err) {
        if (_voicePlayPending !== playId) return;
        if (_isPreviewQuotaError(err.message) && playBrowserVoicePreview(voiceId, err.message)) return;
        if (_isPreviewUnavailableError(err.message || "")) {
          _markVoicePreviewCapability(voiceId, false, err.message || t("voicePreviewError"));
          _buildVoicePicker();
        }
        stopVoicePreview();
        showToast(err.message || t("voicePreviewError"), "error");
        SoundSystem.play("error");
      }
    }

    function stopVoicePreview() {
      // NOTE: Do NOT set _voicePlayPending = null here — that happens in the 'ended' event
      // or when a new playVoicePreview call overwrites it. Clearing it here causes the
      // _voicePlayPending !== playId check after fetch to return early, breaking preview.
      if ("speechSynthesis" in window && _voicePreviewUtterance) {
        window.speechSynthesis.cancel();
        _voicePreviewUtterance = null;
      }
      if (_voiceAudio) {
        const src = _voiceAudio.src;
        _voiceAudio.pause();
        _voiceAudio.src = "";
        _voiceAudio = null;
        if (src && src.startsWith("blob:")) URL.revokeObjectURL(src);
      }
      document.querySelectorAll(".voice-pill").forEach(function(p) { p.classList.remove("playing"); });
    }

    async function loadVoicePicker() {
      try {
        const res = await fetch("/api/voice", { headers: headers() });
        const data = await res.json();
        _cachedVoices = Array.isArray(data.voices) ? data.voices : [];
        _cachedVoiceMeta = data.voice_meta && typeof data.voice_meta === "object" ? data.voice_meta : {};
        _voiceCatalogState = {
          fallback: !!data.fallback,
          cached: !!data.cached,
          stale: !!data.stale,
        };
        _buildVoicePicker();
      } catch(e) {
        const container = document.getElementById("voicePickerScroll");
        if (container) container.innerHTML = '<div style="padding:16px;text-align:center;color:var(--danger);font-size:12px;">Failed to load voices.</div>';
      }
    }

    // Init voice picker on load
    loadVoicePicker();

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
      container.innerHTML = `<div class="rec-progress"><div class="rec-step">${t("recPreparing")}</div></div><div class="rec-script-box"></div><div class="rec-controls-row"><button id="recModalClose" class="secondary-btn" type="button">${t("recCancel")}</button></div>`;
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
          <div class="rec-step">${t("recSegment")} ${idx + 1} / ${total} — ${seg.label}</div>
          <div class="rec-bar"><div class="rec-bar-fill" style="width:${progress}%"></div></div>
        </div>
        <div class="rec-script-box">
          <div class="rec-instruction">${seg.desc}</div>
          <div class="rec-script">"${script}"</div>
        </div>
        <div class="rec-countdown" id="recCountdown">${t("recStartingIn", {seconds: 3})}</div>
        <div class="rec-controls-row">
          <button id="recStartSeg" class="secondary-btn" type="button">${t("recStartRecording")}</button>
          <button id="recModalClose" class="ghost" type="button">${t("recCancel")}</button>
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
          countdownEl.textContent = t("recStartingIn", {seconds: count});
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
            alert(t("recRecordingFailed"));
            closeVoiceRecorder();
            return;
          }
          const rawBlob = new Blob(recordedChunks, { type: mimeType });
          if (rawBlob.size < 1000) {
            alert(t("recTooSmall"));
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
        document.getElementById("recStartSeg").textContent = t("recRecording");
        const countdownEl = document.getElementById("recCountdown");
        let remaining = 5;
        countdownEl.textContent = t("recRecordingCountdown", {seconds: remaining});
        recordingTimer = setInterval(() => {
          remaining--;
          if (remaining > 0) {
            countdownEl.textContent = t("recRecordingCountdown", {seconds: remaining});
          }
        }, 1000);
        setTimeout(() => { if (mediaRecorder.state === "recording") mediaRecorder.stop(); }, SEGMENT_DURATION);
      } catch (err) {
        alert(t("recMicDenied"));
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
          <div class="rec-step">${t("recSegment")} ${idx + 1} / ${segs.length} — ${seg.label} ${UI_ICONS.check}</div>
          <div class="rec-bar"><div class="rec-bar-fill" style="width:${((idx + 1) / segs.length) * 100}%"></div></div>
        </div>
        <div class="rec-script-box">
          <div class="rec-instruction">${seg.desc}</div>
          <div class="rec-script">"${script}"</div>
        </div>
        <div class="rec-review-audio"><audio src="${url}" controls style="height:40px; width:100%;"></audio></div>
        <div class="rec-controls-row">
          <button id="recRerecord" class="ghost" type="button"><svg class="ui-icon" aria-hidden="true"><use href="#icon-refresh"></use></svg> ${t("recRerecord")}</button>
          <button id="recNext" class="secondary-btn" type="button">${t("recNext")}</button>
        </div>
      `;
      document.getElementById("recRerecord").addEventListener("click", () => showSegment(idx));
      document.getElementById("recNext").addEventListener("click", () => showSegment(idx + 1));
    }

    async function showAllDone() {
      const body = document.getElementById("recModalBody");
      body.innerHTML = `<div class="rec-done">${t("recAllDone")}</div>`;
      try {
        const combined = await mergeAudioBlobs(recordedSegments);
        const fd = new FormData();
        fd.append("audio", combined, "voice_sample.wav");
        voiceStatus.textContent = t("recUploadingCloning");
        voiceStatus.style.color = "var(--muted)";
        const res = await fetch("/api/voice/clone", { method: "POST", headers: headers(), body: fd });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          const errMsg = typeof data.error === "string" ? data.error : data.error?.message || (t("recCloneFailed"));
          throw new Error(errMsg);
        }
        clonedVoiceId = data.voice_id || "";
        const expiresHours = data.expires_in_hours || 168;
        const expiresAt = Date.now() + expiresHours * 3600 * 1000;
        localStorage.setItem("terry_music_voice_id", clonedVoiceId);
        localStorage.setItem("terry_music_voice_expires", String(expiresAt));
        localStorage.setItem("terry_music_voice_lang", _activeVoiceLang);
        _lyricsLanguage = _activeVoiceLang;
        if (data.voice_wav_path) localStorage.setItem("terry_music_voice_wav", data.voice_wav_path);
        voicePreviewRow.style.display = "flex";
        closeVoiceRecorder();
        voiceStatus.textContent = t("recVoiceReady");
        voiceStatus.style.color = "var(--accent)";
        voiceStatus.classList.add("animate-bounce-in");
        SoundSystem.play("success");
        showToast(t("toastVoiceCloneSuccess"), "success");
      } catch (err) {
        body.innerHTML = `<div class="rec-done rec-error">${t("recCloneFailedPrefix")}${err.message}</div><div class="rec-controls-row"><button id="recModalClose2" class="secondary-btn" type="button">${t("recClose")}</button></div>`;
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
        if (confirm(t("recRerecordConfirm"))) {
          localStorage.removeItem("terry_music_voice_id");
          localStorage.removeItem("terry_music_voice_expires");
          localStorage.removeItem("terry_music_voice_lang");
          clonedVoiceId = "";
          _lyricsLanguage = _activeVoiceLang || "auto";
          openVoiceRecorder();
        }
      } else {
        openVoiceRecorder();
      }
    });

    // ── Fullscreen Lyrics Modal ─────────────────────────────────────
    const lyricsModal = document.createElement("div");
    lyricsModal.id = "lyricsFullscreenModal";
    lyricsModal.innerHTML = `
      <div class="lfm-bg"></div>
      <div class="lfm-header">
        <div class="lfm-track-info">
          <div class="lfm-title" id="lfmTitle"></div>
          <div class="lfm-artist" id="lfmArtist"></div>
        </div>
        <button class="lfm-close" id="lfmClose" aria-label="${t("closeFullscreenLyrics")}">✕</button>
      </div>
      <div class="lfm-body" id="lfmBody">
        <div class="lfm-lines" id="lfmLines"></div>
      </div>
      <div class="lfm-footer">
        <div class="lfm-controls">
          <button class="lfm-btn" id="lfmPrev" aria-label="${t("previous")}"><svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path d="M19 4H9l-7 8 7 8h10V4z"/></svg></button>
          <button class="lfm-btn lfm-play" id="lfmPlay" aria-label="${t("playPause")}"><svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true"><path class="play-path" d="M8 5v14l11-7L8 5Z"/></svg></button>
          <button class="lfm-btn" id="lfmNext" aria-label="${t("next")}"><svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path d="M5 4l10 8-10 8V4z"/><rect x="17" y="4" width="2" height="16"/></svg></button>
        </div>
        <div class="lfm-progress-row">
          <span class="lfm-time" id="lfmCurrentTime">0:00</span>
          <div class="lfm-bar" id="lfmBar"><div class="lfm-bar-fill" id="lfmBarFill"></div></div>
          <span class="lfm-time" id="lfmDuration">0:00</span>
        </div>
        <div class="lfm-current-line" id="lfmCurrentLine"></div>
      </div>
    `;
    document.body.appendChild(lyricsModal);

    let _lfmLastIndex = -1;
    function _openLyricsModal() {
      lyricsModal.classList.add("open");
      document.body.style.overflow = "hidden";
      _syncLfmFromPlayer();
    }
    function _closeLyricsModal() {
      lyricsModal.classList.remove("open");
      document.body.style.overflow = "";
    }
    function _syncLfmFromPlayer() {
      if (!currentTrack) return;
      document.getElementById("lfmTitle").textContent = currentTrack.title || t("untitled");
      document.getElementById("lfmArtist").textContent = t("artistName");
      const rows = getLyricRows();
      const playableRows = rows.filter(r => !r.isSection && r.text);
      if (!playableRows.length) {
        document.getElementById("lfmLines").innerHTML = '<div class="lfm-empty">' + escapeHtml(t("noLyrics")) + '</div>';
        return;
      }
      document.getElementById("lfmLines").innerHTML = rows.map(row => {
        const cls = row.isSection ? "lfm-line section" : "lfm-line";
        return '<div class="' + cls + '" data-idx="' + row.index + '">' + escapeHtml(row.text) + '</div>';
      }).join("");
      document.getElementById("lfmDuration").textContent = formatTime(audioPlayer.duration);
      document.getElementById("lfmPlay").innerHTML = audioPlayer.paused
        ? '<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true"><path d="M8 5v14l11-7L8 5Z"/></svg>'
        : '<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true"><path d="M7 5h3.5v14H7V5Zm6.5 0H17v14h-3.5V5Z"/></svg>';
    }
    function _updateLfmProgress() {
      const rows = getLyricRows();
      if (!rows.length) return;
      const playableRows = rows.filter(r => !r.isSection && r.text);
      if (!playableRows.length || !audioPlayer.duration) return;
      // Use improved currentLyricRowIndex with weighted time distribution and timestamp support
      const activeIndex = currentLyricRowIndex(rows);
      const activeRow = rows.find(r => r.index === activeIndex);
      const currentText = activeRow ? activeRow.text : "";
      document.getElementById("lfmCurrentLine").textContent = currentText;
      document.getElementById("lfmCurrentTime").textContent = formatTime(audioPlayer.currentTime);
      document.getElementById("lfmBarFill").style.width = ((audioPlayer.currentTime / audioPlayer.duration) * 100) + "%";
      document.getElementById("lfmPlay").innerHTML = audioPlayer.paused
        ? '<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true"><path d="M8 5v14l11-7L8 5Z"/></svg>'
        : '<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true"><path d="M7 5h3.5v14H7V5Zm6.5 0H17v14h-3.5V5Z"/></svg>';
      if (activeIndex !== _lfmLastIndex) {
        _lfmLastIndex = activeIndex;
        document.querySelectorAll(".lfm-line").forEach(el => {
          el.classList.toggle("active", el.getAttribute("data-idx") === String(activeIndex));
        });
        const activeEl = document.querySelector('.lfm-line[data-idx="' + activeIndex + '"]');
        if (activeEl) activeEl.scrollIntoView({ block: "center", behavior: "smooth" });
      }
    }

    let _lfmTimeUpdateHandler = null;
    document.getElementById("lyricsFullscreenBtn").addEventListener("click", () => {
      if (lyricsModal.classList.contains("open")) { _closeLyricsModal(); return; }
      _openLyricsModal();
      _syncLfmFromPlayer();
      _updateLfmProgress();
      // Attach one continuous timeupdate handler while modal is open.
      if (!_lfmTimeUpdateHandler) {
        _lfmTimeUpdateHandler = () => _updateLfmProgress();
        audioPlayer.addEventListener("timeupdate", _lfmTimeUpdateHandler);
      }
    });
    function _closeLyricsModal() {
      lyricsModal.classList.remove("open");
      document.body.style.overflow = "";
      if (_lfmTimeUpdateHandler) {
        audioPlayer.removeEventListener("timeupdate", _lfmTimeUpdateHandler);
        _lfmTimeUpdateHandler = null;
      }
    }
    document.getElementById("lfmClose").addEventListener("click", _closeLyricsModal);
    document.querySelector(".lfm-bg").addEventListener("click", _closeLyricsModal);
    document.getElementById("lfmPlay").addEventListener("click", () => {
      if (audioPlayer.paused) audioPlayer.play(); else audioPlayer.pause();
    });
    document.getElementById("lfmPrev").addEventListener("click", () => {
      if (!currentTrack) return;
      audioPlayer.currentTime = 0;
      _updateLfmProgress();
    });
    document.getElementById("lfmNext").addEventListener("click", () => {
      if (!currentTrack) return;
      audioPlayer.currentTime = 0;
      _updateLfmProgress();
    });
    document.getElementById("lfmBar").addEventListener("click", e => {
      if (!audioPlayer.duration) return;
      const rect = document.getElementById("lfmBar").getBoundingClientRect();
      audioPlayer.currentTime = ((e.clientX - rect.left) / rect.width) * audioPlayer.duration;
      _updateLfmProgress();
    });
    audioPlayer.addEventListener("timeupdate", _updateLfmProgress);
    audioPlayer.addEventListener("play", () => { document.getElementById("lfmPlay").innerHTML = '<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true"><path d="M7 5h3.5v14H7V5Zm6.5 0H17v14h-3.5V5Z"/></svg>'; });
    audioPlayer.addEventListener("pause", () => { document.getElementById("lfmPlay").innerHTML = '<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true"><path d="M8 5v14l11-7L8 5Z"/></svg>'; });
    audioPlayer.addEventListener("ended", () => { document.getElementById("lfmPlay").innerHTML = '<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true"><path d="M8 5v14l11-7L8 5Z"/></svg>'; _lfmLastIndex = -1; });
    audioPlayer.addEventListener("loadedmetadata", () => {
      if (lyricsModal.classList.contains("open")) document.getElementById("lfmDuration").textContent = formatTime(audioPlayer.duration);
    });

    // ── Lyrics timestamp parsing (for [00:12.34] / LRC format) ────────────
    const _timestampCache = new Map();
    function _parseTimestamps(lyricsText, rows = []) {
      const cacheKey = lyricsText + "::" + rows.length;
      if (_timestampCache.has(cacheKey)) return _timestampCache.get(cacheKey);
      const lines = String(lyricsText || "").split(/\r?\n/).map(line => line.trim()).filter(Boolean);
      const results = [];
      let rowCursor = 0;
      for (const line of lines) {
        const row = rows[rowCursor] || null;
        rowCursor += 1;
        if (row && row.isSection) continue;
        const matches = [...line.matchAll(/\[(\d{2}):(\d{2})(?:\.(\d{1,3}))?\]/g)];
        if (!matches.length) continue;
        const text = line.replace(/^(?:\[(\d{2}):(\d{2})(?:\.(\d{1,3}))?\])+/, "").trim();
        if (!text) continue;
        matches.forEach(match => {
          const min = parseInt(match[1], 10);
          const sec = parseInt(match[2], 10);
          const fraction = match[3] || "";
          const ms = fraction ? parseInt(fraction.padEnd(3, "0").slice(0, 3), 10) : 0;
          results.push({ time: min * 60 + sec + ms / 1000, text, row_index: row ? row.index : null, source: "embedded-lrc" });
        });
      }
      results.sort((a, b) => a.time - b.time);
      _timestampCache.set(cacheKey, results);
      return results;
    }
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
        const title = escapeHtml(job.song_title || job.prompt || "Untitled");
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


def parse_iso_datetime(value: Any) -> dt.datetime | None:
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def sweep_jobs_locked(now: dt.datetime | None = None) -> bool:
    """Expire stuck active jobs and prune old terminal jobs. Caller holds JOBS_LOCK."""
    if now is None:
        now = dt.datetime.now(dt.timezone.utc)
    changed = False
    timeout_seconds = max(60, JOB_TIMEOUT_SECONDS)
    retention_seconds = max(timeout_seconds, JOB_RETENTION_SECONDS)
    timeout_minutes = max(1, timeout_seconds // 60)
    for job_id, job in list(JOBS.items()):
        status = str(job.get("status") or "unknown")
        created_at = parse_iso_datetime(job.get("created_at")) or now
        updated_at = parse_iso_datetime(job.get("updated_at")) or created_at
        active_age = (now - updated_at).total_seconds()
        if status in {"queued", "running"} and active_age > timeout_seconds:
            job["status"] = "error"
            job["error"] = job.get("error") or f"Generation timed out after {timeout_minutes} minutes."
            job["updated_at"] = now.isoformat()
            changed = True
            continue
        terminal_age = (now - updated_at).total_seconds()
        if status in {"completed", "error"} and terminal_age > retention_seconds:
            JOBS.pop(job_id, None)
            changed = True
    return changed


def sweep_jobs() -> None:
    with JOBS_LOCK:
        if sweep_jobs_locked():
            save_jobs_locked()


def normalize_client_id(value: str | None) -> str:
    text = (value or "").strip()
    if re.fullmatch(r"[A-Za-z0-9._:-]{8,160}", text):
        return text
    return "anonymous"


def is_valid_client_id(value: str | None) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9._:-]{8,160}", (value or "").strip()))


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
    with JOBS_LOCK:
        if sweep_jobs_locked():
            save_jobs_locked()


def save_jobs_locked() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    tmp = JOBS_DB.with_suffix(".tmp")
    tmp.write_text(json.dumps(JOBS, ensure_ascii=False, indent=2), encoding="utf-8")
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
    tmp.write_text(json.dumps(DRAFTS, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(DRAFTS_DB)


def clean_draft_payload(form: dict[str, Any]) -> dict[str, Any]:
    limits = {
        "email": 320,
        "song_title": 120,
        "prompt": 2000,
        "lyrics": LYRICS_CHAR_LIMIT,
        "lyrics_idea": 2500,
        "lyrics_extra": 500,
        "lyrics_language": 80,
        "interface_language": 40,
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
        "voice_mode": 40,
        "voice_id": 200,
    }
    draft = {key: str(form.get(key, "")).strip()[:limit] for key, limit in limits.items()}
    draft["is_instrumental"] = bool(form.get("is_instrumental"))
    draft["lyrics_optimizer"] = bool(form.get("lyrics_optimizer"))
    return draft


def _lyric_timing_payload(lyrics: str, audio_path: Path | None) -> dict[str, Any]:
    if not str(lyrics or "").strip() or not audio_path or not audio_path.is_file():
        return {}
    timestamps, source = build_lyric_timestamps(lyrics, audio_path)
    if not timestamps and source == "unavailable":
        return {}
    payload: dict[str, Any] = {
        "lyric_timestamps": timestamps,
        "lyric_timing_source": source,
    }
    if timestamps or source != "unavailable":
        payload["lyric_timing_version"] = LYRIC_TIMING_VERSION
    return payload


def refresh_job_lyric_timing(job: dict[str, Any]) -> bool:
    if job.get("status") != "completed":
        return False
    if int(job.get("lyric_timing_version") or 0) >= LYRIC_TIMING_VERSION and isinstance(job.get("lyric_timestamps"), list):
        return False
    file_path = str(job.get("file_path", "") or "").strip()
    if not file_path:
        return False
    payload = _lyric_timing_payload(str(job.get("lyrics", "")), Path(file_path))
    if not payload:
        return False
    job.update(payload)
    return True


def public_job(job: dict[str, Any], include_lyrics: bool = False) -> dict[str, Any]:
    result = {key: job.get(key) for key in ("id", "status", "created_at", "updated_at", "prompt", "song_title", "generated_title", "title_error", "email", "is_instrumental", "lyrics_optimizer", "file_name", "error", "email_sent", "voice_render_mode", "lyric_timing_source")}
    if include_lyrics:
        result["lyrics"] = job.get("lyrics", "")
        result["lyrics_idea"] = job.get("lyrics_idea", "")
        result["lyrics_extra"] = job.get("lyrics_extra", "")
        result["lyrics_language"] = job.get("lyrics_language", "")
        result["generated_lyrics"] = bool(job.get("generated_lyrics"))
        result["lyric_timestamps"] = job.get("lyric_timestamps") or []
    if job.get("status") == "completed" and job.get("file_path"):
        result["download_url"] = f"/download/{urllib.parse.quote(str(job['id']))}"
    return result


def admin_job(job: dict[str, Any]) -> dict[str, Any]:
    result = public_job(job, include_lyrics=True)
    result.update({
        "owner_id": job.get("owner_id"),
        "voice_mode": job.get("voice_mode"),
        "voice_clone_singing_error": job.get("voice_clone_singing_error"),
        "extra": job.get("extra", {}),
    })
    if job.get("status") == "completed" and job.get("file_path"):
        result["download_url"] = f"/download/{urllib.parse.quote(str(job['id']))}?admin_key={urllib.parse.quote(ADMIN_KEY)}"
    return result



def generate_title_from_text_model(job: dict[str, Any], lyrics: str, timeout: float = 180) -> str:
    """
    Generate song title by having AI produce 5 candidates, then score+rank them
    against the full lyrics to pick the most fitting one. No hardcoded keywords.
    """
    lyrics_lines = _lyrics_content_lines(lyrics)
    lyric_text = "\n".join(lyrics_lines)
    preferred_lang = _title_language(lyric_text)

    prompt = str(job.get("prompt", "")).strip()
    lyrics_idea = str(job.get("lyrics_idea", "")).strip()
    extra = job.get("extra", {}) if isinstance(job.get("extra"), dict) else {}
    mood = extra.get("mood", "")

    # Language-aware instruction
    if preferred_lang == "zh":
        title_rules = "中文歌名，4-10个汉字，不要英文。"
        example_titles = "《夜雨寄北》《平凡之路》《匆匆那年》《模特》"
    else:
        title_rules = "English title, 2-6 words, no Chinese characters."
        example_titles = '"Chasing Cars", "Someone Like You", "Fix You", "Let Her Go"'

    # Step 1: Ask AI to generate 5 diverse, creative title options.
    # Each title should capture a DIFFERENT aspect/angle of the song.
    gen_system = (
        "You are a songwriter naming a new song. You will receive the complete lyrics. "
        "Generate exactly 5 different possible song titles. "
        "Each title must be concise, poetic, and capture a distinct angle of the song — "
        "not just the first line or obvious repeated phrase. "
        "Vary the style: one may be image-based, one emotion-based, one action-based, "
        "one metaphor-based, one abstract. "
        "Do NOT use generic titles like 'My Love', 'Forever', 'You and Me' — these are banned. "
        f"TITLE FORMAT: {title_rules} "
        f"Real song title examples: {example_titles} "
        "Output exactly 5 titles, one per line, nothing else."
    )

    gen_message = f"""Here are the complete lyrics of the song:

{lyric_text[:3000]}

{f"Music style prompt: {prompt}" if prompt else ""}
{f"Suggested mood: {mood}" if mood else ""}
{f"Lyrics brief: {lyrics_idea}" if lyrics_idea else ""}

Generate 5 different song titles, one per line. No numbering, no explanation."""

    candidates_raw = run_mmx([
        "text", "chat",
        "--system", gen_system,
        "--message", gen_message,
        "--max-tokens", "200",
        "--temperature", "0.9",
        "--non-interactive",
        "--quiet",
        "--output", "text",
    ], timeout=int(max(60, timeout)))

    # Parse 5 candidates
    candidates = []
    for line in candidates_raw.strip().splitlines():
        line = line.strip().strip('"\'《》')
        if line and len(line) > 1:
            cleaned = clean_song_title(line)
            if cleaned:
                candidates.append(cleaned)
    if len(candidates) < 2:
        # Not enough candidates — use what we have or bail
        if candidates:
            return candidates[0]
        raise RuntimeError("MiniMax could not generate enough title options.")

    # Step 2: Score each candidate against the full lyrics.
    # Ask AI to rank+pick the best one based on how well it captures the song's essence.
    score_system = (
        "You are a music critic. You will receive a song's complete lyrics and 5 title options. "
        "Pick the ONE title that best captures the song's central theme, emotion, and imagery. "
        "Avoid titles that are generic, cliché, or only surface-level. "
        "Prefer titles that are poetic, evocative, or metaphorically rich. "
        "Output ONLY the chosen title text — no explanation, no number, no quotes."
    )

    titles_block = "\n".join(f"- {t}" for t in candidates)
    score_message = f"""Song lyrics:
{lyric_text[:3000]}

Title options:
{titles_block}

Pick the single best title. Output only the title itself."""

    chosen = run_mmx([
        "text", "chat",
        "--system", score_system,
        "--message", score_message,
        "--max-tokens", "60",
        "--temperature", "0.3",
        "--non-interactive",
        "--quiet",
        "--output", "text",
    ], timeout=int(max(30, timeout)))

    final = clean_song_title(chosen)
    if not final:
        # Fallback to first candidate if AI returned gibberish
        return candidates[0]
    return final


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


lyrics_runtime.Path = Path
lyrics_runtime.base64 = base64
lyrics_runtime.json = json
lyrics_runtime.secrets = secrets
lyrics_runtime.time = time
lyrics_runtime.MINIMAX_API_KEY = MINIMAX_API_KEY
lyrics_runtime.VOICE_CLONE_SINGING_MODEL = VOICE_CLONE_SINGING_MODEL
lyrics_runtime.VOICE_CLONE_SINGING_ENDPOINT = VOICE_CLONE_SINGING_ENDPOINT
lyrics_runtime.GENERATED_LYRICS_MIN_CHARS = GENERATED_LYRICS_MIN_CHARS
lyrics_runtime.GENERATED_LYRICS_MAX_CHARS = GENERATED_LYRICS_MAX_CHARS
lyrics_runtime.run_mmx = run_mmx
lyrics_runtime._detect_lang_from_voice_id = _detect_lang_from_voice_id


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
        args.extend(build_music_option_args(job.get("extra", {})))
        run_mmx(args)
        timing_updates = _lyric_timing_payload(lyrics, out_path)
        mark_job(job_id, status="completed", file_name=file_name, file_path=str(out_path), **timing_updates)
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
        voice_mode = str(job.get("voice_mode") or "voice_clone_singing")
        singing_error = ""
        if voice_mode == "voice_clone_singing":
            try:
                synthesize_voice_clone_singing(lyrics, voice_id, out_path, prompt)
                mark_job(
                    job_id,
                    status="completed",
                    file_name=file_name,
                    file_path=str(out_path),
                    voice_render_mode="voice_clone_singing",
                    voice_clone_singing_error=None,
                    **_lyric_timing_payload(lyrics, out_path),
                )
            except Exception as exc:
                singing_error = str(exc)
                out_path.unlink(missing_ok=True)
                print(f"[voice_clone_singing] falling back to music cover: {singing_error}")
        if not out_path.exists() or out_path.stat().st_size <= 0:
            generate_voice_cover_audio(prompt, lyrics, Path(voice_wav), out_path, job.get("extra", {}))
            mark_job(
                job_id,
                status="completed",
                file_name=file_name,
                file_path=str(out_path),
                voice_render_mode="music_cover_fallback" if voice_mode == "voice_clone_singing" else "music_cover",
                voice_clone_singing_error=singing_error or None,
                **_lyric_timing_payload(lyrics, out_path),
            )
        if job.get("email") and out_path.exists():
            ok = send_email(str(job["email"]), out_path, prompt)
            mark_job(job_id, email_sent=ok)
    except Exception as exc:
        mark_job(job_id, status="error", error=str(exc))


class MusicHandler(BaseHTTPRequestHandler):
    server_version = "MusicSpeaks/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"{self.log_date_time_string()} {self.address_string()} {fmt % args}")

    def is_admin_request(self, parsed: urllib.parse.ParseResult | None = None) -> bool:
        key = self.headers.get("X-Admin-Key", "")
        if parsed is not None:
            query = urllib.parse.parse_qs(parsed.query)
            key = key or (query.get("key") or query.get("admin_key") or [""])[0]
        return bool(ADMIN_KEY and key and hmac.compare_digest(key, ADMIN_KEY))

    def end_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header("Permissions-Policy", "camera=(), geolocation=(), microphone=(self)")
        self.send_header("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        super().end_headers()

    def require_client_id(self) -> str | None:
        raw = self.headers.get("X-Client-Id")
        if is_valid_client_id(raw):
            return normalize_client_id(raw)
        self.send_json({"error": "A valid X-Client-Id header is required."}, HTTPStatus.BAD_REQUEST)
        return None

    def send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def send_text(self, text: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_HEAD(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path == "/":
            data_len = len(INDEX_HTML.encode("utf-8"))
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(data_len))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return
        if path == "/admin":
            data_len = len(ADMIN_HTML.encode("utf-8"))
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(data_len))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return
        if path == "/api/health":
            data_len = len(json.dumps({"ok": True}).encode("utf-8"))
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(data_len))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return
        if path == "/favicon.ico":
            self.send_response(HTTPStatus.NO_CONTENT)
            self.end_headers()
            return
        self.send_response(HTTPStatus.NOT_FOUND)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path == "/":
            data = INDEX_HTML.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
            return
        if path == "/admin":
            data = ADMIN_HTML.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
            return
        if path == "/api/health":
            payload = {"ok": True}
            if self.is_admin_request(parsed):
                payload.update({
                    "minimax_configured": bool(MINIMAX_API_KEY),
                    "admin_configured": bool(ADMIN_KEY),
                    "title_fallback": True,
                    "drafts": True,
                    "job_timeout_seconds": JOB_TIMEOUT_SECONDS,
                    "job_retention_seconds": JOB_RETENTION_SECONDS,
                    "smtp_configured": bool(SMTP_USER and SMTP_PASSWORD),
                    "smtp_host": SMTP_HOST,
                    "smtp_port": SMTP_PORT,
                })
            self.send_json(payload)
            return
        if path == "/api/admin/jobs":
            sweep_jobs()
            if not self.is_admin_request(parsed):
                self.send_json({"error": "Unauthorized"}, HTTPStatus.UNAUTHORIZED)
                return
            with JOBS_LOCK:
                dirty = any(refresh_job_lyric_timing(job) for job in JOBS.values())
                if dirty:
                    save_jobs_locked()
                jobs = sorted(
                    [admin_job(job) for job in JOBS.values()],
                    key=lambda item: str(item.get("created_at", "")),
                    reverse=True,
                )
            self.send_json({"jobs": jobs})
            return
        if path == "/api/jobs":
            sweep_jobs()
            client_id = self.require_client_id()
            if client_id is None:
                return
            with JOBS_LOCK:
                own_jobs = [job for job in JOBS.values() if job.get("owner_id") == client_id]
                dirty = any(refresh_job_lyric_timing(job) for job in own_jobs)
                if dirty:
                    save_jobs_locked()
                jobs = sorted(
                    [public_job(job, include_lyrics=True) for job in own_jobs],
                    key=lambda item: str(item.get("created_at", "")),
                    reverse=True,
                )
            self.send_json({"jobs": jobs})
            return
        if parsed.path.startswith("/api/jobs/"):
            sweep_jobs()
            job_id = urllib.parse.unquote(parsed.path.removeprefix("/api/jobs/"))
            client_id = self.require_client_id()
            if client_id is None:
                return
            with JOBS_LOCK:
                job = JOBS.get(job_id)
                if not job or job.get("owner_id") != client_id:
                    self.send_json({"error": "Job not found"}, HTTPStatus.NOT_FOUND)
                    return
                if refresh_job_lyric_timing(job):
                    save_jobs_locked()
                self.send_json(public_job(job, include_lyrics=True))
            return
        if path.startswith("/api/drafts/"):
            self.handle_get_draft(path.removeprefix("/api/drafts/"))
            return
        if path.startswith("/api/voice/preview"):
            self.handle_voice_preview()
            return
        if path == "/api/voice":
            self.handle_get_voices()
            return
        if path.startswith("/download/"):
            self.handle_download(path.removeprefix("/download/"), parsed.query)
            return
        if path == "/favicon.ico":
            self.send_response(HTTPStatus.NO_CONTENT)
            self.end_headers()
            return
        self.send_text("Not found", HTTPStatus.NOT_FOUND)

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
            lyrics_extra = str(form.get("lyrics_extra", "")).strip()
            if not prompt and not lyrics_idea:
                raise ValueError("Lyrics brief or music style prompt is required.")
            if len(prompt) > 2000:
                raise ValueError("Prompt must be 2000 characters or fewer.")
            if len(lyrics_idea) > 2500:
                raise ValueError("Lyrics brief must be 2500 characters or fewer.")
            if lyrics_extra and len(lyrics_extra) > 500:
                raise ValueError("附加要求 must be 500 characters or fewer.")
            extra = {key: str(form.get(key, "")).strip() for key in ("genre", "mood", "instruments", "tempo", "bpm", "key", "vocals", "structure", "references", "avoid", "use_case", "extra")}
            voice_id = str(form.get("voice_id", "")).strip()
            lyrics_language = str(form.get("lyrics_language", "auto")).strip()
            interface_language = str(form.get("interface_language", "")).strip()
            job_for_generation = {
                "prompt": prompt,
                "lyrics_idea": lyrics_idea,
                "lyrics_extra": lyrics_extra,
                "extra": extra,
                "voice_id": voice_id,
                "lyrics_language": lyrics_language,
                "interface_language": interface_language,
            }
            lyrics = generate_lyrics_from_text_model(job_for_generation, timeout=LYRICS_REQUEST_TIMEOUT)
            requested_title = clean_song_title(str(form.get("song_title", "")).strip())
            title_error = None
            if requested_title:
                song_title = requested_title
                generated_title = False
            else:
                try:
                    song_title = generate_title_from_text_model(job_for_generation, lyrics, timeout=min(45, LYRICS_REQUEST_TIMEOUT))
                    generated_title = True
                except Exception as exc:
                    song_title = fallback_song_title(job_for_generation, lyrics)
                    title_error = str(exc)
                    generated_title = False
        except ValueError as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        except Exception as exc:
            fallback = fallback_generated_lyrics(
                prompt if "prompt" in locals() else "",
                lyrics_idea if "lyrics_idea" in locals() else "",
                extra if "extra" in locals() else {},
                voice_id if "voice_id" in locals() else "",
                lyrics_language if "lyrics_language" in locals() else "auto",
                interface_language if "interface_language" in locals() else "",
            )
            self.send_json({"lyrics": fallback, "fallback": True, "warning": "Live lyrics generation timed out or failed; using local fallback."})
            return
        self.send_json({"lyrics": lyrics, "song_title": song_title, "generated_title": generated_title, "title_error": title_error})

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
            lyrics_extra = str(form.get("lyrics_extra", "")).strip()
            lyrics_language = str(form.get("lyrics_language", "auto")).strip()
            interface_language = str(form.get("interface_language", "")).strip()
            voice_id = str(form.get("voice_id", "")).strip()
            voice_mode = str(form.get("voice_mode") or "voice_clone_singing").strip()
            is_instrumental = bool(form.get("is_instrumental"))
            lyrics_optimizer = bool(form.get("lyrics_optimizer") or lyrics_idea) and not is_instrumental
            if not voice_id:
                raise ValueError("voice_id is required for voice music job.")
            if voice_mode not in {"voice_clone_singing", "cover"}:
                raise ValueError("voice_mode must be voice_clone_singing or cover.")
            if is_instrumental:
                raise ValueError("Voice clone singing requires a vocal track. Turn off Instrumental.")
            if not prompt:
                raise ValueError("Prompt is required.")
            if len(prompt) > 2000:
                raise ValueError("Prompt must be 2000 characters or fewer.")
            if len(raw_song_title) > 120:
                raise ValueError("Song title must be 120 characters or fewer.")
            if len(lyrics) > LYRICS_CHAR_LIMIT:
                raise ValueError(f"Lyrics must be {LYRICS_CHAR_LIMIT} characters or fewer.")
            if len(lyrics_idea) > 2500:
                raise ValueError("Lyrics brief must be 2500 characters or fewer.")
            if len(lyrics_extra) > 500:
                raise ValueError("Additional lyrics requirements must be 500 characters or fewer.")
            if not is_instrumental and not lyrics and not lyrics_optimizer:
                raise ValueError("Lyrics, a lyrics brief, or auto lyrics are required for vocal tracks.")
            extra = {key: str(form.get(key, "")).strip() for key in ("genre", "mood", "instruments", "tempo", "bpm", "key", "vocals", "structure", "references", "avoid", "use_case", "extra")}
            client_id = self.require_client_id()
            if client_id is None:
                return
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
            "lyrics_extra": lyrics_extra,
            "lyrics_language": lyrics_language,
            "interface_language": interface_language,
            "is_instrumental": is_instrumental,
            "lyrics_optimizer": lyrics_optimizer,
            "generated_lyrics": False,
            "file_name": None,
            "file_path": None,
            "error": None,
            "email_sent": False,
            "voice_id": voice_id,
            "voice_mode": voice_mode,
            "voice_wav_path": voice_wav_path,
            "extra": extra,
        }
        with JOBS_LOCK:
            sweep_jobs_locked()
            JOBS[job_id] = job
            save_jobs_locked()
        response_job = public_job(job, include_lyrics=True)
        threading.Thread(target=generate_music_with_voice, args=(job_id,), daemon=True).start()
        self.send_json({"job": response_job}, HTTPStatus.ACCEPTED)

    def handle_get_voices(self) -> None:
        """Return a list of available system voices for the TTS voice picker."""
        now = time.time()
        with VOICE_CACHE_LOCK:
            cached_voices = list(VOICE_CACHE.get("voices") or [])
            cache_age = now - float(VOICE_CACHE.get("fetched_at") or 0)
            if cached_voices and cache_age < VOICE_CACHE_TTL_SECONDS:
                self.send_json({
                    "voices": cached_voices,
                    "voice_meta": dict(VOICE_CACHE.get("voice_meta") or {}),
                    "count": len(cached_voices),
                    "fallback": bool(VOICE_CACHE.get("fallback")),
                    "cached": True,
                })
                return
        raw_meta_overrides: dict[str, dict[str, Any]] = {}
        try:
            output = run_mmx(["speech", "voices", "--output", "json", "--non-interactive", "--quiet"], timeout=int(max(1, VOICE_LIST_TIMEOUT)))
            parsed = json.loads(output)
            if isinstance(parsed, dict):
                parsed = parsed.get("voices") or parsed.get("data") or []
            voices = []
            if isinstance(parsed, list):
                for entry in parsed:
                    voice_id = ""
                    if isinstance(entry, str):
                        voice_id = str(entry).strip()
                    elif isinstance(entry, dict):
                        voice_id = str(entry.get("voice_id") or entry.get("id") or entry.get("name") or entry.get("voice") or "").strip()
                        if voice_id:
                            raw_meta_overrides[voice_id] = dict(entry)
                    if voice_id:
                        voices.append(voice_id)
        except Exception as exc:
            print(f"[voices] using fallback voice list: {exc}")
            voices = []
            with VOICE_CACHE_LOCK:
                cached_voices = list(VOICE_CACHE.get("voices") or [])
                if cached_voices:
                    self.send_json({
                        "voices": cached_voices,
                        "voice_meta": dict(VOICE_CACHE.get("voice_meta") or {}),
                        "count": len(cached_voices),
                        "fallback": bool(VOICE_CACHE.get("fallback")),
                        "cached": True,
                        "stale": True,
                    })
                    return
        if not voices:
            voices = DEFAULT_SYSTEM_VOICES
        fallback = voices == DEFAULT_SYSTEM_VOICES
        voice_meta = build_voice_metadata_map(voices, fallback=fallback)
        for voice_id, raw in raw_meta_overrides.items():
            meta = voice_meta.get(voice_id) or build_voice_metadata_map([voice_id]).get(voice_id, {"id": voice_id})
            language = str(raw.get("language") or raw.get("lang") or meta.get("language") or "").strip()
            if language:
                meta["language"] = language
                meta["language_source"] = "api"
            display_name = str(raw.get("display_name") or raw.get("label") or raw.get("title") or meta.get("display_name") or "").strip()
            if display_name:
                meta["display_name"] = display_name
            use_case = str(raw.get("use_case") or raw.get("useCase") or meta.get("use_case") or "").strip()
            if use_case:
                meta["use_case"] = use_case
            preview_supported = raw.get("preview_supported")
            if preview_supported is None:
                preview_supported = raw.get("previewSupport")
            if preview_supported is not None:
                meta["preview_supported"] = bool(preview_supported)
            unavailable_reason = str(raw.get("unavailable_reason") or raw.get("preview_error") or raw.get("previewUnavailableReason") or meta.get("unavailable_reason") or "").strip()
            if unavailable_reason:
                meta["preview_supported"] = False
                meta["unavailable_reason"] = unavailable_reason
            voice_meta[voice_id] = meta
        with VOICE_CACHE_LOCK:
            VOICE_CACHE.update({"voices": list(voices), "voice_meta": dict(voice_meta), "fallback": fallback, "fetched_at": time.time()})
        self.send_json({"voices": voices, "voice_meta": voice_meta, "count": len(voices), "fallback": fallback, "cached": False})

    def handle_voice_preview(self) -> None:
        """Handle GET /api/voice/preview?voice_id=xxx — synthesize a short speech sample with the given voice_id."""
        parsed = urllib.parse.urlparse(self.path)
        params = dict(urllib.parse.parse_qsl(parsed.query))
        raw_voice_id = str(params.get("voice_id", "")).strip()
        if not raw_voice_id:
            self.send_json({"error": "voice_id is required"}, HTTPStatus.BAD_REQUEST)
            return
        if not _is_safe_voice_id(raw_voice_id):
            self.send_json({"error": "voice_id contains invalid characters"}, HTTPStatus.BAD_REQUEST)
            return
        voice_id = raw_voice_id
        try:
            preview_lang = _detect_lang_from_voice_id(voice_id)
            preview_text = VOICE_PREVIEW_TEXTS.get(preview_lang, VOICE_PREVIEW_TEXTS["English"])
            tmp_path = OUTPUT_DIR / f"voice_preview_{secrets.token_hex(8)}.mp3"
            synthesize_speech(preview_text, voice_id, tmp_path)
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "audio/mpeg")
            self.send_header("Content-Length", str(tmp_path.stat().st_size))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            with tmp_path.open("rb") as f:
                while chunk := f.read(256 * 1024):
                    self.wfile.write(chunk)
            tmp_path.unlink(missing_ok=True)
            return
        except ValueError as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        except Exception as exc:
            print(f"[voice_preview] error for voice_id={voice_id}: {exc}")
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_GATEWAY)
            return

    def handle_voice_clone(self) -> None:
        """Handle POST /api/voice/clone — accepts multipart form with audio file."""
        try:
            client_id = self.require_client_id()
            if client_id is None:
                return
            content_type = self.headers.get("Content-Type", "")
            if "multipart/form-data" not in content_type:
                raise ValueError("Content-Type must be multipart/form-data.")
            length = int(self.headers.get("Content-Length") or "0")
            if length <= 0 or length > 25 * 1024 * 1024:
                raise ValueError("File too large or missing (max 20MB).")
            body = self.rfile.read(length)
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
            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            voice_wav_path = OUTPUT_DIR / f"voice_wav_{client_id[:16]}.wav"
            voice_wav_path.write_bytes(audio_data)
            tmp_path = OUTPUT_DIR / f"voice_sample_{secrets.token_hex(8)}{suffix}"
            tmp_path.write_bytes(audio_data)
            try:
                result = clone_voice(tmp_path, voice_id)
            finally:
                tmp_path.unlink(missing_ok=True)
            voice_id_out = result.get("data", {}).get("voice_id", voice_id)
            self.send_json({"ok": True, "voice_id": voice_id_out, "expires_in_hours": 168, "voice_wav_path": str(voice_wav_path)})
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
            prompt = str(form.get("prompt", "")).strip() or "A short natural vocal preview, clear melody, minimal backing"
            if not lyrics:
                raise ValueError("Lyrics are required.")
            if not voice_id:
                raise ValueError("voice_id is required.")
            if not re.fullmatch(r"[A-Za-z0-9_().\- ]{8,160}", voice_id):
                raise ValueError("Invalid voice_id.")
            tmp_sing = OUTPUT_DIR / f"sing_preview_{secrets.token_hex(8)}.mp3"
            client_id = self.require_client_id()
            if client_id is None:
                return
            voice_wav = OUTPUT_DIR / f"voice_wav_{client_id[:16]}.wav"
            singing_error = ""
            try:
                synthesize_voice_clone_singing(lyrics, voice_id, tmp_sing, prompt)
            except Exception as exc:
                singing_error = str(exc)
                tmp_sing.unlink(missing_ok=True)
                if not voice_wav.exists():
                    raise RuntimeError(f"voice_clone_singing unavailable and no local voice recording was found: {singing_error}") from exc
                generate_voice_cover_audio(prompt, lyrics, voice_wav, tmp_sing, {"vocals": "cloned voice singing preview"})
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "audio/mpeg")
            self.send_header("Content-Length", str(tmp_sing.stat().st_size))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Voice-Render-Mode", "voice_clone_singing" if not singing_error else "music_cover_fallback")
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
            self.send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
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
        if parsed.path == "/api/jobs/voice":
            self.handle_jobs_voice()
            return
        if parsed.path != "/api/jobs":
            self.send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
            return
        client_id = self.require_client_id()
        if client_id is None:
            return
        try:
            form = self.read_json_body()
            prompt = str(form.get("prompt", "")).strip()
            raw_song_title = str(form.get("song_title", "")).strip()
            song_title = clean_song_title(raw_song_title)
            email_addr = str(form.get("email", "")).strip()
            lyrics = str(form.get("lyrics", "")).strip()
            lyrics_idea = str(form.get("lyrics_idea", "")).strip()
            lyrics_extra = str(form.get("lyrics_extra", "")).strip()
            lyrics_language = str(form.get("lyrics_language", "auto")).strip()
            interface_language = str(form.get("interface_language", "")).strip()
            is_instrumental = bool(form.get("is_instrumental"))
            lyrics_optimizer = bool(form.get("lyrics_optimizer") or lyrics_idea) and not is_instrumental
            if not prompt:
                raise ValueError("Prompt is required.")
            if len(prompt) > 2000:
                raise ValueError("Prompt must be 2000 characters or fewer.")
            if len(raw_song_title) > 120:
                raise ValueError("Song title must be 120 characters or fewer.")
            if len(lyrics) > LYRICS_CHAR_LIMIT:
                raise ValueError(f"Lyrics must be {LYRICS_CHAR_LIMIT} characters or fewer.")
            if len(lyrics_idea) > 2500:
                raise ValueError("Lyrics brief must be 2500 characters or fewer.")
            if len(lyrics_extra) > 500:
                raise ValueError("Additional lyrics requirements must be 500 characters or fewer.")
            if not is_instrumental and not lyrics and not lyrics_optimizer:
                raise ValueError("Lyrics, a lyrics brief, or auto lyrics are required for vocal tracks.")
            extra = {key: str(form.get(key, "")).strip() for key in ("genre", "mood", "instruments", "tempo", "bpm", "key", "vocals", "structure", "references", "avoid", "use_case", "extra")}
        except ValueError as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
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
            "lyrics_extra": lyrics_extra,
            "lyrics_language": lyrics_language,
            "interface_language": interface_language,
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
            sweep_jobs_locked()
            JOBS[job_id] = job
            save_jobs_locked()
        response_job = public_job(job, include_lyrics=True)
        threading.Thread(target=generate_music, args=(job_id,), daemon=True).start()
        self.send_json({"job": response_job}, HTTPStatus.ACCEPTED)

    def do_DELETE(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path.startswith("/api/drafts/"):
            self.handle_delete_draft(parsed.path.removeprefix("/api/drafts/"))
            return
        if not parsed.path.startswith("/api/jobs/"):
            self.send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
            return
        job_id = urllib.parse.unquote(parsed.path.removeprefix("/api/jobs/"))
        client_id = self.require_client_id()
        if client_id is None:
            return
        with JOBS_LOCK:
            job = JOBS.get(job_id)
            if not job or job.get("owner_id") != client_id:
                self.send_json({"error": "Job not found"}, HTTPStatus.NOT_FOUND)
                return
            del JOBS[job_id]
            save_jobs_locked()
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

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


def _job_cleanup_loop() -> None:
    """Background daemon: periodically clean up stuck/expired jobs."""
    while True:
        time.sleep(60)
        try:
            with JOBS_LOCK:
                if sweep_jobs_locked():
                    save_jobs_locked()
        except Exception:
            pass  # Never crash the cleanup thread


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    load_jobs()
    load_drafts()
    # Start background cleanup thread (runs even when no traffic)
    cleanup_thread = threading.Thread(target=_job_cleanup_loop, daemon=True)
    cleanup_thread.start()
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
