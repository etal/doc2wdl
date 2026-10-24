"""End-to-end generation, validated by the target languages' own checkers."""

import argparse
import sys

import pytest

from doc2wrapper import argparser, docopter, nfgen, wdlgen
from doc2wrapper.tasktree import Argument, ValueType

EXAMPLES = ["cnvkit-antitarget", "samtools-view"]


def two_subcommand_parser():
    """An ArgumentParser shaped like the one the argparse path has never handled.

    Deliberately narrow.  It exercises a parser with two subcommands -- so the
    document carries two tasks -- and a multi-line description, so the usage prose
    cannot be commented by accident.
    """
    parser = argparse.ArgumentParser(description="Parent tool.", epilog="Epilog.")
    subparsers = parser.add_subparsers()
    alpha = subparsers.add_parser("alpha", description="Alpha subcommand.")
    alpha.add_argument("infile")
    alpha.add_argument("-r", "--reference")
    beta = subparsers.add_parser(
        "beta", description="Beta subcommand.\nSecond description line."
    )
    beta.add_argument("sample")
    beta.add_argument("-n", "--count", type=int, default=5)
    return parser


def command_block(wdl):
    """The lines between `command <<<` and `>>>` of a generated WDL task."""
    body = wdl.split("command <<<", 1)[1].split(">>>", 1)[0]
    return [line.strip() for line in body.splitlines() if line.strip()]


def script_block(nextflow):
    """The lines of a generated Nextflow process's triple-quoted script."""
    body = nextflow.split('"""', 2)[1]
    return [line.strip() for line in body.splitlines() if line.strip()]


WRITERS = [
    pytest.param(wdlgen.render, command_block, id="wdl"),
    pytest.param(nfgen.render, script_block, id="nextflow"),
]


@pytest.mark.parametrize("example", EXAMPLES)
def test_generated_wdl_is_accepted_by_miniwdl(help_text, check_wdl, example):
    check_wdl(wdlgen.render([docopter.transform(*docopter.parse(help_text(example)))]))


def test_a_multi_task_document_declares_its_version_once(check_wdl):
    """`version` describes the document, so N tasks must not mean N version lines.

    The writer is what gets called here, not the CLI.  Stripping repeated version
    lines downstream -- which is what the superseded `# TODO` in cli.py proposed --
    would leave this failing, which is the point: a writer whose output is not a
    document invites the next caller to concatenate two of them.
    """
    wdl = wdlgen.render(argparser.unpack_tasks(two_subcommand_parser(), "demotool"))
    assert wdl.splitlines()[0] == "version 1.1"
    assert wdl.count("version 1.1") == 1
    assert wdl.count("task ") == 2
    check_wdl(wdl)


def test_argparse_prose_is_commented_by_the_writer(check_wdl):
    """An ArgumentParser's description is prose, and prose at file scope is a syntax
    error.

    The docopt reader used to escape this by prefixing `# ` itself; the argparse
    reader never did, so this is the case that proves the commenting moved to the
    layer that serves both.  Asserting on every line matters: a template
    interpolating the whole block at once comments only the first, which is what the
    Nextflow template did from the day it was written.
    """
    wdl = wdlgen.render(argparser.unpack_tasks(two_subcommand_parser(), "demotool"))
    prose = ("Alpha subcommand.", "Beta subcommand.", "Second description line.")
    for line in wdl.splitlines():
        assert not any(line.startswith(text) for text in prose), (
            f"usage prose reached WDL as a bare statement: {line!r}"
        )
    for text in prose:
        assert f"# {text}" in wdl, (
            f"usage prose was dropped rather than commented: {text!r}"
        )
    check_wdl(wdl)


@pytest.mark.parametrize("render, block", WRITERS)
@pytest.mark.parametrize("example", EXAMPLES)
def test_every_declared_input_reaches_the_command_line(
    help_text, example, render, block
):
    """The assertion no checker can make, for both target languages.

    An input that is declared but never interpolated produces a wrapper that parses,
    runs, and quietly ignores the argument -- which is what both templates did to
    every positional. This asserts on text, so it needs neither checker installed.
    """
    task = docopter.transform(*docopter.parse(help_text(example)))
    lines = block(render([task]))
    for arg in task["cli_args"]:
        assert any(arg.name in line for line in lines), (
            f"{arg.name} is declared as an input but never used in the command"
        )


