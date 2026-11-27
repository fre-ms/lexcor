# SPDX-FileCopyrightText: 2026 fre.ms
# SPDX-License-Identifier: MIT
"""The annotation document: what recognition found in a text and what a person decided.

One JSON file beside the text (design, section 5).  It holds every mention with its
assignments, and for each assignment where it came from (a machine with its recogniser, or
a person) and how it stands (proposed, confirmed, rejected).  The rules:

- a person never deletes what a machine proposed: they reject it, it stays in the file, and
  a later run does not bring it back as a proposal;
- what a person confirmed or added stays, whatever a later run finds;
- a run replaces only what the same recogniser proposed and nobody has decided yet;
- a mention that is not found again after the text has changed is kept in a list of its
  own (``lost``), not dropped;
- the same text, the same mentions and the same decisions give the same file, byte for
  byte.  The library never reads the clock or asks who is at the keyboard: who decided and
  when is what the caller says, and may be left out;
- the document names every package the last run applied, with its version, the
  fingerprint of its content and the fingerprint of the user's changes in force for it, and
  keeps the packages of earlier runs that decisions in it still come from; a run can be
  repeated exactly as recorded, and refuses where a package is no longer what it was
  (:func:`differences`).

Positions count Unicode code points from the start of the text.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lexcor.language import LexError
from lexcor.package import Package, supplemented, tidy
from lexcor.recognise import Assignment, Kind, Mention
from lexcor.store import Store

FORMAT = 1
PROPOSED, CONFIRMED, REJECTED = "proposed", "confirmed", "rejected"
STATUSES = (PROPOSED, CONFIRMED, REJECTED)
MACHINE, PERSON = "machine", "person"
CONTEXT = 16  # how many code points before and after a mention are kept to find it again
# how a package listed in a document was used; a package with none had its terms applied in
# the last run
NAMES = "names"  # its names and classes were read for the codes of a supplement applied
EARLIER = "earlier"  # applied in an earlier run; decisions in the document come from it
# what a document says of a package, in this order
PACKAGE_KEYS = ("url", "version", "sha256", "title", "supplements", "overlay", "use")


class AnnotationError(LexError):
    """A file that is no annotation document, or a decision about something it does not hold."""


@dataclass
class Assigned:
    """One assignment of a mention as the document holds it: the concept, how the text
    spells it, where the assignment came from and how it stands."""

    system: str
    version: str
    code: str
    display: str
    cls: str
    term: str = ""
    kind: str = Kind.AS_WRITTEN.value
    score: float | None = None
    by: str = MACHINE
    recogniser: str = ""
    status: str = PROPOSED
    decided_by: str = ""
    decided_at: str = ""
    package: str = ""
    package_version: str = ""
    terminal: bool | None = None

    @property
    def key(self) -> tuple[str, str, str]:
        """What makes two assignments of one mention the same: system, code and class."""
        return (self.system, self.code, self.cls)

    @property
    def source(self) -> tuple[str, str]:
        """The package a machine's assignment came from, and its version: the code system
        itself, or the supplement named."""
        return (self.package or self.system, self.package_version if self.package
                else self.version)

    @property
    def decided(self) -> bool:
        """Whether a person has decided it, or made it."""
        return self.status != PROPOSED or self.by == PERSON


@dataclass
class Noted:
    """One mention as the document holds it: its place, its wording with a little of what
    stands around it, and its assignments.  ``by`` is "person" for a place a person marked."""

    id: str
    start: int
    end: int
    exact: str
    prefix: str = ""
    suffix: str = ""
    by: str = MACHINE
    assignments: list[Assigned] = field(default_factory=list)

    @property
    def kept(self) -> bool:
        """Whether a later run must keep it: a person marked it or decided one of its
        assignments."""
        return self.by == PERSON or any(a.decided for a in self.assignments)


def digest(text: str) -> str:
    """The fingerprint of a text as the document names it."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _listing(url: str, version: str, sha256: str, title: str, supplements: str,
             overlay: str, use: str) -> dict[str, str]:
    entry = {"url": url, "version": version, "sha256": sha256}
    for name, value in (("title", title), ("supplements", supplements), ("overlay", overlay),
                        ("use", use)):
        if value:
            entry[name] = value
    return entry


