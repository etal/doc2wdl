"""End-to-end generation, validated by the target languages' own checkers."""
import pytest

from doc2wrapper import docopter, nfgen, wdlgen
from doc2wrapper.tasktree import Argument

EXAMPLES = ["cnvkit-antitarget", "samtools-view"]


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
    check_wdl(wdlgen.render(docopter.transform(*docopter.parse(help_text(example)))))


@pytest.mark.parametrize("render, block", WRITERS)
@pytest.mark.parametrize("example", EXAMPLES)
def test_every_declared_input_reaches_the_command_line(
    help_text, example, render, block
):
    """The assertion no checker can make, for both target languages.

    An input that is declared but never interpolated produces a wrapper that parses,
    runs, and quietly ignores the argument -- which is what both templates did to
    every positional. This asserts on text, so it needs neither checker installed,
    and it deliberately covers the Nextflow writer even while its output is still
    invalid: the natural fix for that invalidity is a ternary that lints clean and
    drops the positionals all over again.
    """
    task = docopter.transform(*docopter.parse(help_text(example)))
    lines = block(render(task))
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
    wdl = wdlgen.render(docopter.transform(*docopter.parse(help_text("samtools-view"))))
    assert "$" not in "".join(command_block(wdl))
    assert "~{ in_bam }" in command_block(wdl)[1]


def test_repeated_positional_is_joined_as_an_array(help_text):
    wdl = wdlgen.render(docopter.transform(*docopter.parse(help_text("samtools-view"))))
    assert "Array[File] region = []" in wdl
    assert '~{sep(" ", region)}' in wdl


def test_reserved_words_are_renamed_per_target_language(check_wdl):
    """Renaming lives in the writer because the reserved set differs per language."""
    task = dict(
        title="Demo",
        usage="# demo",
        cli_prefix="demo",
        cli_args=[Argument(name="scatter", wdl_type="String", is_required=True)],
        has_output_file=False,
    )
    wdl = wdlgen.render(task)
    assert "String scatter_" in wdl
    check_wdl(wdl)
    # The Nextflow writer applies its own list, so the WDL keyword is left alone.
    assert "val scatter\n" in nfgen.render(task)


@pytest.mark.xfail(
    strict=True,
    reason="the Nextflow writer is unreachable from the CLI and its template is "
    "still an adaptation of the WDL one: the output declaration is not valid "
    "Nextflow, `${if defined(x) then ...}` is not Groovy, and `${\"-f \" + x}` "
    "renders `-f null` rather than eliding an unset flag. `nextflow lint` reports "
    "only the first error, so fixing one cause will not make this pass",
)
@pytest.mark.parametrize("example", EXAMPLES)
def test_generated_nextflow_is_accepted_by_nextflow_lint(
    help_text, check_nextflow, example
):
    task = docopter.transform(*docopter.parse(help_text(example)))
    check_nextflow(nfgen.render(task))
