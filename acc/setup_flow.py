"""A guided first run that validates as it goes.

The written setup procedure is long, and following it correctly still produces a
deployment that only *looks* configured — the five silent configuration faults
all pass a careful read of the instructions.

So this flow validates **at the point of entry**. A model id is checked against
the registry when it is entered, not when an agent first tries to use it. A
posture is chosen explicitly, with the consequence stated in the question,
because a governance floor that got its value by default is a floor nobody
decided on.

The questions are **data**, not a script of prompts. That is what lets the same
definitions drive an interactive terminal walk and a non-interactive answer
file, without one of them drifting into asking something the other cannot.

Sections are independently runnable so an operator can redo the model binding
without walking the whole flow again — the thing that makes a wizard something
people avoid.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

logger = logging.getLogger("acc.setup")


class SetupError(Exception):
    """Setup was refused. The message is operator-facing."""


@dataclass
class Question:
    """One thing to ask, bound to a schema key and a check.

    Attributes:
        key: the dotted configuration key the answer is written to.
        prompt: what the operator is asked.
        consequence: what choosing this actually does. Stated for posture
            questions, where the cost of a silent default is highest.
        choices: fixed options, when the schema does not supply them.
        default: offered value.
        secret: never write this through configuration; tell the operator to
            set it in the environment instead.
        validate: extra check returning an error string, or "" when fine.
    """

    key: str
    prompt: str
    consequence: str = ""
    choices: tuple[str, ...] = ()
    default: Any = None
    secret: bool = False
    validate: Callable[[str], str] | None = None

    def options(self) -> tuple[str, ...]:
        if self.choices:
            return self.choices
        from acc import configschema as schema  # noqa: PLC0415

        entry = schema.by_path().get(self.key)
        return tuple(entry.choices) if entry and entry.choices else ()


@dataclass
class Section:
    """A group of questions that can be run on its own."""

    name: str
    title: str
    questions: list[Question] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def _model_exists(value: str) -> str:
    from acc.models import load_models  # noqa: PLC0415

    known = {m.model_id for m in load_models()}
    if not known:
        return ""  # nothing to check against; models.yaml is the operator's next step
    if value not in known:
        return (
            f"{value!r} is not in models.yaml. Known: {', '.join(sorted(known))}"
        )
    return ""


def _nonempty(value: str) -> str:
    return "" if str(value).strip() else "a value is required"


def _url_shape(value: str) -> str:
    if not value.strip():
        return ""
    if not value.startswith(("http://", "https://")):
        return "must start with http:// or https://"
    return ""


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------


def sections() -> list[Section]:
    """Every question, as data.

    Deliberately not a script: the same definitions drive the interactive walk
    and the non-interactive answer file, so neither can ask something the other
    cannot supply.
    """
    return [
        Section(
            name="posture",
            title="Security and governance posture",
            questions=[
                Question(
                    key="deploy_mode",
                    prompt="Where does this deployment run?",
                    consequence=(
                        "rhoai and edge enforce the production security floor and "
                        "refuse the relaxed operator mode; standalone does not."
                    ),
                    default="standalone",
                ),
                Question(
                    key="operator_mode",
                    prompt="Security floor",
                    consequence=(
                        "prod REQUIRES signing, auth and present secrets. dev "
                        "tolerates absent secrets and lowers those floors — for "
                        "local use only, and refused outright on rhoai/edge."
                    ),
                    default="prod",
                ),
                Question(
                    key="compliance.enabled",
                    prompt="Enable the compliance layer?",
                    consequence=(
                        "when off, Category A/B/C evaluation does not run and no "
                        "governance verdicts are recorded."
                    ),
                    choices=("true", "false"),
                    default="true",
                ),
            ],
        ),
        Section(
            name="model",
            title="Language model",
            questions=[
                Question(
                    key="llm.backend",
                    prompt="Which backend?",
                    default="ollama",
                ),
                Question(
                    key="llm.base_url",
                    prompt="Endpoint base URL (blank for provider default)",
                    default="",
                    validate=_url_shape,
                ),
                Question(
                    key="llm.api_key_env",
                    prompt="Name of the environment variable holding the API key",
                    consequence=(
                        "the NAME only — ACC never stores the key itself in "
                        "configuration."
                    ),
                    default="",
                ),
            ],
        ),
        Section(
            name="storage",
            title="Vector store and working memory",
            questions=[
                Question(
                    key="vector_db.backend",
                    prompt="Vector backend",
                    default="lancedb",
                ),
                Question(
                    key="observability.backend",
                    prompt="Metrics backend",
                    default="log",
                ),
            ],
        ),
    ]


def section(name: str) -> Section:
    for candidate in sections():
        if candidate.name == name:
            return candidate
    known = ", ".join(s.name for s in sections())
    raise SetupError(f"no setup section {name!r}. Known: {known}")


# ---------------------------------------------------------------------------
# Answers
# ---------------------------------------------------------------------------


def load_answers(path: str | Path) -> dict[str, str]:
    """Read an answer set for non-interactive provisioning."""
    p = Path(path)
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SetupError(f"cannot read answers from {p}: {exc}") from exc
    if not isinstance(raw, dict):
        raise SetupError("an answer set must be a JSON object of key -> value")
    return {str(k): raw[k] for k in raw}


def answers_from_env(environ: dict[str, str] | None = None) -> dict[str, str]:
    """Answers supplied as ``ACC_SETUP_<KEY>`` variables.

    ``llm.backend`` becomes ``ACC_SETUP_LLM_BACKEND``. Provided so a
    provisioning system can drive setup without writing a file first.
    """
    env = environ if environ is not None else os.environ
    out: dict[str, str] = {}
    for sec in sections():
        for question in sec.questions:
            var = "ACC_SETUP_" + question.key.upper().replace(".", "_")
            if var in env and str(env[var]).strip():
                out[question.key] = env[var]
    return out


# ---------------------------------------------------------------------------
# Validation and application
# ---------------------------------------------------------------------------


@dataclass
class Outcome:
    """What one answer did."""

    key: str
    value: Any
    written: bool
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "value": self.value,
            "written": self.written,
            "error": self.error,
        }


def check_answer(question: Question, value: str) -> str:
    """Validate one answer at the point of entry. Returns "" when acceptable."""
    text = str(value)
    options = question.options()
    if options and text not in options:
        return f"must be one of: {', '.join(options)}"
    if question.validate is not None:
        problem = question.validate(text)
        if problem:
            return problem

    # The schema is the final authority — it is what the runtime validates
    # against, so an answer it would reject must not reach a file.
    from acc import configschema as schema  # noqa: PLC0415
    from acc import configstore as store  # noqa: PLC0415

    entry = schema.find(question.key)
    if entry is None:
        return f"{question.key} is not a known configuration key"
    try:
        store.coerce(text, entry)
    except store.ConfigError as exc:
        return str(exc)
    return ""


def apply_answers(
    answers: dict[str, str],
    *,
    only: Iterable[str] | None = None,
    quick: bool = False,
    repo_root: Path | None = None,
    dry_run: bool = False,
) -> list[Outcome]:
    """Validate every answer, then write. Refuses to write a failing value.

    Validation happens for the whole set BEFORE anything is written: a setup
    that half-applies leaves a deployment in a shape no answer set describes.

    Raises:
        SetupError: any answer failed its check. Nothing is written.
    """
    from acc import configstore as store  # noqa: PLC0415

    wanted = {s.name for s in sections()} if only is None else set(only)
    questions = {
        q.key: q
        for sec in sections()
        if sec.name in wanted
        for q in sec.questions
    }

    problems: list[str] = []
    planned: list[tuple[Question, str]] = []
    for key, value in answers.items():
        question = questions.get(key)
        if question is None:
            continue  # belongs to a section this run is not touching
        if question.secret:
            problems.append(
                f"{key}: refuses secret values — set it in the environment instead"
            )
            continue
        problem = check_answer(question, str(value))
        if problem:
            problems.append(f"{key}: {problem}")
            continue
        planned.append((question, str(value)))

    if problems:
        raise SetupError(
            "setup refused; nothing was written:\n  " + "\n  ".join(problems)
        )

    outcomes: list[Outcome] = []
    for question, value in planned:
        current = store.get(question.key, repo_root=repo_root)
        if quick and current.present:
            outcomes.append(Outcome(question.key, current.value, False, "already set"))
            continue
        if dry_run:
            outcomes.append(Outcome(question.key, value, False, "dry run"))
            continue
        try:
            store.set_key(question.key, value, repo_root=repo_root)
            outcomes.append(Outcome(question.key, value, True))
        except store.ConfigError as exc:
            outcomes.append(Outcome(question.key, value, False, str(exc)))
    return outcomes


def verify(repo_root: Path | None = None) -> list[str]:
    """Run the deployment health checks and return the broken ones.

    A completed setup should be a working deployment, not a plausible one — so
    the flow finishes by asking the same question ``acc-cli doctor`` asks
    rather than declaring success on its own authority.
    """
    from acc import preflight  # noqa: PLC0415

    results = preflight.run(preflight.Context(repo_root=repo_root))
    return [
        f"{r.name}: {r.summary}"
        for r in results
        if r.severity is preflight.Severity.BROKEN
    ]
