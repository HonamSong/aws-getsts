"""MFA / TOTP 코드 생성 / TOTP code generator."""

from __future__ import annotations

import pyotp


def get_mfa_token(totp_secret: str) -> str:
    """현재 시각의 6자리 TOTP 코드 반환 / Return current TOTP code."""
    return pyotp.TOTP(totp_secret).now()