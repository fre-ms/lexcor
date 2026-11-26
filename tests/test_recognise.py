# SPDX-FileCopyrightText: 2026 fre.ms
# SPDX-License-Identifier: MIT
"""Recognition: runs of words, the kinds of match, and which candidate wins."""

from __future__ import annotations

from pathlib import Path

from lexcor import Concept, Entry, Kind, Package, Recogniser, Term, open_store, tag
from lexcor.words import only_as_written, runs, split

LEXTERM = (("ph", "f"), ("th", "t"), ("ce", "ze"), ("ci", "zi"), ("c", "k"))


def entries(*terms: str | tuple[str, str]) -> list[Entry]:
    pairs = [(t, t) if isinstance(t, str) else t for t in terms]
    return [Entry(term, "urn:test", "1", code, code, "", "de", "synonym")
            for term, code in pairs]


def found(recogniser: Recogniser, text: str) -> list[tuple[str, str, str]]:
    return [(m.text, a.code, a.kind.value) for m in recogniser.tag(text) for a in m.assignments]


def test_runs_stop_at_the_end_of_a_clause() -> None:
    text = "Morbus Crohn, nach 2019.\n\nA. carotis\ninterna rechts"
    words = split(text)
    formed = {" ".join(w.text for w in words[r.first:r.first + r.length])
              for r in runs(text, words)}
    assert {"Morbus Crohn", "A carotis interna rechts", "carotis interna"} <= formed
    assert not any("Crohn nach" in run or "2019 A" in run for run in formed)
    assert "nach" not in formed and "2019" not in formed  # a function word, a number alone
    edged = {" ".join(w.text for w in words[r.first:r.first + r.length])
             for r in runs(text, words) if r.edged}
    assert "nach 2019" in edged and "Morbus Crohn" not in edged


def test_case_does_not_matter_but_abbreviations_are_as_written() -> None:
    assert only_as_written("HAT") and only_as_written("Ohr") and only_as_written("HbA1c")
    assert not only_as_written("Herz")
    recogniser = Recogniser(entries("Herz", "HAT", "ALS", "Ohr"))
    assert found(recogniser, "Das HERZ hat als Organ ein Ohr. HAT und ALS.") == [
        ("HERZ", "Herz", "as written"), ("Ohr", "Ohr", "as written"),
        ("HAT", "HAT", "as written"), ("ALS", "ALS", "as written")]


def test_inflection_on_a_stem_of_four_letters() -> None:
    recogniser = Recogniser(entries("Blutdruck", "Diagnose", "Morbus Crohn", "Bein"))
    assert found(recogniser, "des Blutdrucks, zwei Diagnosen, eines Morbus Crohns, Beine") == [
        ("Blutdrucks", "Blutdruck", "inflected"), ("Diagnosen", "Diagnose", "inflected"),
        ("Morbus Crohns", "Morbus Crohn", "inflected"), ("Beine", "Bein", "inflected")]
    assert found(Recogniser(entries("Bau")), "Bauer und Baus") == []


def test_umlauts_written_out_and_a_library_table_are_spelling_variants() -> None:
    plain = Recogniser(entries("Hämaturie", "Kolitis", "Enzephalopathie"))
    assert found(plain, "Haematurie, Colitis, hepatische Encephalopathie") == [
        ("Haematurie", "Hämaturie", "spelling variant")]
    table = Recogniser(entries("Hämaturie", "Kolitis", "hepatische Enzephalopathie"), LEXTERM)
    assert found(table, "Haematurie, Colitis, hepatischer Encephalopathie, Hepatische "
                        "Encephalopathien") == [
        ("Haematurie", "Hämaturie", "spelling variant"),
        ("Colitis", "Kolitis", "spelling variant"),
        ("Hepatische Encephalopathien", "hepatische Enzephalopathie", "inflected")]


