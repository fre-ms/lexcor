# SPDX-FileCopyrightText: 2026 fre.ms
# SPDX-License-Identifier: MIT
"""Recognition in four stages: which concepts does a text mention?

1. **Runs of words** (:mod:`lexcor.words`).
2. **Candidates**: for each run, the terms of the packages that it may spell.
3. **Checking**: each candidate gets the kind of its match.
4. **Resolution**: of a package's candidates that overlap, the longer run wins, then the
   better kind; packages do not shut each other out.

The kinds of match (design, section 4.3) are deterministic: the same text and the same
packages give the same mentions.

- **as written**: upper and lower case do not matter; abbreviations (what a package calls
  one, and what looks like one) and words of up to three letters match in nothing but their
  listed spelling.
- **spelling variant**: umlauts written out or not, ``ß`` and ``ss``, and the pairs a library
  names (lexterm: c with k and z, ph with f).
- **inflected**: a German inflection ending on a stem of four or more letters, in a term of
  several words on its last word.

Within one package a better kind shuts out a worse one: where a term is listed as written,
no other term of that package is assigned to the same words by its stem.  Every package
answers for itself, so that one wording is coded in every code system that knows it: a
mention carries an assignment from each package that lists its words, each with the kind of
its own match.
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
    """The statement that a mention means a concept, with the term that matched and how.
    ``package`` and ``package_version`` name the package the term is in where that is not
    the code system itself (a supplement); ``terminal`` says whether the code is terminal in
    a code system with a hierarchy (None in one without)."""

    system: str
    version: str
    code: str
    display: str
    cls: str
    kind: Kind
    term: str
    score: float | None = None
    package: str = ""
    package_version: str = ""
    terminal: bool | None = None


@dataclass(frozen=True)
class Mention:
    """A place in a text where a term stands (positions in code points)."""

    start: int
    end: int
    text: str
    assignments: tuple[Assignment, ...]


@dataclass(frozen=True)
class Candidate:
    """What a run may spell, before overlaps are resolved: the terms that match it, each with
    the kind of its match, and the best of those kinds."""

    run: Run
    kind: Kind
    found: tuple[tuple[Kind, Entry], ...]


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
        """The terms a run may spell: for every package those of the best kind by which that
        package matches at all."""
        chosen = words[run.first:run.first + run.length]
        key = tuple(norm(w.text) for w in chosen)
        ranked = [((0, 0), Kind.AS_WRITTEN, entry) for entry in
                  self.written.get(tuple(w.text for w in chosen), []) + self.exact.get(key, [])]
        if not run.edged:
            ranked += [((1, 0), Kind.VARIANT, entry)
                       for entry in self.spelt.get(self.canonical(key), [])]
            ranked += self._inflected(key)
        best: dict[str, tuple[int, int]] = {}
        for rank, _kind, entry in ranked:
            if entry.system not in best or rank < best[entry.system]:
                best[entry.system] = rank
        found = tuple((kind, entry) for rank, kind, entry in ranked if rank == best[entry.system])
        if not found:
            return None
        return Candidate(run, min((kind for kind, _e in found), key=lambda k: _ORDER[k]), found)

    def _inflected(self, key: tuple[str, ...]) -> list[tuple[tuple[int, int], Kind, Entry]]:
        """The terms a run spells with an ending on its last word, each with the place of its
        ending in the order of the endings (the first ending that fits a package is its)."""
        ranked = []
        last = key[-1]
        for number, suffix in enumerate(SUFFIXES):
            if last.endswith(suffix) and len(last) - len(suffix) >= MIN_STEM:
                stem = (*key[:-1], last[: -len(suffix)])
                for entry in self.exact.get(stem, []) + self.spelt.get(self.canonical(stem), []):
                    ranked.append(((2, number), Kind.INFLECTED, entry))
        return ranked

    # -- stage 4 -----------------------------------------------------------------------------------
    @staticmethod
    def resolve(candidates: Iterable[Candidate],
                nested: bool = False) -> list[tuple[Run, tuple[tuple[Kind, Entry], ...]]]:
        """Which runs are mentions, and with which terms.  Every package answers for itself:
        of its own candidates that overlap it keeps the longer run, then the better kind,
        then the one further left.  A run is a mention where at least one package keeps it,
        with the terms of the packages that keep it; so a package that knows only a shorter
        wording inside a longer mention of another package still codes what it knows.  With
        ``nested`` a package's own shorter terms inside a run it keeps are given as well."""
        own: dict[str, list[tuple[Kind, Candidate]]] = {}
        for candidate in candidates:
            kinds = {entry.system: kind for kind, entry in candidate.found}
            for system, kind in kinds.items():
                own.setdefault(system, []).append((kind, candidate))
        keepers: dict[tuple[int, int], set[str]] = {}
        for system, theirs in own.items():
            taken: set[int] = set()
            kept: list[Run] = []
            for _kind, candidate in sorted(theirs, key=lambda p: (
                    -p[1].run.length, _ORDER[p[0]], p[1].run.first)):
                run = candidate.run
                places = range(run.first, run.first + run.length)
                inside = nested and any(k.first <= run.first
                                        and run.first + run.length <= k.first + k.length
                                        for k in kept)
                if taken.isdisjoint(places):
                    kept.append(run)
                    taken.update(places)
                elif not inside:
                    continue
                keepers.setdefault((run.first, run.length), set()).add(system)
        resolved = []
        for candidate in candidates:
            systems = keepers.get((candidate.run.first, candidate.run.length))
            if systems:
                resolved.append((candidate.run, tuple(
                    (kind, entry) for kind, entry in candidate.found if entry.system in systems)))
        return sorted(resolved, key=lambda r: (r[0].first, r[0].length))

    def tag(self, text: str, nested: bool = False) -> list[Mention]:
        """Every mention of a term in the text, each with the concepts it may mean, in reading
        order.  Mentions of different packages may overlap: each package gives the longest
        wording it knows at a place.  ``nested`` asks for a package's shorter terms inside
        its longer ones as well."""
        words = split(text)
        candidates = []
        for run in runs(text, words, self.language):
            candidate = self.candidate(words, run)
            if candidate:
                candidates.append(candidate)
        mentions = []
        for run, found in self.resolve(candidates, nested):
            start = words[run.first].start
            end = words[run.first + run.length - 1].end
            assignments = {
                (e.system, e.code): Assignment(e.system, e.version, e.code, e.display, e.cls,
                                               kind, e.term, None, e.package, e.package_version,
                                               e.terminal)
                for kind, e in reversed(found)}
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
    key = (store.key, chosen, kept, tuple(variants), language, spoken)
    version = store.version()
    cached = _recognisers.get(key)
    if cached is None or cached[0] != version:
        cached = (version, Recogniser(store.entries(chosen, kept, spoken), variants, language))
        _recognisers[key] = cached
    return cached[1]


def tag(text: str, store: Store, packages: Iterable[str] | None = None,
        classes: Iterable[str] | None = None, variants: Variants = (),
        language: str = "de", terms: Iterable[str] | None = None,
        nested: bool = False) -> list[Mention]:
    """Which concepts does this text mention?  A package in another language than the text
    is better left out with ``terms``: the rules would take the English "gut" for the German
    word (the trial of search by meaning shows it)."""
    return recogniser(store, packages, classes, variants, language, terms).tag(text, nested)
