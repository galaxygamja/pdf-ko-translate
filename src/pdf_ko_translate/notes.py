"""역주(원문 오류 표시) 위치 찾기, 같은 문제 묶기, 본문 순서대로 번호 매기기.

역주 한 건은 {"n", "page", "quote", "note", "group"}이다. 같은 문제가 여러 곳에 있으면
group이 같은 여러 건이 되고, 모두 같은 번호를 받는다 (역자 주 쪽에는 한 번만 실린다).
"""

from __future__ import annotations

import re
import unicodedata

import pymupdf

from .extract import DocModel, Paragraph


def norm(s: str) -> str:
    """대조용 정규화: µ(마이크로 기호)/μ, 유니코드 빼기 기호, 따옴표, 공백 차이를 없앤다."""
    s = unicodedata.normalize("NFKC", s)
    s = s.replace("−", "-").replace("–", "-").replace("—", "-")
    s = re.sub(r"[‘’′]", "'", s)
    s = re.sub(r"[“”″]", '"', s)
    s = s.replace("­", "")
    return re.sub(r"\s+", " ", s).strip().lower()


def _key(quote: str) -> str:
    return norm(quote)[:60]


def paragraphs_with(paragraphs: list[Paragraph], quote: str) -> list[str]:
    """인용문이 들어 있는 모든 문단 id (본문과 캡션에 같은 문장이 있으면 둘 다)."""
    k = _key(quote)
    if not k:
        return []
    return [p.id for p in paragraphs if k in norm(p.plain)]


def search_page(doc: pymupdf.Document, pno: int, quote: str) -> pymupdf.Rect | None:
    """번역하지 않는 곳(표 칸 등)에서 인용문 위치를 찾는다. 원문을 지우기 전에 불러야 한다."""
    q = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", quote)).strip()
    for cand in (q[:60], q[:25], quote[:60], quote[:25]):
        hits = doc[pno].search_for(cand)
        if hits:
            return hits[0]
    return None


def organize(notes: list[dict], doc: pymupdf.Document, model: DocModel) -> list[dict]:
    """본문에 나오는 순서대로 정렬하고, 같은 group은 같은 번호로 묶는다."""
    order = {p.id: i for i, p in enumerate(model.paragraphs)}

    def position(n: dict) -> tuple:
        pids = paragraphs_with(model.paragraphs, n["quote"])
        if pids:
            p = model.paragraphs[order[pids[0]]]
            return (p.segments[0].page, 0, order[pids[0]])
        pno = n.get("page", 1) - 1
        if pno in model.pages:
            r = search_page(doc, pno, n["quote"])
            if r:
                return (pno, 1, r.y0)
        return (pno, 2, 0)

    ordered = sorted(notes, key=position)
    numbers: dict[str, int] = {}
    out = []
    for n in ordered:
        g = n.get("group") or n["quote"]
        if g not in numbers:
            numbers[g] = len(numbers) + 1
        out.append({**n, "n": numbers[g]})
    return out
