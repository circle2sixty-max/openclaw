"""Lyrics generation, cleaning, validation helpers."""

from __future__ import annotations

import re
from typing import Any

LYRICS_CHAR_LIMIT = 6000

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


def _normalize_copy_check_text(text: str) -> str:
    text = str(text or "").lower()
    return re.sub(r"[^0-9a-z_\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]+", "", text)


def _source_sentence_candidates(text: str) -> list[str]:
    text = str(text or "").strip()
    if not text:
        return []
    candidates: list[str] = []
    for part in re.split(r"[\r\n。！？!?；;]+", text):
        part = re.sub(r"^\s*[-*#>\d.)、]+", "", part).strip(" \t\"'“”‘’`")
        if not part:
            continue
        words = re.findall(r"[A-Za-z0-9_']+", part)
        cjk_chars = re.findall(r"[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]", part)
        normalized = _normalize_copy_check_text(part)
        if len(normalized) >= 24 or len(words) >= 4 or len(cjk_chars) >= 8:
            candidates.append(part)
    return candidates


def _find_copied_source_fragment(lyrics: str, source_texts: list[str]) -> str:
    normalized_lyrics = _normalize_copy_check_text(lyrics)
    lyric_words = " " + " ".join(re.findall(r"[A-Za-z0-9_']+", lyrics.lower())) + " "
    lyric_cjk = "".join(re.findall(r"[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]", lyrics))
    for source in source_texts:
        for sentence in _source_sentence_candidates(source):
            normalized_sentence = _normalize_copy_check_text(sentence)
            if normalized_sentence and normalized_sentence in normalized_lyrics:
                return sentence[:120]

        source_words = re.findall(r"[A-Za-z0-9_']+", str(source or "").lower())
        for size in range(min(10, len(source_words)), 4, -1):
            for index in range(0, len(source_words) - size + 1):
                phrase = " ".join(source_words[index:index + size])
                if len(phrase) >= 25 and f" {phrase} " in lyric_words:
                    return phrase[:120]

        source_cjk = "".join(re.findall(r"[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]", str(source or "")))
        if len(source_cjk) >= 6 and lyric_cjk:
            for index in range(0, len(source_cjk) - 5):
                phrase = source_cjk[index:index + 6]
                if phrase in lyric_cjk:
                    return phrase
    return ""


def _lyrics_body_text(lyrics: str) -> str:
    lines = []
    for line in str(lyrics or "").splitlines():
        stripped = line.strip()
        if not stripped or re.fullmatch(r"\[[^\]]+\]", stripped):
            continue
        lines.append(stripped)
    return "\n".join(lines)


def _validate_lyrics_language_weight(lyrics: str, voice_lang: str) -> None:
    body = _lyrics_body_text(lyrics)
    if not body:
        raise ValueError("Generated lyrics were empty after cleaning.")
    cjk_count = len(re.findall(r"[\u3400-\u9fff]", body))
    kana_count = len(re.findall(r"[\u3040-\u30ff]", body))
    hangul_count = len(re.findall(r"[\uac00-\ud7af]", body))
    letters_count = len(re.findall(r"[A-Za-z]", body))
    meaningful_count = cjk_count + kana_count + hangul_count + letters_count
    if meaningful_count < 80:
        return
    if voice_lang in {"Chinese (Mandarin)", "Cantonese"} and cjk_count / meaningful_count < 0.65:
        raise ValueError("Generated lyrics did not primarily use the selected Chinese voice language.")
    if voice_lang == "Japanese" and (kana_count + cjk_count) / meaningful_count < 0.65:
        raise ValueError("Generated lyrics did not primarily use Japanese.")
    if voice_lang == "Korean" and hangul_count / meaningful_count < 0.65:
        raise ValueError("Generated lyrics did not primarily use Korean.")


def validate_generated_lyrics(lyrics: str, source_texts: list[str] | None = None, voice_lang: str = "") -> None:
    if len(lyrics) < GENERATED_LYRICS_MIN_CHARS:
        raise ValueError(
            f"Generated lyrics were too short ({len(lyrics)} characters). "
            f"Please regenerate; lyrics must be at least {GENERATED_LYRICS_MIN_CHARS} characters for a full song."
        )
    if voice_lang:
        _validate_lyrics_language_weight(lyrics, voice_lang)
    copied_fragment = _find_copied_source_fragment(lyrics, source_texts or [])
    if copied_fragment:
        raise ValueError(
            "Generated lyrics reused text from the lyrics prompt. Please regenerate with a shorter inspiration brief; "
            "the lyrics must be fully original and cannot include prompt sentences or phrases."
        )


