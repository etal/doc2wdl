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

Note the naming skew, which is historical and not worth churning: the **checkout** is
`doc2wdl` and so is the **git remote** (`git@github.com:etal/doc2wdl`), while the
**package and console script** are `doc2wrapper`. `pyproject.toml`'s `project.urls`
entries point at a non-existent `etal/doc2wrapper` and are wrong.

`CLAUDE.md` is a symlink to this file, so the two cannot drift. That holds on macOS
and on WSL2's own ext4 filesystem. It does not hold for a checkout made by native
Windows git without Developer Mode, or on a DrvFS path under `/mnt/c`: git falls back
to `core.symlinks=false` and materializes `CLAUDE.md` as a nine-byte text file whose
contents are the string `AGENTS.md`. If you find yourself reading that, read this file
instead.

```
doc2wrapper/
  cli.py         argparse-based console entry point; two subcommands, `docopt` and `argparse`
  docopter.py    READER: help text -> object model, via the vendored parser
  argparser.py   READER: live ArgumentParser -> object model, via argparse internals
  tasktree.py    MODEL: the `Argument` dataclass and the helpers keyed on it
  wdlgen.py      WRITER: object model -> WDL; owns RESERVED_WDL_NAMES
  nfgen.py       WRITER: object model -> Nextflow (not reachable from the CLI)
  render.py      Jinja2 environment shared by the writers, at two levels:
                 `render_block` for one task, `render_document` for the whole file.
                 A writer needing different settings should build its own
                 environment rather than add a flag
  _docopt.py     VENDORED: the static grammar subset of docopt 0.6.2
  templates/     Jinja2 templates, one document and one block template per target:
                 document_template.{wdl,nf}, task_template.wdl, process_template.nf
tests/           pytest suite; `tests/example/` holds captured help texts and a Makefile
```

Data flow is strictly one-directional: **reader → `dict` of template kwargs → writer**.
The dict carries `title`, `usage`, `cli_prefix`, `cli_args` (a list of `Argument`), and
`has_output_file`. Adding a target language should mean adding a writer plus a template,
nothing else; adding an input format should mean adding a reader. If a change forces a
reader to know about WDL, the abstraction has been breached. Two consequences are
enforced: name sanitization against a language's reserved words happens in the **writer**
(`tasktree.rename_reserved`, called with `RESERVED_WDL_NAMES` or `RESERVED_NF_NAMES`),
and `type_and_default` lives in `tasktree` rather than in either writer.

Rendering is split in two because a document is not a task. Statements that may appear
only once per file — WDL's `version`, Nextflow's shebang and `nextflow.enable.dsl` —
belong to the document template, which is the only layer that knows how many blocks are
being emitted; keeping them in the block template is how a two-task document came to
carry two version statements. Each writer therefore exposes exactly one public
`render(tasks)` taking an iterable, so no caller of `wdlgen` or `nfgen` can obtain a
fragment and concatenate two of them. `render.render_block` still can, and nothing
enforces otherwise: a lone WDL block passes `miniwdl check`, because a version-less
document falls back to draft-2, where `~{...}` is not interpolation and every
declaration merely goes unreferenced. Its docstring is the only guard, so keep that
docstring true. Spelling a comment is likewise per-target and lives in the block
template: WDL 1.1 has only `#` line comments (`/* ... */` is a grammar error, not a
style preference), while the Nextflow template wraps the same prose in `/* ... */`, so
there is no shared helper to write and readers pass `usage` through as plain text.

One breach remains, and it is honest to name it: `Argument.wdl_type` puts a WDL type name
in the shared model, both readers fill it in, and the Nextflow template never reads it.
Repairing that means a neutral vocabulary in the model translated per writer — the same
shape `rename_reserved` already uses — and it moves both readers and both templates at
once, which is why it belongs to the type-inference work rather than to a passing edit.

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

`tests/conftest.py` wires both into the suite as fixtures, skipping when a checker is
absent. Any change to a template, a writer, or type inference **must** go through them.

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

`tests/example/Makefile` encodes an older shell-level version of the same loop
(`make test`, `make check`); it assumes `doc2wrapper` is on `PATH`, and its
`cnvkit-antitarget-argparse` target needs `cnvlib`. That import works, but only if you
call the environment's interpreter directly —
`/opt/homebrew/Caskroom/miniconda/base/envs/cnvkit/bin/python3` — because `conda run -n
cnvkit` and `conda activate` both fail on this machine with a broken activation hook.
An earlier revision of this file recorded `cnvlib` as simply "not importable"; that
conclusion came from trusting `conda run`, and it cost the flagship fixture.