def test_positional_is_interpolated_not_quoted(help_text):
    """`$name` is eleven literal characters; WDL interpolation is `~{name}`.

    Scoped to the command block on purpose: the usage text is embedded as comments,
    so a help text containing a literal `$` would fail a whole-document scan for a
    reason that has nothing to do with interpolation.
    """
    wdl = wdlgen.render(
        [docopter.transform(*docopter.parse(help_text("samtools-view")))]
    )
    assert "$" not in "".join(command_block(wdl))
    assert "~{ in_bam }" in command_block(wdl)[1]


def test_repeated_positional_is_joined_as_an_array(help_text):
    wdl = wdlgen.render(
        [docopter.transform(*docopter.parse(help_text("samtools-view")))]
    )
    assert "Array[File] region = []" in wdl
    assert '~{sep(" ", region)}' in wdl


def one_argument_task(name, usage="demo"):
    """Template keywords for a task taking one required positional argument."""
    return dict(
        title="Demo",
        usage=usage,
        cli_prefix="demo",
        cli_args=[Argument(name=name, value_type=ValueType.TEXT, is_required=True)],
        has_output_file=False,
    )


def test_reserved_words_are_renamed_per_target_language(check_wdl):
    """Renaming lives in the writer because the reserved set differs per language."""
    task = one_argument_task("scatter")
    wdl = wdlgen.render([task])
    assert "String scatter_" in wdl
    check_wdl(wdl)
    # The Nextflow writer applies its own list, so the WDL keyword is left alone.
    assert "val scatter\n" in nfgen.render([task])


@pytest.mark.parametrize("keyword", ["new", "include"])
def test_nextflow_renames_reserved_keywords(check_nextflow, keyword):
    """`nextflow lint` fails on a Groovy or Nextflow keyword as an input name."""
    nextflow = nfgen.render([one_argument_task(keyword)])
    assert f"val {keyword}_\n" in nextflow
    check_nextflow(nextflow)


def test_nextflow_renames_in_although_it_lints_as_the_last_input(check_nextflow):
    """`in` is a binary operator, so it reads the next declaration as its operand.

    As the last input it lints and runs, which is why only an input following it
    shows the defect: `val in` then `val other` is a syntax error.
    """
    task = one_argument_task("in")
    task["cli_args"].append(Argument(name="other", value_type=ValueType.TEXT))
    nextflow = nfgen.render([task])
    assert "val in_\n" in nextflow
    check_nextflow(nextflow)


def test_wdl_keywords_are_renamed_but_their_flags_are_not(check_wdl, wdl_command):
    """`--input`, `--output` and `--command` are ordinary flags and WDL keywords.

    Only the input's name takes the suffix; the command line still spells the
    tool's own flag.  `in` and `version` are reserved by the specification although
    miniwdl accepts them.
    """
    parser = argparse.ArgumentParser(prog="tool", add_help=False)
    for flag in ("--input", "--output", "--command", "--task", "--in", "--version"):
        parser.add_argument(flag)
    wdl = wdlgen.render(argparser.unpack_tasks(parser, "tool"))
    check_wdl(wdl)
    for name in ("input_", "output_", "command_", "task_", "in_", "version_"):
        assert f"String? {name}\n" in wdl
    assert (
        wdl_command(wdl, output_="o.txt", task_="t", version_="2")
        == "tool --output o.txt --task t --version 2"
    )


def test_a_title_is_renamed_against_each_languages_own_list(check_wdl, check_nextflow):
    """A program name can title-case onto a keyword, as `file` becomes `File`.

    WDL forbids the same words for a task as for an input, while Nextflow accepts a
    process named `File` and cannot call one named `Session`.
    """
    file_task = docopter.transform(*docopter.parse("Usage: file [-v]\n"))
    wdl = wdlgen.render([file_task])
    assert "task File_ {" in wdl
    check_wdl(wdl)
    nextflow = nfgen.render([file_task])
    assert "process File {" in nextflow
    check_nextflow(nextflow)
    session_task = docopter.transform(*docopter.parse("Usage: session <x>\n"))
    assert "process Session_ {" in nfgen.render([session_task])


