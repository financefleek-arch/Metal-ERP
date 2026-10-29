"""Devanagari -> Roman, for SEARCH QUERIES only.

A shopkeeper typing "कढ़ाई" or "बाल्टी" into the item search should land on the
Roman-spelled catalogue ("Kadai", "Balti"). Stored names are never rewritten
with this; it feeds the query side of `apply_search`, whose synonym map
("kadhai"/"kadahi" -> "kadai") and trigram rung then absorb the spelling noise.

The scheme is Hinglish, not scholarly: long vowels collapse (ई -> i, ऊ -> u) the
way shop catalogues spell them, and the inherent 'a' is dropped at the end of
a word and on the penultimate consonant of a longer word (चमचा -> chamcha, not
chamacha). It is approximate by design.
"""

from __future__ import annotations

import re

_DEVANAGARI = re.compile(r"[ऀ-ॿ]")

_VOWELS = {
    "अ": "a", "आ": "a", "इ": "i", "ई": "i", "उ": "u", "ऊ": "u",
    "ऋ": "ri", "ए": "e", "ऐ": "ai", "ओ": "o", "औ": "au", "ऑ": "o",
}
_MATRAS = {
    "ा": "a", "ि": "i", "ी": "i", "ु": "u", "ू": "u", "ृ": "ri",
    "े": "e", "ै": "ai", "ो": "o", "ौ": "au", "ॉ": "o", "ॅ": "e",
}
_CONSONANTS = {
    "क": "k", "ख": "kh", "ग": "g", "घ": "gh", "ङ": "n",
    "च": "ch", "छ": "chh", "ज": "j", "झ": "jh", "ञ": "n",
    "ट": "t", "ठ": "th", "ड": "d", "ढ": "dh", "ण": "n",
    "त": "t", "थ": "th", "द": "d", "ध": "dh", "न": "n",
    "प": "p", "फ": "f", "ब": "b", "भ": "bh", "म": "m",
    "य": "y", "र": "r", "ल": "l", "व": "v",
    "श": "sh", "ष": "sh", "स": "s", "ह": "h",
    "ळ": "l",
}
# consonant + nukta (़) forms
_NUKTA = {"ड": "d", "ढ": "dh", "क": "q", "ख": "kh", "ग": "g", "ज": "z", "फ": "f"}
_DIGITS = {c: str(i) for i, c in enumerate("०१२३४५६७८९")}
_VIRAMA = "्"
_NUKTA_MARK = "़"
_NASAL = {"ं": "n", "ँ": "n", "ः": "h"}


# English words that shops type in Devanagari: phonetics would give "stil", "plet".
_LOANWORDS = {
    "स्टील": "steel", "प्लेट": "plate", "ट्रे": "tray", "टिफिन": "tiffin",
    "प्लास्टिक": "plastic", "एल्युमिनियम": "aluminium", "अल्युमिनियम": "aluminium",
    "कॉपर": "copper", "ब्रास": "brass", "बाउल": "bowl", "जग": "jug",
    "बोतल": "bottle", "ग्लास": "glass", "कुकर": "cooker", "तवा": "tawa",
    "सेट": "set", "बकेट": "bucket", "मग": "mug", "केतली": "ketli",
    "डोंगा": "donga", "कैसरोल": "casserole", "थर्मस": "thermos",
}


def has_devanagari(text: str) -> bool:
    return bool(_DEVANAGARI.search(text))


def _word(word: str) -> str:
    """One Devanagari word -> Roman. Builds syllables, then applies schwa deletion."""
    if word in _LOANWORDS:
        return _LOANWORDS[word]
    # syllables: [consonant_roman, vowel_roman | None(inherent a) | "" (virama)]
    out: list[list[str | None]] = []
    i = 0
    while i < len(word):
        ch = word[i]
        if ch in _VOWELS:
            out.append(["", _VOWELS[ch]])
        elif ch in _CONSONANTS:
            roman = _CONSONANTS[ch]
            if i + 1 < len(word) and word[i + 1] == _NUKTA_MARK:
                roman = _NUKTA.get(ch, roman)
                i += 1
            out.append([roman, None])
        elif ch in _MATRAS and out:
            out[-1][1] = _MATRAS[ch]
        elif ch == _VIRAMA and out:
            out[-1][1] = ""  # conjunct: no vowel after this consonant
        elif ch in _NASAL:
            out.append(["", ""])
            out[-1][0] = _NASAL[ch]
        elif ch in _DIGITS:
            out.append([_DIGITS[ch], ""])
        elif ch == _NUKTA_MARK:
            pass
        else:
            out.append([ch, ""])  # latin letters, punctuation: carry through
        i += 1

    # inherent-a handling: indices of syllables still carrying the schwa
    open_idx = [k for k, s in enumerate(out) if s[1] is None]
    for k in open_idx:
        out[k][1] = "a"
    if out and out[-1][1] == "a" and open_idx and open_idx[-1] == len(out) - 1:
        out[-1][1] = ""  # word-final schwa: कमल -> kamal
    # penultimate schwa in a 3+ syllable word whose last syllable has a real vowel
    if len(out) >= 3 and (len(out) - 2) in open_idx and out[-1][1] not in ("", None):
        k = len(out) - 2
        if k > 0 and out[k][1] == "a":
            out[k][1] = ""
    return "".join((s[0] or "") + (s[1] or "") for s in out)


def devanagari_to_roman(text: str) -> str:
    """Transliterate any Devanagari in `text`; everything else passes through."""
    if not has_devanagari(text):
        return text
    parts = re.split(r"([ऀ-ॿ]+)", text)
    return "".join(_word(p) if has_devanagari(p) else p for p in parts)
