<!-- SPDX-FileCopyrightText: 2026 fre.ms -->
<!-- SPDX-License-Identifier: MIT -->

# lexcor: design

Draft 0.2, 27 November 2026. Stage 0.1 is built and the annotation document of stage 0.2 (section 10); the
windows, the client and the server are not. The bench, the
prototype the matcher grew out of and two reports (in German) on what exists already and what was measured
are in the repository of lexterm.

## 1 Purpose

lexcor is the common core of two libraries that find words of a list in German text and let a person check the
result:

- **lexterm** finds medical terms and assigns them the codes of terminologies;
- **lexident** finds identifying information (names, places, hospitals, occupations, numbers).

What the two share is here, once: the packages of terms and their store, recognition, the annotation document
with its record of who decided what, the windows for review and for the packages, and the interfaces to a
terminology server. lexcor knows nothing medical and nothing about persons. What makes a library medical or
about persons (its packages, its rules, its output) is in that library.

Three conditions hold for everything in the core:

- it runs on an ordinary computer **without a graphics card or other special hardware**;
- it **can be packaged** as a program that is shipped with others;
- it gives **the same result for the same input**.

A part that cannot keep them (a recogniser that learns, a language model) is a module that is switched on
beside the core, and is thought of from the start (section 4.7).

## 2 Words used here

- **Package**: one terminology or list in one version, with its licence.
- **Concept**: one meaning in a package, with a code, a name to show and a class.
- **Term**: one wording of a concept as it may stand in a text.
- **Mention**: a place in a text where a term stands.
- **Assignment**: the statement that a mention means a concept. A mention has as many assignments as packages
  (and meanings) hold its term.
- **Recogniser**: anything that takes a text and packages and returns mentions with assignments.

## 3 Packages

### 3.1 Form

A package is a **FHIR CodeSystem** (R4), as a JSON file or inside a FHIR package (`.tgz`). This is the form in
which terminologies are published and which every FHIR terminology server reads and writes.

| What the core needs | In the CodeSystem |
|---|---|
| name, version, identifying URI | `title`, `version`, `url` |
| licence and source | `copyright`, `publisher` |
| concept: code and name to show | `concept.code`, `concept.display` |
| terms of a concept, with their language | `concept.designation` (`value`, `language`) |
| kind of a term: preferred, synonym, abbreviation | `concept.designation.use`: a further name to show carries HL7's code `display`, a synonym carries none, an abbreviation a code of the core's own |
| class of a concept | `concept.property` with the code `class` |
| parent of a concept, where the source has a hierarchy | `concept.property` with the code `parent`, FHIR's own property (`http://hl7.org/fhir/concept-properties#parent`) |
| what else a source says of a concept (the town of a hospital, the number of a study) | further `concept.property` entries, each under a name of the source's choosing; in a CSV file any further column |

A package is written with FHIR's own elements and codes wherever FHIR has one. **Two things it has none for
are the core's own:**

- whether the package **may be passed on**: an extension `redistributable` on the CodeSystem, because FHIR has
  only the free text of `copyright` for it;
- the kind of term **abbreviation**: a code for `designation.use`, where FHIR and HL7 offer codes for a name to
  show and, from SNOMED CT, for a synonym, but none for an abbreviation. An abbreviation is matched only as
  written (section 4.3), so that no further mark says so.

They belong to the form of a package, which the core reads for both libraries, and so they are the core's and
not lexterm's. Their namespace is provisional (`https://lexcor.invalid`, one constant in the code) and is
settled when the first package is passed on (section 11).

A plain list of words is a package too: the code of each concept is the word itself. A list without a URI of
its own gets one from the store that takes it in (`urn:uuid:…`, the same for the same name, so that a list read
again replaces itself). That URI belongs to whoever keeps the store, not to this library; in the commands such
a list is named by its title. **CSV** is the second form, for lists made by hand: one row per term with the columns `term`, `code`,
`display`, `class`, `kind`, `language`; only `term` is required. Comma-separated, UTF-8.

