"""Parse a CLI tool's help text to populate the task object model.

The help text is read as a GRAMMAR: the usage line is parsed into docopt's pattern
tree and inspected statically.  Nothing here ever matches the text against
``sys.argv`` -- doing so is what made every positional argument disappear.
"""

import itertools
import re
import sys
from dataclasses import dataclass
from typing import NamedTuple

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

# A placeholder followed by a repetition of itself, as argparse writes the value of
# an `nargs="+"` option, `NORMAL [NORMAL ...]`, in an options-block entry's flags.
# The placeholder may be attached to its flag with `=`, as in `--normal=N [N ...]`.
_REPEATED_VALUE = re.compile(r"(?<![^\s=])([^\s\[\]-]\S*) \[\1 \.\.\.\]")

# A bracketed optional part inside a flag's name, as in `--region[s]-file`, and
# what a flag is: one or two hyphens and a name of word characters, which may hold
# `.`, `@`, `+` and hyphens, as `--disable-@files`, `-nostdinc++` and gcc's `-I-`
# do, but may not end in `.`, which ends a sentence or opens Nextflow's `-e.key`; or
# one of the characters `%`, `,` and `?`, as in BSD ls's `-%` and `-,`.
# `_passable_flag` reads them.
_OPTIONAL_PART = re.compile(r"\[[A-Za-z0-9-]+\](?=[\w-])")
_PLAIN_FLAG = re.compile(r"--?(?:[\w@](?:[\w.@+-]*[\w@+-])?|[%,?])")

# A short and a long spelling of one flag joined by a bar, as SRA Toolkit and npm
# write `-A|--accession`, and a flag followed by an ellipsis, as clap writes a
# counted flag, `--verbose...`.  `_documented_options` reads the first as
# `-A, --accession` and the second as `--verbose`.
_FLAG_ALTERNATIVE = re.compile(r"((?<![^\s,])-\w)\|(?=--\w)")
_FLAG_REPETITION = re.compile(r"(?<![^\s,])(-[^\s,|]*?[\w\]])\.\.\.(?=[\s,]|$)")

# A line of a description that names the type of the option's value as a bullet,
# as bedtools writes `- FLOAT (e.g. 0.50)` and `- (INTEGER)` under an option whose
# flags show no placeholder: the type word in capitals, or in parentheses in any
# case, so that `- Integer overflow is trapped.` is prose.  `_value_kind` reads it.
_TYPE_NAMES = "INT|INTEGER|FLOAT|STRING"
_TYPE_BULLET = re.compile(rf"-\s+(?:\((?i:{_TYPE_NAMES})\)|(?:{_TYPE_NAMES})(?![\w-]))")
_TYPE_WORD = re.compile(rf"\b({_TYPE_NAMES})\b", re.I)

# The `or:` that opens each further synopsis line in GNU help text.  `formal_usage`
# splits alternatives at the program name, so this token would otherwise end the
# previous alternative as a bare word, and so as a positional named `or`.
_CONTINUATION = re.compile(r"^[ \t]*or:(?=[ \t]|$)", re.IGNORECASE | re.MULTILINE)

# Usage notation: a bracket, a parenthesis, a brace, an angle bracket, a bar, an
# ellipsis, or a token opening with a hyphen.  `_synopsis` reads a line of the usage
# section that carries none of it as prose or a heading, not as a wrapped synopsis.
_NOTATION = re.compile(r"[\[\](){}<>|]|\.\.\.|(?<!\S)-")

# A choice among literal words, as argparse writes the values of a positional
# declared with `choices=`, and so the subcommands of a subparsers action, as in
# `{batch,target,...}`.  `_read_command_line` reads such a slot as no input.
_CHOICE_GROUP = re.compile(r"\{[^{}]*\}")


class UsageNotFound(Exception):
    """The help text has no single ``usage:`` section to read a command line from."""


@dataclass
class Positional:
    """One positional argument, with every spelling the usage line allows for it.

    A usage line may offer alternatives for the same slot, as samtools does with
    ``<in.bam>|<in.sam>|<in.cram>``.  That is one input accepting three file
    formats, not three inputs, so the spellings are collected here and the first
    one names the generated input.  The description is the text of the
    positional's own entry in the help text, or ``""`` where it has none.
    """

    spellings: list[str]
    is_required: bool = True
    is_array: bool = False
    description: str = ""


