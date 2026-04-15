#!/usr/bin/env python3
"""Terry Music web app for Render."""

from __future__ import annotations

import datetime as dt
import email.encoders
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


MINIMAX_API_TOKEN = os.getenv("MINIMAX_API_TOKEN") or legacy_local_config("MINIMAX_API_TOKEN")
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "465"))
SMTP_USER = os.getenv("SMTP_USER") or legacy_local_config("SMTP_USER")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD") or legacy_local_config("SMTP_PASSWORD")

JOBS: dict[str, dict[str, Any]] = {}
JOBS_LOCK = threading.RLock()

INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Terry Music</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #0b0d0c;
      --panel: #141716;
      --soft: #1b201d;
      --input: #101312;
      --line: #2d3430;
      --text: #f4f7f1;
      --muted: #a7b0aa;
      --accent: #50d890;
      --accent-strong: #2fbd76;
      --warn: #efc86a;
      --danger: #ff756d;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      background: linear-gradient(180deg, rgba(80,216,144,.08), transparent 320px), var(--bg);
      color: var(--text);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, sans-serif;
      font-size: 16px;
    }
    .page { width: min(1120px, calc(100% - 28px)); margin: 0 auto; padding: 24px 0 56px; }
    .top { display: flex; justify-content: space-between; gap: 16px; align-items: flex-start; margin-bottom: 24px; }
    h1 { margin: 0 0 8px; font-size: 34px; line-height: 1.08; }
    .subtitle { margin: 0; color: var(--muted); line-height: 1.55; }
    .top-actions { display: flex; gap: 10px; align-items: center; }
    .pill, .ghost {
      border: 1px solid rgba(80,216,144,.32);
      border-radius: 8px;
      padding: 7px 11px;
      color: var(--accent);
      background: rgba(80,216,144,.12);
      font-size: 13px;
      font-weight: 700;
      white-space: nowrap;
    }
    .ghost { cursor: pointer; color: var(--muted); background: rgba(255,255,255,.05); border-color: var(--line); }
    .layout { display: grid; grid-template-columns: 1fr 360px; gap: 18px; align-items: start; }
    .panel { border: 1px solid var(--line); border-radius: 10px; background: rgba(20,23,22,.96); overflow: hidden; }
    .panel-head { padding: 18px 20px 14px; border-bottom: 1px solid var(--line); }
    .panel-head h2 { margin: 0; font-size: 18px; }
    .panel-head p { margin: 6px 0 0; color: var(--muted); font-size: 13px; line-height: 1.45; }
    .panel-body { padding: 18px 20px 20px; }
    .field { margin-bottom: 16px; }
    label { display: block; margin-bottom: 7px; font-size: 13px; font-weight: 700; }
    input, textarea {
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--input);
      color: var(--text);
      font: inherit;
      padding: 11px 12px;
      outline: none;
    }
    input:focus, textarea:focus { border-color: var(--accent); box-shadow: 0 0 0 3px rgba(80,216,144,.12); }
    textarea { min-height: 150px; resize: vertical; line-height: 1.5; }
    .hint { margin-top: 6px; color: var(--muted); font-size: 12px; line-height: 1.4; }
    .checks { display: grid; gap: 8px; margin: 12px 0 16px; }
    .check { display: flex; gap: 10px; align-items: flex-start; padding: 11px 12px; border: 1px solid var(--line); border-radius: 8px; background: var(--soft); cursor: pointer; }
    .check input { width: 16px; margin-top: 3px; accent-color: var(--accent); }
    .check small { display: block; color: var(--muted); margin-top: 3px; }
    details { border-top: 1px solid var(--line); padding-top: 12px; margin-top: 4px; }
    summary { cursor: pointer; color: var(--muted); font-size: 13px; font-weight: 700; }
    .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-top: 14px; }
    .wide { grid-column: 1 / -1; }
    .actions { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-top: 18px; }
    button, .download-link {
      border: 0;
      border-radius: 8px;
      background: var(--accent);
      color: #06100b;
      cursor: pointer;
      font-weight: 800;
      min-height: 44px;
      padding: 11px 18px;
      text-decoration: none;
      display: inline-flex;
      align-items: center;
    }
    button:hover, .download-link:hover { background: var(--accent-strong); }
    button:disabled { opacity: .65; cursor: wait; }
    .error-text { color: var(--danger); min-height: 20px; font-size: 14px; }
    .jobs { display: grid; gap: 10px; padding: 14px 16px 16px; }
    .empty { border: 1px dashed var(--line); border-radius: 8px; padding: 18px; color: var(--muted); text-align: center; }
    .job { border: 1px solid var(--line); border-radius: 8px; background: var(--soft); padding: 13px; display: grid; gap: 9px; }
    .job-top { display: flex; justify-content: space-between; gap: 10px; align-items: flex-start; }
    .job-title { margin: 0; font-size: 14px; line-height: 1.4; overflow-wrap: anywhere; }
    .badge { border: 1px solid var(--line); border-radius: 999px; padding: 4px 9px; color: var(--muted); background: var(--input); font-size: 11px; font-weight: 800; text-transform: uppercase; white-space: nowrap; }
    .badge.completed { color: var(--accent); border-color: rgba(80,216,144,.45); }
    .badge.running, .badge.queued { color: var(--warn); border-color: rgba(239,200,106,.45); }
    .badge.error { color: var(--danger); border-color: rgba(255,117,109,.45); }
    .meta { display: flex; flex-wrap: wrap; gap: 6px; color: var(--muted); font-size: 12px; }
    .meta span { border: 1px solid var(--line); border-radius: 999px; padding: 3px 8px; background: var(--input); }
    .job-file { display: flex; gap: 10px; align-items: center; justify-content: space-between; flex-wrap: wrap; color: var(--muted); font-size: 13px; }
    .job-error { color: var(--danger); font-size: 13px; line-height: 1.4; overflow-wrap: anywhere; }
    .delete-btn { min-height: 32px; padding: 6px 10px; background: rgba(255,117,109,.16); color: var(--danger); border: 1px solid rgba(255,117,109,.32); font-size: 12px; }
    .download-link { min-height: 36px; padding: 8px 12px; font-size: 13px; }
    @media (max-width: 860px) {
      .layout { grid-template-columns: 1fr; }
      .top { flex-direction: column; }
      h1 { font-size: 28px; }
    }
    @media (max-width: 560px) {
      .grid { grid-template-columns: 1fr; }
      .page { width: min(100% - 20px, 1120px); padding-top: 16px; }
      .top-actions { width: 100%; justify-content: space-between; }
    }
  </style>
