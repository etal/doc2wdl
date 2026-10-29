"""Reading a tool's interface out of its help text."""

import argparse

import pytest

from doc2wrapper import docopter
from doc2wrapper.tasktree import ValueType

# A usage line whose `--tag` is declared as taking a value but written bare.  The
# option parser then consumes the closing bracket as that value and bracket depth
# desyncs, so parse_pattern raises.  Unlike an opening bracket after the option,
# which is argparse's notation for an optional value, nothing here can be repaired.
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

# Alternatives naming different subcommands.  A task wraps one command, so only
# the first can be wrapped.
DIFFERENT_SUBCOMMANDS = """Usage: git log [-p] <a>
       git show [-s] <b>
"""


def test_positionals_are_recovered_from_a_usage_line(help_text):
    """The reader must not silently drop the arguments the task exists to accept.

    Asserting on the RETURN VALUE is the point: the defect this replaces printed a
    warning and returned an empty list, which no test of "does it raise" can see.
    """
    positionals = docopter.parse(help_text("cnvkit-antitarget")).positionals
    assert [p.spellings for p in positionals] == [["targets"]]
    assert positionals[0].is_required
    assert not positionals[0].is_array


def test_alternative_spellings_are_one_positional(help_text):
    """`<in.bam>|<in.sam>|<in.cram>` is one input accepting three formats."""
    positionals = docopter.parse(help_text("samtools-view")).positionals
    assert [p.spellings for p in positionals] == [
        ["<in.bam>", "<in.sam>", "<in.cram>"],
        ["region"],
    ]


def test_repeated_optional_positional_is_an_optional_array(help_text):
    """`[region ...]` repeats, and appears in only one usage alternative."""
    region = docopter.parse(help_text("samtools-view")).positionals[1]
    assert region.is_array
    assert not region.is_required


def test_command_words_are_the_prefix_not_arguments(help_text):
    """A subcommand names the command being wrapped; it is not an input to it."""
    task = docopter.transform(*docopter.parse(help_text("samtools-view")))
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


def test_subcommand_is_excluded_from_every_alternative(capsys):
    """A subcommand is dropped from every branch, so alternatives stay aligned.

    Naively removing it from the first alternative only would merge `sub` in as a
    spelling of `<x>`, push `<y>` and `<z>` one slot right, and silently demote
    them to optional.
    """
    parsed = docopter.parse(REPEATED_SUBCOMMAND)
    assert parsed.cli_prefix == "prog sub"
    positionals = parsed.positionals
    assert [p.spellings for p in positionals] == [["<x>", "<y>"], ["<z>"]]
    assert positionals[0].is_required
    assert not positionals[1].is_required
    assert capsys.readouterr().err == ""


def test_options_before_a_later_alternatives_subcommand_keep_it_a_command_word(
    capsys,
):
    """An option group may precede the subcommand in an alternative after the first."""
    doc = "Usage: prog sub <x>\n       prog [-v] sub <y>\n"
    parsed = docopter.parse(doc)
    assert parsed.cli_prefix == "prog sub"
    assert [p.spellings for p in parsed.positionals] == [["<x>", "<y>"]]
    assert capsys.readouterr().err == ""


def test_alternatives_naming_different_subcommands_warn_and_stay_aligned(capsys):
    """The first alternative's command is wrapped, and the user is told so.

    The other alternative's subcommand is dropped by its position, so `<b>` shares
    a slot with `<a>`.  Dropping only the words named on the first line used to
    leave `show` in the first slot, as a spelling of `<a>`, and push `<b>` into a
    second, all without any message on standard error.
    """
    parsed = docopter.parse(DIFFERENT_SUBCOMMANDS)
    assert parsed.cli_prefix == "git log"
    assert [p.spellings for p in parsed.positionals] == [["<a>", "<b>"]]
    assert "begin with different command words" in capsys.readouterr().err


def test_only_the_leading_command_words_are_dropped():
    """A later argument spelled like the subcommand is still an argument."""
    parsed = docopter.parse("Usage: tool index <in> [index]\n")
    assert parsed.cli_prefix == "tool index"
    assert [p.spellings for p in parsed.positionals] == [["<in>"], ["index"]]


def test_a_command_word_must_also_begin_the_parsed_synopsis():
    """Text that only looks like a command word stays an argument.

    `sub|alt` reads as one literal word, but the synopsis offers a choice between
    `sub` and `alt`, so the prefix used to carry a shell pipe.
    """
    parsed = docopter.parse("Usage: prog sub|alt <x>\n")
    assert parsed.cli_prefix == "prog"
    assert [p.spellings for p in parsed.positionals] == [["sub", "alt"], ["<x>"]]