class ParsedHelp(NamedTuple):
    """What `parse` reads from a help text, and what `transform` takes."""

    #: The synopsis `_synopsis` finds, with the leading ``usage:`` marker removed.
    usage: str
    #: The program name and the command words after it that name the command the
    #: task invokes.
    cli_prefix: str
    #: Each `Positional`, with the description `_positional_descriptions` finds.
    positionals: list[Positional]
    #: Each docopt ``Option``, carrying a ``description``: its text from the options
    #: block, or ``""`` where none was written.
    options: list[docopt.Option]


def parse(doc):
    """Parse the given help text into data structures.

    Args:
        doc: the full ``--help`` output of some command-line tool.

    Return:
        A `ParsedHelp`.

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

    if len(printable.split(None, 1)) < 2:
        raise UsageNotFound("the usage section is empty.")
    synopsis = _synopsis(doc, printable)
    printable = "\n".join(printable.split("\n")[: len(synopsis)])
    usage = printable.split(None, 1)[1]
    # Option attributes:
    #   short=None, # string, e.g. -s
    #   long=None,  # string, e.g. --sample
    #   argcount=0, # 0 or 1; whether there is an argument following the option
    #   value=False # default value; False if argcount=0; or if argcount=1, the
    #               # default value if given in help text, or None if not given
    #               # / there isn't one.
    #   description # set by `_documented_options`, not by docopt
    #   repeated    # likewise: the placeholder an entry repeats, as in
    #               # `-n NORMAL [NORMAL ...]`, or None
    #   declared_type
    #               # likewise: for an option with argcount 0, the type its
    #               # description states it takes a value of, as `_value_kind`
    #               # reads it, or None
    #   attached    # likewise: whether the entry writes the flag with its value
    #               # attached by `=`, as in `--time=WORD`
    options = _documented_options(doc, synopsis)
    documented = len(options)
    cli_prefix, positionals = _read_command_line(printable, usage, options)
    # `_read_command_line` appends the options named only in the synopsis, which
    # have no options-block entry and so no description.
    for option in options[documented:]:
        option.description = ""
        option.repeated = None
        option.declared_type = None
        option.attached = False
    _positional_descriptions(doc, synopsis, positionals)
    return ParsedHelp(usage, cli_prefix, positionals, options)


def _synopsis(doc, printable):
    """The numbers of the lines of `doc` that hold the synopsis.

    `printable` is the usage section, which `printable_usage` reads from the marker
    to the first blank line, so an options block or prose written directly under
    the synopsis is part of it.  The synopsis ends at the first later line that is
    indented less than the program name, where the marker's line names it, since a
    wrapped synopsis line is aligned under the program name or deeper, as argparse
    aligns it, while a section heading, an options block or prose is not; a line
    opening with GNU's ``or:`` continues the synopsis however it is indented.  It
    also ends at a line opening with a flag whose description follows two spaces
    later, as `_option_entries` reads an option line.  Indentation counts the
    columns a tab occupies on screen.  The program name's column is measured with
    `str.expandtabs` rather than `_expanded`, which would move it a stop further
    where a tab follows text ending one column before a stop.

    Where the marker stands alone on its line, nothing states that column, and pip
    wraps its synopsis at column 0, so indentation ends nothing.  There a line also
    ends the synopsis when it opens with a word other than the program name, not
    written in capitals, and carries no usage notation, as prose or a heading such
    as ``Options:`` does.  A wrapped synopsis line carries notation, and a further
    alternative opens with the program name.  A word in capitals may be a
    placeholder, which argparse wraps onto a line of its own, so it ends nothing.
    """
    # `printable_usage` has found exactly one marker, spelled as it spells it.
    marker = re.search(r"[Uu][Ss][Aa][Gg][Ee]:", doc)
    start = doc.rfind("\n", 0, marker.start()) + 1
    first = doc.count("\n", 0, start)
    line = doc[start:].split("\n", 1)[0]
    named = line[marker.end() - start :].lstrip()
    column = len(line[: len(line) - len(named)].expandtabs(8)) if named else None
    program = printable.split()[1]
    lines = printable.split("\n")
    for number in range(1 if named else 2, len(lines)):
        if _CONTINUATION.match(lines[number]):
            continue
        expanded = _expanded(lines[number])
        head = expanded.lstrip(" ")
        if column is not None and len(expanded) - len(head) < column:
            break
        if head[:1] == "-" and _ENTRY.match(head) and head.partition("  ")[2].strip():
            break
        word = head.partition(" ")[0]
        if (
            not named
            and word != program
            and _is_command_word(word)
            and not _NOTATION.search(head)
        ):
            break
    else:
        number = len(lines)
    return range(first, first + number)


def transform(usage, cli_prefix, positionals, options):
    """Prepare CLI values for interpolation.

    Args:
        usage: usage text, as returned by `parse`
        cli_prefix: the command words the task invokes, as returned by `parse`
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
            doc=_positional_doc(pos),
        )
        for pos in positionals
    ]

    # docopt's Option.name is the long flag when there is one and the short flag
    # otherwise, which is how a short-only `-h` gets recognized too.
    flags = [_passable_flag(opt.name) for opt in options]
    passing = _one_per_flag(options, flags)
    has_output_file = False
    for opt, option_flag in zip(options, flags):
        if option_flag is None:
            print(
                f"Warning: the option '{opt.name}' is written in a notation that "
                "names no single flag, so the task leaves it out.",
                file=sys.stderr,
            )
            continue
        if _is_skipped(option_flag, opt):
            continue
        if passing[option_flag] is not opt:
            print(
                f"Warning: the option '{opt.name}' passes '{option_flag}', which "
                "another option also passes, so the task leaves it out.",
                file=sys.stderr,
            )
            continue
        # A repeated placeholder is argparse's `nargs="+"`: one flag, then the list.
        is_array = opt.repeated is not None
        takes_value = _takes_value(opt)
        # The templates' output sections name one file, so an output option that
        # takes a list is an ordinary input.
        if option_flag in _OUTPUT_OPTIONS and takes_value and not is_array:
            # This variable will also be used in the WDL task's `output` section
            name = OUTPUT_FILE_NAME
            has_output_file = True
        else:
            name = _identifier(option_flag)
        # The options block carries no requiredness, and the synopsis's brackets are
        # read only for positionals, so no option's input is required.  A default
        # makes the input non-optional; `Argument.is_optional` says so, and the
        # writer declares it without `?`.
        if not takes_value:
            # `Option.value` is False for a valueless flag, which is what such a
            # flag means: absent, the tool never sees it.
            value_type, default = type_and_default(declared=bool, default=opt.value)
        elif opt.declared_type is not None:
            # Only the description says this flag takes a value, and `Option.parse`
            # reads a `[default: ...]` only for a flag with a placeholder.
            value_type, default = type_and_default(declared=opt.declared_type)
        else:
            # It is the text of the `[default: ...]` literal, or None for none.
            value_type, default = type_and_default(
                default=literal_default(opt.value), is_array=is_array
            )
        cli_args.append(
            Argument(
                name=name,
                value_type=value_type,
                is_array=is_array,
                default_value=default,
                option_flag=option_flag,
                option_has_value=takes_value,
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


def _positional_doc(position):
    """A positional's description, then the other spellings it accepts, if any.

    The two are separate lines, as the argparse reader separates the two flags'
    texts of a paired switch, so that a description written without a full stop
    does not run into the list.
    """
    parts = [position.description] if position.description else []
    if len(position.spellings) > 1:
        parts.append("Also accepts: " + ", ".join(position.spellings[1:]))
    return "\n".join(parts)


def _takes_value(option):
    """Does the flag take a value, by its placeholder or by its description?

    The description's evidence, which `_value_kind` reads, is used here and not in
    `parse`, where the option's argument count decides how the synopsis is read:
    there a flag given a value would take the next token of the synopsis as it,
    which degrades bedtools slop's `[-b <int> or (-l and -r)]`, as `-r` would take
    the closing parenthesis.
    """
    return option.argcount == 1 or option.declared_type is not None


def _is_skipped(flag, option):
    """Is this a valueless help or version flag, which no task input passes?"""
    return flag in _SKIPPED_OPTIONS and not _takes_value(option)


def _one_per_flag(options, flags):
    """The option that passes each flag, keyed by the flag, one option for each.

    Two options may pass one flag: an option documented twice, as gcc documents
    ``-flto`` both as a switch and as ``-flto=<value>``, and an option the synopsis
    alone names whose spelling the options block writes otherwise, as git's
    synopsis names ``--dry-run`` beside the documented ``--[no-]dry-run``.  Among
    the options passing a flag, one with a description wins over one without, and
    among those, one that does not take a value attached by ``=`` wins over one that
    does; where these leave a tie, the first wins.  A line of prose read as an entry
    has no description, and the options the synopsis alone names follow every
    documented one and have none.  A flag written both bare and with an attached
    value takes that value optionally, and a task passes a value after a space,
    which such a tool reads as an argument of its own: clang reads ``-flto full`` as
    an input file.  So the switch is the one form of the two a task can pass.

    An entry naming a fixed value of another entry's flag, as GNU ls documents
    ``-p, --indicator-style=slash`` beside ``--indicator-style=WORD``, passes the
    same long flag and is left out like any other: passing its short flag instead
    would keep the value its entry reads, and ``ls -p slash`` names a file.
    `flags` holds each option's passable flag, None where it has none; such options
    and skipped ones take part in no choice.  Options passing different flags are
    never merged, even where their input names would coincide.
    """
    chosen = {}
    for option, flag in zip(options, flags):
        if flag is None or _is_skipped(flag, option):
            continue
        if flag not in chosen or _preference(option) > _preference(chosen[flag]):
            chosen[flag] = option
    return chosen


def _preference(option):
    """How `_one_per_flag` ranks the options passing one flag, higher first."""
    return bool(option.description), not (option.attached and _takes_value(option))


def _is_command_word(token):
    """Is this usage token a literal command word, rather than an argument?"""
    return bool(token) and token[0].isalnum() and not token.isupper()


def _command_words(usage):
    """The command words at the head of a usage line, e.g. ``['samtools', 'view']``.

    This is the reading of the text alone.  The first token is the program name
    whatever it looks like; the tokens after it are command words only for as long
    as they look like literal words rather than arguments, so an option group ends
    the run.  `_read_command_line` keeps only the words the pattern tree confirms.
    """
    tokens = usage.splitlines()[0].split()
    return tokens[:1] + list(itertools.takewhile(_is_command_word, tokens[1:]))


def _passable_flag(flag):
    """The flag a task passes for an option's documented spelling, or None.

    Help text may write an optional part of a flag's name in brackets, as samtools
    writes ``--region[s]-file`` and git ``--[no-]verbose``, and docopt keeps the
    brackets as part of the flag, which no tool accepts.  A bracketed group of
    letters, digits and hyphens that is followed by more of the name is removed,
    which leaves the shortest spelling the notation names, so ``--[no-]verbose``
    becomes ``--verbose`` rather than its negation.  Any other bracket is not
    read: a group that ends the flag, as in ``--color[=WHEN]``, makes its value
    optional and attached, which a task cannot express, and perl's
    ``-[mM][-]module`` is a set of letters rather than an optional part.  Such an
    option has no flag to pass.

    Every flag, bracketed or not, must then be a flag as `_PLAIN_FLAG` describes,
    so other notation names no flag either: absl's trailing colon, as in bq's
    ``--apilog:``, whose value bq reads only when attached with ``=``; a placeholder
    in the name, as in gcc's ``-W<warning>`` and zstd's ``-#``; and text that is no
    flag at all, such as the end-of-options marker ``--`` or a ruler of hyphens.
    """
    plain = _OPTIONAL_PART.sub("", flag)
    return plain if _PLAIN_FLAG.fullmatch(plain) else None


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


def _read_command_line(printable, usage, options):
    """Read the command words and positional arguments out of the usage line's tree.

    `printable` must still carry its `usage:` marker: `formal_usage` drops the first
    token of what it is given and treats the next as the program name, so passing
    the marker-stripped `usage` instead makes it eat the program name and lose the
    first positional.  The two arguments are not interchangeable.

    The command words are those of `_command_words` that the first alternative's
    tree also begins with, as `_lead_commands` finds them.  The same number of
    leading words is dropped from every alternative by position, never by name: a
    later argument may share a command word's name, and an alternative that names
    another subcommand, such as ``git show`` after ``git log``, would otherwise keep
    that word and shift its arguments one slot out of alignment.  Such an
    alternative produces a warning, since the task can wrap only one command.

    A flag that the options block does not document but that the synopsis always
    writes directly before a placeholder, as bedtools writes ``-a <bed>``, takes
    that placeholder as its value, as `_synopsis_valued_options` describes.  Where
    the synopsis does not parse with those options, it is read without them, so a
    usage line that parses neither way is read exactly as it would be without the
    rule.  The options the synopsis alone names follow the documented ones in the
    order the tree first names them, and an option the tree never names, because a
    value-taking option before it took it as its value, is dropped.

    A usage line that parses as written is not rewritten, except that the
    repetition `_without_value_repeats` describes is removed beforehand; one that
    does not is read once more as `_read_pattern` describes.  With no tree to
    confirm them, the command words are those of the text alone.
    """
    words = _command_words(usage)
    documented = len(options)
    formal = _without_value_repeats(
        docopt.formal_usage(_CONTINUATION.sub("", printable)), options
    )
    valued = _synopsis_valued_options(formal, options)
    readings = [[*options, *valued], options] if valued else [options]
    for reading in readings:
        try:
            pattern = _read_pattern(formal, reading)
        except docopt.DocoptLanguageError as error:
            why = error
        else:
            break
    else:
        print(
            "Warning: could not read positional arguments from the usage line:",
            why,
            file=sys.stderr,
        )
        return " ".join(words), []
    first = {}
    for index, leaf in enumerate(pattern.flat(docopt.Option)):
        first.setdefault((leaf.short, leaf.long), index)
    options[:] = reading[:documented] + sorted(
        (opt for opt in reading[documented:] if (opt.short, opt.long) in first),
        key=lambda opt: first[opt.short, opt.long],
    )
    # `formal_usage` wraps each line of the synopsis in parentheses and joins the
    # lines with `|`, so the tree's one child is either an Either of the lines or
    # the single line itself.  A synopsis that closes and reopens a parenthesis, as
    # in `prog a) (b`, gives the tree several children, read as one alternative.
    top = pattern.children[0] if len(pattern.children) == 1 else pattern
    leads = [
        _lead_commands(alternative)
        for alternative in (top.children if isinstance(top, docopt.Either) else [top])
    ]
    count = 0
    for word, node in zip(words[1:], leads[0]):
        if word != node.name:
            break
        count += 1
    confirmed = words[1 : 1 + count]
    prefix = " ".join(words[:1] + confirmed)
    if any([node.name for node in lead[:count]] != confirmed for lead in leads[1:]):
        print(
            "Warning: the usage line's alternatives begin with different command "
            f"words; the task wraps '{prefix}', which the first names.",
            file=sys.stderr,
        )
    dropped = {id(node) for lead in leads for node in lead[:count]}
    positionals = []
    for slot in _positionals_of(pattern, dropped):
        if all(_CHOICE_GROUP.fullmatch(spelling) for spelling in slot.spellings):
            print(
                f"Warning: the usage line writes '{slot.spellings[0]}', a choice "
                "among literal words rather than an argument, so the task leaves "
                "it out.",
                file=sys.stderr,
            )
            continue
        positionals.append(slot)
    return prefix, positionals


def _lead_commands(alternative):
    """The `Command` nodes an alternative of the usage line begins with, in order.

    Options before a command word do not end the run, so that ``prog [-v] sub <x>``
    as a later alternative still begins with ``sub``; an argument, a group holding
    one, and a token that is not a literal word do.  docopt binds ``|`` at sequence
    level, so a line such as ``samtools view [options] <in.bam>|<in.sam>`` parses as
    a choice between whole sequences, and the line's first words are in the first.
    """
    children = _sequence(alternative)
    while len(children) == 1 and isinstance(children[0], docopt.Either):
        children = _sequence(children[0].children[0])
    lead = []
    for child in children:
        if not child.flat(docopt.Argument, docopt.Command):
            continue
        if not (isinstance(child, docopt.Command) and _is_command_word(child.name)):
            break
        lead.append(child)
    return lead


def _sequence(node):
    """The nodes a pattern node consists of, in order; a leaf consists of itself.

    An alternative may be a single token, as `B` is in ``prog A) | B | (C``, which
    `formal_usage` turns into ``( A) | B | (C )``.
    """
    return node.children if isinstance(node, docopt.ParentPattern) else [node]


def _read_pattern(formal, options):
    """Parse `formal` into docopt's pattern tree, retrying once if it does not parse.

    The retry reads the text with the brackets around each option's value removed,
    as `_without_value_brackets` describes.  It runs on a copy of `options`, which
    `options` takes only when the retry succeeds, so where neither reading succeeds
    `options` holds what the first reading appended before it failed, and the
    first reading's error is raised.
    """
    entries = options[:]
    try:
        return docopt.parse_pattern(formal, options)
    except docopt.DocoptLanguageError as why:
        try:
            pattern = docopt.parse_pattern(
                _without_value_brackets(formal, entries), entries
            )
        except docopt.DocoptLanguageError:
            raise why from None
        options[:] = entries
        return pattern


def _synopsis_valued_options(formal, options):
    """The options the synopsis alone shows taking a value, as ``-a <bed>`` does.

    `parse_shorts` gives a flag the options block does not document no value, and
    `parse_long` gives one only where its token carries ``=``, so bedtools'
    ``-a <bed/gff/vcf/bam>``, which no entry documents, reads as a switch followed
    by a positional.  A flag gets a value here when no entry documents its exact
    spelling, and every token of the synopsis that spells it, a long flag without
    ``=`` or a short flag of one letter, is directly followed by a placeholder:
    a token `parse_atom` reads as an argument, angle-bracketed or in capitals.

    Because the value it takes is a token that would otherwise be a positional,
    the rule never decides whether a synopsis parses: never a bracket, a bar or a
    command word, which an option given a value before `parse_pattern` would take
    where it is followed by one, as D27 measured on slop's ``(-l and -r)``.  For
    the same reason a short flag whose letter ends a stack of letters, as ``z``
    ends ``-pz``, takes none, since the stack would take the token after it.

    A flag whose spelling matches a documented option only once the notation that
    `_passable_flag` removes is removed, as git's synopsis names ``--trailer`` and
    its entry ``--[no-]trailer <trailer>``, takes a value only where that option
    does, since `transform` passes the flag from the documented option.
    """
    tokens = _PATTERN_SYMBOL.sub(r" \1 ", formal).split()
    declared, twins = set(), {}
    for opt in options:
        for flag in (opt.short, opt.long):
            if flag:
                declared.add(flag)
                twins.setdefault(_passable_flag(flag), opt.argcount)
    followed = {}
    for token, after in zip(tokens, [*tokens[1:], ""]):
        long = token.startswith("--") and token != "--" and "=" not in token
        short = len(token) == 2 and token[0] == "-" and token[1] != "-"
        if (long or short) and token not in declared:
            followed[token] = followed.get(token, True) and _is_placeholder(after)
    stacked = {
        "-" + token[-1]
        for token in tokens
        if token.startswith("-") and not token.startswith("--") and len(token) > 2
    }
    return [
        docopt.Option(None, flag, 1)
        if flag.startswith("--")
        else docopt.Option(flag, None, 1)
        for flag, always in followed.items()
        if always and flag not in stacked and twins.get(flag, 1) == 1
    ]


def _is_placeholder(token):
    """Does `parse_atom` read this synopsis token as an argument?"""
    return not token.startswith("-") and (
        token.startswith("<") and token.endswith(">") or token.isupper()
    )


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
    removed = set()
    for _option, value in _valued_options(tokens, options):
        closing = _placeholder_group(tokens, value)
        if closing is not None:
            removed.update((value, closing))
    return " ".join(token for i, token in enumerate(tokens) if i not in removed)


def _without_value_repeats(formal, options):
    """Remove the repetition after an option's value: ``-n N [N ...]`` becomes ``-n N``.

    argparse writes an option with ``nargs="+"`` as ``-n NORMAL [NORMAL ...]``, in
    the synopsis and in the options block alike.  The option reads only ``NORMAL``
    as its value, so ``[NORMAL ...]`` would be an optional list of positionals.
    The group is removed only where the option's options-block entry repeats the
    same placeholder, as its ``repeated`` records, because BSD find writes
    ``-f path [path ...]`` for an option followed by a list of positionals.  A long
    option may also carry its value attached, as in ``--normal=N [N ...]``, which
    `parse_long` reads as one token, so the group follows that token.

    Unlike `_without_value_brackets`, this runs before the first reading, since
    the synopsis parses as written.  Removing a whole bracketed group leaves every
    other bracket matched as it was, so no synopsis that parses as written stops
    parsing.  The text is returned unchanged where no group is removed.
    """
    tokens = _PATTERN_SYMBOL.sub(r" \1 ", formal).split()
    # An option `_value_taker` registered from the synopsis has no entry and so no
    # `repeated`.
    values = [
        (getattr(option, "repeated", None), tokens[value], value)
        for option, value in _valued_options(tokens, options)
    ]
    for index, token in enumerate(tokens):
        long, _eq, attached = token.partition("=")
        if long.startswith("--") and attached:
            repeated = (option.repeated for option in options if option.long == long)
            values.append((next(repeated, None), attached, index))
    removed = set()
    for repeated, placeholder, end in values:
        if placeholder != repeated:
            continue
        if tokens[end + 1 : end + 5] == ["[", placeholder, "...", "]"]:
            removed.update(range(end + 1, end + 5))
    if not removed:
        return formal
    return " ".join(token for i, token in enumerate(tokens) if i not in removed)


def _valued_options(tokens, options):
    """Yield each option among `tokens` that takes a value, and its value's index.

    The scan runs on a copy of `options`, so that the caller's list, which
    `parse_pattern` then appends to, receives no option from it.  The scan registers
    long options only, so sharing the list would place every long option the
    synopsis alone names ahead of every such short option, rather than in the order
    the synopsis gives.  The token an option reads as its value is never an option
    itself, so the scan passes over it.
    """
    known = options[:]
    index = 0
    while index < len(tokens) - 1:
        option = _value_taker(tokens[index], known)
        index += 1
        if option is not None:
            yield option, index
            index += 1


def _value_taker(token, known):
    """The option `parse_long` or `parse_shorts` gives the next token as its value.

    None where neither function would give the next token to an option.  The rules
    are theirs as `parse_pattern` runs them: a long option takes the next token
    when the token carries no ``=`` and the option of exactly its spelling takes a
    value; a stack of short options takes it when the first of its letters that
    takes a value is its last letter, since any letters after that one are the
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
        if eq == "=":
            return None
        return next((option for option in similar if option.argcount), None)
    if token.startswith("-") and token not in ("-", "--"):
        letters = token.lstrip("-")
        for position, letter in enumerate(letters, 1):
            short = "-" + letter
            for option in known:
                if option.short == short and option.argcount:
                    return option if position == len(letters) else None
    return None


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


def _documented_options(doc, synopsis):
    """Read each options-block entry into a docopt ``Option`` with its description.

    `Option.parse` is given the text upstream's `parse_defaults` gave it, the option
    line from its first flag followed by the entry's later lines, joined by line
    breaks, so the flags, the argument count and a ``[default: ...]`` are read as
    upstream reads them.  Only where an entry starts and ends differs, and two
    spellings of flags, which `Option.parse` would read as one flag: the flags of
    ``-A|--accession`` are read as ``-A, --accession``, and ``--verbose...`` as
    ``--verbose``, which clap writes for a flag counted each time it is passed.
    Each option also records the placeholder its flags repeat, as argparse writes
    the value of an ``nargs="+"`` option as ``NORMAL [NORMAL ...]``, or None, and as
    ``attached`` whether its flags write its flag with a value attached by ``=``.
    """
    options = []
    for entry, description in _option_entries(doc, synopsis):
        flags, gap, text = entry[0].partition("  ")
        flags = _FLAG_REPETITION.sub(r"\1", _FLAG_ALTERNATIVE.sub(r"\1, ", flags))
        option = docopt.Option.parse("\n".join([flags + gap + text, *entry[1:]]))
        option.description = _joined(description)
        repeat = _REPEATED_VALUE.search(flags)
        option.repeated = repeat.group(1) if repeat else None
        option.declared_type = None if option.argcount else _value_kind(description)
        option.attached = any(
            "=" in spelling
            and spelling.partition("=")[0] in (option.short, option.long)
            for spelling in flags.replace(",", " ").split()
        )
        options.append(option)
    return options


def _value_kind(lines):
    """The type of value a description says its flag takes, or None.

    bedtools writes no placeholder after a flag that takes a value, and names the
    value's type on a line of the description of its own, as in ``- FLOAT (e.g.
    0.50)``, ``- (INTEGER)`` or ``- (Integer) or (Float, e.g. 0.1) if used with
    -pct``; the first such line decides.  The type word is in capitals or in
    parentheses, as `_TYPE_BULLET` requires.  A line naming an integer and a float
    is a float, which holds both, and one naming a string is a string.  Prose that
    merely mentions a type is not read, whether as gcc's "Trap on integer overflow"
    under a switch or as a bullet such as ``- Integer overflow is trapped.``, nor
    is a default stated in prose, which bedtools also writes under switches.
    """
    for line in lines:
        if _TYPE_BULLET.match(line.strip()):
            words = {word.lower() for word in _TYPE_WORD.findall(line)}
            if "string" in words:
                return str
            return float if "float" in words else int
    return None


def _option_entries(doc, synopsis, named=frozenset()):
    """Yield each options-block entry's lines and the lines of its description.

    An entry starts at a line opening with a flag, or with a positional's name, as
    the last paragraph describes, never on the document's first line nor on a line
    of the synopsis, whose line numbers `synopsis` holds, and its first line is
    that line from the flag or name on.  Upstream's `parse_defaults` started an
    option at every line opening with a flag, so a description whose
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

    Tabs are expanded first, as `_expanded` describes, since help text such as
    bedtools' indents its options block with a tab and separates each flag from
    its description with another.

    `named` holds identifiers of positional arguments, as `_identifier` makes them.
    A line whose first word has one of them starts an entry too, by the same rules
    as a line opening with a flag, as argparse lists ``targets`` under "positional
    arguments:"; `_positional_descriptions` reads those entries.  A line where one
    space and a letter follow that word opens a sentence instead, as GNU date's
    ``FORMAT controls the output.  Interpreted sequences are:`` does.
    """
    entry, lines, describing, entry_indent, column = None, [], False, 0, None
    for number, line in enumerate(_expanded(doc).split("\n")):
        head = line.lstrip(" ")
        indent = len(line) - len(head)
        names = named and _names_positional(head, named)
        starts = (
            number > 0
            and number not in synopsis
            and (names or _ENTRY.match(head) is not None)
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
        if starts and (names or head[0] == "-"):
            entry, lines, describing, entry_indent = [head], [], True, indent
            description = head.partition("  ")[2].lstrip()
            if description:
                lines.append(description)
                if not description.startswith("-"):
                    column = len(line) - len(description)
    if entry is not None:
        yield entry, lines


def _names_positional(line, named):
    """Does this line open with the spelling of a positional with one of these names?

    A word with no letter or digit, such as ``-`` for standard input, names none,
    since every such word would make the same identifier.  A word followed by one
    space and a letter opens a sentence rather than an entry, which writes its
    description after two spaces, or notation after one, as dos2unix's ``file ...``
    does.
    """
    word, _, rest = line.partition(" ")
    return (
        any(c.isalnum() for c in word)
        and not rest[:1].isalpha()
        and _identifier(word) in named
    )


def _positional_descriptions(doc, synopsis, positionals):
    """Give each positional the description its own entry in the help text writes.

    argparse writes ``  targets               BED or interval file ...`` under
    "positional arguments:", and other tools write ``<PATTERN>`` or ``FILE``
    entries alongside their options.  The entries are read by `_option_entries`, a
    second time, now that the positionals' names are known, and the first entry
    with a description that names a positional, by any of its spellings, describes
    it.  The options this reading finds are discarded, so the option set is the
    first reading's.
    """
    slots = {}
    for position in positionals:
        for spelling in position.spellings:
            slots.setdefault(_identifier(spelling), []).append(position)
    for entry, description in _option_entries(doc, synopsis, frozenset(slots)):
        if entry[0].startswith("-"):
            continue
        for position in slots[_identifier(entry[0].split(" ", 1)[0])]:
            if not position.description:
                position.description = _joined(description)


def _expanded(text):
    """Expand tabs to stops eight columns apart, as a terminal displays them.

    Indentation and the description column are measured in columns, so a tab must
    count as the columns it occupies on screen.  A tab that follows text and would
    advance one column advances nine, to the stop after the next, because one
    space does not separate flags from a description where `Option.parse` and
    `_option_entries` look for two: bedtools' ``\\t-sorted\\tUse ...`` would
    otherwise read the description's words as the value of ``-sorted``.
    """
    return re.sub(r"(?<=\S)\t", " \t", text).expandtabs(8)


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


def _positionals_of(node, dropped, is_required=True, is_array=False):
    """Collect positional slots from a docopt pattern tree, in usage order.

    `dropped` holds the `id` of each `Command` node that is a command word of the
    wrapped command rather than an argument to it.  Identity, not equality, decides,
    because docopt's nodes compare equal by their text: the ``sub`` that opens one
    alternative equals the ``sub`` that opens the next, and a later argument may
    share a command word's name.
    """
    # Option has no `.children`, so it must be recognized before the generic arm.
    # AnyOptions is included as a guard for the reader rather than from necessity:
    # nothing populates its children on the static path, so the generic arm would
    # return an empty list for it anyway.
    if isinstance(node, (docopt.Option, docopt.AnyOptions)):
        return []
    if isinstance(node, docopt.Command) and id(node) in dropped:
        return []
    # Command subclasses Argument, so this arm covers both remaining leaf kinds.
    if isinstance(node, docopt.Argument):
        return [Positional([node.name], is_required=is_required, is_array=is_array)]
    if isinstance(node, docopt.Either):
        return _merge_alternatives(
            [
                _positionals_of(child, dropped, is_required, is_array)
                for child in node.children
            ]
        )
    return [
        slot
        for child in node.children
        for slot in _positionals_of(
            child,
            dropped,
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
