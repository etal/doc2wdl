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

`tests/corpus/` holds 17 help texts captured from real tools on 2026-10-24 and 2026-10-29, chosen to represent the shapes the reader meets rather than as a sample.
`tests/corpus/manifest.tsv` records, for each file, the command that produced it, the tool's version, the outcome class, and what the reader makes of the file; `tests/test_corpus.py` asserts every file's class, and `cut -s -f2 tests/corpus/manifest.tsv | sed 1d | sort | uniq -c` counts them.
Because the corpus is not a sample, its counts describe the corpus and not how often a shape occurs among tools; this file states no rates, since a rate measured over files that are not committed cannot be reproduced.

- **Hard failure: 3.** No single `usage:` marker: none at all (`tar`), a Cobra heading spelled `USAGE` without a colon (`gh repo clone`), or two markers (`python3 -m timeit`, whose docstring has both "Library usage:" and "Command line usage:").
  These raise `docopter.UsageNotFound`, which the CLI reports as a message and exit status 1.
- **Degraded: 1.** Where `parse_pattern` raises on the synopsis, the positionals degrade to empty with a warning on standard error, and the options survive.
  `zstd` documents `-o` twice, once for its output and once for the dictionary builder, and its synopsis names `-o`, which upstream's parser rejects as ambiguous; `tests/test_docopter.py` also covers the class with synthetic help text.
