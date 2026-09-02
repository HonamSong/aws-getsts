"""AWS STS 세션 토큰 발급 / STS session token issuance."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from .logger import log
from .mfa import get_mfa_token

MIN_DURATION = 900       # AWS 최소값 / AWS minimum
DEFAULT_DURATION = 36000  # 10 hours


@dataclass(frozen=True)
class StsCredentials:
    """발급된 STS 자격증명 / Issued STS credentials."""

    access_key_id: str
    secret_access_key: str
    session_token: str
    expiration_iso: str

    def to_ini_dict(self) -> dict:
        return {
            "AWS_ACCESS_KEY_ID": self.access_key_id,
            "AWS_SECRET_ACCESS_KEY": self.secret_access_key,
            "AWS_SESSION_TOKEN": self.session_token,
            "EXPIRATION": self.expiration_iso,
        }


def get_session_token(
    profile_name: str,
    mfa_serial: Optional[str] = None,
    totp_secret: Optional[str] = None,
    duration: int = DEFAULT_DURATION,
) -> StsCredentials:
    """AWS STS 세션 토큰 발급 / Request an STS session token.

    Args:
        profile_name: Base AWS profile name in ``~/.aws/config``.
        mfa_serial: MFA device ARN (required for MFA-enforced accounts).
        totp_secret: TOTP secret used to generate the current MFA code.
        duration: Session duration in seconds (minimum 900).

    Raises:
        ValueError: on invalid arguments.
        botocore.exceptions.BotoCoreError / ClientError: on AWS errors.
    """
    if duration < MIN_DURATION:
        raise ValueError(f"duration must be >= {MIN_DURATION} seconds")
    if bool(mfa_serial) != bool(totp_secret):
        raise ValueError("mfa_serial and totp_secret must be set together")

    session = boto3.Session(profile_name=profile_name)
    client = session.client("sts")

    kwargs: dict = {"DurationSeconds": duration}
    if mfa_serial and totp_secret:
        kwargs["SerialNumber"] = mfa_serial
        kwargs["TokenCode"] = get_mfa_token(totp_secret)

    try:
        response = client.get_session_token(**kwargs)
    except (BotoCoreError, ClientError) as e:
        log.error(f"STS get_session_token failed for {profile_name}: {e}")
        raise

    creds = response["Credentials"]
    return StsCredentials(
        access_key_id=creds["AccessKeyId"],
        secret_access_key=creds["SecretAccessKey"],
        session_token=creds["SessionToken"],
        expiration_iso=creds["Expiration"].isoformat(),
    )