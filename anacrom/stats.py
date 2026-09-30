"""What Jev has been doing, from the files it leaves behind.

Three files in the config folder carry the record:

``jev.jsonl``      one row per judged tick: the state, the options, every
                   answer with its probabilities, and what we did about it.
``runs.jsonl``     one row per ``uo jev`` run, written when it ends: calls,
                   tokens, gold, kills.  This is where totals come from,
                   because the decision log leaves out the loot questions.
``creatures.json`` what Jev decided about each creature name.

Nothing here talks to the daemon, so ``uo stats`` works with it stopped.
"""
from __future__ import annotations

import json
import re
import statistics
import time
from collections import Counter
from pathlib import Path

# Per-run fields worth keeping; the terminal transcript is left out.
RUN_FIELDS = (
    "stopped", "seconds", "ticks", "actions", "jev_calls", "model",
    "median_latency_ms", "input_tokens", "cost_usd", "health", "gold",
    "kills", "kills_by_name", "items_taken", "position", "map",
)

CONFIDENCE_BINS = 10


def record_run(path: Path, summary: dict, started_at: float, character: str = "") -> None:
    """Append one finished run to ``runs.jsonl``."""
    row = {"started_at": round(started_at, 1), "character": character}
    row.update({k: summary[k] for k in RUN_FIELDS if k in summary})
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as log:
            log.write(json.dumps(row) + "\n")
    except OSError:
        pass


def read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    try:
        with open(path, encoding="utf-8") as log:
            for line in log:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue                          # a half-written last line
    except OSError:
        pass
    return rows


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * fraction))]


def _verdict(prey: float, threat: float, learned: bool) -> str:
    """The same thresholds the autopilot applies (autopilot.PREY_BAR, TOO_STRONG)."""
    from .autopilot import PREY_BAR, TOO_STRONG
    if prey < PREY_BAR:
        return "ignore"
    return "avoid" if threat >= TOO_STRONG or learned else "hunt"


