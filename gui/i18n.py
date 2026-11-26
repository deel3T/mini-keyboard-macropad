"""Arayuz dili (TR/EN).

Sabit metinler main.py'deki TRANSLATIONS tablosunda; mesaj kutusu, durum
satiri, firmware/seri port mesajlari gibi o an uretilen metinler t() ile
secilir. Dil, ayar yuklenince ve kullanici degistirince set_language() ile
guncellenir.
"""

_state = {"lang": "EN"}


def set_language(code):
    _state["lang"] = "EN" if code == "EN" else "TR"


def language():
    return _state["lang"]


def t(tr_text, en_text):
    """O anki dile gore metin."""
    return en_text if _state["lang"] == "EN" else tr_text