def test_names_are_unique_after_the_keyword_rename(check_wdl, check_nextflow):
    """`scatter` becomes `scatter_` in WDL, beside an argument already named that.

    Uniqueness has to be decided over the final names, so a duplicate the rename
    itself creates is caught, while Nextflow, which keeps `scatter`, renames nothing.
    """
    task = one_argument_task("scatter")
    task["cli_args"].append(Argument(name="scatter_", value_type=ValueType.TEXT))
    wdl = wdlgen.render([task])
    assert "String scatter_\n" in wdl and "String? scatter__2\n" in wdl
    check_wdl(wdl)
    nextflow = nfgen.render([task])
    assert "val scatter\n" in nextflow and "val scatter_\n" in nextflow
    check_nextflow(nextflow)


def test_an_input_may_not_take_a_name_the_template_declares(check_wdl, check_nextflow):
    """The WDL template declares `output_file`; Nextflow's `emit:` labels do not count."""
    parser = argparse.ArgumentParser(prog="tool", add_help=False)
    parser.add_argument("--output-file")
    wdl = wdlgen.render(argparser.unpack_tasks(parser, "tool"))
    assert "String? output_file_\n" in wdl
    check_wdl(wdl)
    nextflow = nfgen.render(argparser.unpack_tasks(parser, "tool"))
    assert "val output_file\n" in nextflow
    check_nextflow(nextflow)


def test_the_output_option_keeps_the_name_the_template_reads(check_wdl, wdl_command):
    """`--output` owns `output_file_name` even when a flag spelling it comes first.

    Were the first holder of the name to keep it, the output section would name the
    file `--output-file-name` received, and every checker would accept that.
    """
    help_text = (
        "Usage: tool [options]\n\n"
        "Options:\n"
        "  --output-file-name NAME  Name to record.\n"
        "  --output FILE            Where to write.\n"
    )
    wdl = wdlgen.render([docopter.transform(*docopter.parse(help_text))])
    check_wdl(wdl)
    assert wdl_command(wdl, output_file_name="o.txt") == "tool --output o.txt"
    assert wdl_command(wdl, output_file_name_="n") == "tool --output-file-name n"


def test_subcommands_differing_in_case_or_punctuation_name_distinct_tasks(
    check_wdl, check_nextflow
):
    """Capitalization survives into the title, and a remaining duplicate is numbered."""
    parser = argparse.ArgumentParser(prog="tool")
    subparsers = parser.add_subparsers()
    for name in ("runAll", "runall", "run-all"):
        subparsers.add_parser(name).add_argument("infile")
    wdl = wdlgen.render(argparser.unpack_tasks(parser, "tool"))
    titles = [line for line in wdl.splitlines() if line.startswith("task ")]
    assert titles == ["task ToolRunAll {", "task ToolRunall {", "task ToolRunAll_2 {"]
    check_wdl(wdl)
    check_nextflow(nfgen.render(argparser.unpack_tasks(parser, "tool")))


@pytest.mark.parametrize(
    "prog, title",
    [
        ("9x", "Task9X"),
        ("_x", "Task_X"),
        ("a.b", "A_B"),
        ("/usr/bin/tool", "UsrBinTool"),
        ("-", "Task"),
        # A letter `legal_name` drops leaves the next one at a word boundary, so the
        # title is not the Groovy keyword `def`.
        ("ñdef", "Def"),
    ],
)
def test_an_argparse_title_is_a_legal_identifier(
    check_wdl, check_nextflow, prog, title
):
    """The argparse reader names a task by the rule the help-text reader uses."""
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("infile")
    (task,) = argparser.unpack_tasks(parser, prog)
    help_task = docopter.transform(*docopter.parse(f"Usage: {prog} <infile>\n"))
    assert task["title"] == help_task["title"] == title
    check_wdl(wdlgen.render([task]))
    check_nextflow(nfgen.render([task]))


def test_argparse_destinations_become_legal_identifiers(check_wdl, wdl_command):
    """argparse accepts a `dest` no target language accepts as a name."""
    parser = argparse.ArgumentParser(prog="tool", add_help=False)
    parser.add_argument("in-file")
    parser.add_argument("--fast", dest="a.b", action="store_true")
    wdl = wdlgen.render(argparser.unpack_tasks(parser, "tool"))
    check_wdl(wdl)
    assert wdl_command(wdl, in_file="x", a_b=True) == "tool x --fast"


