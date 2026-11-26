# SPDX-FileCopyrightText: 2026 fre.ms
# SPDX-License-Identifier: MIT
"""The store: one SQLite file with the packages, their concepts and terms, and the overlays.

A user's changes (a term that misleads is hidden, a synonym is added, a concept is moved to
another class) are kept as an **overlay** beside the package and never written into it: a
new version of the package replaces the package and leaves the overlay in force (design,
section 3.3).  A counter changes with every edit, so that an index built from the store is
rebuilt only when needed.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
import sys
from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass
from pathlib import Path

from lexcor.language import say
from lexcor.package import KINDS, Concept, Package, Term, tidy

SCHEMA = """
CREATE TABLE IF NOT EXISTS package (
    id              INTEGER PRIMARY KEY,
    url             TEXT NOT NULL UNIQUE,
    version         TEXT NOT NULL DEFAULT '',
    name            TEXT NOT NULL DEFAULT '',
    title           TEXT NOT NULL DEFAULT '',
    publisher       TEXT NOT NULL DEFAULT '',
    copyright       TEXT NOT NULL DEFAULT '',
    language        TEXT NOT NULL DEFAULT '',
    redistributable INTEGER NOT NULL DEFAULT 0,
    sha256          TEXT NOT NULL,
    added           TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS concept (
    id      INTEGER PRIMARY KEY,
    package INTEGER NOT NULL REFERENCES package(id) ON DELETE CASCADE,
    code    TEXT NOT NULL,
    display TEXT NOT NULL DEFAULT '',
    class   TEXT NOT NULL DEFAULT '',
    parent  TEXT NOT NULL DEFAULT '',
    UNIQUE (package, code)
);
CREATE TABLE IF NOT EXISTS term (
    concept    INTEGER NOT NULL REFERENCES concept(id) ON DELETE CASCADE,
    value      TEXT NOT NULL,
    language   TEXT NOT NULL DEFAULT '',
    kind       TEXT NOT NULL DEFAULT 'synonym',
    as_written INTEGER,
    PRIMARY KEY (concept, value, language)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS overlay (
    url      TEXT NOT NULL,
    code     TEXT NOT NULL,
    action   TEXT NOT NULL CHECK (action IN ('hide', 'add', 'class')),
    value    TEXT NOT NULL DEFAULT '',
    language TEXT NOT NULL DEFAULT '',
    kind     TEXT NOT NULL DEFAULT '',
    made     TEXT NOT NULL,
    PRIMARY KEY (url, code, action, value, language)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
) WITHOUT ROWID;
"""
OVERLAY_FORMAT = 1


class StoreError(ValueError):
    """Something the store was asked for that it does not hold."""


@dataclass(frozen=True)
class PackageInfo:
    """What the store says about a package without its concepts."""

    url: str
    version: str
    title: str
    publisher: str
    copyright: str
    redistributable: bool
    sha256: str
    concepts: int
    terms: int


@dataclass(frozen=True)
class Entry:
    """One term as recognition sees it: its wording, and the concept it stands for."""

    term: str
    as_written: bool | None
    system: str
    version: str
    code: str
    display: str
    cls: str
    language: str
    kind: str


@dataclass(frozen=True)
class Change:
    """One line of an overlay.  ``hide`` with an empty ``value`` hides the whole concept;
    ``add`` adds the term ``value``; ``class`` moves the concept to the class ``value``."""

    url: str
    code: str
    action: str
    value: str = ""
    language: str = ""
    kind: str = ""


def data_folder() -> Path:
    """Where stores live by default: ``LEXCOR_DATA``, else the user's data folder."""
    given = os.environ.get("LEXCOR_DATA")
    if given:
        return Path(given)
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "lexcor"
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming") / "lexcor"
    return Path(os.environ.get("XDG_DATA_HOME") or home / ".local" / "share") / "lexcor"


def _now() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds")


class Store:
    """The packages of one library and what its user changed in them."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(self.path))
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.executescript(SCHEMA)
        self.connection.commit()

    def close(self) -> None:
        """Close the file."""
        self.connection.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- version -----------------------------------------------------------------------------------
    def version(self) -> int:
        """A counter that every change raises."""
        row = self.connection.execute("SELECT value FROM meta WHERE key = 'version'").fetchone()
        return int(row[0]) if row else 0

    def _changed(self) -> None:
        self.connection.execute(
            "INSERT INTO meta (key, value) VALUES ('version', '1') ON CONFLICT(key)"
            " DO UPDATE SET value = CAST(CAST(value AS INTEGER) + 1 AS TEXT)")
        self.connection.commit()

    # -- packages ----------------------------------------------------------------------------------
    def add_package(self, package: Package) -> PackageInfo:
        """Take a package in.  One with the same URL is replaced, whatever its version; the
        overlay of that URL stays in force."""
        if not package.url:
            raise StoreError(say("Einem Paket fehlt die URL.", "A package has no URL."))
        package = tidy(package)
        cursor = self.connection.cursor()
        cursor.execute("DELETE FROM package WHERE url = ?", (package.url,))
        cursor.execute(
            "INSERT INTO package (url, version, name, title, publisher, copyright, language,"
            " redistributable, sha256, added) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (package.url, package.version, package.name, package.title, package.publisher,
             package.copyright, package.language, int(package.redistributable),
             package.sha256(), _now()))
        number = cursor.lastrowid
        for concept in package.concepts:
            cursor.execute(
                "INSERT OR IGNORE INTO concept (package, code, display, class, parent)"
                " VALUES (?, ?, ?, ?, ?)",
                (number, concept.code, concept.display, concept.cls, concept.parent))
            if not cursor.rowcount:
                continue
            cursor.executemany(
                "INSERT OR IGNORE INTO term (concept, value, language, kind, as_written)"
                " VALUES (?, ?, ?, ?, ?)",
                [(cursor.lastrowid, t.value, t.language, t.kind,
                  None if t.as_written is None else int(t.as_written)) for t in concept.terms])
        self._changed()
        return self.info(package.url)

    def remove_package(self, url: str) -> bool:
        """Take a package out; its overlay stays, for the day the package comes back."""
        removed = self.connection.execute("DELETE FROM package WHERE url = ?", (url,)).rowcount
        if removed:
            self._changed()
        return bool(removed)

    def packages(self) -> list[PackageInfo]:
        """Every package of the store, by title."""
        rows = self.connection.execute(
            "SELECT p.url, p.version, p.title, p.publisher, p.copyright, p.redistributable,"
            " p.sha256, (SELECT COUNT(*) FROM concept c WHERE c.package = p.id),"
            " (SELECT COUNT(*) FROM term t JOIN concept c ON c.id = t.concept"
            "  WHERE c.package = p.id) FROM package p ORDER BY p.title, p.url")
        return [PackageInfo(r[0], r[1], r[2], r[3], r[4], bool(r[5]), r[6], r[7], r[8])
                for r in rows]

    def info(self, url: str) -> PackageInfo:
        """What the store says about one package."""
        for info in self.packages():
            if info.url == url:
                return info
        raise StoreError(say(f"Kein Paket mit der URL {url}.", f"No package with the URL {url}."))

    def package(self, url: str) -> Package:
        """A package as it came, without the overlay: what an export writes."""
        row = self.connection.execute(
            "SELECT id, url, version, name, title, publisher, copyright, language,"
            " redistributable FROM package WHERE url = ?", (url,)).fetchone()
        if row is None:
            raise StoreError(say(f"Kein Paket mit der URL {url}.",
                                 f"No package with the URL {url}."))
        concepts: dict[int, Concept] = {}
        for number, code, display, cls, parent in self.connection.execute(
                "SELECT id, code, display, class, parent FROM concept WHERE package = ?"
                " ORDER BY code", (row[0],)):
            concepts[number] = Concept(code, display, cls, parent)
        for number, value, language, kind, as_written in self.connection.execute(
                "SELECT t.concept, t.value, t.language, t.kind, t.as_written FROM term t"
                " JOIN concept c ON c.id = t.concept WHERE c.package = ?", (row[0],)):
            concepts[number].terms.append(
                Term(value, language, kind, None if as_written is None else bool(as_written)))
        return tidy(Package(url=row[1], version=row[2], name=row[3], title=row[4],
                            publisher=row[5], copyright=row[6], language=row[7],
                            redistributable=bool(row[8]), concepts=list(concepts.values())))

    # -- what recognition reads --------------------------------------------------------------------
    def entries(self, packages: Iterable[str] | None = None,
                classes: Iterable[str] | None = None,
                languages: Iterable[str] | None = None) -> Iterator[Entry]:
        """Every term with its concept, the overlay applied: hidden terms and concepts are
        left out, added terms are in, classes are as the user set them.  ``packages`` (URLs),
        ``classes`` and ``languages`` narrow the choice; a term that names no language passes
        every choice of languages."""
        wanted = None if packages is None else set(packages)
        allowed = None if classes is None else set(classes)
        spoken = None if languages is None else {*languages, ""}
        hidden: set[tuple[str, str, str, str]] = set()
        added: dict[tuple[str, str], list[tuple[str, str, str]]] = {}
        moved: dict[tuple[str, str], str] = {}
        for url, code, action, value, language, kind in self.connection.execute(
                "SELECT url, code, action, value, language, kind FROM overlay"):
            if action == "hide":
                hidden.add((url, code, value, language))
            elif action == "add":
                added.setdefault((url, code), []).append((value, language, kind))
            else:
                moved[(url, code)] = value
        rows = self.connection.execute(
            "SELECT p.url, p.version, c.code, c.display, c.class, t.value, t.language, t.kind,"
            " t.as_written FROM term t JOIN concept c ON c.id = t.concept"
            " JOIN package p ON p.id = c.package ORDER BY p.url, c.code, t.value, t.language")
        seen: set[tuple[str, str]] = set()
        for url, version, code, display, cls, value, language, kind, as_written in rows:
            if wanted is not None and url not in wanted:
                continue
            cls = moved.get((url, code), cls)
            if (url, code, "", "") in hidden or (allowed is not None and cls not in allowed):
                continue
            if (url, code) not in seen:
                seen.add((url, code))
                for extra, extra_language, extra_kind in added.get((url, code), []):
                    if spoken is None or extra_language in spoken:
                        yield Entry(extra, None, url, version, code, display, cls,
                                    extra_language, extra_kind or "synonym")
            if (url, code, value, language) in hidden or (url, code, value, "") in hidden:
                continue
            if spoken is not None and language not in spoken:
                continue
            yield Entry(value, None if as_written is None else bool(as_written), url, version,
                        code, display, cls, language, kind)

    # -- overlay -----------------------------------------------------------------------------------
    def _known(self, url: str, code: str) -> None:
        found = self.connection.execute(
            "SELECT 1 FROM concept c JOIN package p ON p.id = c.package"
            " WHERE p.url = ? AND c.code = ?", (url, code)).fetchone()
        if not found:
            raise StoreError(say(f"Das Paket {url} hat keinen Begriff mit dem Code {code}.",
                                 f"The package {url} has no concept with the code {code}."))

    def _put(self, change: Change) -> None:
        self.connection.execute(
            "INSERT OR REPLACE INTO overlay (url, code, action, value, language, kind, made)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (change.url, change.code, change.action, change.value, change.language, change.kind,
             _now()))

    def hide(self, url: str, code: str, value: str = "", language: str = "") -> None:
        """Hide one term of a concept, or with an empty ``value`` the whole concept."""
        self._known(url, code)
        self._put(Change(url, code, "hide", value, language))
        self._changed()

    def add_term(self, url: str, code: str, term: Term) -> None:
        """Add a term to a concept of a package."""
        self._known(url, code)
        value = " ".join(term.value.split())
        if not value:
            raise StoreError(say("Der Begriff ist leer.", "The term is empty."))
        kind = term.kind if term.kind in KINDS else "synonym"
        self._put(Change(url, code, "add", value, term.language, kind))
        self._changed()

    def set_class(self, url: str, code: str, cls: str) -> None:
        """Move a concept to another class."""
        self._known(url, code)
        self.connection.execute(
            "DELETE FROM overlay WHERE url = ? AND code = ? AND action = 'class'", (url, code))
        self._put(Change(url, code, "class", cls))
        self._changed()

    def undo(self, change: Change) -> bool:
        """Take one line of the overlay back."""
        removed = self.connection.execute(
            "DELETE FROM overlay WHERE url = ? AND code = ? AND action = ? AND value = ?"
            " AND language = ?",
            (change.url, change.code, change.action, change.value, change.language)).rowcount
        if removed:
            self._changed()
        return bool(removed)

    def overlay(self, url: str | None = None) -> list[Change]:
        """The user's changes, of one package or of all."""
        rows = self.connection.execute(
            "SELECT url, code, action, value, language, kind FROM overlay"
            " WHERE (? IS NULL OR url = ?) ORDER BY url, code, action, value, language",
            (url, url))
        return [Change(*row) for row in rows]

    def export_overlay(self, path: Path, url: str | None = None) -> int:
        """Write the user's changes as a JSON file; returns how many."""
        changes = self.overlay(url)
        document = {"lexcor-overlay": OVERLAY_FORMAT, "changes": [asdict(c) for c in changes]}
        Path(path).write_text(json.dumps(document, ensure_ascii=False, indent=1) + "\n",
                              encoding="utf-8")
        return len(changes)

    def import_overlay(self, path: Path) -> int:
        """Take changes in from a file written by :meth:`export_overlay`; returns how many.
        Changes for packages or concepts the store does not hold are taken in as well: they
        come into force when the package does."""
        try:
            document = json.loads(Path(path).read_text(encoding="utf-8"))
            if document.get("lexcor-overlay") != OVERLAY_FORMAT:
                raise ValueError("format")
            changes = [Change(**entry) for entry in document["changes"]]
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
            raise StoreError(say(f"{path} ist keine Überlagerung dieser Bibliothek.",
                                 f"{path} is not an overlay of this library.")) from error
        for change in changes:
            if change.action not in ("hide", "add", "class"):
                raise StoreError(say(f"Unbekannte Änderung: {change.action}",
                                     f"Unknown change: {change.action}"))
            self._put(change)
        self._changed()
        return len(changes)


def open_store(path: str | Path | None = None, library: str = "lexcor") -> Store:
    """Open the store at ``path``, or the store of a library in the user's data folder."""
    return Store(path if path is not None else data_folder() / f"{library}.sqlite")
