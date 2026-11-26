# SPDX-FileCopyrightText: 2026 fre.ms
# SPDX-License-Identifier: MIT
"""German or English for everything a person reads.

Identifiers are English; messages exist in both languages and are written side by side with
:func:`say`.  The language is what the caller set with :func:`set_language`, else
``LEXCOR_LANGUAGE``, else the language of the system (German when it is German, English
otherwise).
"""

from __future__ import annotations

import locale
import os

LANGUAGES = ("de", "en")
_chosen: str | None = None


def set_language(code: str | None) -> None:
    """Fix the language for this process ("de" or "en"); None returns to the default."""
    global _chosen
    if code is not None and code not in LANGUAGES:
        raise ValueError(f"unknown language {code!r} (de, en)")
    _chosen = code


def language() -> str:
    """The language in force: "de" or "en"."""
    if _chosen:
        return _chosen
    wanted = os.environ.get("LEXCOR_LANGUAGE", "").lower()[:2]
    if wanted in LANGUAGES:
        return wanted
    system = (locale.getlocale()[0] or os.environ.get("LANG", "")).lower()
    return "de" if system.startswith(("de", "german")) else "en"


def say(german: str, english: str) -> str:
    """The message in the language in force."""
    return german if language() == "de" else english


class LexError(ValueError):
    """Something a person is told about.  ``kind`` is a word a program can tell apart; the
    message exists in both languages, so that a caller with a language of its own (a
    program that answers in German and English at once) need not ask this module for it."""

    def __init__(self, kind: str, german: str, english: str) -> None:
        super().__init__(english)
        self.kind = kind
        self.messages = {"de": german, "en": english}

    def message(self, code: str | None = None) -> str:
        """The message in a language ("de" or "en"), else in the one in force."""
        return self.messages[code if code in LANGUAGES else language()]

    def __str__(self) -> str:
        return self.message()
