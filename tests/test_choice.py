# SPDX-FileCopyrightText: 2026 fre.ms
# SPDX-License-Identifier: MIT
"""Every run chooses its packages, and the annotation document records exactly what it
applied: so that a run can be repeated, or says why it cannot."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lexcor import (
    Concept,
    Package,
    Store,
    Term,
    annotate,
    differences,
    entries_of,
    listed,
    load_annotation,
    open_store,
    packages_of,
    tag,
    write_codesystem,
)
from lexcor.annotation import EARLIER, NAMES
from lexcor.cli import main

TREE = "https://example.org/CodeSystem/tree"
INDEX = "https://example.org/CodeSystem/tree-index"
FLAT = "https://example.org/CodeSystem/flat"


def tree(version: str = "1") -> Package:
    """A code system with a hierarchy: a group, a category in it, and two codes below."""
    return Package(url=TREE, version=version, title="Tree", language="de", concepts=[
        Concept("G", "Musterkrankheiten"),
        Concept("G1", "Musterkrankheit", "", "G", [Term("Musterleiden", "de")]),
        Concept("G1.0", "Musterkrankheit, früh", "", "G1"),
        Concept("G1.9", "Musterkrankheit, nicht näher bezeichnet", "", "G1",
                [Term("Musterleiden ohne Angabe", "de")]),
    ])


def index() -> Package:
    return Package(url=INDEX, version="2", title="Tree index", language="de",
                   supplements=TREE, concepts=[
                       Concept("G1.9", terms=[Term("Musterübel", "de")]),
                       Concept("G", terms=[Term("Musterkrankheiten", "de")])])


def flat() -> Package:
    return Package(url=FLAT, version="7", title="Flat", language="de", concepts=[
        Concept("F1", "Musterleiden")])


@pytest.fixture
def store(tmp_path: Path) -> Store:
    with open_store(tmp_path / "terms.sqlite") as opened:
        for package in (tree(), index(), flat()):
            opened.add_package(package)
        yield opened


def by_code(mentions: list[object]) -> dict[str, bool | None]:
    return {a.code: a.terminal for m in mentions for a in m.assignments}  # type: ignore[attr-defined]


def test_an_assignment_says_whether_its_code_is_terminal(store: Store) -> None:
    text = "Musterleiden, Musterkrankheiten, Musterübel"
    assert by_code(tag(text, store)) == {
        "G1": False, "F1": None,  # a code system without a hierarchy does not say
        "G": False, "G1.9": True}  # the supplement's codes are placed in the tree
    assert by_code(tag(text, store, [FLAT])) == {"F1": None}
    found = {(e.code, e.terminal) for e in entries_of([index(), tree(), flat()])}
    assert {("G", False), ("G1", False), ("G1.0", True), ("G1.9", True), ("F1", None)} <= found


def test_an_assignment_names_the_package_its_term_is_in(store: Store) -> None:
    mention, = tag("Musterübel", store)
    assignment, = mention.assignments
    assert (assignment.system, assignment.version, assignment.package,
            assignment.package_version) == (TREE, "1", INDEX, "2")
    mention, = tag("Musterleiden ohne Angabe", store, [TREE])
    assert mention.assignments[0].package == ""  # the code system's own term


def test_a_document_names_what_the_run_applied(store: Store) -> None:
    listing = packages_of(store, [INDEX])  # the tree is read for its names only
    assert [(p["url"], p.get("use", ""), p.get("supplements", "")) for p in listing] == [
        (TREE, NAMES, ""), (INDEX, "", TREE)]
    assert listing[1]["title"] == "Tree index" and "overlay" not in listing[1]
    store.hide(TREE, "G1.9", "Musterübel")
    changed = packages_of(store, [INDEX])
    assert changed[0]["overlay"] == changed[1]["overlay"] != ""  # the tree's changes hold
    assert differences(listing, changed) == [(TREE, "overlay"), (INDEX, "overlay")]
    store.undo(store.overlay()[0])
    assert packages_of(store, [INDEX]) == listing  # the same changes, the same fingerprint
    assert listed([flat()]) == [{"url": FLAT, "version": "7", "sha256": flat().sha256(),
                                 "title": "Flat"}]


def test_a_change_named_by_a_supplement_is_kept_for_its_code_system(store: Store) -> None:
    store.hide(INDEX, "G1.9", "Musterübel")
    assert [(c.url, c.code) for c in store.overlay()] == [(TREE, "G1.9")]
    assert tag("Musterübel", store) == []


def test_differences_say_what_stands_in_the_way_of_a_repetition() -> None:
    then = [{"url": "a", "version": "1", "sha256": "x"}, {"url": "b", "version": "1",
                                                          "sha256": "y"},
            {"url": "c", "version": "0", "sha256": "z", "use": EARLIER}]
    assert differences(then, then) == []
    now = [{"url": "a", "version": "2", "sha256": "w"}, {"url": "d", "version": "1",
                                                         "sha256": "v"}]
    assert differences(then, now) == [("a", "version"), ("b", "missing"), ("d", "added")]
    assert differences(then[:1], [{"url": "a", "version": "1", "sha256": "q"}]) == [
        ("a", "sha256")]


def test_the_document_keeps_the_packages_decisions_come_from(store: Store) -> None:
    text = "Musterleiden und Musterübel"
    document = annotate(text, tag(text, store), "r", "r", packages_of(store), {})
    flat_mention = next(n for n in document.mentions
                        if any(a.system == FLAT for a in n.assignments))
    document.confirm(flat_mention.id, FLAT)
    # the next run leaves the flat package out: its proposals go, the decision stays, and so
    # does the record of the package it came from
    document.update(text, tag(text, store, [TREE, INDEX]), "r", "r",
                    packages_of(store, [TREE, INDEX]), {"packages": [TREE, INDEX]})
    assert [(p["url"], p.get("use", "")) for p in document.packages] == [
        (FLAT, EARLIER), (TREE, ""), (INDEX, "")]
    assert any(a.system == FLAT and a.status == "confirmed"
               for n in document.mentions for a in n.assignments)
    data = json.loads(document.dumps())
    supplement = next(a for m in data["mentions"] for a in m["assignments"]
                      if a["code"] == "G1.9")
    assert supplement["terminal"] is True
    assert supplement["origin"] == {"by": "machine", "recogniser": "r", "package": INDEX,
                                    "package_version": "2"}
    assert load_annotation_from(document.dumps()).dumps() == document.dumps()
    document.reopen(flat_mention.id, FLAT)  # nothing comes from it any more: it goes
    document.update(text, tag(text, store, [TREE]), "r", "r", packages_of(store, [TREE]), {})
    assert [p["url"] for p in document.packages] == [TREE]


def load_annotation_from(text: str) -> object:
    from lexcor.annotation import loads

    return loads(text)


@pytest.fixture
def files(tmp_path: Path) -> Path:
    write_codesystem(tree(), tmp_path / "tree.json")
    write_codesystem(index(), tmp_path / "index.json")
    write_codesystem(flat(), tmp_path / "flat.json")
    (tmp_path / "letter.txt").write_text("Musterleiden, dazu Musterübel.", encoding="utf-8")
    for name in ("tree", "index", "flat"):
        assert run(tmp_path, "packages", "import", str(tmp_path / f"{name}.json")) == 0
    return tmp_path


def run(folder: Path, *arguments: str) -> int:
    return main(["--store", str(folder / "store.sqlite"), "--language", "en", *arguments])


def systems(capsys: pytest.CaptureFixture[str]) -> set[str]:
    return {a["system"] for m in json.loads(capsys.readouterr().out)["mentions"]
            for a in m["assignments"]}


def test_every_run_chooses_its_packages(files: Path, capsys: pytest.CaptureFixture[str]) -> None:
    capsys.readouterr()
    letter = str(files / "letter.txt")
    assert run(files, "--json", "tag", letter) == 0
    assert systems(capsys) == {TREE, FLAT}
    assert run(files, "--json", "tag", letter, "--without", "Flat") == 0
    assert systems(capsys) == {TREE}
    assert run(files, "--json", "tag", letter, "--package", FLAT, "--package", TREE,
               "--without", TREE) == 0
    assert systems(capsys) == {FLAT}
    assert run(files, "--json", "tag", letter, "--without", "Tree", "--without",
               "Tree index", "--without", "Flat") == 1
    assert json.loads(capsys.readouterr().out)["error"] == "no-package-chosen"
    assert run(files, "tag", letter, "--package", TREE) == 0
    assert "G1 Musterkrankheit (as written, not terminal)" in capsys.readouterr().out


def test_a_run_is_repeated_exactly_as_recorded_or_refuses(
        files: Path, capsys: pytest.CaptureFixture[str]) -> None:
    letter, document = str(files / "letter.txt"), str(files / "letter.lexcor.json")
    assert run(files, "tag", letter, "--without", "Flat", "--annotation", document) == 0
    first = Path(document).read_text(encoding="utf-8")
    recorded = load_annotation(document)
    assert recorded.settings["packages"] == [TREE, INDEX]
    assert [p["url"] for p in recorded.packages] == [TREE, INDEX]
    assert run(files, "tag", letter, "--annotation", document, "--repeat") == 0
    assert Path(document).read_text(encoding="utf-8") == first  # byte for byte
    capsys.readouterr()

    assert run(files, "--json", "tag", letter, "--annotation", document, "--repeat",
               "--package", FLAT) == 1
    assert json.loads(capsys.readouterr().out)["error"] == "repeat-with-choice"
    assert run(files, "--json", "tag", letter, "--annotation", str(files / "none.json"),
               "--repeat") == 1
    assert json.loads(capsys.readouterr().out)["error"] == "no-annotation"

    with open_store(files / "store.sqlite") as store:  # the user changes a package
        store.hide(TREE, "G1", "Musterleiden")
    assert run(files, "tag", letter, "--annotation", document, "--repeat") == 1
    said = capsys.readouterr().err
    assert "cannot be repeated exactly" in said and f"{TREE} has other changes" in said
    with open_store(files / "store.sqlite") as store:
        store.undo(store.overlay()[0])
        store.add_package(tree("2"))  # a new version comes
    assert run(files, "--json", "tag", letter, "--annotation", document, "--repeat") == 1
    refused = json.loads(capsys.readouterr().out)
    assert refused["error"] == "not-repeatable"
    assert f"{TREE} hat eine andere Version" in refused["message"]["de"]
    assert Path(document).read_text(encoding="utf-8") == first  # nothing written

    assert main(["--store", str(files / "store.sqlite"), "--json", "tag", letter,
                 "--annotation", document, "--repeat"], program="lexother") == 1
    assert "Made with lexcor" in json.loads(capsys.readouterr().out)["message"]["en"]
