# SPDX-FileCopyrightText: 2026 fre.ms
# SPDX-License-Identifier: MIT
"""Words and runs of words: the first stage of recognition.

A text is cut into words, and every run of up to five words that may be a term is formed
(design, section 4.2).  A run does not cross an empty line or the end of a clause, a single
function word or a number alone is no run, and a run that begins or ends with a function
word is marked: only a term listed in exactly these words may be found there ("Hepatitis A").
A single line break does not end a run: letters come with their lines wrapped, and a term of
two words is often cut by the end of a line.  Function words come from a small list per
language, not from a language model.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass

TOKEN = re.compile(r"[^\W\d_][\w\-]*|\d+[\w\-]*")
SUFFIXES = ("ern", "en", "es", "er", "em", "e", "n", "s")
TRANSLITERATION = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})
MAX_WORDS = 5
MIN_STEM = 4
# What may stand between two words of one run: blanks, one slash, one line break, and after a
# short word the full stop of an abbreviation ("A. carotis").  Anything else (a comma, a
# bracket, an empty line) ends the clause.
_GAPS = frozenset({"", "/", "\n", "/\n", "\n/"})
_ABBREVIATION_GAPS = frozenset({".", ".\n"})
_BLANKS = str.maketrans("", "", " \t\r")
MAX_ABBREVIATION = 4

FUNCTION_WORDS: dict[str, frozenset[str]] = {"de": frozenset("""
aber alle allem allen aller alles als also am an ander andere anderem anderen anderer anderes
auch auf aus bei beim bin bis bist da damit dann das dass dem den denn der des dessen die dies
diese diesem diesen dieser dieses doch dort du durch ein eine einem einen einer eines er es
etwas euch euer für gegen hat hatte hatten hier hin hinter ich ihm ihn ihnen ihr ihre ihrem
ihren ihrer im in indem ins ist jede jedem jeden jeder jedes jene kann kein keine keinem keinen
keiner man mit muss nach nicht nichts noch nun nur ob oder ohne sehr sein seine seinem seinen
seiner sich sie sind so solche sondern sonst sowie über um und uns unter vom von vor war waren
was weg weil weiter welche wenn wer werde werden wie wieder will wir wird wo zu zum zur zwar
zwischen bzw ca ggf sog vgl z b d h u a o g
""".split())}


@dataclass(frozen=True)
class Word:
    """One word of a text: its place in code points and its wording."""

    start: int
    end: int
    text: str


@dataclass(frozen=True)
class Run:
    """Up to five words in a row: the number of its first word, how many words it has, and
    whether it begins or ends with a function word."""

    first: int
    length: int
    edged: bool


def norm(word: str) -> str:
    """A word as the index holds it: case-folded, without the punctuation around it."""
    return word.casefold().strip(" .,;:!?\"'()[]")


def only_as_written(term: str) -> bool:
    """Abbreviations and words of up to three letters match only in their own spelling:
    "HAT" is a term, "hat" is not."""
    if len(term) <= 3:
        return True
    capitals = sum(1 for c in term if c.isupper())
    return len(term) <= 6 and " " not in term and (capitals >= 2 or any(c.isdigit() for c in term))


def split(text: str) -> list[Word]:
    """The words of a text in their order."""
    return [Word(m.start(), m.end(), m.group(0)) for m in TOKEN.finditer(text)]


def is_function_word(word: str, language: str = "de") -> bool:
    """Is this word an article, preposition, conjunction or the like?  A word written all in
    capitals is not: "ALS" and "HAT" are abbreviations."""
    if len(word) >= 2 and word.isupper():
        return False
    return word.lower() in FUNCTION_WORDS.get(language, frozenset())


def joined(text: str, left: Word, right: Word) -> bool:
    """May two neighbouring words stand in one run?"""
    gap = text[left.end:right.start].translate(_BLANKS)
    return gap in _GAPS or (len(left.text) <= MAX_ABBREVIATION and gap in _ABBREVIATION_GAPS)


def runs(text: str, words: Sequence[Word], language: str = "de") -> Iterator[Run]:
    """Every run of one to five words that may be a term, the longer runs of a place first."""
    functional = [is_function_word(w.text, language) for w in words]
    for first in range(len(words)):
        reach = 1
        while (reach < MAX_WORDS and first + reach < len(words)
               and joined(text, words[first + reach - 1], words[first + reach])):
            reach += 1
        for length in range(reach, 0, -1):
            last = first + length - 1
            if length == 1 and (functional[first] or words[first].text.isdigit()):
                continue
            yield Run(first, length, functional[first] or functional[last])