def test_a_switch_split_over_two_actions_renders_as_one(check_wdl, wdl_command):
    """`--x` and `--no-x` storing one `dest` are one input, not two named alike."""
    parser = argparse.ArgumentParser(prog="tool", add_help=False)
    parser.add_argument("--squash", action="store_true", default=True)
    parser.add_argument("--no-squash", dest="squash", action="store_false")
    wdl = wdlgen.render(argparser.unpack_tasks(parser, "tool"))
    check_wdl(wdl)
    assert wdl_command(wdl) == "tool --squash"
    assert wdl_command(wdl, squash=False) == "tool --no-squash"


def test_flags_sharing_a_destination_are_named_by_flag(check_wdl, check_nextflow):
    """Each `append_const` flag is passed on its own, so each needs its own input."""
    parser = argparse.ArgumentParser(prog="tool", add_help=False)
    for flag in ("--mean", "--median", "--t-test"):
        parser.add_argument(flag, dest="stats", action="append_const", const=flag)
    wdl = wdlgen.render(argparser.unpack_tasks(parser, "tool"))
    assert command_block(wdl)[1:] == [
        '~{if mean then "--mean" else ""} \\',
        '~{if median then "--median" else ""} \\',
        '~{if t_test then "--t-test" else ""} \\',
    ]
    check_wdl(wdl)
    check_nextflow(nfgen.render(argparser.unpack_tasks(parser, "tool")))


def test_a_switch_named_by_its_flag_passes_that_flag_when_true(wdl_command):
    """A name taken from a flag must mean that flag, whichever value it stores.

    `-q` storing False into `verbosity` is an input `q`, and `q = true` asks for
    quiet; a `BooleanOptionalAction` is named by its affirmative spelling.  No
    flag passes by default: argparse fills `verbosity` from the first action's
    default, so the later `default=True` never reaches the tool.
    """
    parser = argparse.ArgumentParser(prog="tool", add_help=False)
    parser.add_argument("-v", dest="verbosity", action="count", default=0)
    parser.add_argument("-q", dest="verbosity", action="store_false")
    parser.add_argument(
        "--color", dest="verbosity", action=argparse.BooleanOptionalAction, default=True
    )
    parser.add_argument("--loud", dest="verbosity", action="store_true", default=True)
    wdl = wdlgen.render(argparser.unpack_tasks(parser, "tool"))
    assert wdl_command(wdl) == "tool"
    assert wdl_command(wdl, q=True) == "tool -q"
    assert wdl_command(wdl, v=True, color=False) == "tool -v --no-color"


def switch_parser():
    """One option of each valueless kind argparse offers, and nothing else."""
    parser = argparse.ArgumentParser(prog="tool", add_help=False)
    parser.add_argument("--loud", action="store_true")
    parser.add_argument("--no-check", action="store_false", dest="check")
    parser.add_argument("--color", action=argparse.BooleanOptionalAction)
    parser.add_argument("-v", "--verbose", action="count", default=2)
    parser.add_argument("--fast", action="store_const", const="fast", dest="mode")
    parser.add_argument("--extra", action="append_const", const="x", dest="extras")
    return parser


@pytest.fixture
def switch_wdl(check_wdl):
    """WDL for `switch_parser`, already accepted by the checker."""
    wdl = wdlgen.render(argparser.unpack_tasks(switch_parser(), "tool"))
    check_wdl(wdl)
    return wdl


def test_paired_flag_renders_one_spelling_per_value(switch_wdl, wdl_command):
    """`BooleanOptionalAction` names both states; neither is emitted when unset.

    Its last option string is the negation, so a reader taking the last spelling
    for the flag, as it does for every other option, renders `--no-color` for true.
    """
    assert "Boolean? color\n" in switch_wdl
    assert wdl_command(switch_wdl) == "tool"
    assert wdl_command(switch_wdl, color=True) == "tool --color"
    assert wdl_command(switch_wdl, color=False) == "tool --no-color"


