"""번역·검토 프롬프트와 묶음 처리. 모델 호출은 providers.call_model (로그인된 Claude/ChatGPT 계정 사용량)."""

from __future__ import annotations

import hashlib
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

from .extract import Paragraph
from .providers import ClaudeError, ModelError, UsageLimitError, call_model, check_cli, provider  # noqa: F401

TAG_COUNT_RE = re.compile(r"</?(?:i|b|sup|sub|a)>")


# ---------------------------------------------------------------- 용어집

GLOSSARY_SYSTEM = """You build a terminology glossary for translating an English academic paper into Korean.
Decide for each technical term whether Korean researchers in this field normally keep it in English or translate it.

action "keep" (leave in English, exactly as written):
- proper nouns: species/strain/gene/protein/plasmid names, chemicals written as formulas or abbreviations, instruments, software, databases, datasets, models, methods named after people or products
- abbreviations and acronyms (PET, PVC, EPS, CFU, c-di-GMP)
- technical terms that Korean papers in this field usually write in English rather than in Korean

action "translate": terms with an established Korean equivalent commonly used in Korean papers (give it in "ko").
Common chemicals, materials, reagents and lab procedures that have standard Korean names are "translate"
(e.g. alum salts -> 명반염, glucose -> 포도당, liquid nitrogen -> 액체 질소, centrifugation -> 원심분리),
unless they are written as a formula or product name.
For an abbreviation that is defined with its full form, list the full form separately.

One concept = one entry = one Korean term. If the paper refers to the same concept with different English wordings
(e.g. "exopolymeric substances", "exopolymeric matrix", "EPS matrix", "extracellular polymeric substances"),
make a single entry and list the other wordings in "variants" so that all of them get the same Korean term.
Return at most 80 of the most important, repeatedly used terms."""

GLOSSARY_SCHEMA = {
    "type": "object",
    "properties": {
        "terms": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "en": {"type": "string"},
                    "action": {"type": "string", "enum": ["keep", "translate"]},
                    "ko": {"type": "string"},
                    "variants": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["en", "action", "ko", "variants"],
            },
        }
    },
    "required": ["terms"],
}


def build_glossary(paragraphs: list[Paragraph], model: str) -> list[dict]:
    text = "\n\n".join(p.plain for p in paragraphs)[:120_000]
    out = call_model(f"<paper>\n{text}\n</paper>", GLOSSARY_SYSTEM, GLOSSARY_SCHEMA, model)
    terms = []
    seen = set()
    for t in out.get("terms", []):
        en = t.get("en", "").strip()
        if not en or en.lower() in seen:
            continue
        seen.add(en.lower())
        variants = [v.strip() for v in t.get("variants", []) if v.strip() and v.strip().lower() != en.lower()]
        terms.append({"en": en, "action": t.get("action", "keep"), "ko": t.get("ko", "").strip(), "variants": variants})
    return terms


def merge_glossary(auto: list[dict], user: list[dict]) -> list[dict]:
    """사용자 용어집이 자동 용어집보다 우선한다 (같은 영어 표현이면 덮어씀)."""
    user_keys = {u["en"].lower() for u in user}
    user_keys |= {v.lower() for u in user for v in u.get("variants", [])}
    kept = [t for t in auto if t["en"].lower() not in user_keys]
    return [{"variants": [], "ko": "", "action": "keep", **u} for u in user] + kept


