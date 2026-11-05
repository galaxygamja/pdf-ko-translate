"""원문 텍스트를 지우고 같은 자리에 한국어를 조판한다.

문단마다 원문 줄 위치(Segment)를 '줄 슬롯'으로 바꾼 뒤 한국어를 채운다.
들어가지 않으면 글자 크기를 조금 줄여 보고(push 모드는 최대 6%),
그래도 넘치면 아래 내용을 밀어 내린다(reflow.py). shrink 모드는 밀지 않고 더 줄인다.
"""

from __future__ import annotations

import html
import io
import os
import re
from dataclasses import dataclass, field

import pymupdf

from .extract import DocModel, Paragraph
from .fonts import FontBook, fontbook
from .reflow import Item, PagePlan, plan_page

KO_SCALE = 0.95  # 한글은 같은 pt에서도 커 보여서 조금 줄인다
SCALES = {
    "push": [1.0, 0.97, 0.94],
    "shrink": [1.0, 0.97, 0.94, 0.91, 0.88, 0.85, 0.82, 0.79, 0.76, 0.73, 0.70],
}
SUP_SIZE = 0.7
TAG_SPLIT = re.compile(r"(</?(?:i|b|sup|sub|a)>)")


@dataclass
class Glyph:
    ch: str
    font: pymupdf.Font
    adv: float  # fontsize 1 기준 폭
    sf: float  # 크기 배율 (위첨자 등)
    dy: float  # 베이스라인 이동 (fontsize 배율)
    link: bool


@dataclass
class Word:
    glyphs: list[Glyph]
    space_before: bool = True

    @property
    def width(self) -> float:
        return sum(g.adv for g in self.glyphs)


@dataclass
class Slot:
    page: int
    x0: float
    x1: float
    baseline: float
    justify: bool
    seg: int = 0


@dataclass
class RenderStats:
    rendered: int = 0
    shrunk: dict[str, float] = field(default_factory=dict)
    overflow: list[str] = field(default_factory=list)  # 밀어 내린(push) 또는 넘친(shrink) 문단
    extended_pages: dict[int, float] = field(default_factory=dict)  # 쪽 번호(0부터) -> 늘어난 높이


def _parse(markup: str, book: FontBook, para_bold: bool) -> list[Word]:
    """태그 문자열 -> 단어 목록. 공백이 단어 경계이며, 태그 경계는 단어를 끊지 않는다."""
    depth = {"i": 0, "b": 0, "sup": 0, "sub": 0, "a": 0}
    words: list[Word] = []
    cur: list[Glyph] = []
    cur_space = False
    saw_space = False
    for tok in TAG_SPLIT.split(markup):
        if not tok:
            continue
        m = re.fullmatch(r"<(/?)(i|b|sup|sub|a)>", tok)
        if m:
            depth[m.group(2)] = max(0, depth[m.group(2)] + (-1 if m.group(1) else 1))
            continue
        bold = para_bold or depth["b"] > 0
        italic = depth["i"] > 0
        sf, dy = 1.0, 0.0
        if depth["sup"]:
            sf, dy = SUP_SIZE, -0.33
        elif depth["sub"]:
            sf, dy = SUP_SIZE, 0.15
        for ch in html.unescape(tok):
            if ch.isspace() and ch != "\u00a0":
                if cur:
                    words.append(Word(cur, cur_space))
                    cur = []
                saw_space = True
                continue
            if not cur:
                cur_space, saw_space = saw_space, False
            f = book.pick(ch, bold, italic)
            cur.append(Glyph(ch, f, f.text_length(ch, 1) * sf, sf, dy, depth["a"] > 0))
    if cur:
        words.append(Word(cur, cur_space))
    return words


def _slots(p: Paragraph, scale: float) -> list[Slot]:
    slots: list[Slot] = []
    pitch = p.pitch * scale
    for si, seg in enumerate(p.segments):
        continues = si < len(p.segments) - 1  # 다음 단으로 이어지는 조각은 끝까지 채운다
        b0 = seg.lines[0].baseline
        blast = seg.lines[-1].baseline
        asc = b0 - seg.lines[0].y0
        first = b0 - asc * (1 - scale)
        n = int((blast - first) / pitch + 0.3) + 1
        x1 = seg.x1_max if len(seg.lines) == 1 else seg.x1
        for k in range(n):
            slots.append(Slot(seg.page, seg.x0, x1, first + k * pitch, seg.justify or continues, si))
    return slots