def test_a_switch_follows_its_value_rather_than_its_presence(switch_wdl, wdl_command):
    """Every switch is off by default and on only when its input says so.

    A `defined()` test cannot tell true from false, and an input with a default is
    always defined, so the previous rendering passed every bare flag regardless.
    `store_false` fires on false; `count` and `store_const` become plain switches.
    """
    assert "Boolean loud = false\n" in switch_wdl
    assert "Boolean check = true\n" in switch_wdl
    assert "Boolean verbose = false\n" in switch_wdl
    assert wdl_command(switch_wdl) == "tool"
    assert wdl_command(switch_wdl, loud=True) == "tool --loud"
    assert wdl_command(switch_wdl, check=False) == "tool --no-check"
    assert wdl_command(switch_wdl, verbose=True, mode=True) == "tool --verbose --fast"


def test_nextflow_switches_render_one_spelling_per_value(check_nextflow):
    """A switch naming its false state stays silent for `''`, the unset value.

    Groovy truth reads `''` as false, so a plain ternary renders the negation, or a
    `store_false` flag, for a switch the caller meant to leave alone.
    """
    nextflow = nfgen.render(argparser.unpack_tasks(switch_parser(), "tool"))
    check_nextflow(nextflow)
    script = script_block(nextflow)
    assert "${ loud ? '--loud' : '' } \\" in script
    assert "${ check == true ? '' : check == false ? '--no-check' : '' } \\" in script
    assert (
        "${ color == true ? '--color' : color == false ? '--no-color' : '' } \\"
        in script
    )


def test_nextflow_command_line_follows_the_inputs(check_nextflow, nextflow_commands):
    """Unset inputs leave no trace, and set ones render even when Groovy reads them
    as false.

    The caller spells an unset input `''` or `[]`, since passing null fails the run.
    Testing `x ?` instead of `x != ''` drops `--num 0`, and the previous
    `"--num " + x` rendered an unset option as `--num null`.  Each multi-value form
    is checked against its argparse action, as for WDL, and `--req` takes the arm
    for a required value option.  `-v` guards the `append` arm: a closure parameter
    named `v` would collide with an input named `v`, which is why that arm joins
    instead, and an input named `task` would render the implicit task map unless
    renamed.
    """
    parser = argparse.ArgumentParser(prog="tool", add_help=False)
    parser.add_argument("inputs", nargs="*")
    parser.add_argument("--num", type=int)
    parser.add_argument("--color", action=argparse.BooleanOptionalAction)
    parser.add_argument("--no-check", action="store_false", dest="check")
    parser.add_argument("-v", action="append")
    parser.add_argument("--files", nargs="+")
    parser.add_argument("--pair", nargs=2, required=True)
    parser.add_argument("--req", required=True)
    parser.add_argument("--task")
    nextflow = nfgen.render(argparser.unpack_tasks(parser, "tool"))
    check_nextflow(nextflow)
    assert nextflow_commands(
        nextflow,
        [[], "", "", "", [], [], ["p", "q"], "r", ""],
        [["x", "y"], 0, False, False, ["a", "b"], ["f", "g"], ["p", "q"], "s", "t"],
        [["x"], 3, True, True, ["a"], ["f"], ["p", "q"], "r", ""],
    ) == {
        "tool --pair p q --req r",
        "tool x y --num 0 --no-color --no-check -v a -v b --files f g --pair p q"
        " --req s --task t",
        "tool x --num 3 --color -v a --files f --pair p q --req r",
    }


def test_nextflow_comments_survive_comment_syntax_in_help_text(check_nextflow):
    """Captured help text is commented per line, so it cannot close its comment.

    A `/* ... */` block ended at the first `*/` in the text and left the rest of
    it to be parsed as Nextflow.
    """
    usage = 'demo FILE\nsee */path/glob for details, or """ and ${x}'
    check_nextflow(nfgen.render([one_argument_task("infile", usage)]))


def test_a_process_without_arguments_declares_no_inputs(check_nextflow):
    """An empty `input:` section is a syntax error, unlike WDL's empty `input {}`.

    Help text offering nothing but `--help` reaches the writer this way.
    """
    doc = "Usage: tool [-h]\n\nOptions:\n  -h, --help  Show this help.\n"
    task = docopter.transform(*docopter.parse(doc))
    assert task["cli_args"] == []
    nextflow = nfgen.render([task])
    assert "process Tool {" in nextflow
    assert "input:" not in nextflow
    check_nextflow(nextflow)


