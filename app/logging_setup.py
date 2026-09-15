"""JSON logs, no secrets (docs/14 §14.2)."""

from __future__ import annotations  # noqa: I001

import json
import logging
import os
import sys
from datetime import UTC, datetime

import re

_SECRET_MARKERS = ("token", "password", "secret", "api_key", "apikey")
_SECRET_RE = re.compile(
    r"((?:api_token|token|password|secret|api_key|apikey|authorization)[=:\s]+(?:bearer\s+)?)([^&\s\"']{4,})", re.I
)


def redact(text: str) -> str:
    """Mask secret-like values in any free text (logs, tracebacks, job errors)."""
    return _SECRET_RE.sub(lambda m: m.group(1) + "[redacted]", text or "")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        msg = redact(record.getMessage())
        payload = {
            "ts": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": msg,
        }
        if record.exc_info:
            payload["exc"] = redact(self.formatException(record.exc_info)[-2000:])
        return json.dumps(payload, ensure_ascii=False)


def setup_logging() -> None:
    root = logging.getLogger()
    if getattr(root, "_bourse_configured", False):
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.handlers = [handler]
    root.setLevel(os.environ.get("LOG_LEVEL", "INFO"))
    root._bourse_configured = True  # type: ignore[attr-defined]
    logging.getLogger("httpx").setLevel("WARNING")
    logging.getLogger("apscheduler").setLevel("INFO")
