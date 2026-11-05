"""모델 호출 창구. API 키 없이 로그인된 계정 사용량을 쓰는 CLI를 부른다.

- claude: Claude Code CLI (`claude -p`, claude.ai 로그인)
- codex:  OpenAI Codex CLI (`codex exec`, ChatGPT 로그인)

번역 코드는 call_model()만 부르고, 어느 CLI를 쓸지는 set_provider()로 정한다.
"""

from __future__ import annotations

import copy
import json
import os
import re
import subprocess
import tempfile

from .osutil import popen_kwargs, which

LIMIT_RE = re.compile(r"usage limit|rate.?limit|limit reached|quota|hit your limit", re.I)


class UsageLimitError(RuntimeError):
    """구독 사용량 한도에 걸림. 진행 상황을 저장하고 나중에 이어서 한다."""


class ModelError(RuntimeError):
    pass


ClaudeError = ModelError  # 예전 이름


PROVIDERS = {
    "claude": {"label": "Claude", "cmd": "claude", "model": "sonnet", "review_model": "opus"},
    # codex 모델은 "이름@추론강도". 이름을 비우면 Codex 기본 모델
    "codex": {"label": "ChatGPT", "cmd": "codex", "model": "@medium", "review_model": "@high"},
}

_current = "claude"


def set_provider(name: str) -> None:
    global _current
    if name not in PROVIDERS:
        raise ValueError(f"알 수 없는 번역 엔진: {name}")
    _current = name


def provider() -> str:
    return _current


def label(name: str | None = None) -> str:
    return PROVIDERS[name or _current]["label"]


# ---------------------------------------------------------------- 상태 확인

def status(name: str) -> dict:
    """설치·로그인 여부. 앱의 준비 상태 화면과 check_cli()가 쓴다."""
    exe = which(PROVIDERS[name]["cmd"])
    out = {"installed": bool(exe), "logged_in": False, "detail": ""}
    if not exe:
        return out
    try:
        if name == "claude":
            p = subprocess.run([exe, "auth", "status"], capture_output=True, timeout=30, **popen_kwargs())
            try:
                info = json.loads(p.stdout)
                out["logged_in"] = bool(info.get("loggedIn"))
                out["detail"] = info.get("authMethod", "")
            except json.JSONDecodeError:
                out["logged_in"] = p.returncode == 0
        else:
            p = subprocess.run([exe, "login", "status"], capture_output=True, timeout=30, **popen_kwargs())
            text = (p.stdout + p.stderr).strip()
            out["logged_in"] = p.returncode == 0 and "not logged in" not in text.lower()
            out["detail"] = text.splitlines()[-1] if text else ""
    except (OSError, subprocess.TimeoutExpired) as e:
        out["detail"] = str(e)
    return out


def check_cli() -> None:
    st = status(_current)
    cmd, lab = PROVIDERS[_current]["cmd"], label()
    if not st["installed"]:
        raise ModelError(f"'{cmd}' 명령을 찾을 수 없습니다. 앱의 '준비 상태'에서 {lab}용 도구를 설치하세요.")
    if not st["logged_in"]:
        raise ModelError(f"{lab} 계정에 로그인되어 있지 않습니다. 앱의 '준비 상태'에서 로그인하세요.")


# ---------------------------------------------------------------- 호출

def call_model(prompt: str, system: str, schema: dict, model: str, timeout: int = 900, images: list[str] | None = None) -> dict:
    """구조화(JSON) 응답 한 번. images: 모델이 볼 그림 파일 경로들 (같은 폴더에 있어야 함)."""
    if _current == "codex":
        return _call_codex(prompt, system, schema, model, timeout, images)
    return _call_claude(prompt, system, schema, model, timeout, images)


def _raise(msg: str, code: int, name: str) -> None:
    if LIMIT_RE.search(msg):
        raise UsageLimitError(msg)
    raise ModelError(msg or f"{name} 종료 코드 {code}")