@pytest.mark.parametrize(
    "synopsis, spellings",
    [("prog A) (B", [["A"], ["B"]]), ("prog A) | B | (C", [["A", "B", "C"]])],
)
def test_a_synopsis_that_closes_its_parenthesis_early_still_parses(synopsis, spellings):
    """`formal_usage` wraps the synopsis in one group, which `A)` closes early.

    The tree then holds several groups, or alternatives that are single tokens.
    """
    parsed = docopter.parse(f"Usage: {synopsis}\n")
    assert parsed.cli_prefix == "prog"
    assert [p.spellings for p in parsed.positionals] == spellings


def test_gnu_continuation_marker_is_not_an_argument(capsys):
    """GNU help opens each further synopsis line with `or:`, which names nothing.

    `formal_usage` splits alternatives at the program name, so the marker used to
    end the previous alternative and become an input named `or`.
    """
    doc = "Usage: tool [-v] SOURCE DEST\n  or:  tool [-v] SOURCE... DIRECTORY\n"
    parsed = docopter.parse(doc)
    assert parsed.cli_prefix == "tool"
    assert [p.spellings for p in parsed.positionals] == [
        ["SOURCE"],
        ["DEST", "DIRECTORY"],
    ]
    assert capsys.readouterr().err == ""


def test_a_line_indented_less_than_the_program_name_ends_the_synopsis():
    """samtools and htsfile write their options block directly under the synopsis.

    The usage text the templates comment ends with the synopsis too.
    """
    doc = "Usage: prog [-a] FILE\nOptions:\n  -a, --all\n      Everything.\n"
    parsed = docopter.parse(doc)
    assert parsed.usage == "prog [-a] FILE"
    assert [p.spellings for p in parsed.positionals] == [["FILE"]]
    assert [(opt.short, opt.long, opt.description) for opt in parsed.options] == [
        ("-a", "--all", "Everything.")
    ]


@pytest.mark.parametrize(
    ("doc", "expected"),
    [
        (
            "Usage: prog [-a] FILE\n  or:  prog [-b] DIR\n",
            "prog [-a] FILE\n  or:  prog [-b] DIR",
        ),
        (
            "Usage:\tprog [-a] FILE\n\tprog [-b] DIR\n",
            "prog [-a] FILE\n\tprog [-b] DIR",
        ),
        (
            " Usage:\tprog [-a] FILE\n\tprog [-b] DIR\n",
            "prog [-a] FILE\n\tprog [-b] DIR",
        ),
        (
            "Usage:\n  prog [-a] FILE\n  prog [-b] DIR\n",
            "prog [-a] FILE\n  prog [-b] DIR",
        ),
    ],
)
def test_a_synopsis_continues_on_lines_aligned_with_its_program_name(doc, expected):
    """GNU's `or:` line, a tab, or a marker alone on its line keep each alternative.

    Where the marker stands alone, nothing on its line states a column.  The usage
    text keeps every line as written, tabs included.
    """
    parsed = docopter.parse(doc)
    assert parsed.usage == expected
    assert [p.spellings for p in parsed.positionals] == [["FILE", "DIR"]]


def test_a_flag_and_its_description_end_a_synopsis_with_no_column():
    """With the marker alone on its line, an option line still ends the synopsis."""
    doc = "Usage:\n  prog [-a]\n[-b] FILE\n  -a, --all  Everything.\n"
    parsed = docopter.parse(doc)
    assert parsed.usage.splitlines() == ["prog [-a]", "[-b] FILE"]
    assert [p.spellings for p in parsed.positionals] == [["FILE"]]
    assert [(opt.short, opt.description) for opt in parsed.options] == [
        ("-a", "Everything."),
        ("-b", ""),
    ]


@pytest.mark.parametrize(
    "after",
    [
        "  Download files and their dependencies\n",
        "  The default action is to add or replace entries.\n",
        "Options:\n  -a  Everything.\n",
    ],
)
def test_prose_or_a_heading_ends_a_synopsis_with_no_column(after):
    """prefetch describes its synopsis on the next line, and zip writes a paragraph.

    Neither line carries usage notation or opens with the program name.
    """
    parsed = docopter.parse(f"Usage:\n  prog [-a] <in>\n{after}")
    assert parsed.usage == "prog [-a] <in>"
    assert [p.spellings for p in parsed.positionals] == [["<in>"]]


