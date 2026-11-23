"""Deterministic synthetic workloads with known answers.

Each task is a small read-only sandbox (a dict of path -> text) that an agent explores
with `list_files` / `read_file`. The files are long and mostly noise, so history grows
fast, but the answer depends on exact values scattered across many of them. Each task
also has a follow-up *probe* question, asked in the same conversation after the main
answer, about a detail from early in the run: that checks whether folding lost it.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from langchain_core.tools import BaseTool, StructuredTool

from context_compact.tokens import count_text

SIZES: dict[str, dict[str, int]] = {
    "small": dict(services=8, service_tokens=650, logs=6, log_tokens=850),
    "medium": dict(services=12, service_tokens=900, logs=8, log_tokens=1100),
    "large": dict(services=18, service_tokens=1100, logs=11, log_tokens=1400),
}

Grader = Callable[[Any, dict], tuple[float, dict]]


@dataclass
class Turn:
    """One graded question in a session. kind: task (main), probe (recall), followup."""

    kind: str
    question: str
    expected: dict
    grade: Grader


@dataclass
class Task:
    name: str
    files: dict[str, str]
    turns: list[Turn]

    @property
    def corpus_tokens(self) -> int:
        return sum(count_text(t) for t in self.files.values())

    @property
    def files_sha256(self) -> str:
        h = hashlib.sha256()
        for path in sorted(self.files):
            h.update(path.encode() + b"\0" + self.files[path].encode() + b"\0")
        return h.hexdigest()

    def _first(self, kind: str) -> Turn:
        return next(t for t in self.turns if t.kind == kind)

    # Convenience accessors for the two-turn (task + probe) experiment.
    prompt = property(lambda self: self._first("task").question)
    expected = property(lambda self: self._first("task").expected)
    grade = property(lambda self: self._first("task").grade)
    probe = property(lambda self: self._first("probe").question)
    probe_expected = property(lambda self: self._first("probe").expected)
    grade_probe = property(lambda self: self._first("probe").grade)


# --------------------------------------------------------------------- helpers
def extract_json(text: str | None) -> Any:
    """Last JSON object in the text (prefers fenced ```json blocks)."""
    if not text:
        return None
    for block in reversed(re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)):
        try:
            return json.loads(block)
        except json.JSONDecodeError:
            pass
    dec = json.JSONDecoder()
    for pos in reversed([m.start() for m in re.finditer(r"\{", text)]):
        try:
            obj, _ = dec.raw_decode(text[pos:])
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            continue
    return None


_DASHES = str.maketrans({c: "-" for c in "\u2010\u2011\u2012\u2013\u2014\u2212"} | {"\u00a0": " "})


def _clean(s: Any) -> str:
    """Lower-case, trim, and map Unicode dashes (models love U+2011) to ASCII."""
    return str(s).translate(_DASHES).strip().lower()


def _norm(s: Any) -> str:
    s = _clean(s)
    s = re.sub(r"^services/|\.py$|[-_ ]service$", "", s)
    return s


def _as_int(v: Any) -> int | None:
    try:
        return int(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def sandbox_tools(files: dict[str, str]) -> list[BaseTool]:
    def list_files(directory: str = "") -> str:
        """List files in the sandbox, optionally under one directory, with line counts."""
        d = directory.strip().strip("/").removeprefix("./")
        rows = [
            f"{p}  ({c.count(chr(10)) + 1} lines, ~{count_text(c):,} tokens)"
            for p, c in sorted(files.items())
            if not d or p == d or p.startswith(d + "/")
        ]
        return "\n".join(rows) or f"No files under {directory!r}."

    def read_file(path: str) -> str:
        """Read one whole file from the sandbox."""
        p = path.strip().removeprefix("./").lstrip("/")
        if p in files:
            return files[p]
        close = [f for f in files if f.endswith("/" + p.split("/")[-1])]
        hint = f" Did you mean {close[0]}?" if close else " Use list_files() to see paths."
        return f"Error: {path} not found.{hint}"

    return [StructuredTool.from_function(list_files), StructuredTool.from_function(read_file)]


# ------------------------------------------------------------ service audit
SERVICE_NAMES = [
    "analytics", "auth", "billing", "catalog", "exports", "fraud", "gateway", "inventory",
    "loyalty", "media", "notifications", "payments", "pricing", "recommendations", "reviews",
    "scheduler", "search", "sessions", "shipping", "webhooks",
]
OWNERS = ["team-atlas", "team-hermes", "team-ledger", "team-nova", "team-orion", "team-vega"]
REGIONS = ["eu-west", "us-east", "ap-south"]
AUTHORS = ["r.okafor", "m.lindqvist", "a.sato", "p.mehta", "j.alvarez", "k.nowak"]
VERBS = ["fetch", "sync", "prune", "rebuild", "export", "score", "validate", "reconcile", "refresh", "index"]
NOUNS = ["orders", "sessions", "invoices", "events", "tokens", "carts", "ledgers", "profiles", "quotes", "reviews"]
KEYS = ["account_id", "tenant", "region_code", "user_id", "sku", "batch_id"]
STATUSES = ["archived", "failed", "pending", "deleted"]

FUNC_TEMPLATES = [
    '''def {verb}_{noun}(client, {key}: str, *, limit: int = {n}) -> list[dict]:
    """{Verb} {noun} records for one {key}."""
    started = time.monotonic()
    rows = client.query("{noun}_v{m}", {key}={key}, limit=limit)
    if not rows:
        log.debug("no {noun} for %s", {key})
        return []
    kept = [r for r in rows if r.get("status") != "{status}"]
    log.info("{verb}_{noun} kept %d/%d in %.1fms", len(kept), len(rows), (time.monotonic() - started) * 1000)
    return kept
''',
    '''@dataclass
class {Noun}Record:
    id: str
    {key}: str
    status: str = "{status}"
    attempts: int = 0
    tags: list[str] = field(default_factory=list)

    def is_stale(self, now: float, ttl: float = {n}.0) -> bool:
        return self.attempts > {m} or (now % ttl) > ttl / 2
''',
    '''def _{verb}_batches(items: list[dict], size: int = {n}) -> list[list[dict]]:
    """Split {noun} work into batches."""
    batches, current = [], []
    for item in items:
        current.append(item)
        if len(current) >= size:
            batches.append(current)
            current = []
    if current:
        batches.append(current)
    log.debug("{noun}: %d batches of <= %d", len(batches), size)
    return batches
''',
    '''class {Noun}Cache:
    """Small TTL cache in front of the {noun}_v{m} table."""

    def __init__(self, ttl_s: float = {n}.0):
        self.ttl_s = ttl_s
        self._data: dict[str, tuple[float, object]] = {{}}

    def get(self, key: str):
        hit = self._data.get(key)
        if hit and time.monotonic() - hit[0] < self.ttl_s:
            return hit[1]
        self._data.pop(key, None)
        return None

    def put(self, key: str, value: object) -> None:
        self._data[key] = (time.monotonic(), value)
''',
]


def _service_module(name: str, cfg: dict, rng: random.Random, target: int) -> str:
    header = (
        f'"""{name} service for the storefront platform.\n\nOwned by {cfg["owner"]}. '
        'Generated scaffolding plus hand-written handlers.\n"""\n'
        "from __future__ import annotations\n\nimport logging\nimport time\n"
        "from dataclasses import dataclass, field\n\n"
        f'log = logging.getLogger("{name}")\n\n'
        "# --- changelog ------------------------------------------------------------\n"
    )
    entries = rng.sample(
        [
            f"tuned pool sizes after load test LT-{rng.randint(100, 999)}",
            "moved metrics export to OpenTelemetry",
            f"raised timeout_ms from {max(300, cfg['timeout_ms'] - rng.choice([300, 500, 700]))} "
            f"to {cfg['timeout_ms']}",
            f"retries were {cfg['retries'] + rng.randint(1, 3)} before INC-{rng.randint(1000, 9999)}",
            "split read and write paths",
            "dropped the legacy XML endpoint",
        ],
        k=rng.randint(3, 5),
    )
    for msg in entries:
        date = f"2026-{rng.randint(1, 10):02d}-{rng.randint(1, 28):02d}"
        header += f"# {date}  {rng.choice(AUTHORS)}: {msg}\n"
    config = "SERVICE_CONFIG = {\n" + "".join(
        f'    "{k}": {json.dumps(v)},\n' for k, v in {"name": name, **cfg}.items()
    ) + "}\n"

    funcs: list[str] = []
    body = header
    while count_text(body + config + "\n".join(funcs)) < target:
        verb, noun = rng.choice(VERBS), rng.choice(NOUNS)
        funcs.append(
            rng.choice(FUNC_TEMPLATES).format(
                verb=verb, Verb=verb.title(), noun=noun, Noun=noun.title().rstrip("s"),
                key=rng.choice(KEYS), status=rng.choice(STATUSES),
                n=rng.choice([25, 50, 100, 200, 500]), m=rng.randint(2, 9),
            )
        )
    funcs.insert(rng.randint(1, max(1, len(funcs) - 1)), config)  # config is buried mid-file
    return body + "\n" + "\n\n".join(funcs)


def service_audit(seed: int, services: int, service_tokens: int, **_: Any) -> Task:
    rng = random.Random(seed)
    names = sorted(["auth", *rng.sample([s for s in SERVICE_NAMES if s != "auth"], services - 1)])
    ports = rng.sample(range(7000, 9900), len(names))
    cfgs: dict[str, dict] = {}
    for name, port in zip(names, ports):
        cfgs[name] = {
            "owner": rng.choice(OWNERS),
            "region": rng.choice(REGIONS),
            "port": port,
            "timeout_ms": rng.choice([800, 1200, 1500, 1800, 2000, 2200, 2500, 3000, 4000]),
            "retries": rng.randint(0, 5),
            "max_connections": rng.randrange(40, 400, 10),
            "depends_on": sorted(rng.sample([x for x in names if x != name], k=rng.randint(0, 2))),
        }
    # Guarantee the questions are non-trivial and have a unique answer.
    others = [n for n in names if n != "auth"]
    for n in rng.sample(others, 3):
        if "auth" not in cfgs[n]["depends_on"]:
            cfgs[n]["depends_on"] = sorted([*cfgs[n]["depends_on"], "auth"])
    for n in rng.sample(names, 2):
        cfgs[n]["region"] = "eu-west"
    slow = [n for n in names if cfgs[n]["timeout_ms"] > 2000]
    for n in rng.sample([x for x in names if x not in slow], max(0, len(names) // 3 - len(slow))):
        cfgs[n]["timeout_ms"] = rng.choice([2200, 2500, 3000, 4000])
    fast = [n for n in names if cfgs[n]["timeout_ms"] <= 2000]
    if fast and not any(cfgs[n]["timeout_ms"] == 2000 for n in names):
        cfgs[rng.choice(fast)]["timeout_ms"] = 2000  # boundary case: not "greater than 2000"
    dependents = [n for n in names if "auth" in cfgs[n]["depends_on"]]
    top = max(cfgs[n]["retries"] for n in dependents)
    leaders = [n for n in dependents if cfgs[n]["retries"] == top]
    if len(leaders) > 1:
        cfgs[leaders[-1]]["retries"] = top + 1
    most_retries = max(dependents, key=lambda n: cfgs[n]["retries"])
    owner_counts = {o: sum(cfgs[n]["owner"] == o for n in names) for o in OWNERS}
    ranked = sorted(owner_counts, key=lambda o: (-owner_counts[o], o))
    if owner_counts[ranked[0]] == owner_counts[ranked[1]]:  # make the top owner unique
        cfgs[next(n for n in names if cfgs[n]["owner"] == ranked[1])]["owner"] = ranked[0]
        owner_counts = {o: sum(cfgs[n]["owner"] == o for n in names) for o in OWNERS}
    top_owner = max(owner_counts, key=lambda o: owner_counts[o])
    region_counts = {r: sum(cfgs[n]["region"] == r for n in names) for r in REGIONS}
    busy_region = max(REGIONS, key=lambda r: (region_counts[r], r))
    in_region = [n for n in names if cfgs[n]["region"] == busy_region]
    threshold = 150 if any(cfgs[n]["max_connections"] >= 150 for n in in_region) else 50

    files = {
        "README.md": "# storefront platform\n\nOne module per service under services/. Each module "
        "defines a SERVICE_CONFIG dict that is loaded at boot; changelog comments are history only.\n",
    }
    for n in names:
        files[f"services/{n}.py"] = _service_module(n, cfgs[n], rng, service_tokens)

    expected = {
        "slow_services": {n: cfgs[n]["owner"] for n in names if cfgs[n]["timeout_ms"] > 2000},
        "eu_west_max_connections": sum(cfgs[n]["max_connections"] for n in names if cfgs[n]["region"] == "eu-west"),
        "most_retries_auth_dependent": most_retries,
    }
    probe_svc = next((n for n in names if cfgs[n]["depends_on"]), names[0])
    prompt = (
        f"Audit the storefront platform in this sandbox. There are {len(names)} service modules under "
        "services/ and each defines a SERVICE_CONFIG dict. Read every one of them: a value you have not "
        "read is unknown. Use SERVICE_CONFIG values (changelog comments are history, not config).\n"
        "1. List every service whose timeout_ms is greater than 2000, with its owner.\n"
        '2. Give the total max_connections over all services whose region is "eu-west".\n'
        '3. Among services whose depends_on includes "auth", which one has the most retries?\n'
        "Finish with a JSON object of exactly this form:\n"
        '{"slow_services": {"<service>": "<owner>"}, "eu_west_max_connections": <int>, '
        '"most_retries_auth_dependent": "<service>"}'
    )
    probe = (
        f"One more question about the audit: what port does the {probe_svc} service listen on, "
        f"and which services does it depend on? Answer with JSON: "
        '{"port": <int>, "depends_on": ["<service>"]}'
    )
    owners_q = (
        "Which owner team owns the most services, and how many does it own? "
        'Answer with JSON: {"team": "<owner>", "count": <int>}'
    )
    region_q = (
        f'List every service in region "{busy_region}" whose max_connections is at least {threshold}. '
        'Answer with JSON: {"services": ["<service>"]}'
    )
    return Task(
        name="service_audit",
        files=files,
        turns=[
            Turn("task", prompt, expected, _grade_audit),
            Turn("probe", probe, {"port": cfgs[probe_svc]["port"], "depends_on": cfgs[probe_svc]["depends_on"]},
                 _grade_audit_probe),
            Turn("followup", owners_q, {"team": top_owner, "count": owner_counts[top_owner]}, _grade_fields),
            Turn("followup", region_q,
                 {"services": sorted(n for n in in_region if cfgs[n]["max_connections"] >= threshold)},
                 _grade_fields),
        ],
    )


def _grade_audit(ans: Any, exp: dict) -> tuple[float, dict]:
    if not isinstance(ans, dict):
        return 0.0, {"parsed": False}
    raw = ans.get("slow_services") or {}
    got = {(_norm(k), _clean(v)) for k, v in raw.items()} if isinstance(raw, dict) else set()
    want = {(k, v) for k, v in exp["slow_services"].items()}
    tp = len(got & want)
    p = tp / len(got) if got else 0.0
    r = tp / len(want) if want else 1.0
    f1 = 2 * p * r / (p + r) if tp else 0.0
    eu = _as_int(ans.get("eu_west_max_connections")) == exp["eu_west_max_connections"]
    top = _norm(ans.get("most_retries_auth_dependent", "")) == exp["most_retries_auth_dependent"]
    return round((f1 + eu + top) / 3, 3), {"slow_services_f1": round(f1, 3), "eu_west_sum": eu, "most_retries": top}


def _grade_audit_probe(ans: Any, exp: dict) -> tuple[float, dict]:
    if not isinstance(ans, dict):
        return 0.0, {"parsed": False}
    port = _as_int(ans.get("port")) == exp["port"]
    deps = ans.get("depends_on") or []
    deps_ok = isinstance(deps, list) and {_norm(d) for d in deps} == set(exp["depends_on"])
    return (port + deps_ok) / 2, {"port": port, "depends_on": deps_ok}


# ----------------------------------------------------------- incident trace
def _hex(rng: random.Random, n: int = 6) -> str:
    return "".join(rng.choice("0123456789abcdef") for _ in range(n))


def _ts(t: datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 1000:03d}Z"


FILLER: dict[str, Callable[[random.Random], str]] = {
    "gateway": lambda r: f"INFO  gateway req=rq-{_hex(r)} {r.choice(['GET', 'GET', 'POST'])} "
    f"{r.choice(['/api/items', '/api/cart', '/api/search', '/api/account', '/api/checkout'])} "
    f"status={r.choice([200, 200, 200, 201, 204, 304])} upstream={r.choice(['catalog', 'cart', 'search', 'account', 'checkout'])}-svc "
    f"latency={r.randint(4, 380)}ms",
    "checkout": lambda r: f"INFO  checkout req=rq-{_hex(r)} order={r.randint(6000, 7999)} "
    f"step={r.choice(['cart', 'address', 'quote', 'confirm'])} ok ({r.randint(3, 240)}ms)",
    "payments": lambda r: f"INFO  payments trace=tr-{_hex(r)} capture order={r.randint(6000, 7999)} "
    f"amount={r.randint(5, 900)}.{r.randint(0, 99):02d} EUR ok",
    "ledger-db": lambda r: f"DEBUG ledger-db txn=tx-{r.randint(10000, 99999)} commit "
    f"table={r.choice(['ledger_entries', 'balances', 'payouts', 'audit_log'])} rows={r.randint(1, 40)} {r.randint(1, 60)}ms",
    "scheduler": lambda r: f"INFO  scheduler job={r.choice(['cache-warm', 'sitemap-build', 'search-reindex'])} "
    f"pid={r.randint(10000, 19999)} heartbeat progress={r.randint(1, 99)}%",
    "auth": lambda r: f"INFO  auth user=u{r.randint(1000, 99999)} token refreshed "
    f"client={r.choice(['web', 'ios', 'android'])} ttl=3600s",
    "search": lambda r: f"INFO  search q=\"{r.choice(['boots', 'lamp', 'desk', 'jacket', 'kettle', 'tent'])}\" "
    f"hits={r.randint(0, 900)} took={r.randint(3, 120)}ms shard={r.randint(0, 7)}",
    "inventory": lambda r: f"INFO  inventory sku=SKU-{r.randint(10000, 99999)} reserved qty={r.randint(1, 5)} "
    f"warehouse={r.choice(['AMS1', 'FRA2', 'DUB1'])}",
    "cdn": lambda r: f"INFO  cdn GET /static/{r.choice(['app', 'vendor', 'styles'])}.{_hex(r, 8)}.js "
    f"{r.choice([200, 200, 304])} bytes={r.randint(900, 990000)} edge={r.choice(['ams', 'fra', 'lhr'])}",
    "email": lambda r: f"INFO  email sent template={r.choice(['order-confirm', 'shipping', 'reset'])} "
    f"to=user{r.randint(100, 99999)}@example.com provider=ses",
    "recommendations": lambda r: f"INFO  recs user=u{r.randint(1000, 99999)} model=v{r.randint(3, 9)} "
    f"items={r.randint(4, 24)} {r.randint(5, 90)}ms",
}
CORE_LOGS = ["gateway", "checkout", "payments", "ledger-db", "scheduler"]
EXTRA_LOGS = ["auth", "search", "inventory", "cdn", "email", "recommendations"]


def _log_file(svc: str, keys: list[tuple[datetime, str]], start: datetime, end: datetime,
              rng: random.Random, target: int) -> str:
    lines = list(keys)
    span = (end - start).total_seconds()

    def text() -> str:
        return "\n".join(f"{_ts(t)} {m}" for t, m in sorted(lines, key=lambda x: x[0])) + "\n"

    while count_text(text()) < target:
        for _ in range(10):
            lines.append((start + timedelta(seconds=rng.uniform(0, span)), FILLER[svc](rng)))
    return text()


def incident_trace(seed: int, logs: int, log_tokens: int, **_: Any) -> Task:
    rng = random.Random(seed + 1000)
    order = rng.randint(8000, 9999)
    req, trace = f"rq-{_hex(rng)}", f"tr-{_hex(rng)}"
    txn, pid = f"tx-{rng.randint(10000, 99999)}", rng.randint(2000, 9000)
    table = rng.choice(["ledger_entries", "balances", "payouts"])
    job = rng.choice(["nightly-reconcile", "fx-revaluation", "payout-rollup", "ledger-compaction"])
    other_table = rng.choice([t for t in ["ledger_entries", "balances", "payouts"] if t != table])
    other_job, other_pid = "cache-warm", rng.randint(10000, 19999)
    T = datetime(2026, 11, 20, 14, rng.randint(2, 7), rng.randint(0, 59), rng.randint(0, 999) * 1000, tzinfo=timezone.utc)
    latency = rng.randint(30005, 30090)
    o2, o3 = order + 1, order - 7
    s = lambda sec: T + timedelta(seconds=sec)  # noqa: E731

    keys: dict[str, list[tuple[datetime, str]]] = {
        "gateway": [
            (T, f"ERROR gateway req={req} POST /api/checkout order={order} status=502 upstream=checkout-svc latency={latency}ms"),
            (s(-190), f"WARN  gateway req=rq-{_hex(rng)} POST /api/checkout order={o2} status=402 upstream=checkout-svc latency=212ms"),
            (s(250), f"ERROR gateway req=rq-{_hex(rng)} GET /api/search order=- status=502 upstream=search-svc latency=10004ms"),
        ],
        "checkout": [
            (s(-latency / 1000 + 0.005), f"INFO  checkout req={req} order={order} step=confirm calling payments-svc trace={trace}"),
            (s(-0.003), f"ERROR checkout req={req} order={order} payments-svc call failed: deadline exceeded after 30000ms trace={trace}"),
            (s(-190.1), f"WARN  checkout order={o2} payment declined: card_declined"),
            (s(-410), f"INFO  checkout order={o3} step=confirm ok (188ms)"),
        ],
        "payments": [
            (s(-29.98), f"INFO  payments trace={trace} capture order={order} begin txn={txn}"),
            (s(-29.95), f"WARN  payments trace={trace} txn={txn} waiting for ledger write"),
            (s(-0.02), f"ERROR payments trace={trace} txn={txn} ledger write timed out after 30000ms"),
            (s(-190.2), f"WARN  payments trace=tr-{_hex(rng)} capture order={o2} declined code=card_declined"),
        ],
        "ledger-db": [
            (s(-29.94), f"WARN  ledger-db txn={txn} lock wait on table={table} blocked_by=pid:{pid}"),
            (s(-0.03), f"ERROR ledger-db txn={txn} lock wait timeout on table={table} blocked_by=pid:{pid}; rolled back"),
            (s(-520), f"WARN  ledger-db txn=tx-{rng.randint(10000, 99999)} lock wait on table={other_table} blocked_by=pid:{other_pid}"),
            (s(-519.8), f"INFO  ledger-db txn=tx-{rng.randint(10000, 99999)} lock acquired on table={other_table} after 180ms"),
        ],
        "scheduler": [
            (s(-840), f"INFO  scheduler job={job} pid={pid} started"),
            (s(-830), f"INFO  scheduler job={job} pid={pid} acquired lock table={table} mode=exclusive"),
            (s(120), f"INFO  scheduler job={job} pid={pid} released lock table={table} held=16m10s"),
            (s(-525), f"INFO  scheduler job={other_job} pid={other_pid} acquired lock table={other_table} mode=shared"),
            (s(-519), f"INFO  scheduler job={other_job} pid={other_pid} released lock table={other_table} held=6s"),
        ],
        "search": [(s(249.9), "ERROR search upstream timeout to index shard 3 after 10000ms")],
    }
    services = CORE_LOGS + EXTRA_LOGS[: max(0, logs - len(CORE_LOGS))]
    start, end = T - timedelta(minutes=18), T + timedelta(minutes=6)
    files = {
        f"logs/{svc}.log": _log_file(svc, keys.get(svc, []), start, end, rng, log_tokens)
        for svc in services
    }
    files["RUNBOOK.md"] = (
        "# Checkout incidents\n\nRequests enter through the gateway, which calls checkout-svc. "
        "Checkout calls payments-svc, which writes to the ledger database. Batch jobs run from the "
        "scheduler. Each service logs to logs/<service>.log in UTC.\n"
    )
    expected = {"request_id": req, "trace_id": trace, "txn_id": txn, "locked_table": table, "blocking_job": job}
    prompt = (
        f"Customer order #{order} failed at checkout on 2026-11-20 around {T:%H:%M} UTC. The service "
        "logs are under logs/. Trace the failure to its root cause, following the ids from one "
        "service's log to the next; do not guess an id you have not read. Finish with a JSON object of "
        'exactly this form: {"request_id": "...", "trace_id": "...", "txn_id": "...", '
        '"locked_table": "...", "blocking_job": "..."}'
    )
    probe = (
        "Follow-up: at exactly what timestamp did the gateway log that failed request, and what "
        'latency did it report? Answer with JSON: {"gateway_timestamp": "...", "latency_ms": <int>}'
    )
    other_q = (
        "A different checkout failed with HTTP 402 a few minutes before that incident. Which order "
        'was it, and what decline code did payments log? Answer with JSON: {"order": <int>, "code": "..."}'
    )
    lock_q = (
        "According to the scheduler log, how long did the blocking job hold its lock, and what was "
        'its pid? Answer with JSON: {"held": "...", "pid": <int>}'
    )
    return Task(
        name="incident_trace",
        files=files,
        turns=[
            Turn("task", prompt, expected, _grade_fields),
            Turn("probe", probe, {"gateway_timestamp": _ts(T), "latency_ms": latency}, _grade_incident_probe),
            Turn("followup", other_q, {"order": o2, "code": "card_declined"}, _grade_fields),
            Turn("followup", lock_q, {"held": "16m10s", "pid": pid}, _grade_fields),
        ],
    )


def _grade_fields(ans: Any, exp: dict) -> tuple[float, dict]:
    """Field-by-field exact match: ints as ints, lists as sets of names, strings normalised."""
    if not isinstance(ans, dict):
        return 0.0, {"parsed": False}
    detail = {}
    for k, v in exp.items():
        got = ans.get(k)
        if isinstance(v, bool) or not isinstance(v, (int, list)):
            detail[k] = _clean(got if got is not None else "") == _clean(v)
        elif isinstance(v, int):
            detail[k] = _as_int(got) == v
        else:
            detail[k] = isinstance(got, list) and {_norm(x) for x in got} == {_norm(x) for x in v}
    return round(sum(detail.values()) / len(detail), 3), detail


def _grade_incident_probe(ans: Any, exp: dict) -> tuple[float, dict]:
    if not isinstance(ans, dict):
        return 0.0, {"parsed": False}
    ts_ok = exp["gateway_timestamp"].rstrip("Z") in str(ans.get("gateway_timestamp", ""))
    lat_ok = _as_int(str(ans.get("latency_ms", "")).removesuffix("ms")) == exp["latency_ms"]
    return (ts_ok + lat_ok) / 2, {"gateway_timestamp": ts_ok, "latency_ms": lat_ok}


BUILDERS: dict[str, Callable[..., Task]] = {"service_audit": service_audit, "incident_trace": incident_trace}


def build_task(name: str, seed: int, size: str) -> Task:
    return BUILDERS[name](seed, **SIZES[size])
