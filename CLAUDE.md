# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`pdf-ko-translate`: translates English academic PDFs to Korean while keeping the original layout (figures, tables, equations, references untouched). Translation goes through a logged-in CLI using the user's subscription — Claude Code (`claude -p`, claude.ai login) or OpenAI Codex (`codex exec`, ChatGPT login) — **not** an API. Users have no API keys. Do not add `anthropic`/`openai` SDK dependencies or API-key flows.

Distributed to friends as unsigned desktop apps for macOS and Windows (built by GitHub Actions); keep code cross-platform.

User-facing docs (Korean) are in `README.md`; UI strings, log messages and prompts' user-visible output are Korean.

## Commands

No test suite or linter is configured. The package is installed as a uv tool, so **code changes only take effect after reinstalling**:

```bash
uv tool install --force /Users/cloudgamja/projects/papar_translation
```

```bash
pdf-ko-translate samples/microplastics.pdf -o samples/out/microplastics.ko.pdf   # full run (uses claude.ai usage)
pdf-ko-translate samples/microplastics.pdf --pages 1-2 --no-review --no-verify    # cheap smoke test
pdf-ko-translate samples/microplastics.pdf --pages 1-2 --provider codex          # same via ChatGPT/Codex
pdf-ko-translate samples/microplastics.pdf --render-only                          # re-render from .ko.json, no model calls
pdf-ko-translate samples/microplastics.pdf --debug-layout                         # writes .ko.layout.pdf showing extracted/skipped regions
pdf-ko-translate-app --install-app                                                # (re)build ~/Applications/PDF 번역기.app
```

`--render-only` is the fast loop for layout/font/reflow work. Hand-edit `translations[*].ko` or `glossary` in the `.ko.json` and re-render to iterate without spending model calls.

## Architecture

Pipeline (orchestrated in `cli.py`): extract → glossary → source review (역주) → translate → verify → render.

- `extract.py` — PyMuPDF `get_text("dict")` into `Paragraph`s with inline markup tags (`<i><b><sup><sub><a>`). Detects columns, splits paragraphs, and `_merge_continuations` joins paragraphs across columns/pages. Skips (not translated): figures, tables, rotated text, headers/footers, references, URLs. Skip heuristics are tuned on real papers; changes here ripple into everything downstream.
- `providers.py` — the only place that runs a model CLI: `call_model()` dispatches to `claude -p` (`--json-schema`, `--system-prompt-file`, tools off except `Read` for figure review) or `codex exec` (`--output-schema` file, `-o` out file, `--ignore-user-config`, read-only sandbox, system prompt prepended as `<instructions>`, figures via `--image`). OpenAI needs strict schemas, so `strict_schema()` adds `additionalProperties:false` + all-required. Codex models are `name@effort` (empty name = Codex default). Also `status()` (installed/logged in) and `UsageLimitError`/`ModelError`.
- `osutil.py` — all OS differences: PATH extension for Finder/Start-menu launches, UTF-8 + hidden-console subprocess kwargs, process-tree kill, Documents/cache/config dirs, open/reveal, opening a terminal for install/login.
- `translate.py` — prompts and batching on top of `call_model`. Holds every prompt (`GLOSSARY_SYSTEM`, `TRANSLATE_SYSTEM`, `VERIFY_SYSTEM`, `REVIEW_SYSTEM`, `REFS_SYSTEM`), batching, retries, and `fingerprint()` — translations are cached by a fingerprint of prompt+glossary+model+options (+engine name for non-Claude engines, so old Claude caches stay valid), so editing a prompt invalidates cached translations on next run.
- `qa.py` — mechanical checks (numbers, citations `[n]`, figure/table refs, empty parens, glossary compliance). Failures feed back into a retranslation prompt.
- `notes.py` — locates source-error notes in the paragraph text (normalized matching) and numbers them in reading order, grouping identical issues.
- `render.py` + `reflow.py` — erase original text only (images/vector graphics kept), typeset Korean in the same boxes (shrink up to 6%, then push content down). `reflow.py` plans which page strips shift down and by how much; pages may be extended vertically. Text is drawn with real space glyphs and ordered writers so the text layer copies/searches correctly. `[역주 N]` markers use NBSP so they never split across lines.
- `fonts.py` — system fonts: macOS AppleMyungjo / Apple SD Gothic Neo (ttc faces extracted to the cache dir), Windows 바탕 (batang.ttc) / 맑은 고딕; fallback PyMuPDF `korea`. Overridable via `PDFKO_SERIF`, `PDFKO_SANS`, `PDFKO_SANS_BOLD`.
- `app.py` + `web/index.html` — native pywebview window (no browser, no HTTP server). JS calls the Python `Api` class via `window.pywebview.api`. Jobs run the CLI as a subprocess (`python -m pdf_ko_translate`, or in the frozen app the app binary with `--run-cli` / Windows `pdf-ko-translate-cli.exe`) and the UI polls its log; outputs go to `<Documents>/PDF 번역/<stem>/`. The engine card shows install/login state and opens a terminal running the official install scripts (claude.ai/install.*, chatgpt.com/codex/install.*) or `claude auth login` / `codex login`. UI settings persist in the config dir.
- `desktop.py` + `packaging/` — PyInstaller entry/spec, Inno Setup script; `.github/workflows/release.yml` builds Windows installer + macOS dmg (Apple Silicon only; Intel Macs deliberately unsupported) on `v*` tags. Mac app is ad-hoc signed by PyInstaller's BUNDLE step (CI only verifies; re-signing nested files afterwards breaks the seal; local `codesign --deep` crashes on this macOS beta). The spec excludes PyMuPDF 2.0's unused layout/LLM deps (onnxruntime, numpy, …) and Pillow, whose `__dot__dylibs` symlinks break the codesign seal. The Mac executable is named `PDFTranslator` (ASCII) inside `PDF 번역기.app`.

State: `<name>.ko.json` ("workfile") stores glossary, notes and per-paragraph translations; reruns resume from it (usage-limit errors exit with code 2 after saving progress). `<name>.ko.qa.txt` is the QA report.

## Conventions worth knowing

- Model-quality problems (e.g. a wrong auto-glossary term, weak note wording) are deliberately **not** patched in code; the user overrides via `--glossary`. Only fix program-side problems. Translation keeps source errors as-is and flags them with 역주 notes rather than correcting them.
- Review/verify use Opus by default; translation uses Sonnet. Codex: translation `@medium`, review `@high`.
- All text file IO and subprocess pipes must be explicit UTF-8 (Windows defaults to cp949).
- Stage 3 ideas (side-by-side viewer, web upload) were explicitly declined.
- Sample paper: `samples/microplastics.pdf`; reference outputs in `samples/out/`.
