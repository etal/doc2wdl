"""Reading a tool's interface out of a live ArgumentParser."""

import argparse

import pytest

from doc2wrapper import argparser
from doc2wrapper.tasktree import ValueType


def test_version_is_not_an_input():
    """`--version` describes the tool, so it belongs with `--help`, not the inputs.

    `add_help` is left on here, so the one assertion covers both skips.

    Declaring it the idiomatic way used to raise, which meant a parser as ordinary
    as `mypy.dmypy.client.parser` produced nothing at all.
    """
    parser = argparse.ArgumentParser(prog="tool")
    parser.add_argument("--version", action="version", version="1.0")
    parser.add_argument("infile")
    (task,) = argparser.unpack_tasks(parser, "tool")
    assert [arg.name for arg in task["cli_args"]] == ["infile"]


def test_bare_const_and_count_flags_are_inputs():
    """`store_const` and `count` are inputs; only help and version are discarded.

    Both used to raise as `--version` did, and are just as ordinary -- `-v` as a
    count is a common spelling.  Neither takes a value on the command line, which
    is what distinguishes them from a plain `store`.
    """
    parser = argparse.ArgumentParser(prog="tool", add_help=False)
    parser.add_argument("--mode", action="store_const", const="fast")
    parser.add_argument("-v", "--verbose", action="count", default=0)
    (task,) = argparser.unpack_tasks(parser, "tool")
    assert [arg.name for arg in task["cli_args"]] == ["mode", "verbose"]
    assert not any(arg.option_has_value for arg in task["cli_args"])


def test_actions_argparse_defines_are_read_without_a_warning(capsys):
    """Every action argparse registers is modeled or handled separately.

    An action class missing from the known ones would warn and lose its default,
    as `--color` would here, though its command line would still be right.  The
    registry check reports any class a later Python release adds that is not a
    subclass of a modeled one.
    """
    parser = argparse.ArgumentParser(prog="tool", add_help=False)
    parser.add_argument("--name", default="d")
    parser.add_argument("--fast", action="store_true")
    parser.add_argument("--slow", action="store_false")
    parser.add_argument("--mode", action="store_const", const="m")
    parser.add_argument("--tag", action="append")
    parser.add_argument("--stat", action="append_const", const="s")
    parser.add_argument("-v", action="count")
    parser.add_argument("--file", action="extend", nargs="+")
    parser.add_argument("--color", action=argparse.BooleanOptionalAction, default=True)
    (task,) = argparser.unpack_tasks(parser, "tool")
    assert capsys.readouterr().err == ""
    defaults = {arg.name: arg.default_value for arg in task["cli_args"]}
    assert (defaults["name"], defaults["color"]) == ("d", True)
    registered = set(parser._registries["action"].values())
    assert {
        cls for cls in registered if not issubclass(cls, argparser._KNOWN_ACTIONS)
    } == {
        argparse._HelpAction,
        argparse._VersionAction,
        argparse._SubParsersAction,
    }


def test_a_paired_switch_takes_the_default_argparse_fills_its_dest_from():
    """argparse fills the `dest` from the first action whose default is not SUPPRESS.

    Here that is the second of the pair, so the switch defaults to its True.
    """
    parser = argparse.ArgumentParser(prog="tool", add_help=False)
    parser.add_argument("--squash", action="store_true", default=argparse.SUPPRESS)
    parser.add_argument("--no-squash", dest="squash", action="store_false")
    assert parser.parse_args([]).squash is True
    (task,) = argparser.unpack_tasks(parser, "tool")
    assert [arg.default_value for arg in task["cli_args"]] == [True]


def test_store_false_default_is_not_confused_with_its_const():
    """A `store_false` argument's default is True, and the reader must report that.

    Both are `_StoreConstAction` subclasses, so a handler keyed on the const value
    rather than on the action class would invert this one and pass every other case.
    """
    parser = argparse.ArgumentParser(prog="tool", add_help=False)
    parser.add_argument("--loud", action="store_true")
    parser.add_argument("--no-check", action="store_false", dest="check")
    (task,) = argparser.unpack_tasks(parser, "tool")
    assert [arg.name for arg in task["cli_args"]] == ["loud", "check"]
    assert [arg.default_value for arg in task["cli_args"]] == [False, True]


