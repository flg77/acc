#!/usr/bin/env python3
"""mode_ab.py — the M1-04 Mode 1 / Mode 2 A/B through ACC's own backends.

Sends one labelled prompt set to several *arms* (a backend + model + system
prompt + optional thinking switch) and grades each answer against the prompt's
accepted answers. Every call goes through the same backend class a running ACC
agent uses, so an arm measures what a role would see.

    python tools/mode_ab/mode_ab.py run --arms tools/mode_ab/arms.example.yaml \
        --only qwen-off-strict,qwen-on --out results.jsonl
    python tools/mode_ab/mode_ab.py summarize results.jsonl \
        --fast qwen-off-strict --slow qwen-on

Credentials come from the environment (``--env-file`` loads a ``.env`` first);
values are never printed. Arms run one after another so one arm's load on a
shared gateway never skews another arm's latency.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import statistics
import sys
import time
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO))

SYSTEMS = {
    "strict": (
        "Answer immediately in one pass. Do not show any working or explanation. "
        "Reply with exactly one line of the form 'ANSWER: <answer>'."
    ),
    "work": (
        "You answer questions. Keep any working short. "
        "End your reply with one final line of the form 'ANSWER: <answer>' "
        "containing only the answer."
    ),
}


def load_env_file(path: Path) -> None:
    """KEY=VALUE lines into os.environ, without overriding what is already set."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def make_backend(arm: dict):
    kind = arm["backend"]
    if kind == "openai_compat":
        from acc.backends.llm_openai_compat import OpenAICompatBackend

        return OpenAICompatBackend(
            base_url=arm["base_url"],
            model=arm["model"],
            api_key_env=arm.get("api_key_env", ""),
            timeout_s=int(arm.get("timeout_s", 300)),
            max_retries=int(arm.get("max_retries", 1)),
        )
    # Anything else is a backend plugin, built the way ACC builds one: it must be
    # installed (an `acc.llm_backends` entry point) AND named in
    # ACC_LLM_BACKEND_PLUGINS -- the same two acts, the same refusals.
    from acc.backends import plugins

    try:
        return plugins.build(kind, {
            "model": arm.get("model", ""),
            "base_url": arm.get("base_url", ""),
            "api_key_env": arm.get("api_key_env", ""),
            "request_timeout_s": int(arm.get("timeout_s", 300)),
            "max_retries": int(arm.get("max_retries", 1)),
        })
    except plugins.BackendPluginError as exc:
        raise SystemExit(f"arm {arm['name']}: {exc}") from None


def extract(text: str) -> str:
    m = re.findall(r"ANSWER:\s*(.+)", text, re.IGNORECASE)
    if m:
        return m[-1].strip()
    b = re.findall(r"\\boxed\{([^}]*)\}", text)
    if b:
        return b[-1].strip()
    lines = text.strip().splitlines()
    return lines[-1].strip() if lines else ""


def norm(s: str) -> str:
    s = s.lower().strip().strip("*`\"'").strip().strip("*").strip()
    s = re.sub(r"[$%]|\bcents?\b|\bminutes?\b|\bday\b|\bsheep\b|\bsisters?\b", "", s)
    return s.strip().rstrip(".").strip()


def correct(ans: str, accept: list[str]) -> bool:
    """Exact after normalising, or the answer *leads* with the accepted token
    ("9 sheep are left." → 9). "2 sisters." still fails against 1."""
    a = norm(ans)
    return any(a == norm(x) or re.match(rf"{re.escape(norm(x))}(?![\w.])", a) for x in accept)


async def one(arm: dict, backend, sem: asyncio.Semaphore, p: dict) -> dict:
    system = SYSTEMS[arm.get("system", "work")]
    prompt = p["prompt"] + arm.get("switch", "")
    async with sem:
        t0 = time.perf_counter()
        err, text, usage = "", "", {}
        try:
            r = await backend.complete(system, prompt)
            text = r.get("text") or r.get("content") or ""
            usage = r.get("usage") or {}
        except Exception as exc:  # record it; one failed call must not end the batch
            err = f"{type(exc).__name__}: {str(exc)[:300]}"
        dt = time.perf_counter() - t0
    ans = extract(text) if text else ""
    return {
        "id": p["id"], "label": p["label"], "consequence": p.get("consequence", "LOW"),
        "arm": arm["name"], "model": f"{arm['backend']}/{arm.get('model', '')}",
        "latency_s": round(dt, 2), "answer": ans, "accept": p["accept"],
        "correct": bool(text) and correct(ans, p["accept"]),
        "in_tok": usage.get("input_tokens", usage.get("prompt_tokens")),
        "out_tok": usage.get("output_tokens", usage.get("completion_tokens")),
        "list_usd": usage.get("list_price_usd_equivalent"),
        "error": err, "raw_tail": text[-400:],
    }


