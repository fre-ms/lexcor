<!-- SPDX-FileCopyrightText: 2026 fre.ms -->
<!-- SPDX-License-Identifier: MIT -->

# lexcor

The common core of lexterm (medical terms) and lexident (identifying information):
two libraries that find words of a list in German text and let a person check the result.

What the two share is here, once: packages of terms as FHIR CodeSystems with their licence, a store, recognition
in four stages with the kinds of match German needs, an annotation document that records who decided what, the
windows for review and for the packages, and the interfaces to a terminology server. lexcor knows nothing
medical and nothing about persons.

The project is at its second stage (0.2): the windows for review are still to come. The [design](docs/design.md) says what is to be built
and in which order. What is here:

- `lexcor.package`: packages as FHIR CodeSystem (R4, JSON), read from FHIR packages (`.tgz`) as well, and as
  CSV; a package may be a supplement (FHIR `content: supplement`), further wordings for the codes of another
  code system, which then code in that system;
- `lexcor.store`: one SQLite file with the packages and a user's changes to them, kept as an overlay;
- `lexcor.recognise`: `tag`, recognition in four stages with the kinds of match (as written, spelling variant,
  inflected);
- `lexcor.cover`: `covers`, the generous question whether a span is vocabulary at all;
- `lexcor.annotation`: the annotation document, one JSON file beside a text with what was found and what a
  person decided (confirm, reject, mark a place); a later run keeps every decision, and a text that has changed
  keeps its mentions where their wording still stands;
- a command line: `lexcor packages list|import|export|remove`, `lexcor tag FILE [--annotation FILE]`, with
  `--json` and `--language de|en`.

Every run chooses its packages (`--package`, `--without`; `packages=` in the library), and the annotation
document records exactly what it applied: each package with its version, the fingerprint of its content and of
the user's changes to it, and the program that ran. `lexcor tag FILE --annotation DOC --repeat` runs again as
the document records it, or refuses and says which package is no longer the same.

The package window (`lexcor packages window`, or `lexcor.window.PackageWindow` in a program of one's own) shows
the packages of a store with a box to tick for the runs that follow, and takes files in, writes packages out
and takes them out. It needs PySide6: `pip install "lexcor[window]"`. Without a window, `lexcor packages
on|off PACKAGE` ticks and unticks.

```python
import lexcor

with lexcor.open_store("terms.sqlite") as store:
    store.add_package(lexcor.read_package("terms.csv", language="de"))
    for mention in lexcor.tag("Bekannte arterielle Hypertonien.", store):
        print(mention.text, [(a.code, a.kind) for a in mention.assignments])
```

The core needs nothing but Python (3.12 or later) and its standard library. Tests: `pytest`; checks: `ruff
check .` and `mypy`.

## Inside another program

lexcor, lexterm and lexident are libraries first: a program that reads documents imports them and stays in
charge. What they keep for such a program:

- **No network, no output of their own.** Only the command line prints; the library returns values.
- **Nothing is written but a store a caller opened for writing.** `open_store(path, readonly=True)` opens a
  store that comes inside a program without leaving a journal beside it; `Store(":memory:")` keeps one for a
  run; and `entries_of(packages)` with `Recogniser` or `Cover` recognises with packages and no store at all.
- **The same result for the same input**, with places in code points and assignments in a fixed order.
- **Importing loads nothing**; a recogniser is built on first use and kept until its store changes.
- **A store may be opened in one thread and used in another**, by one thread at a time.
- **Errors can be told apart**: every error a person is told about is a `LexError` with a `kind` (`no-package`,
  `read-only`, `not-a-codesystem` …) and its message in German and English (`error.messages`).

The matcher, the vocabulary and the lexicon were written in PseudonyMed. Their history there is part of this
repository's history: `git log -- legacy/` shows it. The bench, the prototype the matcher grew out of and the
reports on what exists already are in lexterm's repository for now.

lexterm and lexident are not public yet. `lexcor` is a working name.

## Licence

MIT.
