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
  tasktree.py    MODEL: the `Argument` dataclass, plus the naming and typing helpers
                 the readers and writers share
  wdlgen.py      WRITER: object model -> WDL; owns RESERVED_WDL_NAMES, TEMPLATE_WDL_NAMES
                 and the spelling of Python values as WDL literals
  nfgen.py       WRITER: object model -> Nextflow; owns RESERVED_NF_NAMES and
                 RESERVED_NF_TITLES
  render.py      Jinja2 environment shared by the writers; `render_document` renders
                 every block and the document around them in one call.
                 A writer needing different settings should build its own
                 environment rather than add a flag
  _docopt.py     VENDORED: the static grammar subset of docopt 0.6.2
  templates/     Jinja2 templates, one document and one block template per target:
                 document_template.{wdl,nf}, task_template.wdl, process_template.nf
tests/           pytest suite; `tests/example/` holds captured help texts and a Makefile
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
Third, `type_and_default` lives in `tasktree` rather than in either writer.
Fourth, `Argument.default_value` and `Argument.doc` hold Python values and plain text, never target-language syntax; `wdlgen` spells them as WDL literals on copies of the arguments, the same shape `rename_arguments` uses, so the shared Jinja2 environment carries no per-language filter.

Rendering is split in two because a document is not a task.
Statements that may appear only once per file — WDL's `version`, Nextflow's shebang and its note on unset inputs — belong to the document template, which is the only layer that knows how many blocks are being emitted; keeping them in the block template is how a two-task document came to carry two version statements.
Each writer therefore exposes exactly one public `render(tasks)` taking an iterable, and blocks are rendered only inside `render.render_document`, so no caller can obtain a fragment and concatenate two of them.
That matters because a lone WDL block passes `miniwdl check`: a version-less document falls back to draft-2, where `~{...}` is not interpolation and every declaration merely goes unreferenced.
Title uniqueness, a property of the whole document, is the second reason the blocks are rendered there.
Spelling a comment is likewise per-target and lives in the block template.
WDL 1.1 has only `#` line comments (`/* ... */` is a grammar error, not a style preference), and the Nextflow template uses `//` per line rather than a `/* ... */` block, which the first `*/` in captured help text would close early.
So there is no shared helper to write, and readers pass `usage` through as plain text.

One breach remains, and it is honest to name it: `Argument.wdl_type` puts a WDL type name in the shared model, both readers fill it in, and the Nextflow template never reads it.
Repairing that means a neutral vocabulary in the model translated per writer — the same shape `rename_arguments` already uses — and it moves both readers and both templates at once, which is why it belongs to the type-inference work rather than to a passing edit.
It is also why every Nextflow input is a `val`: staging a file needs `path`, and choosing `path` from `wdl_type` would act on a type D9 records as wrong for samtools' `region`.

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

`tests/example/Makefile` only records how each captured help text was produced; generation and checking live in the pytest suite.

The README's flagship argparse example, `cnvlib.commands.AP`, needs `cnvlib`, which the `cnvkit` environment provides.
That import works only if you call the environment's interpreter directly, because `conda run -n cnvkit` and `conda activate` both fail on this machine with a broken activation hook:

```bash
/opt/homebrew/Caskroom/miniconda/base/envs/cnvkit/bin/python3 -c "import sys; sys.path.insert(0, '.'); \
  sys.argv = ['d2w', 'argparse', '-m', 'cnvlib.commands', '-p', 'AP']; from doc2wrapper import cli; cli.main()"
```

An earlier revision of this file recorded `cnvlib` as simply "not importable"; that conclusion came from trusting `conda run`, and it cost the flagship fixture.

## Current state

The help-text path works end to end for both targets and is covered by tests.
The argparse path emits WDL and Nextflow with unique names throughout for a parser with subcommands, switches and multi-value arguments; it remains invalid only where an inferred type is wrong, the D9 failure described below.
Everything below was verified by execution on 2026-08-16 unless dated otherwise.

