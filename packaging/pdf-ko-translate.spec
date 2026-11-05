# PyInstaller 설정: 맥은 "PDF 번역기.app", 윈도우는 "PDF 번역기" 폴더(→ Inno Setup으로 설치 파일).
# 실행:  uv run --group build pyinstaller packaging/pdf-ko-translate.spec --noconfirm
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).parent
PKG = ROOT / "src" / "pdf_ko_translate"
NAME = "PDF 번역기"
VERSION = "0.2.0"
ICON = str(PKG / "web" / "icon.png")  # Pillow가 있으면 .icns/.ico로 자동 변환

hidden = collect_submodules("pdf_ko_translate") + collect_submodules("fontTools.ttLib")
if sys.platform == "darwin":
    hidden += ["webview.platforms.cocoa"]
elif sys.platform == "win32":
    hidden += ["webview.platforms.edgechromium", "webview.platforms.winforms", "clr"]

a = Analysis(
    [str(ROOT / "packaging" / "launch.py")],
    pathex=[str(ROOT / "src")],
    datas=[(str(PKG / "web"), "pdf_ko_translate/web")],
    hiddenimports=hidden,
    excludes=["tkinter", "matplotlib", "numpy", "PIL.ImageQt"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=NAME,
    console=False,  # 창 앱. 번역 작업(--run-cli)의 출력은 파이프로 앱이 읽는다
    icon=ICON,
    argv_emulation=False,
)
extra = []
if sys.platform == "win32":
    # 창 앱(.exe)은 표준 출력이 없을 수 있어, 번역 작업용 콘솔 실행 파일을 따로 둔다 (앱이 창 없이 실행)
    extra.append(EXE(pyz, a.scripts, [], exclude_binaries=True, name="pdf-ko-translate-cli", console=True, icon=ICON))
coll = COLLECT(exe, *extra, a.binaries, a.datas, name=NAME)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name=f"{NAME}.app",
        icon=ICON,
        bundle_identifier="io.github.galaxygamja.pdf-ko-translate",
        version=VERSION,
        info_plist={
            "CFBundleName": NAME,
            "CFBundleDisplayName": NAME,
            "CFBundleShortVersionString": VERSION,
            "LSMinimumSystemVersion": "11.0",
            "NSHighResolutionCapable": True,
            "NSAppleEventsUsageDescription": "설치·로그인 명령을 터미널 창에서 실행하기 위해 필요합니다.",
        },
    )
