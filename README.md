<!-- SPDX-FileCopyrightText: 2026 fre.ms -->
<!-- SPDX-License-Identifier: MIT -->

# lexcor

The common core of lexterm (medical terms) and lexident (identifying information):
two libraries that find words of a list in German text and let a person check the result.

What the two share is here, once: packages of terms as FHIR CodeSystems with their licence, a store, recognition
in four stages with the kinds of match German needs, an annotation document that records who decided what, the
windows for review and for the packages, and the interfaces to a terminology server. lexcor knows nothing
medical and nothing about persons.

The project is at its first stage (0.1). The [design](docs/design.md) says what is to be built
and in which order. What is here:

- `lexcor.package`: packages as FHIR CodeSystem (R4, JSON) and as CSV;
- `lexcor.store`: one SQLite file with the packages and a user's changes to them, kept as an overlay;
- `lexcor.recognise`: `tag`, recognition in four stages with the kinds of match (as written, spelling variant,
  inflected);
- `lexcor.cover`: `covers`, the generous question whether a span is vocabulary at all;
- a command line: `lexcor packages list|import|export|remove`, `lexcor tag FILE`, with `--json` and
  `--language de|en`.

```python
import lexcor

with lexcor.open_store("terms.sqlite") as store:
    store.add_package(lexcor.read_package("terms.csv", language="de"))
    for mention in lexcor.tag("Bekannte arterielle Hypertonien.", store):
        print(mention.text, [(a.code, a.kind) for a in mention.assignments])
```

The core needs nothing but Python (3.12 or later) and its standard library. Tests: `pytest`; checks: `ruff
check .` and `mypy`.

The matcher, the vocabulary and the lexicon were written in PseudonyMed. Their history there is part of this
repository's history: `git log -- legacy/` shows it. The bench, the prototype the matcher grew out of and the
reports on what exists already are in lexterm's repository for now.

lexterm and lexident are not public yet. `lexcor` is a working name.

## Licence

MIT.
