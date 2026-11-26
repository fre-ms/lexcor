# SPDX-FileCopyrightText: 2026 fre.ms
# SPDX-License-Identifier: MIT
"""Recognition in four stages: which concepts does a text mention?

1. **Runs of words** (:mod:`lexcor.words`).
2. **Candidates**: for each run, the terms of the packages that it may spell.
3. **Checking**: each candidate gets the kind of its match.
4. **Resolution**: of candidates that overlap, the longer run wins, then the better kind.

The kinds of match (design, section 4.3) are deterministic: the same text and the same
packages give the same mentions.

- **as written**: upper and lower case do not matter; abbreviations (what a package calls
  one, and what looks like one) and words of up to three letters match in nothing but their
  listed spelling.
- **spelling variant**: umlauts written out or not, ``ß`` and ``ss``, and the pairs a library
  names (lexterm: c with k and z, ph with f).
- **inflected**: a German inflection ending on a stem of four or more letters, in a term of
  several words on its last word.

For one run a better kind shuts out a worse one: where a term is listed as written, no other
term is assigned to the same words by its stem.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum

from lexcor.store import Entry, Store
from lexcor.words import (
    MAX_WORDS,
    MIN_STEM,
    SUFFIXES,
    TRANSLITERATION,
    Run,
    Word,
    norm,
    only_as_written,
    runs,
    split,
)

_PARTS = re.compile(r"[\s/]+")
_AROUND = " .,;:!?\"'()[]"
Variants = Sequence[tuple[str, str]]


class Kind(StrEnum):
    """How a mention spells its term."""

    AS_WRITTEN = "as written"
    VARIANT = "spelling variant"
    INFLECTED = "inflected"
    APPROXIMATE = "approximate"


_ORDER = {Kind.AS_WRITTEN: 0, Kind.VARIANT: 1, Kind.INFLECTED: 2, Kind.APPROXIMATE: 3}


@dataclass(frozen=True)
class Assignment:
    """The statement that a mention means a concept, with the term that matched and how."""

    system: str
    version: str
    code: str
    display: str
    cls: str
    kind: Kind
    term: str
    score: float | None = None


@dataclass(frozen=True)
class Mention:
    """A place in a text where a term stands (positions in code points)."""

    start: int
    end: int
    text: str
    assignments: tuple[Assignment, ...]


@dataclass(frozen=True)
class Candidate:
    """What a run may spell, before overlaps are resolved."""

    run: Run
    kind: Kind
    entries: tuple[Entry, ...]


class Recogniser:
    """The recogniser that comes with the core: an index of terms, asked run by run."""

    name = "lexcor"

    def __init__(self, entries: Iterable[Entry], variants: Variants = (),
                 language: str = "de") -> None:
        self.variants = tuple(variants)
        self.language = language
        self.written: dict[tuple[str, ...], list[Entry]] = {}
        self.exact: dict[tuple[str, ...], list[Entry]] = {}
        self.spelt: dict[tuple[str, ...], list[Entry]] = {}
        self.terms = 0
        for entry in entries:
            parts = [p for p in _PARTS.split(entry.term.strip()) if p]
            if not parts or len(parts) > MAX_WORDS:
                continue
            strict = entry.kind == "abbreviation" or (len(parts) == 1
                                                      and only_as_written(parts[0]))
            self.terms += 1
            if strict:
                key = tuple(p.strip(_AROUND) for p in parts)
                self.written.setdefault(key, []).append(entry)
                continue
            key = tuple(norm(p) for p in parts)
            if not all(key):
                continue
            self.exact.setdefault(key, []).append(entry)
            self.spelt.setdefault(self.canonical(key), []).append(entry)

    def canonical(self, key: tuple[str, ...]) -> tuple[str, ...]:
        """A run with the spellings that stand side by side made one."""
        words = []
        for word in key:
            word = word.translate(TRANSLITERATION)
            for old, new in self.variants:
                word = word.replace(old, new)
            words.append(word)
        return tuple(words)

    # -- stages 2 and 3 ----------------------------------------------------------------------------
    def candidate(self, words: Sequence[Word], run: Run) -> Candidate | None:
        """The terms a run may spell, of the best kind that matches at all."""
        chosen = words[run.first:run.first + run.length]
        found = self.written.get(tuple(w.text for w in chosen), [])
        key = tuple(norm(w.text) for w in chosen)
        found = found + self.exact.get(key, [])
        if found:
            return Candidate(run, Kind.AS_WRITTEN, tuple(found))
        if run.edged:
            return None
        found = self.spelt.get(self.canonical(key), [])
        if found:
            return Candidate(run, Kind.VARIANT, tuple(found))
        last = key[-1]
        for suffix in SUFFIXES:
            if last.endswith(suffix) and len(last) - len(suffix) >= MIN_STEM:
                stem = (*key[:-1], last[: -len(suffix)])
                found = self.exact.get(stem) or self.spelt.get(self.canonical(stem), [])
                if found:
                    return Candidate(run, Kind.INFLECTED, tuple(found))
        return None

    # -- stage 4 -----------------------------------------------------------------------------------
    @staticmethod
    def resolve(candidates: Iterable[Candidate]) -> list[Candidate]:
        """Of candidates that overlap, keep the longer run, then the better kind, then the
        one further left."""
        kept: list[Candidate] = []
        taken: set[int] = set()
        for candidate in sorted(candidates, key=lambda c: (-c.run.length, _ORDER[c.kind],
                                                           c.run.first)):
            places = range(candidate.run.first, candidate.run.first + candidate.run.length)
            if taken.isdisjoint(places):
                kept.append(candidate)
                taken.update(places)
        return sorted(kept, key=lambda c: c.run.first)

    def tag(self, text: str) -> list[Mention]:
        """Every mention of a term in the text, each with the concepts it may mean."""
        words = split(text)
        candidates = []
        for run in runs(text, words, self.language):
            candidate = self.candidate(words, run)
            if candidate:
                candidates.append(candidate)
        mentions = []
        for candidate in self.resolve(candidates):
            start = words[candidate.run.first].start
            end = words[candidate.run.first + candidate.run.length - 1].end
            assignments = {
                (e.system, e.code): Assignment(e.system, e.version, e.code, e.display, e.cls,
                                               candidate.kind, e.term)
                for e in reversed(candidate.entries)}
            mentions.append(Mention(start, end, text[start:end],
                                    tuple(assignments[k] for k in sorted(assignments))))
        return mentions


_recognisers: dict[tuple[object, ...], tuple[int, Recogniser]] = {}


def recogniser(store: Store, packages: Iterable[str] | None = None,
               classes: Iterable[str] | None = None, variants: Variants = (),
               language: str = "de", terms: Iterable[str] | None = None) -> Recogniser:
    """The recogniser for a choice of packages and classes of a store, built once and built
    again only when the store has changed.  ``language`` is the language of the text;
    ``terms`` names the languages whose terms are looked for (all when None)."""
    chosen = None if packages is None else tuple(sorted(packages))
    kept = None if classes is None else tuple(sorted(classes))
    spoken = None if terms is None else tuple(sorted(terms))
    key = (str(store.path.resolve()), chosen, kept, tuple(variants), language, spoken)
    version = store.version()
    cached = _recognisers.get(key)
    if cached is None or cached[0] != version:
        cached = (version, Recogniser(store.entries(chosen, kept, spoken), variants, language))
        _recognisers[key] = cached
    return cached[1]


def tag(text: str, store: Store, packages: Iterable[str] | None = None,
        classes: Iterable[str] | None = None, variants: Variants = (),
        language: str = "de", terms: Iterable[str] | None = None) -> list[Mention]:
    """Which concepts does this text mention?  A package in another language than the text
    is better left out with ``terms``: the rules would take the English "gut" for the German
    word (the trial of search by meaning shows it)."""
    return recogniser(store, packages, classes, variants, language, terms).tag(text)
