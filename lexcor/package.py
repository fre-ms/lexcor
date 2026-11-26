# SPDX-FileCopyrightText: 2026 fre.ms
# SPDX-License-Identifier: MIT
"""Packages: one terminology or list in one version, with its licence.

A package is exchanged as a **FHIR CodeSystem** (R4, JSON) or, for lists made by hand, as a
**CSV** file (design, section 3.1).  It is written with FHIR's own elements and codes
wherever FHIR has one.  Two things it has none for are this library's own: whether a package
may be passed on (an extension), and the kind of term "abbreviation" (a code), which also
says that the term is matched only as written.

The namespace of these two is provisional (``NAMESPACE``): it is in one place so that it can
be changed before a package is passed on.  A list without a URI of its own gets one from the
store that takes it in, not from this library.
"""

from __future__ import annotations

import csv
import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lexcor.language import LexError

NAMESPACE = "https://lexcor.invalid"
REDISTRIBUTABLE = f"{NAMESPACE}/StructureDefinition/redistributable"
TERM_KINDS = f"{NAMESPACE}/CodeSystem/term-kind"  # its one code so far: abbreviation
# FHIR's and HL7's own: the code for a name to show, the property for a concept's parent
DESIGNATION_USAGE = "http://terminology.hl7.org/CodeSystem/designation-usage"
SNOMED = "http://snomed.info/sct"
PARENT = "http://hl7.org/fhir/concept-properties#parent"  # FHIR's own property, not one of ours
KINDS = ("preferred", "synonym", "abbreviation")
CSV_COLUMNS = ("term", "code", "display", "class", "kind", "language")
# designation.use as a package is written: a synonym carries none
_USES = {"preferred": {"system": DESIGNATION_USAGE, "code": "display"},
         "abbreviation": {"system": TERM_KINDS, "code": "abbreviation"}}
# and as packages are read, with SNOMED CT's fully specified name and synonym
_KINDS_READ = {(DESIGNATION_USAGE, "display"): "preferred",
               (TERM_KINDS, "abbreviation"): "abbreviation",
               (SNOMED, "900000000000003001"): "preferred",
               (SNOMED, "900000000000013009"): "synonym"}


class PackageError(LexError):
    """A file that is no package this library can read."""


@dataclass(frozen=True)
class Term:
    """One wording of a concept as it may stand in a text.  Its kind is ``preferred``,
    ``synonym`` or ``abbreviation``; an abbreviation is matched only as it is written."""

    value: str
    language: str = ""
    kind: str = "synonym"


@dataclass
class Concept:
    """One meaning in a package: its code, the name to show, its class, and its terms."""

    code: str
    display: str = ""
    cls: str = ""
    parent: str = ""
    terms: list[Term] = field(default_factory=list)


