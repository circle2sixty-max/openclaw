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
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
      background: var(--bg-primary);
      color: var(--text-primary);
      min-height: 100vh;
      overflow-x: hidden;
    }
    /* App Layout */
    .app { display: flex; min-height: 100vh; }
    .sidebar {
      width: 280px;
      background: var(--bg-secondary);
      border-right: 1px solid var(--border);
      display: flex;
      flex-direction: column;
      position: fixed;
      top: 0; left: 0; bottom: 0;
      z-index: 100;
    }
    .sidebar-header {
      padding: 24px 20px;
      border-bottom: 1px solid var(--border);
    }
    .logo {
      font-family: 'Space Grotesk', sans-serif;
      font-size: 22px;
      font-weight: 700;
      color: var(--text-primary);
      display: flex;
      align-items: center;
      gap: 10px;
    }
    .logo-icon {
      width: 36px; height: 36px;
      background: var(--gradient-green);
      border-radius: var(--radius-sm);
      display: flex; align-items: center; justify-content: center;
      font-size: 18px;
    }
    .sidebar-nav { flex: 1; padding: 16px 12px; }
    .nav-item {
      display: flex; align-items: center; gap: 12px;
      padding: 12px 16px;
      border-radius: var(--radius-md);
      color: var(--text-secondary);
      cursor: pointer;
      transition: var(--transition);
      margin-bottom: 4px;
      font-weight: 500;
    }
    .nav-item:hover { background: var(--bg-tertiary); color: var(--text-primary); }
    .nav-item.active { background: var(--accent-dim); color: var(--accent); }
    .nav-item .icon { font-size: 20px; }
    .sidebar-footer { padding: 16px 20px; border-top: 1px solid var(--border); }
    .theme-toggle {
      display: flex; align-items: center; justify-content: space-between;
      padding: 12px 16px;
      background: var(--bg-tertiary);
      border-radius: var(--radius-md);
      cursor: pointer;
    }
    .theme-toggle span { font-size: 14px; font-weight: 500; }
    .main-content {
      flex: 1;
      margin-left: 280px;
      padding: 32px 48px;
      padding-bottom: 140px;
    }
    /* Header */
    .header {
      display: flex; justify-content: space-between; align-items: center;
      margin-bottom: 40px;
    }
    .header-left h1 {
      font-family: 'Space Grotesk', sans-serif;
      font-size: 36px;
      font-weight: 700;
      margin-bottom: 8px;
    }
    .header-left p { color: var(--text-secondary); font-size: 15px; }
    .header-actions { display: flex; gap: 12px; align-items: center; }
    .version-badge {
      background: var(--bg-tertiary);
      padding: 8px 16px;
      border-radius: 20px;
      font-size: 12px;
      font-weight: 600;
      color: var(--accent);
    }
    .lang-btn {
      background: var(--bg-tertiary);
      border: 1px solid var(--border);
      color: var(--text-primary);
      padding: 10px 20px;
      border-radius: 20px;
      cursor: pointer;
      font-weight: 600;
      transition: var(--transition);
    }
    .lang-btn:hover { background: var(--bg-elevated); }
    /* Create Section */
    .create-section {
      background: var(--bg-secondary);
      border: 1px solid var(--border);
      border-radius: var(--radius-lg);
      padding: 32px;
      margin-bottom: 32px;
    }
    .section-title {
      font-size: 20px;
      font-weight: 700;
      margin-bottom: 24px;
      display: flex; align-items: center; gap: 12px;
    }
    .section-title .icon { font-size: 24px; }
    /* Form Styles */
    .form-row { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-bottom: 20px; }
    .form-row.full { grid-template-columns: 1fr; }
    .field { margin-bottom: 0; }
    .field label {
      display: block;
      font-size: 13px;
      font-weight: 600;
      color: var(--text-secondary);
      margin-bottom: 8px;
      text-transform: uppercase;
      letter-spacing: 0.5px;
    }
    .field input, .field textarea, .field select {
      width: 100%;
      background: var(--bg-tertiary);
      border: 1px solid var(--border);
      border-radius: var(--radius-md);
      color: var(--text-primary);
      font-family: inherit;
      font-size: 15px;
      padding: 14px 16px;
      transition: var(--transition);
    }
    .field input:focus, .field textarea:focus, .field select:focus {
      outline: none;
      border-color: var(--accent);
      box-shadow: 0 0 0 3px var(--accent-dim);
    }
    .field textarea { min-height: 120px; resize: vertical; line-height: 1.6; }
    .field .hint {
      font-size: 12px;
      color: var(--text-muted);
      margin-top: 6px;
    }
    /* Checkbox */
    .checks { display: flex; gap: 16px; margin: 20px 0; }
    .check {
      display: flex; align-items: center; gap: 12px;
      padding: 14px 20px;
      background: var(--bg-tertiary);
      border: 1px solid var(--border);
      border-radius: var(--radius-md);
      cursor: pointer;
      flex: 1;
      transition: var(--transition);
    }
    .check:hover { border-color: var(--accent); }
    .check input { width: 20px; height: 20px; accent-color: var(--accent); }
    .check-content { flex: 1; }
    .check-content span { display: block; font-weight: 600; font-size: 14px; }
    .check-content small { color: var(--text-muted); font-size: 12px; margin-top: 2px; }
    /* Templates */
    .templates { margin: 24px 0; }
    .templates-label {
      font-size: 13px;
      font-weight: 600;
      color: var(--text-secondary);
      margin-bottom: 12px;
      text-transform: uppercase;
      letter-spacing: 0.5px;
    }
    .template-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; }
    .template-btn {
      padding: 16px;
      background: var(--bg-tertiary);
      border: 1px solid var(--border);
      border-radius: var(--radius-md);
      color: var(--text-primary);
      cursor: pointer;
      transition: var(--transition);
      text-align: left;
    }
    .template-btn:hover { border-color: var(--accent); background: var(--accent-dim); }
    .template-btn.active { border-color: var(--accent); background: var(--accent-dim); }
    .template-btn .emoji { font-size: 24px; margin-bottom: 8px; display: block; }
    .template-btn .name { font-weight: 600; font-size: 14px; display: block; }
    .template-btn .desc { font-size: 11px; color: var(--text-muted); margin-top: 4px; display: block; }
    /* Voice Clone */
    .voice-clone { margin: 24px 0; }
    .voice-btn {
      display: inline-flex; align-items: center; gap: 10px;
      padding: 14px 24px;
      background: var(--bg-tertiary);
      border: 1px solid var(--border);
      border-radius: var(--radius-md);
      color: var(--text-primary);
      cursor: pointer;
      font-weight: 600;
      transition: var(--transition);
    }
    .voice-btn:hover { border-color: var(--accent); background: var(--accent-dim); }
    .voice-btn.cloned { border-color: var(--accent); color: var(--accent); }
    .voice-status { margin-left: 16px; font-size: 13px; color: var(--text-muted); }
    .voice-status.ready { color: var(--accent); }
    /* Advanced Parameters */
    .advanced-toggle {
      display: flex; align-items: center; gap: 8px;
      color: var(--text-secondary);
      cursor: pointer;
      font-size: 14px;
      font-weight: 600;
      margin: 20px 0;
      padding: 12px 0;
      border-top: 1px solid var(--border);
    }
    .advanced-toggle .arrow { transition: transform 0.2s; }
    .advanced-toggle.open .arrow { transform: rotate(180deg); }
    .advanced-panel { display: none; padding-top: 16px; }
    .advanced-panel.open { display: block; }
    .param-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; }
    /* Actions */
    .form-actions { display: flex; gap: 16px; margin-top: 28px; padding-top: 24px; border-top: 1px solid var(--border); }
    .btn-primary {
      background: var(--gradient-green);
      color: #000;
      border: none;
      padding: 16px 40px;
      border-radius: 30px;
      font-size: 16px;
      font-weight: 700;
      cursor: pointer;
      transition: var(--transition);
      display: flex; align-items: center; gap: 10px;
    }
    .btn-primary:hover { transform: translateY(-2px); box-shadow: var(--shadow-md); }
    .btn-primary:disabled { opacity: 0.6; cursor: not-allowed; transform: none; }
    .btn-secondary {
      background: transparent;
      color: var(--text-primary);
      border: 1px solid var(--border);
      padding: 16px 32px;
      border-radius: 30px;
      font-size: 15px;
      font-weight: 600;
      cursor: pointer;
      transition: var(--transition);
    }
    .btn-secondary:hover { background: var(--bg-tertiary); }
    .error-text { color: var(--danger); font-size: 14px; margin-top: 12px; }
    /* Library Section */
    .library-section { margin-top: 40px; }
    .library-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 24px; }
    .library-tabs { display: flex; gap: 8px; }
    .tab {
      padding: 10px 20px;
      background: transparent;
      border: 1px solid var(--border);
      border-radius: 20px;
      color: var(--text-secondary);
      cursor: pointer;
      font-weight: 600;
      transition: var(--transition);
    }
    .tab:hover { background: var(--bg-tertiary); }
    .tab.active { background: var(--accent); border-color: var(--accent); color: #000; }
    /* Track List */
    .track-list { display: flex; flex-direction: column; gap: 8px; }
    .track {
      display: flex; align-items: center; gap: 16px;
      padding: 16px 20px;
      background: var(--bg-secondary);
      border: 1px solid var(--border);
      border-radius: var(--radius-md);
      cursor: pointer;
      transition: var(--transition);
    }
    .track:hover { background: var(--bg-tertiary); border-color: var(--border-light); }
    .track.playing { border-color: var(--accent); background: var(--accent-dim); }
    .track-art {
      width: 56px; height: 56px;
      background: var(--bg-tertiary);
      border-radius: var(--radius-sm);
      display: flex; align-items: center; justify-content: center;
      font-size: 24px;
      flex-shrink: 0;
    }
    .track-info { flex: 1; min-width: 0; }
    .track-title { font-weight: 600; margin-bottom: 4px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .track-meta { display: flex; gap: 12px; font-size: 12px; color: var(--text-muted); }
    .track-meta span { display: flex; align-items: center; gap: 4px; }
    .track-actions { display: flex; gap: 8px; align-items: center; }
    .track-action {
      width: 36px; height: 36px;
      display: flex; align-items: center; justify-content: center;
      border-radius: 50%;
      background: var(--bg-tertiary);
      border: none;
      cursor: pointer;
      transition: var(--transition);
      font-size: 16px;
    }
    .track-action:hover { background: var(--bg-elevated); }
    .track-action.liked { color: var(--danger); }
    .track-badge {
      padding: 4px 12px;
      border-radius: 12px;
      font-size: 11px;
      font-weight: 700;
      text-transform: uppercase;
    }
    .badge.completed { background: var(--accent-dim); color: var(--accent); }
    .badge.running { background: rgba(255, 171, 0, 0.15); color: var(--warning); }
    .badge.error { background: rgba(255, 82, 82, 0.15); color: var(--danger); }
    .badge.queued { background: var(--bg-tertiary); color: var(--text-muted); }
    /* Progress Bar */
    .progress-container { margin-top: 8px; }
    .progress-bar { height: 4px; background: var(--bg-tertiary); border-radius: 2px; overflow: hidden; }
    .progress-fill { height: 100%; background: var(--accent); transition: width 0.3s; }
    .progress-text { font-size: 11px; color: var(--text-muted); margin-top: 4px; }
    /* Empty State */
    .empty-state {
      text-align: center;
      padding: 60px 40px;
      background: var(--bg-secondary);
      border: 1px dashed var(--border);
      border-radius: var(--radius-lg);
    }
    .empty-icon { font-size: 48px; margin-bottom: 16px; opacity: 0.5; }
    .empty-state h3 { font-size: 18px; margin-bottom: 8px; }
    .empty-state p { color: var(--text-muted); }
    /* Player Bar */
    .player-bar {
      position: fixed;
      bottom: 0; left: 0; right: 0;
      height: 90px;
      background: var(--bg-secondary);
      border-top: 1px solid var(--border);
      display: flex;
      align-items: center;
      padding: 0 24px;
      z-index: 200;
    }
    .player-track {
      display: flex; align-items: center; gap: 16px;
      width: 280px;
      flex-shrink: 0;
    }
    .player-art {
      width: 56px; height: 56px;
      background: var(--bg-tertiary);
      border-radius: var(--radius-sm);
      display: flex; align-items: center; justify-content: center;
      font-size: 24px;
    }
    .player-info { flex: 1; min-width: 0; }
    .player-title { font-weight: 600; font-size: 14px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .player-artist { font-size: 12px; color: var(--text-muted); }
    .player-controls { flex: 1; display: flex; flex-direction: column; align-items: center; gap: 8px; }
    .player-buttons { display: flex; align-items: center; gap: 16px; }
    .player-btn {
      background: none; border: none;
      color: var(--text-secondary);
      cursor: pointer;
      font-size: 20px;
      transition: var(--transition);
      display: flex; align-items: center; justify-content: center;
    }
    .player-btn:hover { color: var(--text-primary); }
    .player-btn.play { width: 40px; height: 40px; background: var(--text-primary); color: #000; border-radius: 50%; font-size: 18px; }
    .player-btn.play:hover { transform: scale(1.05); }
    .player-progress { display: flex; align-items: center; gap: 12px; width: 600px; }
    .player-time { font-size: 11px; color: var(--text-muted); width: 40px; text-align: center; }
    .player-slider { flex: 1; height: 4px; background: var(--bg-tertiary); border-radius: 2px; cursor: pointer; position: relative; }
    .player-slider-fill { height: 100%; background: var(--accent); border-radius: 2px; width: 0%; }
    .player-slider:hover .player-slider-fill { background: var(--accent-hover); }
    .player-volume { display: flex; align-items: center; gap: 8px; width: 180px; justify-content: flex-end; }
    .volume-slider { width: 100px; height: 4px; background: var(--bg-tertiary); border-radius: 2px; cursor: pointer; }
    .volume-fill { height: 100%; background: var(--text-secondary); border-radius: 2px; width: 70%; }
    /* Modal */
    .modal-overlay {
      position: fixed; inset: 0;
      background: rgba(0,0,0,0.8);
      backdrop-filter: blur(8px);
      z-index: 1000;
      display: none;
      align-items: center; justify-content: center;
    }
    .modal-overlay.open { display: flex; }
    .modal {
      background: var(--bg-secondary);
      border: 1px solid var(--border);
      border-radius: var(--radius-lg);
      width: min(600px, 95vw);
      max-height: 90vh;
      overflow-y: auto;
    }
    .modal-header {
      padding: 24px;
      border-bottom: 1px solid var(--border);
      display: flex; justify-content: space-between; align-items: center;
    }
    .modal-header h2 { font-size: 20px; font-weight: 700; }
    .modal-close {
      width: 32px; height: 32px;
      display: flex; align-items: center; justify-content: center;
      background: var(--bg-tertiary);
      border: none; border-radius: 50%;
      cursor: pointer; font-size: 18px;
      color: var(--text-secondary);
    }
    .modal-close:hover { background: var(--bg-elevated); color: var(--text-primary); }
    .modal-body { padding: 24px; }
    /* Recording UI */
    .recording-step { margin-bottom: 24px; }
    .recording-progress { margin-bottom: 16px; }
    .recording-step-label { font-weight: 600; margin-bottom: 8px; color: var(--accent); }
    .recording-bar { height: 6px; background: var(--bg-tertiary); border-radius: 3px; overflow: hidden; }
    .recording-bar-fill { height: 100%; background: var(--accent); transition: width 0.3s; }
    .recording-script {
      background: var(--bg-tertiary);
      padding: 20px;
      border-radius: var(--radius-md);
      margin-bottom: 20px;
    }
    .recording-script p { font-size: 14px; color: var(--text-muted); margin-bottom: 8px; }
    .recording-script text { font-size: 18px; line-height: 1.5; }
    .recording-countdown { font-size: 48px; font-weight: 700; text-align: center; color: var(--accent); margin: 20px 0; }
    .recording-actions { display: flex; gap: 12px; justify-content: center; }
    .recording-done { text-align: center; padding: 40px; }
    .recording-done .icon { font-size: 64px; margin-bottom: 16px; }
    .recording-done h3 { font-size: 24px; margin-bottom: 8px; color: var(--accent); }
    /* Responsive */
    @media (max-width: 1200px) {
      .sidebar { width: 240px; }
      .main-content { margin-left: 240px; }
      .template-grid { grid-template-columns: repeat(2, 1fr); }
      .param-grid { grid-template-columns: repeat(2, 1fr); }
    }
    @media (max-width: 900px) {
      .sidebar { transform: translateX(-100%); }
      .main-content { margin-left: 0; padding: 24px; }
      .form-row { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <div class="app">
    <!-- Sidebar -->
    <aside class="sidebar">
      <div class="sidebar-header">
        <div class="logo">
          <div class="logo-icon">🎵</div>
          <span>Music Speaks</span>
        </div>
      </div>
      <nav class="sidebar-nav">
        <div class="nav-item active" data-view="create">
          <span class="icon">✨</span>
          <span>Create</span>
        </div>
        <div class="nav-item" data-view="library">
          <span class="icon">📚</span>
          <span>Library</span>
        </div>
        <div class="nav-item" data-view="favorites">
          <span class="icon">❤️</span>
          <span>Favorites</span>
        </div>
        <div class="nav-item" data-view="queue">
          <span class="icon">📋</span>
          <span>Queue</span>
        </div>
      </nav>
      <div class="sidebar-footer">
        <div class="theme-toggle" id="themeToggle">
          <span>🌙 Dark Mode</span>
          <span>☀️</span>
        </div>
      </div>
    </aside>

    <!-- Main Content -->
    <main class="main-content">
      <!-- Header -->
      <header class="header">
        <div class="header-left">
          <h1 id="pageTitle">Create Music</h1>
          <p id="pageSubtitle">Transform your ideas into songs with AI</p>
        </div>
        <div class="header-actions">
          <div class="version-badge">v2.6</div>
          <button class="lang-btn" id="langBtn">中文</button>
        </div>
      </header>

      <!-- Create Section -->
      <section class="create-section" id="createView">
        <h2 class="section-title"><span class="icon">✨</span> <span data-i18n="createTitle">Create New Track</span></h2>
        <form id="jobForm">
          <div class="form-row">
            <div class="field">
              <label data-i18n="promptLabel">Music Style Prompt</label>
              <input type="text" id="prompt" required placeholder="Describe the music you want to create...">
              <div class="hint" data-i18n="promptHint">Include genre, mood, instruments, tempo</div>
            </div>
            <div class="field">
              <label data-i18n="titleLabel">Song Title (optional)</label>
              <input type="text" id="songTitle" placeholder="Leave empty for AI to generate">
            </div>
          </div>

          <div class="form-row full">
            <div class="field">
              <label data-i18n="lyricsLabel">Lyrics (optional)</label>
              <textarea id="lyrics" placeholder="[Verse]&#10;Your lyrics here...&#10;[Chorus]&#10;Your chorus..."></textarea>
            </div>
          </div>

          <div class="form-row">
            <div class="field">
              <label data-i18n="lyricsIdeaLabel">Lyrics Brief (optional)</label>
              <input type="text" id="lyricsIdea" placeholder="Tell a story or describe the mood...">
            </div>
            <div class="field">
              <label>Genre</label>
              <input type="text" id="genre" placeholder="pop, rock, jazz...">
            </div>
          </div>

          <div class="form-row">
            <div class="field">
              <label>Mood</label>
              <input type="text" id="mood" placeholder="happy, melancholic, energetic...">
            </div>
            <div class="field">
              <label>Instruments</label>
              <input type="text" id="instruments" placeholder="guitar, piano, drums...">
            </div>
          </div>

          <div class="checks">
            <label class="check">
              <input type="checkbox" id="instrumental">
              <div class="check-content">
                <span data-i18n="instrumental">Instrumental</span>
                <small>No vocals, lyrics ignored</small>
              </div>
            </label>
            <label class="check">
              <input type="checkbox" id="lyricsOptimizer">
              <div class="check-content">
                <span data-i18n="autoLyrics">Auto-generate Lyrics</span>
                <small>AI writes lyrics from prompt</small>
              </div>
            </label>
          </div>

          <!-- Templates -->
          <div class="templates">
            <div class="templates-label">Quick Start Templates</div>
            <div class="template-grid">
              <button type="button" class="template-btn" data-template="upbeat_pop">
                <span class="emoji">🎵</span>
                <span class="name">Upbeat Pop</span>
                <span class="desc">Catchy, happy energy</span>
              </button>
              <button type="button" class="template-btn" data-template="chill_ambient">
                <span class="emoji">🌙</span>
                <span class="name">Chill Ambient</span>
                <span class="desc">Relaxing atmosphere</span>
              </button>
              <button type="button" class="template-btn" data-template="rock_anthem">
                <span class="emoji">🎸</span>
                <span class="name">Rock Anthem</span>
                <span class="desc">Powerful, epic</span>
              </button>
              <button type="button" class="template-btn" data-template="cinematic">
                <span class="emoji">🎬</span>
                <span class="name">Cinematic</span>
                <span class="desc">Movie-like score</span>
              </button>
              <button type="button" class="template-btn" data-template="electronic">
                <span class="emoji">💫</span>
                <span class="name">Electronic</span>
                <span class="desc">Synth, futuristic</span>
              </button>
              <button type="button" class="template-btn" data-template="hiphop">
                <span class="emoji">🎤</span>
                <span class="name">Hip-Hop</span>
                <span class="desc">808s, modern beats</span>
              </button>
              <button type="button" class="template-btn" data-template="acoustic">
                <span class="emoji">🎸</span>
                <span class="name">Acoustic</span>
                <span class="desc">Folk, intimate</span>
              </button>
              <button type="button" class="template-btn" data-template="lofi">
                <span class="emoji">☕</span>
                <span class="name">Lo-Fi</span>
                <span class="desc">Chill, nostalgic</span>
              </button>
            </div>
          </div>

          <!-- Voice Clone -->
          <div class="voice-clone">
            <button type="button" class="voice-btn" id="voiceRecordBtn">
              <span>🎙️</span>
              <span data-i18n="voiceRecord">Record Voice</span>
            </button>
            <span class="voice-status" id="voiceStatus"></span>
          </div>

          <!-- Advanced -->
          <div class="advanced-toggle" id="advancedToggle">
            <span class="arrow">▼</span>
            <span>Advanced Parameters</span>
          </div>
          <div class="advanced-panel" id="advancedPanel">
            <div class="param-grid">
              <div class="field"><label>Tempo</label><input type="text" id="tempo" placeholder="fast, slow, moderate"></div>
              <div class="field"><label>BPM</label><input type="number" id="bpm" placeholder="85" min="40" max="240"></div>
              <div class="field"><label>Key</label><input type="text" id="key" placeholder="C major, A minor"></div>
              <div class="field"><label>Vocal Style</label><input type="text" id="vocals" placeholder="male, female, duet"></div>
              <div class="field"><label>Structure</label><input type="text" id="structure" placeholder="verse-chorus-bridge"></div>
              <div class="field"><label>Use Case</label><input type="text" id="useCase" placeholder="video, podcast, game"></div>
            </div>
          </div>

          <div class="form-actions">
            <button type="submit" class="btn-primary" id="submitBtn">
              <span>✨</span>
              <span data-i18n="submit">Generate Music</span>
            </button>
            <button type="button" class="btn-secondary" id="clearDraftBtn">Clear</button>
          </div>
          <div class="error-text" id="formError"></div>
        </form>
      </section>

      <!-- Library Section -->
      <section class="library-section" id="libraryView" style="display:none;">
        <div class="library-header">
          <h2 class="section-title"><span class="icon">📚</span> Your Library</h2>
          <div class="library-tabs">
            <button class="tab active" data-filter="all">All</button>
            <button class="tab" data-filter="completed">Completed</button>
            <button class="tab" data-filter="processing">Processing</button>
          </div>
        </div>
        <div class="track-list" id="trackList"></div>
      </section>
    </main>

    <!-- Player Bar -->
    <div class="player-bar" id="playerBar" style="display:none;">
      <div class="player-track">
        <div class="player-art" id="playerArt">🎵</div>
        <div class="player-info">
          <div class="player-title" id="playerTitle">-</div>
          <div class="player-artist" id="playerArtist">-</div>
        </div>
      </div>
      <div class="player-controls">
        <div class="player-buttons">
          <button class="player-btn">⏮</button>
          <button class="player-btn play" id="playPauseBtn">▶</button>
          <button class="player-btn">⏭</button>
        </div>
        <div class="player-progress">
          <span class="player-time" id="currentTime">0:00</span>
          <div class="player-slider" id="progressSlider">
            <div class="player-slider-fill" id="progressFill"></div>
          </div>
          <span class="player-time" id="totalTime">0:00</span>
        </div>
      </div>
      <div class="player-volume">
        <button class="player-btn">🔊</button>
        <div class="volume-slider">
          <div class="volume-fill" id="volumeFill"></div>
        </div>
      </div>
    </div>
  </div>

  <!-- Recording Modal -->
  <div class="modal-overlay" id="recModal">
    <div class="modal">
      <div class="modal-header">
        <h2>Record Your Voice</h2>
        <button class="modal-close" id="recModalClose">✕</button>
      </div>
      <div class="modal-body" id="recModalBody"></div>
    </div>
  </div>

  <script>
    // App State
    const state = {
      lang: "en",
      currentView: "create",
      jobs: [],
      currentTrack: null,
      isPlaying: false,
      audio: new Audio(),
      likedTracks: JSON.parse(localStorage.getItem("liked_tracks") || "[]"),
    };

    // I18N
    const I18N = {
      en: {
        createTitle: "Create New Track",
        createDesc: "Transform your ideas into songs with AI",
        promptLabel: "Music Style Prompt",
        promptHint: "Include genre, mood, instruments, tempo",
        titleLabel: "Song Title (optional)",
        lyricsLabel: "Lyrics (optional)",
        lyricsIdeaLabel: "Lyrics Brief (optional)",
        instrumental: "Instrumental",
        autoLyrics: "Auto-generate Lyrics",
        voiceRecord: "Record Voice",
        submit: "Generate Music",
        pageTitleCreate: "Create Music",
        pageTitleLibrary: "Your Library",
        emptyLibrary: "No tracks yet. Create your first one!",
        generating: "Generating...",
        queued: "Queued",
        completed: "Completed",
        error: "Error",
        download: "Download",
        delete: "Delete",
        instrumentalMode: "Instrumental",
        vocalMode: "Vocal",
      },
      zh: {
        createTitle: "创建新曲目",
        createDesc: "用 AI 将你的想法变成歌曲",
        promptLabel: "音乐风格描述",
        promptHint: "包含流派、情绪、乐器、节奏",
        titleLabel: "歌曲标题（可选）",
        lyricsLabel: "歌词（可选）",
        lyricsIdeaLabel: "歌词需求描述（可选）",
        instrumental: "纯音乐",
        autoLyrics: "自动生成歌词",
        voiceRecord: "录制声音",
        submit: "生成音乐",
        pageTitleCreate: "创建音乐",
        pageTitleLibrary: "音乐库",
        emptyLibrary: "还没有曲目，创建一个吧！",
        generating: "生成中...",
        queued: "排队中",
        completed: "已完成",
        error: "错误",
        download: "下载",
        delete: "删除",
        instrumentalMode: "纯音乐",
        vocalMode: "有人声",
      },
    };

    function t(key) { return I18N[state.lang][key] || key; }

    // Templates
    const TEMPLATES = {
      upbeat_pop: { prompt: "Upbeat pop song with catchy melody, bright synthesizer, driving drum beat, feel-good energy", genre: "pop", mood: "happy, energetic" },
      chill_ambient: { prompt: "Chill ambient electronic music, soft pad drones, gentle arpeggios, relaxed atmosphere", genre: "ambient", mood: "calm, peaceful" },
      rock_anthem: { prompt: "Epic rock anthem with powerful guitar riffs, thunderous drums, soaring vocals", genre: "rock", mood: "powerful, intense" },
      cinematic: { prompt: "Epic cinematic orchestral music with sweeping strings, powerful brass, dramatic percussion", genre: "cinematic", mood: "epic, dramatic" },
      electronic: { prompt: "Electronic music with lush synth layers, ethereal atmosphere, floating pads, futuristic", genre: "electronic", mood: "futuristic, dreamy" },
      hiphop: { prompt: "Modern hip-hop beat with punchy 808 drums, atmospheric pads, bass-heavy groove", genre: "hip-hop", mood: "cool, confident" },
      acoustic: { prompt: "Acoustic folk song with intimate storytelling, fingerpicked guitar, warm vocals", genre: "folk", mood: "warm, nostalgic" },
      lofi: { prompt: "Lo-fi chillhop beat with vinyl crackle, jazz-inspired piano, relaxed drums", genre: "lo-fi", mood: "relaxed, nostalgic" },
    };

    // DOM Elements
    const $ = id => document.getElementById(id);
    const $$ = sel => document.querySelectorAll(sel);

    // Initialize
    function init() {
      bindEvents();
      loadJobs();
      setInterval(loadJobs, 3000);
      applyLang();
    }

    function bindEvents() {
      // Navigation
      $$(".nav-item").forEach(item => {
        item.addEventListener("click", () => switchView(item.dataset.view));
      });

      // Theme
      $("themeToggle").addEventListener("click", toggleTheme);

      // Language
      $("langBtn").addEventListener("click", () => {
        state.lang = state.lang === "en" ? "zh" : "en";
        applyLang();
      });

      // Templates
      $$(".template-btn").forEach(btn => {
        btn.addEventListener("click", () => applyTemplate(btn.dataset.template));
      });

      // Advanced toggle
      $("advancedToggle").addEventListener("click", () => {
        $("advancedToggle").classList.toggle("open");
        $("advancedPanel").classList.toggle("open");
      });

      // Form
      $("jobForm").addEventListener("submit", handleSubmit);
      $("clearDraftBtn").addEventListener("click", clearForm);

      // Voice recording
      $("voiceRecordBtn").addEventListener("click", openVoiceRecorder);
      $("recModalClose").addEventListener("click", closeRecModal);

      // Tabs
      $$(".tab").forEach(tab => {
        tab.addEventListener("click", () => filterTracks(tab.dataset.filter));
      });

      // Player
      $("playPauseBtn").addEventListener("click", togglePlay);
      $("progressSlider").addEventListener("click", seekAudio);
    }

    function switchView(view) {
      state.currentView = view;
      $$(".nav-item").forEach(n => n.classList.toggle("active", n.dataset.view === view));
      $("createView").style.display = view === "create" ? "block" : "none";
      $("libraryView").style.display = view === "library" ? "block" : "none";
      $("pageTitle").textContent = view === "create" ? t("pageTitleCreate") : t("pageTitleLibrary");
      if (view !== "create") loadJobs();
    }

    function toggleTheme() {
      document.body.classList.toggle("light");
    }

    function applyLang() {
      $("langBtn").textContent = state.lang === "en" ? "中文" : "EN";
      $$("[data-i18n]").forEach(el => el.textContent = t(el.dataset.i18n));
    }

    function applyTemplate(key) {
      const tmpl = TEMPLATES[key];
      if (!tmpl) return;
      $("prompt").value = tmpl.prompt;
      if (tmpl.genre) $("genre").value = tmpl.genre;
      if (tmpl.mood) $("mood").value = tmpl.mood;
      $$(".template-btn").forEach(b => b.classList.remove("active"));
      document.querySelector(`[data-template="${key}"]`).classList.add("active");
    }

    async function handleSubmit(e) {
      e.preventDefault();
      const btn = $("submitBtn");
      btn.disabled = true;
      btn.innerHTML = "<span>⏳</span><span>" + t("generating") + "</span>";

      const payload = {
        prompt: $("prompt").value.trim(),
        song_title: $("songTitle").value.trim(),
        lyrics: $("lyrics").value.trim(),
        lyrics_idea: $("lyricsIdea").value.trim(),
        is_instrumental: $("instrumental").checked,
        lyrics_optimizer: $("lyricsOptimizer").checked,
        genre: $("genre").value.trim(),
        mood: $("mood").value.trim(),
        instruments: $("instruments").value.trim(),
      };

      try {
        const res = await fetch("/api/jobs", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error);
        loadJobs();
        clearForm();
      } catch (err) {
        $("formError").textContent = err.message;
      } finally {
        btn.disabled = false;
        btn.innerHTML = "<span>✨</span><span>" + t("submit") + "</span>";
      }
    }

    function clearForm() {
      $("jobForm").reset();
      $$(".template-btn").forEach(b => b.classList.remove("active"));
    }

    async function loadJobs() {
      try {
        const res = await fetch("/api/jobs");
        const data = await res.json();
        state.jobs = data.jobs || [];
        renderLibrary();
      } catch (e) {
        console.error("Failed to load jobs:", e);
      }
    }

    function renderLibrary() {
      const list = $("trackList");
      if (!state.jobs.length) {
        list.innerHTML = '<div class="empty-state"><div class="empty-icon">🎵</div><h3>' + t("emptyLibrary") + '</h3></div>';
        return;
      }
      list.innerHTML = state.jobs.map(job => {
        const isLiked = state.likedTracks.includes(job.id);
        const statusClass = job.status === "completed" ? "completed" : job.status === "running" ? "running" : job.status === "queued" ? "queued" : "error";
        return '<div class="track' + (state.currentTrack === job.id ? " playing" : "") + '" data-id="' + job.id + '">' +
          '<div class="track-art">🎵</div>' +
          '<div class="track-info">' +
            '<div class="track-title">' + escapeHtml(job.song_title || job.prompt || "Untitled") + '</div>' +
            '<div class="track-meta">' +
              '<span>' + (job.is_instrumental ? t("instrumentalMode") : t("vocalMode")) + '</span>' +
              '<span>' + formatDate(job.created_at) + '</span>' +
              (job.status === "running" || job.status === "queued" ? '<span class="badge ' + statusClass + '">' + t(job.status) + '</span>' : "") +
            '</div>' +
            (job.status === "running" || job.status === "queued" ? '<div class="progress-container"><div class="progress-bar"><div class="progress-fill" style="width:' + (job.status === "queued" ? "10" : "60") + '%"></div></div></div>' : "") +
          '</div>' +
          '<div class="track-actions">' +
            (job.status === "completed" ? '<button class="track-action" onclick="playTrack(\'' + job.id + '\')">▶</button>' : "") +
            '<button class="track-action' + (isLiked ? " liked" : "") + '" onclick="toggleLike(\'' + job.id + '\')">' + (isLiked ? "❤️" : "🤍") + '</button>' +
            (job.status === "completed" && job.download_url ? '<a class="track-action" href="' + job.download_url + '" download>⬇️</a>' : "") +
            (job.status !== "running" && job.status !== "queued" ? '<button class="track-action" onclick="deleteJob(\'' + job.id + '\')">🗑️</button>' : "") +
          '</div>' +
        '</div>';
      }).join("");
    }

    function filterTracks(filter) {
      $$(".tab").forEach(t => t.classList.toggle("active", t.dataset.filter === filter));
      // Apply filter logic here
    }

    function playTrack(id) {
      const job = state.jobs.find(j => j.id === id);
      if (!job || !job.download_url) return;
      state.currentTrack = id;
      state.isPlaying = true;
      audio.src = job.download_url;
      audio.play();
      $("playerBar").style.display = "flex";
      $("playerTitle").textContent = job.song_title || job.prompt || "Untitled";
      $("playPauseBtn").textContent = "⏸";
      renderLibrary();
    }

    function togglePlay() {
      if (!audio.src) return;
      state.isPlaying ? audio.pause() : audio.play();
      $("playPauseBtn").textContent = state.isPlaying ? "▶" : "⏸";
    }

    function seekAudio(e) {
      const rect = e.target.getBoundingClientRect();
      const percent = (e.clientX - rect.left) / rect.width;
      audio.currentTime = percent * audio.duration;
    }

    function toggleLike(id) {
      const idx = state.likedTracks.indexOf(id);
      if (idx > -1) state.likedTracks.splice(idx, 1);
      else state.likedTracks.push(id);
      localStorage.setItem("liked_tracks", JSON.stringify(state.likedTracks));
      renderLibrary();
    }

    async function deleteJob(id) {
      if (!confirm("Delete this track?")) return;
      await fetch("/api/jobs/" + encodeURIComponent(id), { method: "DELETE" });
      loadJobs();
    }

    function openVoiceRecorder() { $("recModal").classList.add("open"); }
    function closeRecModal() { $("recModal").classList.remove("open"); }

    function escapeHtml(str) {
      return String(str || "").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
    }

    function formatDate(iso) {
      const d = new Date(iso);
      return d.toLocaleDateString() + " " + d.toLocaleTimeString([], {hour:"2-digit", minute:"2-digit"});
    }

    // Audio events
    audio.addEventListener("timeupdate", () => {
      if (audio.duration) {
        const pct = (audio.currentTime / audio.duration) * 100;
        $("progressFill").style.width = pct + "%";
        $("currentTime").textContent = formatTime(audio.currentTime);
        $("totalTime").textContent = formatTime(audio.duration);
      }
    });
    audio.addEventListener("ended", () => {
      state.isPlaying = false;
      $("playPauseBtn").textContent = "▶";
    });

    function formatTime(secs) {
      const m = Math.floor(secs / 60);
      const s = Math.floor(secs % 60);
      return m + ":" + (s < 10 ? "0" : "") + s;
    }

    init();
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
        if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
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
        raise RuntimeError(detail)
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

    def handle_jobs_batch(self) -> None:
        """Handle POST /api/jobs/batch — create multiple music jobs from an array of prompts."""
        try:
            form = self.read_json_body()
            prompts = form.get("prompts", [])
            if not isinstance(prompts, list):
                raise ValueError("prompts must be an array.")
            if len(prompts) > 5:
                raise ValueError("Maximum 5 prompts per batch.")
            if len(prompts) == 0:
                raise ValueError("At least one prompt is required.")
            is_instrumental = bool(form.get("is_instrumental"))
            lyrics_idea = str(form.get("lyrics_idea", "")).strip()
            lyrics_optimizer = bool(form.get("lyrics_optimizer") or lyrics_idea) and not is_instrumental
            extra = {key: str(form.get(key, "")).strip() for key in ("genre", "mood", "instruments", "tempo", "bpm", "key", "vocals", "structure", "references", "avoid", "use_case", "extra")}
            client_id = normalize_client_id(self.headers.get("X-Client-Id"))
            jobs = []
            for i, raw_prompt in enumerate(prompts):
                prompt = str(raw_prompt).strip()
                if not prompt:
                    continue
                if len(prompt) > 2000:
                    prompt = prompt[:2000]
                job_id = secrets.token_urlsafe(12)
                job = {
                    "id": job_id,
                    "owner_id": client_id,
                    "status": "queued",
                    "created_at": now_iso(),
                    "updated_at": now_iso(),
                    "prompt": prompt,
                    "song_title": "",
                    "generated_title": False,
                    "title_error": None,
                    "email": "",
                    "lyrics": "",
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
                jobs.append(public_job(job))
            self.send_json({"jobs": jobs, "count": len(jobs)}, HTTPStatus.ACCEPTED)
        except ValueError as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        except Exception as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return

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
        if parsed.path == "/api/jobs/batch":
            self.handle_jobs_batch()
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
