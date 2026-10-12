"""Private credential sanitization and task-scoped suppression of SDK payload logs."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any
from urllib.parse import quote

from core.errors import ToolError

_EXTERNAL_IO: ContextVar[bool] = ContextVar("external_provider_io", default=False)


class _ProviderLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return not _EXTERNAL_IO.get()


@contextmanager
def suppress_sdk_payload_logs() -> Iterator[None]:
    logger = logging.getLogger("client")
    if not any(isinstance(item, _ProviderLogFilter) for item in logger.filters):
        logger.addFilter(_ProviderLogFilter())
    token = _EXTERNAL_IO.set(True)
    try:
        yield
    finally:
        _EXTERNAL_IO.reset(token)


class CredentialRedactor:
    def __init__(self, values: Sequence[str] = ()) -> None:
        self._literal_bytes = tuple(value.encode("utf-8") for value in set(values) if value)
        literals = sorted({variant for value in values if value for variant in (
            value, json.dumps(value, ensure_ascii=True)[1:-1], quote(value, safe=""),
        )}, key=len, reverse=True)
        self._pattern = re.compile("|".join(re.escape(value) for value in literals)) if literals else None

    def text(self, value: str) -> str:
        return value if self._pattern is None else self._pattern.sub(
            lambda match: "[REDACTED]" if len(match.group()) >= 10 else "", value,
        )

    def sensitive_binary(self, encoded: str, decoded: bytes) -> bool:
        return self.text(encoded) != encoded or any(literal in decoded for literal in self._literal_bytes)

    def json(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, list):
            return [self.json(item) for item in value]
        if isinstance(value, dict):
            result = {self.text(key): self.json(item) for key, item in value.items()}
            if len(result) != len(value):
                raise ToolError("malformed_result", "Credential redaction would collide with result keys.")
            return result
        return value