@dataclass
class Package:
    """A terminology or list in one version."""

    url: str
    version: str = ""
    name: str = ""
    title: str = ""
    publisher: str = ""
    copyright: str = ""
    language: str = ""
    redistributable: bool = False
    concepts: list[Concept] = field(default_factory=list)

    def sha256(self) -> str:
        """A fingerprint of the content, the same for the same package whatever its file."""
        canonical = json.dumps(to_codesystem(tidy(self)), ensure_ascii=False, sort_keys=True,
                               separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()

    @property
    def term_count(self) -> int:
        return sum(len(c.terms) for c in self.concepts)


def _clean(value: object) -> str:
    return " ".join(str(value or "").split())


def tidy_concept(concept: Concept, language: str = "") -> Concept:
    """A concept in its one form: its name is among its terms and is the preferred one, each
    term stands once, and the terms are in a fixed order (the name first)."""
    terms: dict[tuple[str, str], Term] = {}
    for term in concept.terms:
        value = _clean(term.value)
        key = (value, term.language or language)
        if value and key not in terms:
            terms[key] = Term(value, key[1], term.kind if term.kind in KINDS else "synonym")
    display = _clean(concept.display) or (next(iter(terms))[0] if terms else concept.code)
    named = next((key for key in terms if key[0] == display), (display, language))
    terms.pop(named, None)
    name = Term(display, named[1], "preferred")
    others = sorted(terms.values(), key=lambda t: (t.kind != "preferred", t.value, t.language))
    return Concept(concept.code, display, concept.cls, concept.parent, [name, *others])


def tidy(package: Package) -> Package:
    """A package with every concept in its one form (:func:`tidy_concept`), by code."""
    concepts = {c.code: tidy_concept(c, package.language) for c in reversed(package.concepts)}
    return Package(package.url, package.version, package.name, package.title, package.publisher,
                   package.copyright, package.language, package.redistributable,
                   [concepts[code] for code in sorted(concepts)])


# -- FHIR CodeSystem -------------------------------------------------------------------------------
def to_codesystem(package: Package) -> dict[str, Any]:
    """The package as a FHIR CodeSystem (R4)."""
    concepts = []
    for concept in tidy(package).concepts:
        entry: dict[str, Any] = {"code": concept.code, "display": concept.display}
        designations = []
        for term in concept.terms:
            if term.value == concept.display and term.language in ("", package.language):
                continue
            designation: dict[str, Any] = {}
            if term.language:
                designation["language"] = term.language
            if term.kind in _USES:
                designation["use"] = dict(_USES[term.kind])
            designation["value"] = term.value
            designations.append(designation)
        if designations:
            entry["designation"] = designations
        properties = []
        if concept.cls:
            properties.append({"code": "class", "valueString": concept.cls})
        if concept.parent:
            properties.append({"code": "parent", "valueCode": concept.parent})
        if properties:
            entry["property"] = properties
        concepts.append(entry)
    resource: dict[str, Any] = {
        "resourceType": "CodeSystem",
        "extension": [{"url": REDISTRIBUTABLE, "valueBoolean": package.redistributable}],
        "url": package.url, "version": package.version, "name": package.name,
        "title": package.title, "status": "active", "publisher": package.publisher,
        "copyright": package.copyright, "caseSensitive": True, "content": "complete",
        "count": len(concepts),
        "property": [{"code": "class", "type": "string"},
                     {"code": "parent", "uri": PARENT, "type": "code"}],
        "concept": concepts}
    if package.language:
        resource["language"] = package.language
    return {k: v for k, v in resource.items() if v not in ("", None)}


def _extension(element: dict[str, Any], url: str) -> bool | None:
    for extension in element.get("extension", []):
        if extension.get("url") == url and isinstance(extension.get("valueBoolean"), bool):
            return bool(extension["valueBoolean"])
    return None


def _property(concept: dict[str, Any], code: str) -> str:
    for entry in concept.get("property", []):
        if entry.get("code") == code:
            return _clean(next((v for k, v in entry.items() if k.startswith("value")), ""))
    return ""


def _concepts(entries: Iterable[dict[str, Any]], language: str, parent: str = "",
              ) -> Iterable[Concept]:
    """The concepts of a CodeSystem; nested ones get the concept above them as parent."""
    for entry in entries:
        code = _clean(entry.get("code"))
        if not code:
            continue
        terms = []
        for designation in entry.get("designation", []):
            use = designation.get("use") or {}
            kind = _KINDS_READ.get((str(use.get("system")), str(use.get("code"))), "synonym")
            terms.append(Term(_clean(designation.get("value")),
                              _clean(designation.get("language")), kind))
        yield Concept(code, _clean(entry.get("display")), _property(entry, "class"),
                      _property(entry, "parent") or parent, terms)
        yield from _concepts(entry.get("concept", []), language, code)


def from_codesystem(resource: dict[str, Any]) -> Package:
    """The package a FHIR CodeSystem describes."""
    if resource.get("resourceType") != "CodeSystem":
        raise PackageError("not-a-codesystem", "Die Datei ist kein FHIR CodeSystem.",
                           "The file is not a FHIR CodeSystem.")
    if not resource.get("url"):
        raise PackageError("no-url", "Dem CodeSystem fehlt die URL.",
                           "The CodeSystem has no URL.")
    if resource.get("content") == "not-present":
        raise PackageError("no-concepts",
                           "Das CodeSystem enthält keine Begriffe (content: not-present).",
                           "The CodeSystem holds no concepts (content: not-present).")
    language = _clean(resource.get("language"))
    return tidy(Package(
        url=_clean(resource["url"]), version=_clean(resource.get("version")),
        name=_clean(resource.get("name")), title=_clean(resource.get("title")),
        publisher=_clean(resource.get("publisher")), copyright=_clean(resource.get("copyright")),
        language=language, redistributable=bool(_extension(resource, REDISTRIBUTABLE)),
        concepts=list(_concepts(resource.get("concept", []), language))))


def read_codesystem(path: Path) -> Package:
    """Read a package from a FHIR CodeSystem file (JSON)."""
    try:
        resource = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise PackageError("unreadable", f"{path} lässt sich nicht als JSON lesen: {error}",
                           f"{path} cannot be read as JSON: {error}") from error
    if not isinstance(resource, dict):
        raise PackageError("not-a-codesystem", "Die Datei ist kein FHIR CodeSystem.",
                           "The file is not a FHIR CodeSystem.")
    return from_codesystem(resource)


def write_codesystem(package: Package, path: Path) -> None:
    """Write a package as a FHIR CodeSystem file (JSON, UTF-8)."""
    if not package.url:
        raise PackageError(
            "no-url",
            "Dem Paket fehlt die URI. Eine Liste bekommt sie von der Ablage, die sie aufnimmt.",
            "The package has no URI. A list gets one from the store that takes it in.")
    Path(path).write_text(json.dumps(to_codesystem(package), ensure_ascii=False, indent=1) + "\n",
                          encoding="utf-8")


# -- CSV -------------------------------------------------------------------------------------------
def read_csv(path: Path, url: str = "", version: str = "", title: str = "",
             language: str = "", redistributable: bool = False) -> Package:
    """Read a list made by hand: one row per term with the columns ``term``, ``code``,
    ``display``, ``class``, ``kind``, ``language``, of which only ``term`` is needed.  Rows
    without a code are concepts of their own, the term being the code.  A file without the
    header line is a plain list of terms.  Without ``url`` the package has none until a
    store takes it in."""
    path = Path(path)
    with path.open(encoding="utf-8-sig", newline="") as file:
        lines = [line for line in file if line.strip() and not line.startswith("#")]
    rows = list(csv.reader(lines))
    header = [cell.strip().lower() for cell in rows[0]] if rows else []
    if "term" in header:
        records = [dict(zip(header, row, strict=False)) for row in rows[1:]]
    else:
        records = [{"term": row[0]} for row in rows if row]
    concepts: dict[str, Concept] = {}
    for record in records:
        value = _clean(record.get("term"))
        if not value:
            continue
        code = _clean(record.get("code")) or value
        concept = concepts.setdefault(code, Concept(code))
        concept.display = concept.display or _clean(record.get("display"))
        concept.cls = concept.cls or _clean(record.get("class"))
        kind = _clean(record.get("kind")).lower()
        concept.terms.append(Term(value, _clean(record.get("language")) or language,
                                  kind if kind in KINDS else "synonym"))
    name = title or path.name.split(".")[0]
    return tidy(Package(url=url, version=version, name=name, title=name,
                        language=language, redistributable=redistributable,
                        concepts=list(concepts.values())))


def write_csv(package: Package, path: Path) -> int:
    """Write the terms of a package as CSV (comma, UTF-8, header line); returns the rows."""
    count = 0
    with Path(path).open("w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file, lineterminator="\n")
        writer.writerow(CSV_COLUMNS)
        for concept in tidy(package).concepts:
            for term in concept.terms:
                writer.writerow([term.value, concept.code, concept.display, concept.cls,
                                 term.kind, term.language])
                count += 1
    return count


def read(path: Path, **details: Any) -> Package:
    """Read a package from a file of either form, told apart by its ending."""
    path = Path(path)
    if path.suffix.lower() == ".json":
        return read_codesystem(path)
    if path.suffix.lower() in (".csv", ".txt"):
        return read_csv(path, **details)
    raise PackageError("unknown-ending",
                       f"Unbekannte Dateiendung: {path.name} (.json, .csv, .txt)",
                       f"Unknown file ending: {path.name} (.json, .csv, .txt)")