def packages_of(store: Store, urls: Iterable[str] | None = None) -> list[dict[str, str]]:
    """The packages of a store a run applies (all, or those with these URLs) as a document
    names them: each with its version, the fingerprint of its content (``sha256``) and of
    the user's changes in force for its terms (``overlay``, where there are any), and for a
    supplement the code system it supplements.  A code system whose names a chosen
    supplement reads without being chosen itself is named with ``use`` "names"."""
    wanted = None if urls is None else set(urls)
    held = store.packages()
    chosen = [p for p in held if wanted is None or p.url in wanted]
    named = {supplemented(p.supplements)[0] for p in chosen if p.supplements}
    taken = {p.url for p in chosen}
    read = [p for p in held if p.url in named and p.url not in taken and not p.supplements]
    return sorted((_listing(p.url, p.version, p.sha256, p.title, p.supplements,
                            store.overlay_digest(p.url), use)
                   for p, use in [(p, "") for p in chosen] + [(p, NAMES) for p in read]),
                  key=lambda e: (e["url"], e["version"]))


def listed(packages: Iterable[Package]) -> list[dict[str, str]]:
    """Packages a run applies without a store (those a library brings) as a document names
    them: as :func:`packages_of` does; there is no overlay, and every package given has its
    terms applied."""
    return sorted((_listing(p.url, p.version, p.sha256(), p.title, p.supplements, "", "")
                   for p in (tidy(p) for p in packages)), key=lambda e: (e["url"], e["version"]))


_COMPARED = ("version", "sha256", "overlay", "supplements", "use")


def differences(recorded: Iterable[Mapping[str, str]],
                current: Iterable[Mapping[str, str]]) -> list[tuple[str, str]]:
    """What stands in the way of repeating a run exactly: how the packages it applied
    (``recorded``, from its document) differ from those a run would apply now
    (``current``).  Each difference is a URL with what differs: "missing" (no longer
    there), "added" (there now, not then), or the first of "version", "sha256" (the
    content), "overlay" (the user's changes), "supplements" or "use" that differs.  Packages
    of earlier runs are no part of the comparison.  Empty: the run can be repeated."""
    then = {p.get("url", ""): p for p in recorded if p.get("use") != EARLIER}
    now = {p.get("url", ""): p for p in current if p.get("use") != EARLIER}
    found = [(url, "missing") for url in sorted(set(then) - set(now))]
    found += [(url, "added") for url in sorted(set(now) - set(then))]
    for url in sorted(set(then) & set(now)):
        differing = [k for k in _COMPARED if then[url].get(k, "") != now[url].get(k, "")]
        if differing:
            found.append((url, differing[0]))
    return sorted(found)