@pytest.mark.parametrize(
    "wrapped", ["[package-index-options] <file> ...", "install <pkg>", "FILE"]
)
def test_a_line_with_notation_or_a_placeholder_continues_a_synopsis(wrapped):
    """pip wraps at column 0, and argparse may wrap a bare placeholder.

    `install <pkg>` opens with a word that is not the program name, but carries
    notation.
    """
    usage = docopter.parse(f"Usage:\n  prog [options] <req>\n{wrapped}\n").usage
    assert usage == f"prog [options] <req>\n{wrapped}"


def test_a_wrapped_synopsis_line_opening_with_a_flag_is_no_option(capsys):
    """argparse may wrap a synopsis so that a line opens with a flag, as zipfile's.

    Read as an entry, `-c <n> [<f> ...] | -t <z>)` declares `-t` a second time.
    """
    doc = """usage: prog [-h] (-l <z> | -e <z> <d> |
                 -c <n> [<f> ...] | -t <z>)

options:
  -l <z>          List.
  -e <z> <d>      Extract.
  -c <n> [<f> ...]
                  Create.
  -t <z>          Test.
"""
    parsed = docopter.parse(doc)
    assert capsys.readouterr().err == ""
    assert [opt.short for opt in parsed.options] == ["-l", "-e", "-c", "-t", "-h"]
    assert [p.spellings for p in parsed.positionals] == [["<d>", "<f>"]]


def test_unparseable_usage_line_degrades_instead_of_crashing(capsys):
    """Options survive when only the usage line is unreadable.

    A partial task the user finishes by hand beats a traceback, and the options are
    still recoverable.
    """
    parsed = docopter.parse(ARITY_OVERLOAD)
    assert parsed.cli_prefix == "tool"
    assert parsed.positionals == []
    assert [opt.long for opt in parsed.options] == ["--tag"]
    assert "could not read positional arguments" in capsys.readouterr().err


# argparse writes an `nargs="?"` value as `[-p [N]]` and an `nargs="*"` one as
# `[-n [FILES ...]]`; git writes an optional value with no space, as `-u[<mode>]`.
# A group after a switch, or after an option whose value is attached, as in `-p4`,
# is an optional positional.  `-x` and `--only` are named in the synopsis alone, and
# keep the order the synopsis gives them.
OPTIONAL_VALUES = """Usage: tool -v [<extra>] --loud [<more>] -p4 [<last>] {} [-x] [--only] <in>

Options:
  -v                 Be verbose.
  --loud             Be loud.
  -p, --processes [N]
                     Processes to use.
  -n [FILES ...]     Normal samples.
  -u <mode>          Untracked mode.
"""


@pytest.mark.parametrize(
    "spelled",
    ["[-p [N]]", "[--processes [N]]", "[-vp [N]]", "[-n [FILES ...]]", "[-u[<mode>]]"],
)
def test_an_optional_value_in_brackets_belongs_to_its_option(spelled, capsys):
    """The bracketed value is the option's, and other groups keep their brackets."""
    parsed = docopter.parse(OPTIONAL_VALUES.format(spelled))
    assert [(p.spellings, p.is_required) for p in parsed.positionals] == [
        (["<extra>"], False),
        (["<more>"], False),
        (["<last>"], False),
        (["<in>"], True),
    ]
    assert [(opt.short, opt.long, opt.argcount) for opt in parsed.options] == [
        ("-v", None, 0),
        (None, "--loud", 0),
        ("-p", "--processes", 1),
        ("-n", None, 1),
        ("-u", None, 1),
        ("-x", None, 0),
        (None, "--only", 0),
    ]
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("group", ["[A | B]", "[N <x>]", "[-q]", "[N [N ...]]"])
def test_a_group_that_is_not_one_placeholder_keeps_its_brackets(group, capsys):
    """A group that is not one placeholder keeps its brackets, so the retry fails.

    A failed second reading discards the options it registered.  This one repairs
    `[-p [N]]` and registers `--extra`, then fails on the group after the second
    `-p`, which is not a single placeholder: an alternation, two arguments, a flag,
    or the form argparse wrote for `nargs="*"` before Python 3.9.  The first reading
    failed before it reached `--extra`, so the options are the documented one alone.
    """
    doc = (
        f"Usage: tool [-p [N]] [--extra] [-p {group}] <in>\n\nOptions:\n  -p N  Gain.\n"
    )
    parsed = docopter.parse(doc)
    assert parsed.positionals == []
    assert [(opt.short, opt.long) for opt in parsed.options] == [("-p", None)]
    assert "unmatched '('" in capsys.readouterr().err