def _break(words: list[Word], fs: float, widths: list[float], indent: float, space: float) -> list[list[Word]]:
    """탐욕적 줄 나눔. widths가 모자라면 마지막 폭을 계속 쓴다."""
    lines: list[list[Word]] = []
    cur: list[Word] = []
    used = 0.0
    queue = list(words)

    def avail() -> float:
        i = len(lines)
        w = widths[i] if i < len(widths) else widths[-1]
        return w - (indent if i == 0 else 0)

    while queue:
        w = queue.pop(0)
        ww = w.width * fs
        gap = space * fs if cur and w.space_before else 0.0
        if used + gap + ww <= avail() + 0.01:
            cur.append(w)
            used += gap + ww
            continue
        if cur and ww <= avail():
            room = avail() - used - gap
            cut = _hangul_cut(w, room, fs) if room > avail() * 0.12 else 0
            if cut:
                # 줄 끝 빈칸이 크면 한글 음절 사이에서 끊는다 (한국어 조판 관례)
                cur.append(Word(w.glyphs[:cut], w.space_before))
                lines.append(cur)
                cur, used = [], 0.0
                queue.insert(0, Word(w.glyphs[cut:], False))
                continue
            lines.append(cur)
            cur, used = [w], ww
            continue
        # 한 줄보다 긴 단어: 글자 단위로 쪼갠다
        room = avail() - used - gap
        take, acc = 0, 0.0
        for g in w.glyphs:
            if acc + g.adv * fs > room:
                break
            acc += g.adv * fs
            take += 1
        if take == 0:
            if cur:
                lines.append(cur)
                cur, used = [], 0.0
                queue.insert(0, w)
                continue
            take = 1
        cur.append(Word(w.glyphs[:take], w.space_before))
        lines.append(cur)
        cur, used = [], 0.0
        if take < len(w.glyphs):
            queue.insert(0, Word(w.glyphs[take:], False))
    if cur:
        lines.append(cur)
    return lines


def _is_hangul(ch: str) -> bool:
    return "\uac00" <= ch <= "\ud7a3"


def _hangul_cut(w: Word, room: float, fs: float) -> int:
    """room 안에 들어가는 가장 긴 앞부분의 길이. 한글 음절 경계에서만 자른다 (없으면 0)."""
    best, acc = 0, 0.0
    g = w.glyphs
    for i in range(1, len(g)):
        acc += g[i - 1].adv * fs
        if acc > room:
            break
        if _is_hangul(g[i - 1].ch) and (_is_hangul(g[i].ch) or g[i].ch == "("):
            best = i
    # 한 글자만 남기거나 넘기는 분리는 피한다
    if best < 2 or len(g) - best < 2:
        return 0
    return best


def _rgb(c: int) -> tuple[float, float, float]:
    return ((c >> 16) & 255) / 255, ((c >> 8) & 255) / 255, (c & 255) / 255


class _Writers:
    """페이지마다 그리는 순서를 지킨다. 색이 바뀔 때만 새 TextWriter를 만든다.
    (색별로 모아 그리면 PDF 텍스트 레이어의 읽기 순서가 깨진다)"""

    def __init__(self, doc: pymupdf.Document):
        self.doc = doc
        self.tw: dict[int, list[tuple[int, pymupdf.TextWriter]]] = {}

    def add(self, page: int, color: int, pos, text: str, font, size: float):
        lst = self.tw.setdefault(page, [])
        if not lst or lst[-1][0] != color:
            lst.append((color, pymupdf.TextWriter(self.doc[page].rect)))
        lst[-1][1].append(pos, text, font=font, fontsize=size)

    def flush(self):
        for page, lst in self.tw.items():
            for color, tw in lst:
                tw.write_text(self.doc[page], color=_rgb(color))


@dataclass
class Placed:
    p: Paragraph
    fs: float
    scale: float
    lines: list[list[Word]]
    slots: list[Slot]
    indent: float
    space: float
    growth: float  # 마지막 Segment 아래로 더 필요한 높이