async def run(args) -> None:
    load_env_file(Path(args.env_file))
    arms = yaml.safe_load(Path(args.arms).read_text(encoding="utf-8"))["arms"]
    if args.only:
        wanted = args.only.split(",")
        arms = [a for a in arms if a["name"] in wanted]
        missing = set(wanted) - {a["name"] for a in arms}
        if missing:
            raise SystemExit(f"no such arm(s): {', '.join(sorted(missing))}")
    prompts = json.loads(Path(args.prompts).read_text(encoding="utf-8"))
    rows: list[dict] = []
    for arm in arms:
        backend = make_backend(arm)
        sem = asyncio.Semaphore(int(arm.get("concurrency", 4)))
        for rep in range(args.repeat):
            batch = await asyncio.gather(*[one(arm, backend, sem, p) for p in prompts])
            for r in batch:
                r["rep"] = rep
            rows += batch
        ok = sum(r["correct"] for r in rows if r["arm"] == arm["name"])
        n = sum(1 for r in rows if r["arm"] == arm["name"])
        print(f"{arm['name']}: {ok}/{n} correct", flush=True)
    with Path(args.out).open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    print(f"wrote {len(rows)} rows -> {args.out}")


def summarize(args) -> None:
    rows = [json.loads(l) for l in Path(args.results).read_text(encoding="utf-8").splitlines() if l]
    arms = list(dict.fromkeys(r["arm"] for r in rows))
    print(f"{'arm':22} {'mode1':>6} {'mode2':>6} {'amb':>6} {'total':>7} {'med_s':>6} {'max_s':>6} {'out_tok':>8} err")
    for a in arms:
        rs = [r for r in rows if r["arm"] == a]
        cell = lambda lab: f"{sum(r['correct'] for r in rs if r['label'] == lab)}/{sum(1 for r in rs if r['label'] == lab)}"
        lat = [r["latency_s"] for r in rs]
        print(f"{a:22} {cell('mode1'):>6} {cell('mode2'):>6} {cell('ambiguous'):>6} "
              f"{sum(r['correct'] for r in rs)}/{len(rs):<4} {statistics.median(lat):6.1f} {max(lat):6.1f} "
              f"{sum(r['out_tok'] or 0 for r in rs):8} {sum(bool(r['error']) for r in rs)}")
    misses = [r for r in rows if not r["correct"]]
    if misses:
        print("\nmisses:")
        for r in sorted(misses, key=lambda r: (r["id"], r["arm"])):
            print(f"  {r['arm']:22} {r['id']:7} got={r['answer'][:40]!r} want={r['accept']} {r['error'][:60]}")
    if args.fast and args.slow:
        by = {(r["arm"], r["id"], r.get("rep", 0)): r for r in rows}
        keys = sorted({(r["id"], r.get("rep", 0)) for r in rows})
        meta = {r["id"]: r for r in rows}

        def policy(name, pick):
            ch = [by[(pick(i), i, rep)] for i, rep in keys]
            print(f"  {name:44} {sum(c['correct'] for c in ch)}/{len(ch)}  slow calls={sum(c['arm'] == args.slow for c in ch):3}"
                  f"  med={statistics.median(c['latency_s'] for c in ch):5.1f}s  out_tok={sum(c['out_tok'] or 0 for c in ch)}")

        print(f"\nrouting between fast={args.fast} and slow={args.slow}:")
        policy("always fast", lambda i: args.fast)
        policy("always slow", lambda i: args.slow)
        policy("M1-02 rules (label mode1 & not HIGH -> fast)",
               lambda i: args.fast if meta[i]["label"] == "mode1" and meta[i]["consequence"] != "HIGH" else args.slow)
        policy("oracle (fast whenever fast was right)",
               lambda i: args.fast if by[(args.fast, i, 0)]["correct"] else args.slow)


