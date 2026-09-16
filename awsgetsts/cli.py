"""CLI 엔트리포인트 / Command-line entry point."""

from __future__ import annotations

import argparse
import json
import sys
from configparser import ConfigParser
from io import StringIO
from pathlib import Path
from typing import Iterable, Optional

from . import __version__
from .aws_config import (
    AWS_CONFIG_PATH,
    AWS_CREDENTIALS_PATH,
    convert_to_kst,
    existing_token_valid,
    list_aws_profiles,
    list_sts_sections,
    profile_exists,
    remove_sts_sections,
    token_info,
    write_sts_sections,
)
from .config import (
    PREFERENCES_PATH,
    ProfileConfig,
    load_config,
    load_preferences,
    resolve_config_path,
    save_preference,
)
from .lang import L, arrow, c_bad, c_dim, c_err, c_ok, c_prompt, c_warn, hrule_light
from .logger import log, set_level
from .setup import run_edit, run_wizard
from .sts import DEFAULT_DURATION, MIN_DURATION, StsCredentials, get_session_token
from .updates import check_for_update


def build_parser() -> argparse.ArgumentParser:
    """CLI 파서 구성 / Build argument parser."""
    p = argparse.ArgumentParser(
        prog="awsgetsts",
        description=(
            "AWS STS 임시 자격증명 발급 유틸 / "
            "Issue AWS STS session tokens (optionally with MFA) "
            "and write them to ~/.aws/config."
        ),
    )
    p.add_argument("-V", "--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument(
        "-p", "--profile",
        action="append",
        help="대상 프로필 (반복 가능) / target profile (repeatable). "
             "생략 시 모든 프로필 처리 / defaults to all configured profiles.",
    )
    p.add_argument(
        "-c", "--config",
        type=Path,
        help="사용자 설정 파일 경로 / user config file path",
    )
    p.add_argument(
        "-d", "--duration",
        type=int,
        default=DEFAULT_DURATION,
        help=f"세션 지속시간(초) / session duration in seconds "
             f"(min {MIN_DURATION}, default {DEFAULT_DURATION})",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="기존 토큰이 유효해도 재발급 / refresh even if current token is valid",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="~/.aws/config 에 쓰지 않고 표준출력 / print INI instead of writing",
    )
    pager = p.add_mutually_exclusive_group()
    pager.add_argument(
        "--cli-pager",
        dest="cli_pager",
        action="store_true",
        default=None,
        help="세션에 `cli_pager =` 추가 (프롬프트 스킵) / add `cli_pager =` (skip prompt)",
    )
    pager.add_argument(
        "--no-cli-pager",
        dest="cli_pager",
        action="store_false",
        default=None,
        help="`cli_pager =` 추가 안함 (프롬프트 스킵) / do not add `cli_pager =` (skip prompt)",
    )
    p.add_argument(
        "--setup",
        action="store_true",
        help="대화형 설정 마법사 실행 / run the interactive setup wizard",
    )
    p.add_argument(
        "--edit",
        action="store_true",
        help="선택한 프로필만 대화형으로 수정 (-p 필수) / edit selected profiles "
             "(requires -p)",
    )
    p.add_argument(
        "--list",
        action="store_true",
        help="설정된 사용자 프로필 목록 / list profiles from user config and exit",
    )
    p.add_argument(
        "--list-aws",
        action="store_true",
        help="~/.aws/config 프로필 목록 / list AWS profiles and exit",
    )
    p.add_argument(
        "--check",
        action="store_true",
        help="종합 진단: 사용자 설정 / AWS 프로필 / 토큰 유효성 점검 후 종료 "
             "(STS 미호출, 파일 미변경) / diagnose config, AWS profile, and token "
             "validity without calling STS or writing files",
    )
    p.add_argument(
        "--clean",
        action="store_true",
        help="도구가 만든 파일/섹션 삭제 (사용자 설정 JSON + ~/.aws/config 의 "
             "profile sts-* 섹션). --dry-run 과 함께 쓰면 미리보기만 / "
             "delete tool-created files/sections (user JSON + sts-* sections); "
             "use with --dry-run to preview",
    )
    p.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="DEBUG 로그까지 출력 / include DEBUG logs",
    )
    p.add_argument(
        "-q", "--quiet",
        action="store_true",
        help="경고/오류만 출력 / suppress INFO logs (warnings + errors only)",
    )
    return p


def _configure_logging(verbose: bool, quiet: bool) -> None:
    if verbose:
        set_level("DEBUG")
    elif quiet:
        set_level("WARN")
    else:
        set_level("INFO")