## Current state

The help-text path works end to end and is covered by tests. The argparse path now emits
valid WDL for a parser with subcommands, but still misstates types and collides with
keywords. Everything below was verified by execution on 2026-08-16, and each open item is
filed in beads — run `bd ready`.

### What the help-text reader will and will not handle

Measured, not estimated, over a corpus of 36 help texts — 34 captured from real tools on
this machine, spanning argparse-generated, hand-written getopt C, and Go/Cobra styles,
plus 2 clearly-labelled synthetic cases covering shapes no installed tool produced. The
corpus lived in `/tmp/d2w-corpus/` and is not preserved; re-capturing it is filed as
follow-up work. Counts against all 36 unless stated:

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

**The argparse reader has no such limits** — it reads a live parser object and needs no
text parsing at all. With D2 and D11 fixed it now emits a single valid document for a
parser with subcommands: `cnvlib.commands.AP`, the README's flagship example, unpacks to
35 tasks under one `version 1.1`. It is nonetheless still the *more* broken path. Three
defects stand between it and WDL that checks, and they are not a sequence — measured on
that flagship output, the first error `miniwdl check` reports is **D3**, at
`String? output_dir = .`, an unquoted string default; **D12** bites separately wherever a
dest collides with a WDL keyword (`String? output`); and **D6** never blocks a checker at
all, because `Array[Boolean] scatter_ = []` is valid WDL that quietly pins every bare
flag on. So D3 and D12 are validity defects and D6 is a silent-correctness defect, which
is the more dangerous kind. Two further defects are reachable only from parsers the two
bundled fixtures do not resemble: **D15**, a paired boolean flag that crashes the reader,
and **D16**, a double quote in help text that breaks the generated `parameter_meta`.

### Open defects

**D15 — `argparse.BooleanOptionalAction` crashes the reader, and its last flag is the
negated one.** It subclasses `Action` directly rather than any handled class, so it hits
the same fatal `else` D13 did. Adding it to the tuple is not sufficient, which is why the
D13 fix left it out: the reader picks `option_strings[-1]`, which for this action is
`--no-foo`, so a Boolean input set to true would render the negation. The action carries
both spellings, and the correct rendering — emit one flag or the other, never neither —
is not expressible in either block template today.

**D16 — a double quote in help text terminates the WDL `parameter_meta` literal.**
`task_template.wdl` writes `{{ arg.name }}: "{{ arg.doc }}"` with no escaping. Measured
on `mypy.dmypy.client.parser`, whose `inspect` subcommand documents its span format as
`(e.g. 1:2:3:4:"int")`, and on `cnvlib.commands.AP`, where 6 of 392 help strings carry a
quote. Escaping belongs to the writer: Jinja2's autoescaping is HTML escaping and is
pinned off in both writers for good reason, so this needs a per-target filter. A
backslash has the same exposure, and WDL reads `~{` inside a string as interpolation.

**D14 — task titles can collide across tasks in one document.** `str.title()` lowercases
interior capitals, so subcommands `runAll` and `runall` both become task `ToolRunall`,
and `miniwdl` rejects the document with `Multiple tasks named ToolRunall`. Latent until
now, because a multi-task document was invalid anyway; the D11 fix makes multi-task the
normal argparse output. Distinct from the argument-name collision already filed.

**D3 — Python literals leak into WDL.** `Boolean? verbose = False` is emitted; WDL spells
it `false`. Defaults pass through `str()`/`repr()` with no target-language serializer.

**D5 — the Nextflow path is unreachable and invalid.** `cli.py` never imports `nfgen`, so
there is no way to ask for Nextflow output. Invoked directly, it produces
`val output_file = "$output_file_name"`, which `nextflow lint` rejects at `19:25`. The
template also carries WDL idioms into Groovy: `${if defined(x) then "-f" else ""}` is not
Groovy, and `${"-f " + x}` renders the literal `-f null` rather than eliding the flag.
Nextflow's optional-flag idiom is different and the template needs to be rewritten
against `nextflow lint`, not adapted from the WDL one. `tests/test_generate.py` carries a
`strict=True` xfail for this, so it will announce itself the moment it starts passing.
The document/block split has already removed the shebang and `nextflow.enable.dsl` from
`process_template.nf`, so the rewrite's surface is now process-level syntax only. Note for
that work: a `/* ... */` comment can be terminated early by a `*/` inside captured help
text, which per-line `//` would avoid — WDL's `#` has no such hazard.

