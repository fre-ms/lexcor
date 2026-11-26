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


def test_messages_come_in_both_languages(files: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(files, "--language", "de", "packages", "list") == 0
    assert capsys.readouterr().out.strip() == "Die Ablage enthält kein Paket."
    assert run(files, "--language", "en", "packages", "list") == 0
    assert capsys.readouterr().out.strip() == "The store holds no package."
    assert run(files, "--language", "en", "--json", "packages", "remove", "urn:x:none") == 1
    assert json.loads(capsys.readouterr().out) == {"error": "No package with the URL urn:x:none."}