### What the help-text reader will and will not handle

Measured, not estimated, over a corpus of 36 help texts — 34 captured from real tools on
this machine, spanning argparse-generated, hand-written getopt C, and Go/Cobra styles,
plus 2 clearly-labelled synthetic cases covering shapes no installed tool produced. The
corpus lived in `/tmp/d2w-corpus/` and is not preserved; re-capturing it is open
work. Counts against all 36 unless stated:

- **Hard failure: 4 (11%).** No single `usage:` marker — absent entirely (`tar`), a
  heading spelled `USAGE` without a colon (`gh`, a Cobra template, while `docker`'s Cobra
  template does use `Usage:`), a truncated footer-only dump (`curl --help all`), or two
  markers in one document. These raise `docopter.UsageNotFound`, which the CLI reports as
  a message and exit status 1.
- **Degraded: 3 (8%).** A flag declared `argcount=1` in the options block but written bare
  in the synopsis makes the option parser eat the next bracket token and desync bracket
  depth, so `parse_pattern` raises far from the real cause. Positionals degrade to empty
  with a warning; the options survive.
- **Parsed: 29.** Of the 31 files whose tool genuinely has positional arguments,
  **7 (23%) recover them cleanly** and 11 (35%) do so once capitalized `[OPTIONS]`-style
  summary placeholders are filtered out. The previous behaviour was 0 of 31. The rest run
  to completion but mix real positionals with option-value placeholders or with prose:
  where a getopt-style tool's option list follows the synopsis with no blank line,
  `printable_usage` swallows the whole options block into the usage text.

The reliable shape is a single blank-line-terminated synopsis naming at most one fixed
subcommand, where no flag's arity differs between the options block and the synopsis.
That covers argparse-generated tools well and hand-written C tools poorly. Both bundled
fixtures sit inside it, which is why they were never a sample.

**The argparse reader has no such limits** — it reads a live parser object and needs no text parsing at all.
`cnvlib.commands.AP`, the README's flagship example, unpacks to 35 tasks under one `version 1.1`.
Measured on 2026-10-23, after D14 and the name collisions were fixed, 34 of the 35 tasks pass `miniwdl check` when each is checked alone.
The one failure is `target --avg-size`, a `type=int` option whose default is the float `266.6666666666667`, a D9 matter; with that one declaration typed `Float` by hand, the whole document passes.

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

**D9 — type inference is thin and inconsistent.** Concrete evidence in the current output:
samtools' `region` is a genomic interval, but every positional is typed `File`, so it
renders as `Array[File]` and a WDL engine will try to localize `chr1:100-200` as a path.
The angle-bracket convention is not a usable signal either — cnvkit's `targets` is a bare
word and genuinely is a file. Options with `argcount == 0` are likewise typed `String`
when they are plainly `Boolean`. Inference should live in one place keyed on the model.
The WDL template keeps a `defined()` arm for exactly those `String?` switches; once the docopt reader types them Boolean, that arm is dead and should go, which will deliberately change the samtools fixture's output.

**D10 — `parameter_meta` is mostly empty from the help-text reader.** `Option.parse` in
the vendored parser already splits the description off the option line and then discards
it, so retaining it is a small change in a file we now own. The argparse reader populates
`Argument.doc` from `action.help`, and the docopt reader now fills it for alternative
positional spellings, so only the option descriptions are missing.

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

### Working on the vendored parser

`doc2wrapper/_docopt.py` is docopt 0.6.2 with every definition of the runtime
argv-matching path deleted whole, and nothing else changed except two regex literals that
gained the `r` prefix they always needed. That property is what makes it reviewable
against upstream, so **do not reformat it** and do not add features to it. Deleting
`docopt()` was deliberate: it is the function whose `argv=sys.argv[1:]` default caused D1,
and its absence stops anyone from reaching for the obvious name and recommitting the bug.

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

Committing and pushing are handoff actions: report the proposed commands and wait, rather
than running them unprompted.
