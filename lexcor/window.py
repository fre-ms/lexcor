# SPDX-FileCopyrightText: 2026 fre.ms
# SPDX-License-Identifier: MIT
"""The package window: the packages of a store, ticked for the runs that follow, taken in,
written out and taken out (design, section 6).

It needs Qt for Python (PySide6, its essential modules suffice), which the core does not:
``pip install "lexcor[window]"``.  A program
places :class:`PackageList` or the whole :class:`PackageWindow` among its own widgets; the
command line opens the window on its own (``lexcor packages window``).  A library hands over
the forms it reads packages from besides the core's (:class:`Reader`) and its text on where
terminologies come from.

What is ticked is the person's choice for the runs that follow.  The window keeps it in the
store (:meth:`lexcor.store.Store.choose`), so that the command line follows it too, and a
run records in its annotation document what it applied.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from lexcor.language import LexError, language, say
from lexcor.package import (
    Package,
    is_fhir_package,
    read,
    read_fhir_package,
    supplemented,
    write_codesystem,
    write_csv,
)
from lexcor.store import PackageInfo, Store, StoreError

try:
    from PySide6 import QtCore, QtGui, QtWidgets
except ImportError as missing:  # pragma: no cover - depends on what is installed
    raise LexError(
        "no-window",
        'Für das Paketfenster wird PySide6 gebraucht: pip install "lexcor[window]".',
        'The package window needs PySide6: pip install "lexcor[window]".') from missing

URL = QtCore.Qt.ItemDataRole.UserRole
WIDEST = 360  # the widest the column of names grows; a longer name is shortened, its tooltip whole
COLUMNS = (("Paket", "Package"), ("Version", "Version"), ("Begriffe", "Concepts"),
           ("Schreibweisen", "Wordings"), ("Hinweise", "Notes"))


@dataclass(frozen=True)
class Reader:
    """A form a library reads packages from besides the core's: its name for a person
    (German, English), whether a folder is read rather than a file, the patterns of its
    files, and the function that reads it into packages."""

    name: tuple[str, str]
    read: Callable[[Path], Sequence[Package]]
    folder: bool = False
    patterns: str = "*"


def _read_core(path: Path) -> list[Package]:
    """A FHIR CodeSystem, every CodeSystem of a FHIR package, or a list."""
    if is_fhir_package(path):
        return read_fhir_package(path)[0]
    return [read(path)]


CORE = Reader(("FHIR CodeSystem, FHIR-Paket oder Liste", "FHIR CodeSystem, FHIR package or list"),
              _read_core, False, "*.json *.tgz *.tar.gz *.csv *.txt")


def _number(count: int) -> str:
    """A count as a person reads it in the language in force."""
    text = f"{count:,}"
    return text.replace(",", ".") if language() == "de" else text


def take_in(store: Store, path: Path, reader: Reader = CORE) -> list[PackageInfo]:
    """Read a file or folder in a reader's form and take its packages into the store."""
    return [store.add_package(package) for package in reader.read(Path(path))]


class _Taking(QtCore.QThread):
    """Takes a file in beside the window, so that a large terminology does not stop it."""

    done = QtCore.Signal(list)
    failed = QtCore.Signal(str)

    def __init__(self, store: Store, path: Path, reader: Reader) -> None:
        super().__init__()
        self.store, self.path, self.reader = store, path, reader

    def run(self) -> None:
        """Read and take in; say how it went."""
        try:
            infos = take_in(self.store, self.path, self.reader)
        except (LexError, OSError, ValueError) as error:
            self.failed.emit(str(error))
        else:
            self.done.emit(infos)


