"""최초 설정 마법사 / First-run setup wizard.

친절한 프롬프트로 aws_user_config.json 을 생성합니다.
Guides the user through creating aws_user_config.json interactively.
"""

from __future__ import annotations

import getpass
import json
import re
from pathlib import Path
from typing import Callable, Optional

import pyotp

from .aws_config import profile_exists, upsert_base_profile
from .lang import (
    L,
    arrow,
    bullet,
    c_bad,
    c_ok,
    c_prompt,
    hrule_heavy,
    hrule_light,
    ok_mark,
    warn_mark,
)
from .logger import log

ACCOUNT_ID_RE = re.compile(r"^\d{12}$")
MFA_ARN_RE = re.compile(r"^arn:aws:iam::\d{12}:mfa/.+$")
BASE32_RE = re.compile(r"^[A-Z2-7]+=*$")
ACCESS_KEY_RE = re.compile(r"^AKIA[0-9A-Z]{16}$|^ASIA[0-9A-Z]{16}$")
SECRET_KEY_RE = re.compile(r"^[A-Za-z0-9/+=]{40}$")


# -----------------------------------------------------------------------------
# UI helpers
# -----------------------------------------------------------------------------

def _rule(char: Optional[str] = None) -> str:
    return (char or hrule_light()) * 60


def _header(title_ko: str, title_en: str) -> None:
    print(_rule(hrule_heavy()))
    print(f" {L(title_ko, title_en)}")
    print(_rule(hrule_heavy()))


def _section(title_ko: str, title_en: str) -> None:
    light = hrule_light()
    print()
    print(f"{light}{light} {L(title_ko, title_en)} " + light * 8)


def _hint(msg_ko: str, msg_en: str = "") -> None:
    msg = L(msg_ko, msg_en or msg_ko)
    print(f"  {bullet()} {msg}")


def _ask(
    prompt: str,
    validate: Optional[Callable[[str], Optional[str]]] = None,
    allow_empty: bool = False,
) -> str:
    """검증하며 재입력 / Prompt with validation loop."""
    while True:
        value = input(f"  {arrow()} {prompt}: ").strip()
        if not value and not allow_empty:
            print(c_bad("     ! " + L("빈 값 불가", "value required")))
            continue
        if validate is not None and value:
            err = validate(value)
            if err:
                print(c_bad(f"     ! {err}"))
                continue
        return value


# -----------------------------------------------------------------------------
# Validators
# -----------------------------------------------------------------------------

def _v_profile_name(v: str) -> Optional[str]:
    if not re.match(r"^[A-Za-z0-9._-]+$", v):
        return L("영문/숫자/._- 만 허용", "letters, digits, . _ - only")
    return None


def _v_account_id(v: str) -> Optional[str]:
    v_clean = v.replace("-", "").replace(" ", "")
    if not ACCOUNT_ID_RE.match(v_clean):
        return L("12자리 숫자여야 합니다", "must be exactly 12 digits")
    return None


def _v_mfa_serial(v: str) -> Optional[str]:
    if not MFA_ARN_RE.match(v):
        return L(
            "형식: arn:aws:iam::<account_id>:mfa/<username>",
            "expected AWS MFA device ARN",
        )
    return None


def _v_totp_secret(v: str) -> Optional[str]:
    clean = v.replace(" ", "").upper()
    if not BASE32_RE.match(clean):
        return L("TOTP secret 은 base32(A-Z, 2-7)여야 합니다",
                 "TOTP secret must be base32")
    try:
        pyotp.TOTP(clean).now()
    except Exception as e:
        return L(f"TOTP 생성 실패: {e}", f"TOTP generation failed: {e}")
    return None


def _v_access_key(v: str) -> Optional[str]:
    if not ACCESS_KEY_RE.match(v):
        return L("형식: AKIA... 또는 ASIA... (20자)",
                 "expected AWS access key ID (AKIA.../ASIA...)")
    return None


def _v_secret_key(v: str) -> Optional[str]:
    if not SECRET_KEY_RE.match(v):
        return L("40자 base64 문자열", "expected 40-char AWS secret access key")
    return None


def _v_region(v: str) -> Optional[str]:
    if not re.match(r"^[a-z]{2}-[a-z]+-\d+$", v):
        return L("형식: ap-northeast-2 처럼", "expected format like ap-northeast-2")
    return None


