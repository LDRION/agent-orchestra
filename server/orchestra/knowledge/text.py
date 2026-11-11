"""Текст → основы слов (русский и английский стемминг) и нарезка документа на фрагменты."""
import re

import snowballstemmer

_ru = snowballstemmer.stemmer("russian")
_en = snowballstemmer.stemmer("english")
WORD = re.compile(r"[а-яёa-z0-9]+(?:[.\-][а-яёa-z0-9]+)*", re.IGNORECASE)
STOP = set("""и в во не что он на я с со как а то все она так его но да ты к у же вы за бы по только ее мне было вот от
меня еще нет о из ему теперь когда даже ну вдруг ли если уже или ни быть был него до вас нибудь опять уж вам ведь там
потом себя ничего ей может они тут где есть надо ней для мы тебя их чем была сам чтоб без будто чего раз тоже себе под
будет ж тогда кто этот того потому этого какой совсем ним здесь этом один почти мой тем чтобы нее сейчас были куда
зачем всех никогда можно при наконец два об другой хоть после над больше тот через эти нас про всего них какая много
разве три эту моя впрочем хорошо свою этой перед иногда лучше чуть том нельзя такой им более всегда конечно всю между
the a an of to in and or for is are be on with as by at this that it from""".split())
CHUNK = 900


def terms(text: str) -> list[str]:
    """Основы значимых слов. Номера нормативов («21.101», «328.1325800») сохраняются целиком.

    Snowball режет «надпись» до «надп» (принимает «-сь» за возвратный суффикс), а «надписи» —
    до «надпис». Поэтому у длинных русских слов есть второй ключ — начало слова «~надпи»:
    формы одного слова совпадают по нему при любом поведении стеммера."""
    out = []
    for m in WORD.finditer(text.lower().replace("ё", "е")):
        w = m.group(0)
        if w in STOP or (len(w) < 2 and not w.isdigit()):
            continue
        if any(ch.isdigit() for ch in w):
            out.append(w)
        elif re.match(r"[a-z]", w):
            out.append(_en.stemWord(w))
        else:
            out.append(_ru.stemWord(w))
            if len(w) >= 6:
                out.append("~" + w[:5])
    return out


def chunks(text: str, size: int = CHUNK) -> list[str]:
    """Абзацы склеиваются до ~size символов; слишком длинный абзац режется по предложениям."""
    paras = [p.strip() for p in re.split(r"\n\s*\n|\r\n\s*\r\n", text) if p.strip()]
    pieces: list[str] = []
    for p in paras:
        if len(p) <= size * 1.5:
            pieces.append(p)
            continue
        buf = ""
        for sent in re.split(r"(?<=[.!?;])\s+", p):
            if buf and len(buf) + len(sent) > size:
                pieces.append(buf)
                buf = ""
            buf = f"{buf} {sent}".strip()
        if buf:
            pieces.append(buf)
    out, buf = [], ""
    for p in pieces:
        if buf and len(buf) + len(p) > size:
            out.append(buf)
            buf = ""
        buf = f"{buf}\n\n{p}".strip()
    if buf:
        out.append(buf)
    return out