def _layout(p: Paragraph, ko: str, mode: str) -> Placed | None:
    book = fontbook(p.serif)
    words = _parse(ko, book, p.bold)
    if not words:
        return None
    space = book.latin[(False, False)].text_length(" ", 1)
    first = p.segments[0]
    indent = max(0.0, first.lines[0].x0 - first.x0) if len(first.lines) > 1 else 0.0
    chosen = None
    for s in SCALES[mode]:
        fs = p.size * KO_SCALE * s
        slots = _slots(p, s)
        lines = _break(words, fs, [sl.x1 - sl.x0 for sl in slots], indent, space)
        chosen = (s, fs, slots, lines)
        if len(lines) <= len(slots):
            break
    s, fs, slots, lines = chosen
    extra = max(0, len(lines) - len(slots))
    last = slots[-1]
    for k in range(extra):
        slots.append(Slot(last.page, last.x0, last.x1, last.baseline + (k + 1) * p.pitch * s, last.justify, last.seg))
    return Placed(p, fs, s, lines, slots, indent, space, extra * p.pitch * s)


def _draw(pl: Placed, w: _Writers, shift: dict[int, float]) -> None:
    """shift: Segment 번호 -> 재배치로 내려간 거리"""
    p, fs, space = pl.p, pl.fs, pl.space
    space_font = fontbook(p.serif).latin[(False, False)]
    for i, (line, slot) in enumerate(zip(pl.lines, pl.slots)):
        page, base = slot.page, slot.baseline + shift.get(slot.seg, 0.0)
        x = slot.x0 + (pl.indent if i == 0 else 0.0)
        natural = sum(wd.width * fs for wd in line) + sum(space * fs for j, wd in enumerate(line) if j and wd.space_before)
        gaps = sum(1 for j, wd in enumerate(line) if j and wd.space_before)
        extra = 0.0
        width = slot.x1 - x
        if slot.justify and i != len(pl.lines) - 1 and gaps and 0 < width - natural < width * 0.25:
            extra = (width - natural) / gaps
        for j, wd in enumerate(line):
            if j and wd.space_before:
                # 공백도 실제 글자로 넣어야 복사·검색할 때 띄어쓰기가 남는다
                w.add(page, p.color, (x, base), " ", space_font, fs)
                x += space * fs + extra
            run: list[Glyph] = []
            for g in wd.glyphs + [None]:
                if run and (g is None or g.font is not run[0].font or g.sf != run[0].sf or g.dy != run[0].dy or g.link != run[0].link):
                    r0 = run[0]
                    color = p.link_color if (r0.link and p.link_color is not None) else p.color
                    w.add(page, color, (x, base + r0.dy * fs), "".join(r.ch for r in run), r0.font, fs * r0.sf)
                    x += sum(r.adv for r in run) * fs
                    run = []
                if g is not None:
                    run.append(g)


def _erase(doc: pymupdf.Document, paragraphs: list[Paragraph]) -> None:
    pages: set[int] = set()
    for p in paragraphs:
        for seg in p.segments:
            page = doc[seg.page]
            for l in seg.lines:
                h = l.y1 - l.y0
                r = pymupdf.Rect(l.x0 + 0.3, l.y0 + h * 0.25, l.x1 - 0.3, l.y1 - h * 0.25)
                page.add_redact_annot(r, fill=False, cross_out=False)
            pages.add(seg.page)
    for pno in pages:
        doc[pno].apply_redactions(
            images=pymupdf.PDF_REDACT_IMAGE_NONE,
            graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
            text=pymupdf.PDF_REDACT_TEXT_REMOVE,
        )


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s)


def _page_items(doc: pymupdf.Document, pno: int, model: DocModel, placed: list[Placed]) -> list[Item]:
    # 텍스트 블록 대신 문단 단위 상자를 쓴다 (한 블록에 여러 문단이 있으면 재배치가 막힌다)
    items = [Item(pymupdf.Rect(r)) for r in model.pages[pno].figures]
    items += [Item(sk.rect) for sk in model.skipped if sk.page == pno]
    placed_ids = {pl.p.id for pl in placed}
    items += [Item(seg.rect) for p in model.paragraphs if p.id not in placed_ids for seg in p.segments if seg.page == pno]
    page = doc[pno]
    for d in page.get_drawings():
        r = pymupdf.Rect(d["rect"])
        if not r.is_empty or r.width > 1 or r.height > 1:
            items.append(Item(r | r.tl + (0.1, 0.1)))
    for pl in placed:
        segs = pl.p.segments
        for k, seg in enumerate(segs):
            if seg.page == pno:
                items.append(Item(seg.rect, pl.growth if k == len(segs) - 1 else 0.0))
    return items


