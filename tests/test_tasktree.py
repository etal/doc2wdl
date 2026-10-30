"""The model shared by both readers, beginning with the one type-inference rule."""

import argparse
from pathlib import Path

import pytest

from doc2wrapper import argparser, docopter
from doc2wrapper.tasktree import ValueType, literal_default, type_and_default


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        # A valueless flag is a Boolean, and absent means not passed.
        (dict(declared=bool, default=False), (ValueType.BOOLEAN, False)),
        (dict(declared=bool, default=None), (ValueType.BOOLEAN, None)),
        # argparse calls no converter for a valueless flag, so the program would
        # receive the text itself, which no Boolean declaration holds.
        (dict(declared=bool, default="True"), (ValueType.BOOLEAN, None)),
        # A declared type outranks the default's own.
        (dict(declared=int, default=5), (ValueType.INTEGER, 5)),
        (dict(declared=int, default="5"), (ValueType.INTEGER, 5)),
        (dict(declared=float, default=5), (ValueType.NUMBER, 5)),
        (dict(declared=str, default="5"), (ValueType.TEXT, "5")),
        # A default the declaration cannot hold is dropped, not coerced: the tool
        # applies its own default to an option nobody passes.
        (dict(declared=int, default=266.6666666666667), (ValueType.INTEGER, None)),
        (dict(declared=int, default="abc"), (ValueType.INTEGER, None)),
        # bool is a subclass of int, yet a Boolean is no number.
        (dict(declared=float, default=True), (ValueType.NUMBER, None)),
        # A path the interface names is not a file the engine can provide.
        (dict(declared=Path, default="-"), (ValueType.FILE, None)),
        # With nothing declared, the default decides.
        (dict(default=5), (ValueType.INTEGER, 5)),
        (dict(default=0.5), (ValueType.NUMBER, 0.5)),
        (dict(default=True), (ValueType.BOOLEAN, True)),
        (dict(default="."), (ValueType.TEXT, ".")),
        # Nothing stated: a positional is the input file, an option a setting.
        (dict(), (ValueType.TEXT, None)),
        (dict(is_positional=True), (ValueType.FILE, None)),
        (dict(default="-", is_positional=True), (ValueType.TEXT, "-")),
        # An array is typed by its elements, and only when they agree.
        (dict(default=[3, 4], is_array=True), (ValueType.INTEGER, [3, 4])),
        (dict(default=["a", "b"], is_array=True), (ValueType.TEXT, ["a", "b"])),
        (
            dict(default=[3, "a"], is_array=True, is_positional=True),
            (ValueType.FILE, None),
        ),
        # A declared number holds a mixed list: each element must fit the
        # declaration, not match the others.
        (
            dict(declared=float, default=[1, 2.5], is_array=True),
            (ValueType.NUMBER, [1, 2.5]),
        ),
        (dict(default=".", is_array=True), (ValueType.TEXT, ".")),
        # A list says nothing about a scalar, and no scalar declaration holds it.
        (dict(default=[3, 4]), (ValueType.TEXT, None)),
    ],
)
def test_type_and_default_precedence(kwargs, expected):
    assert type_and_default(**kwargs) == expected


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("150000", 150000),
        ("0.5", 0.5),
        ("tab", "tab"),
        ("False", False),
        ("None", None),
        # Prose describing a computed default states no value.
        ("1/16 avg size, calculated", None),
        (".", None),
        (None, None),
    ],
)
def test_literal_default_reads_help_text_notation(text, value):
    assert literal_default(text) == value


def test_the_two_readers_infer_the_same_types():
    """The acceptance criterion for one type rule: one tool, two descriptions.

    An `ArgumentParser` and its own `--help` output describe the same interface, so
    corresponding arguments must be typed alike.  The defaults are written into the
    help strings because `[default: ...]` is the only notation help text has for
    one, and it is the only evidence of a type the text carries -- a parser's
    `type=int` on an option with no default says more than any help text can.
    Compared by name, since a help text states positionals in the usage line and
    options in the options block, so one reader cannot recover the other's order.
    """
    parser = argparse.ArgumentParser(prog="tool", description="A tool.")
    parser.add_argument(
        "--count", type=int, default=5, help="N. [default: %(default)s]"
    )
    parser.add_argument(
        "--ratio", type=float, default=0.25, help="R. [default: %(default)s]"
    )
    parser.add_argument("--label", default="hi", help="L. [default: %(default)s]")
    parser.add_argument("--verbose", action="store_true", help="Say more.")
    parser.add_argument("infile", help="Input.")
    parser.add_argument("extra", nargs="*", help="More.")

    def typing(task):
        return {
            arg.name: (arg.value_type, arg.is_array, arg.is_required, arg.default_value)
            for arg in task["cli_args"]
        }

    (from_parser,) = argparser.unpack_tasks(parser, "tool")
    from_text = docopter.transform(*docopter.parse(parser.format_help()))
    assert typing(from_text) == typing(from_parser)
    assert typing(from_parser) == {
        "count": (ValueType.INTEGER, False, False, 5),
        "ratio": (ValueType.NUMBER, False, False, 0.25),
        "label": (ValueType.TEXT, False, False, "hi"),
        "verbose": (ValueType.BOOLEAN, False, False, False),
        "infile": (ValueType.FILE, False, True, None),
        "extra": (ValueType.FILE, True, False, None),
    }


def test_the_two_readers_pass_the_same_flags_for_spellings_of_one_option():
    """An option with two long spellings, and a switch with a flag for false.

    argparse lists both spellings in one entry, and the synopsis names the first;
    each reader passes the last.  Compared by flag rather than by name, since the
    argparse reader names an input from its `dest`, the first spelling, and the
    help-text reader from the flag it passes.
    """
    parser = argparse.ArgumentParser(prog="tool")
    parser.add_argument("--log-dir", "--log_dir", help="Logs.")
    parser.add_argument("--zst", action=argparse.BooleanOptionalAction, help="Zst.")
    parser.add_argument("infile", help="Input.")

    def flags(task):
        return [
            (arg.option_flag, arg.false_flag, arg.option_has_value, arg.default_value)
            for arg in task["cli_args"]
            if arg.option_flag
        ]

    (from_parser,) = argparser.unpack_tasks(parser, "tool")
    from_text = docopter.transform(*docopter.parse(parser.format_help()))
    assert flags(from_text) == flags(from_parser)
    assert flags(from_parser) == [
        ("--log_dir", "", True, None),
        ("--zst", "--no-zst", False, None),
    ]
