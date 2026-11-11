"""Детерминированный разбор сроков на русском относительно даты совещания."""
import calendar
import re
from datetime import date, timedelta

from .text import norm

WD = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
WD_SHORT = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
WD_RE = {0: r"понедельник\w*", 1: r"вторник\w*", 2: r"сред[аеуы]", 3: r"четверг\w*",
         4: r"пятниц[аеуы]", 5: r"суббот[аеуы]", 6: r"воскресень[еяю]"}
MONTHS = {"январ": 1, "феврал": 2, "март": 3, "апрел": 4, "ма": 5, "июн": 6, "июл": 7,
          "август": 8, "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12}
MONTH_RE = r"(январ[яь]|феврал[яь]|марта?|апрел[яь]|ма[яй]|июн[яь]|июл[яь]|августа?|сентябр[яь]|октябр[яь]|ноябр[яь]|декабр[яь])"
NUM_WORDS = {"один": 1, "одну": 1, "одна": 1, "два": 2, "две": 2, "пару": 2, "три": 3, "четыре": 4,
             "пять": 5, "шесть": 6, "семь": 7, "восемь": 8, "девять": 9, "десять": 10}
# Формулировки без конкретной даты — только уточнение у человека
VAGUE = [r"(?<!конца )(?<!конце )следующ\w* недел",r"ближайш\w* врем", r"как можно скорее", r"\basap\b", r"срочно",
         r"оперативно", r"по готовности", r"\bпосле\b", r"следующ\w* (планерк|совещан|встреч|созвон)",
         r"в начале", r"в середине", r"до нового года", r"в течение месяца", r"на днях"]
# Здесь в тексте нет опоры для даты вообще — подсказку AI не показываем
NO_HINT = [r"срочно", r"как можно скорее", r"\basap\b", r"оперативно", r"ближайш\w* врем", r"на днях",
           r"по готовности", r"\bпосле\b"]


def fmt(d: date) -> str:
    return f"{d:%d.%m.%Y} ({WD_SHORT[d.weekday()]})"


def _num(token: str | None) -> int:
    if not token:
        return 1
    return int(token) if token.isdigit() else NUM_WORDS.get(token, 1)


def _add_workdays(start: date, n: int) -> date:
    d = start
    while n > 0:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
    return d


def _safe_date(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def _infer_year(m: int, d: int, meeting: date) -> date | None:
    cand = _safe_date(meeting.year, m, d)
    if cand and cand < meeting - timedelta(days=60):
        cand = _safe_date(meeting.year + 1, m, d)
    return cand


def _absolute(text: str, meeting: date | None) -> tuple[list[date], list[str]]:
    """Явные даты. Без года и без даты совещания год не угадываем."""
    found, problems = [], []
    spans = []
    for m in re.finditer(r"(?<![\d.])(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?(?![\d])", text):
        spans.append(m.span())
        d, mo, y = int(m.group(1)), int(m.group(2)), m.group(3)
        if y:
            yy = int(y) + (2000 if len(y) == 2 else 0)
            val = _safe_date(yy, mo, d)
        elif meeting:
            val = _infer_year(mo, d, meeting)
        else:
            problems.append(f"в дате «{m.group(0)}» нет года, а дата совещания неизвестна")
            continue
        if val:
            found.append(val)
    for m in re.finditer(rf"(?<!\d)(\d{{1,2}})\s+{MONTH_RE}(?:\s+(\d{{4}}))?", text):
        spans.append(m.span())
        d = int(m.group(1))
        mo = next(v for k, v in MONTHS.items() if m.group(2).startswith(k))
        if m.group(3):
            val = _safe_date(int(m.group(3)), mo, d)
        elif meeting:
            val = _infer_year(mo, d, meeting)
        else:
            problems.append(f"в дате «{m.group(0)}» нет года, а дата совещания неизвестна")
            continue
        if val:
            found.append(val)
    return found, problems


def _day_only(text: str, meeting: date | None) -> list[date]:
    """«до 13-го», «к 20 числу» — месяц не назван, берём ближайший."""
    res = []
    for m in re.finditer(r"(?<![\d./])(\d{1,2})\s*-?\s*(?:го|ого|е|числа|числу)(?![а-я])", text):
        if not meeting:
            continue
        day = int(m.group(1))
        y, mo = meeting.year, meeting.month
        for _ in range(2):
            cand = _safe_date(y, mo, day)
            if cand and cand >= meeting:
                res.append(cand)
                break
            mo, y = (1, y + 1) if mo == 12 else (mo + 1, y)
    return res


def _relative(text: str, meeting: date) -> tuple[date | None, str]:
    """Относительный срок → (дата, пояснение) или (None, '')."""
    if re.search(r"послезавтра", text):
        return meeting + timedelta(days=2), "послезавтра от даты совещания"
    if re.search(r"(?<!после)завтра", text):
        return meeting + timedelta(days=1), "завтра от даты совещания"
    if re.search(r"\bсегодня\b", text):
        return meeting, "в день совещания"

    m = re.search(r"через\s+(\d+|\w+)?\s*(дн\w*|день|недел\w*|месяц\w*)", text)
    if m:
        n_tok, unit = m.group(1), m.group(2)
        n = _num(n_tok if n_tok and not n_tok.startswith(("недел", "месяц", "дн", "день")) else None)
        if unit.startswith("недел"):
            return meeting + timedelta(weeks=n), f"через {n} нед. от даты совещания"
        if unit.startswith("месяц"):
            mo = meeting.month - 1 + n
            y, mo = meeting.year + mo // 12, mo % 12 + 1
            d = min(meeting.day, calendar.monthrange(y, mo)[1])
            return date(y, mo, d), f"через {n} мес. от даты совещания"
        return meeting + timedelta(days=n), f"через {n} дн. от даты совещания"

    m = re.search(r"в течени[еи]\s+(\d+|\w+)?\s*(рабоч\w+\s+)?(дн\w*|день|недел\w*)", text)
    if m:
        n_tok = m.group(1)
        n = _num(n_tok if n_tok and not n_tok.startswith(("рабоч", "недел", "дн", "день")) else None)
        if m.group(3).startswith("недел"):
            return meeting + timedelta(weeks=n), f"в течение {n} нед. от даты совещания"
        if m.group(2):
            return _add_workdays(meeting, n), f"{n} рабочих дн. от даты совещания"
        return meeting + timedelta(days=n), f"{n} дн. от даты совещания"

    if re.search(r"конц\w*\s+следующ\w*\s+недел", text):
        friday = meeting + timedelta(days=(4 - meeting.weekday()) + 7)
        return friday, "пятница следующей недели"
    if re.search(r"конц\w*\s+(эт\w+\s+|текущ\w+\s+)?недел|на этой неделе", text):
        if meeting.weekday() > 4:
            return None, ""
        return meeting + timedelta(days=4 - meeting.weekday()), "конец рабочей недели — пятница"
    if re.search(r"конц\w*\s+(эт\w+\s+|текущ\w+\s+)?месяц", text):
        last = calendar.monthrange(meeting.year, meeting.month)[1]
        return date(meeting.year, meeting.month, last), "последний день месяца совещания"

    for wd, pattern in WD_RE.items():
        m = re.search(rf"(следующ\w+\s+)?{pattern}", text)
        if m:
            delta = (wd - meeting.weekday()) % 7 or 7
            if m.group(1):
                start_next_week = meeting + timedelta(days=7 - meeting.weekday())
                return start_next_week + timedelta(days=wd), f"{WD[wd]} следующей недели"
            return meeting + timedelta(days=delta), f"ближайший {WD[wd]} после даты совещания"
    return None, ""


def _has_relative(text: str) -> bool:
    return bool(re.search(r"завтра|сегодня|через|в течени|конц\w*\s+\w*\s*(недел|месяц)|на этой неделе", text)
                or any(re.search(p, text) for p in WD_RE.values()))


def parse_deadline(raw: str | None, meeting_iso: str | None, llm_iso: str | None = None) -> dict:
    """Срок из формулировки: статус, дата (только если однозначна), пояснение, подсказка AI."""
    meeting = date.fromisoformat(meeting_iso) if meeting_iso else None
    # Без даты совещания подсказке AI не от чего отсчитывать — она была бы «датой запуска»
    suggestion = llm_iso if meeting and llm_iso and _valid_iso(llm_iso) else None
    out = {"mention": raw, "value": None, "suggestion": None, "status": "missing", "note": "", "rule": None}
    if not raw or not raw.strip():
        out["note"] = "срок в протоколе не указан"
        return out

    text = norm(raw)
    absolute, problems = _absolute(text, meeting)
    days = _day_only(text, meeting)
    found = sorted(set(absolute + days))

    if len(found) > 1:
        listed = " и ".join(fmt(d) for d in found)
        out.update(status="conflict", note=f"в формулировке несколько сроков: {listed} — выберите один")
        return out
    if problems:
        out.update(status="needs_clarification", note=problems[0], suggestion=suggestion)
        return out
    if absolute:
        out.update(status="fact", value=absolute[0].isoformat(), rule="absolute",
                   note=f"дата указана в протоколе: {fmt(absolute[0])}")
        return _post_checks(out, meeting, suggestion)
    if days:
        out.update(status="needs_clarification", suggestion=days[0].isoformat(), rule="day_only",
                   note=f"месяц не назван — вероятно {fmt(days[0])}, подтвердите")
        return out

    for pattern in VAGUE:
        if re.search(pattern, text):
            hint = None if any(re.search(p, text) for p in NO_HINT) else suggestion
            out.update(status="needs_clarification", suggestion=hint,
                       note=f"«{raw}» — не конкретная дата, уточните срок")
            return out

    if _has_relative(text):
        if not meeting:
            out.update(status="needs_clarification", suggestion=None,
                       note=f"относительный срок «{raw}», а дата совещания не указана — укажите дату")
            return out
        value, why = _relative(text, meeting)
        if value:
            out.update(status="derived", value=value.isoformat(), rule="relative",
                       note=f"«{raw}» → {fmt(value)}: {why}")
            return _post_checks(out, meeting, suggestion)

    out.update(status="needs_clarification", suggestion=suggestion,
               note=f"правила не смогли однозначно вычислить срок «{raw}» — уточните")
    return out


def _post_checks(out: dict, meeting: date | None, suggestion: str | None) -> dict:
    value = date.fromisoformat(out["value"])
    if meeting and value < meeting:
        out.update(status="conflict", value=None, suggestion=None,
                   note=f"срок {fmt(value)} раньше даты совещания {fmt(meeting)}")
        return out
    if suggestion and suggestion != out["value"]:
        out["ai_mismatch"] = suggestion
    return out


def _valid_iso(s: str) -> bool:
    try:
        date.fromisoformat(s)
        return True
    except (TypeError, ValueError):
        return False


def is_weekend(iso: str | None) -> bool:
    return bool(iso) and date.fromisoformat(iso).weekday() >= 5
