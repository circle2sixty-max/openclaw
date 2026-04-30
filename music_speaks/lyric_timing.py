"""Lyric timing helpers for Music Speaks."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

SECTION_RE = re.compile(r"^\[[^\]]+\]$")
TIMESTAMP_RE = re.compile(r"\[(\d{2}):(\d{2})(?:\.(\d{1,3}))?\]")

FFMPEG_BIN = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
FFPROBE_BIN = shutil.which("ffprobe") or "/opt/homebrew/bin/ffprobe"


def parse_lyric_rows(raw_lyrics: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, raw_line in enumerate(line.strip() for line in str(raw_lyrics or "").splitlines() if line.strip()):
        is_section = bool(SECTION_RE.fullmatch(raw_line))
        text = raw_line if is_section else (re.sub(r"^(?:\[[^\]]+\]\s*)+", "", raw_line).strip() or raw_line)
        rows.append({"index": index, "text": text, "is_section": is_section})
    return rows


def parse_embedded_timestamps(raw_lyrics: str) -> list[dict[str, Any]]:
    rows = parse_lyric_rows(raw_lyrics)
    if not rows:
        return []
    results: list[dict[str, Any]] = []
    row_cursor = 0
    for raw_line in (line.strip() for line in str(raw_lyrics or "").splitlines() if line.strip()):
        row = rows[row_cursor]
        row_cursor += 1
        if row["is_section"]:
            continue
        prefix = re.match(r"^(?:\[(?:\d{2}:\d{2}(?:\.\d{1,3})?)\])+", raw_line)
        if not prefix:
            continue
        for minutes, seconds, fraction in TIMESTAMP_RE.findall(prefix.group(0)):
            millis = 0
            if fraction:
                millis = int(fraction) * (100 if len(fraction) == 1 else 10 if len(fraction) == 2 else 1)
            results.append({
                "time": int(minutes) * 60 + int(seconds) + millis / 1000,
                "text": row["text"],
                "row_index": row["index"],
                "source": "embedded-lrc",
            })
    return sorted(results, key=lambda item: (item["time"], item["row_index"]))


def probe_audio_duration(audio_path: Path) -> float:
    if not audio_path or not audio_path.exists() or not Path(FFPROBE_BIN).exists():
        return 0.0
    try:
        result = subprocess.run(
            [FFPROBE_BIN, "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(audio_path)],
            capture_output=True,
            text=True,
            timeout=20,
            check=True,
        )
        return max(0.0, float((result.stdout or "0").strip() or 0.0))
    except Exception:
        return 0.0


def detect_voiced_segments(audio_path: Path, duration: float) -> list[tuple[float, float]]:
    if duration <= 0 or not audio_path or not audio_path.exists() or not Path(FFMPEG_BIN).exists():
        return []
    try:
        result = subprocess.run(
            [
                FFMPEG_BIN,
                "-hide_banner",
                "-nostats",
                "-i",
                str(audio_path),
                "-af",
                "highpass=f=180,lowpass=f=3200,volume=4,silencedetect=noise=-30dB:d=0.18",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
        )
    except Exception:
        return []

    silence_starts: list[float] = []
    silence_ranges: list[tuple[float, float]] = []
    for line in (result.stderr or "").splitlines():
        match_start = re.search(r"silence_start:\s*([0-9.]+)", line)
        if match_start:
            silence_starts.append(float(match_start.group(1)))
            continue
        match_end = re.search(r"silence_end:\s*([0-9.]+)\s*\|\s*silence_duration:\s*([0-9.]+)", line)
        if match_end and silence_starts:
            start = silence_starts.pop(0)
            end = float(match_end.group(1))
            if end > start:
                silence_ranges.append((max(0.0, start), min(duration, end)))

    if not silence_ranges:
        return []

    segments: list[tuple[float, float]] = []
    cursor = 0.0
    for start, end in sorted(silence_ranges):
        if start - cursor >= 0.35:
            segments.append((cursor, start))
        cursor = max(cursor, end)
    if duration - cursor >= 0.35:
        segments.append((cursor, duration))
    return [(max(0.0, start), min(duration, end)) for start, end in segments if end - start >= 0.35]


def _weighted_line_starts(playable_rows: list[dict[str, Any]], start_time: float, end_time: float) -> list[float]:
    if not playable_rows:
        return []
    span = max(0.1, end_time - start_time)
    weights = [max(1.0, float(len(str(row.get("text", "")).strip()))) for row in playable_rows]
    total_weight = sum(weights) or 1.0
    starts: list[float] = []
    cursor = start_time
    for weight in weights:
        starts.append(cursor)
        cursor += span * (weight / total_weight)
    return starts


def _snap_boundary_times(expected_times: list[float], candidate_times: list[float], active_start: float, active_end: float, line_count: int) -> list[float]:
    if not expected_times:
        return []
    if not candidate_times:
        return _enforce_monotonic_boundaries(expected_times, active_start, active_end, line_count)

    vocal_span = max(1.0, active_end - active_start)
    max_window = max(0.7, min(3.5, vocal_span / max(2, line_count)))
    chosen: list[float] = []
    used: set[int] = set()
    for expected in expected_times:
        best_idx = -1
        best_score = float("inf")
        for idx, candidate in enumerate(candidate_times):
            if idx in used:
                continue
            distance = abs(candidate - expected)
            if distance <= max_window and distance < best_score:
                best_idx = idx
                best_score = distance
        if best_idx >= 0:
            used.add(best_idx)
            chosen.append(candidate_times[best_idx])
        else:
            chosen.append(expected)
    return _enforce_monotonic_boundaries(chosen, active_start, active_end, line_count)


def _enforce_monotonic_boundaries(boundary_times: list[float], active_start: float, active_end: float, line_count: int) -> list[float]:
    if not boundary_times:
        return []
    min_gap = max(0.28, min(1.2, (active_end - active_start) / max(4, line_count * 2)))
    times = list(boundary_times)
    previous = active_start
    for idx, current in enumerate(times):
        lower_bound = previous + min_gap
        upper_bound = active_end - min_gap * (len(times) - idx)
        times[idx] = max(lower_bound, min(current, upper_bound))
        previous = times[idx]
    return times


def build_duration_weighted_timestamps(raw_lyrics: str, duration: float, start_time: float = 0.0, end_time: float | None = None) -> list[dict[str, Any]]:
    rows = parse_lyric_rows(raw_lyrics)
    playable_rows = [row for row in rows if not row["is_section"] and row["text"]]
    if not playable_rows or duration <= 0:
        return []
    effective_end = min(duration, end_time if end_time is not None else duration)
    starts = _weighted_line_starts(playable_rows, max(0.0, start_time), max(start_time + 0.1, effective_end))
    timestamps: list[dict[str, Any]] = []
    for idx, row in enumerate(playable_rows):
        line_end = starts[idx + 1] if idx + 1 < len(starts) else effective_end
        timestamps.append({
            "time": round(starts[idx], 3),
            "end": round(max(starts[idx], line_end), 3),
            "text": row["text"],
            "row_index": row["index"],
            "source": "duration-weighted",
        })
    return timestamps


def build_segment_aligned_timestamps(raw_lyrics: str, duration: float, segments: list[tuple[float, float]]) -> list[dict[str, Any]]:
    rows = parse_lyric_rows(raw_lyrics)
    playable_rows = [row for row in rows if not row["is_section"] and row["text"]]
    if not playable_rows or duration <= 0:
        return []
    if not segments:
        return build_duration_weighted_timestamps(raw_lyrics, duration)

    active_start = max(0.0, segments[0][0])
    active_end = min(duration, segments[-1][1])
    if active_end - active_start < 1.0:
        return build_duration_weighted_timestamps(raw_lyrics, duration)

    expected_starts = _weighted_line_starts(playable_rows, active_start, active_end)
    candidate_starts = [segment[0] for segment in segments[1:] if active_start < segment[0] < active_end]
    snapped_boundaries = _snap_boundary_times(expected_starts[1:], candidate_starts, active_start, active_end, len(playable_rows))
    starts = [expected_starts[0]] + snapped_boundaries

    timestamps: list[dict[str, Any]] = []
    for idx, row in enumerate(playable_rows):
        line_start = starts[idx]
        line_end = starts[idx + 1] if idx + 1 < len(starts) else active_end
        timestamps.append({
            "time": round(line_start, 3),
            "end": round(max(line_start, line_end), 3),
            "text": row["text"],
            "row_index": row["index"],
            "source": "audio-segment",
        })
    return timestamps


def finalize_timestamp_ends(timestamps: list[dict[str, Any]], duration: float) -> list[dict[str, Any]]:
    if not timestamps:
        return []
    final_duration = max(duration, timestamps[-1].get("time", 0.0))
    finalized: list[dict[str, Any]] = []
    for idx, item in enumerate(timestamps):
        next_time = timestamps[idx + 1]["time"] if idx + 1 < len(timestamps) else final_duration
        finalized.append({
            **item,
            "time": round(float(item.get("time", 0.0)), 3),
            "end": round(max(float(item.get("time", 0.0)), float(item.get("end", next_time)), next_time), 3),
        })
    return finalized


def build_lyric_timestamps(raw_lyrics: str, audio_path: Path | str | None = None) -> tuple[list[dict[str, Any]], str]:
    raw_lyrics = str(raw_lyrics or "")
    embedded = parse_embedded_timestamps(raw_lyrics)
    audio_file = Path(audio_path) if audio_path else None
    duration = probe_audio_duration(audio_file) if audio_file else 0.0
    if len(embedded) >= 2:
        return finalize_timestamp_ends(embedded, duration), "embedded-lrc"
    if duration <= 0:
        return [], "unavailable"
    segments = detect_voiced_segments(audio_file, duration) if audio_file else []
    if segments:
        aligned = build_segment_aligned_timestamps(raw_lyrics, duration, segments)
        if aligned:
            return finalize_timestamp_ends(aligned, duration), "audio-segment"
    weighted = build_duration_weighted_timestamps(raw_lyrics, duration)
    return finalize_timestamp_ends(weighted, duration), "duration-weighted"
