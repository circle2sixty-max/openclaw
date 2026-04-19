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
VOICE_CLONE_SINGING_ENDPOINT = os.getenv("MINIMAX_VOICE_CLONE_SINGING_ENDPOINT", "/v1/voice_clone_singing")
VOICE_CLONE_SINGING_MODEL = os.getenv("MINIMAX_VOICE_CLONE_SINGING_MODEL", "music-2.6")
LYRICS_REQUEST_TIMEOUT = float(os.getenv("LYRICS_REQUEST_TIMEOUT", "4"))
VOICE_LIST_TIMEOUT = float(os.getenv("VOICE_LIST_TIMEOUT", "15"))
JOB_TIMEOUT_SECONDS = int(os.getenv("JOB_TIMEOUT_SECONDS", "900"))
JOB_RETENTION_SECONDS = int(os.getenv("JOB_RETENTION_SECONDS", "604800"))

DEFAULT_SYSTEM_VOICES = [
    "Chinese (Mandarin)_Reliable_Executive",
    "Chinese (Mandarin)_News_Anchor",
    "Chinese (Mandarin)_Mature_Woman",
    "Chinese (Mandarin)_Sweet_Lady",
    "Chinese (Mandarin)_Lyrical_Voice",
    "Cantonese_ProfessionalHost（F)",
    "Cantonese_GentleLady",
    "English_Trustworthy_Man",
    "English_Graceful_Lady",
    "English_Whispering_girl",
    "Japanese_KindLady",
    "Japanese_CalmLady",
    "Korean_SweetGirl",
    "Korean_CalmLady",
    "Spanish_SereneWoman",
    "Spanish_Narrator",
    "Portuguese_SentimentalLady",
    "French_Female_News Anchor",
    "French_MaleNarrator",
    "German_FriendlyMan",
    "Russian_ReliableMan",
    "Italian_Narrator",
    "Arabic_CalmWoman",
]

VOICE_PREVIEW_TEXTS = {
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
}
VOICE_PREVIEW_LANGUAGES = tuple(sorted(VOICE_PREVIEW_TEXTS, key=len, reverse=True))
VOICE_ID_SAFE_RE = re.compile(r"^[A-Za-z0-9_()./\- （）]+$")


def _detect_lang_from_voice_id(voice_id: str) -> str:
    value = str(voice_id or "").strip()
    for lang in VOICE_PREVIEW_LANGUAGES:
        if value == lang or value.startswith(f"{lang}_") or value.startswith(f"{lang} "):
            return lang
    return "English"