A package may be a **supplement** (FHIR: `content: supplement`, with `supplements` naming a code system):
further wordings for the codes of another code system, such as the alphabetical index to a classification, a
translation, or a user's own synonyms. A supplement is a package of its own, with its URI, its version and its
licence, and is taken in and out on its own, so that it never takes the place of the code system it adds to.
Its terms code in the system it supplements:

- recognition gives the code under that system's URI. Where the store holds the system as well and the system
  has the code, the code comes with the version, the name and the class the system gives it; otherwise with the
  version the supplement names, and without a name, because none is made up;
- the system and its supplements answer as one (section 4.3): a place gets one assignment for a code, whether
  the system's own title or a wording of the supplement matched;
- the overlay of the system holds for the terms of its supplements;
- a supplement is chosen by its own URI, like any package;
- the store says how a supplement fits (`fit`): whether it holds the system, in which version beside the one
  the supplement names, and how many of the supplement's codes the system does not have. A supplement that
  does not fit is taken in all the same, and the command line says what does not fit.

A CSV list becomes a supplement with `--supplements URL`; every row then needs its code.

### 3.2 Three kinds of package

| Kind | How it gets to the user |
|---|---|
| **bundled**: a licence that allows passing on | comes with the library |
| **built by the user from free data**, where the terms of download forbid passing on or ask for a confirmation | instructions and a command that fetches from the source after the user has agreed to its terms |
| **licensed** | imported locally from the user's own copy, or reached through a terminology server the user runs; never shipped, never in a repository or its tests |

A package that is not `redistributable` is never written into an export meant for others, and the server guards
it (section 7.3).

### 3.3 Editing and the store

A user deletes terms that mislead, adds terms and synonyms, moves a term to another class. These changes are
kept as an **overlay** beside the package, never written into it: a new version of the package replaces the
package and leaves the overlay in force. An overlay can be exported and imported like a package.

One SQLite file in the user's data folder holds the packages, their concepts and terms, and the overlays. A
version counter changes with every edit, so that an index is rebuilt only when needed. Each library has its own
file by default; a caller may give another.

## 4 Recognition

### 4.1 Four stages

Recognition is four stages, each of which can be exchanged (the build of QuickUMLS, Soldaini and Goharian
2016):

1. **Runs of words**: the text is cut into words, and every run of up to five words that may be a term is
   formed.
2. **Candidates**: for each run, the terms of the packages that it may spell.
3. **Checking**: each candidate is checked and given the kind of its match; rules of a library (section 4.5)
   may accept, reject or mark it.
4. **Resolution**: of a package's candidates that overlap, a match by rule wins over an approximate one, then
   the longer run, then the better kind of match. Packages do not shut each other out: a run is a mention
   where at least one package keeps it. A package that knows only "Parkinson" codes it inside "Morbus
   Parkinson", the mention of another package, so mentions of different packages may overlap. A package's own
   shorter terms inside its longer ones are given on request (`nested`).

### 4.2 Runs of words

A run is not formed

- across an empty line or the end of a clause (a comma, a bracket, a full stop that does not follow an
  abbreviation of up to four letters as in "A. carotis");
- of a single function word (article, preposition, conjunction), or of a number alone.

A run that begins or ends with a function word is formed but marked: there only a term listed in exactly
these words is found ("Hepatitis A", "Vitamin D"), nothing by its stem or by a variant of its spelling. Inside
a run such words stay ("Morbus des Kindes"). A word written all in capitals is no function word ("ALS", "HAT").

A single line break does not end a run. Letters come with their lines wrapped, and a term of two words is often
cut by the end of a line ("absolute" at the end of one line, "Arrhythmie" at the start of the next).

Function words come from a small list in the core, per language, not from a language model. This alone keeps
"hat", "als" and "sowie" from ever being a term.

### 4.3 Kinds of match

| Kind | What it allows | Made by |
|---|---|---|
| **as written** | upper and lower case; for abbreviations (what a package calls one, and what looks like one) and for words of up to three letters nothing but the listed spelling ("HAT" is a term, "hat" is not) | core |
| **inflected** | a German inflection ending (`-s`, `-n`, `-en`, `-e`, `-es`, `-er`, `-em`, `-ern`) on a stem of four or more letters; in a term of several words on its last word | core |
| **spelling variant** | spellings that stand side by side by rule: umlauts written out or not, `ß` and `ss` (core); further pairs a library names (lexterm: c with k and z, ph with f, th with t) | core, with the library's table |
| **approximate** | a similarity of letters above a threshold | only on request, section 4.4 |

