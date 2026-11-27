# SPDX-FileCopyrightText: 2026 fre.ms
# SPDX-License-Identifier: MIT
"""The store: packages, what recognition reads, and the overlay."""

from __future__ import annotations

from pathlib import Path

import pytest

from lexcor import (
    Change,
    Concept,
    Package,
    Recogniser,
    Store,
    StoreError,
    Term,
    cover,
    entries_of,
    open_store,
    recogniser,
    tidy,
)

URL = "https://example.org/CodeSystem/sample"


def sample(version: str = "1") -> Package:
    return Package(url=URL, version=version, title="Sample", redistributable=True, concepts=[
        Concept("D1", "Bluthochdruck", "disorder", "", [
            Term("Bluthochdruck", "de", "preferred"), Term("Hypertonie", "de")]),
        Concept("M1", "Ramipril", "drug", "", [Term("Ramipril", "de", "preferred")]),
    ])


@pytest.fixture
def store(tmp_path: Path) -> Store:
    with open_store(tmp_path / "terms.sqlite") as opened:
        opened.add_package(sample())
        yield opened


def terms(store: Store, **choice: object) -> list[tuple[str, str, str]]:
    return [(e.term, e.code, e.cls) for e in store.entries(**choice)]  # type: ignore[arg-type]


def test_a_package_goes_in_and_comes_out_as_it_was(store: Store) -> None:
    info = store.info(URL)
    assert (info.version, info.concepts, info.terms, info.redistributable) == ("1", 2, 3, True)
    assert store.package(URL) == tidy(sample())
    assert terms(store) == [("Bluthochdruck", "D1", "disorder"), ("Hypertonie", "D1", "disorder"),
                            ("Ramipril", "M1", "drug")]


def test_packages_and_classes_narrow_what_recognition_reads(store: Store) -> None:
    assert terms(store, classes=["drug"]) == [("Ramipril", "M1", "drug")]
    assert terms(store, packages=["https://example.org/other"]) == []


def test_every_change_raises_the_version(store: Store) -> None:
    before = store.version()
    store.hide(URL, "D1", "Hypertonie", "de")
    assert store.version() == before + 1
    store.remove_package(URL)
    assert store.version() == before + 2 and store.packages() == []
    assert store.remove_package(URL) is False and store.version() == before + 2


def test_the_overlay_hides_adds_and_moves(store: Store) -> None:
    store.hide(URL, "D1", "Hypertonie", "de")
    store.add_term(URL, "D1", Term("Hochdruck", "de"))
    store.set_class(URL, "M1", "substance")
    store.set_class(URL, "M1", "medication")
    assert terms(store) == [("Hochdruck", "D1", "disorder"), ("Bluthochdruck", "D1", "disorder"),
                            ("Ramipril", "M1", "medication")]
    store.hide(URL, "M1")
    assert [code for _term, code, _cls in terms(store)] == ["D1", "D1"]
    assert store.package(URL) == tidy(sample())  # the package itself is untouched


def test_a_new_version_replaces_the_package_and_keeps_the_overlay(store: Store) -> None:
    store.hide(URL, "D1", "Hypertonie", "de")
    newer = sample("2")
    newer.concepts[0].terms.append(Term("Hypertension", "de"))
    store.add_package(newer)
    assert store.info(URL).version == "2" and len(store.packages()) == 1
    assert [t for t, _code, _cls in terms(store)] == ["Bluthochdruck", "Hypertension", "Ramipril"]


def test_a_change_can_be_taken_back(store: Store) -> None:
    store.hide(URL, "D1", "Hypertonie", "de")
    assert store.undo(Change(URL, "D1", "hide", "Hypertonie", "de")) is True
    assert store.undo(Change(URL, "D1", "hide", "Hypertonie", "de")) is False
    assert len(terms(store)) == 3


def test_a_change_needs_its_concept(store: Store) -> None:
    with pytest.raises(StoreError):
        store.hide(URL, "X9")
    with pytest.raises(StoreError):
        store.add_term(URL, "D1", Term("  "))


def test_an_overlay_travels_as_a_file(store: Store, tmp_path: Path) -> None:
    store.hide(URL, "D1", "Hypertonie", "de")
    store.add_term(URL, "M1", Term("Delix", "de", "synonym"))
    file = tmp_path / "overlay.json"
    assert store.export_overlay(file) == 2
    with open_store(tmp_path / "other.sqlite") as other:
        assert other.import_overlay(file) == 2  # in force once the package comes
        other.add_package(sample())
        assert terms(other) == terms(store)
    (tmp_path / "wrong.json").write_text("{}", encoding="utf-8")
    with pytest.raises(StoreError):
        store.import_overlay(tmp_path / "wrong.json")