def test_paired_flag_spellings_follow_the_action():
    """A `BooleanOptionalAction` adds `--no-X` for each long `--X`, and only that.

    The last option string is the negation, and a short spelling has none.
    """
    parser = argparse.ArgumentParser(prog="tool", add_help=False)
    parser.add_argument("-s", "--spam", action=argparse.BooleanOptionalAction)
    parser.add_argument("-q", action=argparse.BooleanOptionalAction)
    (task,) = argparser.unpack_tasks(parser, "tool")
    assert [(arg.option_flag, arg.false_flag) for arg in task["cli_args"]] == [
        ("--spam", "--no-spam"),
        ("-q", ""),
    ]


def test_paired_flag_spelled_with_a_no_prefix_stays_affirmative():
    """`--no-cache` is the option itself, and `--no-no-cache` its negation."""
    parser = argparse.ArgumentParser(prog="tool", add_help=False)
    try:
        parser.add_argument("--no-cache", action=argparse.BooleanOptionalAction)
    except ValueError:
        pytest.skip("this Python forbids a `--no-` spelling on a paired flag")
    (task,) = argparser.unpack_tasks(parser, "tool")
    (arg,) = task["cli_args"]
    assert (arg.option_flag, arg.false_flag) == ("--no-cache", "--no-no-cache")


def test_requiredness_of_a_positional_follows_its_arity():
    """Output must not depend on which Python release built the parser.

    Some releases mark a `nargs="*"` or `REMAINDER` positional without a default as
    required and others do not.  A positional that accepts zero values is optional
    and one that must take a value is required, whatever its attribute says; an
    option keeps whatever its own `required` says.
    """
    parser = argparse.ArgumentParser(prog="tool", add_help=False)
    zero_or_more = [("star", "*"), ("maybe", "?"), ("rest", argparse.REMAINDER)]
    for name, nargs in zero_or_more:
        parser.add_argument(name, nargs=nargs).required = True
    parser.add_argument("one").required = False
    parser.add_argument("plus", nargs="+")
    parser.add_argument("--opt", nargs="*", required=True)
    (task,) = argparser.unpack_tasks(parser, "tool")
    assert {arg.name: arg.is_required for arg in task["cli_args"]} == {
        "star": False,
        "maybe": False,
        "rest": False,
        "one": True,
        "plus": True,
        "opt": True,
    }


def test_an_action_that_consumes_nothing_is_not_an_input():
    """An action that consumes no command-line strings is no input.

    `nargs=SUPPRESS` takes no value and stores nothing; as an input, the option
    rendered `--hush VALUE`, which the tool rejects.  A positional `store_true`,
    which argparse accepts, consumes nothing too, and used to raise.  Neither may
    count among the actions sharing a `dest`: argparse fills `x` from `--real`
    alone when, as here, `--real` is declared first, so `--real` keeps the `dest`'s
    name and its default.
    """
    parser = argparse.ArgumentParser(prog="tool", add_help=False)
    parser.add_argument("--hush", nargs=argparse.SUPPRESS)
    parser.add_argument("hidden", nargs=argparse.SUPPRESS)
    parser.add_argument("flag", action="store_true")
    parser.add_argument("--real", dest="x", default="d")
    parser.add_argument("--ghost", dest="x", nargs=argparse.SUPPRESS)
    (task,) = argparser.unpack_tasks(parser, "tool")
    assert [(arg.name, arg.default_value) for arg in task["cli_args"]] == [("x", "d")]


@pytest.mark.parametrize(
    "name, kwargs, grouped",
    [
        ("--num", {"action": "append", "nargs": 2}, True),
        ("--num", {"action": "append", "nargs": "+"}, True),
        ("--num", {"action": "extend", "nargs": 2}, True),
        ("--num", {"action": "extend", "nargs": "+"}, False),
        ("--num", {"action": "append", "nargs": 1}, False),
        ("--num", {"action": "append"}, False),
        ("num", {"action": "append", "nargs": "+"}, False),
    ],
)
def test_an_element_is_one_occurrence_of_a_flag(name, kwargs, grouped):
    """Where one occurrence takes several values, an element holds them as text.

    `extend` with `nargs="+"` builds one flat list whether its values follow one
    flag or each their own, so it keeps its declared type, and a positional
    occurs only once, so its values already form one group.
    """
    parser = argparse.ArgumentParser(prog="tool", add_help=False)
    parser.add_argument(name, type=int, help="Numbers.", **kwargs)
    (task,) = argparser.unpack_tasks(parser, "tool")
    (arg,) = task["cli_args"]
    assert arg.is_array and arg.flag_per_value
    if grouped:
        assert arg.value_type == ValueType.TEXT
        note = f"Each element is one occurrence of {name}: "
        assert arg.doc.startswith(f"Numbers.\n{note}")
    else:
        assert (arg.value_type, arg.doc) == (ValueType.INTEGER, "Numbers.")
