# Agent Instructions — doc2wrapper

`CLAUDE.md` is a symlink to this file: OMP and Codex discover `AGENTS.md`, Claude Code
reads `CLAUDE.md`, and both reach these bytes. A checkout without symlink support — the
Git for Windows default outside Developer Mode — leaves `CLAUDE.md` as a stub naming
this file rather than a copy of it, so read `AGENTS.md` directly there.

## What this project is

`doc2wrapper` deterministically generates **workflow-language task wrappers** — WDL
`task` blocks, Nextflow `process` blocks — from a command-line tool's interface, given
either its captured `--help` text or a live Python `argparse.ArgumentParser` object.
Snakemake is an intended third target with no code yet.

The point is *determinism*, not intelligence. A language model can write these wrappers
today and write them better; it cannot be dropped into a CI job or a local `make` target
with a fixed cost, a fixed runtime, no network, and byte-identical output across runs.
That niche is what this tool occupies, and every design decision should be read against
it. **Do not** add a model call, a network fetch, or any nondeterministic step to the
generation path.

Longer-range motivation: WDL is the better-specified of the two mainstream languages, but
far more open-source Nextflow exists in the wild, so Nextflow is what practitioners
copy-paste. Cheap, mechanical WDL stubs for arbitrary tools are an attempt to flatten
that onramp.

### Scope decision: task wrappers only

The generator emits **only** the per-tool task/process block, never an enclosing
`workflow {}` or a channel graph. This was settled through trial and error: an outer
workflow requires knowing how tasks compose, which the help text does not say, so
generated workflows were uniformly wrong and had to be discarded. A generated task
wrapper is a useful starting point a human edits; a generated workflow is not. Treat this
as a fence with a signature — do not rebuild the outer workflow without new evidence.

Generated output is expected to need hand-editing. The bar is "saves time over writing
the task from scratch," not "runs unmodified."

## Orientation

Note the naming skew, which is historical and not worth churning: the **checkout** is `doc2wdl` and so is the **git remote** (`git@github.com:etal/doc2wdl`), while the **package and console script** are `doc2wrapper`.
`pyproject.toml`'s `project.urls` and the CLI's epilog point at the remote.

`CLAUDE.md` is a symlink to this file, so the two cannot drift. That holds on macOS
and on WSL2's own ext4 filesystem. It does not hold for a checkout made by native
Windows git without Developer Mode, or on a DrvFS path under `/mnt/c`: git falls back
to `core.symlinks=false` and materializes `CLAUDE.md` as a nine-byte text file whose
contents are the string `AGENTS.md`. If you find yourself reading that, read this file
instead.

```
doc2wrapper/
  cli.py         argparse-based console entry point; two subcommands, `docopt` and `argparse`,
                 sharing `-t/--target {wdl,nextflow}` and `-o`
  docopter.py    READER: help text -> object model, via the vendored parser
  argparser.py   READER: live ArgumentParser -> object model, via argparse internals
  tasktree.py    MODEL: the `Argument` dataclass and the `ValueType` vocabulary, plus
                 the naming and typing helpers the readers and writers share
  wdlgen.py      WRITER: object model -> WDL; owns RESERVED_WDL_NAMES, TEMPLATE_WDL_NAMES,
                 the WDL spelling of each `ValueType` and of Python values as literals
  nfgen.py       WRITER: object model -> Nextflow; owns RESERVED_NF_NAMES and
                 RESERVED_NF_TITLES
  render.py      Jinja2 environment shared by the writers; `render_document` renders
                 every block and the document around them in one call.
                 A writer needing different settings should build its own
                 environment rather than add a flag
  _docopt.py     VENDORED: the static grammar subset of docopt 0.6.2
  templates/     Jinja2 templates, one document and one block template per target:
                 document_template.{wdl,nf}, task_template.wdl, process_template.nf
tests/           pytest suite; `tests/example/` holds the two generation fixtures and a
                 Makefile, `tests/corpus/` the conformance corpus and its manifest
```

Data flow is strictly one-directional: **reader → `dict` of template kwargs → writer**.
The dict carries `title`, `usage`, `cli_prefix`, `cli_args` (a list of `Argument`), and `has_output_file`.
Adding a target language should mean adding a writer plus a template, nothing else; adding an input format should mean adding a reader.
If a change forces a reader to know about WDL, the abstraction has been breached.
Four consequences are enforced.
First, name sanitization against a language's reserved words happens in the **writer**: `render.render_document` renames arguments with `tasktree.rename_arguments` and titles with `tasktree.rename_if_reserved`, each against a list the writer passes.
Arguments and titles take separate lists because the positions differ in Nextflow: an input named `File` resolves to a class, while a process named `File` works.
The argument list also carries names the block template declares itself, which are legal identifiers rather than keywords: WDL's `output_file`, while Nextflow's `emit:` labels form a namespace of their own and need no entry.
Second, uniqueness is decided in the writer too, over the final names, because the keyword rename can itself create a duplicate, as `scatter` becomes `scatter_` beside an argument already named that.
The first argument or task wanting a name keeps it, and later ones take `_2`, `_3` and so on; titles are unique per document, arguments per task.
Character legality, by contrast, is the readers' business and one rule, `tasktree.legal_name`, since a name legal in WDL is legal in Groovy; both readers build titles with `tasktree.task_title`.
Third, a type is decided in one place, `tasktree.type_and_default`, which both readers call, and the model names it in its own vocabulary, `tasktree.ValueType`, never in a target language's.
Fourth, `Argument.value_type`, `Argument.default_value` and `Argument.doc` hold a `ValueType`, Python values and plain text, never target-language syntax; `wdlgen` spells them as WDL on copies of the arguments, the same shape `rename_arguments` uses, so the shared Jinja2 environment carries no per-language filter.
`Argument` is a frozen dataclass, so a writer can change only a copy made with `dataclasses.replace`, and assigning to the model raises `FrozenInstanceError`.

Rendering is split in two because a document is not a task.
Statements that may appear only once per file — WDL's `version`, Nextflow's shebang and its note on unset inputs — belong to the document template, which is the only layer that knows how many blocks are being emitted; keeping them in the block template is how a two-task document came to carry two version statements.
Each writer therefore exposes exactly one public `render(tasks)` taking an iterable, and blocks are rendered only inside `render.render_document`, so no caller can obtain a fragment and concatenate two of them.
That matters because a lone WDL block passes `miniwdl check`: a version-less document falls back to draft-2, where `~{...}` is not interpolation and every declaration merely goes unreferenced.
Title uniqueness, a property of the whole document, is the second reason the blocks are rendered there.
Spelling a comment is likewise per-target and lives in the block template.
WDL 1.1 has only `#` line comments (`/* ... */` is a grammar error, not a style preference), and the Nextflow template uses `//` per line rather than a `/* ... */` block, which the first `*/` in captured help text would close early.
So there is no shared helper to write, and readers pass `usage` through as plain text.

Every Nextflow input is a `val`, although the model now marks files.
Staging a file needs `path`, and a positional with no other evidence is typed `FILE`, so choosing `path` from the type would stage samtools' `region` as a file; it would also change what channel the caller builds, which needs `nextflow run` to measure rather than `lint`.

## Environment

Development happens in the conda `dev` environment (Python 3.13 on the personal
workstation). The package need not be installed to run from a checkout:

```bash
python -m pytest
python -c "import sys; sys.argv=['d2w','docopt','-f','tests/example/help-cnvkit-antitarget.txt']; \
  from doc2wrapper import cli; cli.main()"
```

### Validation oracles — use them

Both target languages have a real checker installed in the `dev` environment. They are
the difference between "the template rendered" and "the output is a valid task."

```bash
miniwdl check <file>.wdl     # WDL 1.1 parser and type checker
nextflow lint  <file>.nf     # Nextflow 25.10+ parser; `-format` also reformats
```

`nextflow` needs Java 17 to 25 and finds the system JDK first, which on the personal workstation is JDK 27, so it exits with "Cannot find Java or it's a wrong version".
The `dev` environment carries its own JDK; point `JAVA_HOME` at it, as in `JAVA_HOME=/opt/homebrew/Caskroom/miniconda/base/envs/dev/lib/jvm`.
Without that, the suite's Nextflow checks skip, reporting the launcher's message: a session fixture runs `nextflow -version` first, because a launcher failure and a lint rejection share exit status 1.

