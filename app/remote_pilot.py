"""Remote piloting (user's request of 15/11/2026: « tous les échanges, validations et lancements via l'interface
cloud ») — the PC side.

The cloud session writes, **after the user's approval in the cloud chat**, a task in ``ops/pc_tasks.yaml`` on the
working branch. On the PC, a Claude Code session in a loop pulls the branch and runs ``python -m app.remote_pilot``
(a quick « tick »): it prints the finished reports not yet sent (``ops/out/<id>.log``, not versioned) for that
session to send back to the cloud session, and starts the first task not yet done **in the background** (a long
load outlives the 10-minute limit of one command). The task is checked **whole** against a closed allow-list
before anything runs, then run command by command.

Safety: no shell (arguments split, never interpreted), no command outside the list below, no secret read (``.env``
is never opened; a line of output that looks like a secret is masked), one task per run, each task run once
(``ops/.done``). A refused task runs nothing. Placing a market order is impossible: no command of the list does it.
"""

from __future__ import annotations

import json
import re
import shlex
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

TASKS = Path("ops/pc_tasks.yaml")
DONE = Path("ops/.done")
OUT = Path("ops/out")

# app.cli commands run inside the container: diagnostics and idempotent loads only (no secret, no prompt, no LLM
# spending beyond the tool's own caps, no reset). Anything else is refused.
CONTAINER_CLI = frozenset(
    """check-config sources-health us-check us-load events-build events-measure events-scoreboard run-job backfill
    backtest backtest-report regime-check index-check gates-coverage radar-check radar-measure social-check
    shorts-check sources-check media-check tv-columns-check saxo-check llm-costs verdict-preview delisted-check
    delisted-load core-compare macro-backfill fx-history earnings-history macro-history-load filings-archive
    insider-archive exchange-list-load ttf-list-load social-backfill moves-study moves-causes entry-replay research
    letters-study letters-trace broker-plan seed-calendar""".split()
)
_ARG = r"(?:\s+(?:--?[a-z][a-z0-9-]*(?:=[A-Za-z0-9_.:,/-]+)?|[A-Za-z0-9_.:,/-]+))*"
_SET = r'(?:\s+--set\s+"[a-z_][a-z0-9_.]*=[^"`$;&|<>]*")*'
ALLOWED = [
    re.compile(r"^git (?:status|log -\d{1,2} --oneline)$"),
    re.compile(r"^uv run python -m app\.cli params-init --update" + _SET + r"$"),
    re.compile(r"^docker compose (?:up -d --build|ps|restart app|logs --tail \d{1,4} app)$"),
    re.compile(r"^docker compose exec -T app python -m app\.cli (?P<cli>[a-z][a-z0-9-]*)" + _ARG + r"$"),
    re.compile(r"^wait-app \d{1,4}$"),  # wait until the container answers its health check
]
_PLACEHOLDER = re.compile(r"\{params:([a-z_][a-z0-9_.]*)\}")
_SECRET = re.compile(r"(?i)\b(token|secret|password|passwd|api[_-]?key|authorization)\b\s*[:=]\s*\S+")


def check(cmd: str) -> str | None:
    """Why a command is refused (None when it is allowed)."""
    c = cmd.strip()
    if not c or "\n" in c:
        return "commande vide ou sur plusieurs lignes"
    for pat in ALLOWED:
        m = pat.match(c)
        if m:
            cli = m.groupdict().get("cli")
            if cli is not None and cli not in CONTAINER_CLI:
                return f"commande de l'outil hors liste : {cli}"
            return None
    return "hors liste blanche"


def substitute(cmd: str, params: dict[str, Any]) -> str:
    """``{params:user.email}`` → the value in the PC's params.yaml (never written in the versioned task)."""

    def one(m: re.Match[str]) -> str:
        v: Any = params
        for k in m.group(1).split("."):
            v = v.get(k) if isinstance(v, dict) else None
        if v is None or isinstance(v, dict | list):
            raise ValueError(f"paramètre {m.group(1)} absent de config/params.yaml")
        return str(v)

    return _PLACEHOLDER.sub(one, cmd)


