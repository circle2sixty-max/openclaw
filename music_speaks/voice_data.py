"""Voice library and interface language helpers for Music Speaks."""

from __future__ import annotations

import re

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
    # Let MiniMax API handle actual voice_id validity — we just filter injection
    if len(value) < 1 or len(value) > 200:
        return False
    if ".." in value or value.startswith("/"):
        return False
    return True

