"""Reading a tool's interface out of its help text."""
import pytest

from doc2wrapper import docopter

# A usage line whose `--tag` is declared as taking a value but written bare.  The
# option parser then consumes the closing bracket as that value and bracket depth
# desyncs, so parse_pattern raises.  Measured on a corpus of real help texts, this
# is the single most common way a present-and-plausible usage line fails to parse.
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

    A partial task the user finishes by hand beats a traceback: roughly one real
    help text in ten hits this, and the options are still perfectly recoverable.
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
