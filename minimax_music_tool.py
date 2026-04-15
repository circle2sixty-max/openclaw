#!/usr/bin/env python3
"""
Local Terry Music 2.6 generator.

Run:
    MINIMAX_API_TOKEN="your_api_key" python3 minimax_music_tool.py

Open:
    http://127.0.0.1:5050
"""

from __future__ import annotations

import datetime as dt
import email.encoders
import email.mime.audio
import email.mime.base
import email.mime.multipart
import email.mime.text
import json
import mimetypes
import os
import re
import secrets
import shutil
import smtplib
import subprocess
import threading
import urllib.error
import urllib.parse
import urllib.request
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "5050"))
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


def _legacy_local_config(name: str) -> str:
    """Local-only bridge while migrating Claude's Downloads script into this project."""
    legacy_path = Path.home() / "Downloads" / "minimax_music_tool.py"
    if not legacy_path.exists():
        return ""
    try:
        text = legacy_path.read_text(encoding="utf-8")
    except OSError:
        return ""
    match = re.search(rf"^{re.escape(name)}\s*=\s*(['\"])(.*?)\1", text, re.MULTILINE)
    return match.group(2) if match else ""


MINIMAX_API_TOKEN = os.environ.get("MINIMAX_API_TOKEN") or _legacy_local_config("MINIMAX_API_TOKEN")

# SMTP email config. Render should provide these as environment variables.
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "465"))
SMTP_USER = os.environ.get("SMTP_USER") or _legacy_local_config("SMTP_USER")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD") or _legacy_local_config("SMTP_PASSWORD")

OUTPUT_DIR = Path.home() / "Downloads" / "minimax_music_outputs"
JOBS_DB = OUTPUT_DIR / "jobs.json"
MAX_BODY_BYTES = 1024 * 1024

JOBS: dict[str, dict[str, Any]] = {}
JOBS_LOCK = threading.RLock()


INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Terry Music — AI Music Generator</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #0b0d0c;
      --panel: #141716;
      --panel-soft: #1a1f1d;
      --input: #101312;
      --line: #2d3430;
      --text: #f4f7f1;
      --muted: #a7b0aa;
      --accent: #50d890;
      --accent-strong: #2fbd76;
      --warn: #efc86a;
      --danger: #ff756d;
      --shadow: 0 18px 60px rgba(0,0,0,0.36);
    }
    * { box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
    body {
      margin: 0; min-height: 100vh;
      background: linear-gradient(180deg, rgba(80,216,144,0.06), transparent 320px), var(--bg);
      color: var(--text);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, sans-serif;
      font-size: 16px;
    }
    a { color: inherit; }
    .page { width: min(1180px, calc(100% - 32px)); margin: 0 auto; padding: 24px 16px 60px; }

    /* ── Header ── */
    .header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 24px; gap: 12px; flex-wrap: wrap; }
    h1 { margin: 0; font-size: 26px; font-weight: 780; line-height: 1.1; }
    .subtitle { margin: 4px 0 0; color: var(--muted); font-size: 14px; line-height: 1.5; }
    .header-right { display: flex; align-items: center; gap: 10px; flex-shrink: 0; }
    .badge-brand { background: rgba(80,216,144,0.15); border: 1px solid rgba(80,216,144,0.3); color: var(--accent); border-radius: 8px; padding: 6px 12px; font-size: 12px; font-weight: 650; white-space: nowrap; }
    .lang-toggle { background: rgba(255,255,255,0.06); border: 1px solid var(--line); color: var(--muted); border-radius: 8px; padding: 6px 12px; font-size: 12px; cursor: pointer; font-weight: 650; transition: all 0.2s; }
    .lang-toggle:hover { border-color: var(--accent); color: var(--accent); }

    /* ── Layout ── */
    .layout { display: grid; grid-template-columns: 1fr 340px; gap: 16px; align-items: start; }
    @media(max-width: 768px) {
      .layout { grid-template-columns: 1fr; }
      h1 { font-size: 22px; }
      .page { padding: 16px 12px 80px; }
      .subtitle { display: none; }
    }
    @media(max-width: 480px) {
      .header { flex-direction: column; align-items: flex-start; }
      .header-right { width: 100%; justify-content: space-between; }
    }

    /* ── Header ── */
    .header { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 32px; gap: 20px; }
    h1 { margin: 0 0 8px; font-size: 34px; font-weight: 780; line-height: 1.08; }
    .subtitle { margin: 0; color: var(--muted); line-height: 1.55; max-width: 600px; font-size: 15px; }
    .badge-brand { flex: 0 0 auto; background: rgba(80,216,144,0.15); border: 1px solid rgba(80,216,144,0.3); color: var(--accent); border-radius: 8px; padding: 8px 14px; font-size: 13px; font-weight: 650; white-space: nowrap; }

    /* ── Layout ── */
    .layout { display: grid; grid-template-columns: 1fr 380px; gap: 20px; align-items: start; }
    @media(max-width:900px) { .layout { grid-template-columns: 1fr; } }

    /* ── Panels ── */
    .panel { border: 1px solid var(--line); border-radius: 12px; background: rgba(20,23,22,0.97); box-shadow: var(--shadow); overflow: hidden; }
    .panel-header { padding: 20px 22px 16px; border-bottom: 1px solid var(--line); }
    .panel-header h2 { margin: 0; font-size: 17px; font-weight: 760; }
    .panel-header p { margin: 6px 0 0; color: var(--muted); font-size: 13px; line-height: 1.5; }
    .panel-body { padding: 20px 22px 22px; }

    /* ── Form ── */
    label { display: block; color: var(--text); font-weight: 650; font-size: 13px; margin-bottom: 7px; }
    .field { margin-bottom: 18px; }
    input[type="text"], input[type="email"], input[type="number"], textarea, select {
      width: 100%; border: 1px solid var(--line); border-radius: 8px; background: var(--input);
      color: var(--text); padding: 11px 13px; font: inherit; font-size: 15px; outline: none;
      transition: border-color 0.15s, box-shadow 0.15s; -webkit-appearance: none;
    }
    input:focus, textarea:focus, select:focus { border-color: rgba(80,216,144,0.7); box-shadow: 0 0 0 3px rgba(80,216,144,0.12); }
    textarea { min-height: 160px; resize: vertical; line-height: 1.5; }
    .hint { margin-top: 6px; color: var(--muted); font-size: 12px; line-height: 1.45; }

    /* ── Checkboxes ── */
    .checks { display: grid; gap: 8px; margin: 14px 0 18px; }
    .check-row { display: flex; align-items: flex-start; gap: 10px; padding: 11px 13px; border: 1px solid var(--line); border-radius: 8px; background: var(--panel-soft); cursor: pointer; }
    .check-row input { margin-top: 3px; accent-color: var(--accent); width: 16px; height: 16px; flex: 0 0 auto; cursor: pointer; }
    .check-row-text { display: block; }
    .check-row-text small { display: block; color: var(--muted); margin-top: 3px; font-size: 12px; line-height: 1.35; }

    /* ── Advanced Section ── */
    .adv-toggle { display: flex; align-items: center; gap: 8px; color: var(--muted); font-size: 13px; cursor: pointer; padding: 10px 0; user-select: none; border-top: 1px solid var(--line); margin-top: 14px; }
    .adv-toggle::before { content: "▸ "; transition: transform 0.2s; display: inline-block; }
    .adv-toggle.open::before { content: "▾ "; }
    .adv-section { overflow: hidden; max-height: 0; transition: max-height 0.25s ease; }
    .adv-section.open { max-height: 1400px; }
    .adv-grid { padding-top: 18px; display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
    @media(max-width:600px) { .adv-grid { grid-template-columns: 1fr; } }
    .adv-grid .field { margin-bottom: 0; }

    /* ── Buttons ── */
    .actions { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-top: 20px; }
    button { border: 0; border-radius: 8px; background: var(--accent); color: #06100b; cursor: pointer; font-weight: 780; padding: 12px 20px; font-size: 15px; min-height: 46px; display: inline-flex; align-items: center; gap: 8px; }
    button:hover { background: var(--accent-strong); }
    button:disabled { cursor: wait; opacity: 0.65; }
    .download-link { border: 0; border-radius: 8px; background: var(--accent); color: #06100b; cursor: pointer; font-weight: 780; padding: 8px 14px; font-size: 13px; text-decoration: none; min-height: 38px; display: inline-flex; align-items: center; }
    .download-link:hover { background: var(--accent-strong); }
    .form-error { color: var(--danger); font-size: 14px; min-height: 20px; }

    /* ── Email notification ── */
    .email-sent { color: var(--accent); font-size: 13px; display: flex; align-items: center; gap: 6px; }
    .email-sent::before { content: "✉"; }

    /* ── Jobs list ── */
    .jobs { padding: 14px 18px 18px; display: grid; gap: 10px; }
    .empty { color: var(--muted); border: 1px dashed var(--line); border-radius: 8px; padding: 20px; text-align: center; font-size: 14px; background: rgba(16,19,18,0.6); }
    .job { border: 1px solid var(--line); border-radius: 8px; background: var(--panel-soft); padding: 13px 15px; display: grid; gap: 9px; }
    .job-top { display: flex; justify-content: space-between; gap: 10px; align-items: flex-start; }
    .job-title { margin: 0; font-size: 14px; font-weight: 650; line-height: 1.4; overflow-wrap: anywhere; }
    .badge { flex: 0 0 auto; border-radius: 999px; padding: 4px 10px; border: 1px solid var(--line); background: #101312; color: var(--muted); font-size: 11px; font-weight: 760; text-transform: uppercase; }
    .badge.completed { color: var(--accent); border-color: rgba(80,216,144,0.4); }
    .badge.running, .badge.queued { color: var(--warn); border-color: rgba(239,200,106,0.4); }
    .badge.error { color: var(--danger); border-color: rgba(255,117,109,0.4); }
    .meta { display: flex; gap: 6px; flex-wrap: wrap; }
    .meta span { border: 1px solid var(--line); border-radius: 999px; padding: 3px 8px; background: #101312; color: var(--muted); font-size: 11px; }
    .job-error { color: var(--danger); font-size: 13px; line-height: 1.4; overflow-wrap: anywhere; }
    .job-file { display: flex; gap: 10px; align-items: center; justify-content: space-between; flex-wrap: wrap; color: var(--muted); font-size: 13px; }
    .job-actions { display: flex; gap: 8px; }
    .delete-btn { background: rgba(255,117,109,0.15); color: var(--danger); border: 1px solid rgba(255,117,109,0.3); border-radius: 6px; padding: 5px 10px; font-size: 11px; cursor: pointer; font-weight: 650; }
    .delete-btn:hover { background: rgba(255,117,109,0.25); }
    code { color: var(--accent); font-size: 13px; }

    /* ── Collapsible advanced ── */
    .adv-grid-full { display: grid; gap: 14px; }
  </style>
</head>
<body>
  <main class="page">
    <div class="header">
      <div>
        <h1>🎵 Terry Music</h1>
        <p class="subtitle" id="subtitle">Describe a music style, AI generates an MP3 and sends it to your email.</p>
      </div>
      <div class="header-right">
        <div class="badge-brand">music-2.6</div>
        <button class="lang-toggle" id="lang-toggle" onclick="toggleLang()">中文</button>
      </div>
    </div>

    <div class="layout">
      <!-- Left: form -->
      <section class="panel">
        <div class="panel-header">
          <h2 id="create-title">Create Music</h2>
          <p id="create-desc">Fill in the details below. Terry Music will generate your track and email it to you.</p>
        </div>
        <div class="panel-body">
          <form id="job-form">
            <!-- Email -->
            <div class="field">
              <label for="email">📧 Email Address</label>
              <input id="email" name="email" type="email" placeholder="your@email.com" required>
              <div class="hint" id="email-hint">Your MP3 will be sent to this email when it is ready.</div>
            </div>

            <!-- Prompt -->
            <div class="field">
              <label for="prompt">🎶 Music Style Prompt</label>
              <input id="prompt" name="prompt" type="text" maxlength="2000" required value="Cinematic electronic pop, confident and bright, polished production, strong hook" placeholder="Describe the style, mood, instruments, BPM...">
              <div class="hint" id="prompt-hint">More detail helps. Example: Reggae Dancehall, heavy bass, Jamaican toasting, dark humor, 85 bpm.</div>
            </div>

            <!-- Lyrics -->
            <div class="field">
              <label for="lyrics">📝 Lyrics (optional)</label>
              <textarea id="lyrics" name="lyrics" maxlength="3500" placeholder="[Verse]
Your lyrics here...
[Hook]
Your chorus..."></textarea>
              <div class="hint" id="lyrics-hint">Use structure tags like [Verse], [Hook], and [Chorus]. Leave empty for instrumental or auto lyrics.</div>
            </div>

            <!-- Mode checks -->
            <div class="checks">
              <label class="check-row">
                <input id="instrumental" name="instrumental" type="checkbox">
                <span class="check-row-text">
                  Instrumental (no vocals)
                  <small>No vocals. Lyrics will be ignored.</small>
                </span>
              </label>
              <label class="check-row">
                <input id="lyrics_optimizer" name="lyrics_optimizer" type="checkbox">
                <span class="check-row-text">
                  Auto-generate Lyrics
                  <small>AI writes lyrics from your prompt.</small>
                </span>
              </label>
            </div>

            <!-- Advanced toggle -->
            <div class="adv-toggle" id="adv-toggle" onclick="toggleAdv()">⚙️ More Parameters (key, BPM, vocal style...)</div>
            <div class="adv-section" id="adv-section">
              <div class="adv-grid">
                <div class="field">
                  <label for="genre" id="lbl-genre">🎸 Genre</label>
                  <input id="genre" name="genre" type="text" placeholder="pop, reggae, jazz, folk...">
                </div>
                <div class="field">
                  <label for="mood" id="lbl-mood">😊 Mood</label>
                  <input id="mood" name="mood" type="text" placeholder="warm, melancholic, uplifting...">
                </div>
                <div class="field">
                  <label for="instruments" id="lbl-instruments">🎹 Instruments</label>
                  <input id="instruments" name="instruments" type="text" placeholder="acoustic guitar, piano, drums...">
                </div>
                <div class="field">
                  <label for="tempo" id="lbl-tempo">💓 Tempo Feel</label>
                  <input id="tempo" name="tempo" type="text" placeholder="fast, slow, moderate">
                </div>
                <div class="field">
                  <label for="bpm" id="lbl-bpm">🔢 BPM (exact tempo)</label>
                  <input id="bpm" name="bpm" type="number" min="40" max="240" placeholder="85">
                </div>
                <div class="field">
                  <label for="key" id="lbl-key">🎼 Musical Key</label>
                  <input id="key" name="key" type="text" placeholder="C major, A minor, G sharp...">
                </div>
                <div class="field" style="grid-column: 1/-1;">
                  <label for="vocals" id="lbl-vocals">🎤 Vocal Style</label>
                  <input id="vocals" name="vocals" type="text" placeholder="warm male baritone, bright female soprano, duet with harmonies...">
                </div>
                <div class="field" style="grid-column: 1/-1;">
                  <label for="structure" id="lbl-structure">📐 Song Structure</label>
                  <input id="structure" name="structure" type="text" placeholder="verse-chorus-verse-bridge-chorus">
                </div>
                <div class="field" style="grid-column: 1/-1;">
                  <label for="references" id="lbl-references">🎧 Reference Tracks / Artists</label>
                  <input id="references" name="references" type="text" placeholder="similar to Ed Sheeran, Taylor Swift, Drake...">
                </div>
                <div class="field" style="grid-column: 1/-1;">
                  <label for="avoid" id="lbl-avoid">🚫 Avoid Elements</label>
                  <input id="avoid" name="avoid" type="text" placeholder="explicit content, auto-tune, violin...">
                </div>
                <div class="field" style="grid-column: 1/-1;">
                  <label for="useCase" id="lbl-useCase">📍 Use Case</label>
                  <input id="useCase" name="useCase" type="text" placeholder="background music for video, theme song, workout track...">
                </div>
                <div class="field" style="grid-column: 1/-1;">
                  <label for="extra" id="lbl-extra">💡 Extra Details</label>
                  <input id="extra" name="extra" type="text" placeholder="Any additional notes...">
                </div>
              </div>
            </div>

            <div class="actions">
              <button id="submit-button" type="submit">🎵 Generate Music</button>
              <div id="form-error" class="form-error"></div>
            </div>
          </form>
        </div>
      </section>

      <!-- Right: jobs -->
      <section class="panel">
        <div class="panel-header">
          <h2 id="jobs-title" data-i18n="jobsTitle">Jobs</h2>
          <p id="jobs-desc" data-i18n="jobsDesc">Real-time status. MP3 will be sent to your email when done.</p>
        </div>
        <div id="jobs" class="jobs">
          <div class="empty" id="jobs-empty">No jobs yet. Fill in the form to start creating.</div>
        </div>
      </section>
    </div>
  </main>

  <script>
    // ── i18n ──────────────────────────────────────────────────────
    const I18N = {
      en: {
        subtitle: "Describe a music style, AI generates an MP3 and sends it to your email.",
        createTitle: "Create Music",
        createDesc: "Fill in the details below. Terry Music will generate your track and email it to you.",
        emailLabel: "📧 Email Address",
        emailHint: "Your MP3 will be sent to this email when it is ready.",
        emailPH: "your@email.com",
        promptLabel: "🎶 Music Style Prompt",
        promptPH: "Describe the style, mood, instruments, BPM...",
        promptHint: "More detail helps. Example: Reggae Dancehall, heavy bass, Jamaican toasting, dark humor, 85 bpm.",
        lyricsLabel: "📝 Lyrics (optional)",
        lyricsPH: "[Verse]\nYour lyrics here...\n[Hook]\nYour chorus...",
        lyricsHint: "Use structure tags like [Verse], [Hook], and [Chorus]. Leave empty for instrumental or auto lyrics.",
        instrumental: "Instrumental (no vocals)",
        instrumentalHint: "No vocals. Lyrics will be ignored.",
        autoLyrics: "Auto-generate Lyrics",
        autoLyricsHint: "AI writes lyrics from your prompt.",
        advToggle: "⚙️ More Parameters (key, BPM, vocal style...)",
        genre: "🎸 Genre", genrePH: "pop, reggae, jazz, folk...",
        mood: "😊 Mood", moodPH: "warm, melancholic, uplifting...",
        instruments: "🎹 Instruments", instrumentsPH: "acoustic guitar, piano, drums...",
        tempo: "💓 Tempo Feel", tempoPH: "fast, slow, moderate",
        bpm: "🔢 BPM (exact tempo)", bpmPH: "85",
        key: "🎼 Musical Key", keyPH: "C major, A minor, G sharp...",
        vocals: "🎤 Vocal Style", vocalsPH: "warm male baritone, bright female soprano, duet with harmonies...",
        structure: "📐 Song Structure", structurePH: "verse-chorus-verse-bridge-chorus",
        references: "🎧 Reference Tracks / Artists", referencesPH: "similar to Ed Sheeran, Taylor Swift, Drake...",
        avoid: "🚫 Avoid Elements", avoidPH: "explicit content, auto-tune, violin...",
        useCase: "📍 Use Case", useCasePH: "background music for video, theme song, workout track...",
        extra: "💡 Extra Details", extraPH: "Any additional notes...",
        submitBtn: "🎵 Generate Music",
        jobsTitle: "Jobs",
        jobsDesc: "Real-time status. MP3 will be sent to your email when done.",
        empty: "No jobs yet. Fill in the form to start creating.",
        running: "Generating",
        queued: "Queued",
        completed: "Done",
        error: "Error",
        unknown: "Unknown",
        untitled: "Untitled",
        instrumental_mode: "Instrumental",
        vocal_mode: "Vocal",
        delete: "Delete",
        emailSent: "Sent to",
        download: "Download MP3",
        deleteConfirm: "Delete this job?",
        deleteFailed: "Delete failed",
      },
      zh: {
        subtitle: "描述音乐风格，AI 生成 MP3，完成后发送到你的邮箱。",
        createTitle: "创建音乐",
        createDesc: "填写以下信息，AI 将生成专属音乐并发送到你的邮箱。",
        emailLabel: "📧 邮箱地址",
        emailHint: "音乐生成完成后，MP3 将自动发送到你的邮箱。",
        emailPH: "your@email.com",
        promptLabel: "🎶 音乐风格描述",
        promptPH: "描述风格、情绪、乐器、BPM...",
        promptHint: "越详细越好，例如：Reggae Dancehall, heavy bass, Jamaican toasting, dark humor, 85 bpm。",
        lyricsLabel: "📝 歌词（可选）",
        lyricsPH: "[Verse]\n你的歌词...\n[Hook]\n副歌部分...",
        lyricsHint: "带结构标签如 [Verse]、[Hook]、[Chorus]。留空则生成纯音乐或自动写歌词。",
        instrumental: "纯音乐（Instrumental）",
        instrumentalHint: "无人声，歌词将被忽略。",
        autoLyrics: "自动生成歌词（Auto Lyrics）",
        autoLyricsHint: "AI 根据音乐描述自动生成歌词。",
        advToggle: "⚙️ 更多参数（调性/BPM/人声风格...）",
        genre: "🎸 音乐流派", genrePH: "pop, reggae, jazz, folk...",
        mood: "😊 音乐情绪", moodPH: "warm, melancholic, uplifting...",
        instruments: "🎹 主要乐器", instrumentsPH: "acoustic guitar, piano, drums...",
        tempo: "💓 节奏感", tempoPH: "fast, slow, moderate",
        bpm: "🔢 BPM（精确速度）", bpmPH: "85",
        key: "🎼 音乐调性", keyPH: "C major, A minor, G sharp...",
        vocals: "🎤 人声风格", vocalsPH: "warm male baritone, bright female soprano, duet with harmonies...",
        structure: "📐 歌曲结构", structurePH: "verse-chorus-verse-bridge-chorus",
        references: "🎧 参考曲目/艺术家", referencesPH: "similar to Ed Sheeran, Taylor Swift, Drake...",
        avoid: "🚫 避免元素", avoidPH: "explicit content, auto-tune, violin...",
        useCase: "📍 使用场景", useCasePH: "background music for video, theme song, workout track...",
        extra: "💡 其他要求", extraPH: "任何额外细节...",
        submitBtn: "🎵 生成音乐",
        jobsTitle: "生成任务",
        jobsDesc: "实时状态，完成后 MP3 将发送到你的邮箱。",
        empty: "暂无任务，填写上方表单开始创作。",
        running: "生成中",
        queued: "排队中",
        completed: "完成",
        error: "错误",
        unknown: "未知",
        untitled: "未命名",
        instrumental_mode: "纯音乐",
        vocal_mode: "有人声",
        delete: "删除",
        emailSent: "已发送到",
        download: "下载 MP3",
        deleteConfirm: "删除此任务？",
        deleteFailed: "删除失败",
      }
    };

    let currentLang = "en";
    let jobsData = [];
    const form = document.getElementById("job-form");
    const jobsBox = document.getElementById("jobs");
    const lyricsEl = document.getElementById("lyrics");
    const instrumental = document.getElementById("instrumental");
    const optimizer = document.getElementById("lyrics_optimizer");
    const submitBtn = document.getElementById("submit-button");
    const errorBox = document.getElementById("form-error");
    const clientId = (() => {
      const key = "terry_music_client_id";
      let value = localStorage.getItem(key);
      if (!value) {
        value = window.crypto && crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
        localStorage.setItem(key, value);
      }
      return value;
    })();

    function clientHeaders(extra = {}) {
      return {"X-Client-Id": clientId, ...extra};
    }

    function toggleLang() {
      currentLang = currentLang === "en" ? "zh" : "en";
      applyLang();
      renderJobs(jobsData);
    }

    function applyLang() {
      const L = I18N[currentLang];
      document.getElementById("subtitle").textContent = L.subtitle;
      document.getElementById("lang-toggle").textContent = currentLang === "en" ? "中文" : "EN";
      const d = document.getElementById("create-title");
      if (d) d.textContent = L.createTitle;
      const dd = document.getElementById("create-desc");
      if (dd) dd.textContent = L.createDesc;
      const em = document.querySelector('label[for="email"]');
      if (em) em.textContent = L.emailLabel;
      const eh = document.querySelector(".field:nth-child(1) .hint");
      if (eh) eh.textContent = L.emailHint;
      const emailHint = document.getElementById("email-hint");
      if (emailHint) emailHint.textContent = L.emailHint;
      const pr = document.querySelector('label[for="prompt"]');
      if (pr) pr.textContent = L.promptLabel;
      const ph = document.getElementById("prompt-hint");
      if (ph) ph.textContent = L.promptHint;
      const ly = document.querySelector('label[for="lyrics"]');
      if (ly) ly.textContent = L.lyricsLabel;
      const lh = document.getElementById("lyrics-hint");
      if (lh) lh.textContent = L.lyricsHint;
      const rows = document.querySelectorAll(".check-row");
      if (rows[0]) rows[0].querySelector("span").innerHTML = `${L.instrumental}<small>${L.instrumentalHint}</small>`;
      if (rows[1]) rows[1].querySelector("span").innerHTML = `${L.autoLyrics}<small>${L.autoLyricsHint}</small>`;
      const at = document.getElementById("adv-toggle");
      if (at) at.textContent = L.advToggle;
      const sb = document.getElementById("submit-button");
      if (sb) sb.textContent = L.submitBtn;
      const jt = document.getElementById("jobs-title");
      if (jt) jt.textContent = L.jobsTitle;
      const jd = document.getElementById("jobs-desc");
      if (jd) jd.textContent = L.jobsDesc;
      const je = document.getElementById("jobs-empty");
      if (je) je.textContent = L.empty;
      const els = [
        ["lbl-genre", L.genre], ["lbl-mood", L.mood], ["lbl-instruments", L.instruments],
        ["lbl-tempo", L.tempo], ["lbl-bpm", L.bpm], ["lbl-key", L.key],
        ["lbl-vocals", L.vocals], ["lbl-structure", L.structure],
        ["lbl-references", L.references], ["lbl-avoid", L.avoid],
        ["lbl-useCase", L.useCase], ["lbl-extra", L.extra],
      ];
      els.forEach(([id, text]) => { const el = document.getElementById(id); if (el) el.textContent = text; });
      const placeholders = [
        ["email", L.emailPH], ["prompt", L.promptPH], ["lyrics", L.lyricsPH],
        ["genre", L.genrePH], ["mood", L.moodPH], ["instruments", L.instrumentsPH],
        ["tempo", L.tempoPH], ["bpm", L.bpmPH], ["key", L.keyPH],
        ["vocals", L.vocalsPH], ["structure", L.structurePH],
        ["references", L.referencesPH], ["avoid", L.avoidPH],
        ["useCase", L.useCasePH], ["extra", L.extraPH],
      ];
      placeholders.forEach(([id, text]) => { const el = document.getElementById(id); if (el) el.placeholder = text; });
      document.title = currentLang === "en" ? "Terry Music" : "Terry Music";
    }

    // translate page to English on first load
    applyLang();

    function toggleAdv() {
      const t = document.getElementById("adv-toggle");
      const s = document.getElementById("adv-section");
      t.classList.toggle("open"); s.classList.toggle("open");
    }

    function escapeHtml(v) {
      return String(v ?? "").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;").replace(/'/g,"&#039;");
    }

    function formatDate(v) {
      if (!v) return "";
      const d = new Date(v);
      if (isNaN(d.getTime())) return v;
      return d.toLocaleString(currentLang === "en" ? "en-GB" : "zh-CN", {month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",second:"2-digit"});
    }

    function renderJobs(jobs) {
      jobsData = jobs;
      if (!jobs.length) { jobsBox.innerHTML=`<div class="empty">${I18N[currentLang].empty}</div>`; return; }
      const L = I18N[currentLang];
      jobsBox.innerHTML = jobs.map(j => {
        const status = escapeHtml(j.status || "unknown");
        const statusLabel = status === "completed" ? L.completed : status === "running" ? L.running : status === "queued" ? L.queued : status === "error" ? L.error : L.unknown;
        const title = escapeHtml(j.prompt || L.untitled);
        const created = formatDate(j.created_at);
        const mode = j.is_instrumental ? L.instrumental_mode : L.vocal_mode;
        const emailInfo = j.email ? `<span style="color:var(--accent)">✉ ${escapeHtml(j.email)}</span>` : "";
        const error = j.error ? `<div class="job-error">${escapeHtml(j.error)}</div>` : "";
        const downloadUrl = j.download_url ? `${escapeHtml(j.download_url)}?client_id=${encodeURIComponent(clientId)}` : "";
        const fileName = escapeHtml(j.file_name || "terry-music.mp3");
        const file = j.status === "completed" && j.download_url
          ? `<div class="job-file"><span>${fileName}</span><a class="download-link" href="${downloadUrl}" download="${fileName}">${L.download}</a></div>`
          : j.status === "completed" && j.email_sent
          ? `<div class="email-sent">${L.emailSent} ${escapeHtml(j.email)}</div>`
          : "";
        const badgeClass = status;
        return `<div class="job" id="job-${escapeHtml(j.id)}">
          <div class="job-top">
            <p class="job-title">${title}</p>
            <span class="badge ${badgeClass}">${statusLabel}</span>
          </div>
          <div class="meta">
            <span>${mode}</span><span>${created}</span>${emailInfo ? `<span>${emailInfo}</span>` : ""}
          </div>
          ${error}${file}
          ${j.status !== "running" && j.status !== "queued" ? `<div class="job-actions"><button class="delete-btn" onclick="deleteJob('${escapeHtml(j.id)}')">${L.delete}</button></div>` : ""}
        </div>`;
      }).join("");
    }

    async function loadJobs() {
      try {
        const r = await fetch("/api/jobs", {cache: "no-store", headers: clientHeaders()});
        const d = await r.json();
        renderJobs(d.jobs || []);
      } catch(e) { jobsBox.innerHTML = `<div class="empty">${I18N[currentLang].empty}</div>`; }
    }

    async function deleteJob(id) {
      const L = I18N[currentLang];
      if (!confirm(L.deleteConfirm)) return;
      const r = await fetch(`/api/jobs/${id}`, {method: "DELETE", headers: clientHeaders()});
      if (r.ok) { await loadJobs(); } else { alert(L.deleteFailed); }
    }

    instrumental.addEventListener("change", () => {
      if (instrumental.checked) { optimizer.checked = false; optimizer.disabled = true; lyricsEl.disabled = true; }
      else { optimizer.disabled = false; lyricsEl.disabled = false; }
    });

    form.addEventListener("submit", async e => {
      e.preventDefault();
      errorBox.textContent = "";
      submitBtn.disabled = true;

      const payload = {
        prompt: document.getElementById("prompt").value.trim(),
        email: document.getElementById("email").value.trim(),
        lyrics: lyricsEl.value.trim(),
        is_instrumental: instrumental.checked,
        lyrics_optimizer: optimizer.checked,
        genre: document.getElementById("genre").value.trim(),
        mood: document.getElementById("mood").value.trim(),
        instruments: document.getElementById("instruments").value.trim(),
        tempo: document.getElementById("tempo").value.trim(),
        bpm: document.getElementById("bpm").value.trim(),
        key: document.getElementById("key").value.trim(),
        vocals: document.getElementById("vocals").value.trim(),
        structure: document.getElementById("structure").value.trim(),
        references: document.getElementById("references").value.trim(),
        avoid: document.getElementById("avoid").value.trim(),
        use_case: document.getElementById("useCase").value.trim(),
        extra: document.getElementById("extra").value.trim(),
      };

      try {
        const r = await fetch("/api/jobs", {
          method: "POST",
          headers: clientHeaders({"Content-Type": "application/json"}),
          body: JSON.stringify(payload)
        });
        const d = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(d.error || `HTTP ${r.status}`);
        await loadJobs();
        form.reset();
        document.getElementById("prompt").value = "Cinematic electronic pop, confident and bright, polished production, strong hook";
      } catch(err) { errorBox.textContent = err.message; }
      finally { submitBtn.disabled = false; }
    });

    loadJobs();
    setInterval(loadJobs, 3000);
  </script>
</body>
</html>
"""


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def compact_text(value: str, max_len: int = 900) -> str:
    value = " ".join(value.split())
    if len(value) <= max_len:
        return value
    return value[: max_len - 3] + "..."


def sanitize_filename_part(value: str, fallback: str = "track") -> str:
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = value.strip("-")
    if not value:
        value = fallback
    return value[:64].strip("-") or fallback


def public_job(job: dict[str, Any]) -> dict[str, Any]:
    result = {
        "id": job["id"],
        "status": job["status"],
        "created_at": job["created_at"],
        "updated_at": job["updated_at"],
        "prompt": job.get("prompt", ""),
        "email": job.get("email", ""),
        "is_instrumental": bool(job.get("is_instrumental")),
        "lyrics_optimizer": bool(job.get("lyrics_optimizer")),
        "file_name": job.get("file_name"),
        "error": job.get("error"),
        "extra_info": job.get("extra_info"),
        "trace_id": job.get("trace_id"),
        "email_sent": job.get("email_sent", False),
    }
    if job.get("status") == "completed" and job.get("file_name"):
        result["download_url"] = f"/download/{urllib.parse.quote(job['id'])}"
    return result


def normalized_client_id(value: str | None) -> str:
    value = (value or "").strip()
    value = re.sub(r"[^A-Za-z0-9._:-]", "", value)
    return value[:128] or "anonymous"


def save_jobs_locked() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    tmp_path = JOBS_DB.with_name(JOBS_DB.name + ".tmp")
    data = {
        "version": 1,
        "jobs": list(JOBS.values()),
    }
    tmp_path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    tmp_path.replace(JOBS_DB)


def load_jobs() -> None:
    """Always start with empty jobs — each user's session is fresh."""
    pass


def mark_job(job_id: str, **updates: Any) -> None:
    with JOBS_LOCK:
        job = JOBS[job_id]
        job.update(updates)
        job["updated_at"] = now_iso()
        save_jobs_locked()


def parse_error_body(body: bytes) -> str:
    text = body.decode("utf-8", errors="replace")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return compact_text(text)

    base_resp = payload.get("base_resp") if isinstance(payload, dict) else None
    if isinstance(base_resp, dict):
        status_msg = base_resp.get("status_msg")
        status_code = base_resp.get("status_code")
        if status_msg:
            return compact_text(f"{status_msg} (status_code={status_code})")
    return compact_text(json.dumps(payload, ensure_ascii=False))


# ─────────────────────────────────────────────────────────────
#  多轮脱敏引擎
# ─────────────────────────────────────────────────────────────

_ROUND1_REPLACEMENTS: list[tuple[str, str]] = [
    # 脏话 → 拼音/符号
    ("老子", "Lz"),
    ("他妈", "TMD"),
    ("他妈了", "TMD"),
    ("他妈个", "TMD"),
    ("他妈的", "TMD"),
    ("他妈啦", "TMD"),
    ("TMD", "TMD"),
    ("我操", "Lz"),
    ("尼玛", "NM"),
    ("你妈", "NM"),
    ("傻逼", "SB"),
    ("傻B", "SB"),
    ("牛逼", "NB"),
    ("装逼", "装B"),
    ("牛逼", "NB"),
    ("牛B", "NB"),
    ("滚", "走开"),
    ("滚蛋", "离开"),
    # 暴力/攻击性词
    ("揍", "怼"),
    ("打一顿", "怼一次"),
    ("干死", "超越"),
    ("砍死", "超越"),
    ("打死", "打败"),
    ("操你", "对你"),
    # 敏感机构
    ("警察", "jc"),
    ("公安", "jg"),
    ("城管", "管理"),
    ("政府", "上层"),
    ("官员", "领导"),
    ("干部", "管理"),
    # 制度性批判词
    ("剥削", "压榨"),
    ("资本主义", "资本世界"),
    ("社会主义", "社会制度"),
    ("共产党", "执政党"),
    ("独裁", "专制"),
    ("民主", "民权"),
    # 极端表述
    ("杀", "克"),
    ("死", "亡"),
    ("亡", "逝"),
    ("狗", "犬"),
    ("猪", "猪猪"),
    ("滚", "走开"),
]

_ROUND2_PATTERNS: list[tuple[str, str]] = [
    # 加强度词处理
    (r"他[妈啦个]*(的*){0,2}", "TMD"),
    (r"[他妈啦个的]+", "X"),
    (r"[我你他妈的]+", "X"),
    (r"操[你妈的]+", "TMD"),
    # 拳头/暴力
    (r"拳头[捏得紧]+", "手紧紧"),
    (r"拳头", "手"),
    (r"揍一顿", "怼一次"),
    (r"揍", "怼"),
    (r"打[一顿]+", "怼一次"),
    # 金钱关系
    (r"十万", "巨款"),
    (r"一万", "巨款"),
    (r"赔钱", "赔款"),
    # 特定敏感说法
    (r"先骂人不用负责", "后动手才担责"),
    (r"一个耳光", "一次冲突"),
    # 派出所/局子
    (r"派出[所]+", "调解"),
    (r"进派出", "进调解"),
    # 垄断/既得
    (r"韭菜", "普通人"),
    (r"割韭菜", "被收割"),
    # 社会批判激进词
    (r"拳头硬不如钱硬", "关系硬不如钱硬"),
    (r"拳头硬", "拳头强"),
    # 医院相关
    (r"医德", "医风"),
    (r"收红包", "灰色收入"),
    # 警察/公安 替换
    (r"\bJC\b", "jc"),
    (r"\bJG\b", "jg"),
    # 重复符号
    (r"[!！]{3,}", "!!"),
    (r"[?？]{3,}", "??"),
    (r"[～~]{2,}", "~"),
]

# Round3: 敏感语义块 → 软化表达
_ROUND3_REWRITES: list[tuple[str, str]] = [
    # 强硬的杜交批判 → 缓和版
    ("规则是给没有背景的人定的", "规矩总是留给没权的人"),
    ("规则是给普通人定的", "规矩总是留给没权的人"),
    ("有钱人不受约束", "权贵总能绕开规则"),
    ("素质道德 都是说给有钱人听的", "素质道德 都是有条件才讲"),
    ("有钱人遮羞布", "权贵的遮羞布"),
    ("韭菜割不完", "普通人忙不完"),
    ("排队等着被割", "排着队被割"),
    ("谁先开口谁就是那个傻子", "敢说真话的人总被针对"),
    ("揣着明白装糊涂", "都揣着明白装不清楚"),
    # 社会现实类
    ("人心比那TM还黑", "人心在现实里变冷"),
    ("人心变坏了", "人心在时代里变凉"),
    ("人不要脸 天下无敌", "不要脸的人 反而活得轻松"),
    ("拳头硬不如钱硬", "关系硬不如钱硬"),
    # 医疗
    ("病人的命 在她眼里不如她的奶茶", "病人命比奶茶便宜"),
    # 婆理姑
    ("老太婆过马路", "老人过马路"),
    # 冲突解决
    ("调解到半夜三更", "调解到凌晨"),
    # 政治隐喻
    ("这个天 给我翻过来", "这命运 给我翻过来"),
    ("翻身", "出头"),
    # 结尾强硬
    ("老子就是愤青 就是不服", "Lz就是不服 Lz就是不甘"),
    ("老子就是愤青", "Lz就是不服"),
    ("等老子翻身了", "等Lz出头了"),
    ("老子哪一天翻身了", "Lz哪天真出头了"),
    # 其他脏话
    ("Lz在这个世道", "Lz在现实里"),
    ("在这个系统里", "在这时代里"),
]

# Round4: 最后的逐字清理
_ROUND4_FINAL: list[tuple[str, str]] = [
    # 残留敏感词
    ("TM", "X"),
    ("RMB", "钱"),
    ("共C", "X"),
    ("贪官", "X官"),
    ("X了", "了"),
    ("X的", ""),
    # 括号标签清理（保留标签如[Verse]但清理内部）
    (r"\[Verse\s*\]", "[Verse]"),
    (r"\[Hook\s*\]", "[Hook]"),
    (r"\[Chorus\s*\]", "[Hook]"),
    (r"\[Outro\s*\]", "[Outro]"),
    (r"\[Intro\s*\]", "[Intro]"),
    (r"\[Bridge\s*\]", "[Bridge]"),
    (r"\[Pre-Chorus\s*\]", "[Pre-Hook]"),
]

_MULTI_SPACE = re.compile(r" {2,}")
_MULTI_NEWLINE = re.compile(r"\n{3,}")
_STRIP_CHARS = " \t　"


def _sanitize_round(text: str) -> str:
    """执行一轮替换，返回处理后的文本。"""
    for pattern, replacement in _ROUND1_REPLACEMENTS:
        text = text.replace(pattern, replacement)
    for pattern, replacement in _ROUND2_PATTERNS:
        text = re.sub(pattern, replacement, text)
    for pattern, replacement in _ROUND3_REWRITES:
        text = text.replace(pattern, replacement)
    for pattern, replacement in _ROUND4_FINAL:
        text = re.sub(pattern, replacement, text)
    return text


def sanitize_lyrics(text: str) -> str:
    """
    多轮脱敏：最多4轮，直到文本不再变化为止。
    保留 [Verse]、[Hook] 等结构标签。
    """
    # 先把标签和正文分离
    parts: list[tuple[bool, str]] = []
    head = 0
    for m in re.finditer(r"(\[(?:Verse|Hook|Chorus|Outro|Intro|Bridge|Pre-Chorus|Bridge)[^\]]*\])", text):
        if m.start() > head:
            parts.append((False, text[head:m.start()]))
        parts.append((True, m.group()))
        head = m.end()
    if head < len(text):
        parts.append((False, text[head:]))

    sanitized_parts: list[str] = []
    for is_tag, part in parts:
        if is_tag:
            sanitized_parts.append(part)
            continue

        # 对正文执行多轮脱敏
        current = part
        for _round in range(4):
            before = current
            current = _sanitize_round(current)
            current = _MULTI_SPACE.sub(" ", current)
            current = _MULTI_NEWLINE.sub("\n\n", current)
            current = current.strip(_STRIP_CHARS)
            if current == before:
                break

        sanitized_parts.append(current)

    result = "".join(sanitized_parts)
    # 移除空行过多
    lines = [l.rstrip() for l in result.splitlines()]
    result = "\n".join(l for l in lines if l.strip("[] \t　"))
    return result


# ─────────────────────────────────────────────────────────────
#  mmx CLI 调用
# ─────────────────────────────────────────────────────────────

def run_mmx(args: list[str]) -> bytes:
    """Run mmx CLI and return stdout bytes."""
    env = os.environ.copy()
    path_parts = [part for part in env.get("PATH", "").split(os.pathsep) if part]
    for path_hint in reversed(MMX_PATH_HINTS):
        if path_hint not in path_parts:
            path_parts.insert(0, path_hint)
    env["PATH"] = os.pathsep.join(path_parts)
    env["MINIMAX_API_TOKEN"] = MINIMAX_API_TOKEN
    result = subprocess.run(
        [MMX_BIN] + args,
        capture_output=True,
        text=False,
        env=env,
        timeout=600,
    )
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace") if result.stderr else ""
        raise RuntimeError(f"mmx failed: {stderr.strip() or result.stdout.decode('utf-8', errors='replace').strip()}")
    return result.stdout


def generate_music(job_id: str, prompt: str, is_instrumental: bool, lyrics_optimizer: bool, lyrics: str, email: str = "", extra: dict = None) -> None:
    mark_job(job_id, status="running", error=None)

    timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    slug = sanitize_filename_part(prompt)
    out_path = OUTPUT_DIR / f"minimax_{timestamp}_{slug}_{job_id[:8]}.mp3"

    args = [
        "music", "generate",
        "--prompt", prompt,
        "--out", str(out_path),
        "--non-interactive",
    ]

    if is_instrumental:
        args.append("--instrumental")
    elif lyrics_optimizer:
        args.append("--lyrics-optimizer")
    elif lyrics:
        clean_lyrics = sanitize_lyrics(lyrics)
        args.extend(["--lyrics", clean_lyrics])

    # pass extra mmx params
    if extra:
        if extra.get("genre"): args.extend(["--genre", extra["genre"]])
        if extra.get("mood"): args.extend(["--mood", extra["mood"]])
        if extra.get("instruments"): args.extend(["--instruments", extra["instruments"]])
        if extra.get("tempo"): args.extend(["--tempo", extra["tempo"]])
        if extra.get("bpm"): args.extend(["--bpm", extra["bpm"]])
        if extra.get("key"): args.extend(["--key", extra["key"]])
        if extra.get("vocals"): args.extend(["--vocals", extra["vocals"]])
        if extra.get("structure"): args.extend(["--structure", extra["structure"]])
        if extra.get("references"): args.extend(["--references", extra["references"]])
        if extra.get("avoid"): args.extend(["--avoid", extra["avoid"]])
        if extra.get("use_case"): args.extend(["--use-case", extra["use_case"]])
        if extra.get("extra"): args.extend(["--extra", extra["extra"]])

    try:
        run_mmx(args)
        mark_job(job_id, status="completed", file_name=out_path.name, file_path=str(out_path))
        # Send email with MP3 attachment
        if email and out_path.exists():
            email_ok = send_email(email, out_path, prompt)
            mark_job(job_id, email_sent=email_ok)
    except Exception as exc:
        mark_job(job_id, status="error", error=str(exc))


class MusicHandler(BaseHTTPRequestHandler):
    server_version = "MiniMaxMusicTool/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"{self.log_date_time_string()} {self.address_string()} {fmt % args}")

    def send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload).encode("utf-8")
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
            token_ok = True
            html = INDEX_HTML.replace(
                "__TOKEN_STATUS__",
                "MINIMAX_API_TOKEN detected" if token_ok else "MINIMAX_API_TOKEN missing",
            ).replace("__TOKEN_CLASS__", "ok" if token_ok else "missing")
            data = html.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
            return

        if path == "/api/jobs":
            client_id = normalized_client_id(self.headers.get("X-Client-Id"))
            with JOBS_LOCK:
                jobs = sorted(
                    (public_job(job) for job in JOBS.values() if job.get("owner_id") == client_id),
                    key=lambda item: item.get("created_at", ""),
                    reverse=True,
                )
            self.send_json({"jobs": jobs})
            return

        if path == "/api/health":
            self.send_json({
                "ok": True,
                "minimax_configured": bool(MINIMAX_API_TOKEN),
                "smtp_configured": bool(SMTP_USER and SMTP_PASSWORD),
                "smtp_host": SMTP_HOST,
                "smtp_port": SMTP_PORT,
            })
            return

        if path.startswith("/download/"):
            self.handle_download(path.removeprefix("/download/"))
            return

        if path == "/favicon.ico":
            self.send_response(HTTPStatus.NO_CONTENT)
            self.end_headers()
            return

        self.send_text("Not found", HTTPStatus.NOT_FOUND)

    def do_DELETE(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if not path.startswith("/api/jobs/"):
            self.send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
            return
        job_id = path[len("/api/jobs/"):]
        client_id = normalized_client_id(self.headers.get("X-Client-Id"))
        with JOBS_LOCK:
            if job_id in JOBS and JOBS[job_id].get("owner_id") == client_id:
                del JOBS[job_id]
                save_jobs_locked()
                self.send_json({"ok": True})
            else:
                self.send_json({"error": "Job not found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/api/jobs":
            self.send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
            return

        length_header = self.headers.get("Content-Length")
        try:
            length = int(length_header or "0")
        except ValueError:
            self.send_json({"error": "Invalid Content-Length."}, HTTPStatus.BAD_REQUEST)
            return

        if length <= 0 or length > MAX_BODY_BYTES:
            self.send_json({"error": "Request body is empty or too large."}, HTTPStatus.BAD_REQUEST)
            return

        try:
            body = self.rfile.read(length)
            form = json.loads(body.decode("utf-8"))
            if not isinstance(form, dict):
                raise ValueError("Expected a JSON object.")
            prompt = str(form.get("prompt", "")).strip()
            lyrics_raw = str(form.get("lyrics", "")).strip()
            lyrics = lyrics_raw if lyrics_raw not in ("", "Your lyrics here...") else ""
            is_instrumental = bool(form.get("is_instrumental"))
            lyrics_optimizer = bool(form.get("lyrics_optimizer")) and not is_instrumental
            email = str(form.get("email", "")).strip()
            if not email:
                raise ValueError("Email is required.")
            if not prompt:
                raise ValueError("Prompt is required.")
            if len(prompt) > 2000:
                raise ValueError("Prompt must be 2000 characters or fewer.")
            if len(lyrics) > 3500:
                raise ValueError("Lyrics must be 3500 characters or fewer.")
            if not is_instrumental and not lyrics and not lyrics_optimizer:
                raise ValueError("Lyrics are required for vocal tracks unless auto lyrics is enabled.")
            # extra model params
            extra = {
                "genre": str(form.get("genre", "")).strip(),
                "mood": str(form.get("mood", "")).strip(),
                "instruments": str(form.get("instruments", "")).strip(),
                "tempo": str(form.get("tempo", "")).strip(),
                "bpm": str(form.get("bpm", "")).strip(),
                "key": str(form.get("key", "")).strip(),
                "vocals": str(form.get("vocals", "")).strip(),
                "structure": str(form.get("structure", "")).strip(),
                "references": str(form.get("references", "")).strip(),
                "avoid": str(form.get("avoid", "")).strip(),
                "use_case": str(form.get("use_case", "")).strip(),
                "extra": str(form.get("extra", "")).strip(),
            }
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return

        job_id = secrets.token_urlsafe(12)
        client_id = normalized_client_id(self.headers.get("X-Client-Id"))
        job = {
            "id": job_id,
            "owner_id": client_id,
            "status": "queued",
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "prompt": prompt,
            "email": email,
            "is_instrumental": is_instrumental,
            "lyrics_optimizer": lyrics_optimizer,
            "file_name": None,
            "file_path": None,
            "error": None,
            "extra_info": None,
            "trace_id": None,
            "email_sent": False,
            "extra": extra,
        }

        with JOBS_LOCK:
            JOBS[job_id] = job
            save_jobs_locked()

        thread = threading.Thread(target=generate_music, args=(job_id, prompt, is_instrumental, lyrics_optimizer, lyrics, email, extra), daemon=True)
        thread.start()
        self.send_json({"job": public_job(job)}, HTTPStatus.ACCEPTED)

    def handle_download(self, encoded_job_id: str) -> None:
        job_id = urllib.parse.unquote(encoded_job_id)
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        client_id = normalized_client_id(self.headers.get("X-Client-Id") or (query.get("client_id") or [""])[0])
        with JOBS_LOCK:
            job = JOBS.get(job_id)
            if not job or job.get("owner_id") != client_id:
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

        if output_root not in file_path.parents:
            self.send_text("Invalid file path", HTTPStatus.BAD_REQUEST)
            return

        content_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
        file_name = file_path.name
        file_size = file_path.stat().st_size
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(file_size))
        self.send_header("Content-Disposition", f'attachment; filename="{file_name}"')
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        with file_path.open("rb") as file_obj:
            while True:
                chunk = file_obj.read(1024 * 256)
                if not chunk:
                    break
                self.wfile.write(chunk)


def send_email(to_email: str, file_path: Path, prompt: str) -> bool:
    """Send MP3 file as attachment via Gmail SMTP."""
    try:
        file_data = file_path.read_bytes()
        content_type, _ = mimetypes.guess_type(str(file_path))
        if content_type is None:
            content_type = "application/octet-stream"

        # Build multipart email
        msg = email.mime.multipart.MIMEMultipart()
        msg["From"] = SMTP_USER
        msg["To"] = to_email
        msg["Subject"] = "🎵 Terry Music — Your Generated Track"

        body = email.mime.text.MIMEText(
            f"Hi! Your music track is ready.\n\n"
            f'Prompt: "{prompt}"\n'
            f"File: {file_path.name}\n\n"
            f"Enjoy!\n— Terry Music",
            "plain",
            "utf-8",
        )
        msg.attach(body)

        # Attach MP3
        attachment = email.mime.base.MIMEBase("application", "octet-stream")
        attachment.set_payload(file_data)
        email.encoders.encode_base64(attachment)
        attachment.add_header(
            "Content-Disposition",
            f'attachment; filename="{file_path.name}"',
        )
        msg.attach(attachment)

        # Send via SMTP SSL (port 465)
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as server:
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(SMTP_USER, to_email, msg.as_string())
        return True
    except Exception as exc:
        print(f"[email] failed to send to {to_email}: {exc}")
        return False


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    load_jobs()
    server = ThreadingHTTPServer((HOST, PORT), MusicHandler)
    print(f"Terry Music Tool running at http://{HOST}:{PORT}")
    print(f"Output directory: {OUTPUT_DIR}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
