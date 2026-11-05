from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

import pymupdf

from .extract import extract
from .render import draw_debug, render
from .notes import organize
from .qa import check
from .providers import PROVIDERS, label, set_provider
from .translate import (
    ModelError,
    UsageLimitError,
    build_glossary,
    check_cli,
    fingerprint,
    merge_glossary,
    note_targets,
    review_source,
    translate_all,
    verify_all,
)


def _model_name(model: str) -> str:
    name, at, effort = model.partition("@")
    if not at:
        return model
    return f"{name or '기본 모델'}, 생각 정도 {effort}"


def _parse_pages(spec: str | None, count: int) -> list[int]:
    if not spec:
        return list(range(count))
    pages: set[int] = set()
    for part in spec.split(","):
        a, _, b = part.strip().partition("-")
        lo = int(a)
        hi = int(b) if b else lo
        pages.update(range(max(1, lo) - 1, min(hi, count)))
    return sorted(pages)


class WorkFile:
    """번역 진행 상황 저장 파일. 중단 후 이어 하기와 렌더링만 다시 하기에 쓴다."""

    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()
        self.data = {"glossary": None, "notes": None, "translations": {}}
        if path.exists():
            self.data.update(json.loads(path.read_text(encoding="utf-8")))

    def save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)

    def get(self, pid: str, src: str, fp: str | None) -> str | None:
        """fp가 None이면 설정이 바뀌었어도 저장된 번역을 쓴다 (--render-only)."""
        t = self.data["translations"].get(pid)
        if t and t.get("src") == src and (fp is None or t.get("fp") == fp):
            return t["ko"]
        return None

    def put(self, pid: str, src: str, ko: str, fp: str, verified: bool = False) -> None:
        self.data["translations"][pid] = {"fp": fp, "src": src, "ko": ko, "verified": verified}

    def verified(self, pid: str) -> bool:
        return bool(self.data["translations"].get(pid, {}).get("verified"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="pdf-ko-translate", description="영어 PDF를 레이아웃을 유지한 채 한국어로 번역합니다.")
    ap.add_argument("input", type=Path, help="번역할 PDF")
    ap.add_argument("-o", "--output", type=Path, help="결과 PDF (기본: <입력>.ko.pdf)")
    ap.add_argument("--pages", help="번역할 쪽 (예: 1-3,5). 지정하면 그 쪽만 결과에 담긴다")
    ap.add_argument("--provider", choices=list(PROVIDERS), default="claude", help="번역 엔진: claude(Claude Code 로그인) 또는 codex(ChatGPT 로그인한 Codex CLI). 기본 claude")
    ap.add_argument("--model", help="번역 모델 (기본: claude는 sonnet, codex는 '@medium' = Codex 기본 모델·중간 추론). codex는 '모델이름@추론강도'")
    ap.add_argument("--workers", type=int, default=4, help="동시 번역 호출 수 (기본: 4)")
    ap.add_argument("--glossary", type=Path, help="사용자 용어집 JSON ([{\"en\", \"action\": keep|translate, \"ko\", \"variants\"}]). 자동 용어집보다 우선")
    ap.add_argument("--overflow", choices=["push", "shrink"], default="push", help="한국어가 넘칠 때: push=아래 내용을 밀어 내림(기본), shrink=글자를 줄여 맞춤")
    ap.add_argument("--no-annotate", action="store_true", help="번역한 전문용어 첫 등장 때 영어 병기하지 않음")
    ap.add_argument("--no-glossary", action="store_true", help="용어집 생성 단계를 건너뜀")
    ap.add_argument("--no-review", action="store_true", help="원문 오류 검토(역주) 단계를 건너뜀")
    ap.add_argument("--review-model", help="원문 오류 검토 모델 (기본: claude는 opus, codex는 '@high')")
    ap.add_argument("--no-verify", action="store_true", help="번역 후 원문과 문장 단위로 대조하는 검증 단계를 건너뜀")
    ap.add_argument("--render-only", action="store_true", help="번역 호출 없이 저장된 번역으로 PDF만 다시 만듦")
    ap.add_argument("--debug-layout", action="store_true", help="레이아웃 분석 결과를 색 상자로 표시한 PDF도 만듦")
    args = ap.parse_args(argv)
    set_provider(args.provider)
    args.model = args.model or PROVIDERS[args.provider]["model"]
    args.review_model = args.review_model or PROVIDERS[args.provider]["review_model"]
    for stream in (sys.stdout, sys.stderr):  # 윈도우 콘솔·파이프에서도 한글이 깨지지 않게
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    src: Path = args.input
    if not src.exists():
        print(f"파일이 없습니다: {src}", file=sys.stderr)
        return 1
    out: Path = args.output or src.with_name(src.stem + ".ko.pdf")
    out.parent.mkdir(parents=True, exist_ok=True)
    work = WorkFile(out.with_suffix(".json"))
    user_glossary = json.loads(args.glossary.read_text(encoding="utf-8-sig")) if args.glossary else []

    doc = pymupdf.open(src)
    if not any(doc[i].get_text("text").strip() for i in range(doc.page_count)):
        print("텍스트가 없는 PDF입니다 (스캔본일 수 있음). 지금은 OCR을 지원하지 않습니다.", file=sys.stderr)
        return 1
    pages = _parse_pages(args.pages, doc.page_count)
    model = extract(doc, pages)
    paras = model.paragraphs
    print(f"[분석] {len(pages)}쪽, 번역할 문단 {len(paras)}개, 제외 {len(model.skipped)}개")

    if args.debug_layout:
        dbg = pymupdf.open(src)
        draw_debug(dbg, model)
        dbg.select(pages)
        dbg_path = out.with_name(out.stem + ".layout.pdf")
        dbg.save(dbg_path)
        print(f"[디버그] {dbg_path}")

    def glossary() -> list[dict]:
        return merge_glossary(work.data["glossary"] or [], user_glossary)

    def fpr() -> str:
        return fingerprint(glossary(), args.model, not args.no_annotate, work.data.get("notes"))

    fp = fpr()
    qa_fixed: list[str] = []
    exit_code = 0
    if not args.render_only:
        try:
            check_cli()
            if work.data["glossary"] is None and not args.no_glossary:
                print("[용어집] 전문용어 정리 중...")
                work.data["glossary"] = build_glossary(paras, args.model)
                work.save()
                keep = [t["en"] for t in work.data["glossary"] if t["action"] == "keep"]
                print(f"[용어집] {len(work.data['glossary'])}개 (영어 유지 {len(keep)}개) -> {work.path.name}")
            if work.data.get("notes") is None and not args.no_review:
                print("[검토] 원문 오류 확인 중...")
                texts = [(i + 1, doc[i].get_text()) for i in pages]
                with tempfile.TemporaryDirectory() as figdir:
                    figs = []
                    for i in pages:
                        for k, r in enumerate(model.pages[i].figures):
                            if r.width * r.height < 5000:
                                continue
                            path = os.path.join(figdir, f"page{i + 1}_fig{k + 1}.png")
                            doc[i].get_pixmap(dpi=150, clip=r).save(path)
                            figs.append((i + 1, path))
                    work.data["notes"] = organize(review_source(texts, args.review_model, figs), doc, model)
                work.save()
                print(f"[검토] 역주 {len(work.data['notes'])}개")
            fp = fpr()
            targets = note_targets(paras, work.data.get("notes") or [])

            todo = [p for p in paras if work.get(p.id, p.src, fp) is None]
            if todo:
                print(f"[번역] {len(todo)}개 문단 번역 시작 ({label()} {_model_name(args.model)}, 동시 {args.workers})")
                t0 = time.time()
                by_id = {p.id: p for p in paras}

                def on_done(res: dict[str, str], done: int, total: int) -> None:
                    with work.lock:
                        for pid, ko in res.items():
                            work.put(pid, by_id[pid].src, ko, fp)
                        work.save()
                    print(f"[번역] {done}/{total} 묶음 완료 ({time.time() - t0:.0f}초)")

                translate_all(todo, paras, glossary(), args.model, args.workers, not args.no_annotate, on_done, targets)

            if not args.no_verify:
                pairs = [(p, ko) for p in paras if (ko := work.get(p.id, p.src, fp)) and not work.verified(p.id)]
                if pairs:
                    print(f"[검증] {len(pairs)}개 문단을 원문과 대조 중...")
                    problems: dict[str, str] = {}

                    def on_checked(res: dict[str, str], done: int, total: int) -> None:
                        problems.update(res)

                    verify_all(pairs, glossary(), args.model, args.workers, on_checked)
                    by_id = {p.id: p for p in paras}
                    if problems:
                        print(f"[검증] {len(problems)}개 문단 다시 번역")

                        def on_fixed(res: dict[str, str], done: int, total: int) -> None:
                            with work.lock:
                                for pid, ko in res.items():
                                    work.put(pid, by_id[pid].src, ko, fp, verified=True)
                                work.save()

                        redo = [by_id[pid] for pid in problems]
                        translate_all(redo, paras, glossary(), args.model, args.workers, not args.no_annotate, on_fixed, targets, problems)
                        qa_fixed.extend(f"[{pid}] 검증에서 고침: {msg}" for pid, msg in problems.items())
                    with work.lock:
                        for p, _ in pairs:
                            if p.id not in problems and (ko := work.get(p.id, p.src, fp)):
                                work.put(p.id, p.src, ko, fp, verified=True)
                        work.save()
        except UsageLimitError as e:
            print(f"\n[중단] {label()} 사용량 한도에 걸렸습니다: {e}", file=sys.stderr)
            print("지금까지의 번역은 저장했습니다. 한도가 풀린 뒤 같은 명령을 다시 실행하면 이어서 번역합니다.", file=sys.stderr)
            exit_code = 2
        except ModelError as e:
            print(f"\n[오류] {e}", file=sys.stderr)
            print("지금까지의 번역은 저장했습니다. 같은 명령을 다시 실행하면 이어서 번역합니다.", file=sys.stderr)
            exit_code = 2

    translations = {p.id: ko for p in paras if (ko := work.get(p.id, p.src, None if args.render_only else fp))}
    missing = len(paras) - len(translations)

    # 문단 단위 대조 결과를 보고서로 남긴다
    qa_lines = []
    for p in paras:
        if p.id in translations and (issues := check(p.plain, translations[p.id], glossary())):
            qa_lines.append(f"[{p.id}] {'; '.join(issues)}\n  원문: {p.plain[:200]}\n  번역: {translations[p.id][:200]}\n")
    qa_path = out.with_name(out.stem + ".qa.txt")
    report = qa_lines or ["기계적 대조(숫자·참조·인용) 문제 없음\n"]
    if qa_fixed:
        report += ["\n# 문장 대조 검증에서 발견해 다시 번역한 문단\n"] + [l + "\n" for l in qa_fixed]
    qa_path.write_text("\n".join(report), encoding="utf-8")

    notes = work.data.get("notes") or []
    result, stats = render(doc, model, translations, args.overflow, notes)
    if args.pages:
        keep_pages = pages + list(range(doc.page_count, result.page_count))  # 끝에 붙은 역자 주 쪽 포함
        result.select(keep_pages)
    result.subset_fonts()
    result.save(out, garbage=3, deflate=True)
    moved = "밀어 내림" if args.overflow == "push" else "넘침"
    print(f"[완료] {out}  (번역 {stats.rendered}개, 글자 축소 {len(stats.shrunk)}개, {moved} {len(stats.overflow)}개, 역주 {len(notes)}개)")
    if stats.extended_pages:
        print("  세로로 늘어난 쪽: " + ", ".join(f"{p + 1}쪽(+{h:.0f}pt)" for p, h in sorted(stats.extended_pages.items())))
    print(f"  대조 검사: 확인 필요 {len(qa_lines)}개 -> {qa_path.name}")
    if missing:
        print(f"  아직 번역되지 않은 문단 {missing}개는 원문 그대로 두었습니다.")
    return exit_code