def _theme_anchor_from_seed(seed: str, voice_lang: str) -> str:
    seed = re.sub(r"\[[^\]]+\]", " ", str(seed or ""))
    seed = re.sub(r"\s+", " ", seed).strip()
    if not seed:
        return {
            "Cantonese": "心入面嘅光",
            "Chinese (Mandarin)": "心里的光",
            "Korean": "마음의 빛",
            "Japanese": "心の光",
        }.get(voice_lang, "inner light")

    if re.search(r"[\u3400-\u9fff]", seed):
        keyword_map = [
            ("梦想", "梦想"), ("夢想", "夢想"), ("雨", "雨夜"), ("海", "海风"), ("城市", "城市"),
            ("星", "星光"), ("夜", "夜色"), ("爱情", "爱"), ("愛情", "愛"), ("思念", "思念"),
            ("失恋", "告别"), ("孤独", "孤独"), ("快乐", "快乐"), ("快樂", "快樂"), ("希望", "希望"),
            ("回家", "归途"), ("家", "归途"), ("朋友", "陪伴"), ("未来", "未来"), ("未來", "未來"),
        ]
        anchors = [anchor for token, anchor in keyword_map if token in seed]
        if anchors:
            return "、".join(dict.fromkeys(anchors[:3]))
        compact = re.sub(r"[^\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]+", "", seed)
        return (compact[:4] + "的回响") if compact else "心里的光"

    stop_words = {
        "about", "above", "after", "again", "against", "also", "and", "another", "around", "because",
        "before", "brief", "chorus", "describe", "feeling", "feelings", "from", "into", "like", "lyrics",
        "make", "music", "only", "prompt", "rewrite", "song", "style", "that", "the", "this", "translate",
        "verse", "want", "with", "write", "your",
    }
    words = [
        word for word in re.findall(r"[A-Za-z][A-Za-z']{2,}", seed.lower())
        if word not in stop_words
    ]
    if not words:
        return "inner light"
    unique_words = list(dict.fromkeys(words))
    return " and ".join(unique_words[:2])


def _fallback_extension_for_language(voice_lang: str) -> str:
    if voice_lang == "Cantonese":
        return (
            "[Verse 3]\n"
            "路燈一路陪我行過轉角\n"
            "舊日嘅影像慢慢變得清楚\n"
            "就算風聲遮住心跳\n"
            "我都會將未講嘅話唱到天光\n\n"
            "[Final Chorus]\n"
            "呢一刻我哋相通\n"
            "微光照住彼此嘅方向\n"
            "所有沉默開成花\n"
            "留低一首唔會熄滅嘅歌\n\n"
            "[Outro]\n"
            "當最後一粒星仍然閃爍\n"
            "我把答案放入旋律\n"
            "輕輕唱畀明日聽\n"
        )
    if voice_lang == "Chinese (Mandarin)":
        return (
            "[Verse 3]\n"
            "路灯陪我走过转角\n"
            "旧日画面慢慢变清楚\n"
            "就算风声盖过心跳\n"
            "我也把没说的话唱到破晓\n\n"
            "[Final Chorus]\n"
            "这一刻我们相通\n"
            "微光照亮彼此方向\n"
            "所有沉默开成花\n"
            "留下不会熄灭的歌\n\n"
            "[Outro]\n"
            "当最后一颗星仍闪烁\n"
            "我把答案放进旋律\n"
            "轻轻唱给明天听\n"
        )
    if voice_lang == "Korean":
        return (
            "[Verse 3]\n"
            "가로등은 천천히 길을 열고\n"
            "오래된 장면들이 선명해져\n"
            "바람이 심장 소릴 덮어도\n"
            "말 못 한 진심을 새벽까지 노래해\n\n"
            "[Final Chorus]\n"
            "이 순간 우린 같은 빛 안에\n"
            "서로의 방향을 다시 찾아\n"
            "침묵은 꽃처럼 피어나\n"
            "꺼지지 않는 노래로 남아\n\n"
            "[Outro]\n"
            "마지막 별이 아직 반짝일 때\n"
            "내 대답을 멜로디에 담아\n"
            "내일에게 조용히 불러\n"
        )
    if voice_lang == "Japanese":
        return (
            "[Verse 3]\n"
            "街灯が曲がり角を照らし\n"
            "古い景色が少しずつ澄んでいく\n"
            "風が鼓動を隠しても\n"
            "言えなかった想いを夜明けまで歌う\n\n"
            "[Final Chorus]\n"
            "この瞬間ふたつの光が\n"
            "もう一度行き先を見つける\n"
            "沈黙は花のように開き\n"
            "消えない歌になって残る\n\n"
            "[Outro]\n"
            "最後の星がまたたくうちに\n"
            "答えをメロディに預けて\n"
            "明日へそっと歌う\n"
        )
    return (
        "[Verse 3]\n"
        "Streetlights open up the corner\n"
        "Old photographs begin to breathe\n"
        "Even when the wind gets louder\n"
        "I keep the truth beneath the beat\n\n"
        "[Final Chorus]\n"
        "In this moment we align\n"
        "Small sparks turning into signs\n"
        "Every silence starts to bloom\n"
        "Every shadow leaves the room\n\n"
        "[Outro]\n"
        "When the final star keeps shining\n"
        "I place my answer in the sound\n"
        "Let tomorrow hear it rising\n"
    )


