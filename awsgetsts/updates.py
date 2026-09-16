"""새 버전 알림 / Check PyPI for a newer awsgetsts release.

- PyPI JSON API 로 최신 버전 조회 (하루 1회 캐시)
- 네트워크/파싱 실패는 조용히 무시
- ``AWSGETSTS_NO_UPDATE_CHECK=1`` 환경변수로 비활성화
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import urllib.request
from typing import Optional

from . import __version__
from .config import PREFERENCES_PATH, load_preferences

PYPI_URL = "https://pypi.org/pypi/awsgetsts/json"
CHECK_INTERVAL_HOURS = 24
ENV_DISABLE = "AWSGETSTS_NO_UPDATE_CHECK"


def _parse_version(v: str) -> tuple:
    """느슨한 PEP 440 파서 (숫자 파트만 비교) / Numeric-only version tuple."""
    parts = []
    for p in v.split("."):
        num = ""
        for ch in p:
            if ch.isdigit():
                num += ch
            else:
                break
        parts.append(int(num) if num else 0)
    return tuple(parts)


def _fetch_latest(timeout: float = 1.5) -> Optional[str]:
    try:
        req = urllib.request.Request(
            PYPI_URL, headers={"User-Agent": f"awsgetsts/{__version__}"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.load(resp)
        version = data.get("info", {}).get("version")
        return version if isinstance(version, str) else None
    except Exception:
        return None


def _save_check_result(prefs: dict, latest: str, now: _dt.datetime) -> None:
    prefs["latest_version_seen"] = latest
    prefs["last_update_check_at"] = now.isoformat()
    PREFERENCES_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        with PREFERENCES_PATH.open("w", encoding="utf-8") as f:
            json.dump(prefs, f, indent=2, ensure_ascii=False)
        PREFERENCES_PATH.chmod(0o600)
    except OSError:
        pass


def check_for_update(force: bool = False) -> Optional[str]:
    """새 버전이 있으면 그 문자열, 없거나 조회 실패면 None.

    ``force=True`` 는 캐시 무시하고 즉시 조회.
    """
    if os.environ.get(ENV_DISABLE) == "1":
        return None

    prefs = load_preferences()
    now = _dt.datetime.now(_dt.timezone.utc)

    latest = prefs.get("latest_version_seen")
    last_at = prefs.get("last_update_check_at")

    stale = True
    if not force and last_at:
        try:
            last_dt = _dt.datetime.fromisoformat(last_at)
            stale = (now - last_dt).total_seconds() >= CHECK_INTERVAL_HOURS * 3600
        except ValueError:
            stale = True

    if stale:
        fetched = _fetch_latest()
        if fetched:
            latest = fetched
            _save_check_result(prefs, latest, now)

    if not isinstance(latest, str) or not latest:
        return None
    try:
        if _parse_version(latest) > _parse_version(__version__):
            return latest
    except ValueError:
        return None
    return None