"""The question envelope (`20260911-question-envelope`, UX-02).

ACC had one decision envelope — the oversight row, answered APPROVE or REJECT —
so every question an agent asked was an approval wearing a label.  A
:class:`Question` is what the agent actually asks: the text, the options, and
for each option whether the gated action **proceeds** if the operator picks it.

It rides on the oversight row (``OversightItem.question``), so finality,
the two-approver rule, persistence and the way a decision reaches a worker are
the row's.  The operator's pick travels back as ``answer`` (the option key) on
the same OVERSIGHT_DECISION, and maps onto APPROVE / REJECT through
``proceeds``.

Pure: no I/O, no imports from the runtime.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class QuestionOption:
    """One answer the operator can give."""

    key: str
    label: str
    #: Does the gated action run when this option is chosen?
    proceeds: bool
    #: What choosing it means, one concrete line each (the panel's detail box).
    detail: tuple[str, ...] = ()


@dataclass(frozen=True)
class Question:
    """What the agent asks, and the answers it accepts."""

    text: str
    options: tuple[QuestionOption, ...]
    #: The call deletes or overwrites data.  A destructive question is answered
    #: on its own, never by a task grant, a bare "yes" or ``/allow``.
    destructive: bool = False
    #: What made the dispatcher ask (the matched command, the declared flag).
    evidence: str = ""

    def option(self, key: str) -> QuestionOption | None:
        return next((o for o in self.options if o.key == key), None)

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "options": [
                {"key": o.key, "label": o.label, "proceeds": o.proceeds,
                 "detail": list(o.detail)}
                for o in self.options
            ],
            "destructive": self.destructive,
            "evidence": self.evidence,
        }

    @classmethod
    def from_dict(cls, data: object) -> "Question | None":
        """The row's or the heartbeat's dict, or ``None`` when it is not a
        question (an empty dict is a row that asked nothing)."""
        if not isinstance(data, dict) or not data.get("text"):
            return None
        options = []
        for raw in data.get("options") or []:
            if not isinstance(raw, dict) or not raw.get("key"):
                continue
            options.append(QuestionOption(
                key=str(raw["key"]),
                label=str(raw.get("label") or raw["key"]),
                proceeds=raw.get("proceeds") is True,
                detail=tuple(str(d) for d in raw.get("detail") or ()),
            ))
        if not options:
            return None
        return cls(
            text=str(data["text"]),
            options=tuple(options),
            destructive=data.get("destructive") is True,
            evidence=str(data.get("evidence") or ""),
        )


def destructive_confirm(kind: str, target: str, evidence: str) -> Question:
    """The confirm question for a call that deletes or overwrites data.

    It names what will be destroyed — the matched command or the declared
    function — so the operator answers about *this* call, not about the skill
    in general."""
    what = f"{kind} {target}"
    return Question(
        text=f"{what} will delete or overwrite data: {evidence}. Run it?",
        options=(
            QuestionOption(
                key="1", label="run it", proceeds=True,
                detail=(
                    f"runs once, exactly as shown: {evidence}",
                    "-> not remembered; the next one is asked again",
                    "recorded as a critical function",
                ),
            ),
            QuestionOption(
                key="2", label="don't run it", proceeds=False,
                detail=(
                    "nothing runs",
                    "-> the agent is told you refused it",
                ),
            ),
        ),
        destructive=True,
        evidence=evidence,
    )