</head>
<body>
  <main class="page">
    <div class="top">
      <div>
        <h1>Terry Music</h1>
        <p id="subtitle" class="subtitle">Describe a music style, generate an MP3, and send it to an email address.</p>
      </div>
      <div class="top-actions">
        <div class="pill">music-2.6</div>
        <button id="langBtn" class="ghost" type="button">中文</button>
      </div>
    </div>
    <div class="layout">
      <section class="panel">
        <div class="panel-head">
          <h2 data-i18n="createTitle">Create Music</h2>
          <p data-i18n="createDesc">Fill in the form. Terry Music will email the track and keep it available for download.</p>
        </div>
        <div class="panel-body">
          <form id="jobForm">
            <div class="field">
              <label for="email" data-i18n="emailLabel">Email Address</label>
              <input id="email" type="email" placeholder="your@email.com" required>
              <div class="hint" data-i18n="emailHint">The MP3 will be sent here when ready.</div>
            </div>
            <div class="field">
              <label for="prompt" data-i18n="promptLabel">Music Style Prompt</label>
              <input id="prompt" type="text" maxlength="2000" required value="Cinematic electronic pop, confident and bright, polished production, strong hook">
              <div class="hint" data-i18n="promptHint">Include style, mood, instruments, tempo, and any references.</div>
            </div>
            <div class="field">
              <label for="lyrics" data-i18n="lyricsLabel">Lyrics (optional)</label>
              <textarea id="lyrics" maxlength="3500" placeholder="[Verse]\nYour lyrics here...\n[Hook]\nYour chorus..."></textarea>
              <div class="hint" data-i18n="lyricsHint">Use tags like [Verse], [Hook], [Chorus]. Leave empty for instrumental or auto lyrics.</div>
            </div>
            <div class="checks">
              <label class="check">
                <input id="instrumental" type="checkbox">
                <span><span data-i18n="instrumental">Instrumental</span><small data-i18n="instrumentalHint">No vocals. Lyrics will be ignored.</small></span>
              </label>
              <label class="check">
                <input id="lyricsOptimizer" type="checkbox">
                <span><span data-i18n="autoLyrics">Auto-generate Lyrics</span><small data-i18n="autoLyricsHint">AI writes lyrics from your prompt.</small></span>
              </label>
            </div>
            <details>
              <summary data-i18n="advanced">More Parameters</summary>
              <div class="grid">
                <div class="field"><label for="genre" data-i18n="genre">Genre</label><input id="genre" placeholder="pop, reggae, jazz"></div>
                <div class="field"><label for="mood" data-i18n="mood">Mood</label><input id="mood" placeholder="warm, bright, intense"></div>
                <div class="field"><label for="instruments" data-i18n="instruments">Instruments</label><input id="instruments" placeholder="piano, guitar, drums"></div>
                <div class="field"><label for="tempo" data-i18n="tempo">Tempo Feel</label><input id="tempo" placeholder="fast, slow, moderate"></div>
                <div class="field"><label for="bpm" data-i18n="bpm">BPM</label><input id="bpm" type="number" min="40" max="240" placeholder="85"></div>
                <div class="field"><label for="key" data-i18n="key">Musical Key</label><input id="key" placeholder="C major, A minor"></div>
                <div class="field wide"><label for="vocals" data-i18n="vocals">Vocal Style</label><input id="vocals" placeholder="warm male vocal, bright female vocal, duet"></div>
                <div class="field wide"><label for="structure" data-i18n="structure">Song Structure</label><input id="structure" placeholder="verse-chorus-verse-bridge-chorus"></div>
                <div class="field wide"><label for="references" data-i18n="references">References</label><input id="references" placeholder="similar to..."></div>
                <div class="field wide"><label for="avoid" data-i18n="avoid">Avoid</label><input id="avoid" placeholder="explicit content, auto-tune"></div>
                <div class="field wide"><label for="useCase" data-i18n="useCase">Use Case</label><input id="useCase" placeholder="video background, theme song"></div>
                <div class="field wide"><label for="extra" data-i18n="extra">Extra Details</label><input id="extra" placeholder="Any additional notes"></div>
              </div>
            </details>
            <div class="actions">
              <button id="submitBtn" type="submit" data-i18n="submit">Generate Music</button>
              <div id="formError" class="error-text"></div>
            </div>
          </form>
        </div>
      </section>
      <section class="panel">
        <div class="panel-head">
          <h2 data-i18n="jobsTitle">Jobs</h2>
          <p data-i18n="jobsDesc">Real-time status. Download appears when the MP3 is ready.</p>
        </div>
        <div id="jobs" class="jobs"></div>
      </section>
    </div>
  </main>
  <script>
    const I18N = {
      en: {
        subtitle: "Describe a music style, generate an MP3, and send it to an email address.",
        createTitle: "Create Music", createDesc: "Fill in the form. Terry Music will email the track and keep it available for download.",
        emailLabel: "Email Address", emailHint: "The MP3 will be sent here when ready.",
        promptLabel: "Music Style Prompt", promptHint: "Include style, mood, instruments, tempo, and any references.",
        lyricsLabel: "Lyrics (optional)", lyricsHint: "Use tags like [Verse], [Hook], [Chorus]. Leave empty for instrumental or auto lyrics.",
        instrumental: "Instrumental", instrumentalHint: "No vocals. Lyrics will be ignored.",
        autoLyrics: "Auto-generate Lyrics", autoLyricsHint: "AI writes lyrics from your prompt.",
        advanced: "More Parameters", genre: "Genre", mood: "Mood", instruments: "Instruments", tempo: "Tempo Feel", bpm: "BPM", key: "Musical Key",
        vocals: "Vocal Style", structure: "Song Structure", references: "References", avoid: "Avoid", useCase: "Use Case", extra: "Extra Details",
        submit: "Generate Music", jobsTitle: "Jobs", jobsDesc: "Real-time status. Download appears when the MP3 is ready.",
        empty: "No jobs yet. Fill in the form to start creating.", queued: "Queued", running: "Generating", completed: "Done", error: "Error", unknown: "Unknown",
        download: "Download MP3", delete: "Delete", sent: "Sent to", instrumentalMode: "Instrumental", vocalMode: "Vocal", deleteConfirm: "Delete this job?", deleteFailed: "Delete failed"
      },
      zh: {
        subtitle: "描述音乐风格，生成 MP3，并发送到指定邮箱。",
        createTitle: "创建音乐", createDesc: "填写表单，Terry Music 会发送邮件，并保留下载按钮。",
        emailLabel: "邮箱地址", emailHint: "MP3 生成完成后会发送到这里。",
        promptLabel: "音乐风格描述", promptHint: "写清风格、情绪、乐器、速度和参考对象。",
        lyricsLabel: "歌词（可选）", lyricsHint: "可用 [Verse]、[Hook]、[Chorus]。纯音乐或自动歌词可留空。",
        instrumental: "纯音乐", instrumentalHint: "无人声，歌词会被忽略。",
        autoLyrics: "自动生成歌词", autoLyricsHint: "AI 根据描述写歌词。",
        advanced: "更多参数", genre: "流派", mood: "情绪", instruments: "乐器", tempo: "节奏感", bpm: "BPM", key: "调性",
        vocals: "人声风格", structure: "歌曲结构", references: "参考对象", avoid: "避免元素", useCase: "使用场景", extra: "其他细节",
        submit: "生成音乐", jobsTitle: "生成任务", jobsDesc: "实时状态。MP3 准备好后会出现下载按钮。",
        empty: "暂无任务，填写表单开始创作。", queued: "排队中", running: "生成中", completed: "完成", error: "错误", unknown: "未知",
        download: "下载 MP3", delete: "删除", sent: "已发送到", instrumentalMode: "纯音乐", vocalMode: "有人声", deleteConfirm: "删除此任务？", deleteFailed: "删除失败"
      }
    };

    let lang = "en";
    let lastJobs = [];
    const jobsBox = document.getElementById("jobs");
    const form = document.getElementById("jobForm");
    const submitBtn = document.getElementById("submitBtn");
    const formError = document.getElementById("formError");
    const instrumental = document.getElementById("instrumental");
    const lyricsOptimizer = document.getElementById("lyricsOptimizer");
    const lyrics = document.getElementById("lyrics");
    const clientId = (() => {
      const key = "terry_music_client_id";
      let id = localStorage.getItem(key);
      if (!id) {
        id = crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
        localStorage.setItem(key, id);
      }
      return id;
    })();

    function t(key) { return I18N[lang][key] || key; }
    function headers(extra = {}) { return {"X-Client-Id": clientId, ...extra}; }
    function escapeHtml(value) {
      return String(value ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#039;");
    }
    function applyLang() {
      document.documentElement.lang = lang;
      document.getElementById("subtitle").textContent = t("subtitle");
      document.getElementById("langBtn").textContent = lang === "en" ? "中文" : "EN";
      document.querySelectorAll("[data-i18n]").forEach(el => { el.textContent = t(el.dataset.i18n); });
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
        jobsBox.innerHTML = `<div class="empty">${t("empty")}</div>`;
        return;
      }
      jobsBox.innerHTML = lastJobs.map(job => {
        const status = escapeHtml(job.status || "unknown");
        const fileName = escapeHtml(job.file_name || "terry-music.mp3");
        const mode = job.is_instrumental ? t("instrumentalMode") : t("vocalMode");
        const downloadUrl = job.download_url ? `${escapeHtml(job.download_url)}?client_id=${encodeURIComponent(clientId)}` : "";
        const download = job.status === "completed" && job.download_url
          ? `<div class="job-file"><span>${fileName}</span><a class="download-link" href="${downloadUrl}" download="${fileName}">${t("download")}</a></div>`
          : "";
        const sent = job.email_sent && job.email ? `<span>${t("sent")} ${escapeHtml(job.email)}</span>` : "";
        const err = job.error ? `<div class="job-error">${escapeHtml(job.error)}</div>` : "";
        const canDelete = job.status !== "running" && job.status !== "queued";
        return `<div class="job">
          <div class="job-top"><p class="job-title">${escapeHtml(job.prompt || "Untitled")}</p><span class="badge ${status}">${statusLabel(status)}</span></div>
          <div class="meta"><span>${mode}</span><span>${formatDate(job.created_at)}</span>${sent}</div>
          ${err}${download}
          ${canDelete ? `<button class="delete-btn" type="button" onclick="deleteJob('${escapeHtml(job.id)}')">${t("delete")}</button>` : ""}
        </div>`;
      }).join("");
    }
    async function loadJobs() {
      try {
        const res = await fetch("/api/jobs", {headers: headers(), cache: "no-store"});
        const data = await res.json();
        renderJobs(data.jobs || []);
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
    instrumental.addEventListener("change", () => {
      const off = instrumental.checked;
      lyrics.disabled = off;
      lyricsOptimizer.disabled = off;
      if (off) lyricsOptimizer.checked = false;
    });
    document.getElementById("langBtn").addEventListener("click", () => {
      lang = lang === "en" ? "zh" : "en";
      applyLang();
    });
    form.addEventListener("submit", async event => {
      event.preventDefault();
      formError.textContent = "";
      submitBtn.disabled = true;
      const get = id => document.getElementById(id).value.trim();
      const payload = {
        email: get("email"), prompt: get("prompt"), lyrics: get("lyrics"),
        is_instrumental: instrumental.checked, lyrics_optimizer: lyricsOptimizer.checked,
        genre: get("genre"), mood: get("mood"), instruments: get("instruments"), tempo: get("tempo"), bpm: get("bpm"), key: get("key"),
        vocals: get("vocals"), structure: get("structure"), references: get("references"), avoid: get("avoid"), use_case: get("useCase"), extra: get("extra")
      };
      try {
        const res = await fetch("/api/jobs", {method: "POST", headers: headers({"Content-Type": "application/json"}), body: JSON.stringify(payload)});
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
        form.reset();
        document.getElementById("prompt").value = "Cinematic electronic pop, confident and bright, polished production, strong hook";
        await loadJobs();
      } catch (error) {
        formError.textContent = error.message;
      } finally {
        submitBtn.disabled = false;
      }
    });
    applyLang();
    loadJobs();
    setInterval(loadJobs, 3000);
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


def safe_name(value: str, fallback: str = "terry-music") -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip().lower()).strip("-._")
    return (text or fallback)[:80]


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


def public_job(job: dict[str, Any]) -> dict[str, Any]:
    result = {key: job.get(key) for key in ("id", "status", "created_at", "updated_at", "prompt", "email", "is_instrumental", "lyrics_optimizer", "file_name", "error", "email_sent")}
    if job.get("status") == "completed" and job.get("file_path"):
        result["download_url"] = f"/download/{urllib.parse.quote(str(job['id']))}"
    return result


def mark_job(job_id: str, **updates: Any) -> None:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return
        job.update(updates)
        job["updated_at"] = now_iso()
        save_jobs_locked()


def run_mmx(args: list[str]) -> None:
    if not MINIMAX_API_TOKEN:
        raise RuntimeError("MINIMAX_API_TOKEN is not configured.")
    env = os.environ.copy()
    path_parts = [part for part in env.get("PATH", "").split(os.pathsep) if part]
    for path_hint in reversed(MMX_PATH_HINTS):
        if path_hint not in path_parts:
            path_parts.insert(0, path_hint)
    env["PATH"] = os.pathsep.join(path_parts)
    env["MINIMAX_API_TOKEN"] = MINIMAX_API_TOKEN
    result = subprocess.run([MMX_BIN] + args, capture_output=True, text=True, env=env, timeout=900)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "Unknown mmx error").strip()
        raise RuntimeError(detail)


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
        msg["Subject"] = "Terry Music - Your Generated Track"
        body = f"Hi! Your Terry Music track is ready.\n\nPrompt: {prompt}\nFile: {file_path.name}\n\nEnjoy!\n"
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
        stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = OUTPUT_DIR / f"terry_music_{stamp}_{safe_name(prompt)}_{job_id[:8]}.mp3"
        args = ["music", "generate", "--prompt", prompt, "--out", str(out_path), "--non-interactive"]
        if job.get("is_instrumental"):
            args.append("--instrumental")
        elif job.get("lyrics_optimizer"):
            args.append("--lyrics-optimizer")
        elif job.get("lyrics"):
            args.extend(["--lyrics", str(job["lyrics"])])
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
        mark_job(job_id, status="completed", file_name=out_path.name, file_path=str(out_path))
        if job.get("email") and out_path.exists():
            ok = send_email(str(job["email"]), out_path, prompt)
            mark_job(job_id, email_sent=ok)
    except Exception as exc:
        mark_job(job_id, status="error", error=str(exc))