Every term carries its language, and a caller says in which languages' terms the rules look: with English
names in the dictionary they tagged "gut", "fast" and "Berlin" in German letters (lexterm's report on the
trial, section 7.4).

Every assignment says of which kind its match is, and an approximate one how similar. The first three kinds
give the same result on every run and make assignments. Within one package a better kind shuts out a worse one
for the same words, in the order as written, spelling variant, inflected: where "Masern" is listed, "Maser" of
the same package is not assigned to it by its stem.

**Every package answers for itself.** One wording is coded in every code system that knows it: a mention
carries an assignment from each package that lists its words, each with the kind of its own match. A package
that lists "Hypertonien" as written does not keep the packages that list "Hypertonie" from assigning their
codes to the same place. Nothing chooses between packages or between classes; that is a person's decision in
the review, and what a FHIR CodeableConcept is for: one concept in the text, a coding from every system. On the trial's test set these rules linked 89 % of the places and tagged nothing they should not,
where every plain dictionary matcher linked 64 %.

### 4.4 Approximate matching: for people, not for assignments

Letter trigrams and the CPMerge algorithm (SimString, Okazaki and Tsujii 2010) find all terms similar to a
string quickly and exactly. The trial showed what that is worth on German text with the packages at hand: more
places found, hardly any more right ("Mobilisation" as "Immobilisation", "Chemotherapie" as
"Photochemotherapie"). The papers that report good results had a dictionary of six million strings, where the
exact entry mostly wins.

So the core uses it only where a person chooses:

- in the review window, for a place that was not recognised: "similar terms?";
- in the package window, to find duplicates and near-duplicates;
- in the server's search behind an input field.

A library may switch it on as a recogniser of its own for text that comes from text recognition. Its
assignments are then proposals of the kind "approximate", never in force until a person confirms them.

### 4.5 Rules of a library

After checking, a library's rules see each candidate with the text around it and accept, reject or mark it.
This is where lexident says that a listed occupation counts only next to a cue word, and where lexterm may
later say that a mention is negated. The core gives the place for such rules and two ready forms (cue words
before or after within a distance; a heading above); it holds no rule itself.

### 4.6 Two questions, kept apart

- **`tag`** answers "which concepts does this text mention?" and makes assignments, by the stages above.
- **`covers`** answers "is this span vocabulary of these packages?". It is generous on purpose (a part of a
  hyphenated word counts, as does an eponym before "-Zeichen" or "-Syndrom") and gives no codes. PseudonyMed
  needs it to leave medical words alone.

### 4.7 Recognisers that are switched on

The stages above are the recogniser that comes with the core. Others can be added without changing anything
else, because a recogniser is only this:

```
recognise(text, packages, settings) -> mentions, each with assignments (code, kind of match, score)
```

and because every assignment in the annotation document names the recogniser that made it. What a recogniser
proposes is merged with what is there by the rules of section 5.

**Learning recognisers** are planned from the start, as modules of their own beside the core:

| Module | What it would add | What it needs |
|---|---|---|
| a recogniser that learns the surroundings of a concept (MedCAT 2) | chooses among several meanings of a term; learns from unlabelled text and from confirmed and rejected assignments | word vectors for German clinical text, which would have to be computed; no graphics card |
| retrieval by meaning (a multilingual model of term embeddings, run without a graphics card) | proposals for places that no package holds in so many words, also across languages | a model of 1.1 GB that states no licence (its English sibling by the same authors is under Apache 2.0); measured (lexterm's report on the trial, section 7): good as a proposal for a person and across languages, not safe as an assignment of its own |
| a language model run locally | the same, and paraphrases | a strong computer; cannot be packaged with the core |

What they have in common: the core hands them the packages and the annotation documents with a person's
decisions, which are exactly what such a recogniser learns from; they return proposals with a score; none of
them is needed for the core to work. Which of them is worth building is decided by the bench, not here.

For the first the bench has decided: **retrieval by meaning is built with stage 0.3, as a module that proposes
and never assigns** (decided on 26 November 2026). What it finds is shown in the review window as a proposal
with its nearness, and nothing of it is in force until a person confirms it. The module and its model are not
part of the core: the user fetches the model. The model's page states no licence. Its authors are asked; until
they answer, the project goes on as if the model were under Apache 2.0, as its English sibling by the same
authors is (decided on 27 November 2026).

## 5 The annotation document

The result of recognition and of a person's review is one JSON file beside the text.

```json
{
  "lexcor": 1,
  "made_with": "lexterm 0.2",
  "text": { "sha256": "…", "length": 1834 },
  "packages": [
    { "url": "http://fhir.de/CodeSystem/bfarm/icd-10-gm", "version": "2026", "sha256": "…",
      "title": "Internationale statistische Klassifikation …", "overlay": "…" }
  ],
  "settings": { "packages": ["…"], "classes": [], "nested": false },
  "mentions": [
    {
      "id": "m1", "start": 112, "end": 128, "exact": "Morbus Parkinson",
      "prefix": "Diagnosen: ", "suffix": ", ED 2019",
      "assignments": [
        {
          "system": "http://fhir.de/CodeSystem/bfarm/icd-10-gm", "version": "2026",
          "code": "G20", "display": "Primäres Parkinson-Syndrom", "class": "disorder",
          "terminal": true, "term": "Morbus Parkinson", "match": { "kind": "as written" },
          "origin": { "by": "machine", "recogniser": "lexcor 0.2" },
          "status": "proposed"
        }
      ]
    }
  ]
}
```

- **Positions** count Unicode code points from the start of the text. A program whose strings count otherwise
  (UTF-16 in JavaScript and Qt) converts.
- **`exact`, `prefix`, `suffix`** are the wording and a little of what stands around it, so that a mention is
  found again after the text has changed a little.
- **`match`** is the kind of match of section 4.3, with `score` for an approximate one.
- **`origin`** is `machine` with the recogniser, or `person`. Where the term that matched is in a supplement
  (section 3.1), `origin` names that package as well (`package`, `package_version`): the code is the code
  system's, the wording the supplement's.
- **`terminal`** says whether the code is terminal in a code system with a hierarchy: no other code of it has
  it as its parent (the ICD-10-GM codes one codes with). It is left out for a code system without a hierarchy.
  It marks and filters nothing: an output may leave out what is not terminal.
- **`status`** is `proposed`, `confirmed` or `rejected`. A decision may carry who made it and when
  (`"decision": {"by": …, "at": …}`): only what the caller says, because the library never reads the clock or
  asks who is at the keyboard, and a caller that must give the same bytes again leaves both out.
- **`term`** is the listed wording that matched, which an inflected or otherwise spelt mention does not show.
- A place a person marked carries `"by": "person"`, with or without an assignment yet.

Rules:

- A person never deletes what a machine proposed: they **reject** it, and it stays in the file. A later run
  does not bring it back as a proposal.
- What a person confirmed or added stays, whatever a later run finds.
- The same text, the same package versions, the same recognisers and the same file give the same result, byte
  for byte.
- A run replaces only what the same recogniser proposed and nobody has decided: several recognisers can write
  into one document, each named in the assignments it made.
- A mention that is not found again after the text has changed is kept in a list of its own (`lost`) and shown,
  not dropped. One that is found again moves to where its wording stands now, by what stood around it.
- For reports and logs there is a form with places, classes and statuses and no wording at all.

**Which packages a run applies, and the record of it.** Every run chooses its packages: all of the store,
those named, less those left out (a window offers them as a list to tick; the command line has `--package` and
`--without`). The document names exactly what the last run applied (`packages`):

- each package with its `url`, `version`, `title` and `sha256`, the fingerprint of its content, which tells two
  packages apart that carry the same version;
- `overlay`, the fingerprint of the user's changes in force for its terms (section 3.3), where there are any:
  the same package with other changes gives other results;
- for a supplement the code system it supplements (`supplements`), and that code system as well with `use`
  `names` where its names and classes were read for the supplement's codes without its own terms being
  applied;
- packages that an earlier run applied and that decisions in the document still come from, with `use`
  `earlier`: every assignment of a machine names a package the list holds, in the version it came from.

`settings` holds what the run was asked for (packages, classes and the settings of the recogniser), and
`made_with` the program and its version. A run can be **repeated** exactly as a document records it (`tag
--annotation FILE --repeat`): with the same choice, and only if the program and every package are the same as
then. Otherwise it refuses and says what differs (a package missing or added, another version, other content,
other changes of the user's), and writes nothing.

The form is close to what INCEpTION exchanges (UIMA CAS); both libraries read and write that form as well.

## 6 The windows

**Review**: the text on the left with its mentions marked, proposed and confirmed ones told apart at a glance;
on the right, for the mention chosen, its assignments with package, code, name and kind of match. A person
confirms, rejects, adds an assignment (searching the packages by wording or code, with similar terms on
request), and marks a place by hand that was not recognised. "Confirm all of this package" for long texts.

**Packages**: the packages with licence, version and number of terms; search, edit, add and delete terms;
import and export. Every package has a box to **tick for the runs that follow**: what is ticked is applied, and
the annotation document of a run records what that was (section 5). A supplement stands below the code system
it supplements, with a box of its own. The ticks are the store's, and so they hold for every program that reads
the store: the command line follows them as well (`packages on`/`off` without a window), and so does an
application such as PseudonyMed, which protects with the ticked lists of lexterm's store. A package is
ticked when it comes into the store; it can be taken in unticked (`packages import --off`, the box "apply at
once" in the window), and a new version of a package that was unticked stays unticked.

*Built in 0.2.6* (`lexcor.window`, `lexcor packages window`; PySide6 as the extra `lexcor[window]`): the list
with its ticks, taking a file in beside the window (a library adds the forms it reads, `Reader`), writing a
package out (one that may not be passed on only for the person's own use), taking one out, and beside the list
the library's text on where terminologies come from. Still to come: searching and editing terms in the window.

Both are widgets that another program can embed. The interface is German or English. What the classes are
called and how they are coloured comes from the library.

## 7 Client and server

Planned from the start, built when a program needs them. Nothing in sections 3 to 5 may stand in their way,
which is why packages are FHIR CodeSystems.

### 7.1 Client of a terminology server

The core speaks the FHIR terminology interface to any server that offers it.

- **As a source of a package.** Recognition needs the terms as a local index; one cannot ask a server for every
  word of a text. The terms of a code system or a value set are read from the server (`$expand` with
  designations, in pages) and built into a package on the user's computer, marked as not redistributable
  unless the user says otherwise.
- **While reviewing.** `$lookup` for the details of a code, `$validate-code` when a person types a code in,
  `$translate` to offer the code of another system.
- **Never with the text.** Only codes, URIs and search words a person types go to a server, and the default is
  a server on the same computer.

### 7.2 Server

A library's own store and recogniser, made available to other programs on the same computer or, if the
operator decides so, in the same network.

| Request | Answer |
|---|---|
| `GET /metadata` | what the server can do |
| `GET /CodeSystem`, `GET /CodeSystem?url=…` | the packages |
| `CodeSystem/$lookup` | name, terms and properties of a code |
| `CodeSystem/$validate-code` | whether a code exists and its name fits |
| `ValueSet/$expand` with `filter` | the codes whose terms match what was typed: the search behind an input field |
| `ConceptMap/$translate` | the code of another system, where the packages carry the mapping |
| `POST /annotate` | the annotation document for a text (not FHIR; FHIR has no operation for this) |

It does not serve the query language and full hierarchy of SNOMED CT, versions side by side, or authoring. An
operator who needs them runs a server made for them; the core can be told to **pass on** what it cannot answer.

### 7.3 Licences and privacy

- The server listens on the computer itself unless the operator opens it.
- A package that is not `redistributable` is not handed out as a whole (its CodeSystem is served with
  `content: not-present`), and its codes are answered beyond the computer only when the operator has stated
  that the licence covers it.
- `/annotate` receives text that may be clinical. It is not logged.

## 8 Interfaces

**Library** (Python 3.12 or later):

```python
import lexcor

store = lexcor.open_store(path)
annotation = lexcor.tag(text, store, packages=[...], rules=[...], variants=[...])
annotation.save("letter.lexterm.json")
```

lexterm and lexident are thin over this: each brings its packages, its table of spelling variants, its rules
and its output, and a command line of its own (`lexterm …`, `lexident …`) whose commands for packages, tagging
and review are the core's.

With `--json` every command prints one JSON document on standard output and nothing else. Names of commands and
options are English; messages are German or English.

**Dependencies**: Python's standard library and SQLite. The windows (PySide6), the search for similar terms
and the UIMA CAS form are extras.

**Inside another program.** The libraries are used by programs that stay in charge of their own files,
threads and answers: PseudonyMed, medorello, and semdoc, the step of the document tools (docrepro) that finds
medical terms and identifying information in what was read from a page. For them the core keeps this:

- it uses no network and prints nothing (only the command line prints);
- it writes nothing but a store the caller opened for writing: a store can be opened for reading only, without
  a journal beside it (a store inside a packaged program), kept in memory for one run, or left out altogether
  (`entries_of(packages)` gives recognition what a store would);
- importing it loads nothing; a recogniser is built on first use and rebuilt only when its store changed;
- a store may be opened in one thread and used in another, by one thread at a time;
- places are in code points, and the same input gives the same result in the same order;
- every error a person is told about has a kind a program can tell apart and its message in both languages,
  so that a caller can answer in its own form.

What such a program still waits for is the annotation document (section 5) with a person's decisions: semdoc
stores proposals and decisions and must give its result again without the packages being installed.

## 9 Bench

Every recogniser is measured the same way, each with the same packages: on a test set whose truth is known by
construction, and on corpora people annotated. The bench that exists (in lexterm's repository, to move here)
measured twelve recognisers on German text; its results are why the core's own rules are as they are. A
recogniser that is switched on has to show there what it adds and what it costs.

## 10 Stages

1. **0.1** The matcher, the vocabulary and the store of identifying terms leave PseudonyMed with their history.
   Packages as FHIR CodeSystem and CSV, the store, the four stages with the kinds of match, `tag` and `covers`.
   *Begun: the code is in `lexcor/` and gives the prototype's results on the bench, and the history from
   PseudonyMed is part of this repository's history; PseudonyMed's change to an adapter and the move of the
   bench are still to come.*
2. **0.2** The annotation document; the two windows; the place for a library's rules.
   *Built in 0.2.0: the annotation document (`lexcor.annotation`, `lexcor tag FILE --annotation FILE`) and
   properties of a concept in packages and in the store. In 0.2.4: supplements (section 3.1). In 0.2.5: the
   record of which packages a run applied, with the user's changes, and the repetition of a run as recorded;
   whether a code is terminal (section 5). In 0.2.6: the package window with the person's choice (section 6);
   in 0.2.7 a package can be taken in unticked.
   Still to come: the review window and the place for a library's rules.*
3. **0.3** The interface for recognisers that are switched on, and the first of them: retrieval by meaning as a
   source of proposals in the review window (section 4.7); the search for similar terms; UIMA CAS.
4. **0.4** Client of a terminology server.
5. **0.5** Server.

lexterm and lexident follow each stage with what is theirs.

## 11 Open questions

1. **Where a user's vocabulary lives today**: in PseudonyMed's pseudonym database. It moves to the store once,
   by a command; PseudonyMed keeps no second copy.
2. **One store or two** for a user who has both libraries.
3. **The first learning module** (section 4.7) is decided: retrieval by meaning, proposing only. For its model
   Apache 2.0 is assumed until the authors, who are asked, say otherwise. Open is on what texts a recogniser
   that learns from a user's decisions may learn.
4. **Languages other than German**: the rules of inflection and the function words are per language; only
   German is planned.
5. **The namespace** of the two things that are the core's own (section 3.1): it stays with the core and is
   settled when the first package is passed on (decided on 26 November 2026). Then: a domain of the project's
   own or another lasting URI.
6. **Inflected adjectives inside a term** ("chronischer Bronchitis" for "chronische Bronchitis"): the rule
   allows an ending on the last word only. The search by meaning found such places; whether a rule finds them
   without false matches is to be measured on the bench.