def mask(text: str) -> str:
    return _SECRET.sub(lambda m: f"{m.group(1)}=***", text)


def clip(text: str, head: int = 6000, tail: int = 6000) -> str:
    if len(text) <= head + tail:
        return text
    return text[:head] + f"\n… [{len(text) - head - tail} caractères coupés] …\n" + text[-tail:]


@dataclass
class Step:
    cmd: str
    code: int | None
    seconds: float
    output: str


@dataclass
class Report:
    task_id: str
    purpose: str
    status: str  # faite | échec | refusée
    steps: list[Step] = field(default_factory=list)
    reason: str | None = None

    def text(self) -> str:
        out = [f"Compte rendu PC — tâche {self.task_id} : {self.status}", f"Objet : {self.purpose}"]
        if self.reason:
            out.append(f"Motif : {self.reason}")
        for k, st in enumerate(self.steps, 1):
            out += ["", f"[{k}] $ {st.cmd}  → code {st.code} en {st.seconds:.0f} s", st.output.rstrip()]
        return "\n".join(out)


Runner = Callable[[list[str], float], tuple[int, str]]


def _run(args: list[str], timeout: float) -> tuple[int, str]:
    try:
        p = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=timeout, stdin=subprocess.DEVNULL)  # fmt: skip
    except subprocess.TimeoutExpired as exc:
        part = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        return 124, f"délai dépassé ({timeout:.0f} s)\n{part}"
    except FileNotFoundError as exc:
        return 127, f"programme introuvable : {exc.filename}"
    return p.returncode, (p.stdout or "") + (("\n" + p.stderr) if p.stderr else "")


def _wait_app(seconds: int, run: Runner, sleep: Callable[[float], None]) -> tuple[int, str]:
    end = time.monotonic() + seconds
    probe = ["docker", "compose", "exec", "-T", "app", "curl", "-fsS", "http://localhost:8000/health"]
    while True:
        code, out = run(probe, 30)
        if code == 0:
            return 0, "application prête : " + out.strip()[:200]
        if time.monotonic() >= end:
            return 1, "application toujours indisponible : " + out.strip()[:400]
        sleep(10)


def done_ids(path: Path | None = None) -> set[str]:
    path = path or DONE  # read at call time (the module's path, or the one given)
    if not path.exists():
        return set()
    out = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            out.add(str(json.loads(line)["id"]))
        except (ValueError, KeyError, TypeError):
            continue
    return out


def next_task(tasks: list[dict[str, Any]], done: set[str]) -> dict[str, Any] | None:
    return next((t for t in tasks if isinstance(t, dict) and str(t.get("id")) not in done), None)


def run_task(task: dict[str, Any], params: dict[str, Any], run: Runner = _run,
             sleep: Callable[[float], None] = time.sleep) -> Report:  # fmt: skip
    tid, purpose = str(task.get("id")), str(task.get("purpose") or "—")
    cmds = [str(c) for c in task.get("commands") or []]
    if not cmds:
        return Report(tid, purpose, "refusée", reason="aucune commande")
    for c in cmds:  # the whole task is checked before anything runs
        why = check(c)
        if why:
            return Report(tid, purpose, "refusée", reason=f"« {c} » : {why}")
    rep = Report(tid, purpose, "faite")
    timeout = float(task.get("timeout_s") or 7200)
    for c in cmds:
        t0 = time.monotonic()
        try:
            real = substitute(c, params)
        except ValueError as exc:
            rep.steps.append(Step(c, None, 0.0, str(exc)))
            rep.status = "échec"
            break
        if c.startswith("wait-app "):
            code, out = _wait_app(int(c.split()[1]), run, sleep)
        else:
            code, out = run(shlex.split(real), timeout)
        rep.steps.append(Step(c, code, time.monotonic() - t0, clip(mask(out))))  # the task's text, not the values
        if code != 0:
            rep.status = "échec"
            if task.get("stop_on_error", True):
                break
    return rep


