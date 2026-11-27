# SPDX-FileCopyrightText: 2026 fre.ms
# SPDX-License-Identifier: MIT
"""The command line of the core: packages and tagging.

    lexcor packages list
    lexcor packages import FILE [--url URL] [--version V] [--title T] [--redistributable]
                                [--supplements URL]
    lexcor packages export PACKAGE FILE [--own-use]
    lexcor packages remove PACKAGE
    lexcor tag FILE [--package PACKAGE]... [--class CLASS]... [--nested] [--annotation FILE]

A package is named by its URL or, where only one is called so, by its title.  Names of
commands and options are English; what a person reads is German or English
(``--language``).  With ``--json`` a command prints one JSON document on standard output and
nothing else.  lexterm and lexident bring these commands under their own names: :func:`main`
takes the program's name, the library whose store is the default, a table of spellings and,
where a library recognises by rules of its own, its tagger.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Any

from lexcor import package as packages
from lexcor.annotation import Annotation, load, packages_of
from lexcor.language import LANGUAGES, LexError, say, set_language
from lexcor.recognise import Mention, Variants, tag
from lexcor.store import PackageInfo, Store, StoreError, open_store

# what a library hands over to recognise by rules of its own: text, store, packages, classes,
# and whether a package's shorter terms inside its longer ones are wanted
Tagger = Callable[[str, Store, Iterable[str] | None, Iterable[str] | None, bool], list[Mention]]


def _parser(program: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=program, description=say(
        "Pakete von Begriffen verwalten und Begriffe in Texten finden.",
        "Manage packages of terms and find terms in texts."))
    parser.add_argument("--store", type=Path, help=say(
        "Datei der Ablage (sonst die Ablage im Datenordner)",
        "file of the store (else the store in the data folder)"))
    parser.add_argument("--language", choices=LANGUAGES, help=say(
        "Sprache der Meldungen", "language of the messages"))
    parser.add_argument("--json", action="store_true", help=say(
        "ein JSON-Dokument auf der Standardausgabe, sonst nichts",
        "one JSON document on standard output and nothing else"))
    commands = parser.add_subparsers(dest="command", required=True)

    group = commands.add_parser("packages", help=say("Pakete verwalten", "manage packages"))
    actions = group.add_subparsers(dest="action", required=True)
    actions.add_parser("list", help=say("Pakete der Ablage zeigen", "show the store's packages"))
    add = actions.add_parser("import", help=say(
        "ein Paket aufnehmen (FHIR CodeSystem als .json, FHIR-Paket als .tgz, Liste als .csv"
        " oder .txt)",
        "take a package in (FHIR CodeSystem as .json, FHIR package as .tgz, a list as .csv or"
        " .txt)"))
    add.add_argument("file", type=Path)
    add.add_argument("--url", default="", help=say(
        "URI einer Liste (sonst vergibt die Ablage eine eigene)",
        "URI of a list (else the store gives it one of its own)"))
    add.add_argument("--version", default="", help=say("Version einer Liste", "version of a list"))
    add.add_argument("--title", default="", help=say("Titel einer Liste", "title of a list"))
    add.add_argument("--term-language", default="", help=say(
        "Sprache der Begriffe einer Liste", "language of a list's terms"))
    add.add_argument("--redistributable", action="store_true", help=say(
        "die Liste darf weitergegeben werden", "the list may be passed on"))
    add.add_argument("--supplements", default="", metavar="URL", help=say(
        "die Liste ist eine Ergänzung: weitere Bezeichnungen zu den Kodes dieses CodeSystems",
        "the list is a supplement: further wordings for the codes of this code system"))
    out = actions.add_parser("export", help=say(
        "ein Paket schreiben (.json oder .csv)", "write a package (.json or .csv)"))
    out.add_argument("package", help=say("URL oder Titel", "URL or title"))
    out.add_argument("file", type=Path)
    out.add_argument("--own-use", action="store_true", help=say(
        "auch ein Paket schreiben, das nicht weitergegeben werden darf (nur für Sie selbst)",
        "write a package that may not be passed on as well (for yourself only)"))
    gone = actions.add_parser("remove", help=say("ein Paket entfernen", "take a package out"))
    gone.add_argument("package", help=say("URL oder Titel", "URL or title"))

    find = commands.add_parser("tag", help=say(
        "die Begriffe in einem Text finden", "find the terms in a text"))
    find.add_argument("file", type=Path)
    find.add_argument("--package", action="append", metavar="PACKAGE", help=say(
        "nur dieses Paket, URL oder Titel (mehrfach möglich)",
        "this package only, URL or title (may be given several times)"))
    find.add_argument("--class", dest="classes", action="append", metavar="CLASS", help=say(
        "nur diese Klasse (mehrfach möglich)", "this class only (may be given several times)"))
    find.add_argument("--nested", action="store_true", help=say(
        "auch die kürzeren Begriffe eines Pakets innerhalb seiner längeren",
        "a package's shorter terms inside its longer ones as well"))
    find.add_argument("--annotation", "-o", type=Path, metavar="FILE", help=say(
        "das Annotationsdokument schreiben; ein vorhandenes wird fortgeschrieben, was ein"
        " Mensch entschieden hat, bleibt",
        "write the annotation document; one that is there is updated, and what a person"
        " decided stays"))
    return parser


def _mention(mention: Mention) -> dict[str, Any]:
    return {"start": mention.start, "end": mention.end, "exact": mention.text,
            "assignments": [{"system": a.system, "version": a.version, "code": a.code,
                             "display": a.display, "class": a.cls, "term": a.term,
                             "match": {"kind": a.kind.value}} for a in mention.assignments]}


def fit_notes(store: Store, info: PackageInfo) -> tuple[dict[str, Any] | None, list[str]]:
    """What to tell about a supplement taken in: what it supplements and how it fits the
    code system in the store, as data and as sentences; nothing for any other package."""
    fit = store.fit(info.url)
    if fit is None:
        return None, []
    lines = [say(f"Ergänzt {fit.system}.", f"Supplements {fit.system}.")]
    if fit.held is None:
        lines.append(say(
            "Dieses CodeSystem liegt nicht in der Ablage: Die Kodes werden ohne ihre Namen"
            " gegeben.",
            "The store does not hold that code system: the codes are given without their"
            " names."))
    if fit.held is not None and fit.wanted and fit.wanted != fit.held:
        lines.append(say(
            f"Die Ergänzung nennt Version {fit.wanted}, in der Ablage liegt {fit.held}.",
            f"The supplement names version {fit.wanted}, the store holds {fit.held}."))
    if fit.unknown:
        lines.append(say(
            f"{fit.unknown} Kodes der Ergänzung kennt das CodeSystem der Ablage nicht.",
            f"{fit.unknown} codes of the supplement are unknown to the code system the store"
            " holds."))
    return vars(fit), lines


def _packages(store: Store, args: argparse.Namespace) -> tuple[dict[str, Any], str]:
    if args.action == "list":
        found = store.packages()
        lines = [f"{p.title or p.url}  {p.version}  {p.concepts} / {p.terms}  {p.url}"
                 + (say(f"  (ergänzt {p.supplements})", f"  (supplements {p.supplements})")
                    if p.supplements else "")
                 + ("" if p.redistributable else say("  (nicht weitergebbar)",
                                                     "  (not to be passed on)"))
                 for p in found]
        text = "\n".join(lines) if lines else say("Die Ablage enthält kein Paket.",
                                                  "The store holds no package.")
        return {"packages": [vars(p) for p in found]}, text
    if args.action == "import" and packages.is_fhir_package(args.file):
        return _import_fhir_package(store, args.file)
    if args.action == "import":
        read = packages.read(args.file, url=args.url, version=args.version, title=args.title,
                             language=args.term_language, redistributable=args.redistributable,
                             supplements=args.supplements) \
            if args.file.suffix.lower() != ".json" else packages.read(args.file)
        info = store.add_package(read)
        fit, notes = fit_notes(store, info)
        document = {"package": vars(info)} | ({} if fit is None else {"fit": fit})
        return document, "\n".join([say(
            f"Aufgenommen: {info.title or info.url} mit {info.concepts} Begriffen in "
            f"{info.terms} Schreibweisen.",
            f"Taken in: {info.title or info.url} with {info.concepts} concepts in "
            f"{info.terms} wordings."), *notes])
    if args.action == "export":
        chosen = store.package(store.find(args.package))
        if not chosen.redistributable and not args.own_use:
            raise StoreError(
                "not-redistributable",
                "Dieses Paket darf nicht weitergegeben werden. Mit --own-use entsteht eine "
                "Kopie nur für Sie selbst.",
                "This package may not be passed on. With --own-use a copy for yourself only "
                "is written.")
        if args.file.suffix.lower() == ".csv":
            packages.write_csv(chosen, args.file)
        else:
            packages.write_codesystem(chosen, args.file)
        return {"file": str(args.file), "url": chosen.url}, say(
            f"Geschrieben: {args.file}", f"Written: {args.file}")
    url = store.find(args.package)
    store.remove_package(url)
    return {"removed": url}, say(f"Entfernt: {url}", f"Removed: {url}")


def _import_fhir_package(store: Store, file: Path) -> tuple[dict[str, Any], str]:
    """Take in every CodeSystem of a FHIR package and say which were left out and why."""
    found, skipped = packages.read_fhir_package(file)
    infos = [store.add_package(package) for package in found]
    reasons = {"not-present": say("nennt seine Begriffe nicht selbst",
                                  "does not hold its concepts"),
               "supplement": say("ist eine Ergänzung, die nicht sagt, was sie ergänzt",
                                 "is a supplement that does not say what it supplements"),
               "example": say("ist nur ein Beispiel", "is an example only"),
               "empty": say("enthält keine Begriffe", "holds no concepts")}
    lines, fits = [], {}
    for i in infos:  # every package is in before a supplement is asked how it fits
        lines.append(say(f"Aufgenommen: {i.title or i.url} mit {i.concepts} Begriffen in"
                         f" {i.terms} Schreibweisen.",
                         f"Taken in: {i.title or i.url} with {i.concepts} concepts in {i.terms}"
                         " wordings."))
        fit, notes = fit_notes(store, i)
        lines += notes
        if fit is not None:
            fits[i.url] = fit
    lines += [say(f"Ausgelassen: {url} ({reasons[reason]}).",
                  f"Left out: {url} ({reasons[reason]}).") for url, reason in skipped]
    document: dict[str, Any] = {
        "packages": [vars(i) for i in infos],
        "skipped": [{"url": url, "reason": reason} for url, reason in skipped]}
    if fits:
        document["fit"] = fits
    return document, "\n".join(lines)


def _tag(store: Store, args: argparse.Namespace, tagger: Tagger,
         made_with: str) -> tuple[dict[str, Any], str]:
    text = args.file.read_text(encoding="utf-8")
    chosen = None if args.package is None else [store.find(p) for p in args.package]
    mentions = tagger(text, store, chosen, args.classes, args.nested)
    if args.annotation is not None:
        document = load(args.annotation) if args.annotation.exists() else Annotation()
        document.update(text, mentions, made_with, made_with, packages_of(store, chosen),
                        {"packages": sorted(chosen or []), "classes": sorted(args.classes or []),
                         "nested": args.nested})
        document.save(args.annotation)
    lines = [f"{m.start}-{m.end}\t{m.text}\t"
             + "; ".join(" ".join(filter(None, (a.code, a.display, f"({a.kind.value})")))
                         for a in m.assignments)
             for m in mentions]
    return {"mentions": [_mention(m) for m in mentions]}, "\n".join(lines)


def main(arguments: Sequence[str] | None = None, program: str = "lexcor",
         library: str = "lexcor", variants: Variants = (), tagger: Tagger | None = None,
         version: str | None = None) -> int:
    """Run one command; returns the exit code.  A library built on the core calls this with
    its own name and version (``program``, ``version``; an annotation document names them as
    what made it), ``library`` for the store it keeps in the user's data folder, its table
    of spellings that stand side by side (``variants``) or, where it recognises by rules of
    its own, the function that tags (``tagger``)."""
    from lexcor import __version__

    made_with = f"{program} {version or __version__}"
    if tagger is None:
        def tagger(text: str, store: Store, packages: Iterable[str] | None,
                   classes: Iterable[str] | None, nested: bool) -> list[Mention]:
            return tag(text, store, packages, classes, variants, nested=nested)
    given = list(sys.argv[1:] if arguments is None else arguments)
    for i, word in enumerate(given[:-1]):  # the language also rules the help texts
        if word == "--language" and given[i + 1] in LANGUAGES:
            set_language(given[i + 1])
    args = _parser(program).parse_args(given)
    try:
        with open_store(args.store, library) as store:
            document, text = _packages(store, args) if args.command == "packages" \
                else _tag(store, args, tagger, made_with)
    except (LexError, OSError, UnicodeDecodeError) as error:
        if not isinstance(error, LexError):  # a file that is not there or not text
            error = LexError("unreadable", str(error), str(error))
        if args.json:
            print(json.dumps({"error": error.kind, "message": error.messages},
                             ensure_ascii=False))
        else:
            print(str(error), file=sys.stderr)
        return 1
    print(json.dumps(document, ensure_ascii=False, indent=1) if args.json else text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
