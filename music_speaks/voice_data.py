"""Voice library and interface language helpers for Music Speaks."""

from __future__ import annotations

import re
from typing import Any

DEFAULT_SYSTEM_VOICES = [
    # Chinese (Mandarin)
    "Chinese (Mandarin)_Reliable_Executive",
    "Chinese (Mandarin)_News_Anchor",
    "Chinese (Mandarin)_Mature_Woman",
    "Chinese (Mandarin)_Sweet_Lady",
    "Chinese (Mandarin)_Lyrical_Voice",
    "Chinese (Mandarin)_Young_Lady",
    "Chinese (Mandarin)_Warm_Father",
    "Chinese (Mandarin)_Professional_Reporter",
    "Chinese (Mandarin)_Warm_Grandmother",
    "Chinese (Mandarin)_Energetic_Youth",
    # Cantonese
    "Cantonese_ProfessionalHost",
    "Cantonese_GentleLady",
    "Cantonese_Cheerful_Girl",
    "Cantonese_Calm_Man",
    # English
    "English_Trustworthy_Man",
    "English_Graceful_Lady",
    "English_Whispering_Girl",
    "English_Professional_Woman",
    "English_Warm_Father",
    "English_Energetic_Youth",
    "English_Calm_Narrator",
    "English_Cheerful_Friend",
    "English_Deep_Emotional_Male",
    "English_Young_Girl",
    "English_British_Gentleman",
    "English_American_Boy",
    # Japanese
    "Japanese_KindLady",
    "Japanese_CalmLady",
    "Japanese_Cheerful_Girl",
    "Japanese_Gentle_Father",
    "Japanese_Professional_Man",
    # Korean
    "Korean_SweetGirl",
    "Korean_CalmLady",
    "Korean_Cheerful_Boy",
    "Korean_Gentle_Father",
    "Korean_Professional_Woman",
    # Spanish
    "Spanish_Serene_Woman",
    "Spanish_Narrator",
    "Spanish_Cheerful_Girl",
    "Spanish_Warm_Father",
    # Portuguese
    "Portuguese_Sentimental_Lady",
    "Portuguese_Cheerful_Woman",
    "Portuguese_Calm_Man",
    # French
    "French_Female_News_Anchor",
    "French_Male_Narrator",
    "French_Cheerful_Girl",
    "French_Warm_Father",
    # German
    "German_Friendly_Man",
    "German_Cheerful_Woman",
    "German_Calm_Narrator",
    # Russian
    "Russian_Reliable_Man",
    "Russian_Warm_Woman",
    "Russian_Calm_Narrator",
    # Italian
    "Italian_Narrator",
    "Italian_Warm_Woman",
    "Italian_Cheerful_Man",
    # Arabic
    "Arabic_Calm_Woman",
    "Arabic_Deep_Male",
    "Arabic_Young_Woman",
    # Indonesian
    "Indonesian_Cheerful_Girl",
    "Indonesian_Calm_Woman",
    "Indonesian_Young_Man",
    # Turkish
    "Turkish_Calm_Woman",
    "Turkish_Cheerful_Man",
    # Ukrainian
    "Ukrainian_Calm_Woman",
    "Ukrainian_Warm_Man",
    # Dutch
    "Dutch_Cheerful_Woman",
    "Dutch_Calm_Man",
    # Vietnamese
    "Vietnamese_Cheerful_Girl",
    "Vietnamese_Calm_Woman",
    # Hindi
    "Hindi_Cheerful_Woman",
    "Hindi_Calm_Man",
    # Thai
    "Thai_Cheerful_Girl",
    "Thai_Calm_Woman",
    # Polish
    "Polish_Cheerful_Woman",
    "Polish_Warm_Man",
]

