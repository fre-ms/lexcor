# SPDX-FileCopyrightText: 2026 fre.ms
# SPDX-License-Identifier: MIT
"""The package window: ticks for the runs that follow, taking in, writing out, taking out;
and the person's choice as the store keeps it and the command line follows it."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from lexcor import Concept, Package, Store, Term, open_store, set_language, write_codesystem
from lexcor.cli import main

TREE = "https://example.org/CodeSystem/tree"
INDEX = "https://example.org/CodeSystem/tree-index"
FLAT = "https://example.org/CodeSystem/flat"


def packages() -> list[Package]:
    return [
        Package(url=TREE, version="1", title="Tree", language="de", redistributable=True,
                concepts=[Concept("G", "Musterkrankheiten"),
                          Concept("G1", "Musterkrankheit", "", "G")]),
        Package(url=INDEX, version="2", title="Tree index", language="de", supplements=TREE,
                concepts=[Concept("G1", terms=[Term("Musterleiden", "de")])]),
        Package(url=FLAT, version="7", title="Flat", language="de", concepts=[
            Concept("F1", "Musterübel")])]


@pytest.fixture
def store(tmp_path: Path) -> Iterator[Store]:
    with open_store(tmp_path / "terms.sqlite") as opened:
        for package in packages():
            opened.add_package(package)
        yield opened


def test_the_store_keeps_a_persons_choice(store: Store) -> None:
    assert store.chosen() is None and store.switched_off() == []  # at first all
    store.switch(FLAT, False)
    assert store.chosen() == [TREE, INDEX] and store.switched_off() == [FLAT]
    store.add_package(packages()[2])  # a new version stays unticked
    assert store.switched_off() == [FLAT]
    store.choose([FLAT])
    assert store.chosen() == [FLAT]
    store.choose([TREE, INDEX, FLAT])
    assert store.chosen() is None


def test_the_command_line_ticks_and_follows_the_ticks(tmp_path: Path,
                                                      capsys: pytest.CaptureFixture[str]) -> None:
    def run(*arguments: str) -> int:
        return main(["--store", str(tmp_path / "store.sqlite"), "--language", "en", *arguments])

    for package in packages():
        write_codesystem(package, tmp_path / f"{package.title}.json")
        assert run("packages", "import", str(tmp_path / f"{package.title}.json")) == 0
    (tmp_path / "letter.txt").write_text("Musterleiden und Musterübel.", encoding="utf-8")
    capsys.readouterr()
    assert run("packages", "off", "Flat") == 0
    assert capsys.readouterr().out.strip() == f"Unticked: {FLAT}"
    assert run("--json", "packages", "list") == 0
    assert {p["url"]: p["ticked"] for p in json.loads(capsys.readouterr().out)["packages"]} == {
        TREE: True, INDEX: True, FLAT: False}
    document = tmp_path / "letter.json"
    assert run("--json", "tag", str(tmp_path / "letter.txt"), "--annotation", str(document)) == 0
    assert [m["exact"] for m in json.loads(capsys.readouterr().out)["mentions"]] == [
        "Musterleiden"]  # the unticked package is not applied, and the document says so
    assert json.loads(document.read_text(encoding="utf-8"))["settings"]["packages"] == [
        TREE, INDEX]
    assert run("--json", "tag", str(tmp_path / "letter.txt"), "--package", FLAT) == 0
    assert [m["exact"] for m in json.loads(capsys.readouterr().out)["mentions"]] == [
        "Musterübel"]  # named packages go before the ticks
    assert run("packages", "on", "Flat") == 0
    assert run("--json", "tag", str(tmp_path / "letter.txt")) == 0
    assert "Musterübel" in capsys.readouterr().out



def test_the_core_loads_no_window() -> None:
    import subprocess
    import sys

    script = ("import sys, lexcor, lexcor.cli, lexcor.store, lexcor.annotation;"
              " sys.exit('PySide6' in sys.modules)")
    assert subprocess.run([sys.executable, "-c", script], check=False).returncode == 0


qt = pytest.importorskip("PySide6.QtWidgets")


@pytest.fixture(scope="module")
def app() -> object:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    set_language("en")
    yield qt.QApplication.instance() or qt.QApplication([])
    set_language(None)


def test_the_list_shows_every_package_with_its_tick(app: object, store: Store) -> None:
    from lexcor.window import PackageList

    store.switch(FLAT, False)
    shown = PackageList(store)
    tops = [shown.topLevelItem(i) for i in range(shown.topLevelItemCount())]
    assert [item.text(0) for item in tops] == ["Flat", "Tree"]
    assert tops[1].child(0).text(0) == "Tree index"  # the supplement below its code system
    assert tops[0].text(4) == "not to be passed on"
    assert shown.chosen() == [TREE, INDEX]
    heard: list[list[str]] = []
    shown.chosen_changed.connect(heard.append)
    tops[1].child(0).setCheckState(0, qt_unchecked())
    assert heard == [[TREE]]
    shown.tick_all(True)
    assert heard[-1] == [FLAT, TREE, INDEX]


def qt_unchecked() -> object:
    from PySide6.QtCore import Qt

    return Qt.CheckState.Unchecked


def test_the_window_keeps_the_ticks_in_the_store(app: object, store: Store) -> None:
    from lexcor.window import PackageWindow

    window = PackageWindow(store)
    window.list.tick_all(False)
    assert store.chosen() == []
    window.list.tick_all(True)
    assert store.chosen() is None
    passing = PackageWindow(store, remember=False)
    passing.list.tick_all(False)
    assert store.chosen() is None  # a program that keeps the choice itself


def test_the_window_takes_in_beside_itself(app: object, store: Store, tmp_path: Path) -> None:
    from lexcor.window import PackageWindow

    extra = Package(url="https://example.org/CodeSystem/extra", version="1", title="Extra",
                    concepts=[Concept("E1", "Extraleiden")])
    write_codesystem(extra, tmp_path / "extra.json")
    window = PackageWindow(store, sources="<p><a href='https://example.org/'>Where</a></p>")
    worker = window.take_in(tmp_path / "extra.json")
    assert not window.isEnabled()
    worker.wait(10_000)
    qt.QApplication.processEvents()
    assert window.isEnabled() and "Taken in: Extra, 1 concepts in 1 wordings." in \
        window.status.text()
    assert "Extra" in [window.list.topLevelItem(i).text(0)
                       for i in range(window.list.topLevelItemCount())]
    (tmp_path / "broken.json").write_text("{", encoding="utf-8")
    worker = window.take_in(tmp_path / "broken.json")
    worker.wait(10_000)
    qt.QApplication.processEvents()
    assert window.status.text().startswith("Not taken in:") and window.isEnabled()
    assert "Where" in window.sources.toPlainText()


def test_the_window_writes_out_and_takes_out(app: object, store: Store, tmp_path: Path) -> None:
    from lexcor import StoreError
    from lexcor.window import PackageWindow

    window = PackageWindow(store)
    window.write_out(TREE, tmp_path / "tree.csv")
    assert (tmp_path / "tree.csv").read_text(encoding="utf-8").startswith("term,code")
    with pytest.raises(StoreError) as refused:
        window.write_out(FLAT, tmp_path / "flat.json")
    assert refused.value.kind == "not-redistributable"
    window.write_out(FLAT, tmp_path / "flat.json", own_use=True)
    assert json.loads((tmp_path / "flat.json").read_text(encoding="utf-8"))["url"] == FLAT
    window.remove(FLAT)
    assert [p.url for p in store.packages()] == [TREE, INDEX]
    assert window.status.text() == "Taken out: Flat"