def test_the_store_of_a_library_lies_in_the_data_folder(tmp_path: Path,
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LEXCOR_DATA", str(tmp_path / "data"))
    with open_store(library="lexterm") as opened:
        assert opened.path == tmp_path / "data" / "lexterm.sqlite" and opened.path.exists()


def test_a_list_without_a_uri_gets_one_of_the_store(tmp_path: Path) -> None:
    def words(*values: str) -> Package:
        return Package(url="", name="Eigene Liste", title="Eigene Liste",
                       concepts=[Concept(v, v) for v in values])

    with open_store(tmp_path / "one.sqlite") as one, open_store(tmp_path / "two.sqlite") as two:
        first = one.add_package(words("Ramipril"))
        assert first.url.startswith("urn:uuid:") and "lexcor" not in first.url
        again = one.add_package(words("Ramipril", "Bisoprolol"))  # read again: replaces itself
        assert again.url == first.url and [p.concepts for p in one.packages()] == [2]
        assert two.add_package(words("Ramipril")).url != first.url  # another keeper, another URI
        assert one.find("eigene liste") == one.find(first.url) == first.url
        with pytest.raises(StoreError):
            one.find("Fremde Liste")
        one.add_package(Package(url="urn:x:other", title="Eigene Liste"))
        with pytest.raises(StoreError):  # two packages of one name: the URL decides
            one.find("Eigene Liste")
        with pytest.raises(StoreError):
            one.add_package(Package(url=""))


def test_a_store_opened_for_reading_writes_nothing(tmp_path: Path) -> None:
    file = tmp_path / "inside a program" / "terms.sqlite"
    with open_store(file) as written:
        written.add_package(sample())
    before = sorted(p.name for p in file.parent.iterdir())
    with open_store(file, readonly=True) as store:
        assert [e.term for e in store.entries(classes=["drug"])] == ["Ramipril"]
        assert store.find("sample") == URL and store.version() == 1
        assert [m.text for m in recogniser(store).tag("Ramipril und Hypertonie")] == [
            "Ramipril", "Hypertonie"]
        for change in (lambda: store.hide(URL, "D1"), lambda: store.add_package(sample("2")),
                       lambda: store.remove_package(URL),
                       lambda: store.add_package(Package(url="", name="Liste"))):
            with pytest.raises(StoreError) as refused:
                change()
            assert refused.value.kind == "read-only"
    assert sorted(p.name for p in file.parent.iterdir()) == before  # no journal, no new file
    with pytest.raises(StoreError) as missing:
        open_store(tmp_path / "nowhere" / "terms.sqlite", readonly=True)
    assert missing.value.kind == "no-store" and not (tmp_path / "nowhere").exists()


def test_stores_in_memory_are_kept_apart() -> None:
    with Store(":memory:") as one, Store(":memory:") as two:
        one.add_package(sample())
        two.add_package(Package(url=URL, concepts=[Concept("X", "Bisoprolol")]))
        assert one.key != two.key and one.version() == two.version()
        assert recogniser(one).tag("Bisoprolol") == []
        assert [m.text for m in recogniser(two).tag("Bisoprolol")] == ["Bisoprolol"]
        assert cover(one).is_term("Ramipril") and not cover(two).is_term("Ramipril")
    assert not Path(":memory:").exists()


def test_a_store_is_opened_in_one_thread_and_used_in_another(tmp_path: Path) -> None:
    import threading

    found: list[str] = []
    with open_store(tmp_path / "terms.sqlite") as store:
        store.add_package(sample())
        worker = threading.Thread(target=lambda: found.extend(e.term for e in store.entries()))
        worker.start()
        worker.join()
    assert found == ["Bluthochdruck", "Hypertonie", "Ramipril"]


def test_packages_are_recognised_without_any_store() -> None:
    entries = list(entries_of([sample()]))
    assert [(e.term, e.system, e.version, e.code, e.cls) for e in entries] == [
        ("Bluthochdruck", URL, "1", "D1", "disorder"), ("Hypertonie", URL, "1", "D1", "disorder"),
        ("Ramipril", URL, "1", "M1", "drug")]
    assert [e.term for e in entries_of([sample()], classes=["drug"])] == ["Ramipril"]
    assert [e.term for e in entries_of([sample()], languages=["en"])] == []
    mention, = Recogniser(entries).tag("Bekannte Hypertonien.")
    assert (mention.text, mention.assignments[0].code) == ("Hypertonien", "D1")


def test_an_error_has_a_kind_and_both_wordings(store: Store) -> None:
    with pytest.raises(StoreError) as caught:
        store.find("Fremdes Paket")
    error = caught.value
    assert error.kind == "no-package"
    assert error.message("de").startswith("Kein Paket") and error.message("en").startswith("No ")
    assert str(error) in (error.message("de"), error.message("en"))


def test_the_store_keeps_the_properties_of_a_concept(tmp_path: Path) -> None:
    package = Package(url=URL, concepts=[
        Concept("H1", "Waldklinik", "hospital", "", [], {"town": "Tannengrund", "beds": "120"}),
        Concept("H2", "Seeklinik", "hospital")])
    with open_store(tmp_path / "terms.sqlite") as store:
        store.add_package(package)
        assert store.properties(URL, "H1") == {"beds": "120", "town": "Tannengrund"}
        assert store.properties(URL, "H2") == {} and store.properties(URL, "none") == {}
        assert store.package(URL) == tidy(package)
        store.connection.execute("DROP TABLE property")  # a store of before, read only
        store.connection.commit()
    with open_store(tmp_path / "terms.sqlite", readonly=True) as old:
        assert old.properties(URL, "H1") == {}
        assert [c.display for c in old.package(URL).concepts] == ["Waldklinik", "Seeklinik"]


def test_a_language_with_a_region_is_the_language_it_is_a_variety_of(tmp_path: Path) -> None:
    """Publishers write "de-DE" where a caller asks for "de"."""
    package = Package(url=URL, language="de-DE", concepts=[
        Concept("I10", "Hypertonie"), Concept("X", "high blood pressure", "", "",
                                              [Term("high blood pressure", "en-GB")])])
    with open_store(tmp_path / "terms.sqlite") as store:
        store.add_package(package)
        assert [e.term for e in store.entries(languages=["de"])] == ["Hypertonie"]
        assert [e.term for e in store.entries(languages=["DE-de"])] == ["Hypertonie"]
        assert [e.term for e in store.entries(languages=["en"])] == ["high blood pressure"]
        assert [e.language for e in store.entries()] == ["de-DE", "en-GB"]  # kept as written
    assert [e.term for e in entries_of([package], languages=["de"])] == ["Hypertonie"]