def _finalize_fallback_lyrics(lyrics: str, voice_lang: str, source_texts: list[str]) -> str:
    lyrics = lyrics.strip()
    extension = _fallback_extension_for_language(voice_lang)
    while len(lyrics) < GENERATED_LYRICS_MIN_CHARS:
        lyrics = f"{lyrics}\n\n{extension}".strip()
    return lyrics[:GENERATED_LYRICS_MAX_CHARS].strip()


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


LANGS_REQUIRING_TRADITIONAL = {"Cantonese"}
LANGS_REQUIRING_SIMPLIFIED = {"Chinese (Mandarin)", "Japanese", "Korean"}

_LANG_CODE_MAP = {
    "English": "English", "Chinese (Mandarin)": "Chinese", "Cantonese": "Chinese",
    "Korean": "Korean", "Japanese": "Japanese", "Spanish": "Spanish",
    "Portuguese": "Portuguese", "French": "French", "German": "German",
    "Indonesian": "Indonesian", "Russian": "Russian", "Italian": "Italian",
    "Arabic": "Arabic", "Hindi": "Hindi", "Vietnamese": "Vietnamese",
    "Thai": "Thai", "Turkish": "Turkish", "Polish": "Polish", "Dutch": "Dutch",
    "Swedish": "Swedish", "Norwegian": "Norwegian", "Danish": "Danish",
    "Finnish": "Finnish", "Czech": "Czech", "Romanian": "Romanian",
    "Hungarian": "Hungarian", "Ukrainian": "Ukrainian",
}


def _detect_text_language(text: str) -> str:
    """Detect the primary language of a text string using simple heuristics + MiniMax."""
    if not text or len(text.strip()) < 10:
        return "English"
    # Heuristic: check for character sets
    chinese_chars = len(re.findall(r'[\u4e00-\u9fff]', text))
    japanese_chars = len(re.findall(r'[\u3040-\u309f\u30a0-\u30ff]', text))
    korean_chars = len(re.findall(r'[\uac00-\ud7af\u1100-\u11ff]', text))
    arabic_chars = len(re.findall(r'[\u0600-\u06ff]', text))
    russian_chars = len(re.findall(r'[\u0400-\u04ff]', text))
    total_chars = len(text)
    # If enough CJK characters, use MiniMax to be more accurate
    if chinese_chars / total_chars > 0.3:
        return "Chinese (Mandarin)"
    elif korean_chars / total_chars > 0.3:
        return "Korean"
    elif japanese_chars / total_chars > 0.2:
        return "Japanese"
    elif arabic_chars / total_chars > 0.3:
        return "Arabic"
    elif russian_chars / total_chars > 0.3:
        return "Russian"
    # Use MiniMax for ambiguous cases (mixed or Latin-script texts)
    prompt_text = text[:500]
    detect_msg = f"""Detect the language of the following text. Reply with ONLY the language name from this list: English, Chinese (Mandarin), Cantonese, Korean, Japanese, Spanish, Portuguese, French, German, Indonesian, Russian, Italian, Arabic, Hindi, Vietnamese, Thai, Turkish, Polish, Dutch, Swedish, Norwegian, Danish, Finnish, Czech, Romanian, Hungarian, Ukrainian.

Text: {prompt_text}"""
    try:
        result = run_mmx([
            "text", "chat",
            "--model", "auto",
            "--system", "You are a language detection assistant. Reply with ONLY the language name, nothing else.",
            "--message", detect_msg,
            "--max-tokens", "20",
            "--temperature", "0.1",
            "--non-interactive", "--quiet", "--output", "text",
        ], timeout=10)
        result = result.strip()
        # Match against known languages
        for lang in _LANG_CODE_MAP:
            if lang.lower() in result.lower() or result.lower() in lang.lower():
                return lang
        return "English"
    except Exception:
        return "English"


