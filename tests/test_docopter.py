"""Reading a tool's interface out of its help text."""

import argparse

import pytest

from doc2wrapper import docopter

# A usage line whose `--tag` is declared as taking a value but written bare.  The
# option parser then consumes the closing bracket as that value and bracket depth
# desyncs, so parse_pattern raises.  `tests/corpus/help-cnvkit-batch.txt` fails the
# same way on an opening bracket, where argparse writes `[-p [PROCESSES]]`.
ARITY_OVERLOAD = """Usage: tool [--tag] <input>

Options:
  --tag TAG          Tag to apply.
"""

NO_USAGE_SECTION = """tool - do a thing

Options:
  -v, --verbose      Be loud.
"""

TWO_USAGE_SECTIONS = """Usage: tool <input>

Subcommands are documented separately.

Usage: tool sub <other>
"""

# A `usage:` marker on a line of its own, with the synopsis after a blank line.
# `printable_usage` stops at the blank line and returns just the marker.
EMPTY_USAGE_SECTION = """usage:

tool foo
"""

# Several alternatives naming the same subcommand.  docopt binds `|` at sequence
# level, so `sub` is repeated inside every branch of the parsed tree.
REPEATED_SUBCOMMAND = """Usage: prog sub <x>
       prog sub <y> <z>
"""


def test_positionals_are_recovered_from_a_usage_line(help_text):
    """The reader must not silently drop the arguments the task exists to accept.

    Asserting on the RETURN VALUE is the point: the defect this replaces printed a
    warning and returned an empty list, which no test of "does it raise" can see.
    """
    _usage, positionals, _options = docopter.parse(help_text("cnvkit-antitarget"))
    assert [p.spellings for p in positionals] == [["targets"]]
    assert positionals[0].is_required
    assert not positionals[0].is_array


def test_alternative_spellings_are_one_positional(help_text):
    """`<in.bam>|<in.sam>|<in.cram>` is one input accepting three formats."""
    _usage, positionals, _options = docopter.parse(help_text("samtools-view"))
    assert [p.spellings for p in positionals] == [
        ["<in.bam>", "<in.sam>", "<in.cram>"],
        ["region"],
    ]


def test_repeated_optional_positional_is_an_optional_array(help_text):
    """`[region ...]` repeats, and appears in only one usage alternative."""
    _usage, positionals, _options = docopter.parse(help_text("samtools-view"))
    region = positionals[1]
    assert region.is_array
    assert not region.is_required


def test_command_words_are_the_prefix_not_arguments(help_text):
    """A subcommand names the command being wrapped; it is not an input to it."""
    usage, positionals, options = docopter.parse(help_text("samtools-view"))
    task = docopter.transform(usage, positionals, options)
    assert task["cli_prefix"] == "samtools view"
    assert "view" not in [arg.name for arg in task["cli_args"]]


def test_usage_reaches_the_model_as_plain_prose(help_text):
    """A reader must not spell a target language's comment.

    The reader used to prefix every usage line with `# ` itself, which is WDL's
    comment syntax and not every target's, so a Nextflow block comment received
    stray hashes.  Worse, it masked the fact that the other reader supplies no
    prefix at all, and the WDL template dropped that prose at file scope, where it
    is a syntax error.
    """
    task = docopter.transform(*docopter.parse(help_text("samtools-view")))
    assert task["usage"].startswith("samtools view")
    assert "#" not in task["usage"]


@pytest.mark.parametrize(
    "doc", [NO_USAGE_SECTION, TWO_USAGE_SECTIONS, EMPTY_USAGE_SECTION]
)
def test_missing_or_ambiguous_usage_section_is_fatal(doc):
    """With no single usage line there is no command line to wrap.

    The failure must be this one exception, because that is what the console script
    catches; anything else reaches the user as a traceback.
    """
    with pytest.raises(docopter.UsageNotFound):
        docopter.parse(doc)


def test_subcommand_is_excluded_from_every_alternative():
    """A subcommand is dropped from every branch, so alternatives stay aligned.

    Naively removing it from the first alternative only would merge `sub` in as a
    spelling of `<x>`, push `<y>` and `<z>` one slot right, and silently demote
    them to optional.
    """
    usage, positionals, options = docopter.parse(REPEATED_SUBCOMMAND)
    assert docopter.transform(usage, positionals, options)["cli_prefix"] == "prog sub"
    assert [p.spellings for p in positionals] == [["<x>", "<y>"], ["<z>"]]
    assert positionals[0].is_required
    assert not positionals[1].is_required


def test_unparseable_usage_line_degrades_instead_of_crashing(capsys):
    """Options survive when only the usage line is unreadable.

    A partial task the user finishes by hand beats a traceback: the degraded files
    in `tests/corpus` hit this, and their options are still recoverable.
    """
    _usage, positionals, options = docopter.parse(ARITY_OVERLOAD)
    assert positionals == []
    assert [opt.long for opt in options] == ["--tag"]
    assert "could not read positional arguments" in capsys.readouterr().err