@pytest.mark.parametrize("retried", ["", " [-p [P]]"])
@pytest.mark.parametrize(
    "spelled",
    ["-n N [N ...]", "--normal N [N ...]", "-vn N [N ...]", "--normal=N [N ...]"],
)
@pytest.mark.parametrize("attached", [" ", "="])
def test_a_repeated_option_value_is_the_options_list(spelled, retried, attached):
    """argparse writes `nargs="+"` as `-n N [N ...]`; the repetition is the option's.

    Without `[-p [P]]`, the synopsis parses as written, so no retry reaches the
    group.  The option's entry, which repeats its placeholder too, marks the option
    a list.  Help written by hand may attach the value with `=`, in the synopsis or
    in the entry.
    """
    doc = (
        f"usage: tool [-h] [-v] [{spelled}]{retried} <in>\n\noptions:\n"
        "  -v               Be loud.\n"
        f"  -n, --normal{attached}N [N ...]\n                   Normal samples.\n"
        "  -p [P]           Processes.\n"
    )
    task = docopter.transform(*docopter.parse(doc))
    args = {arg.name: arg for arg in task["cli_args"]}
    assert list(args) == ["in", "v", "normal", "p"]
    assert args["normal"].is_array
    assert not args["normal"].flag_per_value
    assert not args["p"].is_array


@pytest.mark.parametrize("entry", ["  -f path  Root.\n", ""])
def test_a_list_after_an_option_value_stays_positional_without_its_entry(entry):
    """BSD find writes `-f path [path ...]`, where the list is the operands'."""
    doc = f"usage: find -f path [path ...] <expr>\n\noptions:\n{entry}"
    positionals = docopter.parse(doc).positionals
    assert [(p.spellings, p.is_array) for p in positionals][-2:] == [
        (["path"], True),
        (["<expr>"], False),
    ]


def test_help_and_version_options_are_not_task_inputs(help_text):
    """These describe the tool's own interface; WDL states them in meta{} instead."""
    task = docopter.transform(*docopter.parse(help_text("cnvkit-antitarget")))
    assert "help" not in [arg.name for arg in task["cli_args"]]


def test_output_option_becomes_the_declared_output_file(help_text):
    """Both templates reference this identifier from their output section."""
    task = docopter.transform(*docopter.parse(help_text("cnvkit-antitarget")))
    assert task["has_output_file"]
    assert docopter.OUTPUT_FILE_NAME in [arg.name for arg in task["cli_args"]]


def test_an_output_option_taking_a_list_is_an_ordinary_input():
    """The output sections name one file, which a list of names cannot be."""
    doc = (
        "usage: prog [--output OUT [OUT ...]] FILE\n\noptions:\n"
        "  --output OUT [OUT ...]  Outputs.\n"
    )
    task = docopter.transform(*docopter.parse(doc))
    assert not task["has_output_file"]
    assert [(arg.name, arg.is_array) for arg in task["cli_args"]] == [
        ("FILE", False),
        ("output", True),
    ]


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


def test_an_optional_part_of_a_flag_name_is_dropped_from_the_flag(help_text):
    """samtools rejects `--region[s]-file`; the shortest spelling is one it takes."""
    task = docopter.transform(*docopter.parse(help_text("samtools-view")))
    flags = {arg.name: arg.option_flag for arg in task["cli_args"]}
    assert flags["region_file"] == "--region-file"
    assert flags["target_file"] == "--target-file"
    assert flags["excl_flags"] == "--excl-flags"
    assert not any("[" in (arg.option_flag or "") for arg in task["cli_args"])


def test_an_optional_prefix_names_the_flag_not_its_negation():
    """`--[no-]verbose` passes `--verbose` when true, as its input's name says."""
    doc = "Usage: tool [options]\n\nOptions:\n  -v, --[no-]verbose  Be loud.\n"
    task = docopter.transform(*docopter.parse(doc))
    [arg] = task["cli_args"]
    assert (arg.name, arg.option_flag) == ("verbose", "--verbose")


@pytest.mark.parametrize(
    "entry", ["--color[=WHEN]", "-W[level]", "-[mM][-]module", "--a[b[c]]-d"]
)
def test_brackets_that_are_no_optional_part_of_a_name_skip_the_option(entry, capsys):
    """An optional attached value, or a set of letters, names no flag to pass."""
    doc = f"Usage: tool [options]\n\nOptions:\n  {entry}  Something.\n  -q  Quiet.\n"
    task = docopter.transform(*docopter.parse(doc))
    assert [arg.option_flag for arg in task["cli_args"]] == ["-q"]
    assert "names no single flag" in capsys.readouterr().err