@dataclass
class Marker:
    """원문 오류 역주 표시. 번역 문단에 붙이거나(pid), 번역하지 않은 자리(page, rect)에 찍는다."""

    n: int
    pid: str | None = None
    page: int | None = None
    rect: pymupdf.Rect | None = None


def locate_notes(doc: pymupdf.Document, model: DocModel, notes: list[dict]) -> list[Marker]:
    """역주의 인용문(원문 영어)을 찾아 표시 위치를 정한다. 원문을 지우기 전에 불러야 한다."""
    from .notes import paragraphs_with, search_page

    markers = []
    for n in notes:
        quote = n.get("quote", "")
        pids = paragraphs_with(model.paragraphs, quote)
        if pids:
            markers += [Marker(n["n"], pid=pid) for pid in pids]
            continue
        pno = n.get("page", 1) - 1
        for i in [pno] + [i for i in model.pages if i != pno]:
            if i in model.pages and (r := search_page(doc, i, quote)):
                markers.append(Marker(n["n"], page=i, rect=r))
                break
    return markers


def render(
    doc: pymupdf.Document,
    model: DocModel,
    translations: dict[str, str],
    mode: str = "push",
    notes: list[dict] | None = None,
) -> tuple[pymupdf.Document, RenderStats]:
    """원문 doc을 지우고(제자리 수정) 한국어를 그린 새 문서를 돌려준다."""
    stats = RenderStats()
    notes = notes or []
    markers = locate_notes(doc, model, notes)
    note_by_pid: dict[str, list[int]] = {}
    for m in markers:
        if m.pid:
            note_by_pid.setdefault(m.pid, []).append(m.n)

    # 번역하지 않고 그대로 돌려준 문단(저자 소속, 저널명 등)은 원본을 건드리지 않는다
    todo = [p for p in model.paragraphs if translations.get(p.id) and (_norm(translations[p.id]) != _norm(p.src) or p.id in note_by_pid)]
    placed: list[Placed] = []
    for p in todo:
        # 번역 단계에서 해당 문장 뒤에 넣은 [역주 N]을 위첨자로. 빠진 것은 문단 끝에 붙인다
        # 번역 단계에서 해당 문장 뒤에 넣은 [역주 N]을 위첨자로. 앞 단어에 붙이고(줄 맨 앞에 오지 않게)
        # 안쪽은 줄바꿈 없는 공백으로 묶는다. 빠진 것은 문단 끝에 붙인다
        ko = re.sub(r"\s*\[역주\s*(\d+)\]", "<sup>[역주\u00a0\\1]</sup>", translations[p.id])
        for n in sorted(set(note_by_pid.get(p.id, []))):
            if f"[역주\u00a0{n}]" not in ko:
                ko += f"<sup>[역주\u00a0{n}]</sup>"
        pl = _layout(p, ko, mode)
        if pl:
            placed.append(pl)
            if pl.scale < 1.0:
                stats.shrunk[p.id] = pl.scale
            if pl.growth > 0:
                stats.overflow.append(p.id)

    plans: dict[int, PagePlan] = {}
    if mode == "push":
        for pno in model.pages:
            page_placed = [pl for pl in placed if any(s.page == pno for s in pl.p.segments)]
            if any(pl.growth > 0 and pl.p.segments[-1].page == pno for pl in page_placed) and doc[pno].rotation == 0:
                plan = plan_page(doc[pno].rect, _page_items(doc, pno, model, page_placed))
                if plan.moved:
                    plans[pno] = plan

    _erase(doc, [pl.p for pl in placed])

    out = pymupdf.open()
    for pno in range(doc.page_count):
        plan = plans.get(pno)
        if not plan:
            out.insert_pdf(doc, from_page=pno, to_page=pno)
            continue
        r = doc[pno].rect
        page = out.new_page(width=r.width, height=r.height + plan.extension)
        for st in plan.strips:
            dest = pymupdf.Rect(st.clip.x0, st.clip.y0 + st.dy, st.clip.x1, st.clip.y1 + st.dy)
            page.show_pdf_page(dest, doc, pno, clip=st.clip)
        if plan.extension > 0.01:
            stats.extended_pages[pno] = plan.extension

    w = _Writers(out)
    for pl in placed:
        shift = {}
        for k, seg in enumerate(pl.p.segments):
            plan = plans.get(seg.page)
            shift[k] = plan.shift_at(seg.x0 + 1, seg.lines[0].y0 + 1) if plan else 0.0
        _draw(pl, w, shift)
        stats.rendered += 1
    w.flush()

    for m in markers:
        if m.pid is None and m.page is not None:
            plan = plans.get(m.page)
            dy = plan.shift_at(m.rect.x1, m.rect.y0) if plan else 0.0
            out[m.page].insert_text((m.rect.x1 + 0.5, m.rect.y0 + 3 + dy), f"[역주 {m.n}]", fontsize=4.5, fontname="korea", color=(0.8, 0, 0))
    if notes:
        _notes_pages(out, notes)
    return out, stats


