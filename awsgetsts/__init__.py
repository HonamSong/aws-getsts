"""awsgetsts — AWS STS 임시 자격증명 발급 유틸리티.

awsgetsts is a small utility that fetches AWS STS session tokens
(optionally with MFA/TOTP) and writes them to ``~/.aws/config``
as ``profile sts-<name>`` entries.
"""

from __future__ import annotations

__version__ = "0.2.0"
__all__ = ["__version__"]