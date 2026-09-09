"""``acc-cli compliance`` -- the frameworks and their external vocabulary.

`20260908-asago-alignment` AS-01.  ``mappings`` exports what ACC's
frameworks (and, where the file exists, the threat model) say in Risk Atlas
Nexus ids -- as an SSSOM TSV asago and the Nexus read, or as JSON;
``nexus <id>`` answers "what in ACC addresses this external risk?", the
lookup a policy's ``risk-extraction.json`` needs; ``coverage`` says how much
of each framework speaks the vocabulary at all.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("compliance", help="Frameworks, the threat model, and their Risk Atlas Nexus vocabulary.")
    sp = p.add_subparsers(dest="compliance_command", required=True, metavar="ACTION")

    mp = sp.add_parser("mappings", help="Export the control -> Nexus id mappings (SSSOM TSV by default).")
    mp.add_argument("--framework", default="", help="Only this framework_id (default: every loaded one).")
    mp.add_argument("--json", action="store_true", help="JSON rows instead of SSSOM TSV.")
    mp.add_argument("-o", "--out", default="-", help="Write to a file instead of stdout.")
    mp.set_defaults(func=_cmd_mappings)

    nx = sp.add_parser("nexus", help="What in ACC answers to one Nexus id (a risk from an asago extraction).")
    nx.add_argument("nexus_id")
    nx.add_argument("--json", action="store_true")
    nx.set_defaults(func=_cmd_nexus)

    cv = sp.add_parser("coverage", help="Per framework: controls carrying a Nexus id; unknown ids.")
    cv.add_argument("--json", action="store_true")
    cv.set_defaults(func=_cmd_coverage)

    rk = sp.add_parser("risks", help="Drop in an asago risk extraction: per policy risk, the ACC controls and "
                                     "threats that answer to it and whether a loaded rule covers them.")
    rk.add_argument("path", help="asago risk-extraction.json (or .yaml) from `asago-policy-mapper extract`.")
    rk.add_argument("--no-gaps", action="store_true", help="Skip the gap analysis (no covered / GAP column).")
    rk.add_argument("--json", action="store_true")
    rk.set_defaults(func=_cmd_risks)


def _frameworks(only: str = ""):
    from acc.frameworks import load_all_frameworks  # noqa: PLC0415
    fws = load_all_frameworks()
    if only:
        fws = [f for f in fws if f.framework_id == only]
        if not fws:
            print(f"compliance: no framework {only!r}", file=sys.stderr)
    return fws


def _cmd_mappings(args: argparse.Namespace) -> int:
    from acc import nexus as N  # noqa: PLC0415
    fws = _frameworks(args.framework)
    if args.framework and not fws:
        return 1
    rows = N.sssom_rows(fws)
    text = json.dumps(rows, indent=2, ensure_ascii=False) + "\n" if args.json else N.render_sssom(rows)
    if args.out and args.out != "-":
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"compliance: wrote {len(rows)} mapping(s) -> {args.out}", file=sys.stderr)
    else:
        sys.stdout.write(text)
    return 0


def _cmd_nexus(args: argparse.Namespace) -> int:
    from acc import nexus as N  # noqa: PLC0415
    fws = _frameworks()
    vocab = N.vocabulary()
    hits = N.nexus_index(fws).get(args.nexus_id, [])
    by_id = {f.framework_id: f for f in fws}
    out = {
        "nexus_id": args.nexus_id,
        "known": vocab.known(args.nexus_id),
        "taxonomy": vocab.taxonomy_of(args.nexus_id),
        "controls": [
            {"framework_id": fid, "control_id": cid,
             "title": next((c.title for c in by_id[fid].controls if c.control_id == cid), "")}
            for fid, cid in hits
        ],
    }
    if args.json:
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return 0
    known = "known" if out["known"] else "NOT in the pinned vocabulary"
    print(f"{args.nexus_id} ({out['taxonomy'] or 'no taxonomy'}, {known}): {len(hits)} ACC control(s)")
    for c in out["controls"]:
        print(f"  {c['framework_id']:<20} {c['control_id']:<12} {c['title'][:70]}")
    return 0 if hits else 2


def _cmd_coverage(args: argparse.Namespace) -> int:
    from acc import nexus as N  # noqa: PLC0415
    fws = _frameworks()
    cov = N.coverage(fws)
    unknown = N.unknown_ids(fws)
    vocab = N.vocabulary()
    if args.json:
        print(json.dumps({"vocabulary": {"source": vocab.source, "fetched": vocab.fetched, "ids": vocab.size},
                          "coverage": cov, "unknown": [list(u) for u in unknown]}, indent=2))
        return 0 if not unknown else 1
    print(f"vocabulary: {vocab.size} pinned id(s), fetched {vocab.fetched}")
    for fid, c in cov.items():
        print(f"  {fid:<22} {c['mapped']:>3}/{c['controls']:<3} control(s) carry a Nexus id")
    for fid, cid, nid in unknown:
        print(f"  UNKNOWN id {nid!r} on {fid}/{cid}")
    return 0 if not unknown else 1


def _cmd_risks(args: argparse.Namespace) -> int:
    from acc import nexus as N  # noqa: PLC0415
    try:
        risks = N.load_risk_extraction(args.path)
    except (OSError, ValueError) as exc:
        print(f"compliance: {exc}", file=sys.stderr)
        return 1
    fws = _frameworks()
    covered = None if args.no_gaps else N.gap_coverage_map(fws)
    rows = N.join_risks(risks, fws, gap_covered=covered)
    if args.json:
        print(json.dumps({"source": str(args.path), "risks": rows}, indent=2, ensure_ascii=False))
        return 0 if all(r["controls"] for r in rows) else 2
    unanswered = 0
    print(f"{len(rows)} risk(s) in {args.path}")
    for r in rows:
        conf = f" conf={float(r['confidence']):.2f}" if isinstance(r.get("confidence"), (int, float)) else ""
        known = "" if r["known"] else "  [NOT in the pinned vocabulary]"
        via = f"  (via cross-mapping {r['via_cross_mapping']})" if r["via_cross_mapping"] else ""
        print(f"\n{r['nexus_id']}{conf}{known}{via}")
        for e in r["evidence"][:1]:
            print(f"    evidence: {e!r}")
        if not r["controls"]:
            unanswered += 1
            print("    -> nothing in ACC answers to this risk")
        for ctl in r["controls"]:
            mark = "" if ctl["covered"] is None else ("  covered" if ctl["covered"] else "  GAP")
            print(f"    -> {ctl['framework_id']:<20} {ctl['control_id']:<12} {ctl['title'][:60]}{mark}")
    print(f"\n{len(rows) - unanswered}/{len(rows)} risk(s) answered by ACC; {unanswered} unanswered")
    return 0 if unanswered == 0 else 2