def _translate_text(text: str, from_lang: str, to_lang: str) -> str:
    """Translate text from one language to another using MiniMax."""
    if not text or from_lang == to_lang:
        return text
    if to_lang == "Cantonese":
        to_native = "Cantonese (traditional Chinese characters, spoken Cantonese expressions)"
    elif to_lang == "Chinese (Mandarin)":
        to_native = "simplified Chinese"
    elif to_lang == "Japanese":
        to_native = "Japanese"
    elif to_lang == "Korean":
        to_native = "Korean"
    elif to_lang == "English":
        to_native = "English"
    else:
        to_native = _LANG_CODE_MAP.get(to_lang, to_lang)
    translate_msg = f"""Translate the following text into {to_native}.

Rules:
- Translate meaning and spirit, NOT word-for-word
- Adapt idioms and expressions naturally to {to_native}
- Keep the same tone and emotional quality
- Output ONLY the translated text, no explanation, no quotes, no notes

Text to translate:
{text}"""
    try:
        result = run_mmx([
            "text", "chat",
            "--model", "auto",
            "--system", f"You are a professional translator into {to_native}. Output ONLY the translated text.",
            "--message", translate_msg,
            "--max-tokens", "2000",
            "--temperature", "0.7",
            "--non-interactive", "--quiet", "--output", "text",
        ], timeout=30)
        # Clean up the result
        result = result.strip()
        result = re.sub(r'^["\']', '', result).strip()
        result = re.sub(r'["\']$', '', result).strip()
        prefixes = ("translation:", "translated text:", "here is the translation:")
        lower_result = result.lower()
        for prefix in prefixes:
            if lower_result.startswith(prefix):
                result = result[len(prefix):].strip()
                lower_result = result.lower()
        return result
    except Exception as exc:
        print(f"[translate] failed: {exc}")
        return text  # fallback to original if translation fails


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
        # Check for specific MiniMax error codes
        base_resp = resp.get("base_resp", {})
        status_code = base_resp.get("status_code", 0)
        status_msg = base_resp.get("status_msg", "")
        if status_code == 2054 or "not exist" in status_msg.lower() or "not found" in status_msg.lower():
            raise RuntimeError(f"This voice is not available for preview: {voice_id}")
        # Check for quota/usage limit errors
        response_text = f"{status_msg} {resp}".lower()
        if any(token in response_text for token in ("limit", "quota", "usage", "exceed", "rate")):
            raise RuntimeError("Voice preview is temporarily unavailable due to high demand. Please try again in a few minutes.")
        print(f"[TTS] unexpected resp: {resp}")
        raise RuntimeError(f"No audio in TTS response: {status_msg or str(resp)}")
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


def generate_lyrics_from_text_model(job: dict[str, Any], timeout: float = 180) -> str:
    voice_id = str(job.get("voice_id", "")).strip()
    lyrics_language = str(job.get("lyrics_language", "auto")).strip()
    if lyrics_language and lyrics_language != "auto":
        voice_lang = lyrics_language
    elif voice_id:
        voice_lang = _detect_lang_from_voice_id(voice_id)
    else:
        voice_lang = "English"
    prompt = str(job.get("prompt", "")).strip()
    lyrics_idea = str(job.get("lyrics_idea", "")).strip()
    lyrics_extra = str(job.get("lyrics_extra", "")).strip()
    brief_parts = [part for part in (lyrics_idea, prompt, lyrics_extra) if part]
    brief = "\n".join(brief_parts).strip() or "Create a clear, singable AI music lyric."

    system_prompt = f"The selected vocal language is {voice_lang}. The lyrics must be written in {voice_lang}."
    user_prompt = f"根据提示创作出一首 3-5 分钟时长的歌词。\n\n提示：\n{brief}"

    output = run_mmx([
        "text", "chat",
        "--model", "auto",
        "--system", system_prompt,
        "--message", user_prompt,
        "--max-tokens", "5000",
        "--temperature", "0.8",
        "--non-interactive",
        "--quiet",
        "--output", "text",
    ], timeout=int(max(1, timeout)))
    lyrics = clean_generated_lyrics(output)
    if not lyrics:
        raise RuntimeError("MiniMax lyrics_generation model returned empty lyrics.")
    return lyrics


