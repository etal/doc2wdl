#!/usr/bin/env python3
"""
"""
import ast
import sys
from dataclasses import dataclass

import docopt
import jinja2


@dataclass
class Argument:
    """A command argument for a WDL task."""
    name: str
    wdl_type: str
    is_array: bool = False
    is_required: bool = False
    default_value: str | None = None
    option_flag: str = ""
    option_has_value: bool = False
    doc: str = ""




def parse_doc(doc):
    """Parse the given help text into data structures."""
    usage = docopt.printable_usage(doc).split(None, 1)[1]

    options = docopt.parse_defaults(doc)
    # Option attributes:
    #   short=None, # string, e.g. -s
    #   long=None,  # string, e.g. --sample
    #   argcount=0, # 0 or 1; whether there is an argument following the option
    #   value=False # default value; False if argcount=0; or if argcount=1, the
    #               # default value if given in help text, or None if not given
    #               # / there isn't one.

    # If this doesn't parse, `args` is just the original usage text, otherwise
    # it's a dict (with stable ordering, so positional args are not scrambled)
    try:
        args = docopt.docopt(doc)
    except (docopt.DocoptExit, docopt.DocoptLanguageError) as why:
        print("Warning: didn't parse all arguments:", type(why).__name__, file=sys.stderr)
        args = None
    if isinstance(args, dict):
        # If docopt() worked, then remove the `opts` short and long keys from
        # the `args` dict and what remains are positional arguments.
        # The dict values are not meaningful so just treat them all as
        # (required/optional) positionals.
        opt_keys = set(opt.long or opt.short for opt in options)
        positionals = [k for k in args.keys() if k not in opt_keys]
    else:
        # docopt parsing failed for positional arguments; leave them as an
        # exercise for the user.
        positionals = []
    return usage, positionals, options


def transform(usage, positionals, options):
    """Prepare CLI values for intepolation.

    Args:
        - usage
        - positionals
        - options

    Return:

        - Task title, i.e. name
        - CLI prefix
        - list of Argument instances values as strings/ints ready for interpolation
        - has_output_file

    Magic names:

        - output_file_name: the input field, a string, created if '--output' is a CLI
              option.
        - output_file: the output field. If output_file_name was given/created, this is
              a file referring to that output, otherwise the task will use stdout as its
              output.

    """
    cli_prefix = usage[:usage.find(' [')].rstrip()
    title = (cli_prefix
                .replace(".py", "")
                .title()
                .replace(' ', '')
                .replace('-', ''))

    cli_args = []
    for token in positionals:
        # Just the keys -- no option prefix
        cli_args.append(Argument(
            name=token.strip("<>").replace('.', '_').replace('[', '').replace(']', ''),
            wdl_type="File",
            #is_array=False,
            is_required=True,
            #default_value=None,
            #option_flag="",
            #option_has_value=
            #doc="",
        ))

    has_output_file = False
    for opt in options:
        # Use long-or-short option flag,
        #   argcount=0, # 0 or 1; whether there is an argument following the option
        #   value=False # default value; False if argcount=0; or if argcount=1, the
        #               # default value if given in help text, or None if not given
        #               # / there isn't one.
        option_flag = opt.long or opt.short
        if opt.long in ("--help", "-help", "--version", "-version") and opt.argcount == 0:
            # No help or version options in the WDL task; you'd use meta{} instead
            continue
        elif opt.long in ("--output", "-output") and opt.argcount == 1:
            # This variable will also be used in the WDL task's `output` section
            name = "output_file_name"
            has_output_file = True
        else:
            name = (option_flag.lstrip('-').replace('-', '_')
                    .replace('.', '_').replace('[', '').replace(']', ''))
        arg = Argument(
            name=name,
            wdl_type="String",
            #is_array=False,
            is_required=False,
            option_flag=option_flag,
            option_has_value=(opt.argcount == 1),
            #doc="",
        )
        if opt.value is not None and opt.argcount == 1:
            wdl_type, default = type_and_default(opt.value)
            if default is not None:
                arg.default_value = str(default)
                arg.is_required = True  # Otherwise redundant / undefined behavior
                arg.wdl_type = wdl_type
        cli_args.append(arg)

    return title, cli_prefix, cli_args, has_output_file


def type_and_default(value):
    try:
        value = ast.literal_eval(value)
    except SyntaxError:
        # The default is probably calculated by the command
        # -> no default value to apply here; treat the argument as optional instead
        wdl_type = "String"
        default = None
    except ValueError:
        # Default is a valid token, but not a number or other literal -> string'll do
        wdl_type = "String"
        default = str(value)
    else:
        default = str(value)
        wdl_type = (
                "Boolean" if isinstance(value, bool) else
                "Int" if isinstance(value, int) else
                "Float" if isinstance(value, float) else
                "String")
    return wdl_type, default


def render(title, cli_prefix, cli_args):
    """Render the WDL task template with the given values.

    Args:
        cli_prefix: str
        cli_args: str

    Return:
        out_wdl: str
    """
    env = jinja2.Environment(
        #loader=jinja2.PackageLoader("doc2wdl"),
        loader=jinja2.FileSystemLoader("."),
        autoescape=jinja2.select_autoescape(),
        lstrip_blocks=True, trim_blocks=True
    )
    template = env.get_template("task_template.wdl")
    out_wdl = template.render(title=title, cli_prefix=cli_prefix, cli_args=cli_args)
    return out_wdl



if __name__ == '__main__':
    with open(sys.argv[1]) as inf:
        #args = docopt.docopt(inf.read())
        doc = inf.read()
    usage, positionals, options = parse_doc(doc)
    print(f"Parsed {len(positionals)} positional and {len(options)} optional CLI arguments.",
          file=sys.stderr)
    title, cli_prefix, cli_args, has_output_file = transform(usage, positionals, options)
    out_wdl = render(title, cli_prefix, cli_args)
    print(out_wdl)
