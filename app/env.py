"""``.env`` loading shared by the application and the CLI.

``python-dotenv`` (and Docker Compose ``env_file``) read ``KEY=   # comment`` as the comment text
and ``KEY=<placeholder>`` literally. Such values would silently switch features on (Cloudflare
Access, Saxo…), so after loading they are treated as **unset**: a value is dropped when it starts
with ``#`` or is a ``<…>`` placeholder. Variables already set by the shell or Docker are never
overridden, only sanitised by the same rule.
"""

from __future__ import annotations

import os
import re

_PLACEHOLDER = re.compile(r"<[^<>\s]+>")  # <equipe>.cloudflareaccess.com, bourse.<nom>@gmail.com


def is_placeholder(value: str | None) -> bool:
    if value is None:
        return False
    v = value.strip()
    return v.startswith("#") or bool(_PLACEHOLDER.search(v)) or "CHANGE_ME" in v


def sanitize_environ(keys: list[str] | None = None) -> list[str]:
    """Remove placeholder-valued variables from ``os.environ``; returns the keys removed."""
    removed = []
    for key in list(keys if keys is not None else os.environ):
        if key in os.environ and is_placeholder(os.environ[key]):
            del os.environ[key]
            removed.append(key)
    return removed


def load_env(path: str | None = None) -> list[str]:
    """``load_dotenv`` (never overriding existing variables) followed by ``sanitize_environ`` on the
    keys of the file **and** of the current environment."""
    from dotenv import dotenv_values, load_dotenv

    load_dotenv(path, override=False)
    file_keys = list(dotenv_values(path).keys()) if (path or os.path.exists(".env")) else []
    return sanitize_environ(sorted(set(file_keys) | set(os.environ)))