def test_help_and_version_options_are_not_task_inputs(help_text):
    """These describe the tool's own interface; WDL states them in meta{} instead."""
    task = docopter.transform(*docopter.parse(help_text("cnvkit-antitarget")))
    assert "help" not in [arg.name for arg in task["cli_args"]]


def test_output_option_becomes_the_declared_output_file(help_text):
    """Both templates reference this identifier from their output section."""
    task = docopter.transform(*docopter.parse(help_text("cnvkit-antitarget")))
    assert task["has_output_file"]
    assert docopter.OUTPUT_FILE_NAME in [arg.name for arg in task["cli_args"]]


HOSTILE_TOKENS = ["<in.bam>", "--min-size", "-1", "<a/b>", "--", "FILE(S)", "-#"]


@pytest.mark.parametrize("token", HOSTILE_TOKENS)
def test_every_identifier_starts_with_a_letter(token):
    """WDL rejects a leading underscore exactly as it rejects a leading digit.

    Asserting the invariant rather than each expected string: an earlier guard
    prefixed `_`, which satisfied a value-by-value test while still emitting an
    identifier the checker refuses.
    """
    assert docopter._identifier(token)[:1].isalpha()


def test_identifiers_keep_only_legal_characters():
    """Help-text punctuation is an open set, so only legal characters survive."""
    assert docopter._identifier("<in.bam>") == "in_bam"
    assert docopter._identifier("--min-size") == "min_size"
    assert docopter._identifier("-1") == "arg_1"
    assert docopter._identifier("<a/b>") == "ab"


def test_task_title_is_a_legal_identifier():
    """`2to3` and `run.sh` would otherwise name a task WDL cannot parse."""
    for usage, expected in (("2to3", "Task2To3"), ("run.sh", "Run_Sh")):
        task = docopter.transform(*docopter.parse(f"Usage: {usage} <f>\n"))
        assert task["title"] == expected


def test_a_short_only_help_flag_is_still_skipped():
    """Not every tool spells `--help`; docopt reports `-h` as the option's name."""
    task = docopter.transform(
        *docopter.parse("Usage: tool <in>\n\nOptions:\n  -h   show help\n")
    )
    assert [arg.name for arg in task["cli_args"]] == ["in"]


def descriptions(doc):
    """Each parsed option's ``(short, long)`` flags and description."""
    _usage, _positionals, options = docopter.parse(doc)
    return {(opt.short, opt.long): opt.description for opt in options}


def test_option_descriptions_reach_the_model(help_text):
    """Wrapped lines join into one, and a `[Default: ...]` note stays in the text."""
    task = docopter.transform(*docopter.parse(help_text("cnvkit-antitarget")))
    docs = {arg.name: arg.doc for arg in task["cli_args"] if arg.option_flag}
    assert docs == {
        "access": "Regions of accessible sequence on chromosomes (.bed), "
        "as output by genome2access.py.",
        "avg_size": "Average size of antitarget bins (results are approximate). "
        "[Default: 150000]",
        "min_size": "Minimum size of antitarget bins (smaller regions are dropped). "
        "[Default: 1/16 avg size, calculated]",
        "output_file_name": "Output file name.",
    }


def test_a_description_ends_at_a_section_heading(help_text):
    """samtools starts `Input options:` on the line after `-P`, with no blank line."""
    docs = descriptions(help_text("samtools-view"))
    assert docs[("-P", "--fetch-pairs")] == (
        "Retrieve complete pairs even when outside of region"
    )


def test_a_description_ends_at_a_blank_line():
    """An indented paragraph after a blank line is an epilog, not the last option's.

    The blank line here holds spaces, as a help text's sometimes do.
    """
    doc = "Usage: tool [-v]\n\nOptions:\n  -v  Be loud.\n      \n    See the manual.\n"
    assert descriptions(doc) == {("-v", None): "Be loud."}


def options_of(doc):
    """Each parsed option's flags, argument count and default, in order."""
    _usage, _positionals, options = docopter.parse(doc)
    return [(opt.short, opt.long, opt.argcount, opt.value) for opt in options]


def test_a_flag_opening_a_wrapped_line_continues_the_description():
    """Indented as the description is, the flag is prose, not an option of its own.

    cnvkit's `import-rna` help wraps this way at 80 columns.  Read as an option, the
    wrapped `--normal` took the `[Default: polish]` and was always passed.
    """
    doc = """Usage: tool [options]

Options:
  --method NAME      Strategy, which uses the
                     --normal samples only. [Default: polish]
  -n, --normal FILE  Normal samples.
"""
    assert options_of(doc) == [
        (None, "--method", 1, "polish"),
        ("-n", "--normal", 1, None),
    ]
    docs = descriptions(doc)
    assert docs[(None, "--method")] == (
        "Strategy, which uses the --normal samples only. [Default: polish]"
    )
    assert docs[("-n", "--normal")] == "Normal samples."


