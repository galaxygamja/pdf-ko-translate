"""원문과 번역문을 문단 단위로 대조하는 기계적 검사.

번역에서 빠지면 안 되는 것(숫자, 그림·표 참조, 인용 번호)이 남아 있는지 본다.
"""

from __future__ import annotations

import re
from collections import Counter

NUM_RE = re.compile(r"\d+(?:[.,]\d+)*")
CITE_RE = re.compile(r"\[\s*\d[\d,–\-\s]*\]")
REF_RE = re.compile(r"(Supplementary\s+)?(Figs?\.?|Figures?|Tables?)\s*(\d+)", re.I)
EMPTY_PAREN_RE = re.compile(r"\(\s*[a-z]?\s*\)")
TAG_RE = re.compile(r"</?(?:i|b|sup|sub|a)>")


def _plain(s: str) -> str:
    return TAG_RE.sub("", s).replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")


def _refs(text: str, korean: bool) -> Counter:
    out: Counter = Counter()
    for m in REF_RE.finditer(text):
        kind = "T" if m.group(2).lower().startswith("tab") else "F"
        out[(bool(m.group(1)), kind, m.group(3))] += 1
    if korean:
        for m in re.finditer(r"(보충\s*|보조\s*)?(그림|표)\s*(\d+)", text):
            out[(bool(m.group(1)), "T" if m.group(2) == "표" else "F", m.group(3))] += 1
    return out


def _term_in(term: str, text: str) -> bool:
    return re.search(r"(?<![A-Za-z])" + re.escape(term) + r"(?![A-Za-z])", text, re.I) is not None


def glossary_issues(src: str, ko: str, glossary: list[dict]) -> list[str]:
    """용어집을 지켰는지: 원문에 용어가 있으면 번역문에 정해진 한국어(또는 영어 유지)가 있어야 한다."""
    issues = []
    ko_flat = re.sub(r"\s+", "", ko)
    for t in glossary:
        forms = [t["en"], *t.get("variants", [])]
        used = [f for f in forms if _term_in(f, src)]
        if not used:
            continue
        if t["action"] == "translate" and t.get("ko"):
            # 약어(DGC 등)는 약어 그대로 써도 된다
            words = [f for f in used if not (re.fullmatch(r"[A-Z0-9-]{2,7}s?", f) and _term_in(f, ko))]
            if not words:
                continue
            # 용어집 한국어의 핵심어(마지막 단어)가 있으면 통과: '바이오필름 분산' -> '분산'
            core = re.sub(r"\s+", "", t["ko"].split()[-1])
            if re.sub(r"\s+", "", t["ko"]) not in ko_flat and core not in ko_flat:
                issues.append(f"glossary: translate '{words[0]}' as '{t['ko']}'")
        elif t["action"] == "keep" and not any(_term_in(f, ko) for f in used):
            issues.append(f"glossary: keep '{used[0]}' in English")
    return issues


def check(src: str, ko: str, glossary: list[dict] | None = None) -> list[str]:
    """빠진 항목 목록 (영어로 짧게; 재번역 프롬프트에 그대로 넣는다)."""
    src, ko = _plain(src), _plain(ko)
    issues = glossary_issues(src, ko, glossary) if glossary else []
    ko_nums = Counter(NUM_RE.findall(ko))
    missing = [n for n, c in Counter(NUM_RE.findall(src)).items() if ko_nums[n] < c]
    if missing:
        issues.append("missing numbers: " + ", ".join(missing[:10]))
    ko_cites = {re.sub(r"\s", "", c) for c in CITE_RE.findall(ko)}
    miss_cite = [c for c in CITE_RE.findall(src) if re.sub(r"\s", "", c) not in ko_cites]
    if miss_cite:
        issues.append("missing citations: " + ", ".join(miss_cite[:10]))
    src_refs, ko_refs = _refs(src, False), _refs(ko, True)
    miss_ref = [f"{'Supplementary ' if s else ''}{'Table' if k == 'T' else 'Fig.'} {n}" for (s, k, n), c in src_refs.items() if ko_refs[(s, k, n)] < c]
    if miss_ref:
        issues.append("missing figure/table references: " + ", ".join(miss_ref))
    if len(EMPTY_PAREN_RE.findall(ko)) > len(EMPTY_PAREN_RE.findall(src)):
        issues.append("empty parentheses (a reference was dropped)")
    return issues
