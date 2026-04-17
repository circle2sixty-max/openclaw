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
    .logo-icon { width: 36px; height: 36px; background: var(--gradient-green); border-radius: 10px; display: flex; align-items: center; justify-content: center; font-size: 18px; }
    .header-actions { display: flex; gap: 8px; align-items: center; }
    .header-btn { display: flex; align-items: center; justify-content: center; width: 40px; height: 40px; border: none; border-radius: 50%; background: var(--bg-tertiary); color: var(--text-secondary); cursor: pointer; font-size: 18px; transition: var(--transition); }
    .header-btn:hover { background: var(--bg-elevated); color: var(--text-primary); }
    .lang-toggle { width: auto; padding: 0 14px; border-radius: 20px; font-size: 13px; font-weight: 600; }
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
    .btn-voice:hover { border-color: var(--accent); color: var(--accent); }
    .error-text { color: var(--danger); font-size: 13px; min-height: 20px; margin-top: 12px; }
    /* Jobs Panel */
    .jobs-panel { background: var(--bg-secondary); border: 1px solid var(--border); border-radius: var(--radius-lg); padding: 20px; margin-top: 24px; max-width: 900px; }
    .jobs-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
    .jobs-title { font-size: 16px; font-weight: 700; color: var(--text-primary); }
    .jobs-list { display: flex; flex-direction: column; gap: 10px; max-height: 400px; overflow-y: auto; }
    .job-card { display: flex; align-items: center; gap: 14px; padding: 14px 16px; background: var(--bg-tertiary); border: 1px solid var(--border); border-radius: var(--radius-md); transition: var(--transition); }
    .job-card:hover { border-color: var(--border-light); }
    .job-art { width: 56px; height: 56px; background: var(--gradient-green); border-radius: var(--radius-sm); display: flex; align-items: center; justify-content: center; font-size: 24px; flex-shrink: 0; }
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
    /* Bottom Player */
    .player { position: fixed; bottom: 0; left: 0; right: 0; height: 90px; background: var(--bg-secondary); border-top: 1px solid var(--border); display: flex; align-items: center; padding: 0 24px; gap: 20px; z-index: 100; }
    .player-track { display: flex; align-items: center; gap: 14px; width: 280px; flex-shrink: 0; }
    .player-art { width: 56px; height: 56px; background: var(--gradient-green); border-radius: var(--radius-sm); display: flex; align-items: center; justify-content: center; font-size: 24px; }
    .player-info { min-width: 0; }
    .player-title { font-size: 14px; font-weight: 600; color: var(--text-primary); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .player-artist { font-size: 12px; color: var(--text-muted); }
    .player-controls { flex: 1; display: flex; flex-direction: column; align-items: center; gap: 8px; }
    .player-buttons { display: flex; align-items: center; gap: 16px; }
    .player-btn { background: none; border: none; color: var(--text-secondary); cursor: pointer; font-size: 20px; padding: 8px; transition: var(--transition); }
    .player-btn:hover { color: var(--text-primary); }
    .player-btn.play { width: 40px; height: 40px; background: var(--text-primary); color: var(--bg-primary); border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 18px; }
    .player-btn.play:hover { transform: scale(1.05); }
    .player-progress { display: flex; align-items: center; gap: 10px; width: 100%; max-width: 600px; }
    .player-time { font-size: 11px; color: var(--text-muted); min-width: 40px; text-align: center; }
    .player-bar { flex: 1; height: 4px; background: var(--border); border-radius: 2px; cursor: pointer; position: relative; }
    .player-bar-fill { height: 100%; background: var(--accent); border-radius: 2px; width: 0%; transition: width 0.1s; }
    .player-bar:hover .player-bar-fill { background: var(--accent-hover); }
    .player-volume { display: flex; align-items: center; gap: 8px; width: 140px; flex-shrink: 0; }
    .volume-icon { color: var(--text-muted); font-size: 18px; cursor: pointer; }
    .volume-slider { flex: 1; height: 4px; background: var(--border); border-radius: 2px; cursor: pointer; }
    .volume-fill { height: 100%; background: var(--text-muted); border-radius: 2px; width: 70%; }
    .player-lyrics { flex: 1; max-width: 500px; overflow: hidden; text-align: center; padding: 0 20px; }
    .lyrics-text { font-size: 14px; color: var(--text-secondary); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; transition: color 0.3s; }
    .lyrics-text.playing { color: var(--accent); }
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
      .main-content { padding: 20px 16px 100px; }
      .player { padding: 0 16px; gap: 12px; }
      .player-track { width: auto; }
      .player-volume { display: none; }
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
  </style>
</head>
<body>
  <div class="app">
    <header class="app-header">
      <a href="/" class="logo">
        <div class="logo-icon">🎵</div>
        <span>Music Speaks</span>
      </a>
      <div class="header-actions">
        <button id="soundBtn" class="header-btn sound-toggle on" title="Toggle sound" onclick="toggleSound()">🔊</button>
        <button id="themeBtn" class="header-btn" title="Toggle theme">🌙</button>
        <button id="langBtn" class="header-btn lang-toggle">中文</button>
      </div>
    </header>
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
                <button class="template-btn" type="button" data-template="upbeat_pop">🎵 Upbeat Pop</button>
                <button class="template-btn" type="button" data-template="chill_ambient">🌙 Chill Ambient</button>
                <button class="template-btn" type="button" data-template="rock_anthem">🎸 Rock Anthem</button>
                <button class="template-btn" type="button" data-template="acoustic_story">🎸 Acoustic Story</button>
                <button class="template-btn" type="button" data-template="electronic_dream">💫 Electronic Dream</button>
                <button class="template-btn" type="button" data-template="hiphop_beats">🎤 Hip-Hop Beats</button>
                <button class="template-btn" type="button" data-template="cinematic_epic">🎬 Cinematic Epic</button>
                <button class="template-btn" type="button" data-template="lofi_chill">☕ Lo-Fi Chill</button>
              </div>
            </div>
            <!-- Lyrics Idea -->
            <div class="form-section">
              <label class="form-label" data-i18n="lyricsIdeaLabel">Lyrics Brief for AI (optional)</label>
              <textarea id="lyricsIdea" maxlength="2500" class="form-input" data-i18n-placeholder="lyricsIdeaPlaceholder" placeholder="Tell the story, feelings, images, language, chorus idea, or fragments you want in the lyrics."></textarea>
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
                </div>
              </div>
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
        libraryDesc: "All your generated songs in one place.", favoritesDesc: "Your liked and saved songs.", historyDesc: "Recently generated songs."
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
        libraryDesc: "你生成的所有歌曲。", favoritesDesc: "你喜欢的歌曲。", historyDesc: "最近生成的歌曲。"
      }
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
      document.getElementById("langBtn").textContent = lang === "en" ? "中文" : "EN";
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
        const title = escapeHtml(job.song_title || job.prompt || "Untitled");
        const mode = job.is_instrumental ? t("instrumentalMode") : t("vocalMode");
        const downloadUrl = job.download_url ? `${escapeHtml(job.download_url)}?client_id=${encodeURIComponent(clientId)}` : "";
        const isRunning = job.status === "running" || job.status === "queued";
        const completedClass = job.status === "completed" ? "animate-bounce-in" : "";
        const actions = job.status === "completed" && job.download_url
          ? `<button class="job-action-btn download" onclick="playJob('${escapeHtml(job.id)}')">▶ Play</button><a class="job-action-btn download" href="${downloadUrl}" download="${fileName}">${t("download")}</a>`
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
      currentTrack = { id: job.id, title: job.song_title || job.prompt || 'Untitled', url: url, lyrics: lyrics };
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
        // Play completion sound when a job transitions to completed
        newJobs.forEach(job => {
          const prev = prevJobs[job.id];
          if (prev && prev.status !== "completed" && job.status === "completed") {
            SoundSystem.play("complete");
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
      const get = id => document.getElementById(id).value.trim();
      return {
        email: get("email"), song_title: get("songTitle"), prompt: get("prompt"), lyrics: get("lyrics"), lyrics_idea: get("lyricsIdea"),
        is_instrumental: instrumental.checked, lyrics_optimizer: lyricsOptimizer.checked,
        genre: get("genre"), mood: get("mood"), instruments: get("instruments"), tempo: get("tempo"), bpm: get("bpm"), key: get("key"),
        vocals: get("vocals"), structure: get("structure"), references: get("references"), avoid: get("avoid"), use_case: get("useCase"), extra: get("extra"),
        voice_id: clonedVoiceId,
      };
    }
    function restorePayload(payload = {}) {
      const set = (id, value) => { document.getElementById(id).value = value || ""; };
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
        saveDraftSoon();
        setLyricsAssistMessage(t("lyricsGenerated"));
        generateLyricsBtn.classList.remove("animate-pulse");
        generateLyricsBtn.classList.add("animate-bounce-in");
        setTimeout(() => generateLyricsBtn.classList.remove("animate-bounce-in"), 500);
      } catch (error) {
        setLyricsAssistMessage(error.message || t("lyricsAssistFailed"), true);
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
    function setTheme(theme) {
      document.documentElement.setAttribute("data-theme", theme);
      localStorage.setItem("terry_music_theme", theme);
      themeBtn.textContent = theme === "light" ? "☀️" : "🌙";
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
      document.getElementById("soundBtn").textContent = enabled ? "🔊" : "🔇";
      document.getElementById("soundBtn").className = "header-btn sound-toggle " + (enabled ? "on" : "off");
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
      submitBtn.classList.add("animate-pulse");
      submitBtn.innerHTML = '<span class="spinner"></span> Generating...';
      const payload = collectPayload();
      const endpoint = clonedVoiceId ? "/api/jobs/voice" : "/api/jobs";
      try {
        const res = await fetch(endpoint, {method: "POST", headers: headers({"Content-Type": "application/json"}), body: JSON.stringify(payload)});
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          const errMsg = typeof data.error === "string" ? data.error : data.error?.message || data.error?.error || `HTTP ${res.status}`;
          throw new Error(errMsg);
        }
        saveDraftLocal(payload);
        await saveDraftRemote(payload).catch(() => {});
        setDraftStatus(t("draftSaved"));
        applyLang();
        syncInstrumentalFields();
        await loadJobs();
        SoundSystem.play("success");
        submitBtn.classList.remove("animate-pulse");
        submitBtn.classList.add("animate-bounce-in");
        setTimeout(() => submitBtn.classList.remove("animate-bounce-in"), 500);
      } catch (error) {
        formError.textContent = error.message;
        submitBtn.classList.remove("animate-pulse");
        submitBtn.classList.add("animate-shake");
        setTimeout(() => submitBtn.classList.remove("animate-shake"), 400);
        SoundSystem.play("error");
      } finally {
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
        const mimeType = MediaRecorder.isTypeSupported("audio/wav") ? "audio/wav" : "audio/webm";
        mediaRecorder = new MediaRecorder(segmentStream, { mimeType });
        recordedChunks = [];
        mediaRecorder.ondataavailable = e => { if (e.data.size > 0) recordedChunks.push(e.data); };
        mediaRecorder.onstop = async () => {
          const rawBlob = new Blob(recordedChunks, { type: mimeType });
          const wavBlob = mimeType === "audio/wav" ? rawBlob : await convertToWav(rawBlob);
          recordedSegments[idx] = wavBlob;
          segmentStream.getTracks().forEach(t => t.stop());
          segmentStream = null;
          if (idx + 1 < getSegments().length) {
            showReview(idx, wavBlob);
          } else {
            showAllDone();
          }
        };
        mediaRecorder.start();
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
      } catch (err) {
        body.innerHTML = `<div class="rec-done rec-error">${lang === "en" ? "Clone failed: " : "复刻失败："}${err.message}</div><div class="rec-controls-row"><button id="recModalClose2" class="secondary-btn" type="button">${lang === "en" ? "Close" : "关闭"}</button></div>`;
        SoundSystem.play("error");
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
    result = {key: job.get(key) for key in ("id", "status", "created_at", "updated_at", "prompt", "song_title", "generated_title", "title_error", "email", "is_instrumental", "lyrics_optimizer", "file_name", "error", "email_sent")}
    if job.get("status") == "completed" and job.get("file_path"):
        result["download_url"] = f"/download/{urllib.parse.quote(str(job['id']))}"
    return result


def admin_job(job: dict[str, Any]) -> dict[str, Any]:
    result = public_job(job)
    result.update({
        "owner_id": job.get("owner_id"),
        "lyrics": job.get("lyrics", ""),
        "lyrics_idea": job.get("lyrics_idea", ""),
        "generated_lyrics": bool(job.get("generated_lyrics")),
        "extra": job.get("extra", {}),
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
        mark_job(job_id, status="completed", file_name=file_name, file_path=str(out_path))
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
        mark_job(job_id, status="completed", file_name=file_name, file_path=str(out_path))
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
            self.send_json({
                "ok": True,
                "minimax_configured": bool(MINIMAX_API_KEY),
                "admin_configured": bool(ADMIN_KEY),
                "title_fallback": True,
                "drafts": True,
                "smtp_configured": bool(SMTP_USER and SMTP_PASSWORD),
                "smtp_host": SMTP_HOST,
                "smtp_port": SMTP_PORT,
            })
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
            with JOBS_LOCK:
                jobs = sorted(
                    [public_job(job) for job in JOBS.values() if job.get("owner_id") == client_id],
                    key=lambda item: str(item.get("created_at", "")),
                    reverse=True,
                )
            self.send_json({"jobs": jobs})
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