def test_flags_separated_by_two_spaces_state_no_description_column():
    """wget writes `-V,  --version`, so the text after two spaces is more flags.

    Upstream's two-space rule ends the flags there, which loses `--version` as it
    always has.  Taken as a description, that text would also put the column at
    the long-only options' indentation, and every one of them would read as prose.
    """
    doc = """Usage: wget [OPTION]... [URL]...

Startup:
  -V,  --version                   display the version of Wget and exit
  -b,  --background                go to background after startup
       --config=FILE               specify config file to use
       --no-config                 do not read any config file
"""
    assert options_of(doc) == [
        ("-V", None, 0, False),
        ("-b", None, 0, False),
        (None, "--config", 1, None),
        (None, "--no-config", 0, False),
    ]


def test_an_option_documented_twice_keeps_each_description():
    """Each entry's description stays with the option it was written beside."""
    doc = "Usage: tool [options]\n\nBasic:\n  -v  Be loud.\n\nMore:\n  -v  Be louder.\n"
    _usage, _positionals, options = docopter.parse(doc)
    assert [(opt.short, opt.description) for opt in options] == [
        ("-v", "Be loud."),
        ("-v", "Be louder."),
    ]


def test_an_entry_runs_across_a_blank_line_but_not_into_an_epilog():
    """clap writes a `[default: ...]` as a paragraph of its own within the entry.

    A paragraph indented no deeper than the options belongs to none of them, so
    its `[default: ...]` is not the last option's.
    """
    doc = """Usage: tool [OPTIONS]

Options:
      --color <COLOR>
          Whether to color the output

          [default: auto]

  -n, --number <N>
          How many to keep

Notes:
  The [default: 5] of the old release no longer applies.
"""
    assert options_of(doc) == [
        (None, "--color", 1, "auto"),
        ("-n", "--number", 1, None),
    ]
    assert descriptions(doc)[(None, "--color")] == "Whether to color the output"


def test_an_option_line_without_a_description_takes_a_known_column():
    """argparse moves the description of an option with wide flags to the next line.

    Before any description has stated a column, a wrapped line opening with a flag
    is read as an option; after one has, it continues the description, whether the
    column came from this entry's own first wrapped line or from an earlier entry's.
    """
    doc = """Usage: tool [options]

Options:
  --first STR
           --opened as an option.
  --second STR
           Tags to keep, as
           --exclude does.
  --third STR
           -1 runs until convergence.
"""
    docs = descriptions(doc)
    assert docs[(None, "--first")] == ""
    assert docs[(None, "--second")] == "Tags to keep, as --exclude does."
    assert docs[(None, "--third")] == "-1 runs until convergence."


def test_a_line_end_rejoins_only_where_textwrap_breaks_a_word():
    """A `--` after punctuation may end a line, as cnvkit's `'--gene'` does.

    textwrap breaks after a hyphen only where two letters follow, so a hyphen
    before a bracket was followed by a space in the text it wrapped.
    """
    doc = """Usage: tool [options]

Options:
  -c STR  Genes are shown unless '--
          gene' is already given.
  -w STR  Use a half-
          (or quarter-) width bin.
"""
    docs = descriptions(doc)
    assert docs[("-c", None)] == "Genes are shown unless '--gene' is already given."
    assert docs[("-w", None)] == "Use a half- (or quarter-) width bin."


def test_an_option_only_in_the_synopsis_has_no_description():
    """The usage line can name a flag the options block never documents."""
    doc = "Usage: tool [--extra] [-g N] <in>\n\nOptions:\n  -g N  Gain.\n"
    assert descriptions(doc) == {("-g", None): "Gain.", (None, "--extra"): ""}


def test_descriptions_match_the_parser_that_printed_them():
    """Read from the help text, each description is the `help` argparse was given.

    argparse wraps after a hyphen, so `copy-number` reaches a line end at some of
    these widths and must be rejoined without a space, while ` - ` and `5- to`,
    where argparse never breaks a word, keep their spaces.  Below about 50 columns
    argparse also splits words longer than the help column, and it may break before
    a `--` that follows punctuation, as in `'--gene'`; neither leaves a trace a
    reader can undo.
    """
    helps = {
        "--segment": "Copy-number segmentation of the log2 ratios - with re-centering "
        "applied to each sample's copy-ratio profile - before calling.",
        "--chromosome": "Chromosome or range such as 'chr1:2333000-2444000'; all "
        "genes in it are shown, in a 5- to 10-fold window around each probe.",
        "--gene": "Comma-separated gene names.",
    }
    for width in range(50, 121, 5):
        parser = argparse.ArgumentParser(
            prog="tool",
            formatter_class=lambda prog: argparse.HelpFormatter(prog, width=width),
        )
        for flag, text in helps.items():
            parser.add_argument(flag, help=text)
        docs = descriptions(parser.format_help())
        assert {key[1]: docs[key] for key in docs if key[1] in helps} == helps, width
