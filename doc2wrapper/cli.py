#!/usr/bin/env python3
"""Console script endpoints.
"""
import argparse
import importlib
import sys

from . import argparser
from . import docopter
from . import wdlgen


def main():
    aparser = argparse.ArgumentParser(
        description="Generate WDL or Nextflow tool wrappers from CLI definitions.",
        epilog="See the online docs for details: https://github.com/etal/doc2wrapper",
    )
    aparser.set_defaults(func=lambda args: aparser.print_help())
    ap_subparsers = aparser.add_subparsers(
        help="Sub-commands (use with -h for more info)"
    )

    sp_argparse = ap_subparsers.add_parser("argparse", help=cmd_argparse.__doc__)
    sp_argparse.add_argument(
        "-m",
        "--module",
        help="""Python package and module path where the argument parser object
                is defined.""",
    )
    sp_argparse.add_argument(
        "-p",
        "--parser",
        help="""Name of the argument parser object (ArgumentParser instance)
                in the specified module.""",
    )
    sp_argparse.add_argument(
        "-o", "--output", type=argparse.FileType("wt"), default=sys.stdout
    )
    sp_argparse.set_defaults(func=cmd_argparse)

    sp_docopt = ap_subparsers.add_parser("docopt", help=cmd_docopt.__doc__)
    sp_docopt.add_argument(
        "-f",
        "--filename",
        type=argparse.FileType("rt"),
        default=sys.stdin,
        help="""Text file containing a CLI command's help text, e.g. from '-h'.
                [Default: read from standard input]""",
    )
    sp_docopt.add_argument(
        "-o", "--output", type=argparse.FileType("wt"), default=sys.stdout
    )
    sp_docopt.set_defaults(func=cmd_docopt)

    args = aparser.parse_args()
    # setuptools uses this as the process exit status, so a subcommand that
    # could not generate anything must be able to report failure.
    return args.func(args)


def cmd_argparse(args):
    """Console script for subcommand 'argparse'."""
    module = importlib.import_module(args.module)
    parser_obj = getattr(module, args.parser)
    prog = args.module.split(".", 1)[0]

    tasks = list(argparser.unpack_tasks(parser_obj, prog))
    # A parser carrying nothing but --help yields no tasks, and a document with no
    # tasks is still valid WDL, so silence here would look like success.
    print(f"Unpacked {len(tasks)} task(s) from {args.parser}.", file=sys.stderr)
    args.output.write(wdlgen.render(tasks))
    return 0


def cmd_docopt(args):
    """Console script for subcommand 'docopt'."""
    doc = args.filename.read()
    try:
        usage, positionals, options = docopter.parse(doc)
    except docopter.UsageNotFound as why:
        print(
            f"Cannot read a command line from this help text: {why}",
            file=sys.stderr,
        )
        return 1
    print(
        f"Parsed {len(positionals)} positional and {len(options)} optional CLI arguments.",
        file=sys.stderr,
    )
    task = docopter.transform(usage, positionals, options)
    args.output.write(wdlgen.render([task]))
    return 0


if __name__ == "__main__":
    sys.exit(main())  # pragma: no cover
