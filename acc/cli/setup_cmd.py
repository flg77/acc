"""``acc-cli setup`` — a guided first run that validates as it goes.

    acc-cli setup [<section>] [--quick] [--reconfigure]
    acc-cli setup --answers FILE [--dry-run]
    acc-cli setup --from-env
    acc-cli setup --show-answers

Every value is checked at the point of entry, and the whole set is validated
before anything is written: a setup that half-applies leaves the deployment in
a shape no answer set describes.

The posture section states the consequence of each choice in the question
itself. A governance floor that got its value by default is a floor nobody
decided on.
"""

from __future__ import annotations

import argparse
import json
import sys

from acc import setup_flow


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("setup", help="Guided first-run configuration.")
    p.add_argument(
        "section", nargs="?", default=None,
        help="Run one section only (posture, model, storage).",
    )
    p.add_argument("--quick", action="store_true", help="Only ask for unset values.")
    p.add_argument(
        "--reconfigure", action="store_true",
        help="Ask for everything, including values already set.",
    )
    p.add_argument("--answers", default=None, help="JSON answer set (non-interactive).")
    p.add_argument(
        "--from-env", action="store_true",
        help="Take answers from ACC_SETUP_* environment variables.",
    )
    p.add_argument("--dry-run", action="store_true", help="Validate, write nothing.")
    p.add_argument(
        "--show-answers", action="store_true",
        help="Print an answer-set template and exit.",
    )
    p.set_defaults(func=_cmd_setup)


def _safe() -> None:
    try:
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, OSError):  # pragma: no cover
        pass


def _template() -> dict[str, object]:
    out: dict[str, object] = {}
    for section in setup_flow.sections():
        for question in section.questions:
            out[question.key] = question.default
    return out


def _ask(question: setup_flow.Question, current) -> str | None:
    """Ask one question, re-asking until the answer validates or is skipped."""
    options = question.options()
    suggestion = current if current not in (None, "") else question.default
    hint = f" [{'/'.join(options)}]" if options else ""
    shown = f" ({suggestion!r})" if suggestion not in (None, "") else ""

    print()
    print(f"  {question.prompt}{hint}{shown}")
    if question.consequence:
        print(f"    {question.consequence}")

    while True:
        try:
            raw = input("    > ").strip()
        except EOFError:
            return None
        if not raw:
            if suggestion in (None, ""):
                return ""
            raw = str(suggestion)
        problem = setup_flow.check_answer(question, raw)
        if not problem:
            return raw
        print(f"    not accepted: {problem}")


def _cmd_setup(args: argparse.Namespace) -> int:
    from acc import configstore as store

    _safe()

    if args.show_answers:
        print(json.dumps(_template(), indent=2))
        return 0

    try:
        chosen = [setup_flow.section(args.section).name] if args.section else None
    except setup_flow.SetupError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    # Non-interactive paths first: provisioning must not need a terminal.
    answers: dict[str, str] | None = None
    if args.answers:
        try:
            answers = setup_flow.load_answers(args.answers)
        except setup_flow.SetupError as exc:
            print(str(exc), file=sys.stderr)
            return 2
    elif args.from_env:
        answers = setup_flow.answers_from_env()
        if not answers:
            print("no ACC_SETUP_* variables set", file=sys.stderr)
            return 2

    if answers is None:
        if not sys.stdin.isatty():
            print(
                "setup needs a terminal, or --answers FILE / --from-env for "
                "non-interactive provisioning",
                file=sys.stderr,
            )
            return 2
        answers = {}
        for section in setup_flow.sections():
            if chosen and section.name not in chosen:
                continue
            print()
            print(f"  == {section.title} ==")
            for question in section.questions:
                current = store.get(question.key).value
                present = store.get(question.key).present
                if args.quick and present and not args.reconfigure:
                    continue
                given = _ask(question, current)
                if given is None:
                    print("\n  cancelled; nothing was written")
                    return 1
                answers[question.key] = given

    try:
        outcomes = setup_flow.apply_answers(
            answers, only=chosen, quick=args.quick, dry_run=args.dry_run
        )
    except setup_flow.SetupError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print()
    written = [o for o in outcomes if o.written]
    skipped = [o for o in outcomes if not o.written]
    for outcome in outcomes:
        mark = "wrote" if outcome.written else "kept "
        note = f"  ({outcome.error})" if outcome.error else ""
        print(f"  {mark} {outcome.key} = {outcome.value!r}{note}")
    print()
    print(f"  {len(written)} written, {len(skipped)} unchanged")

    if args.dry_run:
        print("  dry run — nothing was written")
        return 0

    broken = setup_flow.verify()
    print()
    if broken:
        print("  setup finished, but the deployment is not yet healthy:")
        for item in broken:
            print(f"    {item}")
        print()
        print("  `acc-cli doctor` explains each; most are a missing credential.")
        return 1
    print("  setup complete — `acc-cli doctor` reports no broken checks")
    return 0
