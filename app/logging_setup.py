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


_URL_CRED_RE = re.compile(r"(://[^/\s:@\"']+:)([^@\s\"']+)(@)")  # scheme://user:password@host


def redact(text: str) -> str:
    """Mask secret-like values in any free text (logs, tracebacks, job errors), including the
    password of a ``scheme://user:password@host`` URL (DATABASE_URL in a failed command)."""
    out = _SECRET_RE.sub(lambda m: m.group(1) + "[redacted]", text or "")
    return _URL_CRED_RE.sub(lambda m: m.group(1) + "[redacted]" + m.group(3), out)


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


def quiet_http_clients() -> None:
    """One INFO line per HTTP request (httpx, and httpx2 used by the Anthropic SDK) floods the
    console and the logs; warnings and errors are kept."""
    for name in ("httpx", "httpx2", "httpcore", "anthropic"):
        logging.getLogger(name).setLevel("WARNING")


def setup_logging() -> None:
    root = logging.getLogger()
    if getattr(root, "_bourse_configured", False):
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.handlers = [handler]
    root.setLevel(os.environ.get("LOG_LEVEL", "INFO"))
    root._bourse_configured = True  # type: ignore[attr-defined]
    quiet_http_clients()
    logging.getLogger("apscheduler").setLevel("INFO")
