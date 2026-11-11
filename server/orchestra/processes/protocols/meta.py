"""Дата и название совещания из шапки документа. Даты из пунктов (сроки) не берём."""
import re
from datetime import date

MONTHS = {"январ": 1, "феврал": 2, "март": 3, "апрел": 4, "ма": 5, "июн": 6, "июл": 7, "август": 8,
          "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12}
MONTH_RE = r"(январ[яь]|феврал[яь]|марта?|апрел[яь]|ма[яй]|июн[яь]|июл[яь]|августа?|сентябр[яь]|октябр[яь]|ноябр[яь]|декабр[яь])"
# Слова, рядом с которыми дата — это дата совещания, а не срок
ANCHOR = re.compile(r"дата|протокол|совещани|планерк|планёрк|заседани|встреч|состоял|проведен|от\s+\d|г\.\s*москва", re.I)
DEADLINE = re.compile(r"\b(до|к|срок|в течение|не позднее)\b", re.I)
TITLE_WORDS = re.compile(r"протокол|совещани|планерк|планёрк|заседани|встреч|созвон", re.I)


def _dates(line: str) -> list[date]:
    out = []
    for m in re.finditer(r"(?<![\d.])(\d{1,2})\.(\d{1,2})\.(\d{4}|\d{2})(?!\d)", line):
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        y += 2000 if y < 100 else 0
        try:
            out.append(date(y, mo, d))
        except ValueError:
            pass
    for m in re.finditer(rf"(?<!\d)(\d{{1,2}})\s+{MONTH_RE}\s+(\d{{4}})", line, re.I):
        mo = next(v for k, v in MONTHS.items() if m.group(2).lower().startswith(k))
        try:
            out.append(date(int(m.group(3)), mo, int(m.group(1))))
        except ValueError:
            pass
    for m in re.finditer(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)", line):
        try:
            out.append(date(int(m.group(1)), int(m.group(2)), int(m.group(3))))
        except ValueError:
            pass
    return out


def detect(text: str) -> dict:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    head = lines[:15]
    meeting, source = None, None
    for line in head:
        if re.match(r"^\d{1,3}[.)]\s", line):
            break  # начались пункты — дальше сроки, а не дата совещания
        if ANCHOR.search(line) and not DEADLINE.search(line):
            found = _dates(line)
            if found:
                meeting, source = found[0].isoformat(), line[:120]
                break
    title = next((ln[:120] for ln in head[:3] if TITLE_WORDS.search(ln)), None)
    return {"meeting_date": meeting, "date_source": source, "title": title}