def _call_claude(prompt, system, schema, model, timeout, images) -> dict:
    exe = which("claude") or "claude"
    with tempfile.TemporaryDirectory() as tmp:
        sys_file = os.path.join(tmp, "system.txt")
        with open(sys_file, "w", encoding="utf-8") as f:
            f.write(system)
        cmd = [
            exe, "-p",
            "--model", model,
            "--output-format", "json",
            "--json-schema", json.dumps(schema),
            "--system-prompt-file", sys_file,
            "--tools", "Read" if images else "",
            "--setting-sources", "",
            "--strict-mcp-config",
            "--disable-slash-commands",
            "--no-session-persistence",
        ]
        # 프로젝트 설정(CLAUDE.md 등)을 읽지 않도록 빈 폴더(또는 그림 폴더)에서 실행
        cwd = tmp
        if images:
            cmd += ["--allowedTools", "Read"]
            cwd = os.path.dirname(images[0])
        proc = subprocess.run(cmd, input=prompt, capture_output=True, timeout=timeout, cwd=cwd, **popen_kwargs())
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        data = {}
    msg = str(data.get("result") or proc.stderr or proc.stdout)[:500]
    if proc.returncode != 0 or data.get("is_error") or "structured_output" not in data:
        _raise(msg, proc.returncode, "claude")
    return data["structured_output"]


def strict_schema(schema: dict) -> dict:
    """OpenAI 구조화 출력은 모든 object에 additionalProperties=false, 모든 속성 required가 필요하다."""
    s = copy.deepcopy(schema)

    def fix(node):
        if isinstance(node, dict):
            if node.get("type") == "object" and "properties" in node:
                node["additionalProperties"] = False
                node["required"] = list(node["properties"])
            for v in node.values():
                fix(v)
        elif isinstance(node, list):
            for v in node:
                fix(v)

    fix(s)
    return s


def _call_codex(prompt, system, schema, model, timeout, images) -> dict:
    exe = which("codex") or "codex"
    name, _, effort = (model or "").partition("@")
    with tempfile.TemporaryDirectory() as tmp:
        schema_file = os.path.join(tmp, "schema.json")
        out_file = os.path.join(tmp, "out.json")
        with open(schema_file, "w", encoding="utf-8") as f:
            json.dump(strict_schema(schema), f)
        cmd = [
            exe, "exec",
            "--skip-git-repo-check",
            "--ephemeral",
            "--ignore-user-config",  # 사용자 설정(웹 검색·MCP 등)을 쓰지 않음. 로그인은 그대로
            "--ignore-rules",
            "-s", "read-only",
            "-C", tmp,
            "--output-schema", schema_file,
            "-o", out_file,
            "--color", "never",
            "-c", 'web_search="disabled"',
        ]
        if name:
            cmd += ["-m", name]
        if effort:
            cmd += ["-c", f'model_reasoning_effort="{effort}"']
        for img in images or []:
            cmd.append(f"--image={img}")
        cmd.append("-")  # 프롬프트는 표준 입력으로
        # codex에는 시스템 프롬프트 옵션이 없어 지시문을 앞에 붙인다 (윈도우 명령줄 길이 제한도 피함)
        full = f"<instructions>\n{system}\n</instructions>\n\n{prompt}"
        proc = subprocess.run(cmd, input=full, capture_output=True, timeout=timeout, cwd=tmp, **popen_kwargs())
        text = ""
        if os.path.exists(out_file):
            with open(out_file, encoding="utf-8") as f:
                text = f.read().strip()
    if proc.returncode == 0 and text:
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            m = re.search(r"\{.*\}", text, re.S)
            if m:
                try:
                    return json.loads(m.group(0))
                except json.JSONDecodeError:
                    pass
    errors = [l for l in proc.stderr.splitlines() if l.startswith("ERROR")]
    msg = (errors[-1] if errors else (proc.stderr.strip().splitlines() or [text or ""])[-1])[:500]
    m = re.search(r'"message"\s*:\s*"([^"]+)"', msg)
    _raise(m.group(1) if m else msg, proc.returncode, "codex")
    return {}
