"""PDF -> 번역 단위(문단) 추출과 레이아웃 분석.

각 문단은 원문 줄 좌표(Segment)를 기억한다. 렌더러는 그 자리의 원문을 지우고
같은 영역에 한국어를 다시 배치한다. 한 문단이 여러 블록(단 넘김, 쪽 넘김)에 걸쳐
있으면 Segment가 여러 개가 된다.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field

import pymupdf

SENT_END = re.compile(r"[.!?:;][)\]\"”’]*$")
URL_RE = re.compile(r"(https?://|www\.|doi\.org|\S+@\S+\.\w+)", re.I)
REF_HEAD = re.compile(r"^\s*(\d+\.?\s*)?(references|bibliography|literature cited)\s*:?\s*$", re.I)
WORD_RE = re.compile(r"[A-Za-z]{2,}")
TAG_RE = re.compile(r"</?(?:i|b|sup|sub|a)>")

FLAG_SUPERSCRIPT = 1
FLAG_ITALIC = 2
FLAG_SERIF = 4
FLAG_BOLD = 16


@dataclass
class Line:
    x0: float
    y0: float
    x1: float
    y1: float
    baseline: float
    spans: list[dict]

    @property
    def text(self) -> str:
        return "".join(s["text"] for s in self.spans)


@dataclass
class Segment:
    """문단의 일부가 놓인 한 덩어리의 원문 줄들 (한 페이지, 한 단)."""

    page: int
    lines: list[Line]
    x0: float
    x1: float
    justify: bool
    x1_max: float = 0.0  # 오른쪽으로 넓혀도 다른 요소와 겹치지 않는 한계

    @property
    def rect(self) -> pymupdf.Rect:
        return pymupdf.Rect(self.x0, self.lines[0].y0, self.x1, self.lines[-1].y1)


@dataclass
class Paragraph:
    id: str
    segments: list[Segment]
    src: str  # <i>, <b>, <sup>, <sub>, <a> 태그가 들어간 원문
    size: float
    color: int
    link_color: int | None
    serif: bool
    bold: bool
    pitch: float  # 원문 줄 간격(베이스라인 사이)

    @property
    def plain(self) -> str:
        return TAG_RE.sub("", self.src).replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")


@dataclass
class Skipped:
    page: int
    rect: pymupdf.Rect
    reason: str


@dataclass
class PageInfo:
    rect: pymupdf.Rect
    obstacles: list[pymupdf.Rect] = field(default_factory=list)  # 텍스트 블록·그림 영역
    figures: list[pymupdf.Rect] = field(default_factory=list)


@dataclass
class DocModel:
    paragraphs: list[Paragraph]
    skipped: list[Skipped]
    pages: dict[int, PageInfo]


# ---------------------------------------------------------------- span 처리


def _is_bold(span: dict) -> bool:
    name = span["font"].lower()
    return bool(span["flags"] & FLAG_BOLD) or any(k in name for k in ("bold", "black", "heavy", "semibold"))


def _is_italic(span: dict) -> bool:
    name = span["font"].lower()
    return bool(span["flags"] & FLAG_ITALIC) or "italic" in name or "oblique" in name


def _escape(t: str) -> str:
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _dominant(values: list[tuple[object, int]]):
    counts: dict[object, int] = {}
    for v, n in values:
        counts[v] = counts.get(v, 0) + n
    return max(counts, key=counts.get) if counts else None


def _line_markup(line: Line, size: float, color: int, para_bold: bool) -> tuple[str, int | None]:
    """줄 하나를 태그 문자열로. 반환: (markup, 링크 색)"""
    base = max(line.spans, key=lambda s: s["size"])["origin"][1]
    out: list[str] = []
    link_color = None
    prev_style: tuple | None = None
    buf = ""

    def flush():
        nonlocal buf
        if not buf:
            return
        t = _escape(buf)
        sup, sub, ital, bold, link = prev_style
        if sup:
            t = f"<sup>{t}</sup>"
        elif sub:
            t = f"<sub>{t}</sub>"
        if ital:
            t = f"<i>{t}</i>"
        if bold:
            t = f"<b>{t}</b>"
        if link:
            t = f"<a>{t}</a>"
        out.append(t)
        buf = ""

    for s in line.spans:
        text = s["text"]
        if not text:
            continue
        small = s["size"] < size * 0.85
        dy = s["origin"][1] - base
        sup = small and (dy < -0.12 * size or bool(s["flags"] & FLAG_SUPERSCRIPT))
        sub = small and not sup and dy > 0.08 * size
        link = s["color"] != color and text.strip() != ""
        if link and link_color is None:
            link_color = s["color"]
        style = (sup, sub, _is_italic(s), _is_bold(s) and not para_bold, link)
        if text.strip() == "" and prev_style is not None:
            style = prev_style  # 공백만 있는 span은 앞 스타일을 이어 받는다
        if style != prev_style:
            flush()
            prev_style = style
        buf += text
    flush()
    return "".join(out), link_color


def _join_lines(parts: list[str]) -> str:
    text = ""
    for p in parts:
        p = re.sub(r"\s+", " ", p).strip()
        if not p:
            continue
        if not text:
            text = p
            continue
        plain_prev = TAG_RE.sub("", text)
        if plain_prev.endswith("­"):
            text = text[: text.rfind("­")] + text[text.rfind("­") + 1 :]
            text += p
        elif plain_prev.endswith("-") and re.match(r"(<[^>]+>)*[a-z]", p) and not plain_prev.endswith(" -"):
            # 줄 끝 하이픈으로 끊긴 단어: 'trans-' + 'lation' -> 'translation'
            idx = text.rfind("-")
            text = text[:idx] + text[idx + 1 :] + p
        else:
            text += " " + p
    text = text.replace("­", "")
    # 태그 경계의 중복 공백 정리
    text = re.sub(r"\s+", " ", text).strip()
    return text


# ---------------------------------------------------------------- 페이지 분석


def _figure_regions(page: pymupdf.Page, text_blocks: list[dict]) -> list[pymupdf.Rect]:
    regions: list[pymupdf.Rect] = []
    for info in page.get_image_info():
        r = pymupdf.Rect(info["bbox"]) & page.rect
        if r.width * r.height > 1500:
            regions.append(r)
    try:
        clusters = page.cluster_drawings()
    except Exception:
        clusters = []
    for r in clusters:
        r = pymupdf.Rect(r)
        if r.width < 40 or r.height < 40:
            continue
        # 글이 빽빽한 영역(테두리 친 초록 등)은 그림이 아니다
        covered = sum(
            pymupdf.Rect(b["bbox"]).get_area()
            for b in text_blocks
            if pymupdf.Rect(b["bbox"]).intersects(r) and r.contains(_center(b["bbox"]))
        )
        if covered / max(r.get_area(), 1) < 0.3:
            regions.append(r)
    return regions


def _center(bbox) -> pymupdf.Point:
    return pymupdf.Point((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2)


def _block_lines(block: dict) -> tuple[list[Line], bool, bool]:
    """반환: (줄 목록, 회전 텍스트 여부, 표처럼 나란한 줄 여부)"""
    lines: list[Line] = []
    rotated = False
    tabular = False
    for ln in block["lines"]:
        spans = [s for s in ln["spans"] if s["text"]]
        if not spans or not "".join(s["text"] for s in spans).strip():
            continue
        dx, dy = ln["dir"]
        if abs(dx - 1) > 0.01 or abs(dy) > 0.01:
            rotated = True
            continue
        size = max(s["size"] for s in spans)
        base = max(spans, key=lambda s: s["size"])["origin"][1]
        x0, y0, x1, y1 = ln["bbox"]
        if lines:
            prev = lines[-1]
            if abs(prev.baseline - base) < 1.0 and x0 >= prev.x1 - 1:
                if x0 - prev.x1 < 1.5 * size:
                    # 같은 줄인데 PyMuPDF가 나눈 경우 -> 합친다
                    prev.spans.extend(spans)
                    prev.x1 = max(prev.x1, x1)
                    prev.y0 = min(prev.y0, y0)
                    prev.y1 = max(prev.y1, y1)
                    continue
                tabular = True
        lines.append(Line(x0, y0, x1, y1, base, list(spans)))
    return lines, rotated, tabular


def _split_paragraphs(lines: list[Line], size: float) -> list[list[Line]]:
    if not lines:
        return []
    left = min(l.x0 for l in lines)
    right = max(l.x1 for l in lines)
    paras: list[list[Line]] = [[lines[0]]]
    for prev, ln in zip(lines, lines[1:]):
        prev_text = TAG_RE.sub("", prev.text).strip()
        ends = bool(SENT_END.search(prev_text))
        lsize = max(s["size"] for s in ln.spans)
        psize = max(s["size"] for s in prev.spans)
        new = False
        if ln.y0 - prev.y1 > 0.8 * size:
            new = True
        elif abs(lsize - psize) > 1.0:
            new = True
        elif ends and ln.x0 - left > 0.8 * size:
            new = True  # 들여쓰기
        elif prev.x1 < right - 2.5 * size and len(lines) > 2 and (ends or re.match(r"[A-Z]", TAG_RE.sub("", ln.text).strip())):
            new = True  # 앞 줄이 짧게 끝남 (문단 끝 또는 목록)
        elif all(_is_bold(s) for s in prev.spans) != all(_is_bold(s) for s in ln.spans):
            new = True  # 굵은 제목 줄
        if new:
            paras.append([ln])
        else:
            paras[-1].append(ln)
    return paras


@dataclass
class _Cand:
    page: int
    lines: list[Line]
    size: float
    skip: str | None
    src: str = ""
    color: int = 0
    link_color: int | None = None
    serif: bool = True
    bold: bool = False


def extract(doc: pymupdf.Document, pages: list[int]) -> DocModel:
    cands: list[_Cand] = []
    skipped: list[Skipped] = []
    page_infos: dict[int, PageInfo] = {}
    in_refs = False

    for pno in pages:
        page = doc[pno]
        H = page.rect.height
        raw = page.get_text("dict", sort=False)
        blocks = [b for b in raw["blocks"] if b["type"] == 0]
        figures = _figure_regions(page, blocks)
        info = PageInfo(page.rect)
        info.obstacles.extend(figures)
        info.figures = figures
        page_infos[pno] = info

        parsed = []
        for b in blocks:
            lines, rotated, tabular = _block_lines(b)
            if not lines:
                if rotated:
                    skipped.append(Skipped(pno, pymupdf.Rect(b["bbox"]), "rotated"))
                continue
            rect = pymupdf.Rect(b["bbox"])
            info.obstacles.append(rect)
            parsed.append((b, rect, lines, rotated, tabular))

        body_widths = [r.width for _, r, ls, _, _ in parsed if len(ls) >= 3]
        body_w = statistics.median(body_widths) if body_widths else page.rect.width * 0.4

        for b, rect, lines, rotated, tabular in parsed:
            chars = [(round(s["size"], 1), len(s["text"])) for l in lines for s in l.spans]
            size = _dominant(chars) or 8.0
            block_text = " ".join(l.text for l in lines).strip()
            reason = None
            c = _center(rect)
            if any(f.contains(c) or (f + (-6, -6, 6, 6)).contains(rect) for f in figures):
                reason = "figure"
            elif rotated and len(lines) <= 2:
                reason = "rotated"
            elif tabular:
                reason = "table"
            elif rect.y1 < H * 0.065 or rect.y0 > H * 0.945:
                reason = "header/footer"
            elif rect.width < body_w * 0.7 and any(
                o is not rect
                and o.width < body_w * 0.9
                and min(o.y1, rect.y1) - max(o.y0, rect.y0) > 0.5 * min(o.height, rect.height)
                and abs(((o.x0 + o.x1) - (rect.x0 + rect.x1)) / 2) < body_w * 0.75
                and not any(f.contains(_center(o)) for f in figures)
                for _, o, _, _, _ in parsed
            ):
                reason = "table"  # 같은 단 안에서 옆에 다른 블록이 있음 = 표 칸
            if REF_HEAD.match(TAG_RE.sub("", block_text)):
                in_refs = True
                reason = reason or "references-heading"
            elif in_refs and reason is None:
                reason = "references"

            if reason:
                skipped.append(Skipped(pno, rect, reason))
                continue

            for plines in _split_paragraphs(lines, size):
                cands.append(_make_cand(pno, plines))

    # 번역 가치가 없는 조각 걸러내기
    for c in cands:
        plain = TAG_RE.sub("", c.src)
        words = WORD_RE.findall(plain)
        if not (len(words) >= 2 or any(len(w) >= 4 for w in words)):
            c.skip = "too-short"
        elif URL_RE.search(plain) and len(plain) < 120:
            c.skip = "url/email"

    paragraphs = _merge_continuations(cands, skipped)
    _compute_extents(paragraphs, page_infos)
    return DocModel(paragraphs, skipped, page_infos)


def _make_cand(pno: int, lines: list[Line]) -> _Cand:
    spans = [s for l in lines for s in l.spans]
    size = _dominant([(round(s["size"], 1), len(s["text"])) for s in spans]) or 8.0
    color = _dominant([(s["color"], len(s["text"].strip())) for s in spans]) or 0
    serif = _dominant([(bool(s["flags"] & FLAG_SERIF) or "serif" in s["font"].lower() and "sans" not in s["font"].lower(), len(s["text"])) for s in spans])
    bold = all(_is_bold(s) for s in spans if s["text"].strip())
    parts, link = [], None
    for l in lines:
        m, lc = _line_markup(l, size, color, bold)
        parts.append(m)
        link = link or lc
    return _Cand(pno, lines, size, None, _join_lines(parts), color, link, bool(serif), bold)


def _merge_continuations(cands: list[_Cand], skipped: list[Skipped]) -> list[Paragraph]:
    """단/쪽 경계에서 끊긴 문단을 하나로 잇는다 (그 사이의 캡션·표는 건너뜀)."""
    used = [False] * len(cands)
    paragraphs: list[Paragraph] = []
    counter: dict[int, int] = {}
    for i, c in enumerate(cands):
        if used[i]:
            continue
        used[i] = True
        if c.skip:
            skipped.append(Skipped(c.page, _lines_rect(c.lines), c.skip))
            continue
        chain = [c]
        cur = c
        while not SENT_END.search(TAG_RE.sub("", cur.src).strip()):
            at_bottom = _at_column_bottom(cur, cands)
            nxt = None
            # PDF 내부 순서가 읽기 순서와 다를 수 있어 앞쪽 후보도 조금 본다
            for j in [*range(i + 1, min(i + 12, len(cands))), *range(max(0, i - 12), i)]:
                d = cands[j]
                if used[j] or d.skip:
                    continue
                if abs(d.size - cur.size) > 0.3:
                    continue  # 크기가 다른 요소(캡션 등)는 건너뛰고 계속 찾는다
                if d.page == cur.page:
                    l0, ll = d.lines[0], cur.lines[-1]
                    below = l0.baseline > ll.baseline + 1 and l0.x0 < ll.x1 and l0.x1 > ll.x0
                    right = l0.y0 < ll.y0 and l0.x0 >= ll.x1 - 5
                    if not (below or right):
                        continue  # 같은 쪽의 다른 위치(머리말 등)에 있는 요소는 건너뛴다
                starts_lower = re.match(r"(<[^>]+>)*[a-z(]", d.src)
                cur_plain = TAG_RE.sub("", cur.src).strip()
                # 다음 쪽, 또는 오른쪽 단의 위쪽에서 다시 시작하는 경우만 이어 붙인다
                new_column = d.page != cur.page or (d.lines[0].y0 < cur.lines[-1].y0 and d.lines[0].x0 >= cur.lines[-1].x1 - 5)
                if (starts_lower and (not new_column or at_bottom)) or (re.search(r"[a-z,]$", cur_plain) and new_column and at_bottom):
                    nxt = j
                break
            if nxt is None:
                break
            used[nxt] = True
            cur = cands[nxt]
            chain.append(cur)
            i = nxt

        src = chain[0].src
        for d in chain[1:]:
            src = _join_lines([src, d.src])
        n = counter.get(c.page, 0) + 1
        counter[c.page] = n
        groups: list[tuple[int, list[Line]]] = []
        for d in chain:
            if groups and groups[-1][0] == d.page:
                last = groups[-1][1][-1]
                first = d.lines[0]
                if abs(first.x0 - min(l.x0 for l in groups[-1][1])) < 3 and -0.6 * d.size <= first.y0 - last.y1 < d.size:
                    groups[-1][1].extend(d.lines)
                    continue
            groups.append((d.page, list(d.lines)))
        segs = [_make_segment(pg, ls) for pg, ls in groups]
        all_lines = [l for d in chain for l in d.lines]
        paragraphs.append(
            Paragraph(
                id=f"p{c.page + 1}-{n}",
                segments=segs,
                src=src,
                size=c.size,
                color=c.color,
                link_color=next((d.link_color for d in chain if d.link_color is not None), None),
                serif=c.serif,
                bold=c.bold,
                pitch=_pitch(all_lines, c.size),
            )
        )
    return paragraphs


def _at_column_bottom(cur: _Cand, cands: list[_Cand]) -> bool:
    """cur 아래에 같은 단·같은 크기의 본문이 더 없으면 단의 끝으로 본다."""
    last = cur.lines[-1]
    for c in cands:
        if c is cur or c.page != cur.page or c.skip or abs(c.size - cur.size) > 0.3:
            continue
        first = c.lines[0]
        if first.y0 > last.y1 + 1 and first.x0 < last.x1 and first.x1 > last.x0:
            return False
    return True


def _lines_rect(lines: list[Line]) -> pymupdf.Rect:
    return pymupdf.Rect(min(l.x0 for l in lines), min(l.y0 for l in lines), max(l.x1 for l in lines), max(l.y1 for l in lines))


def _pitch(lines: list[Line], size: float) -> float:
    diffs = [b.baseline - a.baseline for a, b in zip(lines, lines[1:]) if 0 < b.baseline - a.baseline < 2.5 * size]
    return statistics.median(diffs) if diffs else size * 1.3


def _make_segment(pno: int, lines: list[Line]) -> Segment:
    x0 = min(l.x0 for l in lines)
    x1 = max(l.x1 for l in lines)
    full = [l for l in lines[:-1] if l.x1 > x1 - 3]
    justify = len(lines) >= 3 and len(full) >= max(1, (len(lines) - 1) * 0.6)
    return Segment(pno, lines, x0, x1, justify)


def _compute_extents(paragraphs: list[Paragraph], pages: dict[int, PageInfo]) -> None:
    """한 줄짜리 제목처럼 원문 폭이 좁은 경우를 위해 오른쪽으로 넓힐 수 있는 한계를 구한다."""
    for p in paragraphs:
        for seg in p.segments:
            info = pages[seg.page]
            W = info.rect.width
            # 단 오른쪽 경계 추정: 왼쪽 단이면 페이지 가운데, 아니면 오른쪽 여백
            if seg.x1 <= W / 2 + 5:
                col_right = max((o.x1 for o in info.obstacles if o.x1 <= W / 2 + 5 and o.height < info.rect.height * 0.9), default=seg.x1)
            else:
                col_right = max((o.x1 for o in info.obstacles), default=seg.x1)
            limit = max(col_right, seg.x1)
            r = seg.rect
            for o in info.obstacles:
                if o.x0 >= seg.x1 - 0.5 and o.y0 < r.y1 - 0.5 and o.y1 > r.y0 + 0.5:
                    limit = min(limit, o.x0 - 3)
            seg.x1_max = max(seg.x1, limit)
