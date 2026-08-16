"""Parse a CLI tool's help text to populate the task object model.

The help text is read as a GRAMMAR: the usage line is parsed into docopt's pattern
tree and inspected statically.  Nothing here ever matches the text against
``sys.argv`` -- doing so is what made every positional argument disappear.
"""
import itertools
import sys
from dataclasses import dataclass

from . import _docopt as docopt
from .tasktree import Argument, type_and_default

# Options that describe the wrapped tool's own interface rather than its inputs.
# A WDL task expresses these through meta{} instead, so they are not task inputs.
# `-h` is included because a tool may offer no long spelling at all; `-v`/`-V` are
# not, because they mean "verbose" as often as "version".
_SKIPPED_OPTIONS = ("--help", "-help", "-h", "--version", "-version")
_OUTPUT_OPTIONS = ("--output", "-output")

#: Input name the templates reference from their `output` section.  Both
#: task_template.wdl and process_template.nf hard-code this identifier.
OUTPUT_FILE_NAME = "output_file_name"


class UsageNotFound(Exception):
    """The help text has no single ``usage:`` section to read a command line from."""


@dataclass
class Positional:
    """One positional argument, with every spelling the usage line allows for it.

    A usage line may offer alternatives for the same slot, as samtools does with
    ``<in.bam>|<in.sam>|<in.cram>``.  That is one input accepting three file
    formats, not three inputs, so the spellings are collected here and the first
    one names the generated input.
    """

    spellings: list[str]
    is_required: bool = True
    is_array: bool = False


def parse(doc):
    """Parse the given help text into data structures.

    Args:
        doc: the full ``--help`` output of some command-line tool.

    Return:
        A tuple of the usage text (with the leading ``usage:`` marker removed),
        a list of `Positional`, and a list of docopt ``Option``.

    Raises:
        UsageNotFound: the help text has no usage section, or has several.  There
            is no command line to wrap in that case, so this is fatal.

    A usage line that is present but unparseable is NOT fatal: the options are
    still recoverable, so the positionals degrade to an empty list with a warning
    and the caller gets a partial task to finish by hand.
    """
    try:
        printable = docopt.printable_usage(doc)
    except docopt.DocoptLanguageError as why:
        raise UsageNotFound(str(why)) from why

    marked = printable.split(None, 1)
    if len(marked) < 2:
        raise UsageNotFound("the usage section is empty.")
    usage = marked[1]
    # Option attributes:
    #   short=None, # string, e.g. -s
    #   long=None,  # string, e.g. --sample
    #   argcount=0, # 0 or 1; whether there is an argument following the option
    #   value=False # default value; False if argcount=0; or if argcount=1, the
    #               # default value if given in help text, or None if not given
    #               # / there isn't one.
    options = docopt.parse_defaults(doc)
    positionals = _parse_positionals(printable, usage, options)
    return usage, positionals, options


def transform(usage, positionals, options):
    """Prepare CLI values for interpolation.

    Args:
        usage: usage text, as returned by `parse`
        positionals: list of `Positional`, as returned by `parse`
        options: list of docopt ``Option``, as returned by `parse`

    Return: a dictionary of template keyword arguments, carrying

        - the task title, i.e. name
        - the CLI prefix, i.e. the command words the task will invoke
        - a list of `Argument` in the order the usage line declares them
        - has_output_file

    Magic names:

        - output_file_name: the input field, a string, created if '--output' is a
              CLI option.
        - output_file: the output field. If output_file_name was given/created,
              this is a file referring to that output, otherwise the task will use
              stdout as its output.
    """
    cli_prefix = " ".join(_command_words(usage))
    title = cli_prefix.replace(".py", "").title().replace(" ", "").replace("-", "")
    if not title[:1].isalpha():
        # A command such as `2to3` would otherwise yield an illegal task name.
        title = "Task" + title

    cli_args = [
        Argument(
            name=_identifier(pos.spellings[0]),
            # Every positional is typed File; see the type-inference issue for why
            # this is wrong for arguments such as samtools' `region`.
            wdl_type="File",
            is_array=pos.is_array,
            is_required=pos.is_required,
            doc=(
                "Also accepts: " + ", ".join(pos.spellings[1:])
                if len(pos.spellings) > 1
                else ""
            ),
        )
        for pos in positionals
    ]

    has_output_file = False
    for opt in options:
        option_flag = opt.name
        # docopt's Option.name is the long flag when there is one and the short
        # flag otherwise, which is how a short-only `-h` gets recognised too.
        if opt.name in _SKIPPED_OPTIONS and opt.argcount == 0:
            continue
        if opt.name in _OUTPUT_OPTIONS and opt.argcount == 1:
            # This variable will also be used in the WDL task's `output` section
            name = OUTPUT_FILE_NAME
            has_output_file = True
        else:
            name = _identifier(option_flag)
        arg = Argument(
            name=name,
            wdl_type="String",
            is_required=False,
            option_flag=option_flag,
            option_has_value=(opt.argcount == 1),
        )
        if opt.value is not None and opt.argcount == 1:
            wdl_type, default = type_and_default(opt.value)
            if default is not None:
                arg.is_required = True  # Otherwise redundant / undefined behavior
                arg.default_value = default
                arg.wdl_type = wdl_type
        cli_args.append(arg)

    return dict(
        title=title,
        usage="\n".join(["# " + line for line in usage.splitlines()]),
        cli_prefix=cli_prefix,
        cli_args=cli_args,
        has_output_file=has_output_file,
    )


