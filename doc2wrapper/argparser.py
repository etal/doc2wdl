#!/usr/bin/env python3
"""Take a populated ArgumentParser, unpack it, and populate the task object model."""

import argparse

from .tasktree import Argument


def unpack_tasks(arg_parser, prog):
    """Extract WDL "task" values from an ArgumentParser.

    In the usual case where the ArgumentParser contains positional arguments and/or
    options, emit an iterable of one item, the "task" values dictionary.

    If the given ArgumentParser contains subcommands, each subcommand will also be
    unpacked (recursively) to yield more tasks. If the parent command contains a mix of
    normal arguments (other than --help and --version) and subcommands, each subcommand
    is emitted first and the parent command last, as a task that skips the subcommands
    as arguments -- the recursion happens while walking the parent's actions, whereas
    the parent's own task is not complete until that walk has finished.  So emission
    order is not source order for such a parser, and the document preserves emission
    order.

    If an ArgumentParser contains no options/arguments other than a "--help" and/or
    "--version" option and/or subcommands, it won't be yielded as a task; only the
    subcommands (if any) will be yielded.

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
        # `--help` and `--version` describe the tool, not its inputs -- the rule
        # `docopter._SKIPPED_OPTIONS` applies to help text.  Not shared as one list:
        # a live parser states the intent in the action class, never in a spelling,
        # which is why a `-v` spelled version flag is skipped here and deliberately
        # is not there, where it means "verbose" as often as "version".
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
                argparse.BooleanOptionalAction,
            ),
        ):
            task_kwargs["cli_args"].append(_argument(action))
        else:
            raise TypeError(f"What is this? {action} :: {type(action)}")

    if task_kwargs["cli_args"]:
        yield task_kwargs


def _argument(action):
    """The model of one handled argparse action."""
    # SUPPRESS leaves the attribute absent from the namespace: there is no value.
    default = None if action.default is argparse.SUPPRESS else action.default
    if action.nargs == 0:
        option_flag, false_flag, default = _switch(action, default)
        return Argument(
            name=action.dest,
            wdl_type="Boolean",
            is_required=action.required,
            default_value=default,
            option_flag=option_flag,
            false_flag=false_flag,
            doc=action.help or "",
        )
    # `append` and `extend` add to their default rather than replace it, so the
    # values the input supplies start empty; spelling the default as well would
    # pass it twice.  `extend` subclasses `append`, and a flag per value suits it.
    appends = isinstance(action, argparse._AppendAction)
    if appends:
        default = None
    is_option = bool(action.option_strings)
    # A positional that may take zero values is never required.  Whether argparse
    # marks one required has changed between Python releases, and output must not,
    # so for a positional the arity decides and the attribute is not consulted.
    is_required = (
        action.required
        if is_option
        else action.nargs not in ("?", "*", argparse.REMAINDER, argparse.SUPPRESS)
    )
    return Argument(
        name=action.dest,
        wdl_type=(
            "Boolean"
            if action.type is bool or isinstance(default, bool)
            else "Int"
            if action.type is int or isinstance(default, int)
            else "Float"
            if action.type is float or isinstance(default, float)
            else "String"
        ),
        # Arity decides, not the type of `dest`: `nargs=1` takes one value into a
        # one-item list and is modeled as the scalar it is on the command line.
        is_array=(
            appends
            or action.nargs in ("*", "+", argparse.REMAINDER)
            or (isinstance(action.nargs, int) and action.nargs > 1)
        ),
        is_required=is_required,
        default_value=default,
        option_flag=action.option_strings[-1] if is_option else "",
        option_has_value=is_option,
        flag_per_value=appends,
        doc=action.help or "",
    )


def _switch(action, default):
    """The spellings for true and for false, and the default, of a valueless option.

    Spellings prefer the last listed, which by argparse convention is the long one.
    """
    bool_default = default if isinstance(default, bool) else None
    if isinstance(action, argparse.BooleanOptionalAction):
        # The action adds `--no-X` after each `--X`, and nothing for a short `-X`.
        # Matching that rule rather than the prefix keeps an option itself spelled
        # `--no-cache` affirmative.
        generated = {"--no-" + s[2:] for s in action.option_strings if s[:2] == "--"}
        negated = [s for s in action.option_strings if s in generated]
        affirmative = [s for s in action.option_strings if s not in generated]
        return affirmative[-1], negated[-1] if negated else "", bool_default
    if isinstance(action.const, bool):
        # store_true and store_false: the flag stores its const.
        flag = action.option_strings[-1]
        return (flag, "", bool_default) if action.const else ("", flag, bool_default)
    # count, store_const and append_const store something other than a Boolean.
    # The input means "pass the flag", and leaving it out is what the parser does
    # when the option is absent, whatever `default` records.  A count therefore
    # passes its flag at most once.
    return action.option_strings[-1], "", False
