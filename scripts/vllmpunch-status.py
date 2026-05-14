#!/usr/bin/env python3
"""Render vllmpunch's model catalogue annotated with ACC test status.

Reads operator's ``~/.config/vllmpunch/models.json`` (or ``$VLLMPUNCH_MODELS_JSON``)
and merges in a hard-coded fallback map for the entries that live only in
vllmpunch's bundled ``example.json`` catalogue.

Status taxonomy (matches the capacity report at
``test/history/vllmpunch-capacity-report-20260514.md``):

* ``pass``         — boots + smoke chat completion succeeds on lighthouse (12 GB)
* ``fail-config``  — weights load, KV cache budget negative; fixable via
                     ``max_model_len`` / ``gpu_memory_utilization``
* ``fail-oom``     — CUDA OOM at weight load; capacity exceeded
* ``fail-unknown`` — container died too fast for log capture; needs retry
* ``untested``     — not run through the capacity matrix (e.g. speech / TTS)

Usage::

    ./scripts/vllmpunch-status.py            # coloured table
    ./scripts/vllmpunch-status.py --no-color # for piping / CI
    ./scripts/vllmpunch-status.py --json     # raw merged data

This is a *viewer* — it never writes to ``models.json``.  The annotation
script ``annotate_models.py`` (operator-local, on lighthouse) is what
populates the ``acc_status`` / ``acc_status_note`` / ``acc_last_tested``
fields.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Fallback for catalogue-only entries (present in vllmpunch's bundled
# example.json but not in the operator's local models.json on lighthouse).
# Keyed by the catalogue *name* (top-level key in vllmpunch's models.json).
CATALOGUE_ONLY_STATUS: dict[str, dict[str, str]] = {
    "Llama-3.2-3B-Instruct-FP8": {
        "acc_status": "pass",
        "acc_status_note": "3B FP8; smoke 2.0s; recommended upgrade from 1B",
        "acc_last_tested": "2026-05-14",
    },
    "Qwen2.5-7B-Instruct-GPTQ-Int4": {
        "acc_status": "pass",
        "acc_status_note": "7B GPTQ-Int4; smoke 1.9s; general-purpose",
        "acc_last_tested": "2026-05-14",
    },
    "Qwen2.5-Coder-7B-Instruct-AWQ": {
        "acc_status": "pass",
        "acc_status_note": "7B AWQ-Int4; smoke 2.8s; ACC coding_agent pick",
        "acc_last_tested": "2026-05-14",
    },
    "Mistral-7B-Instruct-v0.3-W8A8": {
        "acc_status": "pass",
        "acc_status_note": "7B W8A8; smoke 3.0s; strong instruction-following",
        "acc_last_tested": "2026-05-14",
    },
    "Qwen2.5-Coder-1.5B-Instruct": {
        "acc_status": "fail-config",
        "acc_status_note": "loads 2.89 GiB then KV cache=-0.84 GiB; lower max_model_len",
        "acc_last_tested": "2026-05-14",
    },
    "Llama-3.1-8B-Instruct-FP8": {
        "acc_status": "fail-config",
        "acc_status_note": "KV cache -0.21 GiB; drop max_model_len 16384->8192",
        "acc_last_tested": "2026-05-14",
    },
    "Nemotron-3-Nano-Omni-FP8": {
        "acc_status": "fail-oom",
        "acc_status_note": "omni multimodal; container exited <15s; assumed OOM",
        "acc_last_tested": "2026-05-14",
    },
}

# ANSI colour codes.  Disabled when stdout is not a TTY or --no-color.
COLOURS = {
    "pass":         "\033[32m",  # green
    "fail-config":  "\033[33m",  # yellow
    "fail-oom":     "\033[31m",  # red
    "fail-unknown": "\033[35m",  # magenta
    "untested":     "\033[90m",  # grey
    "_reset":       "\033[0m",
    "_bold":        "\033[1m",
}

STATUS_LABEL = {
    "pass":         "PASS",
    "fail-config":  "FIXABLE",
    "fail-oom":     "OOM",
    "fail-unknown": "UNKNOWN",
    "untested":     "untested",
}


def default_models_path() -> Path:
    env = os.environ.get("VLLMPUNCH_MODELS_JSON")
    if env:
        return Path(env)
    return Path.home() / ".config" / "vllmpunch" / "models.json"


def load_models(path: Path) -> dict:
    if not path.is_file():
        print(f"models.json not found: {path}", file=sys.stderr)
        print(
            "Set VLLMPUNCH_MODELS_JSON, or run on the test host where "
            "vllmpunch is configured.",
            file=sys.stderr,
        )
        sys.exit(2)
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    # vllmpunch wraps entries under a top-level "models" key.  Older /
    # hand-edited variants may be flat — accept either shape.
    if isinstance(data, dict) and "models" in data and isinstance(data["models"], dict):
        return data["models"]
    return data


def merge_catalogue(models: dict) -> list[dict]:
    """Yield rows for rendering.

    Each row has: name, aliases (list), model_id, host_port, status, note,
    last_tested, source ("local" or "catalogue").
    """
    rows: list[dict] = []
    seen: set[str] = set()

    for name, entry in models.items():
        if not isinstance(entry, dict):
            continue
        seen.add(name)
        alias = entry.get("alias")
        aliases = entry.get("aliases", [])
        if alias and alias not in aliases:
            aliases = [alias, *aliases]
        rows.append({
            "name":         name,
            "aliases":      aliases,
            "model_id":     entry.get("model_id", ""),
            "host_port":    entry.get("host_port", ""),
            "status":       entry.get("acc_status", "untested"),
            "note":         entry.get("acc_status_note", ""),
            "last_tested":  entry.get("acc_last_tested", ""),
            "source":       "local",
        })

    for name, fb in CATALOGUE_ONLY_STATUS.items():
        if name in seen:
            continue
        rows.append({
            "name":         name,
            "aliases":      [],
            "model_id":     "",  # unknown without parsing example.json
            "host_port":    "",
            "status":       fb["acc_status"],
            "note":         fb["acc_status_note"],
            "last_tested":  fb["acc_last_tested"],
            "source":       "catalogue",
        })

    # Sort: pass first, then fail-config, fail-oom, fail-unknown, untested.
    order = {k: i for i, k in enumerate(
        ["pass", "fail-config", "fail-oom", "fail-unknown", "untested"]
    )}
    rows.sort(key=lambda r: (order.get(r["status"], 99), r["name"]))
    return rows


def render(rows: list[dict], use_colour: bool) -> str:
    def c(key: str) -> str:
        return COLOURS[key] if use_colour else ""

    headers = ("STATUS", "NAME / ALIAS", "PORT", "MODEL ID", "NOTE")
    widths = [9, 38, 5, 50, 60]

    def fmt_row(cells: tuple[str, ...]) -> str:
        return "  ".join(
            cell[:w].ljust(w) for cell, w in zip(cells, widths)
        )

    out: list[str] = []
    out.append(c("_bold") + fmt_row(headers) + c("_reset"))
    out.append("  ".join("-" * w for w in widths))

    counts: dict[str, int] = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
        label = STATUS_LABEL.get(r["status"], r["status"])
        colour = c(r["status"])
        reset = c("_reset")

        name_field = r["name"]
        if r["aliases"]:
            name_field = f"{r['name']}  [{','.join(r['aliases'])}]"
        if r["source"] == "catalogue":
            name_field += "  *"

        cells = (
            f"{colour}{label}{reset}",
            name_field,
            str(r["host_port"]),
            r["model_id"],
            r["note"],
        )
        # Manual ljust accounting for ANSI escapes in the status cell.
        status_pad = " " * max(0, widths[0] - len(label))
        line = (
            f"{colour}{label}{reset}{status_pad}  "
            + "  ".join(
                cell[:w].ljust(w) for cell, w in zip(cells[1:], widths[1:])
            )
        )
        out.append(line)

    out.append("")
    out.append("  *  = present only in vllmpunch's bundled catalogue, "
               "not in operator's models.json")
    out.append("")
    summary_bits = []
    for key in ("pass", "fail-config", "fail-oom", "fail-unknown", "untested"):
        if key in counts:
            summary_bits.append(
                f"{c(key)}{STATUS_LABEL[key]}{c('_reset')}: {counts[key]}"
            )
    out.append("Summary: " + "  ".join(summary_bits))
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--models-json",
        type=Path,
        default=default_models_path(),
        help="Path to vllmpunch's models.json (default: ~/.config/vllmpunch/models.json)",
    )
    ap.add_argument("--no-color", action="store_true",
                    help="Disable ANSI colour output")
    ap.add_argument("--json", action="store_true",
                    help="Emit merged data as JSON instead of a table")
    args = ap.parse_args()

    models = load_models(args.models_json)
    rows = merge_catalogue(models)

    if args.json:
        json.dump(rows, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0

    use_colour = (not args.no_color) and sys.stdout.isatty()
    print(render(rows, use_colour))
    return 0


if __name__ == "__main__":
    sys.exit(main())