SENT = Path("ops/.sent")
LOCK = Path("ops/.running")


def _load_tasks() -> list[dict[str, Any]]:
    if not TASKS.exists():
        return []
    data = yaml.safe_load(TASKS.read_text(encoding="utf-8")) or {}
    return list(data.get("tasks") or [])


def work() -> int:
    """The worker (started in the background by ``tick``): runs the next task and writes its report."""
    task = next_task(_load_tasks(), done_ids())
    if task is None:
        LOCK.unlink(missing_ok=True)
        return 0
    try:
        params = yaml.safe_load(Path("config/params.yaml").read_text(encoding="utf-8")) or {}
    except OSError:
        params = {}
    try:
        rep = run_task(task, params)
    except Exception as exc:  # noqa: BLE001 — the report says it, the lock is always released
        rep = Report(str(task.get("id")), str(task.get("purpose") or "—"), "échec", reason=exc.__class__.__name__)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{rep.task_id}.log").write_text(rep.text(), encoding="utf-8")
    with DONE.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"id": rep.task_id, "status": rep.status, "at": datetime.now(UTC).isoformat()}) + "\n")
    LOCK.unlink(missing_ok=True)
    return 0 if rep.status == "faite" else 1


def _spawn() -> None:
    import os

    OUT.mkdir(parents=True, exist_ok=True)
    log = (OUT / "worker.txt").open("a", encoding="utf-8")
    kw: dict[str, Any] = {"stdout": log, "stderr": log, "stdin": subprocess.DEVNULL, "close_fds": True}
    if os.name == "nt":  # survives the end of the command that started it
        kw["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
    else:
        kw["start_new_session"] = True
    subprocess.Popen([sys.executable, "-m", "app.remote_pilot", "work"], **kw)  # noqa: S603


def tick(spawn: Callable[[], None] = _spawn, now: float | None = None) -> str:
    """One quick pass of the PC's loop: the finished reports not yet sent (marked sent), then the state — a task
    running, a task started in the background, or nothing new. Never waits for a task."""
    now = time.time() if now is None else now
    sent = set(SENT.read_text(encoding="utf-8").split()) if SENT.exists() else set()
    lines: list[str] = []
    for f in sorted(OUT.glob("*.log")) if OUT.exists() else []:
        if f.stem in sent:
            continue
        lines += ["=== COMPTE RENDU À ENVOYER ===", f.read_text(encoding="utf-8"), "=== FIN DU COMPTE RENDU ==="]
        with SENT.open("a", encoding="utf-8") as g:
            g.write(f.stem + "\n")
    tasks = _load_tasks()
    if LOCK.exists():
        info = json.loads(LOCK.read_text(encoding="utf-8") or "{}")
        limit = float(info.get("timeout_s") or 7200) * 12 + 600  # a dozen steps at most, then the lock is stale
        if now - float(info.get("started", 0)) < limit:
            lines.append(
                f"tâche en cours : {info.get('id')} depuis {(now - float(info.get('started', 0))) / 60:.0f} min"
            )
            return "\n".join(lines)
        LOCK.unlink(missing_ok=True)
        lines.append(f"verrou périmé de la tâche {info.get('id')} retiré")
    task = next_task(tasks, done_ids())
    if task is None:
        lines.append("aucune tâche nouvelle")
        return "\n".join(lines)
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    LOCK.write_text(json.dumps({"id": task.get("id"), "started": now, "timeout_s": task.get("timeout_s")}),
                    encoding="utf-8")  # fmt: skip
    spawn()
    lines.append(f"tâche lancée en arrière-plan : {task.get('id')} — {task.get('purpose') or ''}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args[:1] == ["work"]:
        return work()
    print(tick())
    return 0


if __name__ == "__main__":
    sys.exit(main())