def test_what_a_package_calls_an_abbreviation_is_matched_only_as_written() -> None:
    plain = Entry("Krea", "urn:test", "1", "K", "Kreatinin", "", "de", "synonym")
    short = Entry("Krea", "urn:test", "1", "K", "Kreatinin", "", "de", "abbreviation")
    text = "Krea normal, krea und Kreas nicht."
    assert [m.text for m in Recogniser([plain]).tag(text)] == ["Krea", "krea", "Kreas"]
    assert [m.text for m in Recogniser([short]).tag(text)] == ["Krea"]


def test_a_better_kind_shuts_out_a_worse_one_for_the_same_words() -> None:
    recogniser = Recogniser(entries("Masern", "Maser"))
    assert found(recogniser, "Masern") == [("Masern", "Masern", "as written")]


def test_the_longer_run_wins() -> None:
    recogniser = Recogniser(entries("Diabetes", "Diabetes mellitus", "Diabetes mellitus Typ 2",
                                    "Morbus", "Crohn", "Morbus Crohn"))
    assert found(recogniser, "Diabetes mellitus Typ 2 und Morbus Crohns") == [
        ("Diabetes mellitus Typ 2", "Diabetes mellitus Typ 2", "as written"),
        ("Morbus Crohns", "Morbus Crohn", "inflected")]


def test_a_function_word_at_the_edge_needs_the_listed_wording() -> None:
    recogniser = Recogniser(entries("Hepatitis A", "Vitamin D", "Weit"))
    assert found(recogniser, "Hepatitis A, Vitamin D; weiter ohne Befund") == [
        ("Hepatitis A", "Hepatitis A", "as written"), ("Vitamin D", "Vitamin D", "as written")]


def test_a_term_wrapped_by_the_end_of_a_line_is_found() -> None:
    recogniser = Recogniser(entries("absolute Arrhythmie", "A. cerebri media"))
    assert found(recogniser, "Im EKG absolute \nArrhythmie. Verschluss der A . cerebri media.") == [
        ("absolute \nArrhythmie", "absolute Arrhythmie", "as written"),
        ("A . cerebri media", "A. cerebri media", "as written")]
    assert found(recogniser, "absolute\n\nArrhythmie") == []


def test_one_mention_carries_every_concept_that_lists_its_term() -> None:
    recogniser = Recogniser(entries(("PET", "scan"), ("PET", "plastic"), ("Pet", "animal")))
    mentions = recogniser.tag("Im PET kein Befund.")
    assert [(m.start, m.end, m.text) for m in mentions] == [(3, 6, "PET")]
    assert [a.code for a in mentions[0].assignments] == ["plastic", "scan"]


def test_tag_reads_the_store_and_follows_its_changes(tmp_path: Path) -> None:
    url = "https://example.org/CodeSystem/sample"
    with open_store(tmp_path / "terms.sqlite") as store:
        store.add_package(Package(url=url, version="7", concepts=[
            Concept("D1", "Bluthochdruck", "disorder", "", [Term("Hypertonie", "de")])]))
        mention, = tag("arterielle Hypertonie", store)
        assignment, = mention.assignments
        assert (assignment.system, assignment.version, assignment.code, assignment.display,
                assignment.cls, assignment.kind, assignment.term) == (
            url, "7", "D1", "Bluthochdruck", "disorder", Kind.AS_WRITTEN, "Hypertonie")
        assert tag("Hypertonie", store, classes=["drug"]) == []
        store.hide(url, "D1", "Hypertonie", "de")
        assert tag("arterielle Hypertonie", store) == []
        assert [m.text for m in tag("Bluthochdruck", store)] == ["Bluthochdruck"]


def test_terms_of_another_language_can_be_left_out(tmp_path: Path) -> None:
    with open_store(tmp_path / "terms.sqlite") as store:
        store.add_package(Package(url="urn:x:two", language="de", concepts=[
            Concept("G1", "Darm", "anatomy", "", [Term("gut", "en"), Term("Intestinum")]),
        ]))
        text = "Es geht ihr gut, Darm und Intestinum unauffällig."
        assert [m.text for m in tag(text, store)] == ["gut", "Darm", "Intestinum"]
        assert [m.text for m in tag(text, store, terms=["de", "la"])] == ["Darm", "Intestinum"]