class MusicHandler(BaseHTTPRequestHandler):
    server_version = "TerryMusic/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"{self.log_date_time_string()} {self.address_string()} {fmt % args}")

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
        if path == "/api/health":
            self.send_json({
                "ok": True,
                "minimax_configured": bool(MINIMAX_API_TOKEN),
                "smtp_configured": bool(SMTP_USER and SMTP_PASSWORD),
                "smtp_host": SMTP_HOST,
                "smtp_port": SMTP_PORT,
            })
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
        if path.startswith("/download/"):
            self.handle_download(path.removeprefix("/download/"), parsed.query)
            return
        if path == "/favicon.ico":
            self.send_response(HTTPStatus.NO_CONTENT)
            self.end_headers()
            return
        self.send_text("Not found", HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/api/jobs":
            self.send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
            return
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            self.send_json({"error": "Invalid request length."}, HTTPStatus.BAD_REQUEST)
            return
        if length <= 0 or length > MAX_BODY_BYTES:
            self.send_json({"error": "Request body is empty or too large."}, HTTPStatus.BAD_REQUEST)
            return
        try:
            form = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(form, dict):
                raise ValueError("Expected a JSON object.")
            prompt = str(form.get("prompt", "")).strip()
            email_addr = str(form.get("email", "")).strip()
            lyrics = str(form.get("lyrics", "")).strip()
            is_instrumental = bool(form.get("is_instrumental"))
            lyrics_optimizer = bool(form.get("lyrics_optimizer")) and not is_instrumental
            if not email_addr:
                raise ValueError("Email is required.")
            if not prompt:
                raise ValueError("Prompt is required.")
            if len(prompt) > 2000:
                raise ValueError("Prompt must be 2000 characters or fewer.")
            if len(lyrics) > 3500:
                raise ValueError("Lyrics must be 3500 characters or fewer.")
            if not is_instrumental and not lyrics and not lyrics_optimizer:
                raise ValueError("Lyrics are required for vocal tracks unless auto lyrics is enabled.")
            extra = {key: str(form.get(key, "")).strip() for key in ("genre", "mood", "instruments", "tempo", "bpm", "key", "vocals", "structure", "references", "avoid", "use_case", "extra")}
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
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
            "email": email_addr,
            "lyrics": lyrics,
            "is_instrumental": is_instrumental,
            "lyrics_optimizer": lyrics_optimizer,
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
        client_id = normalize_client_id(self.headers.get("X-Client-Id") or (query.get("client_id") or [""])[0])
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
        if output_root not in file_path.parents and file_path != output_root:
            self.send_text("Invalid file path", HTTPStatus.BAD_REQUEST)
            return
        file_name = file_path.name
        content_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
        quoted = urllib.parse.quote(file_name)
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(file_path.stat().st_size))
        self.send_header("Content-Disposition", f"attachment; filename=\"{file_name}\"; filename*=UTF-8''{quoted}")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        with file_path.open("rb") as file_obj:
            while chunk := file_obj.read(1024 * 256):
                self.wfile.write(chunk)


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    load_jobs()
    server = ThreadingHTTPServer((HOST, PORT), MusicHandler)
    print(f"Terry Music running at http://{HOST}:{PORT}")
    print(f"Output directory: {OUTPUT_DIR}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Terry Music.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