def _is_safe_voice_id(voice_id: str) -> bool:
    value = str(voice_id or "").strip()
    return 1 <= len(value) <= 160 and ".." not in value and bool(VOICE_ID_SAFE_RE.fullmatch(value))


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
    .ui-icon { width: 1.1em; height: 1.1em; display: inline-block; fill: currentColor; flex: 0 0 auto; }
    .logo-icon { width: 36px; height: 36px; background: var(--gradient-green); border-radius: 10px; display: flex; align-items: center; justify-content: center; color: #06100b; animation: glow 3s ease-in-out infinite; }
    .logo-icon .ui-icon { width: 20px; height: 20px; }
    .header-actions { display: flex; gap: 8px; align-items: center; }
    .header-btn { display: flex; align-items: center; justify-content: center; width: 40px; height: 40px; border: none; border-radius: 50%; background: var(--bg-tertiary); color: var(--text-secondary); cursor: pointer; font-size: 18px; transition: var(--transition); }
    .header-btn .ui-icon { width: 18px; height: 18px; }
    .header-btn:hover { background: var(--bg-elevated); color: var(--text-primary); transform: scale(1.05); }
    .lang-toggle { width: auto; padding: 0 14px; border-radius: 20px; font-size: 13px; font-weight: 600; }
    .lang-menu-item { display: flex; align-items: center; justify-content: space-between; padding: 10px 16px; cursor: pointer; font-size: 14px; color: var(--text-secondary); transition: var(--transition); }
    .lang-menu-item:hover { background: var(--bg-tertiary); color: var(--text-primary); }
    .lang-menu-item.active { color: var(--accent); font-weight: 600; }
    .lang-menu-item .lang-check { font-size: 12px; }
    /* Main Layout */
    .app-body { display: flex; flex: 1; overflow: hidden; }
    /* Sidebar */
    .sidebar { width: 280px; background: var(--bg-secondary); border-right: 1px solid var(--border); display: flex; flex-direction: column; flex-shrink: 0; }
    .sidebar-nav { padding: 16px 12px; }
    .nav-item { display: flex; align-items: center; gap: 12px; padding: 12px 16px; border-radius: var(--radius-md); color: var(--text-secondary); text-decoration: none; font-weight: 500; cursor: pointer; transition: var(--transition); }
    .nav-item:hover { background: var(--bg-tertiary); color: var(--text-primary); }
    .nav-item.active { background: var(--accent-dim); color: var(--accent); }
    .nav-icon { width: 24px; display: inline-flex; align-items: center; justify-content: center; color: currentColor; }
    .nav-icon .ui-icon { width: 19px; height: 19px; }
    .sidebar-section { padding: 8px 12px; }
    .sidebar-section-title { padding: 8px 16px; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px; color: var(--text-muted); }
    .playlist-item { display: flex; align-items: center; gap: 10px; padding: 8px 16px; border-radius: var(--radius-sm); color: var(--text-secondary); cursor: pointer; transition: var(--transition); }
    .playlist-icon { width: 18px; display: inline-flex; align-items: center; justify-content: center; }
    .playlist-icon .ui-icon { width: 17px; height: 17px; }
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
    .form-input { width: 100%; padding: 12px 16px; background: var(--bg-tertiary); border: 1px solid var(--border); border-radius: var(--radius-md); color: var(--text-primary); font-size: 14px; transition: var(--transition); }
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
    .voice-section { background: var(--bg-tertiary); border: 1px solid var(--border); border-radius: var(--radius-md); padding: 16px; }
    .voice-top-row { display: flex; align-items: center; gap: 12px; margin-bottom: 12px; }
    .voice-status { font-size: 13px; color: var(--text-secondary); }
    .voice-status.success { color: var(--accent); }
    .voice-status.error { color: var(--danger); }
    /* Voice Picker */
    .voice-picker-section { margin-bottom: 16px; }
    .voice-picker-label { font-size: 12px; font-weight: 600; color: var(--text-secondary); margin-bottom: 8px; display: flex; align-items: center; justify-content: space-between; gap: 12px; }
    .voice-picker-selected { font-size: 12px; color: var(--accent); font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 260px; }
    .voice-picker-scroll { height: 308px; overflow: hidden; overscroll-behavior: contain; border: 1px solid var(--border-light); border-radius: 8px; background: linear-gradient(145deg, rgba(18,18,26,0.96), rgba(9,14,16,0.98)); box-shadow: inset 0 1px 0 rgba(255,255,255,0.04), var(--shadow-sm); padding: 8px; }
    .voice-picker-shell { display: grid; grid-template-columns: 156px minmax(0, 1fr); gap: 8px; height: 100%; min-height: 0; }
    .voice-lang-list,
    .voice-option-list { min-height: 0; overflow-y: auto; overscroll-behavior: contain; -webkit-overflow-scrolling: touch; scrollbar-width: thin; scrollbar-color: var(--border-light) transparent; }
    .voice-lang-list::-webkit-scrollbar,
    .voice-option-list::-webkit-scrollbar { width: 6px; height: 6px; }
    .voice-lang-list::-webkit-scrollbar-track,
    .voice-option-list::-webkit-scrollbar-track { background: transparent; }
    .voice-lang-list::-webkit-scrollbar-thumb,
    .voice-option-list::-webkit-scrollbar-thumb { background: var(--border-light); border-radius: 3px; }
    .voice-lang-list { display: flex; flex-direction: column; gap: 6px; padding: 4px; border: 1px solid rgba(255,255,255,0.04); border-radius: 8px; background: rgba(0,0,0,0.16); }
    .voice-lang-btn { width: 100%; min-height: 34px; display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: center; gap: 8px; padding: 8px 9px; border: 1px solid transparent; border-radius: 8px; background: transparent; color: var(--text-secondary); font-size: 11px; font-weight: 700; text-align: left; cursor: pointer; transition: var(--transition); }
    .voice-lang-btn:hover { color: var(--text-primary); background: var(--bg-tertiary); border-color: var(--border); }
    .voice-lang-btn.active { color: var(--accent); background: linear-gradient(135deg, rgba(29,185,84,0.18), rgba(29,185,84,0.06)); border-color: rgba(29,185,84,0.55); box-shadow: inset 3px 0 0 var(--accent); }
    .voice-lang-name { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .voice-lang-count { display: inline-flex; align-items: center; justify-content: center; min-width: 24px; height: 18px; padding: 0 6px; border-radius: 999px; background: var(--bg-elevated); color: var(--text-muted); font-size: 10px; font-weight: 700; }
    .voice-lang-btn.active .voice-lang-count { background: rgba(29,185,84,0.2); color: var(--accent); }
    .voice-custom-row { margin-top: auto; padding-top: 6px; border-top: 1px solid var(--border); }
    .voice-custom-btn { width: 100%; min-height: 34px; padding: 8px 9px; background: var(--bg-elevated); border: 1px solid var(--border); border-radius: 8px; font-size: 11px; font-weight: 700; color: var(--text-primary); cursor: pointer; transition: var(--transition); text-align: left; display: inline-flex; align-items: center; gap: 7px; }
    .voice-custom-btn .ui-icon { width: 14px; height: 14px; }
    .voice-custom-btn:hover { border-color: var(--accent); color: var(--accent); }
    .voice-custom-btn.active { background: var(--accent-dim); border-color: var(--accent); color: var(--accent); }
    .voice-custom-label { display: block; margin-top: 5px; font-size: 10px; line-height: 1.3; color: var(--text-muted); }
    .voice-option-list { border: 1px solid rgba(255,255,255,0.04); border-radius: 8px; background: rgba(255,255,255,0.025); }
    .voice-options-head { position: sticky; top: 0; z-index: 2; display: flex; align-items: center; justify-content: space-between; gap: 10px; padding: 8px 10px; background: rgba(18,18,26,0.96); border-bottom: 1px solid var(--border); backdrop-filter: blur(10px); }
    .voice-options-title { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 11px; font-weight: 800; color: var(--text-primary); }
    .voice-options-meta { flex: 0 0 auto; color: var(--text-muted); font-size: 10px; font-weight: 700; }
    .voice-items { display: grid; grid-template-columns: repeat(auto-fill, minmax(136px, 1fr)); gap: 7px; padding: 9px; align-content: start; }
    .voice-pill { width: 100%; min-width: 0; min-height: 38px; display: grid; grid-template-columns: minmax(0, 1fr) 24px; align-items: center; gap: 8px; padding: 7px 7px 7px 10px; background: rgba(26,26,37,0.9); border: 1px solid var(--border); border-radius: 8px; font-size: 11px; color: var(--text-secondary); cursor: pointer; transition: var(--transition); line-height: 1.2; text-align: left; }
    .voice-pill:hover { border-color: var(--accent); color: var(--text-primary); transform: translateY(-1px); }
    .voice-pill.selected { background: var(--accent-dim); border-color: var(--accent); color: var(--accent); box-shadow: inset 0 0 0 1px rgba(29,185,84,0.16); }
    .voice-pill.playing { background: var(--accent-dim); border-color: var(--accent); color: var(--accent); animation: pulse 1s ease-in-out infinite; }
    .voice-pill .play-icon { width: 24px; height: 24px; display: inline-flex; align-items: center; justify-content: center; border-radius: 50%; background: var(--bg-elevated); color: var(--accent); font-size: 9px; }
    .voice-pill .play-icon svg { width: 11px; height: 11px; fill: currentColor; }
    .voice-pill .voice-name { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
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
    .jobs-panel { background: var(--bg-secondary); border: 1px solid var(--border); border-radius: var(--radius-lg); padding: 20px; margin-top: 24px; max-width: 900px; }
    .jobs-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
    .jobs-title { font-size: 16px; font-weight: 700; color: var(--text-primary); }
    .jobs-list { display: flex; flex-direction: column; gap: 10px; max-height: 400px; overflow-y: auto; }
    .job-card { display: flex; align-items: center; gap: 14px; padding: 14px 16px; background: var(--bg-tertiary); border: 1px solid var(--border); border-radius: var(--radius-md); transition: var(--transition); cursor: pointer; }
    .job-card:hover { border-color: var(--accent); transform: translateY(-2px); box-shadow: var(--shadow-md); }
    .job-art { width: 56px; height: 56px; background: var(--gradient-green); border-radius: var(--radius-sm); display: flex; align-items: center; justify-content: center; font-size: 24px; flex-shrink: 0; transition: var(--transition); }
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
    .job-actions { display: flex; gap: 8px; }
    .job-action-btn { padding: 8px 12px; background: var(--bg-elevated); border: 1px solid var(--border); border-radius: var(--radius-sm); color: var(--text-secondary); font-size: 12px; font-weight: 600; cursor: pointer; transition: var(--transition); display: inline-flex; align-items: center; gap: 6px; text-decoration: none; }
    .job-action-btn svg { width: 13px; height: 13px; fill: currentColor; }
    .job-action-btn:hover { border-color: var(--accent); color: var(--accent); }
    .job-action-btn.download { background: var(--accent); color: #000; border: none; }
    .job-action-btn.download:hover { background: var(--accent-hover); }
    .job-empty { text-align: center; padding: 40px 20px; color: var(--text-muted); }
    .job-progress { display: flex; align-items: center; gap: 10px; }
    .progress-bar { flex: 1; height: 4px; background: var(--border); border-radius: 2px; overflow: hidden; }
    .progress-fill { height: 100%; background: var(--accent); transition: width 0.3s; }
    /* Bottom Player */
    .player { position: fixed; bottom: 0; left: 0; right: 0; min-height: 104px; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); border-top: 1px solid rgba(255,255,255,0.18); box-shadow: 0 -18px 60px rgba(0,0,0,0.42); backdrop-filter: blur(18px); -webkit-backdrop-filter: blur(18px); display: flex; align-items: center; padding: 16px 24px; gap: 20px; z-index: 100; overflow: visible; }
    .player::before { content: ""; position: absolute; inset: 0; background: linear-gradient(180deg, rgba(255,255,255,0.14), rgba(255,255,255,0)); pointer-events: none; }
    .player > * { position: relative; z-index: 1; }
    .player-track { display: flex; align-items: center; gap: 14px; width: 300px; min-width: 0; flex-shrink: 0; }
    .player-art { width: 62px; height: 62px; background: linear-gradient(135deg, #1DB954 0%, #34d399 48%, #0f766e 100%); border-radius: 8px; display: flex; align-items: center; justify-content: center; color: #fff; box-shadow: 0 14px 34px rgba(29,185,84,0.24), inset 0 1px 0 rgba(255,255,255,0.24); }
    .player-art svg { width: 32px; height: 32px; fill: currentColor; filter: drop-shadow(0 6px 12px rgba(0,0,0,0.28)); }
    .player-info { min-width: 0; }
    .player-title { font-size: 14px; font-weight: 800; color: var(--text-primary); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; letter-spacing: -0.01em; }
    .player-artist { font-size: 12px; color: var(--text-muted); margin-top: 3px; }
    .player-controls { flex: 1; min-width: 220px; display: flex; flex-direction: column; align-items: center; gap: 10px; }
    .player-buttons { display: flex; align-items: center; gap: 12px; }
    .player-btn { width: 38px; height: 38px; border: 1px solid rgba(255,255,255,0.08); border-radius: 8px; background: rgba(255,255,255,0.05); color: var(--text-secondary); cursor: pointer; padding: 0; display: inline-flex; align-items: center; justify-content: center; transition: var(--transition); }
    .player-btn svg { width: 17px; height: 17px; fill: currentColor; }
    .player-btn:hover { color: var(--text-primary); background: rgba(255,255,255,0.1); transform: translateY(-1px); }
    .player-btn.play { width: 50px; height: 50px; background: #f7fff9; color: #07130c; border-color: transparent; border-radius: 8px; box-shadow: 0 12px 28px rgba(29,185,84,0.24); }
    .player-btn.play svg { width: 21px; height: 21px; }
    .player-btn.play:hover { transform: scale(1.05); background: #ffffff; }
    .player-progress { display: flex; align-items: center; gap: 10px; width: 100%; max-width: 620px; }
    .player-time { font-size: 11px; color: var(--text-muted); min-width: 40px; text-align: center; font-variant-numeric: tabular-nums; }
    .player-bar { flex: 1; height: 6px; background: rgba(255,255,255,0.1); border-radius: 6px; cursor: pointer; position: relative; overflow: hidden; }
    .player-bar-fill { height: 100%; background: linear-gradient(90deg, #1DB954, #34d399); border-radius: 6px; width: 0%; transition: width 0.1s; box-shadow: 0 0 18px rgba(29,185,84,0.45); }
    .player-bar:hover .player-bar-fill { background: linear-gradient(90deg, #1ed760, #67e8f9); }
    .player-extra { display: flex; align-items: center; justify-content: flex-end; gap: 14px; width: 300px; flex-shrink: 0; }
    .player-volume { display: flex; align-items: center; gap: 8px; width: 130px; flex-shrink: 0; }
    .volume-icon { width: 18px; height: 18px; color: var(--text-muted); display: inline-flex; align-items: center; justify-content: center; }
    .volume-icon svg { width: 18px; height: 18px; fill: currentColor; }
    .volume-slider { flex: 1; height: 5px; background: rgba(255,255,255,0.1); border-radius: 5px; cursor: pointer; overflow: hidden; }
    .volume-fill { height: 100%; background: rgba(255,255,255,0.58); border-radius: 5px; width: 70%; }
    .player-lyrics { flex: 1; max-width: 380px; overflow: hidden; text-align: center; padding: 0 12px; }
    .lyrics-text { font-size: 13px; color: var(--text-secondary); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; transition: color 0.3s; }
    .lyrics-text.playing { color: var(--accent); }
    .lyrics-toggle { min-width: 78px; height: 36px; padding: 0 14px; border: 1px solid rgba(255,255,255,0.12); border-radius: 8px; background: rgba(255,255,255,0.06); color: var(--text-secondary); font-size: 12px; font-weight: 800; letter-spacing: 0.01em; cursor: pointer; transition: var(--transition); }
    .lyrics-toggle:hover:not(:disabled), .lyrics-toggle.active { background: rgba(29,185,84,0.18); border-color: rgba(29,185,84,0.52); color: var(--accent); }
    .lyrics-toggle:disabled { opacity: 0.45; cursor: not-allowed; }
    .lyrics-panel { position: fixed; right: 24px; bottom: 122px; width: min(460px, calc(100vw - 48px)); max-height: min(56vh, 540px); display: none; z-index: 101; border: 1px solid rgba(255,255,255,0.12); border-radius: 8px; background: linear-gradient(145deg, rgba(18,18,26,0.96), rgba(6,10,9,0.96)); box-shadow: 0 24px 80px rgba(0,0,0,0.48); backdrop-filter: blur(18px); -webkit-backdrop-filter: blur(18px); overflow: hidden; }
    .lyrics-panel.open { display: block; animation: slide-up 0.24s ease-out forwards; }
    .lyrics-panel-header { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 16px 18px; border-bottom: 1px solid rgba(255,255,255,0.08); }
    .lyrics-panel-title { font-size: 13px; font-weight: 900; color: var(--text-primary); letter-spacing: 0.08em; text-transform: uppercase; }
    .lyrics-panel-close { border: 0; background: transparent; color: var(--text-muted); font-size: 12px; font-weight: 800; cursor: pointer; padding: 6px 0; }
    .lyrics-panel-close:hover { color: var(--accent); }
    .lyrics-lines { max-height: calc(min(56vh, 540px) - 58px); overflow-y: auto; padding: 18px 20px 24px; scroll-behavior: smooth; }
    .lyrics-line { color: var(--text-secondary); font-size: 15px; line-height: 1.7; padding: 6px 10px; border-radius: 8px; transition: var(--transition); }
    .lyrics-line.section { margin-top: 8px; color: var(--text-muted); font-size: 11px; font-weight: 900; letter-spacing: 0.1em; text-transform: uppercase; }
    .lyrics-line.active { color: #06100b; background: linear-gradient(90deg, #1DB954, #9af7be); box-shadow: 0 10px 28px rgba(29,185,84,0.18); transform: translateX(4px); font-weight: 800; }
    .lyrics-empty { color: var(--text-muted); font-size: 13px; line-height: 1.6; padding: 14px 10px; }
    /* Fullscreen Lyrics Modal */
    .lyrics-fullscreen-btn { width: 36px; height: 36px; border: 1px solid rgba(255,255,255,0.12); border-radius: 8px; background: rgba(255,255,255,0.06); color: var(--text-secondary); font-size: 16px; cursor: pointer; transition: var(--transition); display: flex; align-items: center; justify-content: center; }
    .lyrics-fullscreen-btn:hover { background: rgba(29,185,84,0.18); border-color: rgba(29,185,84,0.52); color: var(--accent); }
    #lyricsFullscreenModal { display: none; position: fixed; inset: 0; z-index: 9999; flex-direction: column; }
    #lyricsFullscreenModal.open { display: flex; animation: lfm-in 0.28s cubic-bezier(0.16, 1, 0.3, 1) forwards; }
    @keyframes lfm-in { from { opacity: 0; transform: translateY(60px); } to { opacity: 1; transform: translateY(0); } }
    .lfm-bg { position: absolute; inset: 0; background: linear-gradient(160deg, #0d0d1a 0%, #0a0a14 50%, #06060e 100%); backdrop-filter: blur(40px); -webkit-backdrop-filter: blur(40px); }
    .lfm-header { position: relative; z-index: 1; display: flex; align-items: center; justify-content: space-between; padding: 24px 32px 16px; border-bottom: 1px solid rgba(255,255,255,0.07); }
    .lfm-track-info { flex: 1; min-width: 0; }
    .lfm-title { font-size: 18px; font-weight: 800; color: #fff; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .lfm-artist { font-size: 13px; color: rgba(255,255,255,0.45); margin-top: 2px; }
    .lfm-close { width: 40px; height: 40px; border: 1px solid rgba(255,255,255,0.15); border-radius: 50%; background: rgba(255,255,255,0.06); color: rgba(255,255,255,0.6); font-size: 16px; cursor: pointer; display: flex; align-items: center; justify-content: center; transition: var(--transition); flex-shrink: 0; }
    .lfm-close:hover { background: rgba(255,255,255,0.14); color: #fff; }
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
        <div id="langBtnDropdown" style="position:relative;">
          <button id="langBtn" class="header-btn lang-toggle" aria-haspopup="listbox" aria-expanded="false">EN ▾</button>
          <div id="langMenu" class="lang-menu" role="listbox" style="display:none;position:absolute;top:100%;right:0;min-width:160px;background:var(--bg-elevated);border:1px solid var(--border);border-radius:8px;padding:6px 0;z-index:9999;box-shadow:0 4px 16px rgba(0,0,0,0.15);">
          </div>
        </div>
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
              <div id="voicePickerScroll" class="voice-picker-scroll">
                <div style="padding:20px;text-align:center;color:var(--text-muted);font-size:13px;" id="voicePickerLoading">
                  <span data-i18n="voicePickerLoading">Loading voices...</span>
                </div>
              </div>
              <input type="hidden" id="vocals" data-i18n-placeholder="vocalsPlaceholder" placeholder="warm male vocal, bright female vocal, duet">
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
    let _lyricsLanguage = "auto"; // "auto" = match voice language, or an explicit IETF language tag
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

    function t(key) { return (I18N[lang] && I18N[lang][key]) || (I18N.en && I18N.en[key]) || key; }
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
          ? `<button class="job-action-btn download" onclick="playJob('${escapeHtml(job.id)}')">${UI_ICONS.play}<span>Play</span></button><a class="job-action-btn download" href="${downloadUrl}" download="${fileName}">${t("download")}</a>`
          : isRunning ? `<span style="font-size:12px;color:var(--text-muted);"><span class="spinner" style="width:12px;height:12px;border-width:1.5px;"></span> ${statusLabel(status)}...</span>` : "";
        return `<div class="job-card ${completedClass}" data-job-id="${escapeHtml(job.id)}" style="animation-delay:${idx * 50}ms">
          <div class="job-art">${statusIcon(job.status)}</div>
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
        // Play completion sound and show toast when a job transitions to completed
        newJobs.forEach(job => {
          const prev = prevJobs[job.id];
          if (prev && prev.status !== "completed" && job.status === "completed") {
            SoundSystem.play("complete");
            showToast(t("toastMusicReady") + (job.song_title || job.prompt || "Untitled"), "success", 5000);
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
        voice_mode: voiceSingingMode && voiceSingingMode.checked ? "voice_clone_singing" : "cover",
        genre: get("genre"), mood: get("mood"), instruments: get("instruments"), tempo: get("tempo"), bpm: get("bpm"), key: get("key"),
        vocals: get("vocals"), structure: get("structure"), references: get("references"), avoid: get("avoid"), use_case: get("useCase"), extra: get("extra"),
        voice_id: clonedVoiceId || _selectedVoiceId || "",
        lyrics_language: _lyricsLanguage || "auto",
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
    }
    // ── Language menu (dropdown) ──────────────────────────────────────
    const LANG_LABELS = {
      "en": "English", "zh": "中文", "yue": "粤语", "ko": "한국어",
      "ja": "日本語", "es": "Español", "fr": "Français", "de": "Deutsch",
      "pt": "Português", "it": "Italiano", "ru": "Русский", "ar": "العربية",
      "hi": "हिन्दी", "id": "Bahasa Indonesia", "vi": "Tiếng Việt",
      "th": "ไทย", "tr": "Türkçe", "pl": "Polski", "nl": "Nederlands",
      "sv": "Svenska", "no": "Norsk", "da": "Dansk", "fi": "Suomi",
      "cs": "Čeština", "ro": "Română", "hu": "Magyar", "uk": "Українська"
    };

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

    function _getAvailableUIVoices() {
      const groups = _voiceGroupsFromCache();
      const langSet = new Set();
      const uniqueLangs = [];
      for (const g of groups) {
        if (!langSet.has(g.lang)) { langSet.add(g.lang); uniqueLangs.push(g.lang); }
      }
      return uniqueLangs;
    }

    function _buildLangMenu() {
      const menu = document.getElementById("langMenu");
      if (!menu) return;
      const voices = _getAvailableUIVoices();
      const current = lang;
      let html = '<div style="padding:8px 16px;font-size:11px;color:var(--text-muted);border-bottom:1px solid var(--border);">' + escapeHtml(t("langMenuLabel")) + '</div>';
      // Add all interface languages first
      const allLangs = ["en", "zh", "yue", "ko", "ja", "es", "fr", "de", "pt", "it", "ru", "ar", "hi", "id", "vi", "th", "tr", "pl", "nl", "sv", "no", "da", "fi", "cs", "ro", "hu", "uk"];
      for (const l of allLangs) {
        const label = LANG_LABELS[l] || l;
        const isActive = l === current ? " active" : "";
        const check = l === current ? '<span class="lang-check">✓</span>' : "";
        html += '<div class="lang-menu-item' + isActive + '" data-lang="' + escapeHtml(l) + '" role="option">' + escapeHtml(label) + check + '</div>';
      }
      // If voice languages include something not in interface list, add a divider + section
      const voiceOnlyLangs = voices.filter(v => !VOICE_LANG_TO_IETF[v] && !["en", "zh", "yue", "ko", "ja", "es", "fr", "de", "pt", "it", "ru", "ar", "hi", "id", "vi", "th", "tr", "pl", "nl", "sv", "no", "da", "fi", "cs", "ro", "hu", "uk"].includes(VOICE_LANG_TO_IETF[v] || ""));
      if (voiceOnlyLangs.length > 0) {
        html += '<div style="padding:8px 16px;font-size:11px;color:var(--text-muted);border-top:1px solid var(--border);margin-top:4px;">Voice Languages</div>';
        for (const v of voiceOnlyLangs) {
          html += '<div class="lang-menu-item" data-lang="voice:' + escapeHtml(v) + '" role="option">' + escapeHtml(v) + '</div>';
        }
      }
      menu.innerHTML = html;
      menu.querySelectorAll(".lang-menu-item").forEach(item => {
        item.addEventListener("click", e => {
          e.stopPropagation();
          const val = item.getAttribute("data-lang");
          if (val.startsWith("voice:")) {
            // Switch to a voice-language-only mode — set UI to best match
            const voiceLang = val.slice(6);
            const ifaceLang = VOICE_LANG_TO_IETF[voiceLang] || "en";
            lang = ifaceLang;
            _lyricsLanguage = voiceLang;
          } else {
            lang = val;
            _lyricsLanguage = "auto";
          }
          applyLang();
          _closeLangMenu();
        });
      });
    }

    function _openLangMenu() {
      _buildLangMenu();
      const menu = document.getElementById("langMenu");
      const btn = document.getElementById("langBtn");
      if (menu && btn) { menu.style.display = "block"; btn.setAttribute("aria-expanded", "true"); }
      // Close on outside click
      setTimeout(() => {
        document.addEventListener("click", _closeLangMenu, { once: true });
      }, 0);
    }

    function _closeLangMenu() {
      const menu = document.getElementById("langMenu");
      const btn = document.getElementById("langBtn");
      if (menu) menu.style.display = "none";
      if (btn) btn.setAttribute("aria-expanded", "false");
    }

    document.getElementById("langBtn").addEventListener("click", e => {
      e.stopPropagation();
      const menu = document.getElementById("langMenu");
      if (menu && menu.style.display === "block") { _closeLangMenu(); }
      else { _openLangMenu(); }
    });

    // ── Lyrics language mismatch check ───────────────────────────────
    function _checkLyricsLanguageMismatch(voiceLang) {
      const lyricsEl = document.getElementById("lyrics");
      if (!lyricsEl || !lyricsEl.value.trim()) return; // No lyrics entered yet, no mismatch
      const lyricsText = lyricsEl.value.trim();
      const ifaceTag = lang; // Current UI language
      // Quick heuristics: count characters that suggest a language
      const hasCJK = /[\u4e00-\u9fff\u3400-\u4dbf]/.test(lyricsText); // CJK Unified Ideographs (Mandarin)
      const hasTraditional = /[\u9fa5\u9fb4-\u9fbf\u20000-\u2a6df\u2a700-\u2b73f\u2b740-\u2b81f]/.test(lyricsText) || /[睇|喺|嚟|哋|唔|佢|咁|啲|噶|囖]/.test(lyricsText); // Traditional + Cantonese chars
      const hasHangul = /[\uac00-\ud7af]/.test(lyricsText);
      const hasHiraganaKatakana = /[\u3040-\u30ff]/.test(lyricsText);
      // Determine detected lyrics language
      let detected = "en";
      if (hasHangul) detected = "ko";
      else if (hasHiraganaKatakana) detected = "ja";
      else if (hasTraditional) detected = "yue";
      else if (hasCJK) detected = "zh";
      // Compare with voice lang
      const voiceIetf = VOICE_LANG_TO_IETF[voiceLang] || voiceLang;
      const mismatch = detected !== voiceIetf && !(detected === "yue" && voiceLang === "Cantonese");
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
      soundBtn.setAttribute("aria-label", enabled ? "Mute sounds" : "Unmute sounds");
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
      playerPlay.setAttribute("aria-label", isPaused ? "Play" : "Pause");
    }
    function parseLyrics(rawLyrics) {
      return String(rawLyrics || "")
        .split(/\r?\n/)
        .map(line => line.trim())
        .filter(Boolean)
        .map((line, index) => {
          const isSection = /^\[[^\]]+\]$/.test(line);
          const text = isSection ? line : (line.replace(/^\[[^\]]+\]\s*/, "").trim() || line);
          return { index, text, isSection };
        });
    }
    function getLyricRows() {
      if (!currentTrack) return [];
      const source = currentTrack.lyrics || "";
      if (currentTrack._lyricsSource !== source) {
        currentTrack._lyricsSource = source;
        currentTrack._lyricsRows = parseLyrics(source);
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
    // ── Time-indexed lyrics sync (weighted by text length for non-timestamp lyrics) ──
    let _lyricTimeIndex = [];  // [{start, end, index}, ...]
    let _lyricTimeIndexKey = "";

    function _buildLyricTimeIndex(rows, duration) {
      const playableRows = rows.filter(row => !row.isSection && row.text);
      if (!playableRows.length || !duration) return;
      const totalChars = playableRows.reduce((s, r) => s + r.text.length, 0) || 1;
      let accumulated = 0;
      _lyricTimeIndex = playableRows.map((row, i) => {
        const weight = row.text.length / totalChars;
        const start = accumulated;
        accumulated += weight;
        return { start: start * duration, end: (i === playableRows.length - 1) ? duration : (start + weight) * duration, index: row.index };
      });
    }

    function currentLyricRowIndex(rows) {
      const playableRows = rows.filter(row => !row.isSection && row.text);
      if (!playableRows.length) return rows[0] ? rows[0].index : -1;
      if (!audioPlayer.duration || Number.isNaN(audioPlayer.duration)) return playableRows[0].index;

      // Use timestamp-based sync if available (2+ timestamps)
      const tsData = _parseTimestamps(currentTrack ? currentTrack.lyrics || "" : "");
      if (tsData.length >= 2) {
        const t = audioPlayer.currentTime;
        for (let i = tsData.length - 1; i >= 0; i--) {
          if (t >= tsData[i].time) {
            const text = tsData[i].text;
            const found = rows.find(r => r.text === text);
            if (found) return found.index;
          }
        }
        return playableRows[0].index;
      }

      // Rebuild time index if track or rows changed
      const rowsKey = playableRows.map(r => r.index + ":" + r.text.length).join("|");
      const cacheKey = (currentTrack ? currentTrack.src : "") + "::" + rowsKey;
      if (cacheKey !== _lyricTimeIndexKey) {
        _lyricTimeIndexKey = cacheKey;
        _buildLyricTimeIndex(rows, audioPlayer.duration);
      }

      // Binary search for current time slot
      const t = audioPlayer.currentTime;
      let lo = 0, hi = _lyricTimeIndex.length - 1, result = playableRows[0].index;
      while (lo <= hi) {
        const mid = (lo + hi) >> 1;
        if (_lyricTimeIndex[mid].start <= t) { result = _lyricTimeIndex[mid].index; lo = mid + 1; }
        else hi = mid - 1;
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
    let _cachedVoices = null;
    let _voiceAudio = null;
    let _voicePlayPending = null;
    let _selectedVoiceId = "";
    let _activeVoiceLang = "Chinese (Mandarin)";

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

    function _voiceGroupsFromCache() {
      const groups = VOICE_LANG_GROUPS.map(function(group) {
        return { lang: group.lang, label: group.label, voices: [] };
      });
      const otherVoices = [];
      for (const voice of (_cachedVoices || [])) {
        let placed = false;
        for (const group of groups) {
          if (voice.startsWith(group.lang + "_") || voice.startsWith(group.lang)) {
            group.voices.push(voice);
            placed = true;
            break;
          }
        }
        if (!placed) otherVoices.push(voice);
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
      let name = String(voice || "");
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
      html += '<div class="voice-options-head"><span class="voice-options-title">' + escapeHtml(activeGroup.label) + '</span><span class="voice-options-meta">' + activeGroup.voices.length + ' voices</span></div>';
      html += '<div class="voice-items">';
      for (const voice of activeGroup.voices) {
        const displayName = _voiceDisplayName(voice, activeGroup.lang);
        const isSelected = voice === _selectedVoiceId ? " selected" : "";
        html += '<button type="button" class="voice-pill' + isSelected + '" data-voice="' + escapeHtml(voice) + '">';
        html += '<span class="voice-name">' + escapeHtml(displayName) + '</span>';
        html += '<span class="play-icon" aria-hidden="true">' + UI_ICONS.play + '</span>';
        html += '</button>';
      }
      html += "</div>";
      html += "</div>";
      html += "</div>";
      container.innerHTML = html;
      _attachScrollSound(document.getElementById("voiceLangList"));
      _attachScrollSound(document.getElementById("voiceOptionList"));
      container.querySelectorAll(".voice-lang-btn").forEach(function(btn) {
        btn.addEventListener("click", function(e) {
          e.stopPropagation();
          const langKey = btn.getAttribute("data-lang");
          if (!langKey || langKey === _activeVoiceLang) return;
          _activeVoiceLang = langKey;
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
            playVoicePreview(voiceId);
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
          selectedLabel.textContent = _t("voiceCustomBtn");
          selectedLabel.style.color = "var(--accent)";
        }
        // Open voice recorder
        if (typeof openVoiceRecorder === "function") openVoiceRecorder();
      } else {
        stopVoicePreview();
        const group = _voiceGroupForId(voiceId);
        if (group) {
          _activeVoiceLang = group.lang;
          // Auto-set lyrics language to match voice language
          _lyricsLanguage = group.lang;
          // Check if existing lyrics mismatch
          _checkLyricsLanguageMismatch(group.lang);
        }
        const label = _voiceDisplayName(voiceId, group ? group.lang : "");
        vocalsInput.value = label;
        if (selectedLabel) {
          selectedLabel.textContent = label;
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

    async function playVoicePreview(voiceId) {
      const playId = voiceId;
      stopVoicePreview();
      document.querySelectorAll(".voice-pill").forEach(function(p) {
        p.classList.remove("playing");
        if (p.getAttribute("data-voice") === voiceId) p.classList.add("playing");
      });
      try {
        const res = await fetch("/api/voice/preview?voice_id=" + encodeURIComponent(voiceId), { headers: headers({"Accept": "audio/mpeg"}) });
        if (!res.ok) {
          const data = await res.json().catch(() => ({}));
          const errMsg = typeof data.error === "string" ? data.error : data.error?.message || t("voicePreviewError");
          throw new Error(errMsg);
        }
        if (_voicePlayPending !== playId) return;
        const blob = await res.blob();
        if (_voicePlayPending !== playId) return;
        const url = URL.createObjectURL(blob);
        _voiceAudio = new Audio(url);
        _voiceAudio.addEventListener("ended", function() {
          _voicePlayPending = null;
          URL.revokeObjectURL(url);
          document.querySelectorAll(".voice-pill").forEach(function(p) { p.classList.remove("playing"); });
        });
        _voicePlayPending = playId;
        await _voiceAudio.play();
      } catch (err) {
        stopVoicePreview();
        showToast(err.message || t("voicePreviewError"), "error");
        SoundSystem.play("error");
      }
    }

    function stopVoicePreview() {
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
          <div class="rec-step">${lang === "en" ? "Segment" : "段落"} ${idx + 1} / ${segs.length} — ${seg.label} ${UI_ICONS.check}</div>
          <div class="rec-bar"><div class="rec-bar-fill" style="width:${((idx + 1) / segs.length) * 100}%"></div></div>
        </div>
        <div class="rec-script-box">
          <div class="rec-instruction">${seg.desc}</div>
          <div class="rec-script">"${script}"</div>
        </div>
        <div class="rec-review-audio"><audio src="${url}" controls style="height:40px; width:100%;"></audio></div>
        <div class="rec-controls-row">
          <button id="recRerecord" class="ghost" type="button"><svg class="ui-icon" aria-hidden="true"><use href="#icon-refresh"></use></svg> ${lang === "en" ? "Re-record" : "重新录制"}</button>
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

    // ── Fullscreen Lyrics Modal ─────────────────────────────────────
    const lyricsModal = document.createElement("div");
    lyricsModal.id = "lyricsFullscreenModal";
    lyricsModal.innerHTML = `
      <div class="lfm-bg"></div>
      <div class="lfm-header">
        <div class="lfm-track-info">
          <div class="lfm-title" id="lfmTitle">Song Title</div>
          <div class="lfm-artist" id="lfmArtist">Music Speaks</div>
        </div>
        <button class="lfm-close" id="lfmClose" aria-label="Close fullscreen lyrics">✕</button>
      </div>
      <div class="lfm-body" id="lfmBody">
        <div class="lfm-lines" id="lfmLines"></div>
      </div>
      <div class="lfm-footer">
        <div class="lfm-controls">
          <button class="lfm-btn" id="lfmPrev" aria-label="Previous">◀</button>
          <button class="lfm-btn lfm-play" id="lfmPlay" aria-label="Play/Pause">▶</button>
          <button class="lfm-btn" id="lfmNext" aria-label="Next">▶</button>
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
      document.getElementById("lfmTitle").textContent = currentTrack.title || "Untitled";
      document.getElementById("lfmArtist").textContent = "Music Speaks";
      const rows = getLyricRows();
      const playableRows = rows.filter(r => !r.isSection && r.text);
      if (!playableRows.length) {
        document.getElementById("lfmLines").innerHTML = '<div class="lfm-empty">No lyrics available.</div>';
        return;
      }
      document.getElementById("lfmLines").innerHTML = rows.map(row => {
        const cls = row.isSection ? "lfm-line section" : "lfm-line";
        return '<div class="' + cls + '" data-idx="' + row.index + '">' + escapeHtml(row.text) + '</div>';
      }).join("");
      document.getElementById("lfmDuration").textContent = formatTime(audioPlayer.duration);
      document.getElementById("lfmPlay").textContent = audioPlayer.paused ? "▶" : "⏸";
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
      document.getElementById("lfmPlay").textContent = audioPlayer.paused ? "▶" : "⏸";
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
      // Attach continuous timeupdate when modal is open
      _lfmTimeUpdateHandler = () => _updateLfmProgress();
      audioPlayer.addEventListener("timeupdate", _lfmTimeUpdateHandler);
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
    audioPlayer.addEventListener("play", () => { document.getElementById("lfmPlay").textContent = "⏸"; });
    audioPlayer.addEventListener("pause", () => { document.getElementById("lfmPlay").textContent = "▶"; });
    audioPlayer.addEventListener("ended", () => { document.getElementById("lfmPlay").textContent = "▶"; _lfmLastIndex = -1; });
    audioPlayer.addEventListener("loadedmetadata", () => {
      if (lyricsModal.classList.contains("open")) document.getElementById("lfmDuration").textContent = formatTime(audioPlayer.duration);
    });

    // ── Lyrics timestamp parsing (for [00:12.34] format) ────────────
    // If lyrics contain timestamps, use them for precise sync instead of equal division
    const _timestampCache = new Map();
    function _parseTimestamps(lyricsText) {
      if (_timestampCache.has(lyricsText)) return _timestampCache.get(lyricsText);
      const lines = lyricsText.split(/\r?\n/);
      const results = [];
      for (const line of lines) {
        const m = line.match(/^\[(\d{2}):(\d{2})(?:\.(\d{2,3}))?\](.*)$/);
        if (m) {
          const min = parseInt(m[1]), sec = parseInt(m[2]);
          const ms = m[3] ? (m[3].length === 2 ? parseInt(m[3]) * 10 : parseInt(m[3])) : 0;
          const time = min * 60 + sec + ms / 1000;
          const text = m[4].trim();
          if (text) results.push({ time, text });
        }
      }
      _timestampCache.set(lyricsText, results);
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
    }
    draft = {key: str(form.get(key, "")).strip()[:limit] for key, limit in limits.items()}
    draft["is_instrumental"] = bool(form.get("is_instrumental"))
    draft["lyrics_optimizer"] = bool(form.get("lyrics_optimizer"))
    return draft


def public_job(job: dict[str, Any], include_lyrics: bool = False) -> dict[str, Any]:
    result = {key: job.get(key) for key in ("id", "status", "created_at", "updated_at", "prompt", "song_title", "generated_title", "title_error", "email", "is_instrumental", "lyrics_optimizer", "file_name", "error", "email_sent", "voice_render_mode")}
    if include_lyrics:
        result["lyrics"] = job.get("lyrics", "")
        result["generated_lyrics"] = bool(job.get("generated_lyrics"))
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
        "voice_mode": job.get("voice_mode"),
        "voice_clone_singing_error": job.get("voice_clone_singing_error"),
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
    return cleaned[:LYRICS_CHAR_LIMIT].strip()


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

    last_network_error = None
    for attempt in range(3):
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"MiniMax API error {exc.code}: {body}")
        except urllib.error.URLError as exc:
            last_network_error = exc
            if attempt < 2:
                time.sleep(0.5 * (attempt + 1))
                continue
    reason = last_network_error.reason if last_network_error else "unknown network error"
    raise RuntimeError(f"MiniMax network error: {reason}")


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


class VoiceCloneSingingUnavailable(RuntimeError):
    """Raised when MiniMax voice_clone_singing is absent or not enabled for this key."""


def build_music_option_args(extra: dict[str, Any] | None) -> list[str]:
    option_map = {
        "genre": "--genre", "mood": "--mood", "instruments": "--instruments", "tempo": "--tempo",
        "bpm": "--bpm", "key": "--key", "vocals": "--vocals", "structure": "--structure",
        "references": "--references", "avoid": "--avoid", "use_case": "--use-case", "extra": "--extra",
    }
    args: list[str] = []
    source = extra if isinstance(extra, dict) else {}
    for key, flag in option_map.items():
        value = str(source.get(key, "")).strip()
        if value:
            args.extend([flag, value])
    return args


def extract_audio_bytes_from_response(payload: Any) -> bytes:
    """Find hex/base64/audio URL payloads returned by MiniMax audio APIs."""
    if isinstance(payload, dict):
        for key in ("audio_file", "audio", "audio_data", "data", "file", "result", "output"):
            if key in payload:
                try:
                    return extract_audio_bytes_from_response(payload[key])
                except ValueError:
                    pass
        for key in ("audio_url", "url", "file_url", "download_url"):
            value = payload.get(key)
            if isinstance(value, str) and value.startswith(("http://", "https://")):
                import urllib.request

                with urllib.request.urlopen(value, timeout=120) as resp:
                    return resp.read()
    if isinstance(payload, str):
        text = payload.strip()
        if text.startswith("data:") and "," in text:
            text = text.split(",", 1)[1]
        if re.fullmatch(r"[0-9a-fA-F]+", text) and len(text) % 2 == 0:
            try:
                return bytes.fromhex(text)
            except ValueError:
                pass
        try:
            return base64.b64decode(text, validate=True)
        except Exception:
            pass
    raise ValueError("No audio data found in MiniMax response.")


def voice_clone_singing_error_is_unavailable(error: Exception) -> bool:
    message = str(error).lower()
    return any(token in message for token in (
        "404",
        "not found",
        "not exist",
        "permission",
        "forbidden",
        "unauthorized",
        "access denied",
        "not enabled",
        "no privilege",
    ))


def synthesize_voice_clone_singing(lyrics: str, voice_id: str, output_path: Path, prompt: str = "") -> Path:
    """Try MiniMax's direct voice_clone_singing endpoint and save audio to output_path."""
    if not MINIMAX_API_KEY:
        raise VoiceCloneSingingUnavailable("MINIMAX_API_KEY is not configured.")
    payload = {
        "model": VOICE_CLONE_SINGING_MODEL,
        "voice_id": voice_id,
        "lyrics": lyrics[:LYRICS_CHAR_LIMIT],
        "prompt": prompt[:2000],
        "stream": False,
        "output_format": "hex",
        "audio_setting": {
            "format": "mp3",
            "sample_rate": 44100,
            "bitrate": 256000,
            "channel": 2,
        },
    }
    try:
        resp = _call_minimax_api("POST", VOICE_CLONE_SINGING_ENDPOINT, payload)
        output_path.write_bytes(extract_audio_bytes_from_response(resp))
        if output_path.stat().st_size <= 0:
            raise RuntimeError("voice_clone_singing returned an empty audio file.")
        return output_path
    except Exception as exc:
        if voice_clone_singing_error_is_unavailable(exc):
            raise VoiceCloneSingingUnavailable(str(exc)) from exc
        raise


def generate_voice_cover_audio(prompt: str, lyrics: str, voice_wav: Path, out_path: Path, extra: dict[str, Any] | None = None) -> None:
    """Fallback path: use the recorded voice as reference audio for MiniMax music cover."""
    if not voice_wav.exists():
        raise RuntimeError("Voice recording not found. Please re-record your voice.")
    cover_prompt = prompt.strip() or "A natural singing voice performance with clear melody"
    args = ["music", "cover", "--prompt", cover_prompt, "--audio-file", str(voice_wav), "--out", str(out_path), "--non-interactive"]
    if lyrics:
        args.extend(["--lyrics", lyrics])
    args.extend(build_music_option_args(extra))
    run_mmx(args)


def clean_song_title(text: str) -> str:
    cleaned = re.sub(r"\x1b\[[0-9;]*m", "", text).strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    prefixes = ("title:", "song title:", "songname:", "歌名:", "歌名：", "标题:", "标题：")
    lower = cleaned.lower()
    for prefix in prefixes:
        if lower.startswith(prefix):
            cleaned = cleaned[len(prefix):].strip()
            break
    lines = [line.strip(" \t\r\n\"'`") for line in cleaned.splitlines() if line.strip()]
    title = lines[0] if lines else ""
    title = re.sub(r"^\s*[-*#]+\s*", "", title).strip(" \t\r\n\"'`")
    title = re.sub(r"\s+", " ", title)
    if title.lower().endswith(".mp3"):
        title = title[:-4].strip(" .-_")
    # Enforce length limits: English <=12 words, Chinese <=12 chars
    if re.search(r"[\u4e00-\u9fff]", title):
        title = title[:12]
    else:
        words = title.split()
        if len(words) > 12:
            title = " ".join(words[:12])
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


def _lyrics_content_lines(lyrics: str) -> list[str]:
    lines: list[str] = []
    for raw in (lyrics or "").splitlines():
        line = re.sub(r"\[[^\]]+\]", "", raw).strip()
        line = re.sub(r"^[\-*•]+\s*", "", line).strip()
        if line:
            lines.append(line)
    return lines


def _title_language(text: str, fallback: str = "en") -> str:
    chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", text or ""))
    latin_words = len(re.findall(r"[A-Za-z]{2,}", text or ""))
    return "zh" if chinese_chars >= max(4, latin_words * 2) else fallback


def _normalized_title_compare(text: str) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", (text or "").lower())


def _is_first_line_title(title: str, lyrics: str) -> bool:
    lines = _lyrics_content_lines(lyrics)
    if not lines:
        return False
    first = _normalized_title_compare(lines[0])
    candidate = _normalized_title_compare(title)
    if not candidate:
        return False
    return candidate == first or (len(candidate) >= 8 and first.startswith(candidate))


def _format_chinese_title(title: str) -> str:
    title = "".join(re.findall(r"[\u4e00-\u9fff]+", title or ""))
    if not title:
        return ""
    if len(title) < 4:
        suffix = "未眠" if any(ch in title for ch in "夜星月灯") else "回响"
        title = f"{title}{suffix}"
    if len(title) > 12:
        title = title[:12]
    return title if 4 <= len(title) <= 12 else ""


def _format_english_title(title: str) -> str:
    words = re.findall(r"[A-Za-z0-9]+(?:'[A-Za-z0-9]+)?", title or "")
    if not words:
        return ""
    if len(words) == 1:
        words.append("Dreams")
    words = words[:6]
    small = {"a", "an", "and", "as", "at", "but", "by", "for", "from", "in", "into", "of", "on", "or", "the", "to", "with"}
    titled = []
    for idx, word in enumerate(words):
        lower = word.lower()
        if 0 < idx < len(words) - 1 and lower in small:
            titled.append(lower)
        else:
            titled.append(lower.capitalize())
    return " ".join(titled) if 2 <= len(titled) <= 6 else ""


def normalize_generated_song_title(title: str, lyrics: str = "", preferred_lang: str = "") -> str:
    cleaned = compact_title_candidate(title, max_words=6, max_chars=36)
    if not cleaned:
        return ""
    if preferred_lang == "zh" and not re.search(r"[\u4e00-\u9fff]", cleaned):
        return ""
    lang = preferred_lang or _title_language(cleaned)
    formatted = _format_chinese_title(cleaned) if lang == "zh" else _format_english_title(cleaned)
    if formatted and _is_first_line_title(formatted, lyrics):
        return ""
    return formatted


ZH_IMAGE_TERMS = [
    ("雨夜", "雨夜"), ("雨", "雨"), ("窗", "窗外"), ("夜空", "夜空"), ("夜", "夜色"),
    ("午夜", "午夜"), ("星", "星光"), ("月", "月光"), ("灯", "灯火"), ("城市", "城市"),
    ("街", "街角"), ("海", "海风"), ("浪", "海浪"), ("风", "风"), ("雪", "雪"),
    ("花", "花开"), ("路", "远方"), ("远方", "远方"), ("天空", "天空"), ("银河", "银河"),
    ("晨光", "晨光"), ("黎明", "黎明"), ("回忆", "回忆"), ("时光", "时光"), ("梦", "梦想"),
    ("火", "火焰"), ("山", "山海"), ("河", "河流"), ("列车", "列车"),
]
ZH_EMOTION_TERMS = [
    ("想念", "想念"), ("思念", "想念"), ("孤单", "孤独"), ("孤独", "孤独"),
    ("寂寞", "孤独"), ("温柔", "温柔"), ("勇敢", "勇敢"), ("自由", "自由"),
    ("快乐", "快乐"), ("开心", "快乐"), ("遗憾", "遗憾"), ("等待", "等待"),
    ("告别", "告别"), ("希望", "希望"), ("爱", "爱"), ("心动", "心动"),
    ("伤心", "伤心"), ("痛", "伤痛"), ("成长", "成长"), ("梦想", "梦想"),
    ("不放弃", "不放弃"), ("永不放弃", "不放弃"),
]
ZH_ACTION_TERMS = [
    ("追逐", "追逐"), ("奔跑", "奔跑"), ("奔向", "奔向"), ("等待", "等待"),
    ("告别", "告别"), ("远行", "远行"), ("重逢", "重逢"), ("守护", "守护"),
    ("飞翔", "飞翔"), ("回家", "回家"), ("逃离", "逃离"), ("燃烧", "燃烧"),
    ("绽放", "绽放"), ("前行", "前行"),
]

EN_IMAGE_TERMS = [
    (("rain", "raining", "rainy"), "rain"), (("window", "windows"), "window"),
    (("night", "midnight"), "night"), (("star", "stars", "starlight"), "star"),
    (("moon", "moonlight"), "moon"), (("city", "cities"), "city"),
    (("street", "streets"), "street"), (("neon",), "neon"), (("ocean", "sea", "waves"), "ocean"),
    (("road", "roads", "highway"), "road"), (("home",), "home"), (("fire", "flame", "flames"), "fire"),
    (("sky", "skies"), "sky"), (("sunrise", "dawn", "morning"), "sunrise"),
    (("memory", "memories", "remember"), "memory"), (("dream", "dreams"), "dream"),
    (("shadow", "shadows"), "shadow"), (("river", "rivers"), "river"), (("light", "lights"), "light"),
]
EN_EMOTION_TERMS = [
    (("hope", "hopeful"), "hope"), (("love", "lover", "loved"), "love"),
    (("lonely", "alone", "lonesome"), "loneliness"), (("free", "freedom"), "freedom"),
    (("happy", "joy", "joyful"), "joy"), (("sad", "sorrow", "tears"), "sadness"),
    (("broken", "heartbreak", "heartbroken"), "heartbreak"), (("brave", "courage"), "courage"),
    (("longing", "miss", "missing"), "longing"), (("goodbye", "farewell"), "goodbye"),
    (("wild",), "wild"), (("perfect",), "perfect"),
]
EN_ACTION_TERMS = [
    (("run", "running"), "running"), (("chase", "chasing"), "chasing"),
    (("wait", "waiting"), "waiting"), (("leave", "leaving"), "leaving"),
    (("dance", "dancing"), "dancing"), (("fly", "flying"), "flying"),
    (("burn", "burning"), "burning"), (("rise", "rising"), "rising"),
    (("hold", "holding"), "holding"), (("fall", "falling"), "falling"),
]
EN_TITLE_STOPWORDS = {
    "the", "and", "for", "are", "but", "not", "you", "your", "all", "can", "had", "her", "was",
    "one", "our", "out", "get", "has", "him", "his", "how", "its", "let", "may", "new", "now",
    "old", "see", "two", "way", "who", "did", "say", "she", "too", "use", "that", "with",
    "have", "this", "will", "from", "they", "been", "come", "could", "each", "find", "give",
    "just", "know", "look", "make", "more", "only", "over", "such", "take", "than", "them",
    "then", "very", "when", "what", "into", "inside", "music", "song", "sing", "feel",
}


def _rank_zh_terms(text: str, terms: list[tuple[str, str]]) -> list[str]:
    scores: dict[str, tuple[int, int]] = {}
    for token, label in terms:
        count = text.count(token)
        if not count:
            continue
        first_pos = text.find(token)
        current_score, current_pos = scores.get(label, (0, first_pos))
        scores[label] = (current_score + count * max(1, len(token)), min(current_pos, first_pos))
    return [label for label, _ in sorted(scores.items(), key=lambda item: (-item[1][0], item[1][1], item[0]))]


def _rank_en_terms(text: str, terms: list[tuple[tuple[str, ...], str]]) -> list[str]:
    lowered = text.lower()
    scores: dict[str, tuple[int, int]] = {}
    for variants, label in terms:
        score = 0
        first_pos = len(lowered)
        for variant in variants:
            pattern = r"\b" + re.escape(variant) + r"\b"
            matches = list(re.finditer(pattern, lowered))
            score += len(matches)
            if matches:
                first_pos = min(first_pos, matches[0].start())
        if score:
            current_score, current_pos = scores.get(label, (0, first_pos))
            scores[label] = (current_score + score, min(current_pos, first_pos))
    return [label for label, _ in sorted(scores.items(), key=lambda item: (-item[1][0], item[1][1], item[0]))]


def _fallback_english_keywords(text: str) -> list[str]:
    counts: dict[str, int] = {}
    for word in re.findall(r"[A-Za-z]{4,}", text.lower()):
        if word in EN_TITLE_STOPWORDS:
            continue
        if word.endswith("ing") and len(word) > 6:
            word = word[:-3]
        elif word.endswith("ed") and len(word) > 5:
            word = word[:-2]
        elif word.endswith("s") and len(word) > 5:
            word = word[:-1]
        counts[word] = counts.get(word, 0) + 1
    return [word for word, _ in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:4]]


def _title_signals(lyrics: str, prompt: str = "", lyrics_idea: str = "") -> dict[str, Any]:
    lyric_text = " ".join(_lyrics_content_lines(lyrics))
    source = " ".join(part for part in (lyric_text, lyrics_idea, prompt) if part).strip()
    lang = _title_language(lyric_text or source)
    if lang == "zh":
        return {
            "lang": "zh",
            "images": _rank_zh_terms(source, ZH_IMAGE_TERMS),
            "emotions": _rank_zh_terms(source, ZH_EMOTION_TERMS),
            "actions": _rank_zh_terms(source, ZH_ACTION_TERMS),
            "fallback_words": [],
        }
    return {
        "lang": "en",
        "images": _rank_en_terms(source, EN_IMAGE_TERMS),
        "emotions": _rank_en_terms(source, EN_EMOTION_TERMS),
        "actions": _rank_en_terms(source, EN_ACTION_TERMS),
        "fallback_words": _fallback_english_keywords(source),
    }


def _contains_any(values: list[str], *needles: str) -> bool:
    return any(needle in values for needle in needles)


def _chinese_title_candidates(signals: dict[str, Any], mood: str) -> list[str]:
    images = signals["images"]
    emotions = signals["emotions"]
    actions = signals["actions"]
    candidates: list[str] = []
    if _contains_any(images, "梦想") or _contains_any(emotions, "梦想", "不放弃") or _contains_any(actions, "追逐", "奔跑"):
        candidates += ["追梦不止", "奔向星光", "逆风奔跑", "梦想发光"]
    if _contains_any(images, "雨", "雨夜") and _contains_any(images, "窗外"):
        candidates += ["窗外的雨", "雨落窗前"]
    if _contains_any(images, "雨", "雨夜") and _contains_any(images, "回忆", "时光"):
        candidates += ["雨中的回忆", "回忆里的雨"]
    if _contains_any(images, "雨", "雨夜") and _contains_any(emotions, "想念", "孤独", "遗憾"):
        candidates += ["雨夜想念", "雨一直下"]
    if _contains_any(images, "夜色", "午夜", "夜空") and _contains_any(images, "星光"):
        candidates += ["夜空中有光", "星光未眠"]
    if _contains_any(images, "夜色", "午夜") and _contains_any(images, "梦想"):
        candidates += ["午夜的梦", "夜色里的梦"]
    if _contains_any(images, "城市", "街角", "灯火") and _contains_any(emotions, "孤独", "想念"):
        candidates += ["孤城灯火", "街角想念"]
    if _contains_any(images, "海风", "海浪") and _contains_any(emotions, "自由", "想念"):
        candidates += ["海风里的你", "自由海岸"]
    if _contains_any(images, "晨光", "黎明") and _contains_any(emotions, "希望", "勇敢"):
        candidates += ["奔向晨光", "黎明之前"]
    if _contains_any(emotions, "告别") or _contains_any(actions, "告别", "远行"):
        candidates += ["最后的告别", "告别之前"]
    if _contains_any(emotions, "爱", "心动", "温柔"):
        candidates += ["温柔心事", "把爱唱给你"]
    if _contains_any(emotions, "自由") or _contains_any(actions, "飞翔", "逃离"):
        candidates += ["向风而行", "自由飞翔"]
    if images and emotions:
        candidates.append(f"{images[0]}与{emotions[0]}")
        candidates.append(f"{images[0]}里的{emotions[0]}")
    if images:
        candidates.append(f"{images[0]}未眠")
        candidates.append(f"{images[0]}回响")
    if emotions:
        candidates.append(f"{emotions[0]}回响")
    if "happy" in mood or "bright" in mood or "快乐" in mood:
        candidates.append("晴天心事")
    if "calm" in mood or "peaceful" in mood or "温柔" in mood:
        candidates.append("温柔回响")
    candidates += ["夜空中有光", "时光回响", "音乐心声"]
    return candidates


def _english_title_candidates(signals: dict[str, Any], mood: str) -> list[str]:
    images = signals["images"]
    emotions = signals["emotions"]
    actions = signals["actions"]
    words = signals["fallback_words"]
    candidates: list[str] = []
    if _contains_any(images, "dream") or _contains_any(actions, "chasing") or _contains_any(emotions, "hope"):
        candidates += ["Chasing the Light", "Running Toward Tomorrow", "Dreams in Motion", "Hold On to Hope"]
    if _contains_any(actions, "running") and _contains_any(emotions, "wild", "freedom"):
        candidates.insert(0, "Running Wild")
    if _contains_any(images, "night") and _contains_any(images, "dream"):
        candidates += ["Midnight Dreams", "Dreams After Dark"]
    if _contains_any(images, "night") and _contains_any(images, "star", "light"):
        candidates += ["Starlit Night", "Light in the Dark"]
    if _contains_any(images, "rain") and _contains_any(images, "window"):
        candidates += ["Window in the Rain", "Rain on the Glass"]
    if _contains_any(images, "rain") and _contains_any(images, "memory"):
        candidates += ["Rainy Memories", "After the Rain"]
    if _contains_any(images, "rain") and _contains_any(emotions, "heartbreak", "longing", "sadness"):
        candidates += ["Rain Keeps Falling", "After the Rain"]
    if _contains_any(images, "city", "neon", "street") and _contains_any(emotions, "loneliness"):
        candidates += ["Neon Shadows", "City of Echoes"]
    if _contains_any(images, "ocean") and _contains_any(emotions, "freedom", "longing", "love"):
        candidates += ["Waves of You", "Ocean Echoes"]
    if _contains_any(images, "home") and _contains_any(actions, "leaving", "running", "chasing"):
        candidates += ["Way Back Home", "Long Road Home"]
    if _contains_any(images, "fire") and _contains_any(emotions, "courage", "hope"):
        candidates += ["Fire in the Heart", "Burning Bright"]
    if _contains_any(emotions, "goodbye"):
        candidates += ["Last Goodbye", "Before We Go"]
    if _contains_any(emotions, "love") and _contains_any(emotions, "longing"):
        candidates += ["Still Loving You", "Waves of You"]
    if _contains_any(emotions, "love"):
        candidates += ["Love Still Remains", "Only Love Knows"]
    if _contains_any(emotions, "loneliness"):
        candidates += ["Lonely Echoes", "Alone Tonight"]
    if _contains_any(emotions, "freedom"):
        candidates += ["Running Free", "Wild and Free"]
    if _contains_any(emotions, "joy", "perfect"):
        candidates += ["Perfect Day", "Bright New Morning"]
    if _contains_any(emotions, "heartbreak", "sadness"):
        candidates += ["Broken Melody", "Tears in the Dark"]
    if _contains_any(emotions, "courage"):
        candidates += ["Brave New Morning", "Stand in the Light"]
    if images and emotions:
        emotion_word = {
            "hope": "Hope", "love": "Love", "loneliness": "Lonely", "freedom": "Freedom",
            "joy": "Joy", "sadness": "Sorrow", "heartbreak": "Broken", "courage": "Brave",
            "longing": "Longing", "goodbye": "Goodbye", "wild": "Wild", "perfect": "Perfect",
        }.get(emotions[0], emotions[0].title())
        image_word = {
            "night": "Night", "star": "Stars", "city": "City", "ocean": "Waves", "road": "Road",
            "home": "Home", "fire": "Fire", "sky": "Sky", "sunrise": "Morning", "memory": "Memories",
            "dream": "Dreams", "shadow": "Shadows", "river": "River", "rain": "Rain", "light": "Light",
            "window": "Window", "moon": "Moon", "street": "Street", "neon": "Neon",
        }.get(images[0], images[0].title())
        candidates += [f"{emotion_word} {image_word}", f"{image_word} of {emotion_word}"]
    if images:
        candidates.append(f"{images[0].title()} Echoes")
    if words:
        candidates.append(" ".join(words[:2]).title())
    if "happy" in mood or "bright" in mood or "upbeat" in mood:
        candidates.append("Perfect Day")
    if "calm" in mood or "peaceful" in mood:
        candidates.append("Quiet Echoes")
    if "sad" in mood or "dark" in mood or "melancholic" in mood:
        candidates.append("Midnight Echoes")
    candidates += ["Midnight Dreams", "Sunrise Dreams", "Music Speaks"]
    return candidates


def _choose_generated_title(candidates: list[str], lyrics: str, lang: str) -> str:
    seen: set[str] = set()
    for candidate in candidates:
        formatted = _format_chinese_title(candidate) if lang == "zh" else _format_english_title(candidate)
        key = _normalized_title_compare(formatted)
        if not formatted or key in seen:
            continue
        seen.add(key)
        if not _is_first_line_title(formatted, lyrics):
            return formatted
    return "时光回响" if lang == "zh" else "Midnight Dreams"


def fallback_song_title(job: dict[str, Any], lyrics: str) -> str:
    """Generate title from lyrics content — NOT just the first line."""
    extra = job.get("extra", {}) if isinstance(job.get("extra"), dict) else {}
    mood = str(extra.get("mood", "")).strip().lower()
    prompt = str(job.get("prompt", "")).strip()
    lyrics_idea = str(job.get("lyrics_idea", "")).strip()
    signals = _title_signals(lyrics, prompt, lyrics_idea)
    if signals["lang"] == "zh":
        return _choose_generated_title(_chinese_title_candidates(signals, mood), lyrics, "zh")
    return _choose_generated_title(_english_title_candidates(signals, mood), lyrics, "en")


def generate_lyrics_from_text_model(job: dict[str, Any], timeout: float = 180) -> str:
    prompt = str(job.get("prompt", "")).strip()
    lyrics_idea = str(job.get("lyrics_idea", "")).strip()
    extra = job.get("extra", {}) if isinstance(job.get("extra"), dict) else {}
    voice_id = str(job.get("voice_id", "")).strip()
    lyrics_language_override = str(job.get("lyrics_language", "auto")).strip()
    # Priority: explicit override > voice_id detection > "auto" (English default)
    if lyrics_language_override and lyrics_language_override != "auto":
        voice_lang = lyrics_language_override
    elif voice_id:
        voice_lang = _detect_lang_from_voice_id(voice_id)
    else:
        voice_lang = "English"
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
    if voice_lang == "Cantonese":
        system = (
            "You are a professional Cantonese songwriter. Write COMPLETE, SINGABLE Cantonese song lyrics ONLY. "
            "Output only the lyrics, with no explanation, no markdown fences, and no notes. "
            "Use structure tags such as [Verse], [Pre-Chorus], [Chorus], [Bridge], and [Outro] where natural. "
            "CRITICAL: Write lyrics using TRADITIONAL Chinese characters (not Simplified). "
            "CRITICAL: Use Cantonese/colloquial vocabulary and expressions. "
            "Authentic Cantonese expressions (USE THESE): 你知唔知 / 我唔知 / 佢話 / 今日 / 晏晝 / 收工 / 唔該 / 多謝 / 邊個 / 點解 / 幾時 / 幾多 / 為乜 / 我好中意你 / 你知唔知我鐘意你 / 喺 / 嘅 / 嚟 / 哋 / 睇 / 聽日 / 尋晚 / 食咗未 / 走咗 / 嚟緊 "
            "MANDATORY: Write in Traditional Chinese characters AND Cantonese vocabulary. "
            "MANDATORY: Do NOT write in Simplified Chinese characters. "
            "MANDATORY: Do NOT use Mandarin vocabulary like: 你知道 / 我不知道 / 他说 / 今天 / 下午 / 下班 / 谢谢 / 为什么 / 什么时候 / 多少钱 / 我很喜欢你 / 走 / 吃 / 看 / 明天 / 昨晚 "
            "Generate enough lyrics to support a full 3-4 minute song, even when the brief is short. "
            "Respect the requested story, feelings, fragments, mood, and imagery. Avoid unsafe or explicit content if requested."
        )
    elif voice_lang == "Chinese (Mandarin)":
        system = (
            "You are a professional Mandarin Chinese songwriter. Write complete, singable Mandarin lyrics ONLY. "
            "Output only the lyrics, with no explanation, no markdown fences, and no notes. "
            "Use structure tags such as [Verse], [Pre-Chorus], [Chorus], [Bridge], and [Outro] where natural. "
            "Write in simplified Chinese. Generate enough lyrics to support a full 3-4 minute song, even when the brief is short. "
            "Respect the requested story, feelings, fragments, mood, and imagery. Avoid unsafe or explicit content if requested."
        )
    elif voice_lang in ("Korean", "Japanese", "Spanish", "French", "German", "Portuguese", "Italian", "Russian", "Arabic", "Hindi", "Indonesian", "Vietnamese", "Thai", "Turkish", "Polish", "Dutch", "Swedish", "Norwegian", "Danish", "Finnish", "Czech", "Romanian", "Hungarian", "Ukrainian"):
        lang_map = {
            "Korean": "Korean (한국어)",
            "Japanese": "Japanese (日本語)",
            "Spanish": "Spanish (Español)",
            "French": "French (Français)",
            "German": "German (Deutsch)",
            "Portuguese": "Portuguese (Português)",
            "Italian": "Italian (Italiano)",
            "Russian": "Russian (Русский)",
            "Arabic": "Arabic (العربية)",
            "Hindi": "Hindi (हिन्दी)",
            "Indonesian": "Indonesian (Bahasa Indonesia)",
            "Vietnamese": "Vietnamese (Tiếng Việt)",
            "Thai": "Thai (ไทย)",
            "Turkish": "Turkish (Türkçe)",
            "Polish": "Polish (Polski)",
            "Dutch": "Dutch (Nederlands)",
            "Swedish": "Swedish (Svenska)",
            "Norwegian": "Norwegian (Norsk)",
            "Danish": "Danish (Dansk)",
            "Finnish": "Finnish (Suomi)",
            "Czech": "Czech (Čeština)",
            "Romanian": "Romanian (Română)",
            "Hungarian": "Hungarian (Magyar)",
            "Ukrainian": "Ukrainian (Українська)",
        }
        lang_label = lang_map.get(voice_lang, voice_lang)
        system = (
            f"You are a professional {lang_label} songwriter. Write complete, singable lyrics in {lang_label} ONLY. "
            "Output only the lyrics, with no explanation, no markdown fences, and no notes. "
            "Use structure tags such as [Verse], [Pre-Chorus], [Chorus], [Bridge], and [Outro] where natural. "
            "Generate enough lyrics to support a full 3-4 minute song, even when the brief is short. "
            "Respect the requested story, feelings, fragments, mood, and imagery. Avoid unsafe or explicit content if requested."
        )
    else:
        system = (
            "You are a professional songwriter. Write complete, singable lyrics only. "
            "Output only the lyrics, with no explanation, no markdown fences, and no notes. "
            "Use structure tags such as [Verse], [Pre-Chorus], [Chorus], [Bridge], and [Outro] where natural. "
            "Write in the same language as the lyrics brief unless the user explicitly requests another language. "
            "Generate enough lyrics to support a full 3-4 minute song, even when the brief is short. "
            "Respect the requested story, feelings, fragments, mood, and imagery. Avoid unsafe or explicit content if requested."
        )
    # Build a flat, plain-English message — no JSON nesting, so the model
    # cannot confuse the brief with lyrics to be copied verbatim.
    music_style = prompt.strip()
    lyrics_brief_raw = lyrics_idea.strip()

    # Plain-language creative context (no JSON that might confuse the model)
    context_lines = []
    if music_style:
        context_lines.append(f"MUSIC STYLE: {music_style}")
    if lyrics_brief_raw:
        context_lines.append(f"LYRICS BRIEF (ideas, feelings, imagery, story fragments — NOT lyrics to copy):\n{lyrics_brief_raw}")
    if extra.get("mood"):
        context_lines.append(f"MOOD: {extra['mood']}")
    if extra.get("genre"):
        context_lines.append(f"GENRE: {extra['genre']}")
    if extra.get("vocals"):
        context_lines.append(f"VOCAL STYLE: {extra['vocals']}")
    if extra.get("structure"):
        context_lines.append(f"PREFERRED STRUCTURE: {extra['structure']}")
    if extra.get("avoid"):
        context_lines.append(f"AVOID: {extra['avoid']}")
    context_str = "\n".join(context_lines) if context_lines else "(No specific brief provided — create freely)"

    message = f"""You are a professional songwriter. A user has provided the creative brief below. Your job is to write a COMPLETE, ORIGINAL, SINGABLE song that honors this brief.

--- CREATIVE BRIEF ---
{context_str}
--- END BRIEF ---

SONGWRITING RULES (follow strictly):
1. Do NOT copy the user's words or phrases from the brief. Treat the brief as inspiration only.
2. Every image, emotion, story beat, and moment in the brief must be INTERPRETED and REIMAGINED through your own artistry — never pasted verbatim into a lyric line.
3. The song must have a clear emotional journey: set the scene (Verse 1) → build tension (Pre-Chorus) → emotional peak (Chorus) → new angle (Verse 2) → contrast/turn (Bridge) → resolution (Final Chorus / Outro).
4. Each lyric line must be original. Do not use generic phrases like "music speaks from the heart", "let music be your voice", "express love through music", "find hope in melody" — these are clichés and strictly forbidden.
5. Use section tags: [Intro], [Verse], [Pre-Chorus], [Chorus], [Drop], [Bridge], [Outro].
6. VERSE 1: Introduce characters, scenes, or emotions suggested by the brief. Create fresh imagery — do not copy the brief's own words.
7. PRE-CHORUS: Build toward the chorus. Increase emotional intensity.
8. CHORUS: The most powerful moment. If the user shared a specific hook or phrase, build a complete chorus around its theme — but write all new lines.
9. VERSE 2: Take the story somewhere new. Deepen or complicate the emotion from verse 1.
10. BRIDGE: Offer a shift in perspective, a moment of contrast, or the emotional peak.
11. FINAL CHORUS / OUTRO: Maximum emotional impact. Resolve the journey.
12. Target 400-600 words to support a full 3-4 minute song.
13. Keep under 6,000 characters total.
14. Output ONLY the lyrics — no explanations, no notes, no markdown fences, no quotes around the output."""

    output = run_mmx([
        "text", "chat",
        "--model", "auto",
        "--system", system,
        "--message", message,
        "--max-tokens", "3200",
        "--temperature", "0.8",
        "--non-interactive",
        "--quiet",
        "--output", "text",
    ], timeout=int(max(1, timeout)))
    lyrics = clean_generated_lyrics(output)
    if not lyrics:
        raise RuntimeError("MiniMax lyrics_generation model returned empty lyrics.")
    return lyrics


def fallback_generated_lyrics(prompt: str, lyrics_idea: str, extra: dict[str, Any] | None = None, voice_id: str = "", lyrics_language: str = "auto") -> str:
    """Fast local fallback so the UI remains usable when live lyrics generation is slow."""
    extra = extra or {}
    seed = (lyrics_idea or prompt or "Music speaks").strip()
    seed = re.sub(r"\s+", " ", seed)[:120] or "Music speaks"
    # Resolve effective language: explicit override > voice_id detection > English default
    if lyrics_language and lyrics_language != "auto":
        voice_lang = lyrics_language
    elif voice_id:
        voice_lang = _detect_lang_from_voice_id(voice_id)
    else:
        voice_lang = "English"
    is_chinese = bool(re.search(r"[\u4e00-\u9fff]", seed))
    mood = str(extra.get("mood", "")).strip()
    genre = str(extra.get("genre", "")).strip()
    # Cantonese lyrics - use Cantonese vocabulary
    if voice_lang == "Cantonese":
        theme = seed.rstrip("。！？")
        mood_cn = f"，帶著{mood}" if mood else ""
        style_cn = f"，似{genre}風格" if genre else ""
        sections = [
            "[Verse 1]",
            f"{theme}喺心裏慢慢發光{mood_cn}",
            f"每一步都聽到回響{style_cn}",
            "城市嘅風將沉默吹亮",
            "我把冇話出口嘅願望收藏",
            "沿住夜色找到新嘅方向",
            "讓旋律替我抵達遠方",
            "",
            "[Pre-Chorus]",
            "如果眼淚都有節拍",
            "就讓佢跌成温柔嘅海",
            "如果聽日仲喺等待",
            "我會把勇氣重新唱出嚟",
            "",
            "[Chorus]",
            f"{theme}，請為我歌唱",
            "穿過黑夜，落在晨光",
            "你知唔知我幾中意你",
            "呢份感覺一路陪住我",
            "",
            "[Verse 2]",
            "時光匆匆走過廣場",
            "記憶地圖逐漸發黃",
            "但你把歌放喺我心上",
            "每一個音符閃閃發光",
            "",
            "[Bridge]",
            "如果可以再揀一次",
            "我都會揀你呢個方向",
            "世界太大你太遠",
            "但你把歌聲留低在我耳邊",
            "",
            "[Chorus]",
            f"{theme}，請為我歌唱",
            "穿過黑夜，落在晨光",
            "你知唔知我幾中意你",
            "呢份感覺一路陪住我",
            "",
            "[Outro]",
            "...",
        ]
        return "\n".join(sections)
    elif voice_lang == "Chinese (Mandarin)" or is_chinese:
        theme = seed.rstrip("。！？")
        color = f"，带着{mood}" if mood else ""
        style = f"，像{genre}一样" if genre else ""
        sections = [
            "[Verse 1]",
            f"{theme}在心里慢慢发光{color}",
            f"每一个脚步都听见回响{style}",
            "城市的风把沉默吹亮",
            "我把没说出口的愿望收藏",
            "沿着夜色找到新的方向",
            "让旋律替我抵达远方",
            "",
            "[Pre-Chorus]",
            "如果眼泪也有节拍",
            "就让它落成温柔的海",
            "如果明天还在等待",
            "我会把勇气重新唱出来",
            "",
            "[Chorus]",
            f"{theme}，请为我歌唱",
            "穿过黑夜，落在晨光",
            "当语言找不到方向",
            "让音乐替我把爱释放",
            f"{theme}，请陪我飞翔",
            "越过人海，越过旧伤",
            "把心跳交给这一段声浪",
            "一直唱到天空发亮",
            "",
            "[Verse 2]",
            "我曾在人群里面躲藏",
            "怕自己的声音不够响亮",
            "后来才懂真实的模样",
            "是颤抖着也愿意绽放",
            f"{theme}像一束光",
            "照见我心里柔软的地方",
            "每一次呼吸都在提醒我",
            "还可以爱，还可以盼望",
            "",
            "[Chorus]",
            f"{theme}，请为我歌唱",
            "穿过黑夜，落在晨光",
            "当语言找不到方向",
            "让音乐替我把爱释放",
            f"{theme}，请陪我飞翔",
            "越过人海，越过旧伤",
            "把心跳交给这一段声浪",
            "一直唱到天空发亮",
            "",
            "[Bridge]",
            "如果世界忽然安静",
            "我仍跟着节拍前行",
            "把遗憾写成和声",
            "把孤单唱成星辰",
            "就算风雨还会来临",
            "我也不再低头逃避",
            "",
            "[Final Chorus]",
            f"{theme}，请为我歌唱",
            "用最明亮的声音回望",
            "每段故事都有回响",
            "每颗真心都值得被收藏",
            f"{theme}，请陪我飞翔",
            "越过人海，越过旧伤",
            "把心跳交给这一段声浪",
            "一直唱到天空发亮",
            "",
            "[Outro]",
            "当语言找不到方向",
            "让音乐替我把爱释放",
        ]
        return "\n".join(sections)[:LYRICS_CHAR_LIMIT].strip()
    descriptors = ", ".join(part for part in (mood, genre) if part)
    detail = f" with {descriptors}" if descriptors else ""
    sections = [
        "[Verse 1]",
        f"I carry {seed} through the quiet night{detail}",
        "A spark beneath the static, a signal turning bright",
        "Every word I buried finds a rhythm of its own",
        "Every little heartbeat starts leading me home",
        "I have been waiting in the space between the lines",
        "Holding on to feelings that were never given time",
        "Now the room is opening, the silence starts to move",
        "And I can hear the melody telling me the truth",
        "",
        "[Pre-Chorus]",
        "If I cannot say it, I can let it rise",
        "Put it in the drumbeat, lift it to the sky",
        "If my voice is shaking, let the chorus be my guide",
        "I am still becoming, I am still alive",
        "",
        "[Chorus]",
        f"Let {seed} rise, let it ring",
        "When words fall short, let the music sing",
        "Through the dark into the morning light",
        "Music speaks what I feel inside",
        f"Let {seed} move, let it fly",
        "Over every doubt I used to hide",
        "Turn the heartbeat into something wide",
        "Music speaks what I feel inside",
        "",
        "[Verse 2]",
        "I used to fold my dreams into a quiet paper plane",
        "Send them through the window, watch them disappear in rain",
        "Now I see the weather was a lesson in disguise",
        "Every storm was teaching me to keep my fire alive",
        "There is a road ahead of me I never walked before",
        "There is a younger version of me waiting at the door",
        "I take their hand and tell them we are not too late to try",
        "We can turn the ache into a song that fills the sky",
        "",
        "[Pre-Chorus]",
        "If I cannot say it, I can let it rise",
        "Put it in the drumbeat, lift it to the sky",
        "If my voice is shaking, let the chorus be my guide",
        "I am still becoming, I am still alive",
        "",
        "[Chorus]",
        f"Let {seed} rise, let it ring",
        "When words fall short, let the music sing",
        "Through the dark into the morning light",
        "Music speaks what I feel inside",
        f"Let {seed} move, let it fly",
        "Over every doubt I used to hide",
        "Turn the heartbeat into something wide",
        "Music speaks what I feel inside",
        "",
        "[Bridge]",
        "If the sky breaks open, I will not hide",
        "I will put my truth on the melody line",
        "All the pieces I could never understand",
        "Start to fit together when the rhythm takes my hand",
        "I am more than the fear that tried to keep me small",
        "I am more than the echoes in an empty hall",
        "Here I am, still breathing, still reaching for the sound",
        "Here I am, still rising every time I hit the ground",
        "",
        "[Final Chorus]",
        f"Let {seed} rise, let it ring",
        "When words fall short, let the music sing",
        "Through the dark into the morning light",
        "Music speaks what I feel inside",
        f"Let {seed} move, let it fly",
        "Over every doubt I used to hide",
        "Turn the heartbeat into something wide",
        "Music speaks what I feel inside",
        "Let it rise, let it ring",
        "Let the whole world hear this hidden thing",
        "Through the dark into the morning light",
        "Music speaks what I feel inside",
        "",
        "[Outro]",
        "When words fall short, let the music sing",
        "Music speaks what I feel inside",
    ]
    return "\n".join(sections)[:LYRICS_CHAR_LIMIT].strip()


def generate_title_from_text_model(job: dict[str, Any], lyrics: str, timeout: float = 180) -> str:
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
        "TITLE RULES: English titles must be 2-6 words. Chinese titles must be 4-12 characters. "
        "Read all lyrics first, identify the central image, emotion, scene, or story, then name the song from that theme. "
        "Do NOT copy the first line, chorus line, or any full lyric line as the title. "
        "Use the same language as the lyrics when possible. Make it sound like a real released song title."
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
    ], timeout=int(max(60, timeout)))
    preferred_lang = _title_language(" ".join(_lyrics_content_lines(lyrics)) or lyrics_idea or prompt)
    title = normalize_generated_song_title(output, lyrics, preferred_lang)
    if not title:
        raise RuntimeError("MiniMax text model returned an invalid song title.")
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
        args.extend(build_music_option_args(job.get("extra", {})))
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
                "job_timeout_seconds": JOB_TIMEOUT_SECONDS,
                "job_retention_seconds": JOB_RETENTION_SECONDS,
                "smtp_configured": bool(SMTP_USER and SMTP_PASSWORD),
                "smtp_host": SMTP_HOST,
                "smtp_port": SMTP_PORT,
            })
            return
        if path == "/api/admin/jobs":
            sweep_jobs()
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
            sweep_jobs()
            client_id = normalize_client_id(self.headers.get("X-Client-Id"))
            with JOBS_LOCK:
                jobs = sorted(
                    [public_job(job, include_lyrics=True) for job in JOBS.values() if job.get("owner_id") == client_id],
                    key=lambda item: str(item.get("created_at", "")),
                    reverse=True,
                )
            self.send_json({"jobs": jobs})
            return
        if parsed.path.startswith("/api/jobs/"):
            sweep_jobs()
            job_id = urllib.parse.unquote(parsed.path.removeprefix("/api/jobs/"))
            client_id = normalize_client_id(self.headers.get("X-Client-Id"))
            with JOBS_LOCK:
                job = JOBS.get(job_id)
                if not job or job.get("owner_id") != client_id:
                    self.send_json({"error": "Job not found"}, HTTPStatus.NOT_FOUND)
                    return
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
            if not prompt and not lyrics_idea:
                raise ValueError("Lyrics brief or music style prompt is required.")
            if len(prompt) > 2000:
                raise ValueError("Prompt must be 2000 characters or fewer.")
            if len(lyrics_idea) > 2500:
                raise ValueError("Lyrics brief must be 2500 characters or fewer.")
            extra = {key: str(form.get(key, "")).strip() for key in ("genre", "mood", "instruments", "tempo", "bpm", "key", "vocals", "structure", "references", "avoid", "use_case", "extra")}
            voice_id = str(form.get("voice_id", "")).strip()
            lyrics_language = str(form.get("lyrics_language", "auto")).strip()
            lyrics = generate_lyrics_from_text_model({
                "prompt": prompt,
                "lyrics_idea": lyrics_idea,
                "extra": extra,
                "voice_id": voice_id,
                "lyrics_language": lyrics_language,
            }, timeout=LYRICS_REQUEST_TIMEOUT)
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
            )
            self.send_json({"lyrics": fallback, "fallback": True, "warning": "Live lyrics generation timed out or failed; using local fallback."})
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
        try:
            output = run_mmx(["speech", "voices", "--output", "json", "--non-interactive", "--quiet"], timeout=int(max(1, VOICE_LIST_TIMEOUT)))
            parsed = json.loads(output)
            if isinstance(parsed, dict):
                parsed = parsed.get("voices") or parsed.get("data") or []
            voices = [str(voice).strip() for voice in parsed if str(voice).strip()] if isinstance(parsed, list) else []
        except Exception as exc:
            print(f"[voices] using fallback voice list: {exc}")
            voices = []
        if not voices:
            voices = DEFAULT_SYSTEM_VOICES
        self.send_json({"voices": voices, "count": len(voices), "fallback": voices == DEFAULT_SYSTEM_VOICES})

    def handle_voice_preview(self) -> None:
        """Handle GET /api/voice/preview?voice_id=xxx — synthesize a short speech sample with the given voice_id."""
        parsed = urllib.parse.urlparse(self.path)
        params = dict(urllib.parse.parse_qsl(parsed.query))
        voice_id = str(params.get("voice_id", "")).strip()
        if not voice_id:
            self.send_json({"error": "voice_id is required"}, HTTPStatus.BAD_REQUEST)
            return
        if not _is_safe_voice_id(voice_id):
            self.send_json({"error": "voice_id contains invalid characters"}, HTTPStatus.BAD_REQUEST)
            return
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
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_GATEWAY)
            return

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
            client_id = normalize_client_id(self.headers.get("X-Client-Id"))
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
            if len(lyrics) > LYRICS_CHAR_LIMIT:
                raise ValueError(f"Lyrics must be {LYRICS_CHAR_LIMIT} characters or fewer.")
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
        client_id = normalize_client_id(self.headers.get("X-Client-Id"))
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