def fingerprint(glossary: list[dict], model: str, annotate: bool, notes: list[dict] | None = None) -> str:
    """프롬프트·용어집·모델·역주가 바뀌면 저장된 번역을 다시 쓰지 않도록 하는 식별값."""
    parts = [TRANSLATE_SYSTEM, glossary, model, annotate, notes or []]
    if provider() != "claude":  # Claude는 예전 번역 파일과 같은 값을 유지
        parts.append(provider())
    blob = json.dumps(parts, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(blob.encode()).hexdigest()[:10]


# ---------------------------------------------------------------- 번역

TRANSLATE_SYSTEM = """You translate passages of an English academic paper into natural, fluent Korean for Korean researchers.

Style
- Academic written style (~이다/~한다/~하였다). Faithful to meaning, but natural Korean word order, not word-for-word.
- Keep numbers, units, symbols, equations, citations such as [12] or (Smith et al., 2020), figure/table references (Fig. 1a, Table 2), URLs unchanged.
- Keep species, strain, gene, protein and chemical names in English as written.
- Never drop or move outside the sentence any reference to a figure, table or supplementary item.
  Write them exactly as in the source, including panel letters: "(<a>Fig. 2</a>a)" stays "(<a>Fig. 2</a>a)".
- Color words that describe how something is displayed in a figure (false color, staining, plotted line or symbol colors)
  must read as display colors, e.g. "grey PVC" in a microscopy image -> "회색으로 표시된 PVC", not "회색 PVC".
- Translate faithfully even if the source looks wrong; do not correct it and do not add translator notes (they are added separately).

Terminology
- "keep_english": write these terms in English exactly as given, never transliterate them into Hangul.
- "use_korean": use the given Korean term, always the same one for the same concept (several English wordings may map to one Korean term).
- "annotate_first": for these terms write the Korean followed by the English in parentheses, once, at their first occurrence in this request, e.g. 손실 함수(loss function).
- Do not add English in parentheses anywhere else.
- Other technical terms: if Korean researchers normally write the term in English, keep it in English; otherwise use the standard Korean term.

Markup
- The text contains inline tags <i>, <b>, <sup>, <sub>, <a>. Keep every tag, wrapping the corresponding translated words. Keep tag counts unchanged. Do not add new tags.
- &lt; &gt; &amp; are escaped characters; keep them escaped.

Items that should not be translated
- If an item is only a list of person names, a journal name, a bare list of keywords that are proper nouns, or otherwise makes no sense to translate, return it unchanged.

Return one output per input id, in the same order. "context" is earlier text for reference only; do not translate it.
"fix" lists problems found in a previous attempt for some ids; make sure they are fixed.
"notes" lists translator's notes about errors in the source. In the item with that id, translate the source as written
(do not correct it) and insert the literal marker "[역주 N]" immediately after the Korean phrase that corresponds to the quote."""

TRANSLATE_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"id": {"type": "string"}, "ko": {"type": "string"}},
                "required": ["id", "ko"],
            },
        }
    },
    "required": ["items"],
}


def _term_re(term: str) -> re.Pattern:
    return re.compile(r"(?<![A-Za-z])" + re.escape(term) + r"(?![A-Za-z])", re.I)


def make_batches(paragraphs: list[Paragraph], max_chars: int = 5000, max_items: int = 25) -> list[list[Paragraph]]:
    batches: list[list[Paragraph]] = []
    cur: list[Paragraph] = []
    size = 0
    for p in paragraphs:
        n = len(p.src)
        if cur and (size + n > max_chars or len(cur) >= max_items):
            batches.append(cur)
            cur, size = [], 0
        cur.append(p)
        size += n
    if cur:
        batches.append(cur)
    return batches


def first_occurrences(all_paragraphs: list[Paragraph], glossary: list[dict]) -> dict[str, str]:
    """번역하는 용어별로 처음 등장하는 문단 id."""
    first: dict[str, str] = {}
    for t in glossary:
        if t["action"] != "translate":
            continue
        rxs = [_term_re(f) for f in (t["en"], *t.get("variants", []))]
        for p in all_paragraphs:
            if any(rx.search(p.plain) for rx in rxs):
                first[t["en"]] = p.id
                break
    return first


def note_targets(paragraphs: list[Paragraph], notes: list[dict]) -> dict[str, list[dict]]:
    """역주 인용문이 들어 있는 문단 id -> 역주 목록 (같은 문장이 여러 문단에 있으면 모두)."""
    from .notes import paragraphs_with

    out: dict[str, list[dict]] = {}
    for n in notes:
        for pid in paragraphs_with(paragraphs, n.get("quote", "")):
            if all(m["n"] != n["n"] for m in out.get(pid, [])):
                out.setdefault(pid, []).append(n)
    return out