@dataclass
class Annotation:
    """The annotation document of one text."""

    made_with: str = ""
    sha256: str = ""
    length: int = 0
    packages: list[dict[str, str]] = field(default_factory=list)
    settings: dict[str, Any] = field(default_factory=dict)
    mentions: list[Noted] = field(default_factory=list)
    lost: list[Noted] = field(default_factory=list)

    # -- a run of a recogniser ---------------------------------------------------------------------
    def update(self, text: str, found: Iterable[Mention], recogniser: str,
               made_with: str = "", packages: Iterable[Mapping[str, str]] | None = None,
               settings: Mapping[str, Any] | None = None) -> Annotation:
        """Take in what a recogniser found in the text.  What it proposed before and nobody
        decided is replaced; decisions and what a person made stay; a text that has changed
        moves the mentions to where their wording stands now."""
        if self.sha256 and self.sha256 != digest(text):
            self._follow(text)
        self.sha256, self.length = digest(text), len(text)
        self.made_with = made_with or self.made_with
        if settings is not None:
            self.settings = dict(settings)
        for noted in self.mentions:
            noted.assignments = [a for a in noted.assignments
                                 if a.decided or a.recogniser != recogniser]
        if packages is not None:
            self._list(packages)
        places = {(n.start, n.end): n for n in self.mentions}
        for mention in found:
            place = places.get((mention.start, mention.end))
            if place is None:
                place = Noted("", mention.start, mention.end, mention.text)
                places[(mention.start, mention.end)] = place
                self.mentions.append(place)
            held = {a.key for a in place.assignments}
            place.assignments += [_proposed(a, recogniser) for a in mention.assignments
                                  if (a.system, a.code, a.cls) not in held]
        self.mentions = [n for n in self.mentions if n.assignments or n.by == PERSON]
        self._settle(text)
        return self

    def _list(self, packages: Iterable[Mapping[str, str]]) -> None:
        """The packages of this run, and those of earlier runs that decisions still come
        from (marked as such): every assignment of a machine names a package listed."""
        now = [dict(p) for p in packages]
        applied = {(p.get("url", ""), p.get("version", "")) for p in now
                   if p.get("use") != NAMES}
        used = {a.source for n in self.mentions + self.lost for a in n.assignments
                if a.by == MACHINE}
        kept = [dict(p, use=EARLIER) for p in self.packages
                if (p.get("url", ""), p.get("version", "")) in used - applied]
        self.packages = sorted(now + kept, key=lambda p: (p.get("url", ""), p.get("version", ""),
                                                          p.get("use", "")))

    def _follow(self, text: str) -> None:
        """After the text has changed: every mention where its wording stands now, and those
        whose wording is gone into ``lost``."""
        still: list[Noted] = []
        for noted in self.mentions:
            start = _found_again(text, noted)
            if start is None:
                self.lost.append(noted)
            else:
                noted.start, noted.end = start, start + len(noted.exact)
                still.append(noted)
        self.mentions = still

    def _settle(self, text: str) -> None:
        """Mentions in reading order, each with its surroundings, a number of its own and its
        assignments in a fixed order."""
        self.mentions.sort(key=lambda n: (n.start, n.end))
        taken = {n.id for n in self.mentions + self.lost if n.id}
        number = 0
        for noted in self.mentions:
            noted.exact = text[noted.start:noted.end]
            noted.prefix = text[max(0, noted.start - CONTEXT):noted.start]
            noted.suffix = text[noted.end:noted.end + CONTEXT]
            noted.assignments.sort(key=lambda a: a.key)
            while not noted.id:
                number += 1
                if f"m{number}" not in taken:
                    noted.id = f"m{number}"
                    taken.add(noted.id)

    # -- a person's decisions ----------------------------------------------------------------------
    def mention(self, mention: str) -> Noted:
        """The mention with this id."""
        for noted in self.mentions:
            if noted.id == mention:
                return noted
        raise AnnotationError("no-mention", f"Keine Erwähnung mit der Kennung {mention}.",
                              f"No mention with the id {mention}.")

    def _decide(self, mention: str, system: str, code: str, status: str, by: str,
                at: str) -> list[Assigned]:
        chosen = [a for a in self.mention(mention).assignments
                  if (not system or a.system == system) and (not code or a.code == code)]
        if not chosen:
            raise AnnotationError(
                "no-assignment", f"Die Erwähnung {mention} hat keine solche Zuordnung.",
                f"The mention {mention} has no such assignment.")
        for assigned in chosen:
            assigned.status, assigned.decided_by, assigned.decided_at = status, by, at
        return chosen

    def confirm(self, mention: str, system: str = "", code: str = "", by: str = "",
                at: str = "") -> list[Assigned]:
        """A person confirms an assignment of a mention (all of them when neither ``system``
        nor ``code`` is given).  ``by`` and ``at`` say who and when, if the caller wants that
        kept."""
        return self._decide(mention, system, code, CONFIRMED, by, at)

    def reject(self, mention: str, system: str = "", code: str = "", by: str = "",
               at: str = "") -> list[Assigned]:
        """A person rejects an assignment of a mention.  It stays in the document, and a
        later run does not propose it again."""
        return self._decide(mention, system, code, REJECTED, by, at)

    def reopen(self, mention: str, system: str = "", code: str = "") -> list[Assigned]:
        """Take a decision back: the assignment is a proposal again."""
        return self._decide(mention, system, code, PROPOSED, "", "")

    def confirm_all(self, system: str, by: str = "", at: str = "") -> int:
        """Confirm every assignment of one package that nobody has decided yet; how many."""
        count = 0
        for noted in self.mentions:
            for assigned in noted.assignments:
                if assigned.system == system and assigned.status == PROPOSED:
                    assigned.status, assigned.decided_by, assigned.decided_at = CONFIRMED, by, at
                    count += 1
        return count

    def add(self, text: str, start: int, end: int, assignment: Assignment | None = None,
            by: str = "", at: str = "") -> Noted:
        """A person marks a place, with an assignment or without one yet.  The assignment is
        the person's and confirmed; a place that is marked already gains it."""
        if digest(text) != self.sha256 and self.sha256:
            raise AnnotationError("other-text", "Das ist nicht der Text dieses Dokuments.",
                                  "This is not the text of this document.")
        if not 0 <= start < end <= len(text):
            raise AnnotationError("no-place", "Diese Stelle gibt es im Text nicht.",
                                  "There is no such place in the text.")
        self.sha256, self.length = digest(text), len(text)
        noted = next((n for n in self.mentions if (n.start, n.end) == (start, end)), None)
        if noted is None:
            noted = Noted("", start, end, text[start:end], by=PERSON)
            self.mentions.append(noted)
        if assignment is not None:
            noted.assignments = [a for a in noted.assignments
                                 if a.key != (assignment.system, assignment.code, assignment.cls)]
            noted.assignments.append(Assigned(
                assignment.system, assignment.version, assignment.code, assignment.display,
                assignment.cls, assignment.term, assignment.kind.value, assignment.score, PERSON,
                "", CONFIRMED, by, at))
        self._settle(text)
        return noted

    # -- what a caller reads -----------------------------------------------------------------------
    def assignments(self, status: str | None = CONFIRMED) -> list[tuple[Noted, Assigned]]:
        """Every assignment with its mention, in reading order: the confirmed ones, those of
        another status, or with None all."""
        return [(noted, assigned) for noted in self.mentions for assigned in noted.assignments
                if status is None or assigned.status == status]

    def positions(self) -> list[dict[str, Any]]:
        """Places, classes and statuses without any wording: what may go into a report or a
        log when the words themselves are someone's data."""
        return [{"id": n.id, "start": n.start, "end": n.end,
                 "assignments": [{"class": a.cls, "status": a.status, "by": a.by}
                                 for a in n.assignments]} for n in self.mentions]

    # -- the file ----------------------------------------------------------------------------------
    def document(self) -> dict[str, Any]:
        """The document as JSON data, every key in its fixed place."""
        document: dict[str, Any] = {
            "lexcor": FORMAT, "made_with": self.made_with,
            "text": {"sha256": self.sha256, "length": self.length},
            "packages": [{k: p[k] for k in PACKAGE_KEYS if k in p} for p in self.packages],
            "settings": {k: self.settings[k] for k in sorted(self.settings)},
            "mentions": [_mention(n) for n in self.mentions]}
        if self.lost:
            document["lost"] = [_mention(n) for n in self.lost]
        return document

    def dumps(self) -> str:
        """The document as the text of its file: the same content, the same bytes."""
        return json.dumps(self.document(), ensure_ascii=False, indent=1) + "\n"

    def save(self, path: str | Path) -> None:
        """Write the document (UTF-8)."""
        Path(path).write_text(self.dumps(), encoding="utf-8", newline="\n")


