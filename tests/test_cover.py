# SPDX-FileCopyrightText: 2026 fre.ms
# SPDX-License-Identifier: MIT
"""The generous question: is this span vocabulary?  The rules are PseudonyMed's."""

from __future__ import annotations

from pathlib import Path

from lexcor import Concept, Cover, Package, Term, cover, covers, open_store

HEADS = ("Zeichen", "Syndrom", "Test")
TERMS = ["Bauch", "Herz", "Morbus Parkinson", "arterielle Hypertonie", "Hypertonie", "Ramipril",
         "Babinski-Zeichen", "Mini-Mental-Status-Test", "Alzheimer", "Parkinson", "Ohr",
         "Müller-Gang", "Berliner Blau", "Hämaturie (Symptom)"]


def small() -> Cover:
    return Cover(TERMS, HEADS)


def test_single_words_inflection_and_hyphens() -> None:
    vocabulary = small()
    assert vocabulary.count == len(TERMS)
    for word in ("Bauch", "BAUCH", "Bauches", "Herzen", "Ramipril", "Parkinson-Syndrom",
                 "Alzheimer-Demenz", "Babinski", "Mini-Mental-Status-Test", "Hypertonie",
                 "Haematurie", "Herz-"):
        assert vocabulary.is_term(word), word
    for word in ("Bauer", "Baur", "Flantz", "Herzog", "Bau", "Müller", "Berlin", "Blau"):
        assert not vocabulary.is_term(word), word


def test_an_eponym_counts_only_before_a_head_the_library_names() -> None:
    assert not Cover(["Babinski-Zeichen"]).is_term("Babinski")
    assert Cover(["Babinski-Zeichen"], ["Zeichen"]).is_term("Babinski")


def test_which_first_parts_count_as_eponyms() -> None:
    vocabulary = Cover(["Babinski-Zeichen", "Berlin-Blau", "Konigsmark-Hollander-Berlin-Syndrom",
                        "Abc-Test", "Parkinson"], HEADS)
    assert vocabulary.is_term("Babinski")
    for word in ("Berlin",       # "Blau" is no head a library names
                 "Konigsmark",   # a term of four parts: the town in it matters more
                 "Abc"):         # a first part of under four letters
        assert not vocabulary.is_term(word), word


def test_how_terms_are_filed() -> None:
    vocabulary = Cover(heads=["Reflex"])
    taken = vocabulary.add(["Bauch (Anatomie)", "ab", "Tabletten/Kapseln",
                            "eins zwei drei vier fünf sechs", "Trömner-Reflex"])
    assert taken == vocabulary.count == 4  # the phrase of six words is taken but not filed
    assert vocabulary.single == {"bauch", "trömner-reflex", "troemner-reflex", "trömner",
                                 "troemner"}
    assert vocabulary.phrases == {"tabletten": {("tabletten", "kapseln")}}


def test_the_longest_phrase_wins_and_umlauts_may_be_written_out() -> None:
    vocabulary = Cover(["Morbus Parkinson", "Morbus Parkinson Stadium III", "Hüft-TEP rechts"])
    text = "Morbus Parkinson Stadium III, Hueft-TEP rechts. Morbus Parkinson links."
    assert [text[a:b] for a, b in vocabulary.spans(text)] == [
        "Morbus Parkinson Stadium III", "Hueft-TEP rechts", "Morbus Parkinson"]
    assert vocabulary.spans("Parkinson links") == []  # no phrase starts in the middle of one
    assert vocabulary.spans("Hüft-TEP rechts") == [(0, 15)]


def test_phrases_count_as_a_whole() -> None:
    vocabulary = small()
    text = "Diagnosen: Morbus Parkinson, arterielle Hypertonie; Bauch weich, Herz rhythmisch."
    spans = vocabulary.spans(text)
    assert [text[a:b] for a, b in spans] == ["Morbus Parkinson", "arterielle Hypertonie",
                                             "Bauch", "Herz"]
    start = text.index("Morbus")
    assert vocabulary.covers(text, start, start + 6, spans)
    assert vocabulary.covers(text, text.index("Bauch"), text.index("Bauch") + 5, spans, whole=True)
    assert not vocabulary.covers(text, 0, 9, spans)
    assert vocabulary.covers(text, start, text.index("arterielle") + 10, whole=True)
    assert not vocabulary.covers(text, text.index("Bauch"), text.index("weich") + 5, whole=True)
    gap = text.index("Morbus") + 6  # the blank inside a phrase holds no word of its own
    assert vocabulary.covers(text, gap, gap + 1, spans, whole=True)


def test_short_words_count_only_with_a_capital() -> None:
    vocabulary = small()
    text = "Das Ohr ist frei, ohr nicht."
    assert [text[a:b] for a, b in vocabulary.spans(text)] == ["Ohr"]
    short = Cover(["Ohr", "hat", "Bauch"])
    text = "HAT hat, bauches und Bauer."  # over three letters, case does not matter
    assert [text[a:b] for a, b in short.spans(text)] == ["HAT", "bauches"]


def test_the_cover_of_a_store_follows_its_changes(tmp_path: Path) -> None:
    url = "https://example.org/CodeSystem/sample"
    with open_store(tmp_path / "terms.sqlite") as store:
        store.add_package(Package(url=url, concepts=[
            Concept("E1", "Babinski-Zeichen", "sign", "", [Term("Babinski-Zeichen", "de")]),
            Concept("D1", "Hypertonie", "disorder", "", [Term("Hypertonie", "de")])]))
        assert covers("Hypertonie bekannt", 0, 10, store)
        assert cover(store, heads=HEADS).is_term("Babinski")
        assert not cover(store, classes=["disorder"], heads=HEADS).is_term("Babinski")
        store.hide(url, "D1")
        assert not covers("Hypertonie bekannt", 0, 10, store)