def _batch_prompt(batch: list[Paragraph], context: str, glossary: list[dict], first: dict[str, str], annotate: bool, fix: dict[str, str] | None = None, notes: dict[str, list[dict]] | None = None) -> str:
    text = " ".join(p.plain for p in batch)
    ids = {p.id for p in batch}
    keep, use, ann = [], {}, []
    for t in glossary:
        forms = [t["en"], *t.get("variants", [])]
        present = [f for f in forms if _term_re(f).search(text)]
        if not present:
            continue
        if t["action"] == "keep":
            keep.extend(present)
        else:
            for f in present:
                use[f] = t["ko"]
            if annotate and first.get(t["en"]) in ids:
                ann.append(present[0])
    payload = {
        "keep_english": keep,
        "use_korean": use,
        "annotate_first": ann,
        "context": context,
        "items": [{"id": p.id, "text": p.src} for p in batch],
    }
    if fix:
        payload["fix"] = fix
    batch_notes = [{"id": p.id, "n": n["n"], "quote": n["quote"]} for p in batch for n in (notes or {}).get(p.id, [])]
    if batch_notes:
        payload["notes"] = batch_notes
    return json.dumps(payload, ensure_ascii=False, indent=1)


def _tags_ok(src: str, ko: str) -> bool:
    return sorted(TAG_COUNT_RE.findall(src)) == sorted(TAG_COUNT_RE.findall(ko))


def translate_batch(batch: list[Paragraph], context: str, glossary: list[dict], first: dict[str, str], model: str, annotate: bool, notes: dict[str, list[dict]] | None = None) -> dict[str, str]:
    from .qa import check

    result = _translate_once(batch, context, glossary, first, model, annotate, None, notes)
    # 숫자·그림 참조·인용이 빠진 문단은 문제를 알려 주고 한 번 더 번역한다
    fix = {p.id: "; ".join(issues) for p in batch if p.id in result and (issues := check(p.plain, result[p.id], glossary))}
    if fix:
        redo = [p for p in batch if p.id in fix]
        again = _translate_once(redo, context, glossary, first, model, annotate, fix, notes)
        for p in redo:
            if p.id in again and len(check(p.plain, again[p.id], glossary)) < len(fix[p.id].split("; ")):
                result[p.id] = again[p.id]
    return result


def _translate_once(batch, context, glossary, first, model, annotate, fix=None, notes=None) -> dict[str, str]:
    prompt = _batch_prompt(batch, context, glossary, first, annotate, fix, notes)
    result: dict[str, str] = {}
    for attempt in range(3):
        try:
            out = call_model(prompt, TRANSLATE_SYSTEM, TRANSLATE_SCHEMA, model)
        except ClaudeError:
            if attempt == 2:
                raise
            continue
        by_id = {p.id: p for p in batch}
        for item in out.get("items", []):
            pid, ko = item.get("id"), item.get("ko", "")
            if pid in by_id and ko.strip():
                if not _tags_ok(by_id[pid].src, ko):
                    ko = _repair_tags(ko)
                result[pid] = ko
        missing = [p for p in batch if p.id not in result]
        if not missing:
            break
        batch = missing
        prompt = _batch_prompt(batch, context, glossary, first, annotate, fix, notes)
    return result


def _repair_tags(ko: str) -> str:
    """짝이 맞지 않는 태그를 정리한다 (렌더러가 깨지지 않도록)."""
    out, stack = [], []
    for tok in re.split(r"(</?(?:i|b|sup|sub|a)>)", ko):
        m = re.fullmatch(r"<(/?)(i|b|sup|sub|a)>", tok)
        if not m:
            out.append(tok)
        elif not m.group(1):
            stack.append(m.group(2))
            out.append(tok)
        elif m.group(2) in stack:
            while stack:
                t = stack.pop()
                out.append(f"</{t}>")
                if t == m.group(2):
                    break
    out.extend(f"</{t}>" for t in reversed(stack))
    return "".join(out)


def translate_all(
    todo: list[Paragraph],
    all_paragraphs: list[Paragraph],
    glossary: list[dict],
    model: str,
    workers: int,
    annotate: bool,
    on_done: Callable[[dict[str, str], int, int], None],
    notes: dict[str, list[dict]] | None = None,
    fix: dict[str, str] | None = None,
) -> None:
    """todo를 묶음으로 나눠 병렬 번역하고, 묶음이 끝날 때마다 on_done(결과, 완료 수, 전체 수)를 부른다."""
    first = first_occurrences(all_paragraphs, glossary)
    order = {p.id: i for i, p in enumerate(all_paragraphs)}
    batches = make_batches(todo)
    total = len(batches)
    done = 0
    limit_error: UsageLimitError | None = None
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {}
        for b in batches:
            i = order[b[0].id]
            context = all_paragraphs[i - 1].plain[-600:] if i > 0 else ""
            if fix:
                fn = lambda b=b, c=context: _translate_once(b, c, glossary, first, model, annotate, {p.id: fix[p.id] for p in b if p.id in fix}, notes)
            else:
                fn = lambda b=b, c=context: translate_batch(b, c, glossary, first, model, annotate, notes)
            futures[ex.submit(fn)] = b
        for fut in as_completed(futures):
            try:
                res = fut.result()
            except UsageLimitError as e:
                limit_error = limit_error or e
                for f in futures:
                    f.cancel()
                continue
            done += 1
            on_done(res, done, total)
    if limit_error:
        raise limit_error


