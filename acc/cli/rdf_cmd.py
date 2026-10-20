"""``acc-cli rdf`` — read ACC's shapes as RDF (RDF-01).

    acc-cli rdf vocab   [--format turtle|ntriples|jsonld]
    acc-cli rdf context
    acc-cli rdf convert {lesson,task,bom,okf} FILE [--collective C] [--bundle B]
                        [--format turtle|ntriples|jsonld]

Read-only and offline: nothing here touches NATS, Redis or a store.  ``convert``
reads a lesson or task as JSON, a bill of materials as YAML, an OKF concept as
markdown, and prints the graph :mod:`acc.lib.rdf` maps it to.

Persons are written pseudonymously when ``ACC_RDF_PERSON_KEY`` is set (the same
key gives the same person IRI in every graph) and as blank nodes otherwise.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

EXIT_OK = 0
EXIT_BAD_INPUT = 1

FORMATS = ("turtle", "ntriples", "jsonld")

#: The environment variable the person pseudonym key is read from.
PERSON_KEY_ENV = "ACC_RDF_PERSON_KEY"


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("rdf", help="Read ACC lessons, tasks, BOMs and OKF concepts as RDF.")
    sp = p.add_subparsers(dest="rdf_command", required=True, metavar="ACTION")

    vo = sp.add_parser("vocab", help="The ACC vocabulary (every acc: term, described).")
    vo.add_argument("--format", choices=FORMATS, default="turtle")
    vo.set_defaults(func=_cmd_vocab)

    cx = sp.add_parser("context", help="The JSON-LD @context ACC documents use.")
    cx.set_defaults(func=_cmd_context)

    tx = sp.add_parser("taxonomy", help="Check a domain taxonomy (taxonomy/domains.yaml) and print it as SKOS.")
    src = tx.add_mutually_exclusive_group(required=True)
    src.add_argument("--file", type=Path, help="A domains.yaml file.")
    src.add_argument("--pack", help="An installed pack (@scope/name) carrying taxonomy/domains.yaml.")
    src.add_argument("--hub", help="The taxonomy a hub collective published (needs Redis).")
    tx.add_argument("--match", nargs=2, metavar=("RECEPTOR", "TAG"),
                    help="Say whether a receptor hears a lesson tagged TAG, and why.")
    tx.add_argument("--format", choices=FORMATS, default="turtle")
    tx.set_defaults(func=_cmd_taxonomy)

    cv = sp.add_parser("convert", help="Map one file to RDF.")
    cv.add_argument("kind", choices=("lesson", "task", "bom", "okf"))
    cv.add_argument("file", type=Path)
    cv.add_argument("--collective", default="", help="task: the collective it ran in.")
    cv.add_argument("--bundle", default="", help="okf: the bundle name (default: the file's parent dir).")
    cv.add_argument("--format", choices=FORMATS, default="turtle")
    cv.set_defaults(func=_cmd_convert)


def persons_from_env():
    """A :class:`Persons` keyed by ``ACC_RDF_PERSON_KEY``, or unkeyed."""
    from acc.lib.rdf import Persons  # noqa: PLC0415

    key = os.environ.get(PERSON_KEY_ENV, "")
    return Persons(key.encode("utf-8") if key else None)


def render(graph, fmt: str) -> str:
    if fmt == "ntriples":
        return graph.to_ntriples()
    if fmt == "jsonld":
        return graph.to_jsonld_text()
    return graph.to_turtle()


def _cmd_vocab(args: argparse.Namespace) -> int:
    from acc.lib.rdf import vocabulary_graph  # noqa: PLC0415

    sys.stdout.write(render(vocabulary_graph(), args.format))
    return EXIT_OK


def _cmd_context(args: argparse.Namespace) -> int:
    from acc.lib.rdf import CONTEXT  # noqa: PLC0415

    print(json.dumps({"@context": CONTEXT}, indent=2))
    return EXIT_OK


def _cmd_taxonomy(args: argparse.Namespace) -> int:
    from acc.lib.rdf.taxonomy import Taxonomy, TaxonomyError  # noqa: PLC0415

    try:
        if args.file:
            import yaml  # noqa: PLC0415
            tax = Taxonomy.from_mapping(yaml.safe_load(args.file.read_text(encoding="utf-8")) or {})
            origin = str(args.file)
        elif args.pack:
            from acc.domain_taxonomy import load_pack  # noqa: PLC0415
            tax, source = load_pack(args.pack)
            origin = f"{source['pack']}@{source['version']}"
        else:
            from acc.domain_taxonomy import read_published  # noqa: PLC0415
            from acc.cli.lessons_cmd import _redis  # noqa: PLC0415
            got = read_published(_redis(), args.hub)
            if got is None:
                print(f"rdf: hub {args.hub} has published no taxonomy", file=sys.stderr)
                return EXIT_BAD_INPUT
            tax, source = got
            origin = f"hub {args.hub} ({source.get('pack', '?')}@{source.get('version', '?')})"
    except (OSError, TaxonomyError) as exc:
        print(f"rdf: not a usable taxonomy: {exc}", file=sys.stderr)
        return EXIT_BAD_INPUT

    if args.match:
        receptor, tag = args.match
        hears = tax.matches(receptor, tag)
        r, t = tax.concept_of(receptor), tax.concept_of(tag)
        if not r or not t:
            why = "a term the taxonomy does not know: exact match only"
        elif hears:
            why = f"{t!r} is {r!r} or narrower"
        else:
            above = [b for b in tax.broader_of(t) if b != t]
            why = f"{t!r} is not under {r!r}" + (
                f" (it is under: {', '.join(above)})" if above else " (it is a top concept)")
        print(f"{'HEARS' if hears else 'DOES NOT HEAR'}: receptor {receptor!r}, tag {tag!r} -- {why}")
        return EXIT_OK if hears else EXIT_BAD_INPUT
    print(f"# {origin}: scheme {tax.scheme}, {len(tax.concepts)} concept(s)", file=sys.stderr)
    sys.stdout.write(render(tax.to_graph(), args.format))
    return EXIT_OK


def _cmd_convert(args: argparse.Namespace) -> int:
    from acc.lib import rdf  # noqa: PLC0415

    try:
        text = args.file.read_text(encoding="utf-8")
        if args.kind == "lesson":
            graph = rdf.lesson_to_graph(json.loads(text), persons=persons_from_env())
        elif args.kind == "task":
            graph = rdf.task_to_graph(json.loads(text), collective_id=args.collective,
                                      persons=persons_from_env())
        elif args.kind == "bom":
            import yaml  # noqa: PLC0415

            graph = rdf.agent_bom_to_graph(yaml.safe_load(text) or {})
        else:
            from acc.lib.okf import load_concept  # noqa: PLC0415

            concept = load_concept(args.file, args.file.name)
            graph = rdf.okf_concept_to_graph(
                concept, bundle=args.bundle or args.file.resolve().parent.name)
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        print(f"rdf: cannot convert {args.file}: {exc}", file=sys.stderr)
        return EXIT_BAD_INPUT
    sys.stdout.write(render(graph, args.format))
    return EXIT_OK