def annotate(text: str, found: Iterable[Mention], recogniser: str, made_with: str = "",
             packages: Iterable[Mapping[str, str]] | None = None,
             settings: Mapping[str, Any] | None = None) -> Annotation:
    """A new annotation document for what a recogniser found in a text."""
    return Annotation().update(text, found, recogniser, made_with, packages or (), settings or {})


def _proposed(assignment: Assignment, recogniser: str) -> Assigned:
    return Assigned(assignment.system, assignment.version, assignment.code, assignment.display,
                    assignment.cls, assignment.term, assignment.kind.value, assignment.score,
                    MACHINE, recogniser, package=assignment.package,
                    package_version=assignment.package_version, terminal=assignment.terminal)


def _found_again(text: str, noted: Noted) -> int | None:
    """Where a mention's wording stands in a changed text: at its old place if it is still
    there, else where most of what stood around it stands too, the nearest first."""
    if text[noted.start:noted.end] == noted.exact:
        return noted.start
    best: tuple[int, int, int] | None = None
    start = text.find(noted.exact)
    while start >= 0 and noted.exact:
        end = start + len(noted.exact)
        around = _shared(text[:start][::-1], noted.prefix[::-1]) + _shared(text[end:], noted.suffix)
        rank = (-around, abs(start - noted.start), start)
        if best is None or rank < best:
            best = rank
        start = text.find(noted.exact, start + 1)
    return None if best is None else best[2]


