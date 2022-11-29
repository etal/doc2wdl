#!/usr/bin/env python3

"""Take a populated ArgumentParser, unpack it, and generate a WDL task."""

# Of interest:
# AP.description
# AP.epilog
# AP._actions
import argparse

import doc2wdl

def unpack_tasks(arg_parser, prog):
    """Extract WDL "task" values from an ArgumentParser.

    In the usual case where the ArgumentParser contains positional arguments and/or
    options, emit an iterable of one item, the "task" values dictionary.

    If the given ArgumentParser contains subcommands, each subcommand will also be
    unpacked (recursively) to yield more tasks. If the parent command contains a mix of
    normal arguments (other than --help) and subcommands, the parent command will be
    emitted as a task that skips the subcommands as arguments, and then each subcommand
    will be individually emitted as another task.

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
            "title": (prog
                .replace(".py", "")
                .title()
                .replace(' ', '')
                .replace('-', '')),
            "usage": " ".join([arg_parser.description or "", arg_parser.epilog or ""]),
            "cli_prefix": prog,
            "cli_args": [],
            "has_output_file": False}
    for action in arg_parser._actions:
        if isinstance(action, argparse._HelpAction):
            continue

        if isinstance(action, argparse._SubParsersAction):
            for cmd_name, sub_ap in action.choices:
                yield from unpack_tasks(sub_ap, f"{prog} {cmd_name}")
            continue

        if isinstance(action, (argparse._StoreAction, argparse._StoreTrueAction)):
            # _StoreTrueAction = _StoreAction where const=True, default=False, nargs=0
            arg = doc2wdl.Argument(
                name=action.dest,
                wdl_type=(
                    "Boolean" if action.type is bool or isinstance(action.default, bool)
                    # XXX or action.const is not None?
                    else
                    "Int" if action.type is int or isinstance(action.default, int) else
                    "Float" if action.type is float or isinstance(action.default, float) else
                    "String"),
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

def main(arg_parser, prog):
    for i, task in enumerate(unpack_tasks(arg_parser, prog)):
        out_wdl = doc2wdl.render(task)
        with open(f"{out_wdl['title']}-{i}.task.wdl", "w", encoding="utf-8") as outfile:
            outfile.write(out_wdl)
