"""Parse a CLI tool's help text to populate the task object model.

The help text is read as a GRAMMAR: the usage line is parsed into docopt's pattern
tree and inspected statically.  Nothing here ever matches the text against
``sys.argv`` -- doing so is what made every positional argument disappear.
"""

import itertools
import re
import sys
from dataclasses import dataclass

from . import _docopt as docopt
from .tasktree import (
    Argument,
    legal_name,
    literal_default,
    task_title,
    type_and_default,
)

# Options that describe the wrapped tool's own interface rather than its inputs.
# A WDL task expresses these through meta{} instead, so they are not task inputs.
# `-h` is included because a tool may offer no long spelling at all; `-v`/`-V` are
# not, because they mean "verbose" as often as "version".
_SKIPPED_OPTIONS = ("--help", "-help", "-h", "--version", "-version")
_OUTPUT_OPTIONS = ("--output", "-output")

#: Input name the templates reference from their `output` section.  Both
#: task_template.wdl and process_template.nf hard-code this identifier.
OUTPUT_FILE_NAME = "output_file_name"

# What may begin an options-block entry, once its indentation is stripped: the two
# alternatives of the split in upstream docopt's `parse_defaults`, copied verbatim.
# `_option_entries` decides which of the lines they match an entry starts at.
_ENTRY = re.compile(r"<\S+?>|-\S+?")

# Where textwrap, with which argparse wraps help, may break a line inside a word,
# transcribed from `textwrap.TextWrapper.wordsep_re`: after a hyphen that follows
# two letters, or a letter, a hyphen and a letter, when two letters follow it,
# optionally separated by a hyphen; and after a dash of two or more hyphens that
# follows a word or punctuation, when a word follows it.
_WRAPPED_WORD = re.compile(
    r"((?:[^\W\d]{2}|[^\W\d]-[^\W\d])-)\n(?=[^\W\d]-?[^\W\d])"
    r"|([\w!\"'&.,?]-{2,})\n(?=\w)"
)

# The symbols `parse_pattern` pads with spaces before splitting its source, copied
# verbatim, so that splitting the padded text yields the tokens it reads.
_PATTERN_SYMBOL = re.compile(r"([\[\]\(\)\|]|\.\.\.)")


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
        a list of `Positional`, and a list of docopt ``Option``, each carrying a
        ``description``: its text from the options block, or ``""`` where none
        was written.

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
    #   description # set by `_documented_options`, not by docopt
    options = _documented_options(doc)
    documented = len(options)
    positionals = _parse_positionals(printable, usage, options)
    # `_parse_positionals` appends the options named only in the synopsis, which
    # have no options-block entry and so no description.
    for option in options[documented:]:
        option.description = ""
    return usage, positionals, options