def _is_command_word(token):
    """Is this usage token a literal command word, rather than an argument?"""
    return bool(token) and token[0].isalnum() and not token.isupper()


def _command_words(usage):
    """The command words at the head of a usage line, e.g. ``['samtools', 'view']``.

    These name the command the task invokes, so they are the CLI prefix rather than
    arguments to it.  The first token is the program name whatever it looks like;
    the tokens after it are command words only for as long as they look like literal
    words rather than arguments.
    """
    tokens = usage.splitlines()[0].split()
    return tokens[:1] + list(itertools.takewhile(_is_command_word, tokens[1:]))


def _identifier(token):
    """Turn a usage token such as ``<in.bam>`` or ``--min-size`` into an identifier.

    Help text carries punctuation that no target language accepts in a name, and it
    is not a closed set -- `FILE(S)`, `<a/b>` and `<n,m>` all occur -- so this keeps
    what is legal rather than removing what is known to be illegal.
    """
    name = "".join(
        character
        if character.isascii() and (character.isalnum() or character == "_")
        else "_"
        if character in "-."
        else ""
        for character in token.lstrip("-").strip("<>")
    )
    # Neither WDL nor Nextflow accepts an identifier starting with a digit, which
    # a short-only numeric flag such as `-1` would otherwise produce.
    return name if name[:1].isalpha() else "_" + name


def _parse_positionals(printable, usage, options):
    """Read the positional arguments out of the usage line's pattern tree."""
    try:
        pattern = docopt.parse_pattern(docopt.formal_usage(printable), options)
    except docopt.DocoptLanguageError as why:
        print(
            "Warning: could not read positional arguments from the usage line:",
            why,
            file=sys.stderr,
        )
        return []
    return _positionals_of(pattern, set(_command_words(usage)))


def _positionals_of(node, command_words, is_required=True, is_array=False):
    """Collect positional slots from a docopt pattern tree, in usage order.

    `command_words` names the command being wrapped.  Those words are dropped
    wherever they occur rather than sliced off the front of the usage text, because
    docopt binds `|` at sequence level: a usage line with several alternatives
    repeats the subcommand inside every one of them, and stripping only the first
    would shift every later alternative's arguments one slot out of alignment.
    """
    # AnyOptions subclasses Optional, so it has to be recognised before the groups.
    if isinstance(node, (docopt.Option, docopt.AnyOptions)):
        return []
    if isinstance(node, docopt.Command) and node.name in command_words:
        return []
    # Command subclasses Argument, so this arm covers both remaining leaf kinds.
    if isinstance(node, docopt.Argument):
        return [Positional([node.name], is_required, is_array)]
    if isinstance(node, docopt.Either):
        return _merge_alternatives(
            [
                _positionals_of(child, command_words, is_required, is_array)
                for child in node.children
            ]
        )
    return [
        slot
        for child in node.children
        for slot in _positionals_of(
            child,
            command_words,
            is_required and not isinstance(node, docopt.Optional),
            is_array or isinstance(node, docopt.OneOrMore),
        )
    ]


def _merge_alternatives(branches):
    """Merge the branches of an ``|`` alternation position by position.

    docopt binds ``|`` at sequence level, so ``prog a <x> | prog <y>`` is two whole
    alternatives rather than one choice between ``<x>`` and ``<y>``.  Slots at the
    same index across branches are therefore the same argument spelled differently,
    and a slot missing from some branch cannot be mandatory.
    """
    merged = []
    for i in range(max((len(branch) for branch in branches), default=0)):
        present = [branch[i] for branch in branches if i < len(branch)]
        merged.append(
            Positional(
                spellings=list(
                    dict.fromkeys(s for slot in present for s in slot.spellings)
                ),
                is_required=(
                    len(present) == len(branches)
                    and all(slot.is_required for slot in present)
                ),
                is_array=any(slot.is_array for slot in present),
            )
        )
    return merged
