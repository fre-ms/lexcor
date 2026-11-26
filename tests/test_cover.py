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


def test_short_words_count_only_with_a_capital() -> None:
    vocabulary = small()
    text = "Das Ohr ist frei, ohr nicht."
    assert [text[a:b] for a, b in vocabulary.spans(text)] == ["Ohr"]


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
