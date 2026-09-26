"""The guard against invented entities.  (Owner: Sumudu)

Two ways a model can invent something, and one check for each:

* **In a claim.** ``ontology.validate`` rejects a claim whose subject or object is not a real entity of
  the right kind, whose predicate is not in the vocabulary, or whose value has the wrong unit or range.
* **In prose.** A summary or an alert can name a shelter that does not exist without making any claim
  at all. ``find_mentions`` finds the references in the text; the guard reports the ones that resolve
  to nothing (or to several things) so the sentence is not shown as fact.

``EntityGuard`` puts both behind one object. It only reads: it never edits, repairs or drops anything.
What to do with a rejected sentence is the caller's decision (the intelligence layer strikes it out and
keeps it as proof the guard works).

The prose check is a heuristic and says so: it recognises ids, names it knows, and phrases that look
like a reference to a place or thing ("Shelter 7", "the bridge at Kuruwita"). It cannot tell that an
arbitrary capitalised word is a made-up village. It reports what it cannot place instead of guessing,
and it never reports "safe" about something it did not recognise.
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

from pydantic import BaseModel, Field

from atlas.knowledge.linking import Mentions, find_mentions
from atlas.knowledge.ontology import Claim, Violation, YamlOntology


class TextCheck(BaseModel):
    text: str
    ok: bool
    mentions: Mentions
    problems: list[str] = Field(default_factory=list)  # one plain sentence per issue


class Issue(BaseModel):
    where: str  # "summary", "concerns[1]", "claims[0].text", ...
    kind: str  # "unresolved" | "ambiguous" | "claim"
    text: str
    detail: str


class AdviceCheck(BaseModel):
    ok: bool
    issues: list[Issue] = Field(default_factory=list)


class EntityGuard:
    def __init__(
        self,
        ontology: YamlOntology,
        graph: Any,
        *,
        context: Sequence[str] = (),
        strict_ambiguity: bool = True,
    ) -> None:
        self.ontology, self.graph = ontology, graph
        self.context = tuple(context)
        self.strict_ambiguity = strict_ambiguity

    # ---------------------------------------------------------------------------- claims

    def check_claims(self, claims: Iterable[Claim]) -> list[Violation]:
        return [v for claim in claims for v in self.ontology.validate(claim)]

    # ----------------------------------------------------------------------------- prose

    def check_text(self, text: str) -> TextCheck:
        mentions = find_mentions(
            text, self.graph, context=self.context, definition=self.ontology.definition
        )
        problems = [f"{phrase!r} does not match anything known" for phrase in mentions.unresolved]
        if self.strict_ambiguity:
            problems += [
                f"{m.text!r} could be any of {', '.join(m.alternatives)}"
                for m in mentions.ambiguous
            ]
        return TextCheck(text=text, ok=not problems, mentions=mentions, problems=problems)

    def check_advice(self, advice: Any) -> AdviceCheck:
        """Every sentence in a piece of advice, and every claim in it. Read-only.

        ``advice`` is anything shaped like ``atlas.intelligence.contracts.Advice`` (this module does
        not import it, to avoid a circular dependency): ``summary``, ``concerns``, ``questions`` and
        ``claims`` (each with ``text`` and ``claim``).
        """
        issues: list[Issue] = []

        def scan(where: str, text: str) -> None:
            check = self.check_text(text)
            issues.extend(
                Issue(where=where, kind="unresolved", text=p, detail="names something unknown")
                for p in check.mentions.unresolved
            )
            if self.strict_ambiguity:
                issues.extend(
                    Issue(where=where, kind="ambiguous", text=m.text, detail=m.reason)
                    for m in check.mentions.ambiguous
                )

        scan("summary", getattr(advice, "summary", "") or "")
        for name in ("concerns", "questions"):
            for i, text in enumerate(getattr(advice, name, []) or []):
                scan(f"{name}[{i}]", text)
        for i, item in enumerate(getattr(advice, "claims", []) or []):
            scan(f"claims[{i}].text", item.text)
            issues.extend(
                Issue(where=f"claims[{i}]", kind="claim", text=v.code, detail=v.message)
                for v in self.ontology.validate(item.claim)
            )
        return AdviceCheck(ok=not issues, issues=issues)
