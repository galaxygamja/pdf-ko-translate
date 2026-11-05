"""한국어/라틴 문자 혼합 조판용 폰트 선택.

- 라틴 문자(영어 용어, 숫자, 기호): PDF 기본 Times/Helvetica 계열 (굵게·기울임 지원)
- 한글 본문: 원문이 세리프면 명조, 아니면 고딕
- 한글 굵게: 고딕 Bold (한국어 조판 관례대로 제목은 고딕)
맥은 AppleMyungjo·Apple SD Gothic Neo, 윈도우는 바탕·맑은 고딕을 쓰고, 없으면 PyMuPDF 내장 한글 폰트.
폰트 경로는 환경 변수로 바꿀 수 있다 (예: Noto Serif KR 설치 시).
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import pymupdf

from .osutil import IS_WIN, cache_dir

SD_GOTHIC_TTC = "/System/Library/Fonts/AppleSDGothicNeo.ttc"
MYUNGJO = "/System/Library/Fonts/Supplemental/AppleMyungjo.ttf"
WIN_FONTS = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
CACHE_DIR = cache_dir()


def _first(*paths: str | None) -> str | None:
    return next((p for p in paths if p and os.path.exists(p)), None)


def _serif_path() -> str | None:
    if IS_WIN:
        return _ttc_face(str(WIN_FONTS / "batang.ttc"), 0)  # 바탕
    return _first(MYUNGJO)


def _sans_path(bold: bool) -> str | None:
    if IS_WIN:
        return _first(str(WIN_FONTS / ("malgunbd.ttf" if bold else "malgun.ttf")))  # 맑은 고딕
    return _ttc_face(SD_GOTHIC_TTC, 6 if bold else 0)


def _ttc_face(path: str, index: int) -> str | None:
    """TTC 묶음에서 한 글꼴을 꺼내 캐시에 TTF로 저장한다."""
    if not os.path.exists(path):
        return None
    out = CACHE_DIR / f"{Path(path).stem}-{index}.ttf"
    if not out.exists():
        from fontTools.ttLib import TTCollection

        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        TTCollection(path).fonts[index].save(str(out))
    return str(out)


def _load(env: str, default: str | None) -> pymupdf.Font | None:
    path = os.environ.get(env) or default
    if path and os.path.exists(path):
        return pymupdf.Font(fontfile=path)
    return None


class FontBook:
    def __init__(self, serif: bool):
        if serif:
            self.latin = {
                (False, False): pymupdf.Font("tiro"),
                (True, False): pymupdf.Font("tibo"),
                (False, True): pymupdf.Font("tiit"),
                (True, True): pymupdf.Font("tibi"),
            }
        else:
            self.latin = {
                (False, False): pymupdf.Font("helv"),
                (True, False): pymupdf.Font("hebo"),
                (False, True): pymupdf.Font("heit"),
                (True, True): pymupdf.Font("hebi"),
            }
        fallback = pymupdf.Font("korea")
        gothic = _load("PDFKO_SANS", _sans_path(False))
        gothic_bold = _load("PDFKO_SANS_BOLD", _sans_path(True))
        if serif:
            self.ko = _load("PDFKO_SERIF", _serif_path()) or gothic or fallback
        else:
            self.ko = gothic or fallback
        self.ko_bold = gothic_bold or self.ko
        self.fallback = fallback

    def pick(self, ch: str, bold: bool, italic: bool) -> pymupdf.Font:
        cp = ord(ch)
        if cp < 0x2E80:
            f = self.latin[(bold, italic)]
            if f.has_glyph(cp):
                return f
        ko = self.ko_bold if bold else self.ko
        if ko.has_glyph(cp):
            return ko
        return self.fallback


@lru_cache(maxsize=2)
def fontbook(serif: bool) -> FontBook:
    return FontBook(serif)
