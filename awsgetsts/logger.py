"""공용 cplogger 인스턴스 + 레벨 필터 / Shared cplogger with level filtering.

cplogger 자체는 레벨 필터링을 하지 않아 얇은 래퍼로 필터를 추가합니다.
cplogger doesn't filter by level, so we add a thin wrapper.
"""

from __future__ import annotations

from cplogger import Logging

_LEVELS = {"DEBUG": 10, "INFO": 20, "WARN": 30, "WARNING": 30, "ERROR": 40}


class _LeveledLogger:
    """레벨 임계값 이상만 통과 / Pass-through logger with level threshold."""

    def __init__(self, backend: Logging, level: str = "WARN") -> None:
        self._backend = backend
        self._threshold = _LEVELS[level.upper()]

    def set_level(self, level: str) -> None:
        self._threshold = _LEVELS[level.upper()]
        self._backend.log_level = level.upper()

    def _should(self, level: str) -> bool:
        return _LEVELS[level] >= self._threshold

    def debug(self, msg: str) -> None:
        if self._should("DEBUG"):
            self._backend.debug(msg)

    def info(self, msg: str) -> None:
        if self._should("INFO"):
            self._backend.info(msg)

    def warn(self, msg: str) -> None:
        if self._should("WARN"):
            self._backend.warn(msg)

    def error(self, msg: str) -> None:
        if self._should("ERROR"):
            self._backend.error(msg)


log = _LeveledLogger(Logging(log_mode="print"), level="INFO")


def set_level(level: str) -> None:
    """로그 레벨 설정 / Set log level (DEBUG/INFO/WARN/ERROR)."""
    log.set_level(level)