# -----------------------------------------------------------------------------
# Wizard
# -----------------------------------------------------------------------------

def _welcome(target: Path) -> None:
    _header(
        "awsgetsts 최초 설정 마법사",
        "awsgetsts first-run setup wizard",
    )
    print("  " + L(
        "AWS STS 임시 자격증명을 발급하여 ~/.aws/config 의",
        "Issues STS tokens (MFA/TOTP) into ~/.aws/config",
    ))
    print("  " + L(
        "'profile sts-<name>' 로 저장합니다.",
        "as 'profile sts-<name>' sections.",
    ))
    print(f"  config: {target}")


def _ask_profile_name() -> Optional[str]:
    _section("프로필 이름", "Profile name")
    _hint("AWS 계정 별칭 (예: dev, prod, sandbox)",
          "short alias, matches ~/.aws/config profile (e.g. dev, prod)")
    _hint("완료하려면 Enter", "press Enter to finish")
    name = input(f"  {arrow()} " + L("프로필 이름", "profile name") + ": ").strip()
    if not name:
        return None
    err = _v_profile_name(name)
    if err:
        print(c_bad(f"     ! {err}"))
        return _ask_profile_name()
    return name


def _ask_account_id(profile: str) -> str:
    _section(f"[{profile}] Account ID", f"[{profile}] AWS Account ID")
    _hint("AWS 콘솔 우측 상단 계정 메뉴 (12자리)",
          "top-right of AWS Console; 12 digits")
    return _ask("Account ID", validate=_v_account_id).replace("-", "").replace(" ", "")


def _ask_mfa_serial(profile: str, account_id: str) -> str:
    _section(f"[{profile}] MFA Serial", f"[{profile}] MFA device ARN")
    _hint("IAM > Users > (본인) > Security credentials > Assigned MFA device",
          "IAM > Users > <you> > Security credentials > Assigned MFA device")
    _hint(f"형식: arn:aws:iam::{account_id}:mfa/<your-username>",
          f"format: arn:aws:iam::{account_id}:mfa/<your-username>")
    return _ask("MFA Serial", validate=_v_mfa_serial)


def _ask_totp_secret(profile: str) -> str:
    _section(f"[{profile}] TOTP Secret", f"[{profile}] TOTP shared secret")
    _hint("MFA 등록 화면의 'Show secret key' 값 (base32)",
          "'Show secret key' shown when registering MFA (base32)")
    _hint("미저장 상태면 재등록 필요, 공백/대소문자 무관",
          "re-register if not saved; spaces & case ignored")
    raw = _ask("TOTP Secret", validate=_v_totp_secret)
    secret = raw.replace(" ", "").upper()

    code = pyotp.TOTP(secret).now()
    print(f"  {ok_mark()} " + L(f"현재 TOTP 코드: {code}", f"current TOTP code: {code}"))
    print("    " + L("인증앱의 값과 일치하는지 확인",
                     "verify against your authenticator app"))
    confirm = input(f"  {arrow()} " + L("일치합니까? [Y/n]: ",
                                        "matches? [Y/n]: ")).strip().lower()
    if confirm in ("", "y", "yes"):
        return secret
    print(c_bad("     ! " + L("다시 입력해주세요.", "please re-enter.")))
    return _ask_totp_secret(profile)


