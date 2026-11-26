# SPDX-FileCopyrightText: 2026 fre.ms
# SPDX-License-Identifier: MIT
"""Packages as FHIR CodeSystem and as CSV."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lexcor import Concept, Package, PackageError, Term, read_package, tidy
from lexcor.package import (
    DESIGNATION_USAGE,
    PARENT,
    REDISTRIBUTABLE,
    TERM_KINDS,
    from_codesystem,
    read_csv,
    to_codesystem,
    write_codesystem,
    write_csv,
)


def sample() -> Package:
    return Package(
        url="https://example.org/CodeSystem/sample", version="2026", name="Sample",
        title="Sample terms", publisher="Nobody", copyright="CC0 1.0", language="de",
        redistributable=True,
        concepts=[
            Concept("D1", "Bluthochdruck", "disorder", "", [
                Term("Bluthochdruck", "de", "preferred"),
                Term("arterielle Hypertonie", "de", "synonym"),
                Term("hypertension", "en", "synonym"),
                Term("high blood pressure", "en", "preferred"),
                Term("HTN", "en", "abbreviation")]),
            Concept("D2", "Hypertensive Krise", "disorder", "D1",
                    [Term("Hypertensive Krise", "de", "preferred")]),
        ])


def test_a_codesystem_holds_everything_the_core_needs(tmp_path: Path) -> None:
    file = tmp_path / "sample.json"
    write_codesystem(sample(), file)
    resource = json.loads(file.read_text(encoding="utf-8"))
    assert resource["resourceType"] == "CodeSystem" and resource["content"] == "complete"
    assert resource["extension"] == [{"url": REDISTRIBUTABLE, "valueBoolean": True}]
    first = resource["concept"][0]
    assert first["display"] == "Bluthochdruck"
    assert [(d["value"], d.get("use")) for d in first["designation"]] == [
        ("high blood pressure", {"system": DESIGNATION_USAGE, "code": "display"}),
        ("HTN", {"system": TERM_KINDS, "code": "abbreviation"}),
        ("arterielle Hypertonie", None), ("hypertension", None)]
    assert all("extension" not in d for d in first["designation"])
    assert {"code": "parent", "uri": PARENT, "type": "code"} in resource["property"]
    assert resource["concept"][1]["property"] == [
        {"code": "class", "valueString": "disorder"}, {"code": "parent", "valueCode": "D1"}]
    assert read_package(file) == tidy(sample())


def test_the_fingerprint_follows_the_content_not_the_file() -> None:
    again = sample()
    again.concepts.reverse()
    assert again.sha256() == sample().sha256()
    again.concepts[0].terms.append(Term("Hochdruckkrise", "de"))
    assert again.sha256() != sample().sha256()


def test_a_codesystem_of_another_publisher_is_read() -> None:
    resource = {
        "resourceType": "CodeSystem", "url": "http://example.org/other", "language": "de",
        "concept": [{
            "code": "A", "display": "Oben",
            "designation": [
                {"language": "de", "value": "Oben"},
                {"use": {"system": "http://snomed.info/sct", "code": "900000000000013009"},
                 "value": "Droben"},
                {"language": "en", "value": "Above",
                 "use": {"system": DESIGNATION_USAGE, "code": "display"}},
                {"use": {"system": "http://example.org/uses", "code": "abbreviation"},
                 "value": "Ob."}],
            "concept": [{"code": "A1", "display": "Unten"}]}]}
    package = from_codesystem(resource)
    assert package.redistributable is False
    assert [(c.code, c.parent) for c in package.concepts] == [("A", ""), ("A1", "A")]
    assert [(t.value, t.kind) for t in package.concepts[0].terms] == [
        ("Oben", "preferred"), ("Above", "preferred"), ("Droben", "synonym"),
        ("Ob.", "synonym")]  # a use of someone else's that is not known says nothing


@pytest.mark.parametrize("resource", [
    {"resourceType": "ValueSet", "url": "http://example.org/x"},
    {"resourceType": "CodeSystem"},
    {"resourceType": "CodeSystem", "url": "http://example.org/x", "content": "not-present"},
])
def test_what_is_no_usable_codesystem_is_refused(resource: dict[str, object]) -> None:
    with pytest.raises(PackageError):
        from_codesystem(resource)


def test_a_list_made_by_hand(tmp_path: Path) -> None:
    file = tmp_path / "Eigene Liste.csv"
    file.write_text("# a comment\nterm,code,display,class,kind\n"
                    "Bluthochdruck,D1,Bluthochdruck,disorder,preferred\n"
                    "arterielle Hypertonie,D1,,,\nRamipril,,,drug,\n", encoding="utf-8")
    package = read_csv(file, language="de")
    assert package.url == "" and package.redistributable is False
    with pytest.raises(PackageError):  # a list has no URI until a store takes it in
        write_codesystem(package, tmp_path / "list.json")
    assert [(c.code, c.display, c.cls, [t.value for t in c.terms]) for c in package.concepts] == [
        ("D1", "Bluthochdruck", "disorder", ["Bluthochdruck", "arterielle Hypertonie"]),
        ("Ramipril", "Ramipril", "drug", ["Ramipril"])]
    out = tmp_path / "out.csv"
    assert write_csv(package, out) == 3
    assert read_csv(out, title="Eigene Liste", language="de") == package


def test_a_plain_list_of_words_is_a_package_too(tmp_path: Path) -> None:
    file = tmp_path / "words.txt"
    file.write_text("Ramipril\nMorbus Parkinson\n\n", encoding="utf-8")
    package = read_package(file)
    assert [(c.code, c.display) for c in package.concepts] == [
        ("Morbus Parkinson", "Morbus Parkinson"), ("Ramipril", "Ramipril")]


def test_an_unknown_ending_is_refused(tmp_path: Path) -> None:
    with pytest.raises(PackageError):
        read_package(tmp_path / "terms.xml")


def test_writing_is_stable() -> None:
    assert to_codesystem(sample()) == to_codesystem(from_codesystem(to_codesystem(sample())))