class PackageList(QtWidgets.QTreeWidget):
    """The packages of a store, each with a box to tick for the runs that follow.  A
    supplement stands below the code system it supplements, with a box of its own."""

    chosen_changed = QtCore.Signal(list)

    def __init__(self, store: Store, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.store = store
        self._filling = False
        self.setHeaderLabels([say(*words) for words in COLUMNS])
        self.setUniformRowHeights(True)
        self.setAlternatingRowColors(True)
        self.itemChanged.connect(self._changed)
        self.refresh()

    def refresh(self, chosen: Sequence[str] | None = None) -> None:
        """Show the store's packages again, ticked as ``chosen`` says, or as the store
        keeps the person's choice."""
        infos = self.store.packages()
        held = {info.url: info for info in infos}
        ticked = set(chosen) if chosen is not None else set(
            self.store.chosen() or [info.url for info in infos])
        below = {info.url: supplemented(info.supplements)[0] for info in infos
                 if info.supplements and supplemented(info.supplements)[0] in held}
        self._filling = True
        self.clear()
        items: dict[str, QtWidgets.QTreeWidgetItem] = {}
        for info in sorted(infos, key=lambda i: (i.url in below, (i.title or i.url).casefold())):
            parent = items.get(below.get(info.url, ""))
            item = QtWidgets.QTreeWidgetItem(parent) if parent is not None \
                else QtWidgets.QTreeWidgetItem(self)
            for column, text in enumerate((info.title or info.url, info.version,
                                           _number(info.concepts), _number(info.terms),
                                           self._notes(info, held))):
                item.setText(column, text)
            for column in (2, 3):
                item.setTextAlignment(column, QtCore.Qt.AlignmentFlag.AlignRight
                                      | QtCore.Qt.AlignmentFlag.AlignVCenter)
            item.setToolTip(0, "\n".join(filter(None, (info.title, info.url, info.publisher,
                                                         info.copyright))))
            item.setToolTip(4, item.text(4))
            item.setData(0, URL, info.url)
            item.setFlags(item.flags() | QtCore.Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(0, QtCore.Qt.CheckState.Checked if info.url in ticked
                               else QtCore.Qt.CheckState.Unchecked)
            items[info.url] = item
        self.expandAll()
        for column in range(len(COLUMNS)):
            self.resizeColumnToContents(column)
        self.setColumnWidth(0, min(self.columnWidth(0), WIDEST))
        self._filling = False

    def _notes(self, info: PackageInfo, held: dict[str, PackageInfo]) -> str:
        """What a person should know of a package beside its numbers."""
        notes = []
        if info.supplements and supplemented(info.supplements)[0] not in held:
            system = supplemented(info.supplements)[0]
            notes.append(say(f"ergänzt {system}, das nicht in der Ablage liegt",
                             f"supplements {system}, which is not in the store"))
        if not info.redistributable:
            notes.append(say("nicht weitergebbar", "not to be passed on"))
        changes = len(self.store.overlay(self.store.system_of(info.url)))
        if changes:
            notes.append(say(f"{_number(changes)} eigene Änderungen",
                             f"{_number(changes)} changes of yours"))
        return "; ".join(notes)

    def _items(self) -> list[QtWidgets.QTreeWidgetItem]:
        found = []
        iterator = QtWidgets.QTreeWidgetItemIterator(self)
        while iterator.value():
            found.append(iterator.value())
            iterator += 1
        return found

    def _changed(self, _item: QtWidgets.QTreeWidgetItem, column: int) -> None:
        if not self._filling and column == 0:
            self.chosen_changed.emit(self.chosen())

    def chosen(self) -> list[str]:
        """The URLs of the packages ticked, in order."""
        return sorted(str(item.data(0, URL)) for item in self._items()
                      if item.checkState(0) == QtCore.Qt.CheckState.Checked)

    def tick_all(self, on: bool) -> None:
        """Tick every package, or none."""
        state = QtCore.Qt.CheckState.Checked if on else QtCore.Qt.CheckState.Unchecked
        self._filling = True
        for item in self._items():
            item.setCheckState(0, state)
        self._filling = False
        self.chosen_changed.emit(self.chosen())

    def current_url(self) -> str | None:
        """The URL of the package the person has selected."""
        item = self.currentItem()
        return None if item is None else str(item.data(0, URL))


class PackageWindow(QtWidgets.QWidget):
    """The packages of a store with what a person does with them: tick them for the runs
    that follow, take a file in, write a package out, take one out.  ``readers`` are the
    forms a library reads besides the core's, ``sources`` its text (HTML) on where
    terminologies come from, shown beside the list.  With ``remember`` the ticks are kept
    in the store."""

    def __init__(self, store: Store, readers: Sequence[Reader] = (), sources: str = "",
                 remember: bool = True, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.store = store
        self.readers = (CORE, *readers)
        self.worker: _Taking | None = None
        self.list = PackageList(store)
        if remember:
            self.list.chosen_changed.connect(store.choose)
        intro = QtWidgets.QLabel(say(
            "Angekreuzte Pakete werden bei den folgenden Läufen angewandt. Das"
            " Annotationsdokument eines Laufs hält fest, welche es waren, in welcher Version"
            " und mit welchen eigenen Änderungen.",
            "Ticked packages are applied in the runs that follow. The annotation document of a"
            " run records which they were, in which version and with which changes of yours."))
        intro.setWordWrap(True)
        self.status = QtWidgets.QLabel()
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        self.buttons = self._buttons()
        left = QtWidgets.QWidget()
        column = QtWidgets.QVBoxLayout(left)
        column.setContentsMargins(0, 0, 0, 0)
        for widget in (intro, self.list):
            column.addWidget(widget)
        column.addLayout(self.buttons)
        column.addWidget(self.status)
        layout = QtWidgets.QHBoxLayout(self)
        if sources:
            split = QtWidgets.QSplitter()
            split.addWidget(left)
            self.sources = QtWidgets.QTextBrowser()
            self.sources.setOpenExternalLinks(True)
            self.sources.setHtml(sources)
            split.addWidget(self.sources)
            split.setStretchFactor(0, 3)
            split.setStretchFactor(1, 2)
            layout.addWidget(split)
        else:
            layout.addWidget(left)
        self.list.currentItemChanged.connect(lambda *_: self._enable())
        self._enable()

    def _buttons(self) -> QtWidgets.QHBoxLayout:
        row = QtWidgets.QHBoxLayout()
        self.take = QtWidgets.QPushButton(say("Aufnehmen …", "Take in …"))
        if len(self.readers) > 1:
            menu = QtWidgets.QMenu(self.take)
            for reader in self.readers:
                action = menu.addAction(say(*reader.name) + " …")
                action.triggered.connect(lambda _=False, r=reader: self._ask_take(r))
            self.take.setMenu(menu)
        else:
            self.take.clicked.connect(lambda: self._ask_take(CORE))
        self.write = QtWidgets.QPushButton(say("Schreiben …", "Write out …"))
        self.write.clicked.connect(self._ask_write)
        self.drop = QtWidgets.QPushButton(say("Entfernen", "Take out"))
        self.drop.clicked.connect(self._ask_remove)
        every = QtWidgets.QPushButton(say("Alle ankreuzen", "Tick all"))
        every.clicked.connect(lambda: self.list.tick_all(True))
        none = QtWidgets.QPushButton(say("Keines", "None"))
        none.clicked.connect(lambda: self.list.tick_all(False))
        for button in (self.take, self.write, self.drop):
            row.addWidget(button)
        row.addStretch(1)
        for button in (every, none):
            row.addWidget(button)
        return row

    def _enable(self) -> None:
        chosen = self.list.current_url() is not None
        self.write.setEnabled(chosen)
        self.drop.setEnabled(chosen)

    def say_status(self, text: str) -> None:
        """Show what has just happened."""
        self.status.setText(text)

    # -- taking in ---------------------------------------------------------------------------------
    def _ask_take(self, reader: Reader) -> None:
        if reader.folder:
            chosen = QtWidgets.QFileDialog.getExistingDirectory(self, say(*reader.name))
        else:
            chosen, _filter = QtWidgets.QFileDialog.getOpenFileName(
                self, say(*reader.name), "", f"{say(*reader.name)} ({reader.patterns})")
        if chosen:
            self.take_in(Path(chosen), reader)

    def take_in(self, path: Path, reader: Reader = CORE) -> _Taking:
        """Take a file or folder in beside the window; the window waits meanwhile."""
        self.setEnabled(False)
        self.say_status(say(f"Wird aufgenommen: {path.name} …", f"Taking in: {path.name} …"))
        self.worker = _Taking(self.store, path, reader)
        self.worker.done.connect(self._taken)
        self.worker.failed.connect(self._failed)
        self.worker.start()
        return self.worker

    def _taken(self, infos: list[PackageInfo]) -> None:
        from lexcor.cli import fit_notes

        lines = []
        for info in infos:
            lines.append(say(f"Aufgenommen: {info.title or info.url}, {_number(info.concepts)}"
                             f" Begriffe in {_number(info.terms)} Schreibweisen.",
                             f"Taken in: {info.title or info.url}, {_number(info.concepts)}"
                             f" concepts in {_number(info.terms)} wordings."))
            lines += fit_notes(self.store, info)[1]
        self.list.refresh()
        self.setEnabled(True)
        self._enable()
        self.say_status("\n".join(lines) or say("Die Datei enthielt kein Paket.",
                                                "The file held no package."))

    def _failed(self, message: str) -> None:
        self.setEnabled(True)
        self._enable()
        self.say_status(say(f"Nicht aufgenommen: {message}", f"Not taken in: {message}"))

    # -- writing out and taking out ----------------------------------------------------------------
    def _ask_write(self) -> None:
        url = self.list.current_url()
        if url is None:
            return
        own_use = False
        if not self.store.info(url).redistributable:
            own_use = self._confirm(say(
                "Dieses Paket darf nicht weitergegeben werden. Eine Kopie nur für Sie selbst"
                " schreiben?",
                "This package may not be passed on. Write a copy for yourself only?"))
            if not own_use:
                return
        chosen, _filter = QtWidgets.QFileDialog.getSaveFileName(
            self, say("Paket schreiben", "Write out the package"), "",
            "FHIR CodeSystem (*.json);;CSV (*.csv)")
        if chosen:
            self.write_out(url, Path(chosen), own_use)

    def write_out(self, url: str, path: Path, own_use: bool = False) -> None:
        """Write a package as a FHIR CodeSystem, or as CSV where the file ends so.  One that
        may not be passed on is written only for the person's own use."""
        package = self.store.package(url)
        if not package.redistributable and not own_use:
            raise StoreError("not-redistributable",
                             "Dieses Paket darf nicht weitergegeben werden.",
                             "This package may not be passed on.")
        if path.suffix.lower() == ".csv":
            write_csv(package, path)
        else:
            write_codesystem(package, path)
        self.say_status(say(f"Geschrieben: {path}", f"Written: {path}"))

    def _ask_remove(self) -> None:
        url = self.list.current_url()
        if url is not None and self._confirm(say(
                f"„{self.store.info(url).title or url}“ aus der Ablage entfernen? Ihre eigenen"
                " Änderungen daran bleiben für den Tag, an dem es wiederkommt.",
                f"Take \"{self.store.info(url).title or url}\" out of the store? Your changes to"
                " it stay for the day it comes back.")):
            self.remove(url)

    def remove(self, url: str) -> None:
        """Take a package out of the store."""
        title = self.store.info(url).title or url
        self.store.remove_package(url)
        self.list.refresh()
        self._enable()
        self.say_status(say(f"Entfernt: {title}", f"Taken out: {title}"))

    def _confirm(self, question: str) -> bool:
        answer = QtWidgets.QMessageBox.question(self, say("Pakete", "Packages"), question)
        return answer == QtWidgets.QMessageBox.StandardButton.Yes


def open_window(store: Store, readers: Sequence[Reader] = (), sources: str = "",
                title: str = "") -> int:
    """Open the package window over a store on its own, until the person closes it."""
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = PackageWindow(store, readers, sources)
    window.setWindowTitle(title or say("Pakete", "Packages"))
    window.resize(1100, 560)
    window.show()
    QtGui.QGuiApplication.processEvents()
    return int(app.exec())
