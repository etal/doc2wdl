"""End-to-end generation, validated by the target languages' own checkers."""

import argparse
import sys

import pytest

from doc2wrapper import argparser, docopter, nfgen, wdlgen
from doc2wrapper.tasktree import Argument

EXAMPLES = ["cnvkit-antitarget", "samtools-view"]


def two_subcommand_parser():
    """An ArgumentParser shaped like the one the argparse path has never handled.

    Deliberately narrow.  Its argument names avoid WDL keywords, because the
    reserved-word list is a separate open defect: a fixture that tripped it would
    make this test fail for reasons it is not about.  What it does exercise is
    a parser with two subcommands -- so the document carries two tasks -- and a
    multi-line description, so the usage prose cannot be commented by accident.
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
        cli_args=[Argument(name=name, wdl_type="String", is_required=True)],
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


def test_nextflow_renames_groovy_keywords(check_nextflow):
    """`nextflow lint` rejects a Groovy keyword as an input name."""
    nextflow = nfgen.render([one_argument_task("new")])
    assert "val new_\n" in nextflow
    check_nextflow(nextflow)


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
    assert "Array[String] inputs = []\n" in wdl
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
    assert "Float ratio = 0.5\n" in wdl
    assert 'Array[String] chroms = ["chr1", "chr2"]\n' in wdl
    assert "Array[String] tag = []\n" in wdl
    # WDL has no literal for a file object, so the input is left optional.
    assert "String? stream\n" in wdl
    # A default must take the declaration's shape; one that cannot is dropped.
    assert "String? dims\n" in wdl
    assert 'Array[String] paths = ["."]\n' in wdl
    # WDL's own string form of a Float has six decimal places.
    assert wdl_command(wdl) == (
        "tool --output-dir . --ratio 0.500000 --chroms chr1 chr2 ."
    )


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
    assert "Float ratio = 0.5\n" in wdl
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