@pytest.mark.parametrize(
    "entry",
    ["--apilog: Log.", "--  End of options.", "--------", "-#  Level.", "-W<w>"],
)
def test_notation_that_is_no_flag_skips_the_option(entry, capsys):
    """absl's trailing colon, a placeholder in the name, or no flag at all.

    bq reads `--apilog` with its value only when attached with `=`.
    """
    doc = f"Usage: tool [options]\n\nOptions:\n  {entry}\n  -q  Quiet.\n"
    task = docopter.transform(*docopter.parse(doc))
    assert [arg.option_flag for arg in task["cli_args"]] == ["-q"]
    assert "names no single flag" in capsys.readouterr().err


@pytest.mark.parametrize("flag", ["-nostdinc++", "--disable-@files", "-I-", "-@", "-%"])
def test_flags_with_punctuation_tools_accept_are_passed(flag, capsys):
    """gcc, java and BSD ls take these as written."""
    doc = f"Usage: tool [options]\n\nOptions:\n  {flag}  Something.\n"
    task = docopter.transform(*docopter.parse(doc))
    assert [arg.option_flag for arg in task["cli_args"]] == [flag]
    assert capsys.readouterr().err == ""


def test_a_bar_joins_two_spellings_of_one_flag():
    """SRA Toolkit writes `-A|--accession`; fastq-dump and prefetch take either.

    The long spelling is passed, as for `-A, --accession`, so `-h|--help` is
    recognized as help.
    """
    doc = (
        "Usage: tool [options] <path>\n\nOptions:\n"
        "  -A|--accession <acc>  Accession.\n"
        "  -h|--help  Show help.\n"
    )
    assert options_of(doc) == [
        ("-A", "--accession", 1, None),
        ("-h", "--help", 0, False),
    ]
    task = docopter.transform(*docopter.parse(doc))
    assert [(arg.name, arg.option_flag) for arg in task["cli_args"]] == [
        ("path", ""),
        ("accession", "--accession"),
    ]


@pytest.mark.parametrize("entry", ["--color[=WHEN]|--colour", "-a|-b|-c", "-ab|--long"])
def test_a_bar_that_joins_no_short_and_long_flag_is_not_read(entry, capsys):
    """Read as spellings, the first two would pass `--colour WHEN` or `-c` alone.

    Only a bar between a one-letter short flag and a long flag is read as a spelling.
    """
    doc = f"Usage: tool [options]\n\nOptions:\n  {entry}  Something.\n  -q  Quiet.\n"
    task = docopter.transform(*docopter.parse(doc))
    assert [arg.option_flag for arg in task["cli_args"]] == ["-q"]
    assert "names no single flag" in capsys.readouterr().err


def test_an_ellipsis_after_a_flag_marks_a_count_not_a_spelling():
    """clap writes a flag counted each time it is passed as `--verbose...`."""
    doc = "Usage: tool [options]\n\nOptions:\n  -v, --verbose...  More output.\n"
    task = docopter.transform(*docopter.parse(doc))
    [arg] = task["cli_args"]
    assert (arg.name, arg.option_flag, arg.option_has_value) == (
        "verbose",
        "--verbose",
        False,
    )


def test_a_flag_the_synopsis_spells_otherwise_is_passed_once(capsys):
    """git's synopsis names `--dry-run`, and its options block `--[no-]dry-run`.

    The synopsis's option has no entry and so no description; the documented one
    is kept, and the other left out with a warning.
    """
    doc = "Usage: tool [--dry-run] <in>\n\nOptions:\n  --[no-]dry-run  Show only.\n"
    task = docopter.transform(*docopter.parse(doc))
    assert [(arg.option_flag, arg.doc) for arg in task["cli_args"][1:]] == [
        ("--dry-run", "Show only.")
    ]
    assert "another option also passes" in capsys.readouterr().err


def test_an_entry_written_twice_alike_is_passed_once(capsys):
    """The second of two identical entries is left out with a warning.

    docopt compares options by their text, so only identity tells the two apart.
    """
    doc = "Usage: tool [options]\n\nOptions:\n  -v  Verbose.\n  -v  Verbose.\n"
    task = docopter.transform(*docopter.parse(doc))
    assert [arg.option_flag for arg in task["cli_args"]] == ["-v"]
    assert "another option also passes" in capsys.readouterr().err


