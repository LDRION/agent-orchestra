"""Деление протокола на блоки для параллельного разбора."""
import math
import re

ITEM_RE = re.compile(r"^\s*(\d{1,3})[.)]\s+")


def preamble(text: str, limit: int = 320) -> str:
    """Шапка до первого пункта: название, дата, присутствующие — контекст для каждого блока."""
    lines = text.strip().splitlines()
    head = []
    for line in lines:
        if ITEM_RE.match(line):
            break
        if line.strip():
            head.append(line.strip())
    return " ".join(head)[:limit]


def split(text: str, max_items: int = 6, max_chars: int = 2000) -> list[dict]:
    """Блоки по пунктам протокола; без нумерации — по строкам. Короткий текст — один блок."""
    lines = text.strip().splitlines()
    starts = [(m.group(1), i) for i, line in enumerate(lines) if (m := ITEM_RE.match(line))]

    if len(starts) >= 2:
        units = []
        for k, (num, start) in enumerate(starts):
            end = starts[k + 1][1] if k + 1 < len(starts) else len(lines)
            units.append((num, "\n".join(lines[start:end]).strip()))
    else:
        units = [(None, line.strip()) for line in lines if line.strip()]

    total = sum(len(t) for _, t in units)
    n = max(math.ceil(len(units) / max_items), math.ceil(total / max_chars), 1)
    if len(starts) < 2 and total <= max_chars:
        n = 1
    size = math.ceil(len(units) / n)
    groups = [units[i:i + size] for i in range(0, len(units), size)]

    # Нумерация заново в каждом разделе («РЕШИЛИ: 1… ПОРУЧИТЬ: 1…») — номера неоднозначны, режем фрагментами
    all_nums = [num for num, _ in units if num]
    by_fragment = len(set(all_nums)) != len(all_nums)

    chunks = []
    for idx, group in enumerate(groups, 1):
        nums = [] if by_fragment else [num for num, _ in group if num]
        if nums:
            label = f"п. {nums[0]}–{nums[-1]}" if len(nums) > 1 else f"п. {nums[0]}"
        else:
            label = f"фрагмент {idx}"
        # Последний пункт предыдущего блока — чтобы не терять проект на границе
        prev = groups[idx - 2][-1][1][:240] if idx > 1 else ""
        chunks.append({"id": idx, "label": label, "items": nums, "text": "\n".join(t for _, t in group), "prev": prev})
    return chunks
