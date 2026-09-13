"""Load and validate ``config/params.yaml`` with feature-level blocking (docs/14 §14.2).

- A missing/invalid *required* group (``config_validation.required_groups``) raises
  :class:`ConfigError` and the application must refuse to start.
- A missing/invalid *optional* group disables only the feature it drives; the reason is
  recorded in :class:`FeatureStatus` and shown on ``/sante``.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from app.config.schema import (
    Availability,
    Llm,
    Params,
    SrdParams,
    TaxVersion,
)

DEFAULT_REQUIRED = ["timezones", "capital", "risk", "accounts", "notifications", "data", "jobs"]

# optional group key -> (feature label in French, pydantic model or None for free-form dicts)
OPTIONAL_FEATURES: dict[str, tuple[str, Any]] = {
    "tax": ("Page fiscale et estimations d'impôt", list[TaxVersion]),
    "llm": ("Classification et rédaction LLM", Llm),
    "availability.schedule": ("Planning de disponibilité par défaut", Availability),
    "detectors.d1_gap_catalyst": ("Détecteur D1 (gap + catalyseur, phase 3)", None),
    "subscriptions": ("Suivi des abonnements", None),
    "brokers": ("Frais de courtage estimés", None),
    "srd": ("Calendrier SRD et coûts SRD", SrdParams),
}


class ConfigError(RuntimeError):
    """Raised when a required configuration group is missing or invalid."""


@dataclass
class FeatureStatus:
    key: str
    label: str
    enabled: bool
    reason: str = ""


@dataclass
class LoadedConfig:
    params: Params
    raw: dict[str, Any]
    path: Path
    sha256: str
    features: list[FeatureStatus] = field(default_factory=list)

    def feature_enabled(self, key: str) -> bool:
        for f in self.features:
            if f.key == key:
                return f.enabled
        return False

    @property
    def disabled_features(self) -> list[FeatureStatus]:
        return [f for f in self.features if not f.enabled]


def _get_path(raw: dict[str, Any], dotted: str) -> Any:
    cur: Any = raw
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _format_validation_error(group: str, err: ValidationError) -> str:
    lines = [f"groupe `{group}` invalide :"]
    for e in err.errors():
        loc = ".".join(str(x) for x in e["loc"])
        lines.append(f"  - {loc}: {e['msg']}")
    return "\n".join(lines)


def load_params(path: str | os.PathLike[str] | None = None) -> LoadedConfig:
    """Load the YAML file, validate required groups (blocking), then optional ones."""
    p = Path(path or os.environ.get("PARAMS_PATH", "config/params.yaml"))
    if not p.exists():
        raise ConfigError(f"fichier de paramètres introuvable : {p}")
    text = p.read_text(encoding="utf-8")
    raw = yaml.safe_load(text) or {}
    if not isinstance(raw, dict):
        raise ConfigError("params.yaml doit être un dictionnaire YAML")
    return validate_params(raw, path=p, sha256=hashlib.sha256(text.encode()).hexdigest())


def validate_params(raw: dict[str, Any], *, path: Path | None = None, sha256: str = "") -> LoadedConfig:
    required = list(DEFAULT_REQUIRED)
    cv = raw.get("config_validation") or {}
    if isinstance(cv, dict) and cv.get("required_groups"):
        required = list(cv["required_groups"])
    optional = list(cv.get("optional_groups", [])) if isinstance(cv, dict) else []

    missing = [g for g in required if _get_path(raw, g) is None]
    if missing:
        raise ConfigError("démarrage refusé : groupes de paramètres requis manquants : " + ", ".join(missing))

    # Optional groups: validate separately so their failure never blocks startup.
    features: list[FeatureStatus] = []
    cleaned = dict(raw)
    for key in sorted(set(optional) | set(OPTIONAL_FEATURES)):
        label, model = OPTIONAL_FEATURES.get(key, (key, None))
        value = _get_path(raw, key)
        if value is None:
            features.append(FeatureStatus(key, label, False, "groupe absent de params.yaml"))
            _drop_optional(cleaned, key)
            continue
        if model is not None:
            try:
                target = value if key != "availability.schedule" else raw.get("availability")
                if key == "availability.schedule":
                    Availability.model_validate(target)
                elif key == "tax":
                    [TaxVersion.model_validate(v) for v in value]
                else:
                    model.model_validate(value)
            except ValidationError as err:
                features.append(FeatureStatus(key, label, False, _format_validation_error(key, err)))
                _drop_optional(cleaned, key)
                continue
        features.append(FeatureStatus(key, label, True))

    if "config_validation" not in cleaned:
        cleaned["config_validation"] = {"required_groups": required, "optional_groups": optional}

    try:
        params = Params.model_validate(cleaned)
    except ValidationError as err:
        # Identify which required group failed to give an actionable message.
        groups = sorted({str(e["loc"][0]) for e in err.errors() if e["loc"]})
        raise ConfigError("démarrage refusé : " + "; ".join(_format_validation_error(g, err) for g in groups)) from err

    return LoadedConfig(params=params, raw=raw, path=path or Path("<memory>"), sha256=sha256, features=features)


def _drop_optional(cleaned: dict[str, Any], key: str) -> None:
    """Remove an invalid optional group so that the strict model does not fail on it."""
    parts = key.split(".")
    if len(parts) == 1:
        cleaned.pop(key, None)
        return
    parent = cleaned.get(parts[0])
    if isinstance(parent, dict):
        parent = dict(parent)
        parent.pop(parts[1], None)
        cleaned[parts[0]] = parent
