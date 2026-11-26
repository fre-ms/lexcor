# SPDX-FileCopyrightText: 2026 fre.ms
# SPDX-License-Identifier: MIT
"""The command line of the core: packages and tagging.

    lexcor packages list
    lexcor packages import FILE [--url URL] [--version V] [--title T] [--redistributable]
    lexcor packages export URL FILE [--own-use]
    lexcor packages remove URL
    lexcor tag FILE [--package URL]... [--class CLASS]...

Names of commands and options are English; what a person reads is German or English
(``--language``).  With ``--json`` a command prints one JSON document on standard output and
nothing else.  lexterm and lexident bring these commands under their own names.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from lexcor import package as packages
from lexcor.language import LANGUAGES, say, set_language
from lexcor.package import PackageError
from lexcor.recognise import Mention, tag
from lexcor.store import Store, StoreError, open_store


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
        "ein Paket aufnehmen (FHIR CodeSystem als .json, Liste als .csv oder .txt)",
        "take a package in (FHIR CodeSystem as .json, a list as .csv or .txt)"))
    add.add_argument("file", type=Path)
    add.add_argument("--url", default="", help=say("URI einer Liste", "URI of a list"))
    add.add_argument("--version", default="", help=say("Version einer Liste", "version of a list"))
    add.add_argument("--title", default="", help=say("Titel einer Liste", "title of a list"))
    add.add_argument("--term-language", default="", help=say(
        "Sprache der Begriffe einer Liste", "language of a list's terms"))
    add.add_argument("--redistributable", action="store_true", help=say(
        "die Liste darf weitergegeben werden", "the list may be passed on"))
    out = actions.add_parser("export", help=say(
        "ein Paket schreiben (.json oder .csv)", "write a package (.json or .csv)"))
    out.add_argument("url")
    out.add_argument("file", type=Path)
    out.add_argument("--own-use", action="store_true", help=say(
        "auch ein Paket schreiben, das nicht weitergegeben werden darf (nur für Sie selbst)",
        "write a package that may not be passed on as well (for yourself only)"))
    gone = actions.add_parser("remove", help=say("ein Paket entfernen", "take a package out"))
    gone.add_argument("url")

    find = commands.add_parser("tag", help=say(
        "die Begriffe in einem Text finden", "find the terms in a text"))
    find.add_argument("file", type=Path)
    find.add_argument("--package", action="append", metavar="URL", help=say(
        "nur dieses Paket (mehrfach möglich)", "this package only (may be given several times)"))
    find.add_argument("--class", dest="classes", action="append", metavar="CLASS", help=say(
        "nur diese Klasse (mehrfach möglich)", "this class only (may be given several times)"))
    return parser


def _mention(mention: Mention) -> dict[str, Any]:
    return {"start": mention.start, "end": mention.end, "exact": mention.text,
            "assignments": [{"system": a.system, "version": a.version, "code": a.code,
                             "display": a.display, "class": a.cls, "term": a.term,
                             "match": {"kind": a.kind.value}} for a in mention.assignments]}


def _packages(store: Store, args: argparse.Namespace) -> tuple[dict[str, Any], str]:
    if args.action == "list":
        found = store.packages()
        lines = [f"{p.title or p.url}  {p.version}  {p.concepts} / {p.terms}  {p.url}"
                 + ("" if p.redistributable else say("  (nicht weitergebbar)",
                                                     "  (not to be passed on)"))
                 for p in found]
        text = "\n".join(lines) if lines else say("Die Ablage enthält kein Paket.",
                                                  "The store holds no package.")
        return {"packages": [vars(p) for p in found]}, text
    if args.action == "import":
        read = packages.read(args.file, url=args.url, version=args.version, title=args.title,
                             language=args.term_language, redistributable=args.redistributable) \
            if args.file.suffix.lower() != ".json" else packages.read(args.file)
        info = store.add_package(read)
        return {"package": vars(info)}, say(
            f"Aufgenommen: {info.title or info.url} mit {info.concepts} Begriffen in "
            f"{info.terms} Schreibweisen.",
            f"Taken in: {info.title or info.url} with {info.concepts} concepts in "
            f"{info.terms} wordings.")
    if args.action == "export":
        chosen = store.package(args.url)
        if not chosen.redistributable and not args.own_use:
            raise StoreError(say(
                "Dieses Paket darf nicht weitergegeben werden. Mit --own-use entsteht eine "
                "Kopie nur für Sie selbst.",
                "This package may not be passed on. With --own-use a copy for yourself only "
                "is written."))
        if args.file.suffix.lower() == ".csv":
            packages.write_csv(chosen, args.file)
        else:
            packages.write_codesystem(chosen, args.file)
        return {"file": str(args.file), "url": chosen.url}, say(
            f"Geschrieben: {args.file}", f"Written: {args.file}")
    removed = store.remove_package(args.url)
    if not removed:
        raise StoreError(say(f"Kein Paket mit der URL {args.url}.",
                             f"No package with the URL {args.url}."))
    return {"removed": args.url}, say(f"Entfernt: {args.url}", f"Removed: {args.url}")


def _tag(store: Store, args: argparse.Namespace) -> tuple[dict[str, Any], str]:
    text = args.file.read_text(encoding="utf-8")
    mentions = tag(text, store, args.package, args.classes)
    lines = [f"{m.start}-{m.end}\t{m.text}\t"
             + "; ".join(f"{a.code} {a.display} ({a.kind.value})" for a in m.assignments)
             for m in mentions]
    return {"mentions": [_mention(m) for m in mentions]}, "\n".join(lines)


def main(arguments: Sequence[str] | None = None, program: str = "lexcor") -> int:
    """Run one command; returns the exit code."""
    given = list(sys.argv[1:] if arguments is None else arguments)
    for i, word in enumerate(given[:-1]):  # the language also rules the help texts
        if word == "--language" and given[i + 1] in LANGUAGES:
            set_language(given[i + 1])
    args = _parser(program).parse_args(given)
    try:
        with open_store(args.store) as store:
            document, text = _packages(store, args) if args.command == "packages" \
                else _tag(store, args)
    except (PackageError, StoreError, OSError, UnicodeDecodeError) as error:
        if args.json:
            print(json.dumps({"error": str(error)}, ensure_ascii=False))
        else:
            print(str(error), file=sys.stderr)
        return 1
    print(json.dumps(document, ensure_ascii=False, indent=1) if args.json else text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