def _sections_from(creds_by_profile: dict[str, StsCredentials]) -> Iterable[tuple[str, dict]]:
    for name, creds in creds_by_profile.items():
        yield f"profile sts-{name}", creds.to_ini_dict()


def _print_ini(
    creds_by_profile: dict[str, StsCredentials],
    add_cli_pager: bool = True,
) -> None:
    parser = ConfigParser()
    for section, kv in _sections_from(creds_by_profile):
        parser.add_section(section)
        for k, v in kv.items():
            parser.set(section, k, v)
        if add_cli_pager:
            parser.set(section, "cli_pager", "")
    buf = StringIO()
    parser.write(buf)
    print(buf.getvalue())


def _resolve_cli_pager(flag: Optional[bool]) -> bool:
    """--cli-pager/--no-cli-pager 우선, 저장된 응답, 프롬프트 순으로 결정.
    Flag > saved preference > prompt. Non-TTY defaults to True.
    """
    if flag is not None:
        return flag
    prefs = load_preferences()
    saved = prefs.get("add_cli_pager")
    if isinstance(saved, bool):
        return saved
    if not sys.stdin.isatty():
        return True
    ans = input(f"  {arrow()} " + c_prompt(L(
        "각 sts 프로필에 `cli_pager =` 를 추가할까요? [Y/n]: ",
        "add `cli_pager =` to each sts profile? [Y/n]: ",
    ))).strip().lower()
    result = ans in ("", "y", "yes")
    save_preference("add_cli_pager", result)
    log.info(L(
        f"응답 저장됨 → 다음부터 묻지 않음 ({PREFERENCES_PATH}). "
        "재질문하려면 파일 삭제 또는 --cli-pager/--no-cli-pager 사용",
        f"saved → won't ask again ({PREFERENCES_PATH}). "
        "delete the file or pass --cli-pager/--no-cli-pager to override",
    ))
    return result


def process_profile(
    name: str,
    cfg: ProfileConfig,
    duration: int,
    force: bool,
) -> tuple[str, Optional[StsCredentials]]:
    """단일 프로필 처리 / Handle one profile.

    Returns ``(status, credentials)`` where status is one of
    ``"refreshed"``, ``"valid"``, ``"missing"``.
    """
    if not profile_exists(name):
        log.warn(L(
            f"[{name}] AWS 프로필 미설정, 건너뜀. `aws configure --profile {name}` 로 등록",
            f"[{name}] not configured, skipping. Run `aws configure --profile {name}`",
        ))
        return "missing", None

    if not force and existing_token_valid(name):
        log.info(L(
            f"[{name}] 기존 토큰 유효 - 갱신 생략",
            f"[{name}] valid token exists, skipping",
        ))
        return "valid", None

    creds = get_session_token(
        profile_name=name,
        mfa_serial=cfg.mfa_serial or None,
        totp_secret=cfg.totp_secret or None,
        duration=duration,
    )
    log.info(L(
        f"[{name}] 새 토큰 발급 (만료: {creds.expiration_iso})",
        f"[{name}] new token issued (expires {creds.expiration_iso})",
    ))
    return "refreshed", creds


def main(argv: Optional[list[str]] = None) -> int:
    try:
        rc = _run(argv)
    except KeyboardInterrupt:
        print()
        print(L("취소됨 (Ctrl+C)", "aborted (Ctrl+C)"))
        return 130
    except EOFError:
        print()
        print(L("입력 종료 (EOF)", "aborted (EOF)"))
        return 130

    try:
        newer = check_for_update()
        if newer:
            print()
            print(c_warn(L(
                f"⚠  새 버전 사용 가능: {newer} (현재 {__version__})",
                f"[!] new version available: {newer} (current {__version__})",
            )))
            print(c_dim(L(
                "   업데이트: pip install -U awsgetsts   "
                "(끄기: AWSGETSTS_NO_UPDATE_CHECK=1)",
                "   update:  pip install -U awsgetsts   "
                "(disable: AWSGETSTS_NO_UPDATE_CHECK=1)",
            )))
    except Exception:
        pass

    return rc


