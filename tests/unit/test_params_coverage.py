"""K13 (docs/25 §25.7): every key of params.example.yaml is either read by the code or tagged on its line
(or a parent's line) as « [phase 3] », « [phase 6] », « [non codé] », « [info] », « [obsolète] »,
« [fixé dans le code] », « [comportement codé] » or « [à décider …] » — a parameter changed without effect
must never mislead the user."""

import re
from pathlib import Path

import yaml

TAGS = ("[phase 3", "[phase 6]", "[non codé", "[info]", "[obsolète", "[fixé dans le code", "[comportement codé]",
        "[à décider")  # fmt: skip
SKIP_TOP = {"user", "calendars", "instruments"}
SKIP_PARENTS = (
    "llm.prices_usd_per_mtok",
    "llm.model_options",
    "data.imap.senders",
    "data.rss_feeds",
    "brokers.boursobank.formulas",  # formula names are data, their fields are read by app/services/broker_plan.py
    "regime.indices_by_mic",  # venue codes (MIC) are data, their fields are read by app/services/indices.py
)


def _source() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in Path("app").rglob("*.py")) + "\n".join(
        p.read_text(encoding="utf-8") for p in Path("app").rglob("*.html")
    )


def _key_lines(text: str) -> dict[str, list[str]]:
    """dotted path → the lines that may carry its tag (its own line and its parents' lines)."""
    out: dict[str, list[str]] = {}
    stack: list[tuple[int, str, str]] = []  # (indent, key, line)
    for line in text.split("\n"):
        m = re.match(r"^( *)([A-Za-z0-9_\-]+):(.*)$", line)
        if not m or line.lstrip().startswith("#"):
            continue
        indent, key = len(m.group(1)), m.group(2)
        while stack and stack[-1][0] >= indent:
            stack.pop()
        path = ".".join([k for _, k, _ in stack] + [key])
        lines = [line] + [ln for _, _, ln in stack]
        out[path] = lines
        inline = re.match(r"\s*\{(.*)\}", m.group(3).split("#")[0])
        if inline:
            for sub in re.findall(r"([A-Za-z0-9_]+)\s*:", inline.group(1)):
                out[f"{path}.{sub}"] = lines
        stack.append((indent, key, line))
    return out


def test_every_parameter_is_used_or_tagged():
    text = Path("config/params.example.yaml").read_text(encoding="utf-8")
    cfg = yaml.safe_load(text)
    src = _source()
    where = _key_lines(text)
    missing: list[str] = []

    def walk(d: object, path: list[str]) -> None:
        if not isinstance(d, dict):
            return
        for k, v in d.items():
            if not isinstance(k, str):
                continue
            p = [*path, k]
            dotted = ".".join(p)
            if p[0] in SKIP_TOP or any(dotted.startswith(x + ".") for x in SKIP_PARENTS):
                continue
            if not re.search(r"\b" + re.escape(k) + r"\b", src):
                if not any(t in ln for ln in where.get(dotted, []) for t in TAGS):
                    missing.append(dotted)
            elif isinstance(v, dict) and len(p) < 4:
                walk(v, p)

    walk(cfg, [])
    assert missing == [], "paramètres sans effet et sans étiquette : " + ", ".join(missing)
