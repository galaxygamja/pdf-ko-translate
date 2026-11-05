"""맥·윈도우 차이를 한곳에 모은 도우미.

하위 프로세스 실행(PATH·인코딩·콘솔 창), 폴더 위치, 파일 열기, 터미널 창 열기.
"""

from __future__ import annotations

import os
import shlex
import shutil
import signal
import subprocess
import sys
from pathlib import Path

IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"
FROZEN = bool(getattr(sys, "frozen", False))  # PyInstaller로 묶인 앱


def extra_path() -> list[str]:
    """Finder·시작 메뉴에서 앱을 열면 PATH가 짧아 claude/codex를 못 찾는다. 설치 위치를 덧붙인다."""
    home = Path.home()
    if IS_WIN:
        local = os.environ.get("LOCALAPPDATA", str(home / "AppData" / "Local"))
        roaming = os.environ.get("APPDATA", str(home / "AppData" / "Roaming"))
        return [
            str(home / ".local" / "bin"),  # Claude Code 공식 설치
            str(Path(local) / "Programs" / "OpenAI" / "Codex" / "bin"),  # Codex 공식 설치
            str(Path(roaming) / "npm"),  # npm -g
        ]
    return ["/opt/homebrew/bin", "/usr/local/bin", str(home / ".local/bin"), str(home / ".claude/local")]


def env() -> dict:
    e = dict(os.environ)
    parts = e.get("PATH", "").split(os.pathsep)
    e["PATH"] = os.pathsep.join(parts + [p for p in extra_path() if p not in parts])
    e["PYTHONUNBUFFERED"] = "1"
    e["PYTHONIOENCODING"] = "utf-8"
    e["PYTHONUTF8"] = "1"
    return e


def which(name: str) -> str | None:
    return shutil.which(name, path=env()["PATH"])


def popen_kwargs(new_group: bool = False) -> dict:
    """subprocess 공통 인자: UTF-8, 윈도우 콘솔 창 숨김, (선택) 새 프로세스 그룹."""
    kw: dict = {"encoding": "utf-8", "errors": "replace", "env": env()}
    if IS_WIN:
        flags = subprocess.CREATE_NO_WINDOW
        if new_group:
            flags |= subprocess.CREATE_NEW_PROCESS_GROUP
        kw["creationflags"] = flags
    elif new_group:
        kw["start_new_session"] = True
    return kw


def kill_tree(proc: subprocess.Popen) -> None:
    """프로세스와 그 자식(claude/codex)까지 끝낸다."""
    if proc.poll() is not None:
        return
    if IS_WIN:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        os.killpg(proc.pid, signal.SIGTERM)


def documents_dir() -> Path:
    if IS_WIN:
        try:  # OneDrive로 옮겨진 '문서' 폴더도 찾는다
            import ctypes
            from ctypes import wintypes

            class GUID(ctypes.Structure):
                _fields_ = [("a", wintypes.DWORD), ("b", wintypes.WORD), ("c", wintypes.WORD), ("d", ctypes.c_ubyte * 8)]

            fid = GUID(0xFDD39AD0, 0x238F, 0x46AF, (ctypes.c_ubyte * 8)(0xAD, 0xB4, 0x6C, 0x85, 0x48, 0x03, 0x69, 0xC7))
            buf = ctypes.c_wchar_p()
            if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(fid), 0, None, ctypes.byref(buf)) == 0:
                path = Path(buf.value)
                ctypes.windll.ole32.CoTaskMemFree(buf)
                return path
        except Exception:
            pass
    return Path.home() / "Documents"


def cache_dir() -> Path:
    if IS_WIN:
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "pdf-ko-translate"
    return Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "pdf-ko-translate"


def config_dir() -> Path:
    if IS_WIN:
        return Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "pdf-ko-translate"
    return Path.home() / "Library" / "Application Support" / "pdf-ko-translate" if IS_MAC else Path.home() / ".config" / "pdf-ko-translate"


def open_path(path: Path, reveal: bool = False) -> None:
    if IS_WIN:
        if reveal:
            subprocess.Popen(["explorer", f"/select,{path}"])
        else:
            os.startfile(str(path))  # type: ignore[attr-defined]
    elif IS_MAC:
        subprocess.Popen(["open", "-R", str(path)] if reveal else ["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path.parent if reveal else path)])


def open_terminal(mac_cmd: str, win_cmd: str) -> None:
    """설치·로그인처럼 사용자가 직접 봐야 하는 명령을 새 터미널 창에서 실행한다."""
    if IS_WIN:
        # 새 PowerShell 창. 끝나도 창이 닫히지 않게 -NoExit
        subprocess.Popen(
            ["powershell", "-NoExit", "-ExecutionPolicy", "Bypass", "-Command", win_cmd],
            creationflags=subprocess.CREATE_NEW_CONSOLE,
            env=env(),
        )
    elif IS_MAC:
        path = env()["PATH"]
        script = f"export PATH={shlex.quote(path)}; {mac_cmd}"
        esc = script.replace("\\", "\\\\").replace('"', '\\"')
        subprocess.Popen(["osascript", "-e", f'tell application "Terminal" to do script "{esc}"', "-e", 'tell application "Terminal" to activate'])
    else:
        subprocess.Popen(["x-terminal-emulator", "-e", "sh", "-c", mac_cmd])