# ---------------------------------------------------------------- 번역 대조 검증

VERIFY_SYSTEM = """You check Korean translations of an English academic paper against the source, sentence by sentence.
For each item report problems only when the Korean:
- omits or adds content (a clause, a condition, a number, a unit, a reference, a citation)
- changes meaning (wrong subject/object, negation, comparison direction, quantity, unit, which figure panel)
- leaves English untranslated where a Korean term would be normal (proper nouns, abbreviations, species, gene and
  chemical formula names, and terms listed in keep_english are fine in English)
Ignore style, word order, and markup tags. Translator markers like [역주 1] are intended.
Return ok=true with empty problems when the translation is faithful. Otherwise ok=false and list the problems briefly in English,
quoting the source words concerned."""

VERIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"id": {"type": "string"}, "ok": {"type": "boolean"}, "problems": {"type": "string"}},
                "required": ["id", "ok", "problems"],
            },
        }
    },
    "required": ["items"],
}


def verify_all(
    pairs: list[tuple[Paragraph, str]],
    glossary: list[dict],
    model: str,
    workers: int,
    on_done: Callable[[dict[str, str], int, int], None],
) -> None:
    """원문과 번역을 문장 단위로 대조한다. on_done(문제 있는 id -> 설명, 완료 수, 전체 수)"""
    keep = [t["en"] for t in glossary if t["action"] == "keep"]
    batches: list[list[tuple[Paragraph, str]]] = []
    cur: list[tuple[Paragraph, str]] = []
    size = 0
    for p, ko in pairs:
        if cur and (size + len(p.src) > 6000 or len(cur) >= 20):
            batches.append(cur)
            cur, size = [], 0
        cur.append((p, ko))
        size += len(p.src)
    if cur:
        batches.append(cur)

    def run(batch):
        payload = {"keep_english": keep, "items": [{"id": p.id, "source": p.plain, "korean": ko} for p, ko in batch]}
        out = call_model(json.dumps(payload, ensure_ascii=False, indent=1), VERIFY_SYSTEM, VERIFY_SCHEMA, model)
        ids = {p.id for p, _ in batch}
        return {it["id"]: it["problems"] for it in out.get("items", []) if it.get("id") in ids and not it.get("ok") and it.get("problems", "").strip()}

    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(run, b) for b in batches]
        for fut in as_completed(futures):
            done += 1
            on_done(fut.result(), done, len(batches))


# ---------------------------------------------------------------- 원문 오류 검토 (역주)

REVIEW_SYSTEM = """You are a careful scientific editor. Find clear errors or internal inconsistencies in the SOURCE paper
(not in any translation). Check systematically, section by section, including figure captions, tables and the reference list:
- units or magnitudes of the same quantity that disagree anywhere in the paper (e.g. a concentration given as mg ml-1 in
  most places but as ug ml-1 or g ml-1 elsewhere, including in figure captions and figure labels)
- sizes, thresholds or numbers that contradict each other (e.g. particles "<100 um" collected vs a filter or sieve size
  that would give a different range, size ranges that differ between sections)
- a scale bar or unit that is implausible for what is shown
- a table row whose description names the wrong gene, plasmid or strain
- figure or panel references that point to the wrong panel
- numbers, units or labels printed inside figure images that disagree with the text (figure images are provided, either
  attached or as files in the current folder to open with the Read tool; look at every one of them before answering)

For an inconsistency that appears in several places, report EVERY deviating occurrence as its own issue (each with its own
page and quote), so each place can be marked, and give all occurrences of the same underlying problem the same "group"
(a short English key such as "PVC concentration unit"); different problems get different groups.
Do NOT report: style, grammar, logic or argument flow, missing details, rounded numbers, or conditions that are normal practice
(e.g. different temperatures for different organisms or steps). Re-read the surrounding text to make sure the issue is not
explained elsewhere.
Rate each issue: "high" only when the text itself shows the contradiction or error; otherwise "low".

For each issue give the page number, a short quote copied VERBATIM from the source text (at most 80 characters, enough to
locate it; for an issue seen only inside a figure image, quote the beginning of that figure's caption, e.g. "Fig. 3."),
and a concise note in Korean (1-2 sentences, academic style).
The note must be hedged, never asserting the correct value as fact: say what conflicts with what, then e.g.
"원문 오류로 보이나 원저자 확인이 필요하다" or "~일 가능성이 있다". Return at most 30 issues; an empty list if there are none."""

REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "issues": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "page": {"type": "integer"},
                    "quote": {"type": "string"},
                    "note": {"type": "string"},
                    "confidence": {"type": "string", "enum": ["high", "low"]},
                    "group": {"type": "string"},
                },
                "required": ["page", "quote", "note", "confidence", "group"],
            },
        }
    },
    "required": ["issues"],
}


REFS_SYSTEM = """You proofread the reference list of an academic paper. Report only clear typos: a misspelled or garbled
journal name (e.g. a well-known journal title with a wrong word), an obviously misspelled common word in a title, or a
duplicated reference. Do not report formatting style, abbreviation style, or anything you are not sure about.
Rate each issue "high" only when the error is obvious. Quote VERBATIM (at most 80 characters) and write a hedged note in Korean
(e.g. "'Microbial Releases'의 오기로 보이나 원저자 확인이 필요하다"). Use the page number shown in the text."""


def _dedupe(notes: list[dict]) -> list[dict]:
    """같은 쪽에서 인용문이 20자 이상 겹치는 역주는 하나만 남긴다."""
    from difflib import SequenceMatcher

    out: list[dict] = []
    for n in notes:
        a = re.sub(r"\s+", " ", n["quote"])
        dup = False
        for m in out:
            b = re.sub(r"\s+", " ", m["quote"])
            if m["page"] == n["page"] and SequenceMatcher(None, a, b).find_longest_match().size >= 20:
                dup = True
                break
        if not dup:
            out.append(n)
    return out


def review_source(page_texts: list[tuple[int, str]], model: str, figures: list[tuple[int, str]] | None = None) -> list[dict]:
    """원문 전체(표·그림 포함)를 읽고 원문 오류로 보이는 곳을 찾는다.

    figures: (쪽 번호, 그림 이미지 경로). 꼼꼼함이 중요하므로 model은 보통 번역보다 상위 모델(opus)을 쓴다.
    """
    full = "\n\n".join(f"<page n={n}>\n{t}\n</page>" for n, t in page_texts)
    m = re.search(r"\n\s*(references|bibliography)\s*:?\s*\n", full, re.I)
    body, refs = (full[: m.start()], full[m.start():]) if m else (full, "")
    if m:
        # 참고문헌이 시작되는 쪽 번호를 앞에 붙여 둔다
        last_page = re.findall(r"<page n=(\d+)>", body)
        if last_page and not refs.lstrip().startswith("<page"):
            refs = f"<page n={last_page[-1]}>\n" + refs

    issues: list[dict] = []
    if figures:
        listing = "\n".join(f"- page {pg}: {os.path.basename(path)}" for pg, path in figures)
        prompt = f"Figure images, in this order:\n{listing}\n\n{body[:150_000]}"
        issues += call_model(prompt, REVIEW_SYSTEM, REVIEW_SCHEMA, model, timeout=1500, images=[path for _, path in figures]).get("issues", [])
    else:
        issues += call_model(body[:150_000], REVIEW_SYSTEM, REVIEW_SCHEMA, model).get("issues", [])
    if refs.strip():
        issues += call_model(refs[:80_000], REFS_SYSTEM, REVIEW_SCHEMA, model).get("issues", [])

    notes = []
    for it in issues:
        if it.get("confidence") == "high" and it.get("quote", "").strip() and it.get("note", "").strip():
            notes.append({"n": 0, "page": int(it.get("page", 1)), "quote": it["quote"].strip(), "note": it["note"].strip(), "group": it.get("group", "").strip()})
    return _dedupe(notes)