`tests/conftest.py` wires both into the suite as fixtures, skipping when a checker is
absent. Any change to a template, a writer, or type inference **must** go through them.
What a Nextflow process renders at run time needs execution, not lint: the `nextflow_commands` fixture runs a generated process under `nextflow run` with its command prefixed by `echo`, at the cost of a JVM start, so it is reserved for what only execution shows.

A green checker is necessary and **not sufficient**, which is why `tests/test_generate.py`
also asserts on the generated text. Two measured instances: a template emitted
`"$output_file_name"` where WDL interpolation is `~{...}`, and a wrong Jinja predicate
dropped every positional from the command line while still declaring it as an input.
Both produce a task that type checks and silently does the wrong thing.

### Clean-room installs

Packaging defects do not show up in a dirty tree. Building a wheel in a checkout that
still holds a stale `doc2wrapper.egg-info/SOURCES.txt` picks up files the packaging
metadata does not actually declare — that is how the missing `templates/` entry stayed
invisible through a wheel inspection and only surfaced on install. Verify with:

```bash
rm -rf build doc2wrapper.egg-info && python -m venv /tmp/v && /tmp/v/bin/pip install .
```

`tests/example/Makefile` only records how each of the two generation fixtures was produced; generation and checking live in the pytest suite.
`tests/corpus/capture.py` recaptures the conformance corpus from `tests/corpus/manifest.tsv` under the pinned environment its docstring describes, and the manifest's header records the `PATH` the committed captures were taken with.
That `PATH` puts the `cnvkit` environment's miniwdl 1.14.2 ahead of the `dev` environment's 1.15.0, so it is for capture only and not for running the checkers.
The suite never runs `capture.py`, because a recapture changes bytes with each tool release: review the diff and run `tests/test_corpus.py`, where a class that moves reflects the new help text rather than the reader, and update the manifest's version and outcome columns to match.

The README's flagship argparse example, `cnvlib.commands.AP`, needs `cnvlib`, which the `cnvkit` environment provides.
That import works only if you call the environment's interpreter directly, because `conda run -n cnvkit` and `conda activate` both fail on this machine with a broken activation hook:

```bash
/opt/homebrew/Caskroom/miniconda/base/envs/cnvkit/bin/python3 -c "import sys; sys.path.insert(0, '.'); \
  sys.argv = ['d2w', 'argparse', '-m', 'cnvlib.commands', '-p', 'AP']; from doc2wrapper import cli; cli.main()"
```

An earlier revision of this file recorded `cnvlib` as simply "not importable"; that conclusion came from trusting `conda run`, and it cost the flagship fixture.

## Current state

The help-text path works end to end for both targets and is covered by tests.
The argparse path emits WDL and Nextflow with unique names throughout for a parser with subcommands, switches and multi-value arguments, and the flagship document passes `miniwdl check` whole.
Everything below was verified by execution on 2026-08-16 unless dated otherwise.

### What the help-text reader will and will not handle

`tests/corpus/` holds 14 help texts captured from real tools on 2026-10-24, chosen to represent the shapes the reader meets rather than as a sample.
`tests/corpus/manifest.tsv` records, for each file, the command that produced it, the tool's version, the outcome class, and what the reader makes of the file; `tests/test_corpus.py` asserts every file's class, and `cut -s -f2 tests/corpus/manifest.tsv | sed 1d | sort | uniq -c` counts them.
Because the corpus is not a sample, its counts describe the corpus and not how often a shape occurs among tools; this file states no rates, since a rate measured over files that are not committed cannot be reproduced.

- **Hard failure: 3.** No single `usage:` marker: none at all (`tar`), a Cobra heading spelled `USAGE` without a colon (`gh repo clone`), or two markers (`python3 -m timeit`, whose docstring has both "Library usage:" and "Command line usage:").
  These raise `docopter.UsageNotFound`, which the CLI reports as a message and exit status 1.
- **Degraded: 0.** Where `parse_pattern` raises on the synopsis, the positionals degrade to empty with a warning on standard error, and the options survive.
  No corpus file does so since D23; `tests/test_docopter.py` covers the class with synthetic help text.
