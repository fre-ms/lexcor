# SPDX-FileCopyrightText: 2026 fre.ms
# SPDX-License-Identifier: MIT
"""Supplements: further wordings for the codes of another code system."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from lexcor import (
    Concept,
    Fit,
    Package,
    PackageError,
    Store,
    StoreError,
    Term,
    entries_of,
    open_store,
    read_fhir_package,
    supplemented,
    tag,
    tidy,
)
from lexcor.cli import main
from lexcor.package import from_codesystem, read_csv, to_codesystem

BASE = "https://example.org/CodeSystem/sample"
INDEX = "https://example.org/CodeSystem/sample-index"


def base(version: str = "2026") -> Package:
    return Package(url=BASE, version=version, title="Sample", language="de", concepts=[
        Concept("D1", "Bluthochdruck", "disorder", "", [Term("Hypertonie", "de")]),
        Concept("M1", "Ramipril", "drug"),
    ])


def index(named: str = BASE) -> Package:
    """Everyday wordings for the codes of the sample; D9 is a code the sample does not have."""
    return Package(url=INDEX, version="7", title="Sample index", language="de",
                   supplements=named, concepts=[
                       Concept("D1", terms=[Term("Hochdruckleiden", "de"),
                                            Term("Bluthochdruck", "de")]),
                       Concept("D9", terms=[Term("Zuckerleiden", "de")]),
                   ])


def coded(store: Store, text: str) -> list[tuple[str, list[tuple[str, str, str, str, str]]]]:
    return [(m.text, [(a.system, a.version, a.code, a.display, a.cls) for a in m.assignments])
            for m in tag(text, store)]


def test_a_supplement_travels_as_a_fhir_codesystem() -> None:
    resource = to_codesystem(index(f"{BASE}|2026"))
    assert (resource["content"], resource["supplements"]) == ("supplement", f"{BASE}|2026")
    assert resource["concept"][0] == {"code": "D1", "designation": [
        {"language": "de", "value": "Bluthochdruck"},
        {"language": "de", "value": "Hochdruckleiden"}]}  # no name is made up for the code
    read = from_codesystem(json.loads(json.dumps(resource)))
    assert read == tidy(index(f"{BASE}|2026"))
    assert supplemented(read) == (BASE, "2026") and supplemented(base()) == ("", "")
    with pytest.raises(PackageError) as refused:
        from_codesystem({"resourceType": "CodeSystem", "url": INDEX, "content": "supplement",
                         "concept": [{"code": "D1"}]})
    assert refused.value.kind == "supplement"


def test_the_terms_of_a_supplement_code_in_the_system_it_supplements(tmp_path: Path) -> None:
    with open_store(tmp_path / "terms.sqlite") as store:
        store.add_package(base())
        info = store.add_package(index())
        assert (info.url, info.supplements, info.concepts, info.terms) == (INDEX, BASE, 2, 3)
        assert store.package(INDEX) == tidy(index())
        # the code has the name, the class and the version its own system gives it
        assert coded(store, "Hochdruckleiden seit Jahren.") == [
            ("Hochdruckleiden", [(BASE, "2026", "D1", "Bluthochdruck", "disorder")])]
        # the system and its supplement answer as one: one assignment for the code
        assert coded(store, "Bluthochdruck") == [
            ("Bluthochdruck", [(BASE, "2026", "D1", "Bluthochdruck", "disorder")])]
        # a code the system held does not have is given as the supplement names it
        assert coded(store, "Zuckerleiden") == [("Zuckerleiden", [(BASE, "", "D9", "", "")])]
        # a supplement is a package: it is chosen by its own URL and goes on its own
        assert sorted(e.term for e in store.entries(packages=[BASE])) == [
            "Bluthochdruck", "Hypertonie", "Ramipril"]
        assert sorted(e.term for e in store.entries(packages=[INDEX])) == [
            "Bluthochdruck", "Hochdruckleiden", "Zuckerleiden"]
        assert sorted(e.term for e in store.entries(classes=["drug"])) == ["Ramipril"]
        store.add_package(base("2027"))  # a new version of the system leaves the supplement
        assert coded(store, "Hochdruckleiden")[0][1][0][1] == "2027"
        store.remove_package(INDEX)
        assert coded(store, "Hochdruckleiden und Hypertonie") == [
            ("Hypertonie", [(BASE, "2027", "D1", "Bluthochdruck", "disorder")])]


def test_a_supplement_works_without_the_system_it_supplements(tmp_path: Path) -> None:
    with open_store(tmp_path / "terms.sqlite") as store:
        store.add_package(index(f"{BASE}|2027"))
        assert store.fit(INDEX) == Fit(BASE, "2027", None, 0)
        assert coded(store, "Hochdruckleiden") == [
            ("Hochdruckleiden", [(BASE, "2027", "D1", "", "")])]


def test_a_store_says_how_a_supplement_fits(tmp_path: Path) -> None:
    with open_store(tmp_path / "terms.sqlite") as store:
        store.add_package(base("2026"))
        store.add_package(index(f"{BASE}|2027"))
        assert store.fit(INDEX) == Fit(BASE, "2027", "2026", 1)  # D9 is unknown to the sample
        assert store.fit(BASE) is None
        with pytest.raises(StoreError):
            store.fit("https://example.org/nowhere")


def test_the_overlay_of_the_system_holds_for_its_supplements(tmp_path: Path) -> None:
    with open_store(tmp_path / "terms.sqlite") as store:
        store.add_package(base())
        store.add_package(index())
        store.hide(BASE, "D1", "Hochdruckleiden")
        assert coded(store, "Hochdruckleiden") == []
        store.add_term(BASE, "D9", Term("Honigharnruhr", "de"))  # a code only the index names
        assert coded(store, "Honigharnruhr") == [("Honigharnruhr", [(BASE, "", "D9", "", "")])]
        store.set_class(BASE, "D9", "disorder")
        assert [a.cls for m in tag("Zuckerleiden", store) for a in m.assignments] == ["disorder"]
        store.hide(BASE, "D9")
        assert coded(store, "Zuckerleiden und Honigharnruhr") == []
        with pytest.raises(StoreError) as unknown:
            store.hide(BASE, "D404")
        assert unknown.value.kind == "no-concept"


def test_supplements_are_recognised_without_any_store() -> None:
    found = {(e.term, e.system, e.version, e.code, e.display, e.cls)
             for e in entries_of([index(), base()])}
    assert ("Hochdruckleiden", BASE, "2026", "D1", "Bluthochdruck", "disorder") in found
    assert ("Zuckerleiden", BASE, "", "D9", "", "") in found
    assert {e.term for e in entries_of([index(), base()], classes=["disorder"])} == {
        "Bluthochdruck", "Hochdruckleiden", "Hypertonie"}
    assert {e.system for e in entries_of([index()])} == {BASE}


def test_a_list_made_by_hand_can_be_a_supplement(tmp_path: Path,
                                                 capsys: pytest.CaptureFixture[str]) -> None:
    def run(*arguments: str) -> int:
        return main(["--store", str(tmp_path / "store.sqlite"), "--language", "en", *arguments])

    (tmp_path / "Sample.json").write_text(json.dumps(to_codesystem(base())), encoding="utf-8")
    (tmp_path / "Own wordings.csv").write_text(
        "term,code\nHochdruckleiden,D1\nZuckerleiden,D9\n", encoding="utf-8")
    (tmp_path / "letter.txt").write_text("Hochdruckleiden", encoding="utf-8")
    assert run("packages", "import", str(tmp_path / "Own wordings.csv"), "--supplements", BASE,
               "--term-language", "de") == 0
    said = capsys.readouterr().out
    assert f"Supplements {BASE}." in said and "does not hold that code system" in said
    assert run("packages", "import", str(tmp_path / "Sample.json")) == 0
    capsys.readouterr()
    assert run("--json", "packages", "import", str(tmp_path / "Own wordings.csv"),
               "--supplements", f"{BASE}|2027", "--term-language", "de") == 0
    answer = json.loads(capsys.readouterr().out)
    assert answer["package"]["supplements"] == f"{BASE}|2027"
    assert answer["package"]["url"].startswith("urn:uuid:")  # the store's, as for any list
    assert answer["fit"] == {"system": BASE, "wanted": "2027", "held": "2026", "unknown": 1}
    assert run("packages", "import", str(tmp_path / "Own wordings.csv"), "--supplements",
               f"{BASE}|2027") == 0
    said = capsys.readouterr().out
    assert "names version 2027, the store holds 2026" in said
    assert "1 codes of the supplement are unknown" in said
    assert run("packages", "list") == 0
    assert f"(supplements {BASE}|2027)" in capsys.readouterr().out
    assert run("--json", "tag", str(tmp_path / "letter.txt")) == 0
    mention, = json.loads(capsys.readouterr().out)["mentions"]
    assert [(a["system"], a["code"], a["display"]) for a in mention["assignments"]] == [
        (BASE, "D1", "Bluthochdruck")]

    (tmp_path / "No code.csv").write_text("term,code\nHochdruckleiden,\n", encoding="utf-8")
    with pytest.raises(PackageError) as refused:
        read_csv(tmp_path / "No code.csv", supplements=BASE)
    assert refused.value.kind == "no-code"


def test_a_fhir_package_brings_its_supplements(tmp_path: Path,
                                               capsys: pytest.CaptureFixture[str]) -> None:
    import io
    import tarfile

    file = tmp_path / "example-1.0.0.tgz"
    with tarfile.open(file, "w:gz") as archive:
        for name, resource in (("package/CodeSystem-index.json", to_codesystem(index())),
                               ("package/CodeSystem-sample.json", to_codesystem(base()))):
            data = json.dumps(resource).encode("utf-8")
            member = tarfile.TarInfo(name)
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
    found, skipped = read_fhir_package(file)
    assert [(p.url, p.supplements) for p in found] == [(BASE, ""), (INDEX, BASE)] and not skipped
    assert main(["--store", str(tmp_path / "store.sqlite"), "--json", "packages", "import",
                 str(file)]) == 0
    answer = json.loads(capsys.readouterr().out)
    assert answer["fit"] == {INDEX: {"system": BASE, "wanted": "", "held": "2026", "unknown": 1}}


def test_a_store_from_before_supplements_is_read_and_brought_up(tmp_path: Path) -> None:
    file = tmp_path / "terms.sqlite"
    with open_store(file) as store:
        store.add_package(base())
    connection = sqlite3.connect(file)  # the store as it was written before
    connection.execute("ALTER TABLE package DROP COLUMN supplements")
    connection.commit()
    connection.close()
    with open_store(file, readonly=True) as store:
        assert [p.supplements for p in store.packages()] == [""]
        assert store.package(BASE) == tidy(base()) and store.fit(BASE) is None
        assert coded(store, "Hypertonie") == [
            ("Hypertonie", [(BASE, "2026", "D1", "Bluthochdruck", "disorder")])]
    with open_store(file) as store:
        store.add_package(index())
        assert coded(store, "Hochdruckleiden")[0][1][0][3] == "Bluthochdruck"