def _shared(one: str, other: str) -> int:
    """How many code points two strings have in common from their start."""
    count = 0
    for a, b in zip(one, other, strict=False):
        if a != b:
            break
        count += 1
    return count


# -- JSON ------------------------------------------------------------------------------------------
def _assignment(assigned: Assigned) -> dict[str, Any]:
    match: dict[str, Any] = {"kind": assigned.kind}
    if assigned.score is not None:
        match["score"] = assigned.score
    origin = {"by": assigned.by}
    if assigned.recogniser:
        origin["recogniser"] = assigned.recogniser
    if assigned.package:
        origin["package"] = assigned.package
        origin["package_version"] = assigned.package_version
    document: dict[str, Any] = {
        "system": assigned.system, "version": assigned.version, "code": assigned.code,
        "display": assigned.display, "class": assigned.cls}
    if assigned.terminal is not None:
        document["terminal"] = assigned.terminal
    document |= {"term": assigned.term, "match": match, "origin": origin,
                 "status": assigned.status}
    decision = {k: v for k, v in (("by", assigned.decided_by), ("at", assigned.decided_at)) if v}
    if decision:
        document["decision"] = decision
    return document


def _mention(noted: Noted) -> dict[str, Any]:
    document: dict[str, Any] = {
        "id": noted.id, "start": noted.start, "end": noted.end, "exact": noted.exact,
        "prefix": noted.prefix, "suffix": noted.suffix}
    if noted.by == PERSON:
        document["by"] = PERSON
    document["assignments"] = [_assignment(a) for a in noted.assignments]
    return document


def _read_assignment(data: Mapping[str, Any]) -> Assigned:
    match, origin = data.get("match") or {}, data.get("origin") or {}
    decision = data.get("decision") or {}
    status = str(data.get("status", PROPOSED))
    if status not in STATUSES or str(match.get("kind", Kind.AS_WRITTEN.value)) not in {
            k.value for k in Kind}:
        raise ValueError("status or kind")
    score, end = match.get("score"), data.get("terminal")
    if end is not None and not isinstance(end, bool):
        raise ValueError("terminal")
    return Assigned(
        str(data["system"]), str(data.get("version", "")), str(data["code"]),
        str(data.get("display", "")), str(data.get("class", "")), str(data.get("term", "")),
        str(match.get("kind", Kind.AS_WRITTEN.value)), None if score is None else float(score),
        str(origin.get("by", MACHINE)), str(origin.get("recogniser", "")), status,
        str(decision.get("by", "")), str(decision.get("at", "")),
        str(origin.get("package", "")), str(origin.get("package_version", "")), end)


def _read_mention(data: Mapping[str, Any]) -> Noted:
    return Noted(str(data["id"]), int(data["start"]), int(data["end"]), str(data["exact"]),
                 str(data.get("prefix", "")), str(data.get("suffix", "")),
                 str(data.get("by", MACHINE)),
                 [_read_assignment(a) for a in data.get("assignments", [])])


def from_document(document: Any) -> Annotation:
    """The annotation a document (JSON data) holds."""
    try:
        if document.get("lexcor") != FORMAT:
            raise ValueError("format")
        text = document.get("text") or {}
        return Annotation(
            str(document.get("made_with", "")), str(text.get("sha256", "")),
            int(text.get("length", 0)), [dict(p) for p in document.get("packages", [])],
            dict(document.get("settings") or {}),
            [_read_mention(m) for m in document.get("mentions", [])],
            [_read_mention(m) for m in document.get("lost", [])])
    except (AttributeError, KeyError, TypeError, ValueError) as error:
        raise AnnotationError("not-an-annotation", "Das ist kein Annotationsdokument.",
                              "This is not an annotation document.") from error


def loads(text: str) -> Annotation:
    """The annotation the text of a file holds."""
    try:
        document = json.loads(text)
    except ValueError as error:
        raise AnnotationError("not-an-annotation", "Das ist kein Annotationsdokument.",
                              "This is not an annotation document.") from error
    return from_document(document)


def load(path: str | Path) -> Annotation:
    """Read an annotation document from a file."""
    return loads(Path(path).read_text(encoding="utf-8"))