def test_multi_value_arguments_are_arrays_spelled_per_action(check_wdl, wdl_command):
    """`append` repeats its flag per value; `nargs` takes one flag for the list.

    Exchanging the two still type checks.  The first form given to a `nargs`
    option keeps only the last value, and the second given to `append` passes the
    rest as stray positionals.
    """
    parser = argparse.ArgumentParser(prog="tool", add_help=False)
    parser.add_argument("inputs", nargs="*")
    parser.add_argument("--tag", action="append")
    parser.add_argument("--files", nargs="+")
    parser.add_argument("--pair", nargs=2, required=True)
    wdl = wdlgen.render(argparser.unpack_tasks(parser, "tool"))
    check_wdl(wdl)
    assert "Array[File] inputs = []\n" in wdl
    assert "Array[String] tag = []\n" in wdl
    assert "Array[String]+ pair\n" in wdl
    assert wdl_command(wdl, pair=["p", "q"]) == "tool --pair p q"
    assert (
        wdl_command(
            wdl, inputs=["x", "y"], tag=["a", "b"], files=["f", "g"], pair=["p"]
        )
        == "tool x y --tag a --tag b --files f g --pair p"
    )


def test_python_defaults_are_spelled_as_wdl_literals(check_wdl, wdl_command):
    """Defaults reach the model as Python values, and the writer spells them."""
    parser = argparse.ArgumentParser(prog="tool", add_help=False)
    parser.add_argument("--output-dir", default=".")
    parser.add_argument("--ratio", type=float, default=0.5)
    parser.add_argument("--chroms", nargs="+", default=["chr1", "chr2"])
    # `append` adds to its default, so passing the default again would double it.
    parser.add_argument("--tag", action="append", default=["x"])
    parser.add_argument("--stream", default=sys.stdin)
    parser.add_argument("--dims", default=[3, 4])
    parser.add_argument("paths", nargs="*", default=".")
    wdl = wdlgen.render(argparser.unpack_tasks(parser, "tool"))
    check_wdl(wdl)
    assert 'String output_dir = "."\n' in wdl
    assert 'String ratio = "0.5"\n' in wdl
    assert 'Array[String] chroms = ["chr1", "chr2"]\n' in wdl
    assert "Array[String] tag = []\n" in wdl
    # No value type holds a file object, so the input is left optional.
    assert "String? stream\n" in wdl
    # A default must take the declaration's shape; one that cannot is dropped.
    assert "String? dims\n" in wdl
    assert 'Array[String] paths = ["."]\n' in wdl
    # A number is declared as text, so the caller's own spelling reaches the tool.
    assert wdl_command(wdl) == "tool --output-dir . --ratio 0.5 --chroms chr1 chr2 ."


def test_help_text_defaults_reach_the_writer_as_python_values(check_wdl):
    """The docopt reader infers a default's type and leaves its spelling to WDL."""
    doc = """Usage: tool [options]

Options:
  -n N, --num N      Count [default: 5]
  -r R, --ratio R    Ratio [default: 0.5]
  -s S, --sep S      Separator [default: tab]
"""
    task = docopter.transform(*docopter.parse(doc))
    assert [arg.default_value for arg in task["cli_args"]] == [5, 0.5, "tab"]
    wdl = wdlgen.render([task])
    check_wdl(wdl)
    assert "Int num = 5\n" in wdl
    assert 'String ratio = "0.5"\n' in wdl
    assert 'String sep = "tab"\n' in wdl


# A literal backslash before `n` or `b`, which WDL would read as an escape, and each
# placeholder opener, which WDL reads differently in its two string contexts.
AWKWARD_TEXT = 'say "hi" C:\\new\\b ~{x} ${y}\nnext\tline'