def _notes_pages(out: pymupdf.Document, notes: list[dict]) -> None:
    """문서 끝에 '역자 주' 쪽을 붙인다."""
    from .fonts import MYUNGJO

    w, h = out[0].rect.width, out[0].rect.height
    items = []
    grouped: dict[int, list[dict]] = {}
    for n in notes:
        grouped.setdefault(n["n"], []).append(n)
    for num in sorted(grouped):
        occ = grouped[num]
        where = "<br>".join(
            f"{o.get('page', '?')}쪽 &mdash; 원문: <i>&ldquo;{html.escape(' '.join(o.get('quote', '').split()))}&rdquo;</i>" for o in occ
        )
        items.append(f"<p><b>[역주 {num}]</b> {where}<br>{html.escape(occ[0].get('note', ''))}</p>")
    body = "<h2>역자 주</h2><p class='lead'>원문에 있는 것으로 보이는 오류나 불일치입니다. 번역은 원문대로 옮겼으며, 확인이 필요한 부분을 여기 적었습니다.</p>" + "".join(items)
    css = "* {font-family: ko; font-size: 9px; line-height: 1.55;} h2 {font-size: 14px;} p {margin: 0 0 8px 0;} .lead {color: #555;}"
    archive = None
    if os.path.exists(MYUNGJO):
        css = f"@font-face {{font-family: ko; src: url({os.path.basename(MYUNGJO)});}}\n" + css
        archive = pymupdf.Archive(os.path.dirname(MYUNGJO))
    story = pymupdf.Story(body, user_css=css, archive=archive)
    buf = io.BytesIO()
    writer = pymupdf.DocumentWriter(buf)
    more = True
    while more:
        dev = writer.begin_page(pymupdf.Rect(0, 0, w, h))
        more, _ = story.place(pymupdf.Rect(56, 56, w - 56, h - 56))
        story.draw(dev)
        writer.end_page()
    writer.close()
    out.insert_pdf(pymupdf.open("pdf", buf.getvalue()))


def draw_debug(doc: pymupdf.Document, model: DocModel) -> None:
    """레이아웃 분석 결과를 색 상자로 표시 (초록: 번역 문단, 빨강: 제외)."""
    for p in model.paragraphs:
        for k, seg in enumerate(p.segments):
            page = doc[seg.page]
            r = seg.rect
            page.draw_rect(r, color=(0, 0.6, 0), width=0.5)
            page.insert_text((r.x0, r.y0 - 0.5), f"{p.id}.{k}", fontsize=4, color=(0, 0.5, 0))
            if seg.x1_max > seg.x1 + 1:
                page.draw_line((seg.x1, r.y1), (seg.x1_max, r.y1), color=(0, 0.6, 0), width=0.3, dashes="[1] 0")
    for s in model.skipped:
        page = doc[s.page]
        page.draw_rect(s.rect, color=(0.85, 0, 0), width=0.4)
        page.insert_text((s.rect.x0, s.rect.y1 + 3), s.reason, fontsize=4, color=(0.85, 0, 0))
