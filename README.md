<!-- SPDX-FileCopyrightText: 2026 fre.ms -->
<!-- SPDX-License-Identifier: MIT -->

# lexcor

The common core of lexterm (medical terms) and lexident (identifying information):
two libraries that find words of a list in German text and let a person check the result.

What the two share is here, once: packages of terms as FHIR CodeSystems with their licence, a store, recognition
in four stages with the kinds of match German needs, an annotation document that records who decided what, the
windows for review and for the packages, and the interfaces to a terminology server. lexcor knows nothing
medical and nothing about persons.

The project is at the design stage: there is no code here yet. The [design](docs/design.md) says what is to be
built and in which order. A prototype of the matcher, a bench and the reports on what exists already are in
lexterm's repository for now.

`lexcor` is a working name; the name search is in lexterm's repository.

## Licence

MIT.