def test_among_entries_passing_one_flag_the_first_described_wins():
    """gcc documents `-fcf-protection=<value>` before the described bare switch.

    An entry with no description is often prose read as an entry.
    """
    doc = (
        "Usage: tool [options]\n\nOptions:\n"
        "  -x VALUE\n"
        "  -x  Turn it on.\n"
        "  -x  Turn it on again.\n"
    )
    task = docopter.transform(*docopter.parse(doc))
    assert [(arg.option_has_value, arg.doc) for arg in task["cli_args"]] == [
        (False, "Turn it on.")
    ]


@pytest.mark.parametrize(
    "entries, expected",
    [
        # clang reads `-flto full` as an input file, so only the switch is passable.
        (["-flto=<value>  Set the mode.", "-flto  Enable it."], (False, "Enable it.")),
        (["-flto  Enable it.", "-flto=<value>  Set the mode."], (False, "Enable it.")),
        # Both take a value; the declaration passing it after a space wins.
        (
            ["-mllvm=<arg>  An alias.", "-mllvm <value>  Forward it."],
            (True, "Forward it."),
        ),
    ],
)
def test_a_value_attached_by_an_equals_sign_loses_to_another_form(entries, expected):
    """gcc documents `-flto=<value>` before `-flto`, and a task passes a value spaced."""
    doc = "Usage: cc [options]\n\nOptions:\n" + "".join(f"  {e}\n" for e in entries)
    task = docopter.transform(*docopter.parse(doc))
    assert [(arg.option_has_value, arg.doc) for arg in task["cli_args"]] == [expected]


def test_flags_that_differ_stay_apart_even_where_their_names_coincide(capsys):
    """torchrun documents both `--log-dir` and `--log_dir`; the writer numbers them."""
    doc = "Usage: tool [options]\n\nOptions:\n  --log-dir D  Logs.\n  --log_dir D  Logs.\n"
    task = docopter.transform(*docopter.parse(doc))
    assert [arg.option_flag for arg in task["cli_args"]] == ["--log-dir", "--log_dir"]
    assert capsys.readouterr().err == ""


def descriptions(doc):
    """Each parsed option's ``(short, long)`` flags and description."""
    options = docopter.parse(doc).options
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
    options = docopter.parse(doc).options
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
    options = docopter.parse(doc).options
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


def test_a_tab_indented_options_block_is_read():
    """bedtools indents each option with a tab and ends its flags with another.

    `-sorted` ends one column before a tab stop, where a tab expanded as a terminal
    shows it leaves a single space, and `Option.parse` would then read the words
    after it as the flag's value, `-k2` among them.
    """
    doc = (
        "Usage:   tool [OPTIONS]\n\nOptions: \n"
        "\t-wa\tWrite the original entry in A.\n\n"
        '\t-sorted\tUse the "chromsweep" algorithm (-k1,1 -k2,2n).\n'
        "\t\t- Requires sorted input.\n\n"
        "\t-f\tMinimum overlap.\n"
    )
    assert options_of(doc) == [
        ("-wa", None, 0, False),
        ("-sorted", None, 0, False),
        ("-f", None, 0, False),
    ]
    assert descriptions(doc)[("-sorted", None)] == (
        'Use the "chromsweep" algorithm (-k1,1 -k2,2n). - Requires sorted input.'
    )


def test_a_type_bullet_in_a_description_gives_the_flag_a_value(corpus_text):
    """bedtools writes no placeholder for `-f`, only `- FLOAT (e.g. 0.50)` below it."""
    doc = corpus_text("bedtools-intersect")
    task = docopter.transform(*docopter.parse(doc))
    args = {arg.name: arg for arg in task["cli_args"]}
    assert (args["f"].option_has_value, args["f"].value_type) == (
        True,
        ValueType.NUMBER,
    )
    assert args["f"].default_value is None
    assert (args["wa"].option_has_value, args["wa"].value_type) == (
        False,
        ValueType.BOOLEAN,
    )


