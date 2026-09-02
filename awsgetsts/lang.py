"""터미널 로케일에 따라 KO/EN 선택 / Pick KO or EN based on terminal locale.

터미널 stdout 인코딩이 UTF-8 이면 한글, 그 외(예: ASCII/C)면 영문을 반환한다.
``AWSGETSTS_LANG=ko|en`` 환경변수로 강제 지정 가능.

Returns Korean when stdout encoding is UTF-8; English otherwise. Override
via the ``AWSGETSTS_LANG`` environment variable (``ko`` or ``en``).
"""

from __future__ import annotations

import os
import sys


def supports_ko() -> bool:
    """터미널이 한글 렌더 가능한지 / Can the terminal render Korean?"""
    override = (os.environ.get("AWSGETSTS_LANG") or "").lower()
    if override == "ko":
        return True
    if override == "en":
        return False
    enc = (sys.stdout.encoding or "").lower()
    return "utf" in enc


def L(ko: str, en: str) -> str:
    """언어 선택 / Pick KO or EN."""
    return ko if supports_ko() else en


# 박스/기호 (UTF-8 or ASCII) / Box-drawing & symbols
def _utf() -> bool:
    return supports_ko()


def bullet() -> str:
    return "·" if _utf() else "*"


def arrow() -> str:
    return "→" if _utf() else "->"


def ok_mark() -> str:
    return "✓" if _utf() else "[OK]"


def warn_mark() -> str:
    return "⚠" if _utf() else "[!]"


def hrule_heavy() -> str:
    return "═" if _utf() else "="


def hrule_light() -> str:
    return "─" if _utf() else "-"


# 컬러 헬퍼 / Color helpers (termcolor-backed; honors FORCE_COLOR / NO_COLOR)
def _color(text: str, color: str, attrs=None) -> str:
    if os.environ.get("NO_COLOR"):
        return text
    if not sys.stdout.isatty() and not os.environ.get("FORCE_COLOR"):
        return text
    try:
        from termcolor import colored
    except ImportError:
        return text
    return colored(text, color, attrs=attrs)


def c_ok(text: str) -> str:
    return _color(text, "green")


def c_err(text: str) -> str:
    return _color(text, "red", attrs=["bold"])


def c_warn(text: str) -> str:
    return _color(text, "yellow")


def c_prompt(text: str) -> str:
    return _color(text, "yellow")


def c_bad(text: str) -> str:
    return _color(text, "magenta")


def c_dim(text: str) -> str:
    return _color(text, "white", attrs=["dark"])