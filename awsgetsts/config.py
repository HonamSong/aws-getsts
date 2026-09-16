"""사용자 설정 로드 / User config loader.

기본 위치 / Default location:
    ~/.config/getAwsSTS/awsUserConfig.json

Lookup order:
    1. Explicit path passed to :func:`load_config`  (``--config``)
    2. ``AWSGETSTS_CONFIG`` environment variable
    3. ``~/.config/getAwsSTS/awsUserConfig.json`` (canonical default)
    4. (legacy) 스크립트 옆 ``aws_user_config.json`` / next to entry script
    5. (legacy) 현재 디렉토리 ``./aws_user_config.json`` / current working directory

If none exist, the setup wizard runs automatically and writes to the
canonical default location.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

REQUIRED_FIELDS = ("account_id", "profile", "mfa_serial", "totp_secret")

# 정규 경로 / Canonical default
DEFAULT_CONFIG_PATH = Path.home() / ".config" / "getAwsSTS" / "awsUserConfig.json"

# 사용자 개인 설정(비-프로필) 저장 위치 / User preferences (non-profile) file
PREFERENCES_PATH = Path.home() / ".config" / "getAwsSTS" / "preferences.json"

# 기존 사용자 호환을 위한 legacy 파일명 / Legacy filename for backward compat
LEGACY_FILENAME = "aws_user_config.json"


def load_preferences(path: Path = PREFERENCES_PATH) -> dict:
    """사용자 개인 설정 로드 / Load persisted user preferences."""
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_preference(key: str, value, path: Path = PREFERENCES_PATH) -> None:
    """단일 설정 항목 저장 / Persist a single preference key."""
    prefs = load_preferences(path)
    prefs[key] = value
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(prefs, f, indent=2, ensure_ascii=False)
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _script_dir_legacy() -> Optional[Path]:
    """엔트리 스크립트 옆 legacy 경로 / Legacy path next to the entry script."""
    try:
        script = Path(sys.argv[0]).resolve()
    except (OSError, IndexError):
        return None
    if script.is_file():
        return script.parent / LEGACY_FILENAME
    return None


def _cwd_legacy() -> Path:
    return Path.cwd() / LEGACY_FILENAME


@dataclass(frozen=True)
class ProfileConfig:
    """단일 프로필 설정 / Single profile entry."""

    account_id: str
    profile: str
    mfa_serial: str
    totp_secret: str

    @classmethod
    def from_dict(cls, data: dict) -> "ProfileConfig":
        missing = [f for f in REQUIRED_FIELDS if f not in data]
        if missing:
            raise ValueError(f"missing required fields: {missing}")
        return cls(
            account_id=data["account_id"],
            profile=data["profile"],
            mfa_serial=data["mfa_serial"],
            totp_secret=data["totp_secret"],
        )


def resolve_config_path(explicit: Optional[Path] = None) -> Path:
    """설정 파일 경로 결정 / Resolve which config path to use.

    See module docstring for the full lookup order. New installs go
    to ``~/.config/getAwsSTS/awsUserConfig.json``. Existing legacy
    files (next to the script or in CWD) are still honored.
    """
    if explicit is not None:
        return explicit
    env_path = os.environ.get("AWSGETSTS_CONFIG")
    if env_path:
        return Path(env_path).expanduser()

    if DEFAULT_CONFIG_PATH.exists():
        return DEFAULT_CONFIG_PATH

    # legacy fallbacks (existing users)
    script_legacy = _script_dir_legacy()
    if script_legacy and script_legacy.exists():
        return script_legacy

    cwd_legacy = _cwd_legacy()
    if cwd_legacy.exists():
        return cwd_legacy

    # 신규 생성 위치 / new-file location: canonical
    return DEFAULT_CONFIG_PATH


def load_config(
    path: Optional[Path] = None,
    create_if_missing: bool = True,
) -> Dict[str, ProfileConfig]:
    """설정 파일 로드 / Load config.

    ``create_if_missing=True`` (기본): 파일이 없으면 대화형 마법사 실행.
    ``False``: 파일이 없으면 ``FileNotFoundError`` 발생 (--check 등 진단용).
    """
    config_path = resolve_config_path(path)

    if not config_path.exists():
        if not create_if_missing:
            raise FileNotFoundError(str(config_path))
        raw = _interactive_create(config_path)
    else:
        with config_path.open("r", encoding="utf-8") as f:
            raw = json.load(f)

    return {name: ProfileConfig.from_dict(entry) for name, entry in raw.items()}


def _interactive_create(target: Path) -> dict:
    """대화형 설정 마법사에 위임 / Delegate to the setup wizard."""
    from .setup import run_wizard  # 지연 임포트로 순환참조 방지 / avoid circular import
    return run_wizard(target)