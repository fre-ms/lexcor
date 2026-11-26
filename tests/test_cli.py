# SPDX-FileCopyrightText: 2026 fre.ms
# SPDX-License-Identifier: MIT
"""The command line: packages in and out, and tagging, in both languages."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from lexcor import set_language
from lexcor.cli import main


@pytest.fixture(autouse=True)
def default_language() -> Iterator[None]:
    yield
    set_language(None)


@pytest.fixture
def files(tmp_path: Path) -> Path:
    (tmp_path / "list.csv").write_text(
        "term,code,display,class\nBluthochdruck,D1,Bluthochdruck,disorder\nHypertonie,D1,,\n",
        encoding="utf-8")
    (tmp_path / "letter.txt").write_text("Bekannte arterielle Hypertonien.", encoding="utf-8")
    return tmp_path


def run(folder: Path, *arguments: str) -> int:
    return main(["--store", str(folder / "store.sqlite"), *arguments])


def test_a_list_goes_in_is_used_and_comes_out(files: Path,
                                              capsys: pytest.CaptureFixture[str]) -> None:
    url = "https://example.org/list"
    assert run(files, "--json", "packages", "import", str(files / "list.csv"), "--url", url,
               "--redistributable") == 0
    taken = json.loads(capsys.readouterr().out)["package"]
    assert (taken["url"], taken["concepts"], taken["terms"]) == (url, 1, 2)

    assert run(files, "--json", "tag", str(files / "letter.txt")) == 0
    mention, = json.loads(capsys.readouterr().out)["mentions"]
    assert (mention["start"], mention["end"], mention["exact"]) == (20, 31, "Hypertonien")
    assert mention["assignments"] == [{
        "system": url, "version": "", "code": "D1", "display": "Bluthochdruck",
        "class": "disorder", "term": "Hypertonie", "match": {"kind": "inflected"}}]
    assert run(files, "--json", "tag", str(files / "letter.txt"), "--class", "drug") == 0
    assert json.loads(capsys.readouterr().out) == {"mentions": []}

    assert run(files, "packages", "export", url, str(files / "out.json")) == 0
    assert json.loads((files / "out.json").read_text(encoding="utf-8"))["url"] == url
    assert run(files, "packages", "remove", url) == 0
    capsys.readouterr()
    assert run(files, "--json", "packages", "list") == 0
    assert json.loads(capsys.readouterr().out) == {"packages": []}


def test_a_package_that_may_not_be_passed_on_is_exported_for_own_use_only(
        files: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(files, "packages", "import", str(files / "list.csv"), "--url", "urn:x:own") == 0
    assert run(files, "--language", "en", "packages", "export", "urn:x:own",
               str(files / "out.csv")) == 1
    assert "may not be passed on" in capsys.readouterr().err
    assert not (files / "out.csv").exists()
    assert run(files, "packages", "export", "urn:x:own", str(files / "out.csv"), "--own-use") == 0
    assert (files / "out.csv").read_text(encoding="utf-8").startswith("term,code,display")


def test_a_list_without_a_uri_is_named_by_its_title(files: Path,
                                                    capsys: pytest.CaptureFixture[str]) -> None:
    assert run(files, "--json", "packages", "import", str(files / "list.csv"),
               "--redistributable") == 0
    url = json.loads(capsys.readouterr().out)["package"]["url"]
    assert url.startswith("urn:uuid:")
    assert run(files, "--json", "tag", str(files / "letter.txt"), "--package", "list") == 0
    assert [m["exact"] for m in json.loads(capsys.readouterr().out)["mentions"]] == ["Hypertonien"]
    assert run(files, "packages", "export", "list", str(files / "out.json")) == 0
    assert json.loads((files / "out.json").read_text(encoding="utf-8"))["url"] == url
    assert run(files, "--language", "en", "packages", "remove", "other") == 1
    assert "No package" in capsys.readouterr().err
    assert run(files, "--json", "packages", "remove", "list") == 0
    assert json.loads(capsys.readouterr().out) == {"removed": url}


def test_messages_come_in_both_languages(files: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(files, "--language", "de", "packages", "list") == 0
    assert capsys.readouterr().out.strip() == "Die Ablage enthält kein Paket."
    assert run(files, "--language", "en", "packages", "list") == 0
    assert capsys.readouterr().out.strip() == "The store holds no package."
    assert run(files, "--language", "en", "--json", "packages", "remove", "urn:x:none") == 1
    assert json.loads(capsys.readouterr().out) == {"error": "no-package", "message": {
        "de": "Kein Paket mit der URL oder dem Namen urn:x:none.",
        "en": "No package with the URL or the name urn:x:none."}}
    assert run(files, "--json", "tag", str(files / "missing.txt")) == 1
    assert json.loads(capsys.readouterr().out)["error"] == "unreadable"



def test_a_library_brings_its_name_its_store_its_spellings_or_its_tagger(
        files: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    from lexcor import Mention

    monkeypatch.setenv("LEXCOR_DATA", str(files / "data"))
    (files / "c.csv").write_text("term,code\nColitis,K51\n", encoding="utf-8")
    (files / "k.txt").write_text("Bekannte Kolitis.", encoding="utf-8")
    assert main(["packages", "import", str(files / "c.csv")], program="lexsample",
                library="lexsample") == 0
    assert (files / "data" / "lexsample.sqlite").exists()
    capsys.readouterr()
    assert main(["--json", "tag", str(files / "k.txt")], library="lexsample") == 0
    assert json.loads(capsys.readouterr().out) == {"mentions": []}
    assert main(["--json", "tag", str(files / "k.txt")], library="lexsample",
                variants=(("c", "k"),)) == 0
    mention, = json.loads(capsys.readouterr().out)["mentions"]
    assert (mention["exact"], mention["assignments"][0]["match"]) == (
        "Kolitis", {"kind": "spelling variant"})
    assert main(["--json", "tag", str(files / "k.txt")], library="lexsample",
                tagger=lambda text, store, packages, classes, nested: [
                    Mention(0, 8, text[:8], ())]) == 0
    assert json.loads(capsys.readouterr().out)["mentions"] == [
        {"start": 0, "end": 8, "exact": "Bekannte", "assignments": []}]
    with pytest.raises(SystemExit):
        main(["--help"], program="lexsample")
    assert "usage: lexsample" in capsys.readouterr().out


def test_tagging_writes_the_annotation_document_and_keeps_decisions(
        files: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from lexcor import load_annotation

    url, target = "https://example.org/list", files / "letter.annotation.json"
    assert run(files, "packages", "import", str(files / "list.csv"), "--url", url) == 0
    assert run(files, "tag", str(files / "letter.txt"), "--annotation", str(target)) == 0
    first = load_annotation(target)
    assert first.made_with.startswith("lexcor ") and first.settings == {
        "classes": [], "nested": False, "packages": []}
    assert [p["url"] for p in first.packages] == [url]
    assert [(n.id, n.exact, a.code, a.status) for n, a in first.assignments(None)] == [
        ("m1", "Hypertonien", "D1", "proposed")]
    first.reject("m1")
    first.save(target)
    before = target.read_bytes()
    assert run(files, "tag", str(files / "letter.txt"), "-o", str(target)) == 0
    assert target.read_bytes() == before  # the same run again: the same bytes, still rejected
    assert main(["--store", str(files / "store.sqlite"), "tag", str(files / "letter.txt"),
                 "-o", str(files / "other.json")], program="lexsample", version="7.1") == 0
    assert load_annotation(files / "other.json").made_with == "lexsample 7.1"
    capsys.readouterr()