def _run(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    _configure_logging(args.verbose, args.quiet)

    if args.list_aws:
        for p in list_aws_profiles():
            print(p)
        return 0

    if args.setup:
        target = resolve_config_path(args.config)
        if target.exists():
            print(L(
                f"⚠  이미 설정 파일이 있습니다: {target}",
                f"[!] config already exists: {target}",
            ))
            ans = input("   " + L("덮어쓸까요? [y/N]: ",
                                  "overwrite? [y/N]: ")).strip().lower()
            if ans not in ("y", "yes"):
                print("   " + L("취소됨.", "aborted."))
                return 0
        run_wizard(target)
        return 0

    if args.edit:
        if not args.profile:
            log.error(L(
                "--edit 는 -p/--profile 지정이 필요합니다",
                "--edit requires -p/--profile",
            ))
            return 2
        return run_edit(resolve_config_path(args.config), list(args.profile))

    if args.clean:
        return run_clean(
            resolve_config_path(args.config),
            profiles=list(args.profile) if args.profile else None,
            dry_run=args.dry_run,
        )

    try:
        user_cfg = load_config(args.config, create_if_missing=False)
    except FileNotFoundError as e:
        if args.check:
            log.warn(L(
                f"설정 로드 실패, 사용자 설정 없이 진단 진행: {e}",
                f"config load failed, diagnosing without user config: {e}",
            ))
            user_cfg = {}
        else:
            log.error(L(
                f"설정 파일이 없습니다: {e}",
                f"config file not found: {e}",
            ))
            print(L(
                "  → `./awsgetsts.py --setup` 으로 설정을 생성하거나",
                f"  {arrow()} run `./awsgetsts.py --setup` to create config, or",
            ))
            print(L(
                "     `./awsgetsts.py --check` 로 상태를 점검하세요.",
                "     `./awsgetsts.py --check` to diagnose.",
            ))
            return 2
    except (OSError, ValueError) as e:
        if args.check:
            log.warn(L(
                f"설정 로드 실패, 사용자 설정 없이 진단 진행: {e}",
                f"config load failed, diagnosing without user config: {e}",
            ))
            user_cfg = {}
        else:
            log.error(L(f"설정 로드 실패: {e}", f"failed to load config: {e}"))
            return 2

    if args.list:
        for name in user_cfg:
            print(name)
        return 0

    targets = args.profile or list(user_cfg.keys())

    if args.check:
        user_cfg_path = resolve_config_path(args.config)
        rc = run_check(targets, user_cfg, user_cfg_path)
        if not user_cfg_path.exists():
            print()
            ans = input(f"  {arrow()} " + L(
                "설정 파일이 없습니다. 지금 마법사로 생성할까요? [y/N]: ",
                "config missing. Run setup wizard now? [y/N]: ",
            )).strip().lower()
            if ans in ("y", "yes"):
                run_wizard(user_cfg_path)
        return rc

    unknown = [t for t in targets if t not in user_cfg]
    if unknown:
        log.error(L(
            f"알 수 없는 프로필: {', '.join(unknown)}",
            f"unknown profile(s): {', '.join(unknown)}",
        ))
        return 2

    results: dict[str, StsCredentials] = {}
    summary = {"refreshed": [], "valid": [], "missing": [], "failed": []}
    exit_code = 0
    for i, name in enumerate(targets):
        if i > 0:
            print()  # 프로필 블록 구분 / blank line between profiles
        try:
            status, creds = process_profile(
                name, user_cfg[name], args.duration, args.force
            )
        except Exception as e:  # boto/value errors already logged in sts.py
            log.error(L(f"[{name}] 발급 실패: {e}", f"[{name}] failed: {e}"))
            summary["failed"].append(name)
            exit_code = 1
            continue
        summary[status].append(name)
        if creds is not None:
            results[name] = creds

    print()  # 요약 앞 빈 줄 / spacer before summary
    if results:
        add_cli_pager = _resolve_cli_pager(args.cli_pager)
        if args.dry_run:
            _print_ini(results, add_cli_pager=add_cli_pager)
        else:
            write_sts_sections(_sections_from(results), add_cli_pager=add_cli_pager)
            log.info(L(
                f"~/.aws/config 갱신됨: {len(results)}개 섹션 기록",
                f"wrote {len(results)} section(s) to {AWS_CONFIG_PATH}",
            ))

    _print_summary(summary, dry_run=args.dry_run)
    return exit_code


# Local aliases mapping semantic --check statuses to the shared palette.
_ok = c_ok
_bad = c_err
_warn = c_warn
_dim = c_dim


def _short_path(p: Path) -> str:
    """홈 경로를 ~ 로 축약 / Abbreviate $HOME to ~."""
    s = str(p)
    home = str(Path.home())
    return "~" + s[len(home):] if s.startswith(home) else s


def run_check(targets: list[str], user_cfg: dict, user_cfg_path: Path) -> int:
    """종합 진단 / Diagnose profiles without side effects."""
    print("[awsgetsts --check] " + L("프로필 진단", "profile diagnosis"))
    print(hrule_light() * 60)

    print("Files")
    for label, path in (
        ("user_config", user_cfg_path),
        ("aws_config", AWS_CONFIG_PATH),
        ("aws_credentials", AWS_CREDENTIALS_PATH),
    ):
        exists = path.exists()
        status = _ok("FOUND") if exists else _bad("MISSING")
        print(f"  {label:<16} : {status}  {_dim(_short_path(path))}")
    print(hrule_light() * 60)

    if not targets:
        log.warn(L(
            "점검할 프로필이 없습니다 (hint: -p <name> 또는 --setup)",
            "no profiles to check (hint: use -p <name> or run --setup)",
        ))
        return 0

    stats = {"valid": 0, "expired": 0, "no_token": 0,
             "missing_user_cfg": 0, "missing_aws_profile": 0}

    name_width = max((len(n) for n in targets), default=0)

    for name in targets:
        user_ok = name in user_cfg
        aws_ok = profile_exists(name)
        state, exp = token_info(name)

        user_part = f"user_config={_ok('OK') if user_ok else _bad('MISSING')}"
        aws_part = f"aws_profile={_ok('OK') if aws_ok else _bad('MISSING')}"

        if state == "valid":
            token_part = f"token={_ok('VALID')} {_dim(f'(until {convert_to_kst(exp)})')}"
            stats["valid"] += 1
        elif state == "expired":
            token_part = f"token={_bad('EXPIRED')} {_dim(f'({convert_to_kst(exp)})')}"
            stats["expired"] += 1
        elif state == "invalid":
            token_part = f"token={_bad('INVALID_EXPIRATION')}"
            stats["no_token"] += 1
        else:
            token_part = f"token={_warn('NONE')}"
            stats["no_token"] += 1

        if not user_ok:
            stats["missing_user_cfg"] += 1
        if not aws_ok:
            stats["missing_aws_profile"] += 1

        label = f"[{name}]".ljust(name_width + 2)
        print(f"{label} {user_part} | {aws_part} | {token_part}")

    print(hrule_light() * 60)
    print("[awsgetsts --check] Summary")

    def _fmt(label: str, value: int, colorize) -> str:
        v = colorize(str(value)) if value else _dim(str(value))
        return f"  {label:<22} : {v}"

    print(_fmt("total", len(targets), _dim))
    print(_fmt("valid", stats["valid"], _ok))
    print(_fmt("expired", stats["expired"], _bad))
    print(_fmt("no_token", stats["no_token"], _warn))
    print(_fmt("missing_user_cfg", stats["missing_user_cfg"], _bad))
    print(_fmt("missing_aws_profile", stats["missing_aws_profile"], _bad))
    return 0


def run_clean(
    user_cfg_path: Path,
    profiles: Optional[list[str]] = None,
    dry_run: bool = False,
) -> int:
    """도구가 만든 파일/섹션 삭제 / Remove tool-created config & sts-* sections.

    ``profile sts-<name>`` 은 ``awsUserConfig.json`` 의 프로필 이름과 매칭되는
    것만 도구 생성으로 판정. ``profiles`` 가 주어지면 그 목록만 대상.
    ``profiles`` 지정 시 사용자 설정 파일은 삭제하지 않고 해당 키만 제거.
    """
    print("[awsgetsts --clean] " + L("삭제 대상", "targets"))
    print(hrule_light() * 60)

    cfg_exists = user_cfg_path.exists()

    cfg_data: dict = {}
    if cfg_exists:
        try:
            with user_cfg_path.open("r", encoding="utf-8") as f:
                cfg_data = json.load(f)
        except (OSError, ValueError) as e:
            log.warn(L(
                f"사용자 설정 파싱 실패, sts 섹션은 건너뜀: {e}",
                f"failed to parse user config, skipping sts sections: {e}",
            ))
            cfg_data = {}

    managed_names: set[str] = set(cfg_data.keys())

    # 필터: -p 로 대상 지정된 경우
    if profiles:
        unknown = [p for p in profiles if p not in managed_names]
        if unknown:
            log.error(L(
                f"알 수 없는 프로필: {', '.join(unknown)}",
                f"unknown profile(s): {', '.join(unknown)}",
            ))
            return 2
        target_names = set(profiles)
        partial = True
    else:
        target_names = managed_names
        partial = False

    all_sts = list_sts_sections(AWS_CONFIG_PATH)
    if managed_names:
        managed = [s for s in all_sts
                   if s.removeprefix("profile sts-") in target_names]
        skipped = [s for s in all_sts if s not in managed]
    else:
        managed = []
        skipped = all_sts

    if partial:
        print(f"  {L('대상 프로필', 'target profiles'):<20}: "
              f"{', '.join(sorted(target_names))}")
        print(f"  {L('사용자 설정 항목', 'user config keys'):<20}: "
              f"{L('선택 프로필만 제거 (파일 유지)', 'remove selected keys only (file kept)')}")
    else:
        print(f"  {L('사용자 설정', 'user config'):<20}: "
              f"{_ok('FOUND') if cfg_exists else _dim('(none)')}  "
              f"{_dim(_short_path(user_cfg_path))}")
    print(f"  {L('삭제할 sts 섹션', 'sts sections to remove'):<20}: {len(managed)}")
    for s in managed:
        print(f"      - {_dim(s)}")
    if skipped:
        print(f"  {L('유지할 sts 섹션', 'sts sections to keep'):<20}: {len(skipped)} "
              + _dim(L("(대상 아님)", "(not targeted)")))
        for s in skipped:
            print(f"      - {_dim(s)}")
    print(hrule_light() * 60)

    if not partial and not cfg_exists and not managed:
        print(L("삭제할 항목이 없습니다.", "nothing to delete."))
        return 0
    if partial and not target_names and not managed:
        print(L("삭제할 항목이 없습니다.", "nothing to delete."))
        return 0

    if dry_run:
        print(L("(dry-run — 실제 삭제하지 않음)",
                "(dry-run - nothing was deleted)"))
        return 0

    ans = input(f"  {arrow()} " + c_prompt(L(
        "위 항목을 삭제할까요? [y/N]: ",
        "delete the above? [y/N]: ",
    ))).strip().lower()
    if ans not in ("y", "yes"):
        print(L("취소됨.", "aborted."))
        return 0

    removed_cfg = False
    removed_keys: list[str] = []
    if partial:
        # 사용자 설정 파일에서 해당 키만 제거
        for p in sorted(target_names):
            if p in cfg_data:
                cfg_data.pop(p)
                removed_keys.append(p)
        if removed_keys:
            try:
                with user_cfg_path.open("w", encoding="utf-8") as f:
                    json.dump(cfg_data, f, indent=4, ensure_ascii=False)
                user_cfg_path.chmod(0o600)
            except OSError as e:
                log.error(L(
                    f"사용자 설정 저장 실패: {e}",
                    f"failed to save user config: {e}",
                ))
                return 1
    elif cfg_exists:
        try:
            user_cfg_path.unlink()
            removed_cfg = True
        except OSError as e:
            log.error(L(
                f"사용자 설정 삭제 실패: {e}",
                f"failed to delete user config: {e}",
            ))
            return 1

    removed_sections = 0
    if managed:
        removed_sections = remove_sts_sections(managed, AWS_CONFIG_PATH)

    print(hrule_light() * 60)
    print("[awsgetsts --clean] " + L("완료", "done"))
    if removed_cfg:
        print(f"  {_ok(L('삭제됨', 'removed'))}: {_short_path(user_cfg_path)}")
    if removed_keys:
        print(f"  {_ok(L('제거된 사용자 설정 키', 'removed user config keys'))}: "
              f"{', '.join(removed_keys)}")
    print(f"  {_ok(L('제거된 sts 섹션', 'removed sts sections'))}: {removed_sections}")
    return 0


def _print_summary(summary: dict[str, list[str]], dry_run: bool) -> None:
    """항상 표시되는 최종 요약 / Always-visible final summary."""
    parts = []
    if summary["refreshed"]:
        verb = "would refresh" if dry_run else "refreshed"
        parts.append(f"{verb}: {', '.join(summary['refreshed'])}")
    if summary["valid"]:
        parts.append(f"still valid (skipped): {', '.join(summary['valid'])}")
    if summary["missing"]:
        parts.append(f"missing in ~/.aws/config: {', '.join(summary['missing'])}")
    if summary["failed"]:
        parts.append(f"failed: {', '.join(summary['failed'])}")
    if not parts:
        parts.append("no profiles processed")
    print("[awsgetsts] " + " | ".join(parts))


if __name__ == "__main__":
    sys.exit(main())