def test_text_survives_as_a_wdl_string_literal(check_wdl, wdl_task):
    """Help text and string defaults read back from the document exactly as given.

    `~{` in a default is an interpolation and in `parameter_meta` a syntax error, so
    an escape that suits one context breaks the other.
    """
    import WDL

    parser = argparse.ArgumentParser(prog="tool", add_help=False)
    parser.add_argument("--label", default=AWKWARD_TEXT, help=AWKWARD_TEXT)
    wdl = wdlgen.render(argparser.unpack_tasks(parser, "tool"))
    check_wdl(wdl)
    task = wdl_task(wdl)
    assert task.parameter_meta["label"] == AWKWARD_TEXT
    (label,) = task.inputs
    default = label.expr.eval(WDL.Env.Bindings(), WDL.StdLib.Base("1.1"))
    assert default.value == AWKWARD_TEXT


@pytest.mark.parametrize("example", EXAMPLES)
def test_generated_nextflow_is_accepted_by_nextflow_lint(
    help_text, check_nextflow, example
):
    """Both fixtures take `--output`, so each process also declares that file.

    Without `optional: true`, an unset name makes that output emit an empty list in
    place of a file; with it, the output emits nothing and `stdout` carries the
    result.
    """
    task = docopter.transform(*docopter.parse(help_text(example)))
    assert task["has_output_file"]
    nextflow = nfgen.render([task])
    check_nextflow(nextflow)
    assert (
        'path "${output_file_name}", emit: output_file, optional: true\n'
        "    stdout emit: stdout\n"
    ) in nextflow


def test_a_number_reaches_the_command_line_exactly(check_wdl, wdl_command):
    """WDL prints a Float with six decimals, so a number is declared as text.

    `Float tiny = 1e-10` is spelled correctly and interpolates as `0.000000`; the
    specification mandates that form, and WDL 1.1 has no formatting function to
    undo it, so the value must not be a Float by the time it is interpolated.
    """
    parser = argparse.ArgumentParser(prog="tool", add_help=False)
    parser.add_argument("--tiny", type=float, default=1e-10)
    parser.add_argument("--whole", type=float, default=10)
    # A six-significant-digit format would preserve every value but this one.
    parser.add_argument("--pair", type=float, nargs=2, default=[1, 266.6666666666667])
    wdl = wdlgen.render(argparser.unpack_tasks(parser, "tool"))
    check_wdl(wdl)
    assert 'String tiny = "1e-10"\n' in wdl
    assert 'String whole = "10"\n' in wdl
    assert 'Array[String] pair = ["1", "266.6666666666667"]\n' in wdl
    assert wdl_command(wdl) == "tool --tiny 1e-10 --whole 10 --pair 1 266.6666666666667"
    assert (
        wdl_command(wdl, tiny="2.5e-07")
        == "tool --tiny 2.5e-07 --whole 10 --pair 1 266.6666666666667"
    )


def test_a_default_the_declaration_cannot_hold_is_dropped(check_wdl, wdl_command):
    """`type=int, default=266.6666666666667` is argparse's, and WDL has no such Int.

    argparse applies `type` to a string default and to nothing else, so the tool
    itself receives the float when the flag is absent.  An input left unset passes
    no flag and reproduces exactly that, where `Int avg_size = 266.6666666666667`
    is a document no engine accepts.
    """
    parser = argparse.ArgumentParser(prog="tool", add_help=False)
    parser.add_argument("--avg-size", type=int, default=266.6666666666667)
    parser.add_argument("--count", type=int, default="5")
    wdl = wdlgen.render(argparser.unpack_tasks(parser, "tool"))
    check_wdl(wdl)
    assert "Int? avg_size\n" in wdl
    assert "Int count = 5\n" in wdl
    assert wdl_command(wdl) == "tool --count 5"
    assert wdl_command(wdl, avg_size=267) == "tool --avg-size 267 --count 5"


def test_a_help_text_switch_is_a_boolean(help_text, check_wdl, wdl_command):
    """A flag taking no value is a Boolean, not a string the caller fills in.

    `String? bam` with a `defined()` test asked the caller for text to mean yes,
    and could not express no; the arm that rendered it is gone from the template.
    """
    wdl = wdlgen.render(
        [docopter.transform(*docopter.parse(help_text("samtools-view")))]
    )
    check_wdl(wdl)
    assert "Boolean bam = false\n" in wdl
    assert "defined(bam)" not in wdl
    assert "--bam" not in wdl_command(wdl, in_bam="x.bam")
    assert "--bam" in wdl_command(wdl, in_bam="x.bam", bam=True)