@pytest.mark.parametrize(
    ("bullet", "expected"),
    [
        ("- (INTEGER)", ValueType.INTEGER),
        ("- (Integer) or (Float, e.g. 0.1) if used with -pct.", ValueType.NUMBER),
        ("- (STRING): can be + or -", ValueType.TEXT),
        ("- By default, overlaps are reported.", ValueType.BOOLEAN),
        ("- Integer overflow is trapped.", ValueType.BOOLEAN),
        ("Traps on integer overflow.", ValueType.BOOLEAN),
    ],
)
def test_only_a_type_bullet_decides_the_value_type(bullet, expected):
    """A float holds an integer; prose that mentions a type or a default is no bullet."""
    doc = f"Usage:   tool\n\nOptions:\n\t-b\tExtend each entry.\n\t\t{bullet}\n"
    [arg] = docopter.transform(*docopter.parse(doc))["cli_args"]
    assert arg.value_type == expected
    assert arg.option_has_value == (expected != ValueType.BOOLEAN)


def test_a_type_bullet_leaves_the_synopsis_reading_alone(capsys):
    """bedtools slop writes `[-b <int> or (-l and -r)]`.

    Given a value before the synopsis is read, `-r` would take the `)`.
    """
    doc = (
        "Usage:   tool [OPTIONS] -i <bed> [-b <int> or (-l and -r)]\n\nOptions:\n"
        "\t-l\tBases to subtract from the start.\n\t\t- (INTEGER)\n"
        "\t-r\tBases to add to the end.\n\t\t- (INTEGER)\n"
    )
    positionals = docopter.parse(doc).positionals
    assert capsys.readouterr().err == ""
    assert positionals
    assert options_of(doc)[:2] == [("-l", None, 0, False), ("-r", None, 0, False)]


@pytest.mark.parametrize(
    "flag, value, name",
    [("-g", "<genome>", "g"), ("--log-dir", "LOG_DIR", "log_dir")],
)
def test_an_undocumented_flag_before_a_placeholder_takes_it_as_its_value(
    flag, value, name, capsys
):
    """bedtools writes `-a <bed>` and documents no `-a`, so the placeholder was an input.

    argparse 3.13 writes `--log-dir, --log_dir LOG_DIR`, whose entry keeps only the
    last long spelling, so the synopsis's is undocumented too.
    """
    task = docopter.transform(*docopter.parse(f"Usage: tool {flag} {value} <in>\n"))
    assert capsys.readouterr().err == ""
    assert [
        (arg.name, arg.option_flag, arg.option_has_value) for arg in task["cli_args"]
    ] == [("in", "", False), (name, flag, True)]


@pytest.mark.parametrize(
    "synopsis, spellings",
    [
        # One occurrence is followed by a command word, which a value would take.
        ("[-x <n>] [-x word]", [["<n>"], ["word"]]),
        # `-pz` would give the token after it to `-z`.
        ("-z <x> -pz run", [["<x>"], ["run"]]),
        # A repeated option would lose the list.
        ("-i <file>...", [["<file>"]]),
        # `transform` leaves out a flag written in notation, and its value with it.
        ("--apilog: <path> <in>", [["<path>"], ["<in>"]]),
    ],
)
def test_a_synopsis_flag_takes_no_value_where_a_positional_would_be_lost(
    synopsis, spellings
):
    """Only a token that would otherwise be a positional, and stay one, is a value."""
    positionals = docopter.parse(f"Usage: tool {synopsis}\n").positionals
    assert [p.spellings for p in positionals] == spellings


@pytest.mark.parametrize(
    "entry, spellings, takes_value",
    [
        ("--[no-]foo  Enable it.", [["<x>"]], False),
        ("--[no-]foo <v>  Use it.", [], True),
    ],
)
def test_a_documented_twin_decides_whether_a_synopsis_flag_takes_a_value(
    entry, spellings, takes_value
):
    """git's synopsis names `--trailer <token>` and its entry `--[no-]trailer <t>`.

    The documented option is the one `transform` passes, so a placeholder its
    switch cannot take stays a positional.
    """
    doc = f"Usage: tool [--foo <x>]\n\nOptions:\n  {entry}\n"
    parsed = docopter.parse(doc)
    assert [p.spellings for p in parsed.positionals] == spellings
    [foo] = [arg for arg in docopter.transform(*parsed)["cli_args"] if arg.option_flag]
    assert (foo.option_flag, foo.option_has_value) == ("--foo", takes_value)


def test_synopsis_options_keep_the_order_the_synopsis_names_them():
    """`-a` gets its value before the synopsis is read, and still follows `-x`.

    `-b` is the value `-o` takes, so the tree never names it as an option.
    """
    doc = "Usage: tool [-x] [-a <in>] [--only] -o -b <y>\n\nOptions:\n  -o FILE  Out.\n"
    assert options_of(doc) == [
        ("-o", None, 1, None),
        ("-x", None, 0, False),
        ("-a", None, 1, None),
        (None, "--only", 0, False),
    ]


