# SPDX-FileCopyrightText: 2026 fre.ms
# SPDX-License-Identifier: MIT
"""The store: one SQLite file with the packages, their concepts and terms, and the overlays.

A user's changes (a term that misleads is hidden, a synonym is added, a concept is moved to
another class) are kept as an **overlay** beside the package and never written into it: a
new version of the package replaces the package and leaves the overlay in force (design,
section 3.3).  A counter changes with every edit, so that an index built from the store is
rebuilt only when needed.

A **supplement** is a package of its own in the store, and its terms code in the system it
supplements: recognition sees them as terms of that system's codes, with the name and class
the system gives the code where the store holds the system as well.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import sqlite3
import sys
import uuid
from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass
from pathlib import Path

from lexcor.language import LexError
from lexcor.package import KINDS, Concept, Package, Term, supplemented, tidy

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
    added           TEXT NOT NULL,
    supplements     TEXT NOT NULL DEFAULT ''
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
    PRIMARY KEY (concept, value, language)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS property (
    concept INTEGER NOT NULL REFERENCES concept(id) ON DELETE CASCADE,
    name    TEXT NOT NULL,
    value   TEXT NOT NULL,
    PRIMARY KEY (concept, name)
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


class StoreError(LexError):
    """Something the store was asked for that it does not hold, or may not do."""


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
    supplements: str = ""


@dataclass(frozen=True)
class Fit:
    """How a supplement fits the code system it supplements, as a store holds it: the
    system, the version of it the supplement names (empty for any), the version the store
    holds (None where it holds none), and how many codes of the supplement the system held
    does not have."""

    system: str
    wanted: str
    held: str | None
    unknown: int


# of each code system held that a supplement names: its version, and by code the name, the
# class and whether the code is terminal
Held = dict[str, tuple[str, dict[str, tuple[str, str, bool | None]]]]
Coded = tuple[str, str, str, str, bool | None]  # system, version, name, class, terminal


def _coded(named: str, code: str, display: str, cls: str, bases: Held) -> Coded:
    """Where a term of a supplement codes: in the system it supplements.  Where that system
    is held and has the code, with its version and the name, the class and the place in the
    hierarchy it gives the code; else with the version the supplement names, what the
    supplement says itself, and nothing about the hierarchy."""
    system, version = supplemented(named)
    held = bases.get(system)
    if held and code in held[1]:
        return system, held[0], *held[1][code]
    return system, version, display, cls, None


def terminal(code: str, parents: set[str] | None) -> bool | None:
    """Whether a code is terminal in its code system: no other code has it as its parent.
    None for a code system without a hierarchy, where the question does not arise."""
    return None if parents is None else code not in parents


def digest_of(changes: Iterable[Change]) -> str:
    """The fingerprint of some changes of an overlay, the same for the same changes whenever
    they were made; empty for none."""
    listed = sorted((c.url, c.code, c.action, c.value, c.language, c.kind) for c in changes)
    if not listed:
        return ""
    return hashlib.sha256(json.dumps(listed, ensure_ascii=False, separators=(",", ":"))
                          .encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Entry:
    """One term as recognition sees it: its wording, and the concept it stands for.
    ``package`` and ``package_version`` name the package the term is in where that is not
    the code system itself (a supplement); ``terminal`` says whether the code is terminal in
    a code system with a hierarchy (None in one without)."""

    term: str
    system: str
    version: str
    code: str
    display: str
    cls: str
    language: str
    kind: str
    package: str = ""
    package_version: str = ""
    terminal: bool | None = None


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


def _speaks(language: str, spoken: set[str] | None) -> bool:
    """Whether a term's language is among those asked for.  A term that names none passes
    every choice, and a language with a region ("de-DE", "en-GB") is the language it is a
    variety of: publishers write either."""
    return spoken is None or language.split("-")[0].lower() in spoken


def _spoken(languages: Iterable[str] | None) -> set[str] | None:
    return None if languages is None else {*(lang.split("-")[0].lower() for lang in languages), ""}


def _now() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds")


class Store:
    """The packages of one library and what its user changed in them.

    ``readonly`` opens a store that exists without writing anything, not even a journal
    beside it: for a store that comes inside a program, or in a folder a caller may only
    read.  ``":memory:"`` as the path keeps the store in memory, for one run.  A store may be
    opened in one thread and used in another, by one thread at a time."""

    def __init__(self, path: str | Path, readonly: bool = False) -> None:
        self.path = Path(path)
        self.readonly = readonly
        self.memory = str(path) == ":memory:"
        try:
            if readonly:
                self.connection = sqlite3.connect(
                    f"{self.path.resolve().as_uri()}?mode=ro", uri=True, check_same_thread=False)
                self.connection.execute("SELECT COUNT(*) FROM package").fetchone()
            else:
                if not self.memory:
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                self.connection = sqlite3.connect(str(path), check_same_thread=False)
        except (sqlite3.Error, OSError) as error:
            raise StoreError("no-store", f"Die Ablage {self.path.name} lässt sich nicht öffnen.",
                             f"The store {self.path.name} cannot be opened.") from error
        if not readonly:
            self.connection.execute("PRAGMA foreign_keys = ON")
            self.connection.executescript(SCHEMA)
            if not self._names_supplements():  # a store from before there were supplements
                self.connection.execute(
                    "ALTER TABLE package ADD COLUMN supplements TEXT NOT NULL DEFAULT ''")
            self.connection.commit()
        # a store written before then and opened for reading only holds no supplement
        self._supplements = "p.supplements" if self._names_supplements() else "''"

    def _names_supplements(self) -> bool:
        return any(row[1] == "supplements"
                   for row in self.connection.execute("PRAGMA table_info(package)"))

    @property
    def key(self) -> str:
        """What tells this store from every other in this process: its file, or for a store
        in memory itself."""
        return f":memory:{id(self)}" if self.memory else str(self.path.resolve())

    def _writable(self) -> None:
        if self.readonly:
            raise StoreError("read-only", "Diese Ablage ist nur zum Lesen geöffnet.",
                             "This store is open for reading only.")

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
        self._writable()
        self.connection.execute(
            "INSERT INTO meta (key, value) VALUES ('version', '1') ON CONFLICT(key)"
            " DO UPDATE SET value = CAST(CAST(value AS INTEGER) + 1 AS TEXT)")
        self.connection.commit()

    # -- packages ----------------------------------------------------------------------------------
    def add_package(self, package: Package) -> PackageInfo:
        """Take a package in.  One with the same URL is replaced, whatever its version; the
        overlay of that URL stays in force.  A list without a URL gets one of this store's.
        A supplement is taken in like any package, whether or not the code system it
        supplements is here (:meth:`fit` says how the two go together)."""
        self._writable()
        package = tidy(package)
        package.url = package.url or self.local_url(package.name or package.title)
        cursor = self.connection.cursor()
        cursor.execute("DELETE FROM package WHERE url = ?", (package.url,))
        cursor.execute(
            "INSERT INTO package (url, version, name, title, publisher, copyright, language,"
            " redistributable, sha256, added, supplements)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (package.url, package.version, package.name, package.title, package.publisher,
             package.copyright, package.language, int(package.redistributable),
             package.sha256(), _now(), package.supplements))
        number = cursor.lastrowid
        for concept in package.concepts:
            cursor.execute(
                "INSERT OR IGNORE INTO concept (package, code, display, class, parent)"
                " VALUES (?, ?, ?, ?, ?)",
                (number, concept.code, concept.display, concept.cls, concept.parent))
            if not cursor.rowcount:
                continue
            row = cursor.lastrowid
            cursor.executemany(
                "INSERT OR IGNORE INTO term (concept, value, language, kind)"
                " VALUES (?, ?, ?, ?)",
                [(row, t.value, t.language, t.kind) for t in concept.terms])
            cursor.executemany(
                "INSERT INTO property (concept, name, value) VALUES (?, ?, ?)",
                [(row, k, v) for k, v in concept.properties.items()])
        self._changed()
        return self.info(package.url)

    def local_url(self, name: str) -> str:
        """The URI of a list that has none of its own.  It is this store's, so it belongs to
        whoever keeps the store and not to this library, and it is the same for the same
        name, so that a list read again replaces itself."""
        words = " ".join(name.casefold().split())
        if not words:
            raise StoreError("no-name", "Einem Paket fehlen URL und Name.",
                             "A package has neither a URL nor a name.")
        row = self.connection.execute("SELECT value FROM meta WHERE key = 'owner'").fetchone()
        if row is None:
            self._writable()
            row = (str(uuid.uuid4()),)
            self.connection.execute("INSERT INTO meta (key, value) VALUES ('owner', ?)", row)
            self.connection.commit()
        return f"urn:uuid:{uuid.uuid5(uuid.UUID(row[0]), words)}"

    def find(self, given: str) -> str:
        """The URL of the package a person means: its URL, or its title or name where only
        one package is called so."""
        rows = self.connection.execute("SELECT url, title, name FROM package").fetchall()
        if any(url == given for url, _title, _name in rows):
            return given
        wanted = given.casefold()
        found = sorted(url for url, title, name in rows
                       if wanted in (title.casefold(), name.casefold()))
        if len(found) == 1:
            return str(found[0])
        if found:
            raise StoreError(
                "several-packages",
                f"Mehrere Pakete heißen {given}; bitte die URL angeben: {', '.join(found)}",
                f"Several packages are called {given}; please give the URL: {', '.join(found)}")
        raise StoreError("no-package", f"Kein Paket mit der URL oder dem Namen {given}.",
                         f"No package with the URL or the name {given}.")

    def remove_package(self, url: str) -> bool:
        """Take a package out; its overlay stays, for the day the package comes back."""
        self._writable()
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
            f"  WHERE c.package = p.id), {self._supplements} FROM package p"
            " ORDER BY p.title, p.url")
        return [PackageInfo(r[0], r[1], r[2], r[3], r[4], bool(r[5]), r[6], r[7], r[8], r[9])
                for r in rows]

    def info(self, url: str) -> PackageInfo:
        """What the store says about one package."""
        for info in self.packages():
            if info.url == url:
                return info
        raise StoreError("no-package", f"Kein Paket mit der URL {url}.",
                         f"No package with the URL {url}.")

    def package(self, url: str) -> Package:
        """A package as it came, without the overlay: what an export writes."""
        row = self.connection.execute(
            "SELECT p.id, p.url, p.version, p.name, p.title, p.publisher, p.copyright,"
            f" p.language, p.redistributable, {self._supplements} FROM package p"
            " WHERE p.url = ?", (url,)).fetchone()
        if row is None:
            raise StoreError("no-package", f"Kein Paket mit der URL {url}.",
                             f"No package with the URL {url}.")
        concepts: dict[int, Concept] = {}
        for number, code, display, cls, parent in self.connection.execute(
                "SELECT id, code, display, class, parent FROM concept WHERE package = ?"
                " ORDER BY code", (row[0],)):
            concepts[number] = Concept(code, display, cls, parent)
        for number, value, language, kind in self.connection.execute(
                "SELECT t.concept, t.value, t.language, t.kind FROM term t"
                " JOIN concept c ON c.id = t.concept WHERE c.package = ?", (row[0],)):
            concepts[number].terms.append(Term(value, language, kind))
        if self._has_properties():
            for number, name, value in self.connection.execute(
                    "SELECT y.concept, y.name, y.value FROM property y"
                    " JOIN concept c ON c.id = y.concept WHERE c.package = ?", (row[0],)):
                concepts[number].properties[name] = value
        return tidy(Package(url=row[1], version=row[2], name=row[3], title=row[4],
                            publisher=row[5], copyright=row[6], language=row[7],
                            redistributable=bool(row[8]), concepts=list(concepts.values()),
                            supplements=row[9]))

    def _has_properties(self) -> bool:
        """A store written before concepts had properties, and opened for reading only, has
        no table for them."""
        return self.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'property'"
        ).fetchone() is not None

    def properties(self, url: str, code: str) -> dict[str, str]:
        """What else a package says of one concept, by name."""
        if not self._has_properties():
            return {}
        return dict(self.connection.execute(
            "SELECT y.name, y.value FROM property y JOIN concept c ON c.id = y.concept"
            " JOIN package p ON p.id = c.package WHERE p.url = ? AND c.code = ?"
            " ORDER BY y.name", (url, code)).fetchall())

    # -- hierarchy and supplements -----------------------------------------------------------------
    def _parents(self) -> dict[int, set[str]]:
        """The codes that are a parent, for each package with a hierarchy, by its number."""
        parents: dict[int, set[str]] = {}
        for number, parent in self.connection.execute(
                "SELECT package, parent FROM concept WHERE parent != ''"):
            parents.setdefault(number, set()).add(parent)
        return parents

    def _bases(self, parents: dict[int, set[str]] | None = None) -> Held:
        """The code systems that supplements of this store name, as far as it holds them."""
        parents = self._parents() if parents is None else parents
        bases: Held = {}
        named = self.connection.execute(
            f"SELECT DISTINCT {self._supplements} FROM package p").fetchall()
        for system in sorted({supplemented(str(n))[0] for (n,) in named if n}):
            row = self.connection.execute(
                f"SELECT p.id, p.version FROM package p WHERE p.url = ?"
                f" AND {self._supplements} = ''", (system,)).fetchone()
            if row is not None:
                above = parents.get(row[0])
                bases[system] = (row[1], {
                    code: (display, cls, terminal(code, above))
                    for code, display, cls in self.connection.execute(
                        "SELECT code, display, class FROM concept WHERE package = ?", (row[0],))})
        return bases

    def system_of(self, url: str) -> str:
        """The code system a package codes in: its own URL, or for a supplement the URL of
        the code system it supplements.  The overlay of a code is kept under this URL."""
        row = self.connection.execute(
            f"SELECT {self._supplements} FROM package p WHERE p.url = ?", (url,)).fetchone()
        return supplemented(str(row[0]))[0] if row and row[0] else url

    def overlay_digest(self, url: str) -> str:
        """The fingerprint of the changes in force for the terms of a package (for a
        supplement those of the code system it supplements); empty where there are none."""
        return digest_of(self.overlay(self.system_of(url)))

    def fit(self, url: str) -> Fit | None:
        """How the supplement with this URL fits the code system it supplements, as this
        store holds it; None for a package that is no supplement."""
        named = self.info(url).supplements
        if not named:
            return None
        system, wanted = supplemented(named)
        held = self._bases().get(system)
        if held is None:
            return Fit(system, wanted, None, 0)
        codes = self.connection.execute(
            "SELECT c.code FROM concept c JOIN package p ON p.id = c.package WHERE p.url = ?",
            (url,))
        return Fit(system, wanted, held[0], sum(1 for (code,) in codes if code not in held[1]))

    # -- what recognition reads --------------------------------------------------------------------
    def entries(self, packages: Iterable[str] | None = None,
                classes: Iterable[str] | None = None,
                languages: Iterable[str] | None = None) -> Iterator[Entry]:
        """Every term with its concept, the overlay applied: hidden terms and concepts are
        left out, added terms are in, classes are as the user set them.  ``packages`` (URLs),
        ``classes`` and ``languages`` narrow the choice; a term that names no language passes
        every choice of languages.  A term of a supplement is a term of the code it names in
        the system supplemented, and the overlay of that system holds for it; a supplement is
        chosen by its own URL, like any package."""
        wanted = None if packages is None else set(packages)
        allowed = None if classes is None else set(classes)
        spoken = _spoken(languages)
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
        parents = self._parents()
        bases = self._bases(parents)
        rows = self.connection.execute(
            f"SELECT p.id, p.url, {self._supplements}, p.version, c.code, c.display, c.class,"
            " t.value, t.language, t.kind FROM term t JOIN concept c ON c.id = t.concept"
            " JOIN package p ON p.id = c.package ORDER BY p.url, c.code, t.value, t.language")
        seen: set[tuple[str, str]] = set()
        for number, url, named, version, code, display, cls, value, language, kind in rows:
            if wanted is not None and url not in wanted:
                continue
            source, source_version = ("", "") if not named else (url, version)
            if named:
                url, version, display, cls, end = _coded(named, code, display, cls, bases)
            else:
                end = terminal(code, parents.get(number))
            cls = moved.get((url, code), cls)
            if (url, code, "", "") in hidden or (allowed is not None and cls not in allowed):
                continue
            if (url, code) not in seen:
                seen.add((url, code))
                for extra, extra_language, extra_kind in added.get((url, code), []):
                    if _speaks(extra_language, spoken):
                        yield Entry(extra, url, version, code, display, cls,
                                    extra_language, extra_kind or "synonym", terminal=end)
            if (url, code, value, language) in hidden or (url, code, value, "") in hidden:
                continue
            if not _speaks(language, spoken):
                continue
            yield Entry(value, url, version, code, display, cls, language, kind, source,
                        source_version, end)

    # -- overlay -----------------------------------------------------------------------------------
    def _known(self, url: str, code: str) -> None:
        """A change needs its concept: a code of the package, or one a supplement to it
        names."""
        found = self.connection.execute(
            "SELECT 1 FROM concept c JOIN package p ON p.id = c.package"
            f" WHERE c.code = ? AND (p.url = ? OR {self._supplements} = ?"
            f" OR substr({self._supplements}, 1, length(?) + 1) = ? || '|')",
            (code, url, url, url, url)).fetchone()
        if not found:
            raise StoreError("no-concept",
                             f"Das Paket {url} hat keinen Begriff mit dem Code {code}.",
                             f"The package {url} has no concept with the code {code}.")

    def _put(self, change: Change) -> None:
        self._writable()
        self.connection.execute(
            "INSERT OR REPLACE INTO overlay (url, code, action, value, language, kind, made)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (change.url, change.code, change.action, change.value, change.language, change.kind,
             _now()))

    def hide(self, url: str, code: str, value: str = "", language: str = "") -> None:
        """Hide one term of a concept, or with an empty ``value`` the whole concept.  A
        change named by the URL of a supplement is kept for the code system it supplements:
        that is where its terms code."""
        url = self.system_of(url)
        self._known(url, code)
        self._put(Change(url, code, "hide", value, language))
        self._changed()

    def add_term(self, url: str, code: str, term: Term) -> None:
        """Add a term to a concept of a package."""
        url = self.system_of(url)
        self._known(url, code)
        value = " ".join(term.value.split())
        if not value:
            raise StoreError("empty-term", "Der Begriff ist leer.", "The term is empty.")
        kind = term.kind if term.kind in KINDS else "synonym"
        self._put(Change(url, code, "add", value, term.language, kind))
        self._changed()

    def set_class(self, url: str, code: str, cls: str) -> None:
        """Move a concept to another class."""
        url = self.system_of(url)
        self._known(url, code)
        self.connection.execute(
            "DELETE FROM overlay WHERE url = ? AND code = ? AND action = 'class'", (url, code))
        self._put(Change(url, code, "class", cls))
        self._changed()

    def undo(self, change: Change) -> bool:
        """Take one line of the overlay back."""
        self._writable()
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
            raise StoreError("not-an-overlay", f"{path} ist keine Überlagerung dieser Bibliothek.",
                             f"{path} is not an overlay of this library.") from error
        for change in changes:
            if change.action not in ("hide", "add", "class"):
                raise StoreError("unknown-change", f"Unbekannte Änderung: {change.action}",
                                 f"Unknown change: {change.action}")
            self._put(change)
        self._changed()
        return len(changes)


def entries_of(packages: Iterable[Package], classes: Iterable[str] | None = None,
               languages: Iterable[str] | None = None) -> Iterator[Entry]:
    """Every term of some packages as recognition sees it, without a store: for a library
    that tags with the packages it brings, and writes nothing anywhere."""
    allowed = None if classes is None else set(classes)
    spoken = _spoken(languages)
    given = [tidy(package) for package in packages]
    parents = {package.url: {c.parent for c in package.concepts if c.parent} or None
               for package in given if not package.supplements}
    named = {supplemented(package)[0] for package in given if package.supplements}
    bases: Held = {package.url: (package.version, {
        c.code: (c.display, c.cls, terminal(c.code, parents[package.url]))
        for c in package.concepts})
        for package in given if package.url in named and not package.supplements}
    for package in given:
        for concept in package.concepts:
            source, source_version = ("", "") if not package.supplements else (
                package.url, package.version)
            if package.supplements:
                url, version, display, cls, end = _coded(
                    package.supplements, concept.code, concept.display, concept.cls, bases)
            else:
                url, version, display, cls = (package.url, package.version, concept.display,
                                              concept.cls)
                end = terminal(concept.code, parents[package.url])
            if allowed is not None and cls not in allowed:
                continue
            for term in concept.terms:
                if _speaks(term.language, spoken):
                    yield Entry(term.value, url, version, concept.code, display, cls,
                                term.language, term.kind, source, source_version, end)


def open_store(path: str | Path | None = None, library: str = "lexcor",
               readonly: bool = False) -> Store:
    """Open the store at ``path``, or the store of a library in the user's data folder."""
    return Store(path if path is not None else data_folder() / f"{library}.sqlite", readonly)