def _ensure_base_aws_profile(profile: str) -> None:
    """base AWS 프로필 존재 확인 후 없으면 inline 등록.

    Ensure the base AWS profile exists; if missing, prompt for keys
    and write to ~/.aws/credentials + ~/.aws/config directly.
    """
    if profile_exists(profile):
        print(f"  {ok_mark()} " + L(
            f"~/.aws/config 에 '{profile}' 프로필 존재",
            f"~/.aws/config profile '{profile}' already configured",
        ))
        return

    print()
    print(f"  {warn_mark()}  " + L(
        f"'{profile}' 이 ~/.aws/config 에 없습니다",
        f"base AWS profile '{profile}' is missing",
    ))
    ans = input(f"  {arrow()} " + L(
        "지금 등록할까요? (writes ~/.aws/{config,credentials}) [Y/n]: ",
        "configure now (writes ~/.aws/{config,credentials})? [Y/n]: ",
    )).strip().lower()
    if ans not in ("", "y", "yes"):
        print("     " + L(
            f"건너뜁니다. 나중에 `aws configure --profile {profile}` 실행",
            f"skipping. Run `aws configure --profile {profile}` later.",
        ))
        return

    _section(f"[{profile}] AWS Access Key", f"[{profile}] AWS Access Key ID")
    _hint("IAM > Users > (본인) > Security credentials > Access keys",
          "IAM > Users > <you> > Security credentials > Access keys")
    _hint("형식: AKIA... 또는 ASIA... (20자)",
          "AKIA... or ASIA... (20 chars)")
    access_key = _ask("Access Key ID", validate=_v_access_key)

    _section(f"[{profile}] Secret Access Key",
             f"[{profile}] AWS Secret Access Key")
    _hint("입력은 화면에 표시되지 않습니다", "input hidden (getpass)")
    while True:
        try:
            secret = getpass.getpass(f"  {arrow()} Secret Access Key: ").strip()
        except (EOFError, KeyboardInterrupt):
            raise SystemExit(L("취소됨", "aborted"))
        err = _v_secret_key(secret)
        if err:
            print(c_bad(f"     ! {err}"))
            continue
        break

    _section(f"[{profile}] Region / Output", f"[{profile}] region / output")
    _hint("기본값: ap-northeast-2, json", "defaults: ap-northeast-2, json")
    region = input(f"  {arrow()} region [ap-northeast-2]: ").strip() or "ap-northeast-2"
    err = _v_region(region)
    if err:
        print(c_bad(f"     ! {err} - " + L("기본값 ap-northeast-2 사용",
                                            "using default ap-northeast-2")))
        region = "ap-northeast-2"
    output = input(f"  {arrow()} output [json]: ").strip() or "json"

    upsert_base_profile(
        profile_name=profile,
        access_key_id=access_key,
        secret_access_key=secret,
        region=region,
        output=output,
    )
    print(f"  {ok_mark()} " + L(
        f"[{profile}] ~/.aws/{{credentials,config}} 등록 완료",
        f"[{profile}] wrote base profile to ~/.aws/{{credentials,config}}",
    ))


def _add_more() -> bool:
    print()
    ans = input(f"  {arrow()} " + c_prompt(L(
        "프로필을 더 추가할까요? [y/N]: ",
        "add another profile? [y/N]: ",
    ))).strip().lower()
    return ans in ("y", "yes")


def _next_steps(target: Path) -> None:
    print()
    print(_rule(hrule_heavy()))
    print(" " + L("설정 완료", "setup complete"))
    print(_rule(hrule_heavy()))
    print(f"  {bullet()} config: {target}")
    print(f"  {bullet()} " + L("STS 토큰 발급:", "issue tokens:"))
    print("      ./awsgetsts.py                  # " + L("모든 프로필", "all profiles"))
    print("      ./awsgetsts.py -p <name>        # " + L("특정 프로필", "one profile"))
    print("      ./awsgetsts.py --dry-run        # " + L("미리보기", "preview"))
    print()


def run_wizard(target: Path) -> dict:
    """대화형 마법사 실행, dict 반환 / Run wizard, return config dict."""
    _welcome(target)

    result: dict = {}
    while True:
        name = _ask_profile_name()
        if name is None:
            if result:
                break
            print(c_bad("  ! " + L("최소 1개 이상의 프로필이 필요합니다",
                                    "at least one profile required")))
            continue

        if name in result:
            print(c_bad("     ! " + L(f"이미 추가된 이름: {name}",
                                       f"already added: {name}")))
            continue

        # base AWS 프로필 먼저 확인/등록 / verify or add base AWS profile first
        _ensure_base_aws_profile(name)

        account_id = _ask_account_id(name)
        mfa_serial = _ask_mfa_serial(name, account_id)
        totp_secret = _ask_totp_secret(name)

        result[name] = {
            "account_id": account_id,
            "profile": name,
            "mfa_serial": mfa_serial,
            "totp_secret": totp_secret,
        }
        print()
        print(f"  {ok_mark()} " + c_ok(L(f"[{name}] 추가 완료", f"[{name}] added")))

        if not _add_more():
            break

    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as f:
        json.dump(result, f, indent=4, ensure_ascii=False)
    try:
        target.chmod(0o600)
    except OSError as e:  # non-fatal
        log.warn(f"chmod 0600 failed for {target}: {e}")

    _next_steps(target)
    return result


# -----------------------------------------------------------------------------
# Edit (partial update)
# -----------------------------------------------------------------------------

