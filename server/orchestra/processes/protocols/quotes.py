"""Поиск цитаты в протоколе: точно, неточно или не найдена."""
from difflib import SequenceMatcher

QUOTES = set("«»“”„\"'")
DASHES = set("—–‑")


def _normalize(text: str) -> tuple[str, list[int]]:
    """Нормализованная строка + индексы исходных символов (для подсветки)."""
    out, idx, prev_space = [], [], False
    for i, ch in enumerate(text):
        c = ch.lower().replace("ё", "е")
        if c in QUOTES:
            c = '"'
        elif c in DASHES:
            c = "-"
        if c.isspace():
            if prev_space:
                continue
            c, prev_space = " ", True
        else:
            prev_space = False
        out.append(c)
        idx.append(i)
    return "".join(out), idx


def _clean_quote(q: str) -> str:
    return q.strip().strip('«»"\'“” ').strip(" .;,…")


def _tighten(nq: str, text: str, s: int, e: int) -> tuple[int, int]:
    """Границы неточной цитаты — от первого до последнего совпавшего куска, а не вся строка."""
    chunk, idx = _normalize(text[s:e])
    blocks = [b for b in SequenceMatcher(None, nq, chunk, autojunk=False).get_matching_blocks() if b.size >= 3]
    if not blocks:
        return s, e
    first, last = blocks[0].b, blocks[-1].b + blocks[-1].size - 1
    return s + idx[first], s + idx[last] + 1


def locate(quote: str | None, text: str) -> dict:
    q = _clean_quote(quote or "")
    if len(q) < 6:
        return {"status": "not_found", "start": None, "end": None, "match": None, "score": 0.0}
    nq, _ = _normalize(q)
    nt, idx = _normalize(text)

    pos = nt.find(nq)
    if pos >= 0:
        start, end = idx[pos], idx[pos + len(nq) - 1] + 1
        return {"status": "exact", "start": start, "end": end, "match": text[start:end], "score": 1.0}

    # Неточная цитата: ищем строку (или пару строк), которая лучше всего её покрывает
    lines, offset = [], 0
    for raw in text.splitlines(keepends=True):
        if raw.strip():
            lines.append((offset, offset + len(raw.rstrip("\r\n"))))
        offset += len(raw)
    windows = lines + [(a[0], b[1]) for a, b in zip(lines, lines[1:])]

    best = (0.0, None)
    for s, e in windows:
        chunk, _ = _normalize(text[s:e])
        sm = SequenceMatcher(None, nq, chunk, autojunk=False)
        covered = sum(b.size for b in sm.get_matching_blocks() if b.size >= 3) / len(nq)
        if covered > best[0]:
            best = (covered, (s, e))
    score, span = best
    if span and score >= 0.8:
        s, e = _tighten(nq, text, *span)
        return {"status": "fuzzy", "start": s, "end": e, "match": text[s:e], "score": round(score, 2)}
    return {"status": "not_found", "start": None, "end": None, "match": None, "score": round(score, 2)}
