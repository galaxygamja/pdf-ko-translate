"""버튼으로 조작하는 앱 (맥·윈도우).

pywebview로 자체 창을 띄우고(브라우저·서버 없음), 화면(web/index.html)의 버튼이
Api 클래스의 파이썬 메서드를 직접 호출한다.
번역 작업은 명령어 버전(cli)을 하위 프로세스로 돌리고, 그 출력을 화면에 보여 준다.
결과물은 문서/PDF 번역/<파일 이름>/ 에 저장된다.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from importlib import resources
from pathlib import Path

from . import providers
from .osutil import FROZEN, IS_MAC, IS_WIN, config_dir, documents_dir, kill_tree, open_path, open_terminal, popen_kwargs

BASE = documents_dir() / "PDF 번역"
APP_PATH = Path.home() / "Applications" / "PDF 번역기.app"
SETTINGS = config_dir() / "settings.json"

# 공식 설치 스크립트 (Anthropic·OpenAI 배포 주소). 터미널 창에서 실행해 진행 상황을 직접 보게 한다
INSTALL = {
    "claude": ("curl -fsSL https://claude.ai/install.sh | bash", "irm https://claude.ai/install.ps1 | iex"),
    "codex": ("curl -fsSL https://chatgpt.com/codex/install.sh | sh", "irm https://chatgpt.com/codex/install.ps1 | iex"),
}
LOGIN = {
    "claude": ("claude auth login", "claude auth login"),
    "codex": ("codex login", "codex login"),
}


def _safe_stem(name: str) -> str:
    stem = Path(name).stem
    stem = re.sub(r"[/\\:\x00]", "_", stem).strip(". ") or "document"
    return stem[:120]


class Job:
    def __init__(self, folder: Path, pdf: Path, cmd: list[str], engine: str = "claude"):
        self.engine = engine
        self.id = uuid.uuid4().hex[:10]
        self.folder = folder
        self.pdf = pdf
        self.out = folder / f"{pdf.stem}.ko.pdf"
        self.qa = folder / f"{pdf.stem}.ko.qa.txt"
        self.log: list[str] = []
        self.status = "running"
        self.started = time.time()
        self.ended: float | None = None
        self.cancelled = False
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=folder, **popen_kwargs(new_group=True))
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        assert self.proc.stdout
        for line in self.proc.stdout:
            self.log.append(line.rstrip("\n"))
        code = self.proc.wait()
        self.ended = time.time()
        text = "\n".join(self.log)
        if self.cancelled:
            self.status = "cancelled"
        elif code == 0:
            self.status = "done"
        elif "[중단]" in text:
            self.status = "paused"  # 사용량 한도. 다시 시작하면 이어서 한다
        else:
            self.status = "error"

    def cancel(self) -> None:
        if self.proc.poll() is None:
            self.cancelled = True
            kill_tree(self.proc)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.pdf.stem,
            "status": self.status,
            "log": self.log[-400:],
            "elapsed": (self.ended or time.time()) - self.started,
            "output": str(self.out) if self.out.exists() else None,
            "qa": str(self.qa) if self.qa.exists() else None,
            "folder": str(self.folder),
            "engine_label": providers.label(self.engine),
        }


JOBS: dict[str, Job] = {}


def start_job(payload: dict) -> Job:
    src = Path(payload["path"])
    data = src.read_bytes()
    if not data.startswith(b"%PDF"):
        raise ValueError("PDF 파일이 아닙니다.")
    stem = _safe_stem(src.name)
    folder = BASE / stem
    folder.mkdir(parents=True, exist_ok=True)
    pdf = folder / f"{stem}.pdf"
    if not pdf.exists() or pdf.read_bytes() != data:
        pdf.write_bytes(data)

    o = payload.get("options", {})
    engine = o.get("provider", "claude")
    if engine not in providers.PROVIDERS:
        raise ValueError("번역 엔진을 골라 주세요.")
    # 묶인 앱에서는 자기 자신을 명령어 모드로 다시 실행한다
    if FROZEN:
        cli_exe = Path(sys.executable).with_name("pdf-ko-translate-cli.exe")  # 윈도우 묶음에만 있음
        base = [str(cli_exe), "--run-cli"] if cli_exe.exists() else [sys.executable, "--run-cli"]
    else:
        base = [sys.executable, "-u", "-m", "pdf_ko_translate"]
    cmd = base + [str(pdf), "-o", str(folder / f"{stem}.ko.pdf"), "--provider", engine]
    if o.get("pages", "").strip():
        if not re.fullmatch(r"[\d,\-\s]+", o["pages"]):
            raise ValueError("쪽 범위는 1-3,5 처럼 숫자로 적어 주세요.")
        cmd += ["--pages", o["pages"].replace(" ", "")]
    default = providers.PROVIDERS[engine]
    model = str(o.get("model") or default["model"]).strip()
    review_model = str(o.get("review_model") or default["review_model"]).strip()
    for m in (model, review_model):
        if not re.fullmatch(r"[\w.\-]*(@\w+)?", m):
            raise ValueError(f"모델 이름이 올바르지 않습니다: {m}")
    cmd += ["--model", model]
    if not o.get("review", True):
        cmd.append("--no-review")
    else:
        cmd += ["--review-model", review_model]
    if not o.get("verify", True):
        cmd.append("--no-verify")
    if not o.get("annotate", True):
        cmd.append("--no-annotate")
    cmd += ["--overflow", "shrink" if o.get("overflow") == "shrink" else "push"]
    if payload.get("glossary"):
        text = Path(payload["glossary"]).read_text(encoding="utf-8-sig")
        try:
            json.loads(text)
        except json.JSONDecodeError as e:
            raise ValueError(f"용어집 JSON 형식이 잘못되었습니다: {e}")
        g = folder / "용어집.json"
        g.write_text(text, encoding="utf-8")
        cmd += ["--glossary", str(g)]

    for j in JOBS.values():
        if j.folder == folder and j.status == "running":
            raise ValueError("같은 파일을 이미 번역하고 있습니다.")
    job = Job(folder, pdf, cmd, engine)
    JOBS[job.id] = job
    return job


def history() -> list[dict]:
    items = []
    if BASE.exists():
        for d in BASE.iterdir():
            outs = list(d.glob("*.ko.pdf")) if d.is_dir() else []
            if outs:
                out = max(outs, key=lambda p: p.stat().st_mtime)
                qa = out.with_name(out.stem + ".qa.txt")
                items.append({
                    "name": d.name,
                    "output": str(out),
                    "qa": str(qa) if qa.exists() else None,
                    "folder": str(d),
                    "mtime": out.stat().st_mtime,
                })
    return sorted(items, key=lambda i: -i["mtime"])[:30]


def _inside_base(path: str) -> Path | None:
    p = Path(path).resolve()
    try:
        p.relative_to(BASE.resolve())
    except ValueError:
        return None
    return p if p.exists() else None


class Api:
    """화면(JS)에서 window.pywebview.api.<이름>(...)으로 부르는 함수들."""

    def __init__(self) -> None:
        self._window = None

    def pick_pdf(self) -> dict | None:
        import webview

        paths = self._window.create_file_dialog(webview.FileDialog.OPEN, file_types=("PDF 파일 (*.pdf)",))
        return self.file_info(paths[0]) if paths else None

    def pick_glossary(self) -> dict | None:
        import webview

        paths = self._window.create_file_dialog(webview.FileDialog.OPEN, file_types=("용어집 JSON (*.json)",))
        return {"name": Path(paths[0]).name, "path": paths[0]} if paths else None

    def file_info(self, path: str) -> dict | None:
        p = Path(path)
        if not p.is_file():
            return None
        return {"name": p.name, "size": p.stat().st_size, "path": str(p)}

    def start(self, payload: dict) -> dict:
        try:
            return start_job(payload).to_dict()
        except (ValueError, OSError) as e:
            return {"error": str(e)}

    def job(self, job_id: str) -> dict | None:
        j = JOBS.get(job_id)
        return j.to_dict() if j else None

    def jobs(self) -> list[dict]:
        return [j.to_dict() for j in JOBS.values()]

    def history(self) -> list[dict]:
        return history()

    def open(self, path: str, reveal: bool = False) -> bool:
        p = _inside_base(path)
        if not p:
            return False
        open_path(p, reveal)
        return True

    def open_folder(self) -> None:
        BASE.mkdir(parents=True, exist_ok=True)
        open_path(BASE)

    # ---- 준비 상태 (claude / codex 설치·로그인)

    def setup_status(self) -> dict:
        return {
            "os": "windows" if IS_WIN else "mac" if IS_MAC else "other",
            "engines": {name: {"label": p["label"], **providers.status(name)} for name, p in providers.PROVIDERS.items()},
        }

    def install_engine(self, name: str) -> bool:
        if name not in INSTALL:
            return False
        open_terminal(*INSTALL[name])
        return True

    def login_engine(self, name: str) -> bool:
        if name not in LOGIN:
            return False
        open_terminal(*LOGIN[name])
        return True

    # ---- 화면 설정 기억 (엔진·모델 선택)

    def get_settings(self) -> dict:
        try:
            return json.loads(SETTINGS.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def save_settings(self, data: dict) -> bool:
        try:
            SETTINGS.parent.mkdir(parents=True, exist_ok=True)
            SETTINGS.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            return True
        except OSError:
            return False

    def cancel(self, job_id: str) -> bool:
        j = JOBS.get(job_id)
        if j:
            j.cancel()
        return bool(j)


def _icon_png() -> str:
    return str(resources.files("pdf_ko_translate").joinpath("web/icon.png"))


def _mac_identity() -> None:
    """메뉴 막대와 Dock에 'Python' 대신 앱 이름과 아이콘이 보이게 한다."""
    try:
        from AppKit import NSApplication, NSImage
        from Foundation import NSBundle

        info = NSBundle.mainBundle().infoDictionary()
        info["CFBundleName"] = "PDF 번역기"
        img = NSImage.alloc().initWithContentsOfFile_(_icon_png())
        if img:
            NSApplication.sharedApplication().setApplicationIconImage_(img)
    except Exception:
        pass


def install_app() -> Path:
    """~/Applications 에 더블클릭으로 여는 앱(PDF 번역기.app)을 만든다."""
    exe = shutil.which("pdf-ko-translate-app") or str(Path.home() / ".local/bin/pdf-ko-translate-app")
    contents = APP_PATH / "Contents"
    macos = contents / "MacOS"
    res = contents / "Resources"
    macos.mkdir(parents=True, exist_ok=True)
    res.mkdir(parents=True, exist_ok=True)
    launcher = macos / "launcher"
    launcher.write_text(f'#!/bin/zsh\nexec "{exe}"\n')
    launcher.chmod(0o755)
    _make_icns(res / "icon.icns")
    (contents / "Info.plist").write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>PDF 번역기</string>
  <key>CFBundleDisplayName</key><string>PDF 번역기</string>
  <key>CFBundleIdentifier</key><string>local.pdf-ko-translate</string>
  <key>CFBundleExecutable</key><string>launcher</string>
  <key>CFBundleIconFile</key><string>icon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleVersion</key><string>2.0</string>
  <key>LSMinimumSystemVersion</key><string>11.0</string>
  <key>NSHighResolutionCapable</key><true/>
</dict></plist>
"""
    )
    subprocess.run(["touch", str(APP_PATH)])  # Finder가 아이콘을 다시 읽도록
    return APP_PATH


