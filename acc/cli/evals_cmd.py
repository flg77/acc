"""``acc-cli evals …`` -- the evaluation attachments of a profile (058 Phase 4).

* ``status``            -- what ``evals.*`` is set to here.
* ``promptfoo-config``  -- write a promptfoo config from the golden prompts,
  aimed at ACC's OpenAI-compatible endpoint.
* ``garak-gate``        -- compare a garak report with the baseline; exit 1 on
  a regression.  ``--accept`` makes the run the new baseline.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml


def register(sub: argparse._SubParsersAction) -> None:
    ev = sub.add_parser("evals", help="promptfoo and Garak attached to a profile.")
    es = ev.add_subparsers(dest="evals_command", required=True, metavar="ACTION")

    st = es.add_parser("status", help="Show the evals settings.")
    st.set_defaults(func=_cmd_status)

    pf = es.add_parser("promptfoo-config", help="Write a promptfoo config from the golden prompts.")
    pf.add_argument("--out", required=True, help="Where to write promptfooconfig.yaml.")
    pf.add_argument("--base-url", default="http://acc-webgui:8080/v1",
                    help="ACC's OpenAI-compatible endpoint.")
    pf.add_argument("--prefix", default="", help="Only golden prompts whose name starts with this.")
    pf.add_argument("--root", default=None, help="Golden prompt directory.")
    pf.set_defaults(func=_cmd_promptfoo)

    gg = es.add_parser("garak-gate", help="Gate a garak report against the baseline.")
    gg.add_argument("--report", required=True, help="garak's report JSONL.")
    gg.add_argument("--baseline", required=True, help="Baseline JSON (written by --accept).")
    gg.add_argument("--tolerance", type=float, default=0.02)
    gg.add_argument("--accept", action="store_true", help="Make this run the baseline.")
    gg.set_defaults(func=_cmd_garak)


def _cmd_status(args: argparse.Namespace) -> int:
    from acc.config import load_config

    cfg = load_config()
    print(json.dumps(cfg.evals.model_dump(), indent=2))
    return 0


def _cmd_promptfoo(args: argparse.Namespace) -> int:
    from acc.evals.promptfoo import build_config
    from acc.golden_prompts import load_all

    prompts = load_all(Path(args.root)) if args.root else load_all()
    cfg = build_config(prompts, base_url=args.base_url, name_prefix=args.prefix)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True), encoding="utf-8")
    print(f"wrote {out} — {len(cfg['tests'])} test(s) over {len(cfg['providers'])} role(s)")
    return 0


def _cmd_garak(args: argparse.Namespace) -> int:
    from acc.evals.garak import gate, load_report

    current = load_report(args.report)
    base_path = Path(args.baseline)
    baseline = json.loads(base_path.read_text(encoding="utf-8")) if base_path.is_file() else None
    ok, regressions = gate(current, baseline, tolerance=args.tolerance)
    for probe, v in sorted(current.items()):
        print(f"  {probe:<16} {v['passed']}/{v['total']}  {v['rate']:.2%}")
    if args.accept or baseline is None:
        base_path.parent.mkdir(parents=True, exist_ok=True)
        base_path.write_text(json.dumps(current, indent=2), encoding="utf-8")
        print(f"baseline {'written' if baseline is None else 'replaced'}: {base_path}")
    if not ok:
        print("garak gate: BLOCKED —\n  " + "\n  ".join(regressions), file=sys.stderr)
        return 1
    print("garak gate: pass")
    return 0
