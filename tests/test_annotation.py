# SPDX-FileCopyrightText: 2026 fre.ms
# SPDX-License-Identifier: MIT
"""The annotation document: proposals, a person's decisions, later runs, a changed text."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lexcor import Annotation, AnnotationError, Assignment, Kind, Mention, annotate, load_annotation
from lexcor.annotation import CONFIRMED, PROPOSED, REJECTED, digest, loads

TEXT = "Diagnosen: Morbus Parkinson, ED 2019. Hypertonie bekannt."
ICD = "https://example.org/CodeSystem/icd"


def code(value: str, display: str, cls: str = "disorder", system: str = ICD) -> Assignment:
    return Assignment(system, "2026", value, display, cls, Kind.AS_WRITTEN, display)


def found(*places: tuple[str, Assignment]) -> list[Mention]:
    return [Mention(TEXT.index(word), TEXT.index(word) + len(word), word, (assignment,))
            for word, assignment in places]


def first_run() -> Annotation:
    return annotate(TEXT, found(("Morbus Parkinson", code("G20", "Parkinson-Syndrom")),
                                ("Hypertonie", code("I10", "Hypertonie"))),
                    "lexcor 0.2", made_with="lexsample 0.2",
                    packages=[{"url": ICD, "version": "2026", "sha256": "ab"}],
                    settings={"packages": [ICD], "classes": []})


def test_the_document_holds_what_the_design_says() -> None:
    document = first_run().document()
    assert list(document) == ["lexcor", "made_with", "text", "packages", "settings", "mentions"]
    assert document["text"] == {"sha256": digest(TEXT), "length": len(TEXT)}
    first = document["mentions"][0]
    assert first == {
        "id": "m1", "start": 11, "end": 27, "exact": "Morbus Parkinson",
        "prefix": "Diagnosen: ", "suffix": ", ED 2019. Hyper",
        "assignments": [{
            "system": ICD, "version": "2026", "code": "G20", "display": "Parkinson-Syndrom",
            "class": "disorder", "term": "Parkinson-Syndrom", "match": {"kind": "as written"},
            "origin": {"by": "machine", "recogniser": "lexcor 0.2"}, "status": "proposed"}]}
    assert [m["id"] for m in document["mentions"]] == ["m1", "m2"]


def test_the_same_content_gives_the_same_bytes(tmp_path: Path) -> None:
    one, two = first_run(), first_run()
    assert one.dumps() == two.dumps() and one.dumps().endswith("}\n")
    one.save(tmp_path / "letter.json")
    again = load_annotation(tmp_path / "letter.json")
    assert again == one and again.dumps() == one.dumps()
    assert (tmp_path / "letter.json").read_bytes() == one.dumps().encode("utf-8")


def test_a_rejection_stays_and_is_not_proposed_again() -> None:
    annotation = first_run()
    annotation.reject("m2", by="reviewer", at="2026-11-27T10:00:00")
    annotation.confirm("m1", code="G20")
    annotation.update(TEXT, found(("Morbus Parkinson", code("G20", "Parkinson-Syndrom")),
                                  ("Hypertonie", code("I10", "Hypertonie"))), "lexcor 0.2")
    assert [(n.id, a.code, a.status) for n, a in annotation.assignments(None)] == [
        ("m1", "G20", CONFIRMED), ("m2", "I10", REJECTED)]
    hypertension = annotation.mention("m2").assignments[0]
    assert (hypertension.decided_by, hypertension.decided_at) == ("reviewer", "2026-11-27T10:00:00")
    assert annotation.document()["mentions"][1]["assignments"][0]["decision"] == {
        "by": "reviewer", "at": "2026-11-27T10:00:00"}
    assert "decision" not in annotation.document()["mentions"][0]["assignments"][0]
    annotation.reopen("m2")
    assert annotation.mention("m2").assignments[0].status == PROPOSED


def test_a_run_replaces_only_its_own_undecided_proposals() -> None:
    annotation = first_run()
    other = code("Q11085", "Parkinson-Krankheit", system="http://www.wikidata.org/entity/")
    annotation.update(TEXT, found(("Morbus Parkinson", other)), "meaning 0.1")
    annotation.confirm("m1", system=ICD)
    # the first recogniser runs again and finds only the hypertension, under another code
    annotation.update(TEXT, found(("Hypertonie", code("I10.9", "Hypertonie, n. n. bez."))),
                      "lexcor 0.2")
    assert [(n.id, a.code, a.recogniser, a.status) for n, a in annotation.assignments(None)] == [
        ("m1", "Q11085", "meaning 0.1", PROPOSED),    # another recogniser's: stays
        ("m1", "G20", "lexcor 0.2", CONFIRMED),       # decided: stays though not found again
        ("m2", "I10.9", "lexcor 0.2", PROPOSED)]      # the undecided proposal is replaced
    annotation.update(TEXT, [], "lexcor 0.2")
    assert [n.id for n in annotation.mentions] == ["m1"]
    annotation.update(TEXT, found(("Hypertonie", code("I10", "Hypertonie"))), "lexcor 0.2")
    assert [n.id for n in annotation.mentions] == ["m1", "m2"]  # numbers are not given twice


def test_a_person_marks_a_place_and_confirms_all_of_a_package() -> None:
    annotation = first_run()
    start = TEXT.index("ED 2019")
    marked = annotation.add(TEXT, start, start + 2, by="reviewer")
    assert (marked.id, marked.by, marked.assignments) == ("m3", "person", [])
    annotation.add(TEXT, start, start + 2, code("R69", "Erstdiagnose", "context"))
    assert [(a.code, a.by, a.status) for a in annotation.mention("m3").assignments] == [
        ("R69", "person", CONFIRMED)]
    assert annotation.confirm_all(ICD, by="reviewer") == 2
    assert [a.code for _n, a in annotation.assignments()] == ["G20", "R69", "I10"]
    annotation.update(TEXT, [], "lexcor 0.2")  # a run that finds nothing takes nothing decided
    assert [n.id for n in annotation.mentions] == ["m1", "m3", "m2"]
    with pytest.raises(AnnotationError) as wrong:
        annotation.add(TEXT + " Nachtrag.", 0, 4)
    assert wrong.value.kind == "other-text"
    with pytest.raises(AnnotationError) as nowhere:
        annotation.add(TEXT, 50, 500)
    assert nowhere.value.kind == "no-place"
    with pytest.raises(AnnotationError) as unknown:
        annotation.confirm("m9")
    assert unknown.value.kind == "no-mention"
    with pytest.raises(AnnotationError) as none:
        annotation.reject("m1", code="X")
    assert none.value.kind == "no-assignment"


def test_mentions_follow_a_text_that_has_changed() -> None:
    annotation = first_run()
    annotation.confirm("m1")
    annotation.confirm("m2")
    changed = "Brief vom 3. Mai.\n" + TEXT.replace("Hypertonie bekannt", "Blutdruck normal")
    annotation.update(changed, [], "lexcor 0.2")
    moved = annotation.mention("m1")
    assert changed[moved.start:moved.end] == "Morbus Parkinson"
    assert moved.prefix.endswith("gnosen: ")
    assert [n.id for n in annotation.lost] == ["m2"]  # kept in a list of its own, not dropped
    assert annotation.document()["lost"][0]["exact"] == "Hypertonie"
    assert (annotation.sha256, annotation.length) == (digest(changed), len(changed))
    twice = "Parkinson fraglich. " + changed + " Kein Parkinson."
    annotation.update(twice, [], "lexcor 0.2")
    moved = annotation.mention("m1")
    assert twice[moved.start - 11:moved.end] == "Diagnosen: Morbus Parkinson"


def test_positions_carry_no_wording() -> None:
    annotation = first_run()
    text = json.dumps(annotation.positions(), ensure_ascii=False)
    assert "Parkinson" not in text and "G20" not in text and "disorder" in text
    assert annotation.positions()[0] == {"id": "m1", "start": 11, "end": 27, "assignments": [
        {"class": "disorder", "status": "proposed", "by": "machine"}]}


@pytest.mark.parametrize("text", [
    "no json", "[]", '{"lexcor": 2, "mentions": []}',
    '{"lexcor": 1, "mentions": [{"id": "m1"}]}',
    '{"lexcor": 1, "mentions": [{"id": "m1", "start": 0, "end": 1, "exact": "x",'
    ' "assignments": [{"system": "s", "code": "c", "status": "perhaps"}]}]}',
])
def test_what_is_no_annotation_document_is_refused(text: str) -> None:
    with pytest.raises(AnnotationError) as refused:
        loads(text)
    assert refused.value.kind == "not-an-annotation"
