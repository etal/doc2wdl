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
- **Degraded: 3.** `parse_pattern` raises on the synopsis, so the positionals degrade to empty with a warning on standard error, and the options survive.
  `cnvkit.py batch` fails on argparse's notation for an optional value, an open defect below, and `python3 -m zipfile` on a wrapped synopsis line opening with `-c`, which is read as an options entry and declares `-t` a second time.
  In `samtools stats` the options block follows the synopsis with no blank line, so `printable_usage` reads it as usage.
- **Parsed: 8.** Only `wgsim` yields exactly its positionals.
  The rest run to completion with extra positionals: summary placeholders such as `[OPTIONS]` and `...` (`tabix`), option values the reader cannot attribute to an option (`ls`, which has no options block, and `bedtools intersect`, whose tab-indented block yields no options), an options block's prose (`htsfile`, swallowed as `samtools stats` is but without desyncing), or a placeholder split at its space (`jq`'s `<jq filter>`).
  argparse's `X [X ...]` for `nargs="+"` becomes two positionals, one required and one an optional array (`miniwdl check`), and after an option, as in `-n NORMAL [NORMAL ...]`, the repetition becomes a positional (`cnvkit.py import-rna`).

The reliable shape is a single blank-line-terminated synopsis naming at most one fixed subcommand, where no flag's arity differs between the options block and the synopsis and no wrapped line of the synopsis begins with a flag.
argparse-generated help departs from it in two ways: it writes an optional value in brackets, and it wraps the synopsis at `COLUMNS`, so whether a synopsis line opens with a flag depends on the width: `python3 -m zipfile` degrades at 80 columns and parses at 120.
Hand-written C help departs from it by placing the options block directly under the synopsis (`samtools stats`, `htsfile`) or by leaving the block out (`ls`).
Both fixtures in `tests/example/` sit inside the shape, which is why they were never a sample.

**The argparse reader has no such limits** — it reads a live parser object and needs no text parsing at all.
`cnvlib.commands.AP`, the README's flagship example, unpacks to 35 tasks under one `version 1.1`.
Measured on 2026-10-23, after D9, the whole 35-task WDL document passes `miniwdl check`.
The last failure before D9 was `target --avg-size`, a `type=int` option whose default is the float `266.6666666666667`; that default no longer fits the declared type and is dropped, so the input is `Int? avg_size` and the tool applies its own default when it is unset.

Switches render faithfully with three deliberate gaps.
`count`, `store_const` and `append_const` become a Boolean "pass this flag" input, so `-vvv` cannot be expressed; WDL 1.1 has no string repetition, and the expression that emulates it is unreadable in output meant for editing.
A `required=True` `store_true` declares `Boolean x = false`, so a caller may omit what argparse would demand.
`nargs=1` is modeled as the scalar it is on the command line, although argparse hands the program a one-item list.

### What the Nextflow writer emits

Measured on Nextflow 25.10.4 on 2026-10-23, with `nextflow lint` and with `nextflow run` on the rendered command lines.
A DSL2 process input has no default and no unset state.
Passing `null` as an argument fails the run with "A process input channel evaluates to null", and a `null` element in a channel reaches the script, where `x != ''` renders it as `--num null`.
`lint` also rejects a call that leaves an input out, so the caller passes `''` for an unset value or switch and `[]` for an empty list, as the document's header comment says.
The template therefore tests a scalar value option with `x != ''`, never with Groovy truth, which drops `--num 0` and `--flag false`; a list is tested by truth, since emptiness is its only false value.
A switch with no spelling for false is tested by truth, and one with a spelling for false by `x == true` and `x == false`, so that `''` renders neither.
An array option renders per action as in WDL, and is elided when the list is empty: `append` repeats the flag before each value, by joining the values with the flag as the separator after one leading flag, and `nargs` gives one flag followed by the values joined with spaces.
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

**A wrapped synopsis line that opens with a flag declares an option.** An options-block entry may start on any line opening with a flag where no description column is yet known, the usage section included, because hand-written help may place its options block directly under the synopsis with no blank line, where `printable_usage` reads it as usage.
So in `help-zipfile.txt` the synopsis line opening with `-c` declares `-t` a second time, and docopt rejects `-t` as ambiguous.
Skipping entries inside the usage paragraph would let `help-zipfile.txt` parse, but it would also turn `help-samtools-stats.txt` from degraded into parsed with 208 positionals read from its options block, so the two cases need a rule that tells a wrapped synopsis line from an options block, which none yet does.
An options block indented with tabs, as the bedtools subcommands' are, yields no options at all, because an entry starts only after spaces.

**argparse's notation for an optional value desyncs the synopsis.** argparse writes an option with `nargs="?"` or `nargs="*"` with its value in brackets, as in `[-p [PROCESSES]]`, because the value may be left out.
The options block declares `-p` as taking a value, so the option parser takes the opening bracket as that value and bracket depth desyncs, as `ARITY_OVERLOAD` in `tests/test_docopter.py` does with a closing bracket.
In `tests/corpus/help-cnvkit-batch.txt`, `[-p [PROCESSES]]` and `[-n [FILES ...]]` raise `unmatched '('`, and with both rewritten without the inner brackets the synopsis parses.

Smaller known wrongness, low priority: help-text notation leaks into flags, so
samtools' `--region[s]-file FILE` becomes a literal flag `--region[s]-file`.

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
