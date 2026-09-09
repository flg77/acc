"""The external risk vocabulary ACC speaks -- IBM AI Atlas Nexus ids.

`20260908-asago-alignment` AS-01.  ACC's frameworks name their controls in
their own terms (``GOVERN-1.1``, ``ART-14``, the threat model's ids); nothing outside
ACC could read them.  asago keys everything it produces -- risks extracted
from a policy, scenarios, recommended controls -- on the ids of the Risk
Atlas Nexus (``atlas-prompt-injection``, ``llm01-prompt-injection``,
``asi07-insecure-inter-agent-communication``, ``nist-ms-2.5``), cross-walked
by SSSOM.  This module is the join:

* :func:`vocabulary` -- the pinned snapshot of Nexus ids ACC references
  (``regulatory_layer/nexus/vocabulary.yaml``), so a framework that names an
  id nobody has is a test failure, offline;
* :func:`nexus_index` -- ``nexus id -> [(framework, control)]`` over the
  loaded frameworks, the reverse lookup a policy risk needs;
* :func:`sssom_rows` / :func:`render_sssom` -- the mappings as an SSSOM
  TSV, the interchange format the Nexus and asago use.

Which ids a control carries is curated by hand in the framework YAML
(``nexus_ids:``) and verified against the vocabulary; nothing here guesses.
The threat model's ids live in its withheld YAML twin and are exported only
where that file exists (the spearhead tree), never on the public mirror.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable

import yaml

if TYPE_CHECKING:  # pragma: no cover
    from acc.frameworks import Framework

#: Where the pinned vocabulary lives (repo root / regulatory_layer / nexus).
VOCABULARY_PATH = Path(__file__).resolve().parent.parent / "regulatory_layer" / "nexus" / "vocabulary.yaml"

#: SSSOM predicates ACC emits.  A control that *is* the Nexus entry
#: (NIST GOVERN-1.1 == nist-gv-1.1) is an exact match; a threat that
#: *instantiates* a Nexus risk is a related match -- the threat is ACC's
#: own, the risk is the family it belongs to.
PREDICATE_EXACT = "skos:exactMatch"
PREDICATE_RELATED = "skos:relatedMatch"
#: The one framework whose controls are Nexus entries themselves.
_EXACT_FRAMEWORKS = {"nist_ai_rmf"}
#: SSSOM `mapping_justification` for hand-curated rows.
JUSTIFICATION = "semapv:ManualMappingCuration"


@dataclass(frozen=True)
class Vocabulary:
    """The pinned Nexus ids, by family, plus the derived-id patterns."""

    source: str
    fetched: str
    families: dict[str, dict[str, Any]]

    def known(self, nexus_id: str) -> bool:
        """Whether *nexus_id* is a pinned id or matches a family pattern."""
        text = str(nexus_id or "").strip()
        if not text:
            return False
        for fam in self.families.values():
            if text in (fam.get("ids") or []):
                return True
            pattern = fam.get("pattern")
            if pattern and re.match(pattern, text):
                return True
        return False

    def family_of(self, nexus_id: str) -> str:
        """The family name an id belongs to ("" when unknown)."""
        text = str(nexus_id or "").strip()
        for name, fam in self.families.items():
            if text in (fam.get("ids") or []):
                return name
            pattern = fam.get("pattern")
            if pattern and re.match(pattern, text):
                return name
        return ""

    def taxonomy_of(self, nexus_id: str) -> str:
        fam = self.family_of(nexus_id)
        return str(self.families.get(fam, {}).get("taxonomy", "")) if fam else ""

    @property
    def size(self) -> int:
        return sum(len(f.get("ids") or []) for f in self.families.values())


_VOCAB: Vocabulary | None = None


def vocabulary(path: Path | None = None) -> Vocabulary:
    """Load (and cache) the pinned vocabulary."""
    global _VOCAB
    if path is None and _VOCAB is not None:
        return _VOCAB
    raw = yaml.safe_load((path or VOCABULARY_PATH).read_text(encoding="utf-8")) or {}
    vocab = Vocabulary(
        source=str(raw.get("source", "")),
        fetched=str(raw.get("fetched", "")),
        families={str(k): dict(v or {}) for k, v in (raw.get("families") or {}).items()},
    )
    if path is None:
        _VOCAB = vocab
    return vocab


# ---------------------------------------------------------------------------
# validation + lookup over loaded frameworks
# ---------------------------------------------------------------------------


def nist_subcategory_id(control_id: str) -> str:
    """``GOVERN-1.1`` -> ``nist-gv-1.1`` (asago's policy-mapper form); ""
    for anything that is not an AI RMF subcategory id."""
    m = re.match(r"^(GOVERN|MAP|MEASURE|MANAGE)-(\d+\.\d+)$", str(control_id or "").strip().upper())
    if not m:
        return ""
    fn = {"GOVERN": "gv", "MAP": "mp", "MEASURE": "ms", "MANAGE": "mg"}[m.group(1)]
    return f"nist-{fn}-{m.group(2)}"


def unknown_ids(frameworks: Iterable["Framework"], vocab: Vocabulary | None = None) -> list[tuple[str, str, str]]:
    """``(framework_id, control_id, nexus_id)`` for every id no vocabulary knows."""
    vocab = vocab or vocabulary()
    out: list[tuple[str, str, str]] = []
    for fw in frameworks:
        for c in fw.controls:
            for nid in c.nexus_ids:
                if not vocab.known(nid):
                    out.append((fw.framework_id, c.control_id, nid))
    return out


def nexus_index(frameworks: Iterable["Framework"]) -> dict[str, list[tuple[str, str]]]:
    """``nexus id -> [(framework_id, control_id), ...]`` -- what in ACC
    answers to a given external risk or control."""
    index: dict[str, list[tuple[str, str]]] = {}
    for fw in frameworks:
        for c in fw.controls:
            for nid in c.nexus_ids:
                index.setdefault(nid, []).append((fw.framework_id, c.control_id))
    return index


def coverage(frameworks: Iterable["Framework"]) -> dict[str, dict[str, int]]:
    """Per framework: how many controls carry at least one Nexus id."""
    out: dict[str, dict[str, int]] = {}
    for fw in frameworks:
        total = len(fw.controls)
        mapped = sum(1 for c in fw.controls if c.nexus_ids)
        out[fw.framework_id] = {"controls": total, "mapped": mapped}
    return out


# ---------------------------------------------------------------------------
# SSSOM
# ---------------------------------------------------------------------------

SSSOM_COLUMNS = (
    "subject_id", "subject_label", "predicate_id", "object_id",
    "mapping_justification", "subject_source", "object_source", "comment",
)


def subject_curie(framework_id: str, control_id: str) -> str:
    return f"acc:{framework_id}/{control_id}"


def sssom_rows(frameworks: Iterable["Framework"], vocab: Vocabulary | None = None) -> list[dict[str, str]]:
    """One SSSOM row per (control, nexus id), in framework then control order."""
    vocab = vocab or vocabulary()
    rows: list[dict[str, str]] = []
    for fw in frameworks:
        predicate = PREDICATE_EXACT if fw.framework_id in _EXACT_FRAMEWORKS else PREDICATE_RELATED
        for c in fw.controls:
            for nid in c.nexus_ids:
                rows.append({
                    "subject_id": subject_curie(fw.framework_id, c.control_id),
                    "subject_label": c.title,
                    "predicate_id": predicate,
                    "object_id": nid,
                    "mapping_justification": JUSTIFICATION,
                    "subject_source": f"acc:{fw.framework_id}",
                    "object_source": vocab.taxonomy_of(nid) or "nexus",
                    "comment": "" if vocab.known(nid) else "UNKNOWN in the pinned vocabulary",
                })
    return rows


def render_sssom(rows: Iterable[dict[str, str]], *, mapping_set_id: str = "acc:nexus-mappings",
                 vocab: Vocabulary | None = None, now: float | None = None) -> str:
    """An SSSOM TSV: the YAML metadata block as ``#`` lines, then the table."""
    vocab = vocab or vocabulary()
    stamp = time.strftime("%Y-%m-%d", time.gmtime(now if now is not None else time.time()))
    header = [
        "# curie_map:",
        "#   acc: https://github.com/flg77/acc/regulatory_layer/frameworks/",
        "#   skos: http://www.w3.org/2004/02/skos/core#",
        "#   semapv: https://w3id.org/semapv/vocab/",
        f"# mapping_set_id: {mapping_set_id}",
        f"# mapping_set_description: ACC framework controls and threats mapped to IBM AI Atlas Nexus ids (vocabulary fetched {vocab.fetched})",
        "# license: https://www.apache.org/licenses/LICENSE-2.0",
        f"# mapping_date: {stamp}",
        f"# object_source: {vocab.source}",
    ]
    lines = header + ["\t".join(SSSOM_COLUMNS)]
    for r in rows:
        lines.append("\t".join(str(r.get(col, "")).replace("\t", " ").replace("\n", " ") for col in SSSOM_COLUMNS))
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# an asago risk extraction, joined to ACC
# ---------------------------------------------------------------------------
#
# `asago-policy-mapper extract policy.pdf` writes ``risk-extraction.json``
# (or YAML): ``{"risk_extraction": {"risks": [{"nexus_id", "confidence",
# "evidence": [{"exact", "document", "page"}], "cross_mappings": ["nist-ms-2.5
# (Confabulation)", ...]}]}}``.  Dropping it into ACC means answering, per
# risk the policy names: which ACC controls and threats address it, and does
# a loaded governance rule cover them.  ``nexus_id`` is the join key;
# ``cross_mappings`` (ids with a label in parentheses) are the fallback when
# the primary id has no ACC counterpart.


def load_risk_extraction(path: Path | str) -> list[dict[str, Any]]:
    """The ``risks`` list of an asago extraction file (JSON or YAML; the
    top-level ``risk_extraction`` wrapper is optional)."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if isinstance(raw, dict) and isinstance(raw.get("risk_extraction"), dict):
        raw = raw["risk_extraction"]
    risks = raw.get("risks") if isinstance(raw, dict) else raw
    if not isinstance(risks, list):
        raise ValueError(f"{path}: no 'risks' list (an asago risk-extraction file has one)")
    return [r for r in risks if isinstance(r, dict) and r.get("nexus_id")]


def cross_mapping_ids(values: Iterable[Any]) -> list[str]:
    """``"nist-ms-2.5 (Confabulation)"`` -> ``"nist-ms-2.5"`` for each entry."""
    out: list[str] = []
    for v in values or ():
        text = str(v or "").strip()
        if not text:
            continue
        out.append(re.split(r"\s*\(", text, 1)[0].strip())
    return out


def join_risks(risks: Iterable[dict[str, Any]], frameworks: Iterable["Framework"],
               gap_covered: dict[tuple[str, str], bool] | None = None,
               vocab: Vocabulary | None = None) -> list[dict[str, Any]]:
    """Per extracted risk: the ACC controls and threats that answer to it.

    *gap_covered* (``(framework_id, control_id) -> covered``) comes from the
    gap analysis when the caller has run one; without it ``covered`` is
    ``None`` (unknown), never a guess.
    """
    vocab = vocab or vocabulary()
    fws = list(frameworks)
    index = nexus_index(fws)
    titles = {(f.framework_id, c.control_id): c.title for f in fws for c in f.controls}
    out: list[dict[str, Any]] = []
    for r in risks:
        nid = str(r.get("nexus_id") or "").strip()
        hits = [(nid, fid, cid) for fid, cid in index.get(nid, [])]
        via = ""
        if not hits:
            for alt in cross_mapping_ids(r.get("cross_mappings") or []):
                if index.get(alt):
                    hits = [(alt, fid, cid) for fid, cid in index[alt]]
                    via = alt
                    break
        controls = []
        for key, fid, cid in hits:
            controls.append({
                "framework_id": fid, "control_id": cid, "title": titles.get((fid, cid), ""),
                "matched_on": key,
                "covered": (gap_covered or {}).get((fid, cid)) if gap_covered is not None else None,
            })
        out.append({
            "nexus_id": nid,
            "known": vocab.known(nid),
            "taxonomy": vocab.taxonomy_of(nid),
            "confidence": r.get("confidence"),
            "evidence": [str(e.get("exact") or "")[:160] for e in (r.get("evidence") or []) if isinstance(e, dict)][:3],
            "via_cross_mapping": via,
            "controls": controls,
        })
    return out


def gap_coverage_map(frameworks: Iterable["Framework"]) -> dict[tuple[str, str], bool]:
    """``(framework_id, control_id) -> covered`` from the deterministic gap
    analysis against the loaded governance layers."""
    from acc.gap_analysis import analyze_gaps  # noqa: PLC0415
    from acc.governance_inventory import load_all_layers  # noqa: PLC0415
    layers = load_all_layers()
    out: dict[tuple[str, str], bool] = {}
    for fw in frameworks:
        for c in analyze_gaps(layers, fw).controls:
            out[(fw.framework_id, c.control_id)] = bool(c.covered)
    return out