- **Parsed: 11.** `wgsim`, `cnvkit.py batch` and `htsfile` yield exactly their positionals.
  The rest run to completion with extra positionals: summary placeholders such as `[OPTIONS]` and `...` (`tabix`, `samtools stats`), option values the reader cannot attribute to an option (`ls`, which has no options block, `bedtools intersect`, whose options block omits the `-a` and `-b` its synopsis names, and `python3 -m zipfile`, whose `-e` takes two values), or a placeholder split at its space (`jq`'s `<jq filter>`).
  argparse's `X [X ...]` for a positional with `nargs="+"` becomes two positionals, one required and one an optional array (`miniwdl check`, `cnvkit.py import-rna`).

The reliable shape is a single synopsis naming at most one fixed subcommand, written directly after the program name, where no flag's arity differs between the options block and the synopsis, and which ends at a blank line, at a line indented less than the program name, or, where the marker stands alone on its line, at a line of prose.
argparse-generated help wraps the synopsis at `COLUMNS` and aligns each wrapped line under the program name or deeper, so a wrapped line that opens with a flag no longer matters: `python3 -m zipfile`, which degraded at 80 columns, parses at every `COLUMNS` from 30 to 220 in steps of 10.
Hand-written C help may place the options block directly under the synopsis, as `samtools stats` and `htsfile` do, which D23 reads, or leave the block out (`ls`).
Both fixtures in `tests/example/` sit inside the shape, which is why they were never a sample.

**The argparse reader has no such limits** — it reads a live parser object and needs no text parsing at all.
`cnvlib.commands.AP`, the README's flagship example, unpacks to 35 tasks under one `version 1.1`.
Measured on 2026-10-23, after D9, the whole 35-task WDL document passes `miniwdl check`.
The last failure before D9 was `target --avg-size`, a `type=int` option whose default is the float `266.6666666666667`; that default no longer fits the declared type and is dropped, so the input is `Int? avg_size` and the tool applies its own default when it is unset.

Switches render faithfully with three deliberate gaps.
`count`, `store_const` and `append_const` become a Boolean "pass this flag" input, so `-vvv` cannot be expressed; WDL 1.1 has no string repetition, and the expression that emulates it is unreadable in output meant for editing.
A `required=True` `store_true` declares `Boolean x = false`, so a caller may omit what argparse would demand.
`nargs=1` is modeled as the scalar it is on the command line, although argparse hands the program a one-item list.

An option whose every occurrence may take more than one value, such as `append` with `nargs=2`, is an array of text in which each element is one occurrence, its values separated by spaces, so `["a b", "c d"]` passes `--pair a b --pair c d`; the element is text whatever `type` declares, so a `type=Path` pair is not localized, and a value that itself contains a space cannot be passed.
An action with `nargs=SUPPRESS` consumes nothing and is no input, so a `required=True` option of that kind, which argparse still demands, is left out.

### What the Nextflow writer emits

Measured on Nextflow 25.10.4 on 2026-10-23, with `nextflow lint` and with `nextflow run` on the rendered command lines.
A DSL2 process input has no default and no unset state.
Passing `null` as an argument fails the run with "A process input channel evaluates to null", and a `null` element in a channel reaches the script, where `x != ''` renders it as `--num null`.
`lint` also rejects a call that leaves an input out, so the caller passes `''` for an unset value or switch and `[]` for an empty list, as the document's header comment says.
The template therefore tests a scalar value option with `x != ''`, never with Groovy truth, which drops `--num 0` and `--flag false`; a list is tested by truth, since emptiness is its only false value.
A switch with no spelling for false is tested by truth, and one with a spelling for false by `x == true` and `x == false`, so that `''` renders neither.
An array option renders per action as in WDL, and is elided when the list is empty: `append` repeats the flag before each element, by joining the elements with the flag as the separator after one leading flag, and `nargs` gives one flag followed by the values joined with spaces.
The `append` form uses no closure, because an explicit parameter can collide with an input of the same name and `lint` warns that the implicit `it` is deprecated.
Defaults are not declared at all; an elided flag leaves the tool's own default in force.
With `has_output_file`, the process emits `path "${output_file_name}", emit: output_file, optional: true` beside `stdout emit: stdout`, which is the closest Nextflow comes to WDL's fallback to standard output when no name is given; every process emits `stdout`.
A process with no arguments omits `input:`, because an empty section is a syntax error.
`RESERVED_NF_NAMES` holds the Groovy keywords `lint` rejects as `val NAME`, plus names that lint accepts but that misbehave: `true`, `false`, `null`, `threadsafe` and `strictfp` fail under `nextflow run`, and `task`, `it`, `log`, `session`, `channel`, `System`, the primitive type names and commonly used class names such as `String`, `File` and `Path` resolve to an implicit variable or a class instead of the input.
Every class a Nextflow script imports by default behaves this way, so the class names are a measured selection rather than a complete set.
`process`, which the list used to hold, is accepted and runs correctly.
`in` belongs to the first group although a process whose last input is `in` lints and runs: as a binary operator it reads the next declaration's `val` as its right operand, so it fails only where another input follows.
The Nextflow keyword `include` joins it: `nextflow run` accepts `val include`, but `nextflow lint` stops with an internal error, `Range [15, 16) out of bounds`, in either position.
The contextual keywords `trait`, `var`, `yield`, `record`, `permits` and `sealed` lint and run in either position, and are not listed.

On `cnvlib.commands.AP`, the 35-process Nextflow document passes `nextflow lint` with no errors.

### Open defects

**Positional typing is a measured default, not an inference.** A positional with no declared type and no default is `FILE`, in both readers.
Over 83 positionals from `cnvlib.commands.AP`, the two fixtures, 16 samtools subcommands and the type-less positionals of Python's standard-library command-line tools, `FILE` is the right type for 78 and `TEXT` for the other 5; no cnvkit positional declares a type.
The five misses are `region`, `reg`, `prefix`, `sql` and `arguments`, the first three from samtools and the last two from `sqlite3` and `trace`, so samtools' `region` still renders as `Array[File]` and an engine will try to localize `chr1:100-200`.
No name-based rule was adopted: a filename-word list scored 65 of 83, splits one tool's own positionals between `File` and `String`, and is open-ended, while a list of string names such as `region` must leave out cnvkit's `interval`, which is a BED file.
The angle-bracket convention is no signal either, since cnvkit's `targets` and samtools' `region` are both bare words.
An option value placeholder such as `-t FILE` would be real evidence, but `Option.parse` discards it; `docopter._option_entries` yields each options-block entry's lines, which is where reading one would start.

**A subcommand after an option group is read as an argument.** The command words are the program name and the literal words written directly after it, so `Usage: prog [-v] sub <x>` wraps `prog` and declares an input `sub`, typed `File` as every positional is, which the generated task cannot pass as the word `sub`.
The pattern tree cannot tell such a word from a lowercase placeholder: `tr [-Ccsu] string1 string2` and the fixture's `cnvkit.py antitarget [-h] ... targets` have the same shape.
Reading a word as a command word when an angle-bracketed or uppercase placeholder follows it and every alternative agrees would accept `prog [-v] sub <x>`, but no synopsis among the 196 help texts of 2026-10-24 has that shape, and the rule would read `tool [-v] infile <outfile>` the same way, so it was not adopted.
A subcommand that a `Commands:` section of the help text also names would be real evidence; no reader reads such a section yet.

### Resolved

**D1** — the reader called `docopt.docopt(doc)` with no `argv`, so docopt parsed
*doc2wrapper's own command line* against the target tool's usage, always raised
`DocoptExit`, and left `positionals` empty. Now the usage line is parsed statically into
docopt's pattern tree and walked. **D4** — the output declaration emitted the literal
`"$output_file_name"` instead of an interpolation. **D7** — `docopt.py` sat at the
repository root, outside the package, so an installed wheel could not import it.
**D2** — the argparse reader set `usage` to raw `description + epilog` prose, which
`task_template.wdl` dropped at file scope; the docopt reader escaped this only by
spelling WDL's `#` itself, in a reader. Both readers now pass plain text and each
target's block template comments it per line. **D11** — the version statement moved from
`task_template.wdl` to a new `document_template.wdl`, and each writer now exposes one
`render(tasks)` that takes an iterable, so `cli.py` no longer joins rendered fragments.
Both bundled help-text fixtures still generate byte-identical WDL, verified by checksum
against the pre-change tree: the docopt path's output did not move. **D13** —
`action="version"` raised `TypeError` from `unpack_tasks`, killing any parser that
declared a version flag the idiomatic way; `_VersionAction` now joins `_HelpAction` in
the skip, and the handled-action tuple gained `_CountAction` and generalized
`_StoreTrueAction` and `_StoreFalseAction` to their base `_StoreConstAction`, so a bare
`store_const` and a `count` flag no longer take the same fatal branch. The flagship
output did not move, verified by checksum.

**D3, D6, D15 and D16**, fixed together because all four meet in how a switch and a literal are spelled.
Defaults were pasted into the template with `str()`, so `False` and an unquoted `.` reached the WDL; the docopt reader avoided this only by storing WDL text itself.
Both readers now store the Python value, and `wdlgen` spells defaults and help text as WDL literals, escaping `~{` and `${` as `\u007E{` and `\u0024{`, the one spelling both WDL string contexts read as plain text.
The argparse reader's `is_array` was inverted, so every switch became `Array[Boolean] x = []`, which a `defined()` test always passed; switches are now Boolean, rendered from their value, with `Argument.false_flag` carrying the spelling for false, which is how `BooleanOptionalAction` and `store_false` are expressed.
`Argument.flag_per_value` separates `append`, whose flag repeats per value, from `nargs`, whose one flag takes the list; each form type checks in place of the other, and a `nargs` option given the per-value form keeps only its last value.
Because `append` and `extend` add to their default rather than replace it, their input starts empty and the tool's own default applies.
A positional's requiredness follows its arity alone, since argparse's own `required` for `nargs="*"` and `REMAINDER` has differed between Python releases.
Both bundled help-text fixtures still generate byte-identical WDL, verified by checksum; cnvkit's `[Default: 150000]` exercises the docopt reader's default path, and `tests/test_generate.py` covers an integer, a float and a string.
Their Nextflow output then changed only on switch lines.

**D5**, with the Nextflow half of D6 and the harness's launcher check.
`cli.py` never imported `nfgen`; both subcommands now take `-t/--target {wdl,nextflow}`, defaulting to `wdl`, from one parent parser that also carries `-o`, and the writer table in `cli.py` is also the list of choices.
`process_template.nf` was an adaptation of the WDL template: `val output_file = "$output_file_name"` failed `nextflow lint`, and `${"-f " + x}` rendered `-f null` for an unset option.
It was rewritten against `nextflow lint` and `nextflow run`, as described under "What the Nextflow writer emits", and now reads `is_array` and `flag_per_value`.
Flags are spelled in single-quoted Groovy strings, in which `$` and `"` are plain characters, so the writer needs no spelling pass of its own.
`nextflow.enable.dsl=2` is gone from the document template, because 25.10 supports only DSL2 and the statement selected nothing.
`check_nextflow` used to count a launcher failure under an unsupported Java as a lint rejection, which a strict xfail then reported as expected; that xfail is gone, and a session probe skips instead.
Both bundled help-text fixtures and the flagship `cnvlib.commands.AP` still generate byte-identical WDL, verified by checksum.

**D12** — `RESERVED_WDL_NAMES` held only `scatter`, so a tool with an `--input`, `--output` or `--command` flag generated a task WDL rejects, and the title never went through the rename at all: `Usage: file [-v]` produced `task File`.
The list is now the WDL 1.1 specification's Reserved Keywords, including `Directory`, `hints` and `requirements`, which the specification says to treat as reserved for a future version.
That is a superset of what miniwdl 1.15 rejects, since it accepts eight of the 38: `Boolean`, `Object`, `after`, `in`, `version` and the three future ones; the specification governs any other engine that reads the document.
The title is renamed alongside the arguments, now in `render_document`, with WDL's keyword list and with Nextflow's `RESERVED_NF_TITLES`, which holds only `Session`: every capitalized entry of `RESERVED_NF_NAMES` works as a process name, measured with `nextflow lint` and `nextflow run`, while a process named `Session` lints but cannot be called.
Both bundled help-text fixtures generate byte-identical WDL and Nextflow, verified by checksum; the flagship's WDL changes only where `output` becomes `output_`.

**D14 and the name collisions**, fixed together because both are a name declared twice.
Nothing deduplicated generated names, and five sources produced duplicates: two help-text tokens that sanitize alike, a keyword rename landing on an existing name, several argparse actions sharing one `dest`, an input taking a name the WDL template declares itself, `output_file`, and two subcommands whose titles coincide.
`render.render_document` now renders every block, so the one function that sees the whole document makes argument names unique per task and titles unique per document, over the final names: the first holder of a name keeps it, and later ones take `_2`, `_3` and so on.
`output_file` is passed with the WDL keywords and takes `_`; Nextflow needs no such entry, since `emit:` labels are a namespace of their own, measured with `nextflow lint` and `nextflow run`.
The docopt reader never gives any argument but the output option, `--output` or `-output`, the name `output_file_name`, because the templates' output sections read it and the first holder of a name need not be the output option.
Titles were built with `str.title()`, which lowercases interior capitals, so subcommands `runAll` and `runall` both became `ToolRunall`; `tasktree.task_title` capitalizes the same word boundaries and leaves other letters as written, so `SAMtools view` now titles `SAMtoolsView` rather than `SamtoolsView`.
The argparse reader had never applied the legality rule to titles or `dest` names, so `/usr/bin/tool` titled a task `/Usr/Bin/Tool` and `add_argument("in-file")` declared `in-file`; both readers now share `tasktree.legal_name` and `task_title`.
Two argparse actions sharing a `dest` are modeled by what they are rather than numbered: a `store_true` and a `store_false` become one switch with a spelling for each value, as a `BooleanOptionalAction` already is, and other actions sharing a `dest`, such as cnvkit's `append_const` statistics flags, are each named after their flag, since the `dest` names the list they build together.
A switch named after its flag means "pass this flag", so a `store_false` spelled `-q` in such a group becomes an input `q` that passes `-q` when true, rather than one whose name and value disagree.
None of these flag-named inputs carries its action's default, because argparse fills a shared `dest` from one action's default alone, that of the first action whose default is not `SUPPRESS`; left unset, each passes nothing.
Both bundled help-text fixtures generate byte-identical WDL and Nextflow, verified by checksum; the flagship's output changes only in `diagram`, `genemetrics`, `gainloss` and `segmetrics`, the four tasks that declared a name twice.

**D8** — `readme` named a non-existent `README.rst`, which setuptools ignores silently, so the wheel shipped with no long description; it now names `README.md`.
`requires-python` is `>=3.10`: 3.9 reached end of life in October 2025, and 3.10 is the version `[tool.mypy]` checks against.
`project.urls` point at `etal/doc2wdl`.
The bare `setup()` shim in `setup.py` is gone, since PEP 517 builds and editable installs do not need it, and `requirements-dev.txt` lists only tools the repository configures or runs.

**D9 and the Float placeholder**, fixed together because the second is a choice of how WDL spells a type the first decides.
The readers typed one interface differently: a positional was `File` from help text and `String` from a parser, a help-text switch was `String?` tested with `defined()`, and the argparse reader kept its own copy of the type precedence, in which `type=float, default=10` became `Int`.
`tasktree.type_and_default` is now the one rule: a valueless flag is `BOOLEAN`, then a declared type decides, then the type of a stated default, and otherwise a positional is `FILE` and an option `TEXT`.
A declared number reads its own text default as a Python literal, much as argparse calls `type` on a string default and nothing else; a default the type cannot hold is dropped, and a `FILE` takes no path as a default.
`tasktree.literal_default` reads help text's `[default: ...]` notation, which is the help-text reader's business: a parser's string default is a value, and `default="."` must not be parsed.
`Argument.wdl_type` is now `Argument.value_type`, a `tasktree.ValueType`, which `wdlgen` translates on copies; the Nextflow template still does not read it.
Help-text switches take `Option.value`, already `False`, so they render as argparse's `store_true` does, and the template's `defined()` arm for a non-Boolean switch is gone.
"Equivalent" in the acceptance test means equivalent information: help text shows no `type=`, so a parser's `type=int` option with no default is `INTEGER` from the parser and `TEXT` from its help text.
The help-text reader also reads no `[default: ...]` for a positional, since argparse prints one only in the "positional arguments" block, so `nargs="?", default="."` is `TEXT` with its default from the parser and `FILE` without one from its help text.
WDL prints a `Float` placeholder "in the style `[-]ddd.dddddd`, with 6 digits after the decimal point", a rule of the 1.1 specification and unchanged in 1.2, so `1e-10` reached the tool as `0.000000`, and no WDL 1.1 function formats a number otherwise.
`wdlgen` therefore declares a `NUMBER` as `String`, its default the quoted shortest round-trip text, so the caller's text reaches the command line exactly; an inputs file must then give the value as a string, since a JSON number for a `String` input is rejected.
Both fixtures' Nextflow and the cnvkit fixture's WDL are byte-identical, verified by checksum, as is the flagship's Nextflow.
The samtools WDL changes only in its 16 switches, each now `Boolean x = false` rendered with `if x then`.
The flagship's WDL changes in 76 declarations and the `--avg-size` command line: 41 positionals from `String` to `File`, 33 `Float` inputs and `--drop-outliers`, a `type=float` option with default `10`, to `String`, and `Int avg_size = 266.6666666666667` to `Int? avg_size`, whose flag is now passed only when set; the whole document then passes `miniwdl check`.

**D10** — help-text options reached `parameter_meta` as `""`, because `Option.parse` splits each description off its flags and discards it.
`docopter.parse` annotates each `Option` with a `description`, and `_docopt.py` was left unchanged: the vendored file's own header says a feature belongs in `docopter`, and a stored description would have had to survive the four places `parse_long` and `parse_shorts` copy an option.
D10 found each description with `_option_entries` and matched it to its option by `(short, long)`, while `parse_defaults` still decided the option set; D17 below made the entries decide it, each carrying its own description.
Options that the synopsis alone names, which `parse_pattern` appends to the list, take `""`.
Because `parse_defaults` ran an entry to the next one, samtools' section headings and any epilog were read as description; a description here ends at a blank line or at a line indented no deeper than its option.
A line opening with a flag ends it only when indented less than the description, since argparse wraps prose onto a flag.
An option line with no description on it, as argparse writes an option whose flags are too wide for the help column, takes the column of the last description until its own first wrapped line states one.
Wrapped lines are joined with single spaces, except where `textwrap`, with which argparse wraps help, can have broken a word: after a hyphen that follows two letters, or a letter, a hyphen and a letter, and precedes two letters, optionally separated by a hyphen, or after a `--` that follows a word or punctuation, the lines are rejoined without a space.
A suspended hyphen, as in `pre- and post-`, or a word ending in `--`, that falls at a line end cannot be told from a broken word and loses its space; `[default: ...]` notes stay in the text, as the argparse reader keeps them.
Against the `help` strings the parsers were given, the descriptions read from `format_help()` of every `cnvlib.commands.AP` subcommand match 307 of 307 at 78 of the 91 even `COLUMNS` values from 40 to 220, among them 60, 80, 100, 120 and 200.
At `COLUMNS` of 53 or less argparse splits words longer than the help column, and at six other even values it breaks before the `--` in `heatmap`'s `'--gene'`, which leaves `' --gene'`; neither can be undone by a reader.
Of the 47 option inputs the samtools fixture generates, 45 gain a description; `-R` and `-F` separate flags from description with one space and stay empty.
Both fixtures' WDL is byte-identical outside `parameter_meta`, verified by checksum, and their Nextflow is byte-identical; the flagship's WDL and Nextflow are untouched.

**D17** — wrapped prose that opened with a flag declared an option.
Upstream's `parse_defaults` started an option at every line opening with a flag, so on cnvkit's `import-rna` help at `COLUMNS=80` a description wrapped onto `--normal` declared a second `--normal`, which took `--normalize-method`'s `[Default: polish]` and was always passed as `--normal polish`, while the real `-n, --normal` became `normal_2`.
Over the subcommands of `cnvlib.commands.AP`, four long flags were read twice at `COLUMNS=80`, one at 120 and none at 60, 100 or 200; now none is, at any even `COLUMNS` from 40 to 220.
`docopter._option_entries`, which D10 wrote to find descriptions, now decides the option set, and `parse_defaults` is deleted from the vendored file, as `docopt()` was, so that no caller can use it again.
A line opening with a flag starts an entry only when indented less than the description column, and each entry's lines reach `Option.parse` joined by line breaks, the text `parse_defaults` gave it, so flags, argument counts and defaults are read as before.
Two further rules keep that from losing what `parse_defaults` found.
The text after two spaces on an option line that itself opens with a flag, as in wget's `-q,  --quiet`, where upstream's two-space rule ends the flags early, states no column; otherwise wget's long-only options, indented to that column, would read as prose.
An entry runs across blank lines while its lines are indented deeper than its option, because clap writes `[default: auto]` as a paragraph of its own, and it ends at a shallower line, so a `[default: ...]` in an epilog indented no deeper than the options belongs to no option; one indented deeper is still read as the last option's.
Each option now carries its own description, so an option documented twice no longer takes the other entry's description.
The column is carried from one section to the next, so a later section whose option lines are indented as deep as an earlier section's descriptions would read as prose; no captured help text is laid out that way, and resetting the column at a section heading instead adds a prose fragment to pytest's options.
Both fixtures generate byte-identical WDL and Nextflow, verified by checksum, and every corpus file keeps its class.
In the corpus, `cnvkit.py batch` loses its stray `--count` and `--diagram`, and `cnvkit.py import-rna` its stray `--normal`, where `--normalize-method` now takes `polish`; both documents pass `miniwdl check`.
Over 196 help texts, the fixtures, the corpus and 180 captured from tools on the personal workstation on 2026-10-24, 84 option declarations disappear, each a fragment of wrapped prose or a second reading of a flag that keeps its first, and one appears, in R's tab-separated help, which the reader already misread.

**D18** — argparse's notation for an optional value desynchronized the synopsis.
argparse writes the value of an option with `nargs="?"` or `nargs="*"` in brackets, as in `[-p [PROCESSES]]` and `[-n [FILES ...]]`, because the value may be left out, while the options block declares the option as taking a value.
`parse_shorts` and `parse_long` therefore read the opening bracket as the value, the group's closing bracket ended the enclosing group, and `parse_pattern` raised `unmatched '('`, so `cnvkit.py batch` lost its one positional.
When a synopsis fails to parse, `docopter._read_command_line` now reads it a second time, with the brackets removed from each group that directly follows an option the parser would give the next token as its value and that holds one placeholder, optionally followed by `...`.
The test of the option mirrors the two functions' own rules, an exact match for a long option without `=` and, for a stack of short options, a first value-taking letter that is the last, and it runs on `parse_pattern`'s tokens rather than on the text, so git's attached `-u[<mode>]` is found too.
Any other group keeps its brackets, among them an alternation, a group of two arguments, a group that opens with a flag, and the form Python releases before 3.9 wrote for `nargs="*"`, `[FILES [FILES ...]]`, which still degrades.
The second reading runs only after the first fails, never as a pre-pass: of the 1,948,716 patterns of one to six tokens drawn from `[ ] ( ) | -p -q -pq --proc --flag A`, with `-p, --proc` taking a value, a pre-pass would rewrite 688, and 32 of those parse as written, each with a bracket left unclosed, such as `tool [-p [A]`.
It runs on a copy of the options-block entries, so a synopsis that fails both readings leaves the option list and the warning exactly as before.
Over 196 help texts, the fixtures, the corpus and the 180 captured on 2026-10-24, 190 records are byte-identical and six move from degraded to parsed.
`cnvkit.py batch`, in both its captures, yields exactly its one positional, and `tox` yields one, its subcommand placeholder with the arguments after it.
`git-commit` yields 11 positionals, among them fragments of `--[no-]status`, and `bsd-make` and `gnu-make`, whose options blocks follow the synopsis with no blank line, yield 142 and 180 positionals where the desynchronization used to hide them.
Those three also gain 39 option declarations named only in what is read as the synopsis, such as `--just-print,` with its comma; no option-block entry or description changes in any of the six.
Both fixtures generate byte-identical WDL and Nextflow, verified by checksum, and the corpus's `cnvkit.py batch` gains `Array[File] sample_fnames` and passes `miniwdl check` and `nextflow lint`.
The option keeps its argument count of 1, so the generated input passes `--processes` with a value or not at all, and cannot pass the bare flag, which cnvkit reads as "use every CPU".
Not covered: an optional prefix of a value, such as curl's `--proxy [protocol://]host`, which the rule would misread in a synopsis that otherwise parses; no captured synopsis does.
`nargs="+"`, written `-n NORMAL [NORMAL ...]`, whose group follows the value rather than the option, is D22's.

**D19** — the command words were read from the head of the first synopsis line and then removed from the pattern tree wherever their names occurred.
An alternative naming another subcommand therefore kept its word: `Usage: git log <a>` followed by `git show <b>` read `show` as a spelling of `<a>` and moved `<b>` into a slot of its own, with no warning.
`docopter._read_command_line` now keeps those of the text's command words that the first alternative's tree also begins with, and removes as many leading command words from every alternative by position, never by name, so `<a>` and `<b>` share one slot.
A word that only the text reads as a command word is therefore an argument: `Usage: prog sub|alt <x>` offers a choice between `sub` and `alt`, and its prefix used to be `prog sub|alt`, a shell pipe.
An alternative that begins with other words produces a warning on standard error, since a task wraps one command, and the task wraps the first alternative's.
The warning shares no text with the one for a synopsis that does not parse, so `tests/test_corpus.py` still classifies such a file as parsed.
`docopter.parse` now returns the CLI prefix beside the usage text, and `transform` takes it rather than reading the text again.
GNU help opens each further synopsis line with `or:`, and `formal_usage`, which splits alternatives at the program name, made that token the last word of the alternative before it and so an input named `or`; `_CONTINUATION` removes it before `formal_usage` reads the synopsis, and the usage text the templates comment keeps it.
Over 196 help texts, the fixtures, the corpus and the 180 captured on 2026-10-24, no CLI prefix changes, no record of the fixtures or the corpus moves, and 15 captured records change.
In 14 of them an input named `or` or a spelling `or:` is gone, among them `git-log`, which also warns and aligns its two alternatives, and `samtools-sort`, which warns because its options block, read as usage, mentions `samtools index`.
The fifteenth, `bsd-sed`, warns because its first alternative begins `sed script`, where `script` is a placeholder that the text, now as before, reads as a command word; the `script` in its second alternative's `-e script` is no longer removed, so `script_file` becomes an input of its own.
In `samtools-index`, three words `index` in that file's options block, which is likewise read as usage, are no longer removed, since they are not the leading words of an alternative.
Both fixtures generate byte-identical WDL and Nextflow, verified by comparing the generated text before and after.

**D20** — an options block indented with tabs yielded no options, because `_option_entries` measured indentation in spaces alone.
Every bedtools subcommand writes its entries as `\t-wa\tWrite the original entry ...`, so `bedtools intersect` declared only the two flags its synopsis names.
`_option_entries` now reads the text with its tabs expanded by `docopter._expanded`, to stops eight columns apart as a terminal shows them, so indentation and the description column are counted in the columns a tab occupies on screen.
A tab after text advances at least two columns: one that would advance one advances nine, to the stop after the next.
Without that rule, bedtools' `\t-sorted\tUse ... (-k1,1 -k2,2n)` leaves one space after `-sorted`, where `Option.parse` looks for two, and the description's words become flags, among them `-k2`; `-header` likewise became an option taking a value.
The width is measured rather than conventional: with stops four columns apart, or with each tab replaced by two spaces, R's continuation line of three tabs falls left of its description column and declares `--no-init-file` a second time.
Only the options-block reading sees the expanded text; the usage text the templates comment keeps its tabs.
Over 196 help texts, the fixtures, the corpus and the 180 captured on 2026-10-24, nine records change and no positional, CLI prefix or warning moves.
The six bedtools records that change, five captured subcommands and the corpus's `bedtools intersect`, go from between 1 and 5 options to between 10 and 29, `bedtools intersect` from 2 to 29, and across the nine records 144 option declarations become 249, of which 227 rather than 79 carry a description and 63 rather than 89 take a value.
`vim` and `R` separate a flag from its description with a tab, so `vim`'s switches, which read their descriptions as values, become switches again, and `R` recovers `--vanilla`.
The captured `samtools view` gains two spurious options, from example command lines in its notes that are indented with a tab and fall left of the description column carried from the options block, the gap D17 describes.
bedtools writes no placeholder for an option's value, so options such as `-f`, a minimum overlap fraction, became switches; D27 reads the type bullet many of them carry.
Both fixtures generate byte-identical WDL and Nextflow, verified by comparing the generated text before and after, and the corpus's `bedtools intersect` passes `miniwdl check` and `nextflow lint`.

**D21** — help-text notation leaked into flags.
docopt reads an options-block entry's flags as written, so samtools' `--region[s]-file`, `--target[s]-file` and `--excl[ude]-flags` were passed literally, and samtools 1.23.1 rejects each as an unrecognized option while accepting `--region-file`, `--target-file` and `--excl-flags`.
`docopter.transform` now passes the flag `_passable_flag` gives for an option's documented spelling.
A bracketed group of letters, digits and hyphens followed by more of the name is an optional part, and is removed, leaving the shortest spelling the notation names, so git's `--[no-]verbose` passes `--verbose`, never its negation, and the input is named from that flag.
Any other bracket names no single flag, and the option is left out with a warning on standard error: a group that ends the flag, as in `--color[=WHEN]` and `-W[level]`, makes its value optional and attached to the flag, which a task cannot express, and perl's `-[mM][-]module` is a set of letters.
The warning names the flag as `Option.parse` stored it, which for `--color[=WHEN]` is `--color[`, since it splits the flags at `=`.
A flag with no brackets was unchanged until D25, and the change is confined to `transform`, so `parse`, and with it every outcome class, is unchanged: removing an entry from the options list instead would change how the synopsis reads the flags it names.
An option is left out, rather than passed by another spelling its entry gives, because that spelling may take the same optional attached value, as git's `-S, --[no-]gpg-sign[=<key-id>]` does.
Over 196 help texts, the fixtures, the corpus and the 180 captured on 2026-10-24, 254 options in 35 files carry a bracket in their flag; 135 are passed by their shortest spelling, 118 of them in git subcommands, and 119 are left out, 15 of them bq's absl options, whose `--[no]quiet:` ends in a colon.
35 records change, none in its CLI prefix or positionals.
A flag rewritten to the spelling of another option, as git-commit's `--[no-]dry-run` meets the `--dry-run` its synopsis names, gave two inputs passing one flag, which the writer's unique names do not prevent; D26 resolves this.
The cnvkit fixture generates byte-identical WDL and Nextflow; the samtools fixture changes only in its three bracketed options, whose inputs are now `region_file`, `target_file` and `excl_flags`.

**D22** — argparse's notation for an option taking one or more values put a positional in the synopsis.
argparse writes an option with `nargs="+"` as `-n NORMAL [NORMAL ...]`, in the synopsis and in the options block alike, and the option reads only `NORMAL` as its value, so `[NORMAL ...]` became an optional list of positionals, as it did in `cnvkit.py import-rna`.
The synopsis parses as written, so D18's second reading never reaches it.
`docopter._documented_options` now records, as an option's `repeated`, the placeholder its entry's flags repeat in this form, and `_without_value_repeats` removes the group `[ NORMAL ... ]` that directly follows such an option and the value it reads, when the group repeats that value and the option's `repeated` names it.
The entry is required as evidence because BSD find writes `-f path [path ...]` for an option followed by a list of operands; find prints no options block, so its synopsis is unchanged.
The removal runs before the first reading, which D18 avoided for its own rewrite because removing the brackets around a value can leave other brackets unmatched.
Removing a whole group cannot: of the 5,380,839 patterns of one to seven tokens drawn from `[ ] ( ) | ... -p -q A`, with `-p` repeating `A`, it applies to 18, of which 9 fail to parse both before and after, and the other 9 parse both times and differ only in the removed list.
An option whose entry repeats its placeholder is an array input with one flag before its values, as the argparse reader models `nargs="+"`, so `--normal` becomes `Array[String] normal = []`, passed as `--normal a b`; pylint's `--help-msg`, which its synopsis does not name, becomes one too.
An `--output` option of that form is such an input rather than the declared output file, because the templates' output sections name one file.
Over 196 help texts, the fixtures, the corpus and the 180 captured on 2026-10-24, three records change: both captures of `cnvkit.py import-rna` lose the positional `NORMAL`, and pylint's `--help-msg` becomes an array.
No outcome class or CLI prefix moves, and the corpus's `cnvkit.py import-rna` passes `miniwdl check` and `nextflow lint`.
Both fixtures generate byte-identical WDL and Nextflow, verified by comparing the generated text before and after.

**D23** — an options block or prose written directly under the synopsis was read as usage.
`printable_usage` reads the usage section to the first blank line, and a wrapped synopsis line opening with a flag could start an options-block entry, so `python3 -m zipfile` declared `-t` a second time from its synopsis and degraded, while `samtools stats` read its options block as synopsis and degraded too.
Skipping every entry in the usage section would fix zipfile but read the 208 words of samtools stats' options block as positionals; the two need a boundary inside the section.
`docopter._synopsis` now ends the synopsis at the first line, after the marker's, that is indented less than the program name, where the marker's line names the program, since argparse aligns a wrapped synopsis line under the program name or deeper and hand-written help aligns it there or a tab further, while a heading, an options block or prose is indented less.
A line opening with GNU's `or:` continues the synopsis however it is indented, and indentation counts the columns a tab occupies, as D20's does.
The synopsis also ends at a line opening with a flag whose description follows two spaces later, as `_option_entries` reads an option line, which was the only end it had where the marker stands alone on its line until D24.
A heading rule, such as a line ending in `:`, was measured and adds nothing: of the 48 usage sections among the 196 help texts that run past their synopsis, the two rules cut 45 exactly where a reader would end the synopsis, `java`'s one line early, since its further alternatives open with `or` and no colon, and two too late, which D24 resolves, while a heading rule with the two-space rule cut only 20 exactly.
No line of the synopsis starts an options-block entry, and the usage text the templates comment ends with the synopsis, so `curl`'s comment is its synopsis line rather than 259 lines that repeat `parameter_meta`.
Over 196 help texts, the fixtures, the corpus and the 180 captured on 2026-10-24, 53 records change, no CLI prefix moves, and their inputs fall from 4,152 to 1,414.
Seven records move from degraded to parsed: both captures of zipfile and of `samtools stats`, `curl`, `plot-bamstats` and `python3`; four remain degraded, for other reasons.
`htsfile` yields exactly `FILE` and `DESTFILE`, and `bsd-make` and `gnu-make`, whose options blocks follow the synopsis with no blank line, one positional each, `target`, where D18 had left 142 and 180.
189 option declarations disappear, none with a description, each read from a line of usage that is not synopsis or from prose, and three appear: `bsd-diff`'s `-y` and `--side-by-side` from its synopsis, and `ssh-keygen`'s `-s` as a switch, where its wrapped synopsis line had declared it with a value.
The corpus's zipfile, `samtools stats` and `htsfile` pass `miniwdl check` and `nextflow lint`, and both fixtures generate byte-identical WDL and Nextflow, verified by comparing the generated text before and after.

**D24** — prose under a synopsis whose `Usage:` marker stands alone on its line was read as usage.
Where the marker stands alone, nothing states the program name's column, so D23's indentation rule cannot apply, and only a blank line or an option line ended the synopsis.
`prefetch` describes each alternative on the line after it, and Info-ZIP's `zip`, whose `Usage:` ends its title line, continues its synopsis with a paragraph of prose; both were read as positionals.
Taking the column from the first synopsis line instead ends nothing in the survey, since prefetch's prose is at the synopsis's column and zip's deeper, and it would cut `pip install`, which wraps at column 0.
`docopter._synopsis` now also ends such a synopsis at a line that opens with a word other than the program name, not written in capitals, and that carries no usage notation: no bracket, parenthesis, brace, angle bracket, bar, ellipsis or token opening with a hyphen.
A wrapped synopsis line carries notation, as pip's `[package-index-options] ...` does, and a further alternative opens with the program name; a word in capitals may be a placeholder that argparse wrapped onto a line of its own.
The rule applies only where the marker stands alone: under a marker that names the program, argparse wraps a bare lowercase placeholder such as cnvkit's `targets` onto its own line, which the rule would cut.
A heading such as `Options:` under such a synopsis now ends it too, while one in capitals, `OPTIONS:`, still reads as a positional, as does prose that carries notation; no captured help text has either.
`java`'s further alternatives, which open with `or` and no colon, are still cut, because each is followed by a parenthesized description, such as `(to execute a jar file)`, that a continuation rule would read as synopsis.
Over 196 help texts, the fixtures, the corpus and the 180 captured on 2026-10-24, two records change, `bsd-zip` and `prefetch`, each losing the inputs its prose declared, and no option, CLI prefix or warning moves; the extent of the synopsis is unchanged for `pip install` and `pip download` at `COLUMNS` of 60, 70, 80, 100, 120 and 200.
Both fixtures and the corpus generate byte-identical WDL and Nextflow, verified by comparing the generated text before and after.

**D25** — help-text notation other than brackets reached the command line.
`Option.parse` splits flags only at commas, spaces and `=`, so SRA Toolkit's and npm's `-A|--accession` was one short flag with a bar in it, clap's counted `--verbose...` kept its ellipsis, absl's `--apilog:` its colon, and text that is no flag, such as the end-of-options entry `--`, tree's rulers of hyphens, zstd's `-#` and gcc's `-W<warning>`, was passed as written.
`docopter._documented_options` now reads an entry's flags with a bar between a one-letter short flag and a long flag as a comma, and with an ellipsis directly after a flag removed, before `Option.parse` sees them, so `-A|--accession` has a short and a long spelling and is passed as `--accession`, and `-h|--help` and `-V|--version` are recognized and skipped.
Any other bar is left as written, so `--color[=WHEN]|--colour`, `-a|-b|-c` and `-ab|--long`, of which the first two, read as spellings, would pass `--colour` with a value and `-c` alone, are left out with D21's warning; no captured help text has any of them.
The long spelling is the one passed, as for any option with both: probed against fastq-dump, prefetch and npm, every short spelling and all but one long spelling are accepted, fastq-dump rejecting its own documented `--verbose`, while prefetch documents both `-o|--order` and `-o|--output-file`, so passing short spellings would make two inputs pass `-o`, one of them with the wrong meaning.
`_passable_flag` now requires every flag, not only a bracketed one, to be one or two hyphens followed either by word characters, which may include `.`, `@`, `+` and hyphens but may not end in `.`, or by one of `%`, `,` and `?`; any other option is left out with D21's warning.
That keeps `-nostdinc++`, `--disable-@files`, gcc's `-I-`, bedtools' `-name+` and BSD ls's `-@`, `-%` and `-,`, which those tools accept, and leaves out gcc's `-###`, a real flag that has no input.
absl's colon is left out rather than removed, because bq reads such a flag's value only when attached with `=`: `bq --apilog=/tmp/x version` writes the log, while `bq --apilog /tmp/x version` reads the path as its command, and a task passes a value after a space.
bq's 15 `--[no]x:` switches stay left out as before; they could be expressed with `Argument.false_flag`, but `Option.parse` reads each as taking a value, since absl separates a flag from its description by one space.
Over 196 help texts, the fixtures, the corpus and the 180 captured on 2026-10-24, 32 records change, none in its CLI prefix or positionals: 59 inputs are renamed after their passable flag, such as `A__accession` to `accession`, 90 are left out, and 86 warnings appear.
In the corpus only `jq` changes, losing the `arg_` input its `--` entry declared; the changed `fastq-dump`, `prefetch`, `npm-install`, `bq`, `uv` and `tree` documents pass `miniwdl check` and `nextflow lint`.
Both fixtures generate byte-identical WDL and Nextflow, verified by comparing the generated text before and after.
Not covered: a bracketed value spelled inside the synopsis, as in `ls [--color[=WHEN]] FILE`, still registers a switch `--color` and a positional `WHEN`, since the synopsis is read before any flag is rewritten; no captured help text does so.

**D26** — two inputs could pass the same flag.
The writer makes names unique, never flags, and the help-text reader produced options that pass one flag from three sources: an option documented twice, as gcc documents `-fcf-protection` both as a switch and as `-fcf-protection=<value>`; a line of prose read as an entry; and a synopsis-only option whose spelling the options block writes otherwise, since `parse_pattern` compares the synopsis's `--dry-run` with the documented `--[no-]dry-run` before D21 rewrites it, finds no match, and appends an option of its own.
Over 196 help texts, 77 inputs in 12 files passed a flag that another input of the same task already passed.
`docopter.transform` now passes each flag from one option, chosen by `_one_per_flag`: among the options passing a flag, the first with a description, and where none has one, the first, and every other is left out with a warning on standard error.
A synopsis-only option has no description and follows every documented one, so the documented option wins without a separate rule, and so does a described entry over a prose fragment: preferring the first alone would keep conda-create's prose line `--dry-run or --download-only is specified.` as `--download-only`, taking a value, and gcc's `-fcf-protection=<value>`, rendered `-fcf-protection full`, which clang reads as an input file, over the described switch.
The flag that decides is the one `_passable_flag` returns, never the input name, so flags that differ but sanitize alike, such as torchrun's `--log-dir` and `--log_dir`, stay two inputs and the writer numbers them.
The rule is in `transform` rather than `parse`, because the option list is `parse_pattern`'s input: removing an option there changes how the synopsis reads its flags, and upstream raises on a flag documented twice that the synopsis names, which is why `conda-create` and `zstd` degrade.
No arity and no text are merged between the declarations of a flag, so a form is lost where two declarations differ in arity: `du`'s `--time` keeps its switch and loses `--time=WORD`, which `du` accepts only attached.
Two options that GNU documents with a short flag of their own beside a long flag taking a fixed value, `ls`'s `-p, --indicator-style=slash` and `sort`'s `-C, --check=silent`, are left out, since docopt names each by its long flag.
Over the 196 help texts 12 records change, each only by losing inputs, and no CLI prefix or positional moves; the git-commit synopsis's `--squash <commit>` still declares a positional `commit`, since the synopsis registers `--squash` as a switch before the rewrite.
Both fixtures and the corpus generate byte-identical WDL and Nextflow, verified by comparing the generated text before and after.

**D27** — a bedtools option that takes a value was read as a switch.
`Option.parse` gives a flag a value only where its entry writes a placeholder after it, and bedtools writes none: `-f` is documented as `\t-f\tMinimum overlap required as a fraction of A.`, with `- FLOAT (e.g. 0.50)` on a line of its own below, so the generated task could pass `-f` but never its value.
Probing each documented option of the 43 bedtools 2.31.1 subcommands that print a `Usage:` marker against the binary, by passing the flag before a sentinel token, found 145 options that take a value, 131 of them read as switches.
`docopter._value_kind` now reads a description line that opens with a dash and a type word, `INT`, `INTEGER`, `FLOAT` or `STRING`, in capitals, or in parentheses in any case, as the type of the flag's value: an integer, a float where the line also names a float, as slop's `- (Integer) or (Float, e.g. 0.1) if used with -pct.` does, and text where it names a string.
`_documented_options` records it as `declared_type` for an option with no placeholder, and `transform` gives such an option a value of that type, with no default, since `Option.parse` reads a `[default: ...]` only for a flag with a placeholder; WDL declares a float as `String`, as it does every `NUMBER`.
The argument count itself is left at 0, so the synopsis is read as before: given a value before `parse_pattern` runs, slop's, flank's and shift's `[-b <int> or (-l and -r)]` degrade, as `-r` takes the closing parenthesis as its value.
Over the 43 subcommands 44 options in 22 of them take a value this way, every one a value-taker by the probe.
Prose that only mentions a type is not read, whether as gcc's "Trap on integer overflow" under a switch or as a bullet such as `- Integer overflow is trapped.`, which is why a type word outside parentheses must be in capitals, nor is a default stated in prose, which bedtools writes under switches as often as under value-takers, `- By default, overlaps are reported without respect to strand` among them.
The rest of the 131 are still switches: their descriptions name no type, as `-g`'s "Provide a genome file" does, or, for 57 of them, such as `-a` and `-b`, there is no description to read, since only the synopsis names the flag.
Over 196 help texts, the fixtures, the corpus and the 180 captured on 2026-10-24, four records change, `bedtools genomecov`, `bedtools merge` and both captures of `bedtools intersect`, and nothing outside bedtools; no CLI prefix, positional or warning moves.
Both fixtures generate byte-identical WDL and Nextflow, verified by comparing the generated text before and after, and the corpus's `bedtools intersect` passes `miniwdl check` and `nextflow lint`.

**D28** — a help-text positional had no description.
argparse lists each positional with its help under "positional arguments:", as in `  targets               BED or interval file ...`, and other tools write `<PATTERN>` or `FILE` entries alongside their options, but an options-block entry starts only at a flag, so `parameter_meta` carried only a positional's alternative spellings.
`docopter._positional_descriptions` now reads the entries a second time, once `_read_command_line` has named the positionals, with `_option_entries` also starting an entry at a line whose first word has a positional's identifier; the first entry with a description that names any spelling of a positional describes it, joined as an option's description is, the same precedence D26 gives a flag's declarations.
Such a line starts an entry under the same rule as an option line, indented less than the description column, so prose that opens with a positional's name inside a description is not read.
The second reading's options are discarded, so the option set is the first reading's, and the description column it learns from positional entries reaches no option.
No section heading is required: 31 of the 196 help texts have one, and requiring it loses `dos2unix`, `mypy` and `openssl dgst`, while lz4's "Advanced arguments:" and mypy's "Optional arguments:" head options.
`transform` writes the description and then, on a line of its own, the `Also accepts:` list, as the argparse reader joins the texts of a paired switch.
Over the subcommands of `cnvlib.commands.AP` at `COLUMNS=80`, all 34 positionals with help get a description equal to the parser's `help`, once the line breaks and indentation written inside cnvkit's own help strings are collapsed, which the argparse reader passes through as written.
Over 196 help texts, the fixtures, the corpus and the 180 captured on 2026-10-24, 38 of 532 positionals in 27 files gain a description, and only `parameter_meta` moves; no input, warning, CLI prefix or Nextflow line changes.
Two kinds are not what a reader would write: argparse's subcommand placeholder, such as `{batch,target,...}`, takes the whole list of subcommands as its description, and GNU `date`'s `FORMAT` takes the text after two spaces in the prose line `FORMAT controls the output.  Interpreted sequences are:`.
The cnvkit fixture's `targets` gains its description; the samtools fixture's WDL and both fixtures' Nextflow are byte-identical, verified by comparing the generated text before and after, and the changed corpus documents pass `miniwdl check`.

**D29** — an action with `nargs=SUPPRESS` became an input although it takes no value.
Such an action consumes no command-line strings and never writes its `dest`, as measured on 2026-10-25 on Python 3.13 to 3.15 and read in the `argparse` source of 3.10 to 3.12: `--hush` alone is accepted and does nothing, `--hush v` is rejected, and a positional so declared consumes nothing.
The reader declared both as value-taking inputs, rendering `~{"--hush " + hush}` and `~{select_first([hidden, ""])}`, so a caller who set either produced a command line the tool rejects.
`unpack_tasks` now skips them beside `--help` and `--version`, before `_arguments` groups actions by `dest`: an action sharing a `dest` with `--real` would otherwise rename it after its flag and drop its default, which argparse takes from `--real` alone when `--real` is declared first.
Declared before `--real`, the skipped action's own default, `None` when it declares none, fills the `dest` instead, since argparse takes the first default that is not `SUPPRESS`, and the task, which declared `--real`'s default, did not reproduce that until D31.
`argparse.SUPPRESS` therefore leaves the positional arity rule, where it was a copy of CPython 3.12's own list.
Both fixtures, the flagship and `mypy.dmypy.client.parser` generate byte-identical WDL and Nextflow, verified by checksum.

**D30** — an `append` option taking several values per occurrence lost its grouping.
The reader modeled `action="append", nargs=2` as a flat array with a flag per value, so `[a, b, c, d]` rendered `--pair a --pair b --pair c --pair d`, which argparse rejects with "unrecognized arguments: b d"; `extend` with `nargs=2` failed the same way, and `append` with `nargs="+"` was accepted but read as one-item lists, `[['a'], ['b']]`.
mypy's `--shadow-file SOURCE_FILE SHADOW_FILE` and ipykernel's `--env ENV VALUE` have this shape.
`argparser._is_grouped_option` now selects these options, and an element of such an array is one occurrence, its values written as text separated by spaces, so `["a b", "c d"]` renders `--pair a b --pair c d` in both targets through the existing per-value arm, because placeholders are unquoted; the element is declared text through `type_and_default`, whatever `type` says, and `parameter_meta` gains a line stating the convention.
`extend` with `nargs="+"` or `"*"` is left as it was, since a flag per value reaches the tool as the same flat list, and so is an `append` positional, which occurs only once on a command line and so is already rendered correctly.
A nested `Array[Array[T]]` was measured and not adopted.
WDL 1.1 has no `map` function, so for an input named `pair` the command line needs `flatten(transpose([prefix("--pair ", transpose(pair)[0]), transpose(pair)[1]]))`, generated per arity.
It must also be guarded, since `transpose(pair)[0]` fails on an empty array, and the whole placeholder, `~{if length(pair) > 0 then sep(" ", flatten(transpose([prefix("--pair ", transpose(pair)[0]), transpose(pair)[1]]))) else ""}`, is 125 characters.
It cannot express `nargs="+"`, since `transpose` rejects ragged rows.
What the text element loses is the element type: a `type=int` pair is `Array[String]` and checked only by the tool, and a `type=Path` pair is not localized by the engine.
A value that itself contains a space cannot be passed either, since the placeholders are unquoted and the shell splits it, as it splits every other value.
On 2026-10-25 the rendered command line parsed back to `[[1, 2], [3, 4]]` under the declaring parser, and `nextflow run` rendered the same line; `tests/test_generate.py` asserts both.
Both fixtures, the flagship and `mypy.dmypy.client.parser` generate byte-identical WDL and Nextflow, verified by checksum.

**D31** — an input declared the default of a `dest` that argparse fills from an earlier action.
argparse sets an absent attribute from the first action, in declaration order, whose default is not `SUPPRESS`.
The reader skipped actions that are no input before reading defaults, so `--ghost`, with `nargs=SUPPRESS`, declared before `--real` with `default="d"`, left argparse's `x` at `None` while the task declared `String x = "d"` and passed `--real d`.
`argparser._dest_defaults` now reads the rule once over every action of the parser, and an input declares its action's default only where that action fills the `dest`; `_paired_switch`, which applied the rule within its own two actions, uses it too.
With no input set, the task then passes what the bare tool sees, which `tests/test_generate.py` asserts against `parse_args([])`.
Over 424 synthetic parsers, output changes only where an action that is no input is declared before another action writing its `dest`; no real parser among the 30 reachable from mypy, miniwdl, torchrun, `build`, numpy's f2py, keyring and `mypy.dmypy.client` has that shape.
Both fixtures, the flagship and `mypy.dmypy.client.parser` generate byte-identical WDL and Nextflow, verified by checksum.

### Working on the vendored parser

`doc2wrapper/_docopt.py` is docopt 0.6.2 with every definition of the runtime argv-matching path deleted whole, and `parse_defaults` with it, and nothing else changed except two regex literals that gained the `r` prefix they always needed.
That property is what makes it reviewable against upstream, so **do not reformat it** and do not add features to it.
Both deletions were deliberate.
`docopt()` is the function whose `argv=sys.argv[1:]` default caused D1, and `parse_defaults` the one that read wrapped prose as options; with both gone, neither bug can return through a call to its familiar name.

`DocoptExit` survives the cut even though nothing in the static path raises it, because
`parse_long` and `parse_shorts` evaluate it in `tokens.error is DocoptExit` guards. That
is not a stylistic scruple: deleting the class raises `NameError` on any usage line that
spells a flag, which is most of them. The samtools fixture hides this by writing
`[options]` instead.

The tree is formatted with `ruff format`, configured in `pyproject.toml` to exclude `_docopt.py` even when the file is passed by name.
The whole-tree reformat that adopted it is a commit of its own, listed in `.git-blame-ignore-revs`; keep any future formatter change the same way, never as a side effect of another change.

## Conventions

- Formatting and linting are `ruff format` and `ruff check`, both clean across the tree; keep them so.
- Both readers must keep producing the same template-kwargs `dict`. Widening the contract
  means updating `tasktree.Argument`, both readers, and both templates together.
- Prefer fixing a defect in the writer or the model over fixing it in a reader; a bug that
  appears in one reader's output usually belongs to the layer both share.
- Determinism includes ordering: preserve the order in which arguments appear in the
  source interface, and never iterate an unordered set to build `cli_args`.
- Committed generated output, if any is ever added as a fixture, is regenerated by the
  tool and never hand-edited.

## Handoff

Commit completed, verified work without waiting for approval.
Pushing to `main` remains a handoff action: report it and wait, unless the maintainer has asked for the push.