def transform(usage, positionals, options):
    """Prepare CLI values for interpolation.

    Args:
        usage: usage text, as returned by `parse`
        positionals: list of `Positional`, as returned by `parse`
        options: list of docopt ``Option``, as returned by `parse`

    Return: a dictionary of template keyword arguments, carrying

        - the task title, i.e. name
        - the usage prose, as plain text: the writer decides how its target
          language spells a comment
        - the CLI prefix, i.e. the command words the task will invoke
        - a list of `Argument`: the positionals in the order the usage line
          declares them, then the options in the order the options block does
        - has_output_file

    Magic names:

        - output_file_name: the input field, a string, created if '--output' is a
              CLI option.
        - output_file: the output field. If output_file_name was given/created,
              this is a file referring to that output, otherwise the task will use
              stdout as its output.
    """
    cli_prefix = " ".join(_command_words(usage))
    title = task_title(cli_prefix)

    # A usage line states neither a type nor a default for a positional, so the
    # shared rule decides from that alone, as it does for the argparse reader.
    positional_type, _ = type_and_default(is_positional=True)
    cli_args = [
        Argument(
            name=_identifier(pos.spellings[0]),
            value_type=positional_type,
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
        # docopt's Option.name is the long flag when there is one and the short
        # flag otherwise, which is how a short-only `-h` gets recognised too.
        option_flag = opt.name
        if option_flag in _SKIPPED_OPTIONS and opt.argcount == 0:
            continue
        if option_flag in _OUTPUT_OPTIONS and opt.argcount == 1:
            # This variable will also be used in the WDL task's `output` section
            name = OUTPUT_FILE_NAME
            has_output_file = True
        else:
            name = _identifier(option_flag)
        # The options block carries no requiredness, and the synopsis's brackets are
        # read only for positionals, so no option's input is required.  A default
        # makes the input non-optional; `Argument.is_optional` says so, and the
        # writer declares it without `?`.
        if opt.argcount == 0:
            # `Option.value` is False for a valueless flag, which is what such a
            # flag means: absent, the tool never sees it.
            value_type, default = type_and_default(declared=bool, default=opt.value)
        else:
            # It is the text of the `[default: ...]` literal, or None for none.
            value_type, default = type_and_default(default=literal_default(opt.value))
        cli_args.append(
            Argument(
                name=name,
                value_type=value_type,
                default_value=default,
                option_flag=option_flag,
                option_has_value=(opt.argcount == 1),
                doc=opt.description,
            )
        )

    return dict(
        title=title,
        # Plain text: how a target language spells a comment is the writer's business.
        usage=usage,
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

    `OUTPUT_FILE_NAME` is never returned: it belongs to the output option, `--output`
    or `-output` taking a value, whose input the templates' output sections name,
    and no other argument may take it.
    """
    name = legal_name(token.lstrip("-").strip("<>"), prefix="arg_")
    return name + "_" if name == OUTPUT_FILE_NAME else name


def _parse_positionals(printable, usage, options):
    """Read the positional arguments out of the usage line's pattern tree.

    `printable` must still carry its `usage:` marker: `formal_usage` drops the first
    token of what it is given and treats the next as the program name, so passing
    the marker-stripped `usage` instead makes it eat the program name and lose the
    first positional.  The two arguments are not interchangeable.

    A pattern that does not parse is read once more with the brackets around each
    option's value removed, as `_without_value_brackets` describes.  The retry runs
    on a copy of the options-block entries, and `options` takes the options it
    appends only when it succeeds, so a usage line that parses neither way leaves
    `options` exactly as the first attempt did, and the warning reports that
    attempt's error.  A usage line that parses as written is never rewritten.
    """
    entries = options[:]
    formal = docopt.formal_usage(printable)
    try:
        pattern = docopt.parse_pattern(formal, options)
    except docopt.DocoptLanguageError as why:
        try:
            pattern = docopt.parse_pattern(
                _without_value_brackets(formal, entries), entries
            )
        except docopt.DocoptLanguageError:
            print(
                "Warning: could not read positional arguments from the usage line:",
                why,
                file=sys.stderr,
            )
            return []
        options[:] = entries
    return _positionals_of(pattern, set(_command_words(usage)))


def _without_value_brackets(formal, options):
    """Remove the brackets around an option's value: ``-p [N]`` becomes ``-p N``.

    argparse writes the value of an ``nargs="?"`` or ``nargs="*"`` option in
    brackets, as in ``[-p [PROCESSES]]`` and ``[-n [FILES ...]]``, while the
    options block declares the option as taking a value.  `parse_shorts` and
    `parse_long` then read the opening bracket as that value, and the group's
    closing bracket ends an enclosing group early.  The brackets are found among
    `parse_pattern`'s own tokens rather than in the text, because a tool may write
    them with no space after the option, as git writes ``[-u[<mode>]]``.  Only a
    group holding one placeholder, optionally followed by ``...``, loses its
    brackets; any other group after such an option stays as written.
    """
    tokens = _PATTERN_SYMBOL.sub(r" \1 ", formal).split()
    # A copy, so that the caller's list, which `parse_pattern` then appends to,
    # receives no option from this scan.  The scan registers long options only, so
    # sharing the list would place every long option the synopsis alone names ahead
    # of every such short option, rather than in the order the synopsis gives.
    known = options[:]
    removed = set()
    index = 0
    while index < len(tokens):
        token = tokens[index]
        index += 1
        if not _takes_next_token(token, known):
            continue
        closing = _placeholder_group(tokens, index)
        if closing is not None:
            removed.update((index, closing))
            index += 1
        # The token the option reads as its value is never an option itself.
        index += 1
    return " ".join(token for i, token in enumerate(tokens) if i not in removed)


def _takes_next_token(token, known):
    """Would `parse_long` or `parse_shorts` read the next token as this one's value?

    The rules are theirs as `parse_pattern` runs them: a long option takes the next
    token when the token carries no ``=`` and the option of exactly its spelling
    takes a value; a stack of short options takes it when the first of its letters
    that takes a value is its last letter, since any letters after that one are the
    value.  `parse_long` registers a long option it does not know with the argument
    count its token shows, which decides how a later occurrence reads, so this
    function appends it to `known` in the same way.  `parse_shorts` registers an
    unknown short option as taking no value, which can never make it take the next
    token, so that registration is left out.  A spelling declared twice is not told
    apart, because both functions then raise and the retry fails however the
    spelling is read.
    """
    if token.startswith("--") and token != "--":
        long, eq, _value = token.partition("=")
        similar = [option for option in known if option.long == long]
        if not similar:
            known.append(docopt.Option(None, long, 1 if eq == "=" else 0))
        return eq != "=" and any(option.argcount for option in similar)
    if token.startswith("-") and token not in ("-", "--"):
        letters = token.lstrip("-")
        for position, letter in enumerate(letters, 1):
            short = "-" + letter
            if any(option.argcount for option in known if option.short == short):
                return position == len(letters)
    return False


def _placeholder_group(tokens, opening):
    """Index of the ``]`` closing ``[ N ]`` or ``[ N ... ]`` at `tokens[opening]`.

    None unless the group holds exactly one placeholder, a token that is neither a
    pattern symbol nor a flag, optionally followed by ``...``.  A wider rule would
    read the first token of any group as the value: ``-p [N <x>]`` would make
    ``<x>`` required, and ``-s [-r host]`` would read ``-r`` as the value of ``-s``.
    """
    group = tokens[opening : opening + 4]
    if group[:1] != ["["] or len(group) < 3:
        return None
    placeholder = group[1]
    if placeholder.startswith("-") or _PATTERN_SYMBOL.fullmatch(placeholder):
        return None
    closing = 3 if group[2] == "..." else 2
    return opening + closing if group[closing : closing + 1] == ["]"] else None


def _documented_options(doc):
    """Read each options-block entry into a docopt ``Option`` with its description.

    `Option.parse` is given the text upstream's `parse_defaults` gave it, the option
    line from its first flag followed by the entry's later lines, joined by line
    breaks, so the flags, the argument count and a ``[default: ...]`` are read as
    upstream reads them.  Only where an entry starts and ends differs.
    """
    options = []
    for entry, description in _option_entries(doc):
        option = docopt.Option.parse("\n".join(entry))
        option.description = _joined(description)
        options.append(option)
    return options


def _option_entries(doc):
    """Yield each options-block entry's lines and the lines of its description.

    An entry starts at a line opening with a flag, never on the document's first
    line, and its first line is the option line from that flag on.  Upstream's
    `parse_defaults` started an option at every such line, so a description whose
    wrapped line begins with a flag declared an option of its own and could take
    the enclosing option's ``[default: ...]``.  Here a line opening with a flag
    starts an entry, and one opening with a ``<name>`` ends the current entry, only
    when indented less than the description column, where the help formatter aligns
    descriptions: that of the last description, until an option line with none on
    it states its own on its first wrapped line.  With no column known, every such
    line does so, as upstream's did.  Where the text after two spaces on an option
    line itself opens with a flag, as in ``-q,  --quiet``, it is the rest of the
    flags: it still begins the description, by upstream's rule, but states no
    column.  The column is carried from one section to the next, so a
    later section whose option lines are indented as deep as an earlier section's
    descriptions would read as prose; no captured help text is laid out that way.

    An entry ends at the next one or at a non-blank line indented no deeper than
    its option, so a section heading or an epilog at that depth is no part of it,
    and it runs across blank lines, since a ``[default: ...]`` may follow one.  Its
    description is the first paragraph: it starts after the first two spaces on the
    option line, upstream's rule, so where one space separates the flags from the
    text nothing on that line is description, and it ends at the first blank line.
    """
    entry, lines, describing, entry_indent, column = None, [], False, 0, None
    for number, line in enumerate(doc.split("\n")):
        head = line.lstrip(" ")
        indent = len(line) - len(head)
        starts = (
            number > 0
            and _ENTRY.match(head) is not None
            and (column is None or indent < column)
        )
        if entry is not None:
            if not starts and (not line.strip() or indent > entry_indent):
                entry.append(line)
                if not line.strip():
                    describing = False
                elif describing:
                    if not lines:
                        column = indent
                    lines.append(line)
                continue
            yield entry, lines
            entry = None
        if starts and head[0] == "-":
            entry, lines, describing, entry_indent = [head], [], True, indent
            description = head.partition("  ")[2].lstrip()
            if description:
                lines.append(description)
                if not description.startswith("-"):
                    column = len(line) - len(description)
    if entry is not None:
        yield entry, lines


def _joined(lines):
    """Join a description's lines into one, with each run of whitespace one space.

    Where argparse's wrapping can have broken a word, as `copy-number` into `copy-`
    and `number`, the lines are rejoined without a space.  A hyphen it never breaks
    after, as in `5- to 10-fold`, keeps its space, but a suspended hyphen such as
    `pre- and post-`, or a word ending in `--`, at a line end reads as a broken
    word and loses it.
    """
    text = "\n".join(" ".join(line.split()) for line in lines)
    return _WRAPPED_WORD.sub(r"\1\2", text).replace("\n", " ")


def _positionals_of(node, command_words, is_required=True, is_array=False):
    """Collect positional slots from a docopt pattern tree, in usage order.

    `command_words` names the command being wrapped.  Those words are dropped
    wherever they occur rather than sliced off the front of the usage text, because
    docopt binds `|` at sequence level: a usage line with several alternatives
    repeats the subcommand inside every one of them, and stripping only the first
    would shift every later alternative's arguments one slot out of alignment.
    """
    # Option has no `.children`, so it must be recognised before the generic arm.
    # AnyOptions is included as a guard for the reader rather than from necessity:
    # nothing populates its children on the static path, so the generic arm would
    # return an empty list for it anyway.
    if isinstance(node, (docopt.Option, docopt.AnyOptions)):
        return []
    if isinstance(node, docopt.Command) and node.name in command_words:
        return []
    # Command subclasses Argument, so this arm covers both remaining leaf kinds.
    if isinstance(node, docopt.Argument):
        return [Positional([node.name], is_required=is_required, is_array=is_array)]
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
