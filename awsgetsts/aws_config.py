"""~/.aws/config 관련 유틸 / Helpers for ~/.aws/config.

- 프로필 존재 여부 확인 / profile existence checks
- 토큰 만료 검사 / expiration check for existing sts-<name> profiles
- INI 파일 병합 저장 / merge-write INI sections
"""

from __future__ import annotations

import configparser
import datetime
from datetime import timedelta, timezone
from pathlib import Path
from typing import Iterable, List, Optional

from dateutil.tz import tzutc

from .lang import L
from .logger import log

AWS_CONFIG_PATH = Path.home() / ".aws" / "config"
AWS_CREDENTIALS_PATH = Path.home() / ".aws" / "credentials"
KST = timezone(timedelta(hours=9), name="KST")


def convert_to_kst(dt: datetime.datetime) -> datetime.datetime:
    """UTC → KST 변환 / Convert to Korea Standard Time."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tzutc())
    return dt.astimezone(KST)


def _read_ini(path: Path) -> configparser.ConfigParser:
    parser = configparser.ConfigParser()
    if path.exists():
        parser.read(path)
    return parser


def list_aws_profiles(
    config_path: Path = AWS_CONFIG_PATH,
    credentials_path: Path = AWS_CREDENTIALS_PATH,
) -> List[str]:
    """~/.aws 에 설정된 모든 프로필 이름 반환 / List AWS profiles."""
    profiles: set[str] = set()

    cfg = _read_ini(config_path)
    for section in cfg.sections():
        name = section.replace("profile ", "") if section != "default" else "default"
        profiles.add(name)

    creds = _read_ini(credentials_path)
    for section in creds.sections():
        profiles.add(section)

    return sorted(profiles)


def profile_exists(
    profile_name: str,
    config_path: Path = AWS_CONFIG_PATH,
    credentials_path: Path = AWS_CREDENTIALS_PATH,
) -> bool:
    """지정 프로필이 존재하는지 확인 / Check whether a profile is configured."""
    cfg = _read_ini(config_path)
    section = "default" if profile_name == "default" else f"profile {profile_name}"
    if cfg.has_section(section):
        return True

    creds = _read_ini(credentials_path)
    return creds.has_section(profile_name)


def existing_token_valid(
    profile_name: str,
    config_path: Path = AWS_CONFIG_PATH,
    now: Optional[datetime.datetime] = None,
) -> bool:
    """기존 sts-<name> 프로필이 아직 유효한지 검사 / Is the stored token still valid?"""
    section = f"profile sts-{profile_name}"

    cfg = _read_ini(config_path)
    if not cfg.has_section(section):
        log.debug(f"section {section} not found")
        return False
    if not cfg.has_option(section, "expiration"):
        log.debug(f"expiration missing in {section}")
        return False

    expiration_str = cfg.get(section, "expiration")
    try:
        expiration = datetime.datetime.fromisoformat(expiration_str)
    except ValueError as e:
        log.warn(f"invalid expiration in {section}: {e}")
        return False

    current = now or datetime.datetime.now(tz=expiration.tzinfo)
    kst = convert_to_kst(expiration)
    if current >= expiration:
        # 타임스탬프 없이 강조 / plain print for visibility
        print(f"[{section}] " + L(
            f"토큰 만료됨 (만료: {kst}) - 새 토큰 발급 필요",
            f"token expired (at {kst}) - refresh required",
        ))
        return False

    print(f"[{section}] " + L(
        f"토큰 유효함 (만료: {kst})",
        f"token valid (until {kst})",
    ))
    return True


def token_info(
    profile_name: str,
    config_path: Path = AWS_CONFIG_PATH,
    now: Optional[datetime.datetime] = None,
) -> tuple[str, Optional[datetime.datetime]]:
    """sts-<name> 토큰 상태 조회 / Inspect stored STS token.

    Returns ``(state, expiration)`` where state is one of
    ``"none"``, ``"invalid"``, ``"expired"``, ``"valid"``.
    """
    section = f"profile sts-{profile_name}"
    cfg = _read_ini(config_path)
    if not cfg.has_section(section) or not cfg.has_option(section, "expiration"):
        return ("none", None)
    try:
        expiration = datetime.datetime.fromisoformat(cfg.get(section, "expiration"))
    except ValueError:
        return ("invalid", None)
    current = now or datetime.datetime.now(tz=expiration.tzinfo)
    return ("valid" if current < expiration else "expired", expiration)


def write_sts_sections(
    sections: Iterable[tuple[str, dict]],
    config_path: Path = AWS_CONFIG_PATH,
    add_cli_pager: bool = True,
) -> None:
    """STS 세션을 ~/.aws/config 에 병합 저장 / Merge STS sections into ~/.aws/config.

    ``sections`` yields ``(section_name, key_value_dict)``. The section
    name is written verbatim (e.g. ``"profile sts-dev"``). When
    ``add_cli_pager`` is True, ``cli_pager = `` is appended to each
    written section (disables the AWS CLI pager).
    """
    existing = _read_ini(config_path)

    for section_name, kv in sections:
        if not existing.has_section(section_name):
            existing.add_section(section_name)
        for key, value in kv.items():
            existing.set(section_name, key, value)
        if add_cli_pager:
            existing.set(section_name, "cli_pager", "")

    config_path.parent.mkdir(parents=True, exist_ok=True)
    with config_path.open("w", encoding="utf-8") as f:
        existing.write(f)


def list_sts_sections(config_path: Path = AWS_CONFIG_PATH) -> List[str]:
    """~/.aws/config 의 'profile sts-*' 섹션 이름 목록 / List sts-* sections."""
    parser = _read_ini(config_path)
    return [s for s in parser.sections() if s.startswith("profile sts-")]


def remove_sts_sections(
    section_names: Iterable[str],
    config_path: Path = AWS_CONFIG_PATH,
) -> int:
    """지정된 섹션들을 ~/.aws/config 에서 제거 / Remove sections; return count."""
    if not config_path.exists():
        return 0
    parser = _read_ini(config_path)
    removed = 0
    for name in section_names:
        if parser.has_section(name):
            parser.remove_section(name)
            removed += 1
    if removed:
        with config_path.open("w", encoding="utf-8") as f:
            parser.write(f)
    return removed


def upsert_base_profile(
    profile_name: str,
    access_key_id: str,
    secret_access_key: str,
    region: str = "ap-northeast-2",
    output: str = "json",
    config_path: Path = AWS_CONFIG_PATH,
    credentials_path: Path = AWS_CREDENTIALS_PATH,
) -> None:
    """base AWS 프로필 신규/갱신 / Create or update a base AWS profile.

    ~/.aws/credentials 에는 access_key / secret_key,
    ~/.aws/config 에는 region / output 을 저장합니다.
    Writes access keys to ~/.aws/credentials and region/output to ~/.aws/config.
    Both files are chmod'd to 0600.
    """
    creds = _read_ini(credentials_path)
    if not creds.has_section(profile_name):
        creds.add_section(profile_name)
    creds.set(profile_name, "aws_access_key_id", access_key_id)
    creds.set(profile_name, "aws_secret_access_key", secret_access_key)

    credentials_path.parent.mkdir(parents=True, exist_ok=True)
    with credentials_path.open("w", encoding="utf-8") as f:
        creds.write(f)
    try:
        credentials_path.chmod(0o600)
    except OSError as e:
        log.warn(f"chmod 0600 failed for {credentials_path}: {e}")

    cfg = _read_ini(config_path)
    section = "default" if profile_name == "default" else f"profile {profile_name}"
    if not cfg.has_section(section):
        cfg.add_section(section)
    cfg.set(section, "region", region)
    cfg.set(section, "output", output)

    config_path.parent.mkdir(parents=True, exist_ok=True)
    with config_path.open("w", encoding="utf-8") as f:
        cfg.write(f)
    try:
        config_path.chmod(0o600)
    except OSError as e:
        log.warn(f"chmod 0600 failed for {config_path}: {e}")