**D6 — `is_array` is inverted in the argparse reader.** It computes
`is_array=(action.nargs in (0, 1, "?"))`, which marks scalars as arrays; the array cases
are `nargs` in `("*", "+")`, an integer greater than one, and `action="append"`. The WDL
template now consumes `is_array`, so this error has become visible output rather than a
dormant field, and the visible output is worse than a wrong type name. A bare flag is
declared `Array[Boolean] recursive = []` and rendered as
`~{if defined(recursive) then "--recursive" else ""}`; `defined()` is false only for
`None`, and a non-optional declaration carrying a default is never `None`, so the flag
is emitted unconditionally and no input value can switch it off. Verified with miniwdl's
own evaluator, which returns `'--recursive'` for the declared default of `[]`.

**D9 — type inference is thin and inconsistent.** Concrete evidence in the current output:
samtools' `region` is a genomic interval, but every positional is typed `File`, so it
renders as `Array[File]` and a WDL engine will try to localize `chr1:100-200` as a path.
The angle-bracket convention is not a usable signal either — cnvkit's `targets` is a bare
word and genuinely is a file. Options with `argcount == 0` are likewise typed `String`
when they are plainly `Boolean`. Inference should live in one place keyed on the model.

**D10 — `parameter_meta` is mostly empty from the help-text reader.** `Option.parse` in
the vendored parser already splits the description off the option line and then discards
it, so retaining it is a small change in a file we now own. The argparse reader populates
`Argument.doc` from `action.help`, and the docopt reader now fills it for alternative
positional spellings, so only the option descriptions are missing.

**D8 — packaging metadata drift (partly fixed).** `testpaths` and the missing
`package-data` entry are corrected. Still wrong: `readme = "README.rst"` names a file that
does not exist, which setuptools ignores silently, shipping a wheel with no long
description; `requires-python = ">=3.7"` is contradicted by `tasktree.py`, which uses
`str | None` and needs 3.10; and `project.urls` points at a non-existent repository.

Smaller known wrongness, filed but low priority: help-text notation leaks into flags, so
samtools' `--region[s]-file FILE` becomes a literal flag `--region[s]-file`. Not low
priority any more, though it is still small: **D12**, `RESERVED_WDL_NAMES` holding one
entry against roughly thirty WDL 1.1 keywords, is what now blocks the argparse path.
With D2 and D11 fixed, a parser with a `--output` option renders `String? output`, and
`miniwdl` rejects it with `unexpected keyword output`.

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

Note the consequence for formatting generally: the tree was formatted with a `black`
predating the blank-line-after-module-docstring rule, so a modern `black` or
`ruff format` run would rewrite every file. Reformat deliberately and separately, with
`_docopt.py` excluded — never as a side effect of another change.

## Conventions

- Formatting is `black`, already applied across the tree; keep it.
- Both readers must keep producing the same template-kwargs `dict`. Widening the contract
  means updating `tasktree.Argument`, both readers, and both templates together.
- Prefer fixing a defect in the writer or the model over fixing it in a reader; a bug that
  appears in one reader's output usually belongs to the layer both share.
- Determinism includes ordering: preserve the order in which arguments appear in the
  source interface, and never iterate an unordered set to build `cli_args`.
- Committed generated output, if any is ever added as a fixture, is regenerated by the
  tool and never hand-edited.

## Issue tracking

This project uses **bd** (beads). Run `bd prime` for full workflow context.

> **Architecture in one line:** Issues live in a local Dolt database
> (`.beads/dolt/`); cross-machine sync uses `bd dolt push/pull` (a
> git-compatible protocol), stored under `refs/dolt/data` on your git
> remote — separate from `refs/heads/*` where your code lives.
> `.beads/issues.jsonl` is a passive export, not the wire protocol.
>
> See [sync-concepts](https://github.com/gastownhall/beads/blob/main/docs/core-concepts/sync-concepts.md)
> for the one-screen overview and anti-patterns (don't treat JSONL as the
> source of truth; don't `bd import` during normal operation; don't
> reach for third-party Dolt hosting before trying the default).

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --claim  # Claim work atomically
bd close <id>         # Complete work
bd dolt push          # Push beads data to remote
```

Committing, pushing and `bd dolt push` are handoff actions: report the proposed commands
and wait, rather than running them unprompted.
