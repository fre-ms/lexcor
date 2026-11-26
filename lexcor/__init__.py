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

from lexcor.annotation import Annotation, AnnotationError, annotate
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
    tidy,
    write_codesystem,
    write_csv,
)
from lexcor.package import read as read_package
from lexcor.recognise import Assignment, Kind, Mention, Recogniser, recogniser, tag
from lexcor.store import (
    Change,
    Entry,
    PackageInfo,
    Store,
    StoreError,
    entries_of,
    open_store,
)

__version__ = "0.2.0"

__all__ = [
    "Annotation", "AnnotationError", "Assignment", "Change", "Concept", "Cover", "Entry", "Kind",
    "LexError", "Mention", "annotate", "load_annotation",
    "Package", "PackageError", "PackageInfo", "Recogniser", "Store", "StoreError", "Term",
    "cover", "covers", "entries_of", "open_store", "read_codesystem", "read_csv", "read_package",
    "recogniser", "say", "set_language", "tag", "tidy", "write_codesystem", "write_csv",
]