def fallback_generated_lyrics(prompt: str, lyrics_idea: str, extra: dict[str, Any] | None = None, voice_id: str = "", lyrics_language: str = "auto", interface_language: str = "") -> str:
    """Fast local fallback — generates simple lyrics from user seed only.
    No hardcoded templates. Uses seed words as thematic anchors only.
    The language is determined by voice_lang, not by the seed language."""
    extra = extra or {}
    seed = (lyrics_idea or prompt or "").strip()
    seed = re.sub(r"\s+", " ", seed)[:80] if seed else ""
    if lyrics_language and lyrics_language != "auto":
        voice_lang = lyrics_language
    elif voice_id:
        voice_lang = _detect_lang_from_voice_id(voice_id)
    else:
        voice_lang = "English"
    mood = str(extra.get("mood", "")).strip()
    genre = str(extra.get("genre", "")).strip()

    # Build a short theme anchor without copying full user sentences into the fallback lyrics.
    seed_theme = _theme_anchor_from_seed(seed, voice_lang)
    source_texts = [prompt, lyrics_idea, *[str(value) for value in extra.values() if value]]

    if voice_lang == "Cantonese":
        theme_line = "有束光喺心入面慢慢浮現"
        mood_line = "带着" + mood + "心情" if mood else ""
        genre_line = "，" + genre + "曲风" if genre else ""
        return _finalize_fallback_lyrics((
            "[Verse 1]\n" + theme_line + genre_line + "\n"
            + "霓虹喺雨後慢慢散開\n"
            + "我將未講出口嘅心事收埋\n\n"
            + "[Pre-Chorus]\n"
            + "仍然聽見心跳帶路\n"
            + "行過暗巷都唔怕再重來\n\n"
            + "[Chorus]\n"
            + "呢一刻我哋望住同一片海\n"
            + "浪花將沉默唱到天光\n"
            + "就算世界轉得再快\n"
            + "我都會跟住旋律搵返方向\n\n"
            + "[Verse 2]\n"
            + "舊相簿入面有風經過\n"
            + "每一頁都照住未完嘅夢\n"
            + "我學識喺跌低之後\n"
            + "將眼淚變成節奏\n\n"
            + "[Bridge]\n"
            + "如果長夜仲未肯離開\n"
            + "我就點起一盞細細嘅燈\n\n"
            + "[Chorus]\n"
            + "呢一刻我哋望住同一片海\n"
            + "浪花將沉默唱到天光\n"
            + "就算世界轉得再快\n"
            + "我都會跟住旋律搵返方向\n\n"
            + "[Outro]\n"
            + "留低一首歌陪明日發亮\n"
        ), voice_lang, source_texts)

    elif voice_lang == "Chinese (Mandarin)":
        theme_line = "有束光在心里慢慢浮现"
        mood_line = "带着" + mood if mood else ""
        genre_line = "，" + genre + "风格" if genre else ""
        return _finalize_fallback_lyrics((
            "[Verse 1]\n" + theme_line + genre_line + mood_line + "\n"
            + "城市光影忽明忽暗\n"
            + "思绪飘向每一个深夜\n\n"
            + "[Pre-Chorus]\n"
            + "依然听见心里的声音\n"
            + "一次次尝试从未放弃\n\n"
            + "[Chorus]\n"
            + "这一刻我们相通\n"
            + "一切都不需要言语\n"
            + "风把沉默吹成花\n"
            + "梦把远方推近一点\n\n"
            + "[Verse 2]\n"
            + "时光机里存着感受\n"
            + "打开发现全是你\n"
            + "为什么不放手\n"
            + "就是因为太喜欢你\n\n"
            + "[Bridge]\n"
            + "世界停下来等我\n"
            + "今天阴天转晴\n\n"
            + "[Chorus]\n"
            + "这一刻我们相通\n"
            + "一切都不需要言语\n"
            + "风把沉默吹成花\n"
            + "梦把远方推近一点\n\n"
            + "[Outro]\n"
            + "把答案放进旋律里\n"
        ), voice_lang, source_texts)

    elif voice_lang == "Korean":
        theme_line = "작은 빛이 마음속에 천천히 떠올라"
        return _finalize_fallback_lyrics((
            "[Verse 1]\n" + theme_line + "\n"
            + "도시 불빛이 반짝이고\n"
            + "낡은 기억이 밤을 건너와\n\n"
            + "[Pre-Chorus]\n"
            + "여전히 네 목소리가 들려\n"
            + "흔들려도 멈춘 적은 없어\n\n"
            + "[Chorus]\n"
            + "이 순간 우린 같은 숨을 쉬어\n"
            + "말로 다 못 한 마음이 노래가 돼\n"
            + "멀어진 길도 다시 이어져\n"
            + "새벽 끝에서 빛을 찾아\n\n"
            + "[Verse 2]\n"
            + "접어 둔 편지 위로 비가 내려\n"
            + "잊은 줄 알던 장면들이 깨어나\n"
            + "상처는 리듬이 되고\n"
            + "두려움은 발걸음이 돼\n\n"
            + "[Bridge]\n"
            + "세상이 멈춰서 나를 기다려\n"
            + "오늘 흐렸다가 맑아졌어\n\n"
            + "[Chorus]\n"
            + "이 순간 우린 같은 숨을 쉬어\n"
            + "말로 다 못 한 마음이 노래가 돼\n"
            + "멀어진 길도 다시 이어져\n"
            + "새벽 끝에서 빛을 찾아\n\n"
            + "[Outro]\n"
            + "작은 멜로디가 내일을 깨워\n"
        ), voice_lang, source_texts)

    elif voice_lang == "Japanese":
        theme_line = "小さな光が胸の奥で揺れている"
        return _finalize_fallback_lyrics((
            "[Verse 1]\n" + theme_line + "\n"
            + "街の灯りが煌めいて\n"
            + "古い記憶が夜を渡る\n\n"
            + "[Pre-Chorus]\n"
            + "まだ鼓動が道を覚えてる\n"
            + "迷いながらも歩いてきた\n\n"
            + "[Chorus]\n"
            + "この瞬間 同じ空を見上げ\n"
            + "言えなかった想いが歌になる\n"
            + "遠い道もまたつながって\n"
            + "夜明けの端で光を探す\n\n"
            + "[Verse 2]\n"
            + "閉じた手紙に雨が落ちて\n"
            + "忘れた景色が息を返す\n"
            + "傷跡はリズムになり\n"
            + "ためらいは一歩に変わる\n\n"
            + "[Bridge]\n"
            + "世界が少し静かになる\n"
            + "その隙間で願いを灯す\n\n"
            + "[Chorus]\n"
            + "この瞬間 同じ空を見上げ\n"
            + "言えなかった想いが歌になる\n"
            + "遠い道もまたつながって\n"
            + "夜明けの端で光を探す\n\n"
            + "[Outro]\n"
            + "小さなメロディが明日を起こす\n"
        ), voice_lang, source_texts)

    else:
        seed_word = "the light inside"
        mood_tag = " — " + mood if mood else ""
        genre_tag = " / " + genre if genre else ""
        return _finalize_fallback_lyrics((
            "[Verse 1]\n"
            + seed_word + mood_tag + genre_tag + "\n"
            + "Every corner holds a memory\n"
            + "Every breath reminds me of you\n\n"
            + "[Pre-Chorus]\n"
            + "Still hearing your voice inside\n"
            + "Never once did I let go\n\n"
            + "[Chorus]\n"
            + "In this moment we align\n"
            + "Feelings words could never find\n\n"
            + "[Verse 2]\n"
            + "Time machine stores all the feelings\n"
            + "Open it up and it's all you\n"
            + "Why I can't let go\n"
            + "Because I love you this much\n\n"
            + "[Bridge]\n"
            + "The world stopped just to wait for me\n"
            + "Today turned from gray to bright\n\n"
            + "[Chorus]\n"
            + "In this moment we align\n"
            + "Feelings words could never find\n\n"
            + "[Outro]\n"
            + seed_word + "\n"
        ), voice_lang, source_texts)