def export_golden(args) -> None:
    """Write the prompt set as golden prompts for ``acc-cli e2e run --root``.

    The reply must end in an ``ANSWER:`` line matching an accepted answer;
    latency is recorded by e2e (``elapsed_ms``) but not asserted.
    """
    prompts = json.loads(Path(args.prompts).read_text(encoding="utf-8"))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for p in prompts:
        alts = "|".join(re.escape(a) for a in p["accept"])
        doc = {
            "name": f"mab_{p['id'].replace('-', '_')}",
            "description": f"M1-04 mode A/B prompt {p['id']} (label {p['label']}, "
                           f"consequence {p.get('consequence', 'LOW')}). Generated by "
                           "tools/mode_ab/mode_ab.py export-golden; edit prompts.json, not this file.",
            "prompt": p["prompt"] + "\n\nEnd your reply with one final line of the form "
                                    "'ANSWER: <answer>' containing only the answer.",
            "target_role": args.role,
            "timeout_s": float(args.timeout),
            "expects": {
                "reply_non_empty": True,
                "blocked": False,
                # ANSWER: line, or \boxed{...} — reasoning models (R1-distill)
                # box the answer; `run` accepts both, so e2e does too.
                "output_matches_regex": rf"(?:ANSWER:\W*|\\boxed\{{\s*)(?:{alts})(?!\w|\.\d)",
            },
        }
        (out / f"{doc['name']}.yaml").write_text(
            yaml.safe_dump(doc, sort_keys=False, allow_unicode=True), encoding="utf-8")
    print(f"wrote {len(prompts)} golden prompts -> {out} (target_role={args.role})")


def compare_e2e(args) -> None:
    """Side-by-side summary of ``acc-cli e2e run --history`` files, one per collective."""
    labels = {f"mab_{p['id'].replace('-', '_')}": p["label"]
              for p in json.loads(Path(args.prompts).read_text(encoding="utf-8"))}
    print(f"{'history':34} {'mode1':>6} {'mode2':>6} {'amb':>6} {'total':>7} {'med_s':>6} {'max_s':>6} err")
    for path in args.histories:
        rows = [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines() if l]
        rows = [r for r in rows if r.get("name") in labels]
        if not rows:
            print(f"{Path(path).name:34} (no mode_ab rows)")
            continue
        cell = lambda lab: f"{sum(bool(r.get('passed')) for r in rows if labels[r['name']] == lab)}/{sum(1 for r in rows if labels[r['name']] == lab)}"
        lat = [int(r.get("elapsed_ms", 0)) / 1000 for r in rows]
        print(f"{Path(path).name:34} {cell('mode1'):>6} {cell('mode2'):>6} {cell('ambiguous'):>6} "
              f"{sum(bool(r.get('passed')) for r in rows)}/{len(rows):<4} {statistics.median(lat):6.1f} {max(lat):6.1f} "
              f"{sum(bool(r.get('error')) for r in rows)}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="send the prompt set to each arm")
    r.add_argument("--arms", default=str(HERE / "arms.example.yaml"))
    r.add_argument("--prompts", default=str(HERE / "prompts.json"))
    r.add_argument("--only", default="", help="comma-separated arm names")
    r.add_argument("--repeat", type=int, default=1, help="samples per prompt")
    r.add_argument("--env-file", default=".env")
    r.add_argument("--out", default="mode_ab_results.jsonl")
    s = sub.add_parser("summarize", help="per-arm table, misses, routing simulation")
    s.add_argument("results")
    s.add_argument("--fast", default="", help="the Mode 1 arm for the routing simulation")
    s.add_argument("--slow", default="", help="the Mode 2 arm for the routing simulation")
    g = sub.add_parser("export-golden", help="write the prompt set as e2e golden prompts")
    g.add_argument("--prompts", default=str(HERE / "prompts.json"))
    # analyst, not assistant: on lighthouse (PB-13 Part C, 2026-09-27) the assistant
    # reached for python_exec/shell_exec on arithmetic, each a HIGH oversight item
    # that blocks for a human — every prompt timed out. analyst has no exec skills.
    g.add_argument("--role", default="analyst", help="target_role for every prompt")
    g.add_argument("--timeout", type=float, default=300.0)
    g.add_argument("--out", default=str(HERE / "golden"))
    c = sub.add_parser("compare-e2e", help="summarize acc-cli e2e --history files side by side")
    c.add_argument("histories", nargs="+")
    c.add_argument("--prompts", default=str(HERE / "prompts.json"))
    args = ap.parse_args()
    {"run": lambda a: asyncio.run(run(a)), "summarize": summarize,
     "export-golden": export_golden, "compare-e2e": compare_e2e}[args.cmd](args)


if __name__ == "__main__":
    main()