def test_a_synopsis_that_does_not_parse_is_read_as_without_synopsis_values(capsys):
    """The options are those the first reading registered before it failed."""
    assert options_of("Usage: tool -a <x> (\n") == [("-a", None, 0, False)]
    assert "unmatched '('" in capsys.readouterr().err


def test_tabs_advance_to_stops_eight_columns_apart():
    """R continues `--vanilla` on a line of three tabs, at its description column.

    Tabs counted as any fixed width, or with stops closer together, would put that
    line left of the column, where its leading flag starts an entry of its own.
    """
    doc = (
        "Usage: R [options]\n\nOptions:\n"
        "  --save                Do save workspace.\n"
        "  --vanilla\t\tCombine --no-save, --no-restore,\n"
        "\t\t\t--no-init-file and --no-environ\n"
        "  -q, --quiet           Don't print startup message\n"
    )
    assert descriptions(doc) == {
        (None, "--save"): "Do save workspace.",
        (None, "--vanilla"): (
            "Combine --no-save, --no-restore, --no-init-file and --no-environ"
        ),
        ("-q", "--quiet"): "Don't print startup message",
    }


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


def test_a_positional_takes_the_description_of_its_entry(help_text):
    """argparse lists `targets` under "positional arguments:" with its help.

    The option set is the one read without positional names.
    """
    doc = help_text("cnvkit-antitarget")
    before = options_of(doc)
    task = docopter.transform(*docopter.parse(doc))
    assert (
        task["cli_args"][0].doc == "BED or interval file listing the targeted regions."
    )
    assert options_of(doc) == before


def test_a_positional_entry_matches_any_spelling_and_keeps_the_others():
    """`<in.bam>|<in.sam>` is described by an entry naming either spelling."""
    doc = (
        "Usage: tool [-q] <in.bam>|<in.sam>\n\nArguments:\n"
        "  <in.sam>  Alignments\n\nOptions:\n  -q  Quiet.\n"
    )
    task = docopter.transform(*docopter.parse(doc))
    assert task["cli_args"][0].doc == "Alignments\nAlso accepts: <in.sam>"


def test_a_positional_entry_without_a_description_leaves_it_to_a_later_one():
    """The first entry with a description wins, as among a flag's declarations."""
    doc = "Usage: tool FILE\n\nArguments:\n  FILE\n\n  FILE  The input.\n"
    positionals = docopter.parse(doc).positionals
    assert positionals[0].description == "The input."


def test_prose_opening_with_a_positionals_name_is_no_entry():
    """Indented as descriptions are, a line naming the positional is prose."""
    doc = (
        "Usage: tool [-q] FILE\n\nOptions:\n"
        "  -q          Quiet; implies that\n"
        "              FILE  is read silently.\n"
    )
    positionals = docopter.parse(doc).positionals
    assert positionals[0].description == ""


@pytest.mark.parametrize(
    "line, description",
    [
        ("FORMAT controls the output.  Sequences are:", ""),
        ("FORMAT ...      The output format.", "The output format."),
    ],
)
def test_a_sentence_opening_with_a_positionals_name_is_no_entry(line, description):
    """GNU date writes prose at column 0 that opens with its `FORMAT` placeholder.

    One space and a letter after the name open a sentence; dos2unix's entry
    `file ...` writes notation after one space and is still an entry.
    """
    doc = f"Usage: date [+FORMAT]\n\n{line}\n"
    assert docopter.parse(doc).positionals[0].description == description


@pytest.mark.parametrize(
    "synopsis", ["tool {check,register,upload}", "tool [-v] {run,r,list} ..."]
)
def test_a_choice_among_literal_words_is_no_input(synopsis, capsys):
    """argparse writes a subparsers action, or `choices=` without a metavar, so.

    The slot names no argument, and its identifier would concatenate the choices.
    """
    assert docopter.parse(f"Usage: {synopsis}\n").positionals == []
    assert "a choice among literal words" in capsys.readouterr().err


def test_a_slot_another_alternative_names_with_a_placeholder_is_kept(capsys):
    """Only a slot that every alternative writes as a choice group is left out."""
    positionals = docopter.parse("Usage: tool {a,b}\n       tool <y>\n").positionals
    assert [p.spellings for p in positionals] == [["{a,b}", "<y>"]]
    assert capsys.readouterr().err == ""


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
