# awsgetsts

AWS STS 임시 자격증명(session token)을 MFA/TOTP 와 함께 자동으로 발급·갱신하여
`~/.aws/config` 에 `[profile sts-<name>]` 섹션으로 저장하는 CLI 유틸리티.

Small CLI that issues AWS STS session tokens (with MFA/TOTP) and merges
them into `~/.aws/config` as `[profile sts-<name>]` sections.

## 특징 / Features

- MFA 디바이스 ARN + TOTP shared secret 으로 `sts:GetSessionToken` 호출
- 기존 토큰 유효기간 확인 후 만료 임박 시에만 갱신 (`--force` 로 강제)
- 다중 프로필 지원, `--profile` 반복 지정 가능
- 대화형 마법사(`--setup`)로 최초 설정 파일 생성
- 종합 진단(`--check`): 설정 파일 / AWS 프로필 / 토큰 유효성 점검
- 한/영 자동 전환 (터미널 UTF-8 지원 여부 기준, `AWSGETSTS_LANG` 로 강제)
- 컬러 상태 표시 (TTY 자동 감지, `FORCE_COLOR` / `NO_COLOR` 존중)

## 요구사항 / Requirements

- Python 3.9+
- 의존성 / dependencies: `boto3`, `pyotp`, `python-dateutil`, `cplogger`, `termcolor`

설치 / install:

```
pip install boto3 pyotp python-dateutil cplogger termcolor
```

## 빠른 시작 / Quick start

```
# 최초 설정 (대화형 마법사) / first-time setup
./awsgetsts.py --setup

# 상태 진단 / diagnose
./awsgetsts.py --check

# 모든 프로필 갱신 / refresh all profiles
./awsgetsts.py

# 특정 프로필만 / one profile
./awsgetsts.py -p dev

# 강제 재발급 / force refresh
./awsgetsts.py --force

# 파일에 쓰지 않고 미리보기 / dry-run
./awsgetsts.py --dry-run

# 특정 프로필만 수정 / edit one profile
./awsgetsts.py --edit -p dev

# 특정 프로필만 삭제 / delete one profile
./awsgetsts.py --clean -p dev
```

## CLI 옵션 / Options

| 옵션 | 설명 |
| --- | --- |
| -p, --profile NAME | 대상 프로필 (반복 가능). 생략 시 설정된 모든 프로필. |
| -c, --config PATH | 사용자 설정 파일 경로 |
| -d, --duration SEC | 세션 지속시간(초). 기본값은 sts 모듈의 DEFAULT_DURATION |
| --force | 기존 토큰이 유효해도 재발급 |
| --dry-run | ~/.aws/config 에 쓰지 않고 표준출력에 INI 출력 |
| --cli-pager | 각 sts 섹션에 `cli_pager =` 추가 (프롬프트 스킵) |
| --no-cli-pager | `cli_pager =` 추가 안 함 (프롬프트 스킵) |
| --setup | 대화형 설정 마법사 실행 |
| --edit | 선택한 프로필만 대화형으로 수정 (`-p` 필수, 필드별 Enter로 기존값 유지) |
| --list | 사용자 설정 파일의 프로필 이름 목록 |
| --list-aws | ~/.aws/config 의 프로필 이름 목록 |
| --check | 설정 / AWS 프로필 / 토큰 유효성 진단 (STS 미호출) |
| --clean | 도구가 만든 파일 삭제 (사용자 JSON + `awsUserConfig.json` 프로필과 매칭되는 sts-* 섹션만). `-p` 지정 시 해당 프로필만 부분 삭제 (파일 유지). `--dry-run` 병용 시 미리보기 |
| -v, --verbose | DEBUG 로그까지 출력 |
| -q, --quiet | 경고/오류만 출력 |
| -V, --version | 버전 표시 |

## 설정 파일 / Config file

기본 위치 / default location:

```
~/.config/getAwsSTS/awsUserConfig.json
```

탐색 순서 / lookup order:

1. `--config` 로 명시한 경로
2. `AWSGETSTS_CONFIG` 환경변수
3. `~/.config/getAwsSTS/awsUserConfig.json` (정규 기본값)
4. (legacy) 엔트리 스크립트 옆 `aws_user_config.json`
5. (legacy) 현재 디렉토리 `./aws_user_config.json`

포맷 / format:

```
{
    "dev": {
        "account_id": "123456789012",
        "profile": "dev",
        "mfa_serial": "arn:aws:iam::123456789012:mfa/your-username",
        "totp_secret": "BASE32SECRET"
    }
}
```

- `account_id`: 12자리 AWS 계정 ID
- `profile`: `~/.aws/config` 의 base 프로필 이름 (자격증명 조회 소스)
- `mfa_serial`: MFA 디바이스 ARN
- `totp_secret`: MFA 등록 시 발급되는 base32 shared secret

파일은 wizard 가 자동으로 `chmod 600` 처리.

## 동작 흐름 / How it works

1. `~/.aws/config` 에 base profile (`profile <name>`) 존재 확인
2. 기존 `profile sts-<name>` 섹션의 만료시각 확인 → 유효하면 스킵
3. `sts:GetSessionToken` 호출 (TOTP 코드로 MFA 인증)
4. 발급된 자격증명을 `[profile sts-<name>]` 로 병합 저장
5. 옵션에 따라 `cli_pager =` 추가

## 환경변수 / Environment variables

| 변수 | 용도 |
| --- | --- |
| AWSGETSTS_CONFIG | 사용자 설정 파일 경로 override |
| AWSGETSTS_LANG | `ko` / `en` — 출력 언어 강제 (기본: 터미널 인코딩) |
| FORCE_COLOR | 비-TTY 에서도 컬러 강제 |
| NO_COLOR | 컬러 비활성화 |

## --check 출력 예 / Sample --check output

```
[awsgetsts --check] 프로필 진단
────────────────────────────────────────────────────────────
Files
  user_config      : FOUND    ~/.config/getAwsSTS/awsUserConfig.json
  aws_config       : FOUND    ~/.aws/config
  aws_credentials  : FOUND    ~/.aws/credentials
────────────────────────────────────────────────────────────
[dev] user_config=OK | aws_profile=OK | token=VALID (until 2026-09-02 03:15:00 KST)
[prod] user_config=OK | aws_profile=OK | token=EXPIRED (2026-09-01 08:00:00 KST)
────────────────────────────────────────────────────────────
[awsgetsts --check] Summary
  total                  : 2
  valid                  : 1
  expired                : 1
  no_token               : 0
  missing_user_cfg       : 0
  missing_aws_profile    : 0
```

## 종료 코드 / Exit codes

| 코드 | 의미 |
| --- | --- |
| 0 | 정상 종료 |
| 1 | STS 호출 실패 등 발급 실패 존재 |
| 2 | 잘못된 인자 / 설정 로드 실패 / 알 수 없는 프로필 |
| 130 | 사용자 취소 (Ctrl+C / EOF) |

## 보안 / Security

- `awsUserConfig.json` 은 TOTP shared secret 을 포함 → 반드시 `chmod 600`, 유출 금지
- 노출된 secret 은 즉시 MFA 재등록으로 폐기
- `.aws/credentials` 의 Access Key 도 동일하게 취급