- **Parsed: 13.** `wgsim`, `cnvkit.py batch`, `htsfile` and `ls` yield exactly their positionals.
  The rest run to completion with extra positionals: summary placeholders such as `[OPTIONS]` and `...` (`tabix`, `samtools stats`, `bedtools intersect`), option values the reader cannot attribute to an option (`python3 -m zipfile`, whose `-e` takes two values), or a placeholder split at its space (`jq`'s `<jq filter>`).
  argparse's `X [X ...]` for a positional with `nargs="+"` becomes two positionals, one required and one an optional array (`miniwdl check`, `cnvkit.py import-rna`).
  Two write further alternatives after GNU's `or:`, which share slots by position: `samtools merge`'s second alternative writes `<out.bam>` where its first, in which `-o` takes that placeholder, writes `<in1.bam> ...`, and `git log`'s names `git show`, so a warning names the command wrapped and `<object>...` shares the slot of `<revision-range>`, while `<options>` and `--` are read as positionals.

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
An action class the reader does not model, which a tool defines for itself as mypy does for `--version`, is read by its command-line shape: a flag that takes no value passes when set, and any other takes values as a `store` option does, without its default, and with a warning on standard error naming the class.
So a version flag declared with such a class is an input, `Boolean version_ = false` in mypy's task, and a class that takes several values per occurrence is read as `store` is, without the grouping `append` has.

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
When a synopsis fails to parse, the reader now reads it a second time, in `docopter._read_pattern`, with the brackets removed from each group that directly follows an option the parser would give the next token as its value and that holds one placeholder, optionally followed by `...`.
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
Not covered: an optional prefix of a value, such as curl's `--proxy [protocol://]host`, which the rule would misread in a synopsis that otherwise parses; D43 reads such a prefix where it is written directly before the rest of the value.
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
A bracketed group of letters, digits and hyphens followed by more of the name is an optional part, and is removed, leaving the shortest spelling the notation names, so git's `--[no-]verbose` passes `--verbose`, never its negation, and an entry whose only long flag is that one names its input `verbose`.
Any other bracket names no single flag, and the option is left out with a warning on standard error: a group that ends the flag, as in `--color[=WHEN]` and `-W[level]`, makes its value optional and attached to the flag, which a task cannot express, and perl's `-[mM][-]module` is a set of letters; D47 reads the first two as the switch.
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
Any other bar is left as written, so `--color[=WHEN]|--colour`, `-a|-b|-c` and `-ab|--long`, of which the first two, read as spellings, would pass `--colour` with a value and `-c` alone, are left out with D21's warning; no captured help text has any of them, and since D47 the first passes the switch `--color`, its bar being part of the value `Option.parse` splits off at `=`.
The long spelling is the one passed, as for any option with both: probed against fastq-dump, prefetch and npm, every short spelling and all but one long spelling are accepted, fastq-dump rejecting its own documented `--verbose`, while prefetch documents both `-o|--order` and `-o|--output-file`, so passing short spellings would make two inputs pass `-o`, one of them with the wrong meaning.
`_passable_flag` now requires every flag, not only a bracketed one, to be one or two hyphens followed either by word characters, which may include `.`, `@`, `+` and hyphens but may not end in `.`, or by one of `%`, `,` and `?`; any other option is left out with D21's warning.
That keeps `-nostdinc++`, `--disable-@files`, gcc's `-I-`, bedtools' `-name+` and BSD ls's `-@`, `-%` and `-,`, which those tools accept, and leaves out gcc's `-###`, a real flag that has no input.
absl's colon is left out rather than removed, because bq reads such a flag's value only when attached with `=`: `bq --apilog=/tmp/x version` writes the log, while `bq --apilog /tmp/x version` reads the path as its command, and a task passes a value after a space.
bq's 15 `--[no]x:` switches stay left out as before; they could be expressed with `Argument.false_flag`, but `Option.parse` reads each as taking a value, since absl separates a flag from its description by one space.
Over 196 help texts, the fixtures, the corpus and the 180 captured on 2026-10-24, 32 records change, none in its CLI prefix or positionals: 59 inputs are renamed after their passable flag, such as `A__accession` to `accession`, 90 are left out, and 86 warnings appear.
In the corpus only `jq` changes, losing the `arg_` input its `--` entry declared; the changed `fastq-dump`, `prefetch`, `npm-install`, `bq`, `uv` and `tree` documents pass `miniwdl check` and `nextflow lint`.
Both fixtures generate byte-identical WDL and Nextflow, verified by comparing the generated text before and after.
Not covered: a bracketed value spelled inside the synopsis, as in `ls [--color[=WHEN]] FILE`, still registered a switch `--color` and a positional `WHEN`, since the synopsis is read before any flag is rewritten; BSD grep's `[--context[=num]]` and git's `[--exec-path[=<path>]]` do so, and D42 reads them.

**D26** — two inputs could pass the same flag.
The writer makes names unique, never flags, and the help-text reader produced options that pass one flag from three sources: an option documented twice, as gcc documents `-fcf-protection` both as a switch and as `-fcf-protection=<value>`; a line of prose read as an entry; and a synopsis-only option whose spelling the options block writes otherwise, since `parse_pattern` compares the synopsis's `--dry-run` with the documented `--[no-]dry-run` before D21 rewrites it, finds no match, and appends an option of its own.
Over 196 help texts, 77 inputs in 12 files passed a flag that another input of the same task already passed.
`docopter.transform` now passes each flag from one option, chosen by `_one_per_flag`: among the options passing a flag, the first with a description, and where none has one, the first, and every other is left out with a warning on standard error.
D36 below ranks, among the declarations with a description, one whose value is not attached by `=` above one whose value is.
A synopsis-only option has no description and follows every documented one, so the documented option wins without a separate rule, and so does a described entry over a prose fragment: preferring the first alone would keep conda-create's prose line `--dry-run or --download-only is specified.` as `--download-only`, taking a value, and gcc's `-fcf-protection=<value>`, rendered `-fcf-protection full`, which clang reads as an input file, over the described switch.
The flag that decides is the one `_passable_flag` returns, never the input name, so flags that differ but sanitize alike, such as `--log-dir` and `--log_dir` documented in two entries, stay two inputs and the writer numbers them; D37 reads two such spellings written in one entry as one option.
The rule is in `transform` rather than `parse`, because the option list is `parse_pattern`'s input: removing an option there changes how the synopsis reads its flags, and upstream raises on a flag documented twice that the synopsis names, which is why `conda-create` and `zstd` degrade.
No arity and no text are merged between the declarations of a flag, so a form is lost where two declarations differ in arity: `du`'s `--time` keeps its switch and loses `--time=WORD`, which `du` accepts only attached.
Two options that GNU documents with a short flag of their own beside a long flag taking a fixed value, `ls`'s `-p, --indicator-style=slash` and `sort`'s `-C, --check=silent`, are left out, since docopt names each by its long flag.
Over the 196 help texts 12 records change, each only by losing inputs, and no CLI prefix or positional moves; the git-commit synopsis's `--squash <commit>` still declared a positional `commit` until D43, since the synopsis registers `--squash` as a switch before the rewrite.
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
Two kinds are not what a reader would write: argparse's subcommand placeholder, such as `{batch,target,...}`, takes the whole list of subcommands as its description, and GNU `date`'s `FORMAT` takes the text after two spaces in the prose line `FORMAT controls the output.  Interpreted sequences are:`; D35 below leaves the first out and reads the second as prose.
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

**D32** — an action class the reader did not model stopped the whole document.
`unpack_tasks` handled a fixed tuple of action classes and raised `TypeError` on any other, so mypy's parser, whose `-V/--version` uses its own `CapturableVersionAction`, produced no task, and neither did miniwdl's or torchrun's.
argparse reads what an action consumes from its option strings and `nargs` alone, whatever the class's `__call__` does with the values, so such an action now has the input its shape gives: a flag with `nargs=0` passes when set, and any other takes values as a `store` option does.
The tuple, now `argparser._KNOWN_ACTIONS`, keeps argparse's action classes that store a value for the tool to see, whose defaults are therefore what the tool sees; argparse's own help, version and subparsers actions are handled before the tuple is consulted, and a test checks the tuple against argparse's registry of action classes.
Three of the tuple's classes also say more of the command line than `option_strings` and `nargs` do: `append` repeats its flag, `BooleanOptionalAction` adds a negated spelling, and `store_true` and `store_false` state which spelling means true.
The `const` of another class says what it stores, so a `nargs=0` flag passes when set even where its `const` is `False`.
No default of such a class is declared, because the class may compute it: torchrun's options read theirs from environment variables, and with `PET_NNODES=4:4` set, the input stays `String? nnodes` rather than declaring `4:4`.
Each such action prints a warning on standard error naming its flag and its class.
Skipping version and help flags declared with a class of the tool's own was not adopted, because no attribute marks them.
The packages in the `dev` and `cnvkit` environments define 28 action classes for themselves, one of them fontTools' subclass of argparse's own help action, and of their version flags mypy's `CapturableVersionAction` has a `version` attribute and `default=SUPPRESS` while miniwdl's `PipVersionAction` has neither.
A live option's `dest` is never `SUPPRESS`, since `add_argument` derives it from the option strings, and `default=SUPPRESS` is also what traitlets gives every ordinary switch through `argument_default`, all 25 `_FlagAction` switches in ipykernel's parser among them.
So mypy's task declares `Boolean version_ = false`, which passes `--version` only when set.
A parser holding only help, such a version flag and subcommands now yields a task for the version flag too, as miniwdl's does: `miniwdl ~{if version_ then "--version" else ""}`.
Skipping unknown actions instead would have dropped the 27 of torchrun's 31 inputs whose actions warn.
A positional with `nargs=0`, which argparse accepts from a `store_true` or from a class of the tool's own, consumes nothing and is skipped as `nargs=SUPPRESS` is; it used to raise `IndexError`.
On 2026-10-25 mypy's parser generated one task of 126 inputs, miniwdl's 11 tasks and torchrun's one task of 31 inputs, and each document passed `miniwdl check` and `nextflow lint`.
Both fixtures, the flagship and `mypy.dmypy.client.parser` generate byte-identical WDL and Nextflow, verified by checksum.

**D33** — a repeated option value attached with `=` was not read as a list.
D22 reads argparse's `-n N [N ...]` as one option taking a list, but help written by hand may attach the value, as in `--normal=N [N ...]`.
The entry's repeated placeholder had to follow whitespace, and `_valued_options` gives no value to a long option whose token carries `=`, since `parse_long` reads such a value as attached, so the synopsis yielded an array positional `N` beside a scalar `--normal`.
The entry's placeholder may now follow `=` after a long flag, and `_without_value_repeats` also removes the repetition after a long option whose value is attached, where the options block records that option as repeating the same placeholder; a short flag is left as it was, since `parse_shorts` gives a documented `-n` the value `=N` in `-n=N`, which is not the placeholder.
argparse never writes this form, and none of the 196 help texts, recaptured on 2026-10-29 with the same commands as on 2026-10-24, has it, so no record changes.

**D34** — a flag that only the synopsis names read as a switch, and its placeholder as a positional.
`parse_shorts` gives a flag the options block does not document no value, and `parse_long` gives one only where its token carries `=`, so bedtools' `bedtools intersect [OPTIONS] -a <bed/gff/vcf/bam> -b <bed/gff/vcf/bam>`, whose options block omits `-a` and `-b`, declared two switches and two positionals, and the task passed the files before the flags.
`docopter._synopsis_valued_options` now gives such a flag a value before the synopsis is read, where no entry documents its exact spelling and every token of the synopsis that spells it, a long flag without `=` or a short flag of one letter, is directly followed by a placeholder, a token `parse_atom` reads as an argument: angle-bracketed or in capitals.
The value it takes is then a token that would otherwise be a positional, never a bracket, a bar or a command word, so the rule never stops a synopsis from parsing, which is what D27 could not show for a value read from a description: given one, slop's `-r` takes the `)` of `(-l and -r)`.
A short flag whose letter ends a stack, as `z` ends `-pz`, takes no value, since the stack would take the token after it.
Over the 8,108,730 token sequences of one to six tokens drawn from `[ ] ( ) | ... -p -pz --flag -z --other A <x> w`, each read with `-p, --flag` undocumented, documented as a switch and documented as taking a value, the rule applies to 2,167,304 readings, and no sequence starts or stops parsing; without the stack condition, 1,048 of the 148,064 applications among sequences of up to five tokens stop parsing.
The alphabet writes no attached value, and with one the rule can let a synopsis parse that did not: `--foo <X> --foo=<Y>`, with `--foo` undocumented, used to register `--foo` as a switch at its bare spelling and then reject the attached value.
A flag that matches a documented option only once `_passable_flag` removes its notation, as the synopsis's `--trailer` matches the entry `--[no-]trailer <trailer>`, takes a value only where that option does, since `transform` passes the flag from the documented option and a switch would leave the placeholder nowhere.
For the same reason a flag that `_passable_flag` rejects, such as absl's `--apilog:`, takes no value.
A placeholder followed by `...` is taken as one value, so `-i <file>...` passed one file until D44 read the repetition; read as a positional list, as before D34, the files were passed before the flag.
Where the synopsis does not parse with these options it is read without them, so a degraded record is read exactly as before, and the options the synopsis alone names keep the order the tree first names them in, so that a flag given its value first does not move ahead of the others.
An option the tree never names, because an option before it took it as its value, is dropped, as it would never have been registered.
A documented switch whose synopsis occurrences are all followed by a placeholder, such as bedtools genomecov's `-g <genome>`, kept the arity its entry gives, until D40.
Over the 196 help texts, recaptured on 2026-10-29 as D33 describes, eight records change, and no outcome class, CLI prefix or warning moves: 27 options gain a value and 28 positionals of 532 disappear.
Five records are bedtools: the captures of `intersect`, `merge`, `sort` and `genomecov` and the corpus's `intersect`, whose `-a` and `-b` become `String? a` and `String? b`.
torchrun's 18 long options written in Python 3.13's alias form, `--log-dir, --log_dir LOG_DIR`, gain a value and lose their positional, since the entry keeps only its last long spelling; each such option was two inputs, one passing each spelling, until D37 read the entry as one option.
git's `-C <path>` and `-c <name>=<value>` gain a value, and git-commit's synopsis loses the positional `token` its `--trailer <token>` declared.
genomecov, whose synopsis writes `-ibam`, loses three switches, `-b`, `-a` and `-m`, that the letters of `-ibam` declared, since `-i` now takes `bam` as its value; bedtools rejects each of the three as an unrecognized parameter, as it rejects annotate's `-l` and `-e`, read from `-files`, which annotate loses the same way.
Since D39 the token `-ibam` names its documented option instead, and `-i` takes its own placeholder; the inputs are the same.
Over the 43 bedtools 2.31.1 subcommands that print a `Usage:` marker, 35 records change, 54 options gain a value and 54 positionals disappear.
Each of the 54 distinct subcommand and flag pairs given a value, passed to the binary before a sentinel token, with the other inputs its subcommand requires, read the sentinel as its value, where a switch such as intersect's `-wa` leaves it to be rejected as an unrecognized parameter.
Every changed document passes `miniwdl check` and `nextflow lint`, and both fixtures generate byte-identical WDL and Nextflow, verified by comparing the generated text before and after.
Not covered: a placeholder after a group of alternative flags, as git-commit writes `(-c | -C | --squash) <commit>`, where `-c` takes the bar as its value, and an optional prefix before the placeholder, as in `--fixup [(amend|reword):]<commit>`, until D43; a single-dash flag of several letters, such as bedtools' `-fi <fasta>`, which docopt reads as a stack, until D39; and a lowercase placeholder, such as BSD's `-f file`, which docopt reads as a command word, as it reads a subcommand, until D41.

**D35** — a choice group was an input, and prose opening with a positional's name described it.
argparse writes the subcommands of a subparsers action, and the values of a positional declared with `choices=` and no `metavar`, as a brace group, `{batch,target,...}`, which docopt reads as one placeholder, so cnvkit's top level declared a required `Array[File]+` named after every subcommand at once, and D28 then gave it the whole subcommand list as its description.
`_read_command_line` now leaves out a slot that every alternative writes as a brace group, with a warning on standard error, as the argparse reader never declares a subparsers action an input; a slot that another alternative writes as a placeholder is kept.
A brace group is notation, so it is recognized as written: argparse's `metavar="COMMAND"`, as conda writes, removes the braces from the synopsis and the entry alike, and `COMMAND` is still read as a positional, with the subcommand list as its description.
Separately, a line where one space and a letter follow the positional's name opens a sentence rather than an entry, so GNU date's `FORMAT controls the output.  Interpreted sequences are:`, at column 0, no longer describes `FORMAT`, while dos2unix's `file ...            files to analyze`, notation after one space, still describes `file`.
Ending a positional's description at deeper lines laid out as entries was considered and not adopted: it would reach conda's `COMMAND` too, but argparse writes an aliased subcommand as `run (r)` with one space, so a `word  text` rule leaves part of tox's list, and the same rule applied to option entries would cut descriptions where prose has two spaces after a sentence.
Over the 196 help texts, six records change and no outcome class, CLI prefix or option moves: `cnvkit.py`, `miniwdl`, tox and twine each lose the slot, torchrun loses two `--start-method` and `--numa-binding` values the reader cannot attribute to their options, and date's `FORMAT` loses its description; 504 positionals become 498.
twine's `{check,register,upload}` is a required argument, so its task can no longer name the command; the warning on standard error names the group it leaves out.
An entry written by hand as `FILE the input`, with one space before a description that opens with a letter, is read as prose too; argparse always writes at least two spaces there.
Every changed document passes `miniwdl check` and `nextflow lint`, and both fixtures generate byte-identical WDL and Nextflow, verified by comparing the generated text before and after.

**D36** — of two declarations of one flag, the one a task cannot pass could win.
D26 passes each flag from its first declaration with a description, and Apple clang's help, captured as `gcc`, documents 32 flags first with a value attached by `=` and then bare, as in `-flto=<value>` before `-flto`, so the task rendered `-flto full`, which clang reads as an input file.
A flag written both bare and with an attached value takes that value optionally, and a task passes a value after a space, so the switch is the one form of the two a task can pass; `du`'s `--time` before `--time=WORD` chose it only because GNU lists the switch first.
`_documented_options` now records as `attached` whether an entry writes its flag with a value attached by `=`, and `_one_per_flag` still prefers a declaration with a description and, among those, one that does not take a value attached by `=`; any tie still goes to the first.
A declaration with a description still wins over one without, since an entry with none is often prose read as an entry.
Four of clang's flags are documented as `-mllvm=<arg>  Alias for -mllvm` beside `-mllvm <value>`, and now take the second declaration's description, while still taking a value.
Passing a fixed-value entry such as GNU ls's `-p, --indicator-style=slash` by its short flag was measured and not adopted: `Option.parse` reads `=slash` as a placeholder, so the input would pass `-p slash`, which `ls` reads as a file name, and no textual rule separates `=slash` from a placeholder such as pytest's `--maxfail=num`.
Over the 196 help texts only `gcc` changes: 32 inputs become switches and four take another description, each input moving to where its winning declaration stands, and no outcome class, CLI prefix or positional moves and no warning appears or disappears; the document passes `miniwdl check` and `nextflow lint`, and both fixtures generate byte-identical WDL and Nextflow, verified by comparing the generated text before and after.
One entry that writes its flag both bare and with an attached value, as GNU sort's `-c, --check, --check=diagnose-first` does, remained one declaration taking a value, so the task passed `--check diagnose-first`, which `sort` 9.5 rejects as an extra operand; D37 reads it as the switch.

**D37** — an entry writing more than one long flag was read as one flag.
`Option.parse` keeps the last long flag an entry writes and gives the option a value where any of its flags is written with one, and three shapes do not fit that.
Python 3.13's argparse writes an option with two long spellings as `--log-dir, --log_dir LOG_DIR`, and names the first in its synopsis, which then named an undocumented flag: since D34 that flag took its placeholder as a value, so each of torchrun's 23 such options was two inputs, one undescribed, and GNU date's synopsis, `[-u|--utc|--universal]`, declared a second switch beside the documented `--universal`.
argparse's `BooleanOptionalAction` writes `--zst, --no-zst` and click writes `--color / --no-color`; the first became a switch passing only the negation, beside an undescribed switch that the synopsis's `[--zst | --no-zst]` declared, and the second an option taking a value, since `Option.parse` reads the slash as a placeholder, so black's task passed `--no-color VALUE`, which black reads as a path.
GNU sort's `-c, --check, --check=diagnose-first` was read as taking a value, as D36 records.
`docopter._entry_spellings` now reads an entry's flags as a list of declarations, each a flag followed by at most one placeholder, before `Option.parse` sees them.
An entry that writes one flag both bare and with a value attached records `value_optional`, and `_takes_value` reads such an option as the switch, as D36 chooses across two entries; the sort entry that follows, `-C, --check=quiet, --check=silent`, still passes `--check` with a value and is left out with D26's warning.
The argument count itself is left as `Option.parse` reads it, so the synopsis reads the flag as before: dropping the attached declaration from the text instead, measured and rejected, made `[--check=WORD]` fail to parse, as `parse_long` rejects a value given to a switch.
The flags after click's slash, and a second long flag that is the first with `no-` inserted, which is the rule `BooleanOptionalAction` generates its flag by and the one `argparser._switch` matches, are the option's `false_flag` and are removed from the text, so the input passes either flag, or neither when unset, and declares no default, as the argparse reader declares `--zst` with no default.
Any other long flags are spellings of one option, which still carries the last, as before and as the argparse reader passes `option_strings[-1]`; `docopter._carried_spellings` rewrites each other spelling the synopsis names to it before the synopsis is read.
A spelling is rewritten only where the rewrite cannot stop the synopsis from parsing: where the carried option takes no value, no token naming the spelling may attach one with `=`, and where it takes one, every such token must attach its value or be followed by a placeholder or a choice group, so `[--log-dir]` before `FILE` is left as written rather than giving `]` to `--log_dir` as its value.
A spelling left as written is read as before, an input of its own with no description beside the described option.
Text that is no list of declarations is left as written, so prose read as an entry, such as conda's `--dry-run or --download-only is specified.`, keeps its reading, and so does every entry from which no flag for false is removed, whose text `Option.parse` reads byte for byte as before.
Until D45 the help-text reader named an alias input from the flag it passes, `log_dir` from `--log_dir` and `platform` from `--subdir, --platform`, where the argparse reader names it from `dest`, the first spelling; the two readers pass the same flags.
A negation that is not the first flag with `no-` inserted is a spelling, so node's `--webstorage, --no-experimental-webstorage` passes the negation, as before; node 24.14 rejects `--webstorage` as a bad option, so reading any `--no-` flag as a negation would have passed a flag the tool refuses.
Over the 199 help texts, which are the 196 of D36 with the three corpus captures added since, and over the 43 bedtools subcommands, seven records change and no outcome class, CLI prefix or positional moves.
torchrun goes from 53 inputs to 30, each option described; `--start-method` and `--numa-binding`, whose synopsis spellings had been read as switches followed by a choice group, are one input each taking its value, so the two choice-group warnings disappear.
gnu-date loses its undescribed `utc`, conda-create's `--repodata-use-zst` becomes one `Boolean?` with `--no-repodata-use-zst` as its flag for false and takes its description, gnu-sort's `--check` becomes a switch, and black, hatch and papermill's nine click switches, among them `--fast / --safe` and `--prepare-only / --prepare-execute`, each become one input named after the flag for true.
Every changed document passes `miniwdl check` and `nextflow lint`, and both fixtures and the corpus generate byte-identical WDL and Nextflow, verified by comparing the generated text before and after; the samtools fixture's `-U, --unoutput FILE, --output-unselected FILE` still passes `--output-unselected`.
The shapes below are not covered, and no surveyed help text writes any of them.
A synopsis naming a bare-and-attached flag bare before an argument, as `[--check] FILE` or `[-c] FILE`, still fails to parse, as it did before D37, since the flag's argument count of 1 takes the bracket as its value; the trade is deliberate, because the rejected reading broke `[--check=WORD]`, which parses.
An entry whose declarations wrap onto a second line has only the first line's flags read, as `Option.parse` reads only them, and one whose flags are separated by two spaces, as in `--log-dir,  --log_dir DIR`, has its flags ended after the first by upstream's two-space rule.
An alias whose value repeats, as in `--normal N [N ...], --norm N [N ...]`, is no list of declarations, and one whose value is optional, as Python 3.13 writes `--log-dir, --log_dir [LOG_DIR]` beside `[--log-dir [LOG_DIR]]` in its synopsis, is not rewritten, since the bracket is no placeholder.
A short flag after click's slash, as in `-f, --fast / -s, --safe`, is not rewritten where the synopsis names it.
A flag for false that another entry also documents is not compared by `_one_per_flag`, so two inputs may pass it.
A slash between two spellings of one value, as hand-written help might write `-v / --verbose`, is read as click's pair.
A synopsis spelling whose carried flag two entries declare is not rewritten, since `parse_long` would reject the carried flag as ambiguous, so it stays an input of its own.

**D38** — punctuation inside a name was deleted, so the words it separated were joined.
`tasktree.legal_name` mapped `-` and `.` to `_` and deleted every other character outside `[A-Za-z0-9_]`, a rule from the first draft whose deletion half had been argued only from `FILE(S)`, `<a/b>` and `<n,m>`.
mypy's parser declares 20 dests such as `special-opts:python_version`, which became `special_optspython_version`, and help-text placeholders such as bedtools' `<bed/gff/vcf>` and samtools' `<chr:from-to>` became `bedgffvcf` and `chrfrom_to`.
`legal_name` now maps every character that `tasktree._ILLEGAL` matches, which is every character outside `[A-Za-z0-9_]`, to `_`, collapses runs of underscores, and removes those at either end, before the prefix rule; `task_title` deletes every such character itself before calling it, so a title stays one capitalized word, as its word-boundary rule intends.
Mapping only `:` was measured and not adopted: it fixes mypy and two help-text names, and gives ssh's `:remote_tun`, `bind_address:` and git-commit's `:` the names `arg__remote_tun`, `bind_address_` and `arg__`, while 15 other names with joined words remain.
Mapping every character without the trim, or a chosen set of separators, gives `arg__FORMAT`, `arg__num` and gcc's `ObjC__`, and loses GNU date's `FORMAT` description, since `+FORMAT` no longer names the positional.
The trim also removes the notation around a token, so `docopter._identifier` and the argparse reader's flag-named inputs no longer strip hyphens and angle brackets themselves.
Because `_positional_descriptions` matches an entry to a positional by identifier, an entry that writes its positional as the synopsis does, as clap's `[FILES]...`, now describes it: `ruff check`, `ruff format`, `rg`, `cargo run`, `pixi run` and `uv pip install` each gain one description, where `FILES___` had failed to match `FILES`.
Over 242 help texts, the 180 captured on 2026-10-24, the 43 bedtools subcommands, the corpus and the fixtures, 37 records change and no outcome class, CLI prefix, option set or warning moves: 33 inputs are renamed, such as `bed_gff_vcf`, `chr_from_to`, `host_port`, `NAME_VALUE`, `data_dir_path` from `arg___data_dirpath`, and `arg_` from the `arg____` that `...` gave, six positionals gain a description, and `python3 -m zipfile` titles `Main` rather than `Task__Main__`.
Over the argparse parsers of mypy, `mypy.dmypy.client`, miniwdl, torchrun and `build`, only mypy's 20 `special-opts:` inputs change, to names such as `special_opts_python_version`.
Every changed document passes `miniwdl check` and `nextflow lint`, and both fixtures and `cnvlib.commands.AP` generate byte-identical WDL and Nextflow, verified by comparing the generated text before and after.
A name that is already a legal identifier is no longer always kept as written: `a__b` becomes `a_b` and `_private` becomes `private`, and `class_` becomes `class`, to which the Nextflow writer's keyword rename restores the underscore; no measured parser declares such a dest.
A trailing hyphen no longer distinguishes a flag's name, so gcc's `-I-`, declared first, takes the name `I` and its `-I <dir>` becomes `I_2`, where they were `I_` and `I`.
The name `I` therefore denotes a different input than before, a switch rather than a directory, so a value written for an earlier wrapper reaches the wrong flag: WDL rejects a path given for a `Boolean`, while the Nextflow process tests the switch by truth and passes `-I-` for any nonempty text.
bedtools' `-name` and `-name+` already shared the name `name` under the old rule, which deleted `+`, so they are numbered as before.
Grouping punctuation inside a token separates too, so `FILE(S)` would become `FILE_S`; no captured help text names a positional or option that way.
A word that ends a sentence, or the first part of a hyphenated word, and that wraps onto a line of its own indented less than the description column now names the positional of that word, as `command.` and `command-` name `command`, so the line ends the description being read and a deeper line after it can describe that positional.
Under the old rule this happened only to a word followed by another mark, such as `command,`; no captured help text is affected.

**D39** — a single-dash flag of several letters was read as a stack of one-letter flags.
`parse_shorts` reads every token with one dash as a stack, so bedtools getfasta's `-fi <fasta> -bed <bed/gff/vcf>`, whose options block documents `-fi` and `-bed`, declared the switches `-f`, `-i`, `-b`, `-e` and `-d`, each of which bedtools rejects as an unrecognized parameter.
`Option.parse` stores the documented entry whole, as a short flag `-fi`, so the entry was read correctly and only the synopsis was not.
`docopter._single_dash_spellings` now rewrites each synopsis token that an entry documents as its whole flag to a long spelling, the same name under a second dash, and reads the synopsis with a copy of that option carrying the long spelling, so `parse_long` matches the token to the documented option and the letters name nothing; `options` keeps the flag the entry writes, which `transform` passes.
The long spelling is lengthened with `_` until neither the options block nor the synopsis writes it, as `java` documents both `-version` and `--version`.
An entry was the only evidence used until D49.
Of the 68 single-dash tokens of several letters in the 242 surveyed synopses, 13 in 7 records are documented whole, all of them bedtools flags, and 55 are not: BSD stacks such as ssh's `-46AaCf...` and ssh-keygen's `-lv`, and cargo's `-Zscript`, which is `-Z` with its value attached; four undocumented bedtools flags, such as bamtofastq's `-fq <FQ>`, stay stacks, until D49.
A spelling two entries document is left as written, since only one of the two could be read for it, and so is one whose rewrite could stop the synopsis from parsing, by the guard `_carried_spellings` already applies and that `docopter._rewritable` now states for both: a documented `-fi FILE` written bare before a bracket, as in `[-fi] <fasta>`, would take the bracket as its value.
Over the 12,204,240 token sequences of one to six tokens drawn from `[ ] ( ) | ... -fi -f -fi=A --fi --fi=A -fo A <x> w`, each read with `-fi` undocumented, documented as a switch, as taking a value, with a long spelling, declared twice, beside a documented `-f` taking a value, and beside a documented `--fi`, the rewrite applies 14,810,670 times, and no sequence starts or stops parsing.
A rewritten documented switch keeps its entry's arity, so getfasta's `-fi` remained a switch and `<fasta>` a positional until D40.
Over the 242 help texts, five records change, both captures of `bedtools getfasta` and `maskfasta`, `multicov` and `nuc`: 26 switches that the letters of a stack declared disappear, and no positional, outcome class, CLI prefix or warning moves.
Each changed document passes `miniwdl check` and `nextflow lint`, and both fixtures generate byte-identical WDL and Nextflow, verified by comparing the generated text before and after.

**D40** — a documented switch that the synopsis always writes before a placeholder took no value.
bedtools writes no placeholder in its options block, so genomecov's `-g` is documented as `-g\tProvide a genome file.`, and D34 kept the arity an entry gives, so `-g <genome>` in the synopsis read as a switch followed by a positional, and the task could pass the flag but not the file; once D39 read `-fi` as one flag, getfasta's `-fi <fasta>` had the same shape.
`docopter._synopsis_valued_switches` now gives a documented option that takes no value the placeholder after it, under the condition D34 applies to an undocumented flag, now `docopter._flags_before_placeholders`: the synopsis writes at least one of its spellings, and every token spelling it is a long flag without `=` or a short flag of one letter directly followed by a placeholder.
The option then takes a value in `options` too, so `transform` declares it as it declares an option whose entry writes a placeholder, typed by D27's type bullet where its description has one.
The entry is not overruled, since it shows no value rather than showing that none is taken.
A short flag whose letter the synopsis also writes inside a stack takes no value: given one, `-p` would take the `z` of `-pz` as it, so a documented `-z` taking a value would no longer take the token after the stack.
Without that condition, of the 8,108,730 token sequences of one to six tokens drawn from D34's alphabet, read with `-p, --flag` a documented switch beside `-z, --other` documented as taking a value, 12,464 stop parsing and 11,872 start; with it, the rule applies 327,884 times in that setting, as often with `-p, --flag` documented alone and 543,354 times beside `-z` documented as a switch, and no sequence starts or stops parsing.
Where the synopsis does not parse with these values it is read without them, as for D34.
The cost is a switch the help text documents as one and writes before an operand in every alternative, as in a synthetic `date --utc FILE` with `-u, --utc, --universal` documented: the switch takes the operand.
No surveyed help text has that shape; an options block that writes a placeholder for its other options would be the evidence to tell the two apart, and none of the switches given a value here is in such a block.
Over the 242 help texts, ten records change, all bedtools, and no outcome class, CLI prefix or warning moves: 14 options gain a value, `-g` and `-ibam` in both captures of genomecov, `-fi` and `-bed` in both captures of getfasta and in nuc, `-fi`, `-fo` and `-bed` in maskfasta, and `-bed` in multicov, and 17 positionals disappear, among them flank's, shift's and slop's `<int>`, whose options D27 already gave a value.
Each of the ten distinct subcommand and flag pairs given a value, probed against bedtools 2.31.1 with the other inputs its subcommand requires and a sentinel token after the flag, read the sentinel as its value, opening it as a file or, for maskfasta's `-fo`, writing it, where getfasta's switch `-split` leaves it to be rejected as an unrecognized parameter.
Each changed document passes `miniwdl check` and `nextflow lint`, and both fixtures generate byte-identical WDL and Nextflow, verified by comparing the generated text before and after.

**D41** — a lowercase placeholder after a flag was read as a command word.
BSD help text writes its synopsis from the manual page, with lowercase placeholders such as `[-f file]` and `[-e pattern]`, and usually no options block, so D34, which gives an undocumented flag the token after it only where `parse_atom` reads that token as an argument, left each such flag a switch and its placeholder a positional: ssh declared 26 positionals and patch 16.
`docopter._flags_before_placeholders` now also counts a lowercase word, which `parse_atom` reads as a command word, as the placeholder after an undocumented flag, under D34's other conditions: every token spelling the flag is directly followed by one, the flag ends no stack, and `_passable_flag` accepts it.
Documented switches, which D40 gives a value, still need an angle-bracketed or capitalized placeholder: the entry is evidence that the flag is a switch, and a lowercase word is weaker evidence against it than a placeholder, since it is also how an operand or a subcommand is written.
The value taken is a token that would otherwise be a command word or a positional, never a bracket, a bar or an ellipsis, so the rule cannot stop a synopsis from parsing; over the 5,229,042 sequences of one to six tokens drawn from `[ ] ( ) | ... -p -pz --flag -z A <x> w`, read with `-p, --flag` undocumented, documented as a switch and documented as taking a value, the rule applies 1,376,682 times, and no sequence starts or stops parsing.
Nor can it shorten a CLI prefix, since `_command_words` reads the command words that follow the program name directly, never a flag.
A word the text could also read as a subcommand, as in `prog -v sub <x>`, is now the flag's value; the open defect on a subcommand after an option group describes why no reading tells the two apart.
A literal choice for the flag's value, as patch writes `[-V t | nil | never | none]` and ssh-keygen `[-t ecdsa | ecdsa-sk | ...]`, would leave the words after the first as alternatives read as one positional, so `docopter._without_literal_alternatives` removes each `| word` after the value, within a group that the flag opens and whose every alternative is one lowercase word, whether the flag's entry or the synopsis gives it the value; a group whose alternatives hold more, as `[-O fmt | -o fmt]` does, stays as written, and each flag takes its own value.
Only bars and words are removed, which never changes whether a text parses: over the 19,173,960 sequences of one to eight tokens drawn from `[ ] | ... --flag -p w A`, the removal applies 334 times with the flags undocumented and 840 times with `-p`, `--flag` or both documented as taking a value, and no sequence starts or stops parsing.
The words after the first are not recorded, since nothing in the model holds a choice of values.
Over the 242 help texts, 24 records change, and no outcome class, CLI prefix or warning moves: 135 options gain a value and the positionals of those records fall from 172 to 61, ssh's from 26 to 6, patch's from 16 to 2 and ssh-keygen's from 10 to 1.
Of the 135 flags given a value, 132 read the token after them as their value when probed against the binaries, as D34's probe did; Rscript's `-e` was not probed, since Rscript is not installed, and two are switches written directly before an operand, tr's `-d` in `tr [-Ccu] -d string1` and diff's `--side-by-side` in `-y | --side-by-side file1 file2`, which now take that operand.
No condition on the text was found that excludes those two without excluding real placeholders: diff's `-C number file1 file2` has the same shape, and `-C` takes a value.
bsd-sort and bsd-zip each lose switches that letters of a stack declared, as D34 described for genomecov, since a flag given a value now takes the rest of the stack: sort's `-1` and `-2`, from `-kPOS1` and `-POS2`, where `-S` takes the digit after it, and zip's `-s`, from `-options`.
Excluding every letter written inside a stack, as D40 does for a documented switch, was measured after D49 and not adopted: it changes three records, making BSD tr's `-d` the switch it is, but making sort's `-S` and zip's `-t` and `-n` switches and their placeholders `File` positionals, although `sort -S x` and `zip -t x` reject `x` as a buffer size and a date, so each reads the token after it as its value.
Protecting only letters that no entry or other synopsis token names fixes nothing and keeps those regressions, and no property of the text was found to separate notation such as `-POS2` and `-options` from stacks such as `-Ccsu` and `-ds`.
A letter taken this way is never an input that an entry documents and the task loses, since every such letter is documented, written on its own elsewhere, or part of such notation; the uncovered shape is a real stack holding, before its last letter, a flag that the synopsis elsewhere writes before a placeholder, as a `tr [-Ccsu] -s string1` would, which no surveyed help text writes.
What remains is other notation: ssh's `-D [bind_address:]port` keeps its two positionals, since `-D` is followed by a bracket, and `-w local_tun[:remote_tun]` keeps `remote_tun`.
Each changed document passes `miniwdl check` and `nextflow lint`, and both fixtures generate byte-identical WDL and Nextflow, verified by comparing the generated text before and after; the corpus's `ls` now yields exactly its positional, and its `-D` takes `format`.

**D42** — an optional value attached to a long flag in the synopsis was read as a positional.
BSD grep writes `[--context[=num]]` and git `[--exec-path[=<path>]]`, with no entry for either flag; `parse_pattern` pads brackets but not `=`, so the group `[ =num ]` held a token docopt reads as a command word, and each record declared a positional `num` or `path` beside the switch.
`docopter._without_attached_values` now removes such a group, a `[`, a token of `=` followed by at least one character, and a `]`, directly after a long flag written without `=`.
The flag stays the switch it was, which D37's reading of an entry such as `--check, --check=WORD` makes the one form of the two a task can pass: probed, BSD grep 2.6.0 accepts a bare `--context` and reads `--context 1` as a pattern of `1`, and git 2.56.0 accepts a bare `--exec-path` and does not read the next word as its value.
tree's `--gitfile[=]file`, whose brackets hold the `=` alone, is left as written, since it writes a required value whose separator is optional.
The group is removed whole, so every other bracket stays matched, as D22 argues, and where an option reads the `[` as its value, as a documented `--color` taking a value does, the group is left as written for D18's retry; without that condition, of the 8,108,730 token sequences of one to six tokens drawn from `[ ] ( ) | ... --flag --flag=Z --other -p =V = <x> w`, read with `--flag` undocumented, documented as a switch and documented as taking a value, 27 stop parsing and 252 start, and with it, the removal applies 3,101 times and none starts or stops.
The values of the reading are decided on the text before the removal, so that `--context[=N] FILE` does not give `FILE` to `--context`.
The short form, as grep writes `-C[num]` and git-commit `-u[<mode>]`, is not read: `parse_pattern`'s tokens cannot tell it from a switch followed by an optional positional, `[-C] [num]`, and BSD grep rejects a bare `-C`; D48 reads it from the text.
An entry written in the same notation, as GNU ls documents `--color[=WHEN]`, is still left out by D21, so a synopsis-only flag of that form is now a switch while a documented one is not, until D47.
Over the 242 help texts, two records change, BSD grep, which loses the positional `num` that `--context[=num]` declared, and git's top level, which loses `path`; no option, outcome class, CLI prefix or warning moves.
Each changed document passes `miniwdl check` and `nextflow lint`, and both fixtures generate byte-identical WDL and Nextflow, verified by comparing the generated text before and after.

**D43** — a placeholder written after a group of alternative flags, or after an optional prefix, was read as a positional.
git commit's synopsis writes `[(-c | -C | --squash) <commit> | --fixup [(amend|reword):]<commit>]`.
docopt gives a value-taking flag the next token, so the documented `-c` and `-C` took the bars as their values, the alternatives were lost, and `<commit>` was a positional of the whole group; `--fixup` met the bracket of its value's optional prefix, whose words `amend`, `reword` and `:` became positionals.
`docopter._distributed_values` now rewrites a parenthesized group whose every alternative is one flag written without `=`, directly followed by a placeholder, so that each alternative carries the placeholder: `(-c <commit> | -C <commit> | --squash <commit>)`.
A bracketed group is not rewritten, since its placeholder is required and would no longer be inside the brackets.
The rewrite is declined where an option before the group takes its opening parenthesis as its value, since the group is then no group, and where a flag of the group is a documented switch, which D40's rule would then give the placeholder, although the group shows no value for that flag alone.
`docopter._without_value_prefixes` removes a bracketed group that opens a word and is written directly before the rest of it, as `_VALUE_PREFIX` matches, where that rest is a placeholder or a lowercase word, so the flag meets its value and the caller writes any prefix into it; ssh's `-D [bind_address:]port` is read the same way.
A group written apart from the token after it, as in `--fix [(a|b):] <commit>`, is a group of the pattern and is left as written, and so is one inside which an option takes a token as its value, since its closing bracket would then close another.
Only the text shows that a group is written directly before its value, and every later rewrite joins `parse_pattern`'s tokens with spaces, so this one runs first, on the text `formal_usage` returns.
Each removes or moves a balanced run of tokens, so every other bracket stays matched, but each changes which token a flag takes, so neither is safe as a pre-pass on its own: as written, the last flag of a group takes the closing parenthesis, and a documented flag before a prefix takes its opening bracket, and an unbalanced text can parse only that way.
`_read_command_line` therefore reads four texts in turn, with both rewrites, with the prefixes removed alone, with the values distributed alone, and as written, each with and without the values the synopsis shows, and keeps the first reading that parses, so a usage line that parsed before still parses; over the 12,204,240 token sequences of one to six tokens drawn from `[ ] ( ) | ... -c -C --sq <x> w ]<x> ]w [w]<x> [(w|w):]<x>`, read with `-c`, `-C` and `--sq` documented as switches, as taking values, with `-C` undocumented, and with none documented, no sequence stops parsing, and 276,864 start, each a text that failed because a flag took a bracket, as D18's retry recovers.
Over the 242 help texts, two records change, and no outcome class, CLI prefix or warning moves: git-commit loses its positionals `commit`, `:` and a second `commit`, and ssh's `-D` takes `port`, which loses the positionals `bind_address` and `port`.
Each changed document passes `miniwdl check` and `nextflow lint`, and both fixtures generate byte-identical WDL and Nextflow, verified by comparing the generated text before and after.
git 2.56.0 accepts `-c X`, `-C X`, `--squash X`, `--fixup X` and `--fixup amend:X`, each as one value, and the probe of D41 found that ssh's `-D` takes a value.

**D44** — a repetition written after an option was lost, so the task passed one value.
docopt's `parse_seq` wraps in `OneOrMore` whatever atom a `...` follows, and an option has taken its value by then, so `-i <file>...` and `[-D <name>]...` both repeat the option itself, as docopt's own grammar reads `[-o <o>]...` for `-o a -o b`; nothing read a repetition over an option, so each such input was a scalar.
`docopter._repeated_options` now reads, in the tree, each `OneOrMore` that holds exactly one option taking a value, and `transform` makes that option an array whose flag precedes each value, `Argument.flag_per_value`, as the argparse reader models `append`.
A repeated switch is a count, which no target language can express, and a repetition over two options taking values, as in `(-a <x> -b <y>)...`, repeats the pair, so neither is read; D46 reads a repetition holding a choice of such options, `(-a <x> | -b <x>)...`, as repeating each.
Reading `-i <file>...` as one flag followed by the list, as D22 reads `-n N [N ...]`, and only `[-D <name>]...` as a flag per value was considered and not adopted: the tree does not distinguish the two, and every surveyed instance repeats the flag.
Probed, BSD sed's `-e x y` reads `y` as a file while `-e x -e y` runs both scripts, ripgrep 15.1.0's `-e alpha beta` reads `beta` as a path while `-e alpha -e beta` matches both, ssh-keygen accepts `-O` more than once, and git's manual page writes `--trailer "A" --trailer "B"`.
An entry that states how many values one occurrence takes wins over the repetition: D22's `-n N [N ...]` and argparse's `nargs="*"`, written `-n [FILES ...]`, which `_documented_options` now also records as `repeated` with `docopter._REPEATED_LIST`, give one flag the whole list.
Without that, cnvkit batch's `[-n [FILES ...]]`, which D18's retry reads as `-n FILES ...`, would have passed `--normal a --normal b`, which argparse reads as `['b']`.
The list form loses one reading of `nargs="*"`: an empty list elides the flag, so the bare `-n`, which cnvkit reads as a flat reference, cannot be passed, as D18 records for `--processes`.
The repetition is returned as the flag `_passable_flag` gives, rather than as an option, since git's synopsis names `--trailer` where its entry writes `--[no-]trailer`, and `transform` passes the flag from the documented option; a reading's copy of a documented option, which D39 and D40 make, is traced to the option `parse` returns.
The tree is read after it is parsed and no text is rewritten, so no synopsis can start or stop parsing.
Over the 242 help texts, seven records change, and no outcome class, CLI prefix, positional or warning moves: ripgrep's `--regexp` and `--file`, git-commit's `--trailer`, BSD sed's `-e` and `-f`, ssh-keygen's `-O` and Rscript's `-e` become arrays passing their flag before each value, and both captures of cnvkit batch's `--normal` an array passing it once; Rscript, which is not installed, was not probed.
Each changed document passes `miniwdl check` and `nextflow lint`, and both fixtures generate byte-identical WDL and Nextflow, verified by comparing the generated text before and after.

**D45** — the two readers named an input with several long spellings differently.
The argparse reader names an input after `dest`, which argparse derives from the first long flag, while the help-text reader named it after the flag it passes, the last, as `Option.parse` keeps it, so cnvkit's `-m, --seq-method, --method` was `seq_method` from the parser and `method` from its own help text.
Both readers pass the last spelling, and that is unchanged.
`_entry_spellings` now also returns the first long flag of the option's declarations, recorded as `first_long`, and `transform` names the input after it, read through `_passable_flag` so that `--[no-]verbose` names `verbose`, falling back to the passed flag where an entry writes no passable long flag; the output option keeps `output_file_name`.
A flag for false is no declaration of the option, so `--zst, --no-zst` and click's `--fast / --safe` name `zst` and `fast`; taking the first of `other_spellings` instead, which lists the flags for false too, would have named ten such switches in black, hatch, conda-create and papermill after their negation.
Naming the argparse reader's aliases after the passed flag instead was considered and not adopted: aliases would then follow a different naming rule from every other option of the parser, and cnvkit's `sample_sex` would become `gender`, its legacy spelling.
An explicit `dest=` is invisible in help text, so the readers can still disagree where a parser sets one; 51 of the 313 options of `cnvlib.commands.AP` have a `dest` that is not derived from their first long flag.
Over the 242 help texts, 14 records change, each only in input names, and no flag, type, default, description, warning, outcome class or CLI prefix moves: 47 inputs are renamed, among them pytest's `last_failed` to `lf`, as pytest's own `dest` is, and its `co` to `collect_only`.
torchrun's 23 aliases keep their names, since `--log-dir` and `--log_dir` sanitize alike.
The samtools fixture's `--rf, --incl-flags, --include-flags` and `-U, --unoutput FILE, --output-unselected FILE` become `rf` and `unoutput`, and the corpus's `cnvkit.py batch` names `seq_method` and `male_reference`, so all 29 of its inputs now have the names the argparse reader gives `cnvlib.commands.AP`'s `batch`.
Where an entry joins two distinct options, the name now disagrees with the flag passed: ruby's `--enable=feature..., --disable=feature...` names `enable` and passes `--disable`, and node's `--webstorage, --no-experimental-webstorage` names `webstorage` and passes the negation, as D37 describes; before, the name and the flag agreed, though either input already passed the wrong flag for one of the two options.
An entry that writes a negation before its flag, as in `--no-x, --x`, is not read as a switch with a flag for false either, so it names `no_x` and passes `--x`; no surveyed help text writes one.
Each changed document passes `miniwdl check` and `nextflow lint`.

**D46** — a repetition over a choice of options taking values made no array.
D43 rewrites `(-a | -b) <x>...` to `(-a <x> | -b <x>)...`, and the `...`, which bound to `<x>` as written, then repeats the group, a choice between two options, which D44's rule of exactly one option taking a value declined, so each flag was a scalar.
Before D43 the same synopsis declared an array positional `x` and two switches where neither flag was documented, and degraded where both were documented as taking a value, since `-a` took the bar and `-b` the closing parenthesis.
`_repeated_options` now also reads a repetition holding a choice, through groups of one child, as `docopter._alternatives` finds it, where no alternative names two options taking a value: each occurrence passes one flag, so every option of the choice that takes a value repeats on its own, its flag before each value, as D44 reads one option.
A choice with an alternative holding a pair, as in `(-a <x> | -b <y> -c <z>)...`, is declined whole, and a choice nested in another is not read.
Declining the distribution before `...`, which would restore the positional, was measured and not adopted: over the 21,435,887 token sequences of one to seven tokens drawn from `[ ] ( ) | ... -c -C --sq <x> w`, 45 stop parsing with the three flags documented as taking values, and it would not reach `[(-a | -b) <x>]...`, whose `...` follows the bracket.
The repetition is read from the tree once it parses, and no text is rewritten, so no synopsis starts or stops parsing.
The order of occurrences is not kept: `-a 1 -b 3 -a 2` is passed as `-a 1 -a 2 -b 3`, as for any two arrays, and a tool that rejects both flags in one command line is not modeled.
Over the 242 help texts no record changes: of their 169 repetitions, 160 hold no option taking a value and 9 exactly one.

**D47** — an entry writing its flag's value optional and attached was left out.
D21 left out an option whose flag ends in a bracketed group, as GNU ls documents `--color[=WHEN]` and GNU date `-I[FMT], --iso-8601[=FMT]`, while D37 reads `--check, --check=WORD` and D42 a synopsis's `--context[=num]` as the switch, the one form of the two a task can pass.
`_passable_flag` now removes such a group with `docopter._without_optional_value`: the trailing `[` that `Option.parse` keeps of a long flag, since it splits the flags at `=`, or a final group attached to a short flag, as in `-W[level]`, closed or, where `Option.parse` split ruby's `-W[level=2]` at `=`, unclosed; and `_documented_options` records `value_optional` for an option whose carried flag ends so, which `_takes_value` reads as a switch.
A long flag's group must hold `=`, so ruby's `--jit-[option]`, an optional part of the name with nothing after it, names no flag, and two groups, as in perl's `-d[t][:MOD]` and vim's `-V[N][fname]`, are not read; java's `-agentlib:<libname>[=<options>]` keeps its placeholder in the name and stays out.
Only the carried flag is read, so ruby's `-Eex[:in], --encoding=ex[:in]`, which brackets an optional part of a value its long flag shows required, still takes a value.
A second long flag that is the first with `no-` inserted is compared with the first's name without its group, so `--color[=WHEN], --no-color` is one switch with a flag for false, as D37 reads `--color, --no-color`.
The entry text is not rewritten and no argument count moves, so a long flag reaches the synopsis as `--color[`, as before: `[--color] FILE` registers a switch of its own, which `_one_per_flag` sets aside for the described entry with D26's warning.
Rewriting the entry to `--color, --color=WHEN` instead, as D37 reads that text, made the documented flag a spelling of argument count 1, and `[--color] FILE` then failed to parse, since `--color` took the bracket; reading it would need a further reading with that count lowered, which is a change to D37 and is not made here.
A synopsis's `--color <x>` therefore still gives `<x>` to a flag of its own, as before, and since D26 sets that flag aside for the documented switch, `<x>` reaches no input; giving that flag no value instead stopped 736 of the sequences below from parsing in each setting documenting `--color[=WHEN]`, and 1,024 beside a documented `-z` taking a value, as `--color <x> --color=Z` then registers a switch at the first and rejects the value at the second.
A short flag's twin keeps the argument count its entry shows, which for `-q[l]` is none, so a synopsis's `-q <x>` beside that entry now leaves `<x>` a positional rather than giving it to `-q`, since the value is attached only.
Over the 402,233 token sequences of one to five tokens drawn from `[ ] ( ) | ... --color --color=Z -q -qz -z <x> w`, read with `-q[l]` documented alone, and with `--color[=WHEN]` and `-q[l]` documented alone, beside a documented switch `-z` and beside a documented `-z` taking a value, 448 stop parsing in that last setting only and none starts: in `-q <x> -qz`, `-q` took the `z` of `-qz` as its value, and now `-z` demands a value that the synopsis does not write.
Among the records from D39 to D49 this is the one shape where a synopsis that parsed stops parsing, and it is accepted: the reading that parsed gave `-q` a value its entry shows attached only, and the shape needs a synopsis writing `-qz` after `-q <x>` beside a documented `-z` taking a value, which no surveyed help text writes.
Probed with each flag bare, GNU coreutils 9.5, GNU make 4.4.1, git 2.56.0, zstd 1.5.7, fzf 0.74, node 24.14.0, perl 5.32.1, ruby 2.6.10, OpenJDK 23.0.2, Vim 9.1, Neovim 0.12.6 and graphviz 14.1.2 accept every flag read here, and several read a value after a space as an operand, as `zstd --fast 3` reads `3` as a file.
Over the 242 help texts, 30 records change and no outcome class, CLI prefix or positional moves: 97 switches appear, 72 long flags among them, and perl's, ruby's, java's, vim's, nvim's and dot's 25 short flags, and warnings fall from 315 to 218.
One input changes: fzf documents `--listen[=[ADDR:]PORT]` beside `--listen=SOCKET_PATH`, and D36 now prefers the switch, although fzf also reads a value after a space.
git log's `--[no-]decorate[=...] decorate options` and node's `--inspect-brk[=[host:]port] activate inspector on host:port and` separate the flag from the description with one space, which docopt does not read as a description, as the README notes for samtools' `-R`, so `--decorate` has no description and `--inspect-brk` keeps only its continuation line.
Each changed document passes `miniwdl check` and `nextflow lint`, and both fixtures generate byte-identical WDL and Nextflow, verified by comparing the generated text before and after.

**D48** — an optional value attached to a short flag in the synopsis was read as a positional.
BSD grep writes `[-C[num]]`, which `parse_pattern` pads to `[ -C [ num ] ]`, the tokens of `[-C] [num]`, so `num` was an optional positional beside the switch `-C`; only the text shows that the group is attached to the flag.
`docopter._without_attached_shorts` removes, from the text `formal_usage` returns and before any rewrite joins its tokens with spaces, a group directly after a one-letter short flag that holds one word and is followed by neither a word nor a bracket, as `_ATTACHED_SHORT` matches, so a flag of several letters, as in BSD sort's `-kPOS1[,POS2]` and Info-ZIP's `-opts[modifiers]`, is left as written.
The group stays where an option takes its `[` as its value, as git-commit documents `-u` and `-S` with a value, so D18's retry reads `-u [<mode>]` as before and D47 makes the input a switch; where an option takes the flag itself as its value, as in `-p -C[N]`; where a placeholder or a lowercase word follows it, since the bare flag would then take that token as its value, as D34 and D41 give one, and `-C[num] pattern` would pass the pattern to `-C`; where the flag is one of several alternatives, since D43 would then give it the placeholder written after them, and `(-C[num] | -D) <x>` would lose `<x>`; and where `...` follows it, which would then repeat the switch rather than the value.
The flag stays the switch it was, as D42 leaves `--context`.
That is the one form a task can pass for git, which accepts a bare `-u` and `-S` and reads a word after either as a path, but not for BSD grep 2.6.0, which rejects a bare `-C` and reads `-C 2` as the count; the text does not tell the two apart, and getopt's notation for an optional value says the value is attached.
Over the 5,229,042 token sequences of one to six tokens drawn from `[ ] ( ) | ... -c -c[v] -cv -p <x> w [v]`, read with `-c` undocumented, documented as a switch and documented as taking a value, each beside a documented `-p` taking a value, and with neither documented, the removal applies 1,419,174 times with neither documented and 1,335,488 times in each of the first two settings, never in the third, and no sequence starts or stops parsing.
Of the 242 help texts, two synopses write the notation, in three flags, BSD grep's `-C[num]` and git-commit's `-u[<mode>]` and `-S[<keyid>]`, and one record changes: BSD grep loses the positional `num`, and no option, outcome class, CLI prefix or warning moves.
The changed document passes `miniwdl check` and `nextflow lint`, and both fixtures generate byte-identical WDL and Nextflow, verified by comparing the generated text before and after.

**D49** — an undocumented single-dash flag of several letters was read as a stack.
D39 reads `-fi` as one flag only where an entry documents it, so bedtools bamtofastq's `-fq <FQ>`, annotate's `-files FILE1 FILE2..FILEn` and tag's `-files FILE1 .. FILEn -labels LAB1 .. LABn`, which no entry documents, declared switches for their letters, such as `-f` and `-q`, which bedtools rejects as unrecognized parameters, and their placeholders as positionals.
`docopter._undocumented_single_dash` now reads such a token as one flag taking a value where every token spelling it is directly followed by a placeholder in angle brackets or capitals, no letter of it is a documented flag taking a value, not every letter of it is documented, and `_passable_flag` accepts it.
The placeholder is the evidence D34 already reads for a one-letter or long flag; a lowercase word is not, since BSD tr's `-ds string1` and zip's `-xi list` are stacks before an operand.
The documented letter taking a value separates cargo's `-Zscript <MANIFEST_RS>`, which `parse_shorts` reads as the documented `-Z` with `script` attached, and an options block naming every letter explains the token as a stack, as htsfile's names those of `[-chHv]`.
Of the 55 undocumented single-dash tokens of several letters in the 242 synopses, five are followed by a placeholder, cargo's and the four bedtools ones.
`_single_dash_spellings` reads each such token through a new option carrying the flag and a long spelling made as for a documented flag, annotated as `parse` annotates an option the synopsis alone names, since the rewrites read that list; `docopter._spelled_single_dash` removes the long spelling once the synopsis is read, or fails to be, so the task passes `-fq`.
Over the 8,108,730 token sequences of one to six tokens drawn from `[ ] ( ) | ... -fi -f -i --fi -fi=A A <x> w`, read with nothing documented, with `-f` documented as a switch or as taking a value, with `-f` and `-i` documented as switches, with `-fi` documented as a switch, as taking a value or twice, and with `--fi` documented, the rule applies 308,478 times with nothing documented, with `-f` a switch and with `--fi` documented, never with `-f` taking a value or both letters documented, and with `-fi` documented D39's rewrite applies instead; no sequence starts or stops parsing.
Over the 242 help texts three records change, all bedtools, and no outcome class, CLI prefix or warning moves: `-fq`, annotate's `-files` and tag's `-files` and `-labels` take a value, and the seven letter switches and four positionals they declared disappear.
Probed against bedtools 2.31.1, annotate and tag open the token after `-files` as a file, and bamtofastq rejects `-q` and annotate `-l` as unrecognized parameters.
`-files` takes several files, which the task passes as one value, and `FILE2..FILEn`, `..` and `FILEn` remain positionals, notation the reader does not read.
A stack of letters that the help text writes directly before a placeholder, as in `prog -xvf FILE`, where some letter is undocumented and none is documented as taking a value, is now one flag; no surveyed help text writes one.
Each changed document passes `miniwdl check` and `nextflow lint`, and both fixtures generate byte-identical WDL and Nextflow, verified by comparing the generated text before and after.

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
