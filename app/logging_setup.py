"""JSON logs, no secrets (docs/14 §14.2)."""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import UTC, datetime

_SECRET_MARKERS = ("token", "password", "secret", "api_key", "apikey")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        msg = record.getMessage()
        low = msg.lower()
        if any(m in low for m in _SECRET_MARKERS) and "=" in msg:
            msg = "[redacted: message contained a secret-like key]"
        payload = {
            "ts": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": msg,
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)[-2000:]
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
