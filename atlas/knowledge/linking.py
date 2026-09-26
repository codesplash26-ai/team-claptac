"""Entity linking.  (Owner: Sumudu)

Resolve what a person or a model wrote ("the Kalu Ganga", "Ratnapura shelter 1", an OSM road id) to a
known ontology entity id, or say it cannot. Used by the intelligence layer to ground free text and by
field-report handling. A link with low confidence is returned as ambiguous, never guessed.

    link("Ratnapura shelter 1", graph)        -> shelter:ratnapura-shelter-01     (0.9)
    link("shelter 1", graph)                  -> ambiguous: one per site          (no entity)
    link("shelter 1", graph, context=["ratnapura"])  -> shelter:ratnapura-shelter-01  (0.9)
    link("shelter:ratnapura-shelter-99", graph) -> no such entity                 (no entity)

Rules that keep it from inventing things
    * An explicit id (``class:key``, or ``edge:`` for a road) matches exactly or not at all. A
      well-formed id that does not exist is NOT fuzzy-matched to a real one.
    * A name matches exactly (after case, punctuation and leading zeros are ignored), or fuzzily only
      if the numbers in it are identical, the match is strong, and it is clearly better than the runner
      up. "Shelter 7" never becomes "shelter 1".
    * Two candidates the text cannot tell apart are returned as alternatives with no entity, unless the
      ``context`` (an area, a site name) singles one out.

``find_mentions`` scans a passage of prose and returns what it could resolve, what was ambiguous, and
what looked like a reference to a place or thing but matched nothing: those are what a model may have
made up, and what extraction should ask about.
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Sequence

from pydantic import BaseModel, Field

from atlas.knowledge.ontology import OntologyDefinition, YamlOntology

FUZZY_MIN = 0.86  # how alike two names must be to link without an exact match
FUZZY_MARGIN = 0.08  # and how much better than the runner-up
FUZZY_CANDIDATE_MIN = 0.6  # below this a name is not even offered as an alternative
MAX_ALTERNATIVES = 4
MAX_PHRASE_TOKENS = 8

CONFIDENCE = {"id": 1.0, "name": 0.95, "generated": 0.9, "configured": 0.9, "context": 0.9}

# Words that, followed by a number or a capitalised name, look like a reference to a specific place or
# thing. If the reference does not resolve it is reported as unresolved. A heuristic, stated as one.
PLACE_WORDS = (
    "shelter road junction gauge mission resource group incident area bridge school hospital temple "
    "church mosque village town camp station depot clinic river"
).split()
_PLACE = "|".join(PLACE_WORDS)
_PREPOSITION = r"(?:(?:at|in|near|of|on|by)\s+)?"
_NUMBER_OR_NAME = r"(?:no\.?\s*)?(?:\d+[A-Za-z]?|[A-Z][\w'-]+(?:\s+[A-Z][\w'-]+)*)"
# Words that can start a sentence or a phrase but are never the name of a place.
NOT_NAMES = {
    "the", "a", "an", "this", "that", "these", "those", "our", "your", "their", "his", "her", "its",
    "my", "one", "every", "each", "all", "no", "any", "some", "both", "another", "such", "same",
}  # fmt: skip
PLACE_THEN_NAME = re.compile(rf"\b(?i:{_PLACE})\s+{_PREPOSITION}{_NUMBER_OR_NAME}")
NAME_THEN_PLACE = re.compile(rf"\b(?:[A-Z][\w'-]+\s+)+(?i:{_PLACE})\b")


class Link(BaseModel):
    text: str
    entity_id: str | None  # None: no confident match
    entity_class: str | None = None
    confidence: float = 0.0
    alternatives: list[str] = Field(default_factory=list)
    reason: str = ""  # why: "exact id", "exact name", "ambiguous", "no such entity", ...


class Mentions(BaseModel):
    resolved: list[Link] = Field(default_factory=list)
    ambiguous: list[Link] = Field(default_factory=list)
    unresolved: list[str] = Field(
        default_factory=list
    )  # phrases that named something we cannot find

    @property
    def clean(self) -> bool:
        """True if nothing in the text names something that does not exist or cannot be told apart."""
        return not self.ambiguous and not self.unresolved


# --------------------------------------------------------------------------------- text forms


def normalise(text: str) -> str:
    """Lower-case, accent-free, punctuation-free, single-spaced, with leading zeros dropped."""
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    tokens = re.findall(r"[a-z0-9]+", folded.lower())
    return " ".join(str(int(t)) if t.isdigit() else t for t in tokens)


def _numbers(phrase: str) -> tuple[str, ...]:
    return tuple(t for t in phrase.split() if t.isdigit())


@dataclass
class Lexicon:
    """Every phrase that can name an entity, and what each phrase can mean."""

    phrases: dict[str, dict[str, str]] = field(default_factory=dict)  # phrase -> {entity: kind}
    classes: dict[str, str] = field(default_factory=dict)  # entity -> class
    tokens: dict[str, set[str]] = field(default_factory=dict)  # entity -> tokens of its id and name

    def add(self, phrase: str, entity: str, kind: str) -> None:
        phrase = normalise(phrase)
        if len(phrase) >= 3:
            self.phrases.setdefault(phrase, {}).setdefault(entity, kind)


def _entities(graph) -> list[tuple[str, str, str, dict[str, Any]]]:
    """(id, class, label, meta) for every entity, from either kind of graph."""
    found = []
    for node, data in graph.graph.nodes(data=True):
        if "entity_class" in data:
            meta = {k: v for k, v in data.items() if k not in ("entity_class", "attributes")}
            found.append((node, data["entity_class"], str(meta.get("label", "")), meta))
    return found


def build_lexicon(graph, definition: OntologyDefinition) -> Lexicon:
    lex = Lexicon()
    for entity, klass, label, _ in _entities(graph):
        key = entity.split(":", 1)[1]
        cls_word = klass.lower()
        lex.classes[entity] = klass
        lex.tokens[entity] = set(normalise(key).split()) | set(normalise(label).split())
        lex.add(entity, entity, "id")
        lex.add(key, entity, "name")
        if label and normalise(label) != normalise(key):
            lex.add(label, entity, "name")
        key_tokens = normalise(key).split()
        if cls_word in key_tokens and key_tokens[-1].isdigit():
            # "ratnapura-shelter-01": also "shelter 1" and "shelter 1 ratnapura"
            site = [t for t in key_tokens if t != cls_word and not t.isdigit()]
            lex.add(f"{cls_word} {key_tokens[-1]}", entity, "generated")
            if site:
                lex.add(f"{' '.join(site)} {cls_word} {key_tokens[-1]}", entity, "generated")
                lex.add(f"{cls_word} {key_tokens[-1]} {' '.join(site)}", entity, "generated")
        if klass in ("Road", "Junction", "Mission", "Group", "Incident", "Resource"):
            lex.add(f"{cls_word} {key}", entity, "generated")
        if klass == "Gauge" and label:
            for word in ("gauge", "station"):
                lex.add(f"{label} {word}", entity, "generated")
                lex.add(f"{word} {label}", entity, "generated")
        if klass == "Hazard":
            lex.add(f"{key}s", entity, "generated")
    for entity, names in definition.aliases.items():
        if entity in lex.classes:
            for name in names:
                lex.add(name, entity, "configured")
    return lex


def lexicon_for(graph, definition: OntologyDefinition) -> Lexicon:
    cache = getattr(graph, "cache", None)
    if cache is not None and "lexicon" in cache:
        return cache["lexicon"]
    lex = build_lexicon(graph, definition)
    if cache is not None:
        cache["lexicon"] = lex
    return lex


# ------------------------------------------------------------------------------------ linking


def _definition(definition: OntologyDefinition | None, graph) -> OntologyDefinition:
    return definition or getattr(graph, "definition", None) or YamlOntology.read()


def link(
    text: str,
    graph,
    *,
    context: Sequence[str] = (),
    definition: OntologyDefinition | None = None,
) -> Link:
    """Resolve one reference to an entity id. See the module docstring for the rules."""
    definition = _definition(definition, graph)
    raw = text.strip()

    explicit = _link_explicit(raw, graph, definition)
    if explicit is not None:
        return explicit

    lex = lexicon_for(graph, definition)
    phrase = normalise(raw)
    if len(phrase) < 3:
        return Link(text=text, entity_id=None, reason="no match")
    if phrase in lex.phrases:
        return _from_candidates(text, lex, lex.phrases[phrase], context, exact=True)
    return _fuzzy(text, phrase, lex, context)


def _link_explicit(raw: str, graph, definition: OntologyDefinition) -> Link | None:
    """An id, or a bare OSM road pair. Exact or nothing: never fuzzy."""
    if re.fullmatch(r"\d+-\d+", raw):
        raw = f"road:{raw}"
    entity = definition.normalise_id(raw)
    prefix, sep, key = entity.partition(":")
    if not sep or not re.fullmatch(r"[a-z][a-z_]*", prefix):
        return None
    klass = definition.class_of_prefix(prefix)
    if klass is None:
        return None  # "bridge:kuruwita": not one of ours; let find_mentions flag it if it is prose
    if graph.has_entity(entity):
        return Link(
            text=raw,
            entity_id=entity,
            entity_class=graph.entity_class(entity),
            confidence=CONFIDENCE["id"],
            reason="exact id",
        )
    if klass == "Hazard":
        return Link(text=raw, entity_id=None, reason=f"{key!r} is not a known hazard")
    return Link(
        text=raw, entity_id=None, entity_class=klass, reason="well-formed id but no such entity"
    )


def _context_score(entity: str, lex: Lexicon, context_tokens: set[str]) -> int:
    return len(lex.tokens.get(entity, set()) & context_tokens)


def _from_candidates(text, lex, candidates: dict[str, str], context, *, exact: bool) -> Link:
    ordered = sorted(candidates)
    if len(ordered) == 1:
        entity = ordered[0]
        kind = candidates[entity]
        return Link(
            text=text,
            entity_id=entity,
            entity_class=lex.classes[entity],
            confidence=CONFIDENCE[kind],
            reason="exact id" if kind == "id" else "exact name",
        )
    context_tokens = {t for c in context for t in normalise(c).split()}
    if context_tokens:
        scores = {e: _context_score(e, lex, context_tokens) for e in ordered}
        best = max(scores.values())
        winners = [e for e in ordered if scores[e] == best]
        if best > 0 and len(winners) == 1:
            entity = winners[0]
            return Link(
                text=text,
                entity_id=entity,
                entity_class=lex.classes[entity],
                confidence=CONFIDENCE["context"],
                reason="resolved by context",
            )
    return Link(
        text=text,
        entity_id=None,
        alternatives=ordered[:MAX_ALTERNATIVES] if len(ordered) > MAX_ALTERNATIVES else ordered,
        reason=f"ambiguous: {len(ordered)} things are called that",
    )


def _fuzzy(text: str, phrase: str, lex: Lexicon, context) -> Link:
    scored: dict[str, float] = {}
    for candidate, entities in lex.phrases.items():
        if _numbers(candidate) != _numbers(phrase):
            continue  # "shelter 7" is never "shelter 1"
        ratio = difflib.SequenceMatcher(None, phrase, candidate).ratio()
        if ratio >= FUZZY_CANDIDATE_MIN:
            for entity in entities:
                scored[entity] = max(scored.get(entity, 0.0), ratio)
    if not scored:
        return Link(text=text, entity_id=None, reason="no match")
    ranked = sorted(scored.items(), key=lambda item: (-item[1], item[0]))
    best_entity, best = ranked[0]
    runner_up = ranked[1][1] if len(ranked) > 1 else 0.0
    if best >= FUZZY_MIN and best - runner_up >= FUZZY_MARGIN:
        return Link(
            text=text,
            entity_id=best_entity,
            entity_class=lex.classes[best_entity],
            confidence=round(best * 0.9, 3),
            reason="fuzzy match",
        )
    return Link(
        text=text,
        entity_id=None,
        alternatives=[e for e, _ in ranked[:MAX_ALTERNATIVES]],
        reason="no confident match",
    )


# ------------------------------------------------------------------------------- find mentions


def find_mentions(
    text: str,
    graph,
    *,
    context: Sequence[str] = (),
    definition: OntologyDefinition | None = None,
) -> Mentions:
    """What a passage refers to: resolved, ambiguous, and references that match nothing."""
    definition = _definition(definition, graph)
    lex = lexicon_for(graph, definition)
    out = Mentions()
    taken: list[tuple[int, int]] = []

    def free(start: int, end: int) -> bool:
        return all(end <= a or start >= b for a, b in taken)

    # 1. explicit ids: class:key (and edge:), for our prefixes and for prefixes we do not have
    prefixes = {s["prefix"] for s in definition.classes.values()} | set(definition.id_aliases)
    for match in re.finditer(r"\b([a-z][a-z_]*):([\w./-]+[\w])", text):
        result = link(match.group(0), graph, context=context, definition=definition)
        taken.append(match.span())
        if result.entity_id:
            out.resolved.append(result)
        elif match.group(1) in prefixes or match.group(1) in PLACE_WORDS:
            out.unresolved.append(match.group(0))

    # 2. known names: longest phrase first, over the words of the text
    words = [(m.group(0), m.span()) for m in re.finditer(r"[A-Za-z0-9]+", text)]
    i = 0
    while i < len(words):
        for size in range(min(MAX_PHRASE_TOKENS, len(words) - i), 0, -1):
            start, end = words[i][1][0], words[i + size - 1][1][1]
            phrase = normalise(text[start:end])
            if phrase in lex.phrases and free(start, end):
                result = _from_candidates(
                    text[start:end], lex, lex.phrases[phrase], context, exact=True
                )
                taken.append((start, end))
                (out.resolved if result.entity_id else out.ambiguous).append(result)
                i += size
                break
        else:
            i += 1

    # 3. things that look like a reference to a place or thing but matched nothing above
    for pattern in (PLACE_THEN_NAME, NAME_THEN_PLACE):
        for match in pattern.finditer(text):
            if not free(*match.span()):
                continue
            phrase = _without_leading_determiners(match.group(0))
            if pattern is NAME_THEN_PLACE and len(phrase.split()) < 2:
                continue  # "The river": a determiner and a place word, not a named place
            taken.append(match.span())
            out.unresolved.append(phrase)
    seen: dict[str, str] = {}
    for phrase in out.unresolved:  # one report per reference, however it is capitalised or repeated
        seen.setdefault(normalise(phrase) or phrase, phrase)
    out.unresolved = list(seen.values())
    return out


def _without_leading_determiners(phrase: str) -> str:
    words = phrase.split()
    while words and words[0].lower() in NOT_NAMES:
        words.pop(0)
    return " ".join(words).strip()
