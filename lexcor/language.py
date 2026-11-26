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
