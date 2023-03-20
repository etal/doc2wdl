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
    args.func(args)


def cmd_argparse(args):
    """Console script for subcommand 'argparse'."""
    module = importlib.import_module(args.module)
    parser_obj = getattr(module, args.parser)
    prog = args.module.split(".", 1)[0]

    out_wdls = [
        wdlgen.render(task) for task in argparser.unpack_tasks(parser_obj, prog)
    ]
    # TODO: strip the first line ("version") after the first task
    out_text = "\n\n".join(out_wdls)
    args.output.write(out_text)
    return 0


def cmd_docopt(args):
    """Console script for subcommand 'docopt'."""
    doc = args.filename.read()
    usage, positionals, options = docopter.parse(doc)
    print(
        f"Parsed {len(positionals)} positional and {len(options)} optional CLI arguments.",
        file=sys.stderr,
    )
    task = docopter.transform(usage, positionals, options)
    out_wdl = wdlgen.render(task)
    args.output.write(out_wdl)
    return 0


if __name__ == "__main__":
    sys.exit(main())  # pragma: no cover
