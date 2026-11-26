# SPDX-FileCopyrightText: 2026 fre.ms
# SPDX-License-Identifier: MIT
"""Is this span vocabulary of these packages?

The second question beside :func:`lexcor.recognise.tag`, kept apart from it (design, section
4.6).  It gives no codes and is generous on purpose: a part of a hyphenated word counts
("Parkinson-Syndrom"), as does an eponym that reports use on its own when it is listed
before one of the head nouns a library names ("Babinski" of "Babinski-Zeichen").  A program
that guesses at names needs it to leave such words alone.

These are the rules of PseudonyMed's matcher, from which this module comes.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from lexcor.store import Store
from lexcor.words import MAX_WORDS, MIN_STEM, SUFFIXES, TOKEN, TRANSLITERATION, norm

_BRACKET = re.compile(r"\s*\([^)]*\)\s*$")
_PARTS = re.compile(r"[\s/]+")
MIN_TERM = 3
MIN_EPONYM = 4
Span = tuple[int, int]


def _forms(term: str) -> set[str]:
    """A term as the index holds it, as written and with umlauts written out."""
    form = norm(term)
    return {form, form.translate(TRANSLITERATION)} - {""}


class Cover:
    """The vocabulary of some packages as one set of words and phrases."""

    def __init__(self, terms: Iterable[str] = (), heads: Iterable[str] = ()) -> None:
        self.heads = {norm(h) for h in heads}
        self.single: set[str] = set()
        self.phrases: dict[str, set[tuple[str, ...]]] = {}
        self.count = 0
        self.add(terms)

    def add(self, terms: Iterable[str]) -> int:
        """Take terms in (a trailing bracket removed, entries under three characters left
        out); returns how many were taken."""
        taken = 0
        for raw in terms:
            term = _BRACKET.sub("", raw.strip())
            if len(term) < MIN_TERM:
                continue
            for form in _forms(term):
                self._remember(form)
            taken += 1
        self.count += taken
        return taken

    def _remember(self, form: str) -> None:
        words = tuple(w for w in _PARTS.split(form) if w)
        if len(words) == 1:
            self.single.add(words[0])
            parts = words[0].split("-")
            if len(parts) == 2 and parts[1] in self.heads and len(parts[0]) >= MIN_EPONYM:
                self.single.add(parts[0])
        elif 1 < len(words) <= MAX_WORDS:
            self.phrases.setdefault(words[0], set()).add(words)

    # -- single words ------------------------------------------------------------------------------
    def is_term(self, word: str) -> bool:
        """Is this word listed, an inflected form of a listed word, or a hyphenated word
        with a listed part?"""
        return any(self._known(form) for form in _forms(word))

    def _known(self, form: str) -> bool:
        if form in self.single or self._stemmed(form):
            return True
        if "-" in form and any(len(part) >= MIN_TERM and (part in self.single
                                                          or self._stemmed(part))
                               for part in form.split("-")):
            return True
        return form.endswith("-") and form[:-1] in self.single

    def _stemmed(self, form: str) -> bool:
        return any(form.endswith(s) and len(form) - len(s) >= MIN_STEM
                   and form[: -len(s)] in self.single for s in SUFFIXES)

    # -- text --------------------------------------------------------------------------------------
    def spans(self, text: str) -> list[Span]:
        """The places of the vocabulary in a text, phrases before single words."""
        tokens = [(m.start(), m.end(), m.group(0)) for m in TOKEN.finditer(text)]
        forms = [norm(word) for _a, _b, word in tokens]
        taken = [False] * len(tokens)
        found: list[Span] = []
        for i, (start, _end, _word) in enumerate(tokens):
            length = self._phrase(forms, i)
            if length:
                found.append((start, tokens[i + length - 1][1]))
                taken[i:i + length] = [True] * length
        # words of up to three letters count only with a capital: "hat" is a verb, "Ohr" a term
        found += [(a, b) for i, (a, b, word) in enumerate(tokens)
                  if not taken[i] and (len(word) > 3 or word[:1].isupper()) and self.is_term(word)]
        return sorted(found)

    def _phrase(self, forms: list[str], i: int) -> int:
        """How many words the longest listed phrase from word ``i`` on has (0 when none)."""
        listed = self.phrases.get(forms[i]) or self.phrases.get(forms[i].translate(TRANSLITERATION))
        for phrase in sorted(listed or (), key=len, reverse=True):
            if i + len(phrase) <= len(forms) and all(
                    form == word or form.translate(TRANSLITERATION) == word
                    for form, word in zip(forms[i:i + len(phrase)], phrase, strict=True)):
                return len(phrase)
        return 0

    def covers(self, text: str, start: int, end: int, spans: list[Span] | None = None,
               whole: bool = False) -> bool:
        """Does ``text[start:end]`` touch the vocabulary, or with ``whole`` consist of it?
        ``spans`` saves the search when a caller asks about many places of one text."""
        spans = self.spans(text) if spans is None else spans
        touched = [s for s in spans if start < s[1] and end > s[0]]
        if not touched:
            return False
        return not whole or all(any(s[0] <= m.start() and m.end() <= s[1] for s in touched)
                                for m in TOKEN.finditer(text, start, end))


_covers: dict[tuple[object, ...], tuple[int, Cover]] = {}


def cover(store: Store, packages: Iterable[str] | None = None,
          classes: Iterable[str] | None = None, heads: Iterable[str] = (),
          terms: Iterable[str] | None = None) -> Cover:
    """The vocabulary of a choice of packages and classes of a store, built once and built
    again only when the store has changed.  ``terms`` names the languages whose terms count
    (all when None)."""
    chosen = None if packages is None else tuple(sorted(packages))
    kept = None if classes is None else tuple(sorted(classes))
    spoken = None if terms is None else tuple(sorted(terms))
    key = (str(store.path.resolve()), chosen, kept, tuple(sorted(heads)), spoken)
    version = store.version()
    cached = _covers.get(key)
    if cached is None or cached[0] != version:
        cached = (version, Cover((e.term for e in store.entries(chosen, kept, spoken)), heads))
        _covers[key] = cached
    return cached[1]


def covers(text: str, start: int, end: int, store: Store,
           packages: Iterable[str] | None = None, whole: bool = False) -> bool:
    """Is this span vocabulary of these packages?"""
    return cover(store, packages).covers(text, start, end, whole=whole)