def _make_icns(dest: Path) -> None:
    import tempfile

    import pymupdf

    src = pymupdf.Pixmap(_icon_png())
    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "icon.iconset"
        iconset.mkdir()
        for size in (16, 32, 128, 256, 512):
            for scale in (1, 2):
                px = size * scale
                pm = pymupdf.Pixmap(src, px, px) if src.width != px else src
                name = f"icon_{size}x{size}{'@2x' if scale == 2 else ''}.png"
                pm.save(str(iconset / name))
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(dest)], check=False)


def main() -> None:
    ap = argparse.ArgumentParser(prog="pdf-ko-translate-app", description="PDF 번역기 앱 창을 엽니다.")
    ap.add_argument("--install-app", action="store_true", help="~/Applications 에 'PDF 번역기' 앱을 만듭니다")
    args = ap.parse_args()
    if args.install_app:
        if not IS_MAC:
            print("--install-app 은 맥 전용입니다. 윈도우는 설치 파일(Setup.exe)을 쓰세요.")
            return
        print(f"앱을 만들었습니다: {install_app()}")
        return

    import webview

    BASE.mkdir(parents=True, exist_ok=True)
    api = Api()
    html = resources.files("pdf_ko_translate").joinpath("web/index.html").read_text(encoding="utf-8")
    window = webview.create_window("PDF 번역기", html=html, js_api=api, width=820, height=900, min_size=(520, 600))
    api._window = window

    def on_closing():
        running = [j for j in JOBS.values() if j.status == "running"]
        if running and not window.create_confirmation_dialog(
            "번역 중입니다", "창을 닫으면 번역이 중지됩니다. 지금까지 번역한 부분은 저장되어, 같은 파일로 다시 시작하면 이어서 합니다."
        ):
            return False
        for j in running:
            j.cancel()
        return True

    window.events.closing += on_closing
    if IS_MAC and not FROZEN:
        _mac_identity()  # 묶인 .app은 Info.plist가 이름·아이콘을 정한다
    webview.start()


if __name__ == "__main__":
    main()
