#!/usr/bin/env python3
"""Take a populated ArgumentParser, unpack it, and populate the task object model."""

import argparse
from dataclasses import replace
from pathlib import Path

from .tasktree import Argument, legal_name, task_title, type_and_default


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
        "title": task_title(prog),
        "usage": " ".join([arg_parser.description or "", arg_parser.epilog or ""]),
        "cli_prefix": prog,
        "has_output_file": False,
    }
    handled = []
    for action in arg_parser._actions:
        # `--help` and `--version` describe the tool, not its inputs -- the rule
        # `docopter._SKIPPED_OPTIONS` applies to help text.  Not shared as one list:
        # a live parser states the intent in the action class, never in a spelling,
        # which is why a `-v` spelled version flag is skipped here and deliberately
        # is not there, where it means "verbose" as often as "version".
        if isinstance(action, (argparse._HelpAction, argparse._VersionAction)):
            continue
        # `nargs=SUPPRESS` consumes no command-line strings and never writes its
        # `dest`, so the action is no input.  Skipped here rather than in
        # `_arguments`, which must not count it among the actions sharing a `dest`.
        if action.nargs == argparse.SUPPRESS:
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
            handled.append(action)
        else:
            raise TypeError(f"What is this? {action} :: {type(action)}")

    task_kwargs["cli_args"] = list(_arguments(handled))
    if task_kwargs["cli_args"]:
        yield task_kwargs


def _arguments(actions):
    """Model the handled actions in source order, given which share a `dest`.

    Several actions may write one `dest`.  A `store_true` and a `store_false` doing
    so are one switch with a spelling for each value, which is how the model
    already expresses `BooleanOptionalAction`, so they become one input at the
    first one's position.  Any other actions sharing a `dest`, such as
    `append_const` flags each adding a statistic to one list, are separately
    passable, so each is named after its own flag (a positional keeps the `dest`):
    the `dest` names what they build together, not what one of them controls.
    The flag is the model's `option_flag` rather than the last option string,
    which for a `BooleanOptionalAction` is its negation.  A switch with a single
    spelling so named means "pass this flag", whatever value it stores, so that
    its name and its value agree: a `store_false` spelled `-q` becomes an input `q`
    that passes `-q` when true.  None of these flag-named inputs carries its
    action's default, because argparse fills the `dest` from one action's default
    alone, so each defaults to passing nothing and the tool's own default stands.
    """
    sharing = {}
    for action in actions:
        sharing.setdefault(action.dest, []).append(action)
    for action in actions:
        group = sharing[action.dest]
        dest_name = legal_name(action.dest, prefix="arg_")
        if len(group) == 1:
            yield _argument(action, dest_name)
        elif _is_paired_switch(group):
            if action is group[0]:
                yield _paired_switch(group, dest_name)
        else:
            arg = _argument(action, dest_name)
            if arg.false_flag and not arg.option_flag:
                arg = replace(arg, option_flag=arg.false_flag, false_flag="")
            # A switch with one spelling is passed or not, as a `count` is; one
            # with two may be left unset, which passes neither.
            is_switch = action.nargs == 0 and not arg.false_flag
            yield replace(
                arg,
                name=(
                    legal_name(arg.option_flag.lstrip("-"), prefix="arg_")
                    if arg.option_flag
                    else dest_name
                ),
                default_value=False if is_switch else None,
            )


def _is_paired_switch(group):
    """Are these the `store_true` and `store_false` actions of one switch?"""
    return (
        len(group) == 2
        and all(
            isinstance(action, argparse._StoreConstAction)
            and action.option_strings
            and isinstance(action.const, bool)
            for action in group
        )
        and {action.const for action in group} == {True, False}
    )


def _paired_switch(group, name):
    """One switch input for a `store_true` and a `store_false` sharing a `dest`.

    argparse fills an absent `dest` from the first action whose default is not
    `SUPPRESS`, so that action's default is the switch's default.
    """
    affirmative, negative = sorted(group, key=lambda action: not action.const)
    defaulting = [action for action in group if action.default is not argparse.SUPPRESS]
    first = _argument(defaulting[0] if defaulting else group[0], name)
    return replace(
        first,
        option_flag=affirmative.option_strings[-1],
        false_flag=negative.option_strings[-1],
        is_required=any(action.required for action in group),
        doc="\n".join(
            f"{action.option_strings[-1]}: {action.help}"
            for action in group
            if action.help
        ),
    )


def _argument(action, name):
    """The model of one handled argparse action, under the given input name."""
    # SUPPRESS leaves the attribute absent from the namespace: there is no value.
    default = None if action.default is argparse.SUPPRESS else action.default
    if action.nargs == 0:
        option_flag, false_flag, default = _switch(action, default)
        value_type, default = type_and_default(declared=bool, default=default)
        return Argument(
            name=name,
            value_type=value_type,
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
        else action.nargs not in ("?", "*", argparse.REMAINDER)
    )
    # Arity decides, not the type of `dest`: `nargs=1` takes one value into a
    # one-item list and is modeled as the scalar it is on the command line.
    is_array = (
        appends
        or action.nargs in ("*", "+", argparse.REMAINDER)
        or (isinstance(action.nargs, int) and action.nargs > 1)
    )
    # `FileType` opens the path it is given, so it declares a file exactly as
    # `pathlib.Path` does, and the model has one name for both.
    value_type, default = type_and_default(
        declared=Path if isinstance(action.type, argparse.FileType) else action.type,
        default=default,
        is_array=is_array,
        is_positional=not is_option,
    )
    return Argument(
        name=name,
        value_type=value_type,
        is_array=is_array,
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
    A default that is not a Boolean does not fit the switch and is dropped by
    `tasktree.type_and_default`, which types every switch.
    """
    if isinstance(action, argparse.BooleanOptionalAction):
        # The action adds `--no-X` after each `--X`, and nothing for a short `-X`.
        # Matching that rule rather than the prefix keeps an option itself spelled
        # `--no-cache` affirmative.
        generated = {"--no-" + s[2:] for s in action.option_strings if s[:2] == "--"}
        negated = [s for s in action.option_strings if s in generated]
        affirmative = [s for s in action.option_strings if s not in generated]
        return affirmative[-1], negated[-1] if negated else "", default
    if isinstance(action.const, bool):
        # store_true and store_false: the flag stores its const.
        flag = action.option_strings[-1]
        return (flag, "", default) if action.const else ("", flag, default)
    # count, store_const and append_const store something other than a Boolean.
    # The input means "pass the flag", and leaving it out is what the parser does
    # when the option is absent, whatever `default` records.  A count therefore
    # passes its flag at most once.
    return action.option_strings[-1], "", False
