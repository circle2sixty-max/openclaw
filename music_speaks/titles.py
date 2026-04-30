"""Song title cleaning and fallback generation helpers."""

from __future__ import annotations

import re
from typing import Any


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


