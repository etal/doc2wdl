"""Reading a tool's interface out of a live ArgumentParser."""
import argparse

from doc2wrapper import argparser


def names(parser, prog="tool"):
    """The input names of the single task the given parser unpacks to."""
    (task,) = argparser.unpack_tasks(parser, prog)
    return [arg.name for arg in task["cli_args"]]


def test_version_is_not_an_input():
    """`--version` describes the tool, so it belongs with `--help`, not the inputs.

    Declaring it the idiomatic way used to raise from the reader's `else` branch,
    which meant a parser as ordinary as `mypy.dmypy.client.parser` produced nothing
    at all.
    """
    parser = argparse.ArgumentParser(prog="tool")
    parser.add_argument("--version", action="version", version="1.0")
    parser.add_argument("infile")
    assert names(parser) == ["infile"]


def test_bare_const_and_count_flags_are_inputs():
    """`store_const` and `count` are inputs; only help and version are discarded.

    Both reached the same fatal `else` as `--version` and are just as ordinary --
    `-v` as a count is a common spelling.  Neither takes a value on the command
    line, which is what distinguishes them from a plain `store`.
    """
    parser = argparse.ArgumentParser(prog="tool", add_help=False)
    parser.add_argument("--mode", action="store_const", const="fast")
    parser.add_argument("-v", "--verbose", action="count", default=0)
    (task,) = argparser.unpack_tasks(parser, "tool")
    assert [arg.name for arg in task["cli_args"]] == ["mode", "verbose"]
    assert not any(arg.option_has_value for arg in task["cli_args"])


def test_store_true_and_store_false_survive_the_const_generalization():
    """They are `_StoreConstAction` subclasses, so listing the base must keep them.

    A `store_false` argument defaults to True, which is the case that would expose a
    handler keyed on the const rather than on the action class.
    """
    parser = argparse.ArgumentParser(prog="tool", add_help=False)
    parser.add_argument("--loud", action="store_true")
    parser.add_argument("--no-check", action="store_false", dest="check")
    (task,) = argparser.unpack_tasks(parser, "tool")
    assert [arg.name for arg in task["cli_args"]] == ["loud", "check"]
    assert [arg.default_value for arg in task["cli_args"]] == [False, True]
