#!/usr/bin/env python3
"""Take a populated ArgumentParser, unpack it, and populate the task object model."""
# Of interest:
# AP.description
# AP.epilog
# AP._actions
import argparse

from .tasktree import Argument


def unpack_tasks(arg_parser, prog):
    """Extract WDL "task" values from an ArgumentParser.

    In the usual case where the ArgumentParser contains positional arguments and/or
    options, emit an iterable of one item, the "task" values dictionary.

    If the given ArgumentParser contains subcommands, each subcommand will also be
    unpacked (recursively) to yield more tasks. If the parent command contains a mix of
    normal arguments (other than --help) and subcommands, each subcommand is emitted
    first and the parent command is emitted last, as a task that skips the subcommands
    as arguments -- the recursion happens while walking the parent's actions, whereas
    the parent's own task is not complete until that walk has finished.  So emission
    order is not source order for such a parser, and the document preserves emission
    order.

    If an ArgumentParser contains no options/arguments other than a "--help" option
    and/or subcommands, it won't be yielded as a task; only the subcommands (if any)
    will be yielded.

    If there are no normal arguments/options, emit nothing (no tasks, empty iterable).

    Args:
        arg_parser: ArgumentParser instance
        prog: the executable name or prefix, since Python doesn't know it
    Output: iterable of template-ready kwarg dictionaries.
    """
    task_kwargs = {
        "title": (prog.replace(".py", "").title().replace(" ", "").replace("-", "")),
        "usage": " ".join([arg_parser.description or "", arg_parser.epilog or ""]),
        "cli_prefix": prog,
        "cli_args": [],
        "has_output_file": False,
    }
    for action in arg_parser._actions:
        # `--help` and `--version` describe the tool itself rather than one of its
        # inputs, which is the same rule the docopt reader applies through
        # `_SKIPPED_OPTIONS`.  It is spelled here in the vocabulary this reader has --
        # the action class -- rather than shared as a list of flag spellings, because
        # a live parser states the intent and does not have to guess it from a name.
        if isinstance(action, (argparse._HelpAction, argparse._VersionAction)):
            continue

        if isinstance(action, argparse._SubParsersAction):
            for cmd_name, sub_ap in action.choices.items():
                yield from unpack_tasks(sub_ap, f"{prog} {cmd_name}")
            continue

        if isinstance(
            action,
            (
                argparse._StoreAction,
                # _StoreConstAction covers store_true and store_false, which are it
                # with const=True/False, default the negation, and nargs=0.
                argparse._StoreConstAction,
                argparse._AppendAction,
                argparse._AppendConstAction,
                argparse._CountAction,
            ),
        ):
            arg = Argument(
                name=action.dest,
                wdl_type=(
                    "Boolean"
                    if action.type is bool or isinstance(action.default, bool)
                    # XXX or action.const is not None?
                    else "Int"
                    if action.type is int or isinstance(action.default, int)
                    else "Float"
                    if action.type is float or isinstance(action.default, float)
                    else "String"
                ),
                is_array=(action.nargs in (0, 1, "?")),
                is_required=action.required,
                default_value=action.default,
                option_flag=action.option_strings[-1] if action.option_strings else "",
                option_has_value=action.nargs != 0,
                doc=action.help or "",
            )
            task_kwargs["cli_args"].append(arg)
        else:
            raise TypeError(f"What is this? {action} :: {type(action)}")

    if task_kwargs["cli_args"]:
        yield task_kwargs