def build_stats(folder: Path) -> dict:
    folder = Path(folder)
    runs = read_jsonl(folder / "runs.jsonl")
    decisions = [r for r in read_jsonl(folder / "jev.jsonl") if not r.get("dry_run")]
    try:
        creatures = json.loads((folder / "creatures.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        creatures = {}

    # -- runs ---------------------------------------------------------------
    run_rows = []
    kills_by_name: Counter = Counter()
    for run in runs:
        gold = run.get("gold") or [0, 0]
        kills_by_name.update(run.get("kills_by_name") or {})
        run_rows.append({
            "started_at": run.get("started_at"),
            "seconds": run.get("seconds", 0),
            "calls": run.get("jev_calls", 0),
            "tokens": run.get("input_tokens", 0),
            "cost_usd": run.get("cost_usd", 0.0),
            "gold": gold[1] - gold[0],
            "kills": run.get("kills"),                 # None: run predates counting
            "items": run.get("items_taken"),
            "stopped": run.get("stopped", ""),
            "actions": run.get("actions", {}),
        })
    seconds = sum(r["seconds"] for r in run_rows)
    gold = sum(r["gold"] for r in run_rows)
    died = sum(1 for r in run_rows if r["stopped"].startswith("we died"))

    # -- decisions ----------------------------------------------------------
    actions = Counter(r["decision"]["action"] for r in decisions)
    sources = Counter(r["decision"]["source"] for r in decisions)
    by_action: dict[str, list[float]] = {}
    latencies = []
    confidences = []
    for row in decisions:
        decision = row["decision"]
        if row.get("seconds") is not None:
            latencies.append(row["seconds"])
        if decision["source"] == "jev" and decision.get("confidence") is not None:
            confidences.append(decision["confidence"])
            by_action.setdefault(decision["action"], []).append(decision["confidence"])
    histogram = [0] * CONFIDENCE_BINS
    for value in confidences:
        histogram[min(CONFIDENCE_BINS - 1, int(value * CONFIDENCE_BINS))] += 1
    # Jev being unsure is worth seeing: decisions the bar turned into a fallback.
    fallbacks = Counter()
    for row in decisions:
        if row["decision"]["source"] == "fallback":
            found = re.match(r"unsure \((\w+)", row["decision"].get("reason", ""))
            fallbacks[found.group(1) if found else "other"] += 1

    # -- creatures ----------------------------------------------------------
    creature_rows = []
    for name, info in creatures.items():
        creature_rows.append({
            "name": name,
            "prey": info.get("prey", 0.0),
            "threat": info.get("threat", 0.0),
            "learned": bool(info.get("learned")),
            "verdict": _verdict(info.get("prey", 0.0), info.get("threat", 0.0),
                                bool(info.get("learned"))),
            "kills": kills_by_name.get(name, 0),
        })
    creature_rows.sort(key=lambda c: (-c["kills"], c["name"]))

    tokens = sum(r["tokens"] for r in run_rows)
    calls = sum(r["calls"] for r in run_rows)
    return {
        "generated_at": round(time.time(), 1),
        "totals": {
            "runs": len(run_rows),
            "hours": round(seconds / 3600, 2),
            "calls": calls,
            "tokens": tokens,
            "cost_usd": round(sum(r["cost_usd"] for r in run_rows), 4),
            "gold": gold,
            "kills": sum(r["kills"] or 0 for r in run_rows),
            "items": sum(r["items"] or 0 for r in run_rows),
            "deaths": died,
            "gold_per_hour": round(gold / (seconds / 3600)) if seconds else 0,
            "tokens_per_call": round(tokens / calls) if calls else 0,
            "cost_per_1k_gold": round(sum(r["cost_usd"] for r in run_rows) / gold * 1000, 4)
            if gold > 0 else None,
        },
        "runs": run_rows,
        "decisions": {
            "count": len(decisions),
            "actions": dict(actions.most_common()),
            "sources": dict(sources),
            "fallbacks": dict(fallbacks.most_common()),
            "confidence_histogram": histogram,
            "confidence_by_action": {
                a: round(statistics.mean(v), 3) for a, v in sorted(by_action.items())
            },
            "latency_ms": {
                "median": round(statistics.median(latencies) * 1000) if latencies else None,
                "p95": round(_percentile(latencies, 0.95) * 1000) if latencies else None,
                "max": round(max(latencies) * 1000) if latencies else None,
            },
        },
        "creatures": creature_rows,
        "kills_by_name": dict(kills_by_name.most_common()),
    }


# --------------------------------------------------------------------------
# text
# --------------------------------------------------------------------------

def render_text(stats: dict) -> str:
    t = stats["totals"]
    d = stats["decisions"]
    lines = [
        f"{t['runs']} runs, {t['hours']} hours with Jev",
        f"  calls {t['calls']:,}   tokens {t['tokens']:,}"
        f" ({t['tokens_per_call']:,}/call)   cost ${t['cost_usd']:.4f}",
        f"  gold {t['gold']:+,}  ({t['gold_per_hour']:,}/hour)   kills {t['kills']}"
        f"   items {t['items']}   deaths {t['deaths']}",
    ]
    if t["cost_per_1k_gold"] is not None:
        lines.append(f"  ${t['cost_per_1k_gold']:.4f} of Jev per 1,000 gold")
    if d["count"]:
        lines.append("")
        lines.append(f"{d['count']:,} decisions:  "
                     + "  ".join(f"{a} {n}" for a, n in d["actions"].items()))
        lat = d["latency_ms"]
        if lat["median"] is not None:
            lines.append(f"  latency median {lat['median']} ms, p95 {lat['p95']} ms")
        if d["fallbacks"]:
            lines.append("  held back for low confidence: "
                         + ", ".join(f"{a} {n}" for a, n in d["fallbacks"].items()))
    if stats["creatures"]:
        lines.append("")
        lines.append("creatures:")
        for c in stats["creatures"]:
            lines.append(f"  {c['name']:<26} {c['verdict']:<7} prey {c['prey']:.2f}"
                         f"  threat {c['threat']:.1f}"
                         + (f"  kills {c['kills']}" if c["kills"] else "")
                         + ("  (learned)" if c["learned"] else ""))
    return "\n".join(lines)