def _mask_secret(s: str) -> str:
    """TOTP secret 을 마스킹 / Mask TOTP secret for display."""
    if not s:
        return ""
    if len(s) <= 6:
        return "*" * len(s)
    return s[:2] + "*" * (len(s) - 4) + s[-2:]


def _ask_keep(
    prompt: str,
    current: str,
    validate: Optional[Callable[[str], Optional[str]]] = None,
    hidden: bool = False,
    display_current: Optional[str] = None,
) -> str:
    """현재값 표시 후 Enter 시 유지 / Prompt with current-value default."""
    shown = display_current if display_current is not None else current
    hint = f" [{shown}]" if shown else ""
    while True:
        if hidden:
            raw = getpass.getpass(f"  {arrow()} {prompt}{hint}: ").strip()
        else:
            raw = input(f"  {arrow()} {prompt}{hint}: ").strip()
        if not raw:
            return current
        if validate is not None:
            err = validate(raw)
            if err:
                print(c_bad(f"     ! {err}"))
                continue
        return raw


def run_edit(target: Path, profile_names: list[str]) -> int:
    """지정한 프로필만 대화형 수정 / Edit selected profiles in place.

    반환값: 종료 코드 (0=성공, 2=대상 프로필 없음)
    """
    if not target.exists():
        log.error(L(
            f"설정 파일이 없습니다: {target}",
            f"config file not found: {target}",
        ))
        return 2

    with target.open("r", encoding="utf-8") as f:
        cfg = json.load(f)

    unknown = [n for n in profile_names if n not in cfg]
    if unknown:
        log.error(L(
            f"알 수 없는 프로필: {', '.join(unknown)}",
            f"unknown profile(s): {', '.join(unknown)}",
        ))
        return 2

    _header("awsgetsts 프로필 수정", "awsgetsts profile edit")
    print("  " + L(
        f"config: {target}",
        f"config: {target}",
    ))
    print("  " + L("각 필드에서 Enter 를 누르면 기존값 유지.",
                   "press Enter at any field to keep the current value."))

    changed = 0
    for name in profile_names:
        cur = cfg[name]
        _section(f"[{name}]", f"[{name}]")

        new_account = _ask_keep("Account ID",
                                cur.get("account_id", ""),
                                validate=_v_account_id)
        new_account = new_account.replace("-", "").replace(" ", "")

        new_mfa = _ask_keep("MFA Serial",
                            cur.get("mfa_serial", ""),
                            validate=_v_mfa_serial)

        cur_totp = cur.get("totp_secret", "")
        new_totp = _ask_keep(
            "TOTP Secret",
            cur_totp,
            validate=_v_totp_secret,
            hidden=True,
            display_current=_mask_secret(cur_totp) if cur_totp else "",
        )
        new_totp = new_totp.replace(" ", "").upper()

        # 확인용 코드 (변경되었을 때만)
        if new_totp != cur_totp:
            code = pyotp.TOTP(new_totp).now()
            print(f"  {ok_mark()} " + L(f"현재 TOTP 코드: {code}",
                                        f"current TOTP code: {code}"))
            print("    " + L("인증앱 값과 일치하는지 확인",
                             "verify against your authenticator app"))

        new_entry = {
            "account_id": new_account,
            "profile": cur.get("profile", name),
            "mfa_serial": new_mfa,
            "totp_secret": new_totp,
        }
        if new_entry != cur:
            cfg[name] = new_entry
            changed += 1
            print(f"  {ok_mark()} " + c_ok(L(f"[{name}] 수정됨",
                                             f"[{name}] updated")))
        else:
            print(f"  {bullet()} " + L(f"[{name}] 변경 없음",
                                        f"[{name}] no change"))

    if changed == 0:
        print()
        print(L("변경 사항 없음. 파일을 그대로 둡니다.",
                "no changes; file left untouched."))
        return 0

    with target.open("w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=4, ensure_ascii=False)
    try:
        target.chmod(0o600)
    except OSError as e:
        log.warn(f"chmod 0600 failed for {target}: {e}")

    print()
    print(f"  {ok_mark()} " + c_ok(L(f"{changed}개 프로필 저장 완료",
                                     f"saved {changed} profile(s)")))
    print(f"  {bullet()} " + L("`./awsgetsts.py -p <name> --force` 로 즉시 재발급",
                                "use `./awsgetsts.py -p <name> --force` to refresh now"))
    return 0