"""Нормализация русского текста: токены и основы слов."""
import re
from functools import lru_cache

import snowballstemmer

_stemmer = snowballstemmer.stemmer("russian")
WORD_RE = re.compile(r"[a-zа-яё0-9]+", re.IGNORECASE)


def norm(text: str | None) -> str:
    return (text or "").lower().replace("ё", "е")


def tokens(text: str | None) -> list[str]:
    return WORD_RE.findall(norm(text))


@lru_cache(maxsize=4096)
def stem(word: str) -> str:
    return _stemmer.stemWord(word)


def stems(text: str | None) -> list[str]:
    return [stem(t) for t in tokens(text)]
