# SPDX-FileCopyrightText: 2026 fre.ms
# SPDX-License-Identifier: MIT
"""lexcor: packages of terms, their store and their recognition in German text.

The common core of lexterm (medical terms) and lexident (identifying information).

    import lexcor

    store = lexcor.open_store("terms.sqlite")
    store.add_package(lexcor.read_package("icd-10-gm.json"))
    for mention in lexcor.tag(text, store):
        ...
"""

from lexcor.annotation import (
    Annotation,
    AnnotationError,
    annotate,
    differences,
    listed,
    packages_of,
)
from lexcor.annotation import load as load_annotation
from lexcor.cover import Cover, cover, covers
from lexcor.language import LexError, say, set_language
from lexcor.package import (
    Concept,
    Package,
    PackageError,
    Term,
    read_codesystem,
    read_csv,
    read_fhir_package,
    supplemented,
    tidy,
    write_codesystem,
    write_csv,
)
from lexcor.package import read as read_package
from lexcor.recognise import Assignment, Kind, Mention, Recogniser, recogniser, tag
from lexcor.store import (
    Change,
    Entry,
    Fit,
    PackageInfo,
    Store,
    StoreError,
    entries_of,
    open_store,
)

__version__ = "0.2.5"

__all__ = [
    "Annotation", "AnnotationError", "Assignment", "Change", "Concept", "Cover", "Entry", "Fit",
    "Kind", "LexError", "Mention", "annotate", "differences", "listed", "load_annotation",
    "packages_of",
    "Package", "PackageError", "PackageInfo", "Recogniser", "Store", "StoreError", "Term",
    "cover", "covers", "entries_of", "open_store", "read_codesystem", "read_csv",
    "read_fhir_package", "read_package",
    "recogniser", "say", "set_language", "supplemented", "tag", "tidy", "write_codesystem",
    "write_csv",
]