VOICE_PREVIEW_TEXTS = {
    "Chinese (Mandarin)": "你好,这是一段音色试听样本。Music Speaks 把你的文字变成歌曲。",
    "Cantonese": "你好,呢段係音色試聽樣本。Music Speaks 將你嘅文字變成歌曲。",
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
VOICE_ID_SAFE_RE = re.compile(r"^[A-Za-z0-9_()./\- ()]+$")

VOICE_USE_CASE_RULES = (
    (("anchor", "announcer", "host", "radio", "flight attendant", "reporter"), "Broadcast / host"),
    (("executive", "trustworthy", "reliable", "diligent", "gentleman", "father", "man"), "Business / explainer"),
    (("narrator", "butler", "elder", "senior", "wise", "intellectual"), "Narration / storytelling"),
    (("princess", "queen", "knight", "warrior", "robot", "santa", "grinch", "rudolph", "pig", "elf", "armor", "spirit"), "Character / cinematic"),
    (("whisper", "soft", "warm", "gentle", "lady", "woman", "bestie", "sweet", "graceful", "sentimental", "serene", "soothing", "charming", "grandmother", "mother"), "Warm / intimate"),
    (("boy", "girl", "youth", "teen", "student", "friend", "bloke", "boyfriend", "sister", "young"), "Youthful / social"),
)

VOICE_PERSONA_RULES = (
    (("young", "youth", "boy", "girl", "teen", "student"), "Youthful"),
    (("mature", "senior", "elder", "grandmother", "grandfather", "father", "mother"), "Mature"),
    (("executive", "professional", "reporter", "anchor", "narrator"), "Professional"),
    (("warm", "gentle", "kind", "sweet", "soothing", "friendly", "cheerful", "sentimental", "emotional"), "Warm"),
    (("princess", "queen", "knight", "warrior", "robot", "santa", "grinch", "rudolph", "pig", "elf", "spirit", "character"), "Character"),
)

VOICE_GENDER_KEYWORDS = {
    "female": ("woman", "lady", "girl", "mother", "grandmother", "sister", "female", "she"),
    "male": ("man", "boy", "father", "grandfather", "gentleman", "male", "he"),
}

VOICE_STYLE_TAGS = (
    (("warm", "gentle", "kind", "sweet", "soothing", "friendly"), ["warm", "friendly"]),
    (("professional", "executive", "reporter", "anchor", "business"), ["professional", "broadcast"]),
    (("calm", "serene", "quiet", "steady", "relaxed"), ["calm", "steady"]),
    (("cheerful", "happy", "energetic", "bright", "lively"), ["cheerful", "energetic"]),
    (("whisper", "soft", "intimate", "gentle"), ["whisper", "intimate"]),
    (("narrator", "storytelling", "wise", "intellectual"), ["narrative", "storytelling"]),
    (("youth", "young", "teen", "student"), ["youthful"]),
    (("deep", "low", "bass"), ["deep", "low"]),
    (("broadcast", "radio", "news", "announcer"), ["broadcast"]),
    (("character", "cinematic", "warrior", "princess", "robot"), ["character", "cinematic"]),
)

VOICE_MOOD_RULES = (
    (("calm", "serene", "quiet", "steady", "relaxed", "peaceful"), "calm"),
    (("cheerful", "happy", "energetic", "bright", "lively", "joyful"), "energetic"),
    (("sentimental", "emotional", "deep", "warm", "romantic", "nostalgic"), "sentimental"),
    (("professional", "business", "executive", "reliable", "trustworthy"), "professional"),
    (("whisper", "intimate", "soft", "gentle", "quiet"), "intimate"),
    (("youth", "young", "teen", "playful"), "playful"),
)


def _voice_language_details(voice_id: str) -> tuple[str, str]:
    value = str(voice_id or "").strip()
    for lang in VOICE_PREVIEW_LANGUAGES:
        if value == lang or value.startswith(f"{lang}_") or value.startswith(f"{lang} "):
            return lang, "prefix"
    return "English", "default"


def _detect_lang_from_voice_id(voice_id: str) -> str:
    return _voice_language_details(voice_id)[0]


UI_LANGUAGE_LABELS = {
    "en": "English",
    "zh": "Chinese (Mandarin)",
    "yue": "Cantonese",
    "ko": "Korean",
    "ja": "Japanese",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "pt": "Portuguese",
    "it": "Italian",
    "ru": "Russian",
    "ar": "Arabic",
    "hi": "Hindi",
    "id": "Indonesian",
    "vi": "Vietnamese",
    "th": "Thai",
    "tr": "Turkish",
    "pl": "Polish",
    "nl": "Dutch",
    "sv": "Swedish",
    "no": "Norwegian",
    "da": "Danish",
    "fi": "Finnish",
    "cs": "Czech",
    "ro": "Romanian",
    "hu": "Hungarian",
    "uk": "Ukrainian",
}


def _interface_language_label(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith("voice:"):
        return text.removeprefix("voice:").strip()
    return UI_LANGUAGE_LABELS.get(text, text if re.fullmatch(r"[A-Za-z][A-Za-z ()-]{1,40}", text) else "")


def _is_safe_voice_id(voice_id: str) -> bool:
    value = str(voice_id or "").strip()
    # Basic safety: reject path traversal and obviously malicious patterns
    # Let MiniMax API handle actual voice_id validity - we just filter injection
    if len(value) < 1 or len(value) > 200:
        return False
    if ".." in value or value.startswith("/"):
        return False
    return True


def normalize_voice_display_name(voice_id: str, group_key: str = "") -> str:
    name = str(voice_id or "")
    if group_key and group_key != "__other__":
        if name.startswith(group_key + "_"):
            name = name[len(group_key) + 1 :]
        elif name.startswith(group_key):
            name = name[len(group_key) :].lstrip("_ -")
    name = (
        name.replace("_", " ")
        .replace("(F)", " Female")
        .replace("(M)", " Male")
    )
    name = re.sub(r"([a-z])([A-Z])", r"\1 \2", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name.title() or str(voice_id or "").replace("_", " ")


def infer_voice_use_case(voice_id: str, display_name: str = "") -> str:
    sample = f"{voice_id} {display_name}".lower()
    for tokens, label in VOICE_USE_CASE_RULES:
        if any(token in sample for token in tokens):
            return label
    return "General music / spoken demo"


def infer_voice_persona(voice_id: str, display_name: str = "") -> str:
    sample = f"{voice_id} {display_name}".lower()
    for tokens, label in VOICE_PERSONA_RULES:
        if any(token in sample for token in tokens):
            return label
    return "General"


def infer_voice_gender(voice_id: str, display_name: str = "") -> str:
    sample = f"{voice_id} {display_name}".lower()
    for gender, keywords in VOICE_GENDER_KEYWORDS.items():
        if any(kw in sample for kw in keywords):
            return gender
    return "neutral"


def derive_style_tags(voice_id: str, display_name: str = "") -> list[str]:
    sample = f"{voice_id} {display_name}".lower()
    tags: set[str] = set()
    for tokens, tag_list in VOICE_STYLE_TAGS:
        if any(token in sample for token in tokens):
            tags.update(tag_list)
    return sorted(tags)


def infer_voice_mood(voice_id: str, display_name: str = "") -> str:
    sample = f"{voice_id} {display_name}".lower()
    for tokens, mood in VOICE_MOOD_RULES:
        if any(token in sample for token in tokens):
            return mood
    return "neutral"


def build_voice_metadata(
    voice_id: str,
    *,
    preview_supported: bool = True,
    unavailable_reason: str = "",
    source: str = "catalog",
    display_name: str = "",
    language: str = "",
    use_case: str = "",
    language_source: str = "",
    persona: str = "",
    gender: str = "",
    style_tags: list[str] | tuple[str, ...] | str = "",
    mood: str = "",
    recommended_for: str = "",
) -> dict[str, Any]:
    voice = str(voice_id or "").strip()
    detected_language, detected_language_source = _voice_language_details(voice)
    resolved_language = str(language or detected_language).strip() or "English"
    resolved_language_source = str(language_source or detected_language_source).strip() or detected_language_source
    resolved_display_name = str(display_name or normalize_voice_display_name(voice, resolved_language)).strip()
    resolved_reason = str(unavailable_reason or "").strip()
    resolved_preview = bool(preview_supported) and not resolved_reason
    resolved_use_case = str(use_case or infer_voice_use_case(voice, resolved_display_name)).strip()
    resolved_persona = str(persona or infer_voice_persona(voice, resolved_display_name)).strip()
    resolved_gender = str(gender or infer_voice_gender(voice, resolved_display_name)).strip()
    if isinstance(style_tags, str):
        if style_tags:
            resolved_style_tags = [t.strip() for t in style_tags.split(",") if t.strip()]
        else:
            resolved_style_tags = derive_style_tags(voice, resolved_display_name)
    else:
        resolved_style_tags = list(style_tags) if style_tags else derive_style_tags(voice, resolved_display_name)
    resolved_mood = str(mood or infer_voice_mood(voice, resolved_display_name)).strip()
    resolved_recommended = str(recommended_for or "").strip()
    return {
        "id": voice,
        "language": resolved_language,
        "language_source": resolved_language_source,
        "display_name": resolved_display_name,
        "preview_supported": resolved_preview,
        "use_case": resolved_use_case,
        "persona": resolved_persona,
        "gender": resolved_gender,
        "style_tags": resolved_style_tags,
        "mood": resolved_mood,
        "recommended_for": resolved_recommended,
        "unavailable_reason": resolved_reason,
        "source": source,
    }


def build_voice_metadata_map(
    voices: list[str] | tuple[str, ...],
    *,
    preview_supported: bool = True,
    fallback: bool = False,
    source: str = "catalog",
) -> dict[str, dict[str, Any]]:
    resolved_source = "fallback" if fallback else source
    metadata: dict[str, dict[str, Any]] = {}
    for entry in voices or []:
        voice = str(entry or "").strip()
        if not voice:
            continue
        metadata[voice] = build_voice_metadata(
            voice,
            preview_supported=preview_supported,
            source=resolved_source,
        )
    return metadata
