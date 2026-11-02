"""Parse a CLI tool's help text to populate the task object model.

The help text is read as a GRAMMAR: the usage line is parsed into docopt's pattern
tree and inspected statically.  Nothing here ever matches the text against
``sys.argv`` -- doing so is what made every positional argument disappear.
"""

import collections
import copy
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
# verbatim; `_tokens` splits a text as it does.
_PATTERN_SYMBOL = re.compile(r"([\[\]\(\)\|]|\.\.\.)")

# A placeholder followed by a repetition of itself, as argparse writes the value of
# an `nargs="+"` option, `NORMAL [NORMAL ...]`, in an options-block entry's flags.
# The placeholder may be attached to a long flag with `=`, as in
# `--normal=N [N ...]`, which `parse_long` reads as one token, but not to a short
# flag: `parse_shorts` gives a documented `-n` the value `=N` in `-n=N`, not the
# placeholder `N`.
_REPEATED_VALUE = re.compile(r"(?:^|\s|--[^\s=]+=)([^\s\[\]-]\S*) \[\1 \.\.\.\]")

# The bracketed list argparse writes for the value of an `nargs="*"` option, as in
# `-n, --normal [FILES ...]`.  The list must be the flag's own value, so zipfile's
# `-c, --create <name> [<file> ...]`, whose list is a further operand, is no match.
# `_documented_options` records it as `repeated`, as it does `_REPEATED_VALUE`:
# both say that one flag takes the whole list.
_REPEATED_LIST = re.compile(r"(?:^|\s)--?[^\s,=]+[ =]\[([^\s\[\]-]\S*) \.\.\.\]")

# A bracketed optional part inside a flag's name, as in `--region[s]-file`, and
# what a flag is: one or two hyphens and a name of word characters, which may hold
# `.`, `@`, `+` and hyphens, as `--disable-@files`, `-nostdinc++` and gcc's `-I-`
# do, but may not end in `.`, which ends a sentence or opens Nextflow's `-e.key`; or
# one of the characters `%`, `,` and `?`, as in BSD ls's `-%` and `-,`.
# `_passable_flag` reads them.
_OPTIONAL_PART = re.compile(r"\[[A-Za-z0-9-]+\](?=[\w-])")
_PLAIN_FLAG = re.compile(r"--?(?:[\w@](?:[\w.@+-]*[\w@+-])?|[%,?])")

# A value a short flag writes optional and attached, as GNU date documents
# `-I[FMT]` and git `-S[<keyid>]`, or, unclosed, what `Option.parse` keeps of ruby's
# `-W[level=2]`, since it splits flags at `=`.  `_without_optional_value` reads it.
_OPTIONAL_VALUE = re.compile(r"\[[^\[\]]*\]?$")

# A bracketed group that opens a word and is written directly before the rest of
# it, as git writes `--fixup [(amend|reword):]<commit>`: notation for an optional
# prefix of a value, as `_OPTIONAL_PART` is notation inside a flag's name, rather
# than a group of the pattern.  One level of parentheses may be nested in it, so
# the text it matches is balanced.  `_without_value_prefixes` reads it.
_VALUE_PREFIX = re.compile(r"(?<=\s)\[(?:[^\[\]()]|\([^()]*\))*\](?=\S)")

# A value a synopsis writes optional and attached to a one-letter short flag, as
# BSD grep writes `[-C[num]]` and git `[-u[<mode>]]`.  Only the text shows that the
# group is attached, as it shows an optional prefix, so `_without_attached_shorts`
# reads it there.  A flag of several letters is left out: BSD sort's
# `-kPOS1[,POS2]` and Info-ZIP's `-opts[modifiers]` attach the group to a value.
_ATTACHED_SHORT = re.compile(r"(?<![-\w])(-[^-\s\[\]()|])\[[^\s\[\]|]+\](?![\w\[])")

# A short and a long spelling of one flag joined by a bar, as SRA Toolkit and npm
# write `-A|--accession`, and a flag followed by an ellipsis, as clap writes a
# counted flag, `--verbose...`.  `_documented_options` reads the first as
# `-A, --accession` and the second as `--verbose`.
_FLAG_ALTERNATIVE = re.compile(r"((?<![^\s,])-\w)\|(?=--\w)")
_FLAG_REPETITION = re.compile(r"(?<![^\s,])(-[^\s,|]*?[\w\]])\.\.\.(?=[\s,]|$)")

# The slash click writes between the spellings of a Boolean flag's two values, as in
# `--color / --no-color` and `--fast / --safe`.  `_entry_spellings` reads what
# precedes it as the flag for true and what follows it as the flag for false.
_FLAG_SENSES = re.compile(r"(?<=[\w\]]) / (?=-)")

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
    #               # `-n NORMAL [NORMAL ...]` and `-n [FILES ...]`, or None
    #   declared_type
    #               # likewise: for an option with argcount 0, the type its
    #               # description states it takes a value of, as `_value_kind`
    #               # reads it, or None
    #   attached    # likewise: whether the entry writes the flag with its value
    #               # attached by `=`, as in `--time=WORD`
    #   other_spellings
    #               # likewise: the long flags the entry writes beside the one the
    #               # option carries, which the synopsis may name instead, as
    #               # `_entry_spellings` reads them
    #   first_long  # likewise: the first long flag the entry writes for the
    #               # option, other than a flag for false, or "" if it writes
    #               # none; argparse derives `dest` from this flag, and the input
    #               # is named after it
    #   false_flag  # likewise: the flag a switch passes for false, as in
    #               # `--color / --no-color`, or ""
    #   value_optional
    #               # likewise: whether the entry writes the flag both bare and
    #               # with a value attached, as in `--check, --check=WORD`, or
    #               # brackets the value at the end of the flag, `--color[=WHEN]`
    #   spelled     # set by `_single_dash_spellings` on an option it makes for
    #               # a single-dash flag no entry documents, whose long spelling
    #               # `_spelled_single_dash` removes once the synopsis is read
    #   repeats_flag
    #               # set here: whether the synopsis repeats the option itself
    #               # with `...`, as in `-e PATTERN ...`, so that its flag
    #               # precedes each value, as `_repeated_options` reads it
    options = _documented_options(doc, synopsis)
    documented = len(options)
    cli_prefix, positionals, repeating = _read_command_line(printable, usage, options)
    # `_read_command_line` appends the options named only in the synopsis, which
    # have no options-block entry and so no description.
    for option in options[documented:]:
        _undocumented(option)
    for option in options:
        option.repeats_flag = _passable_flag(option.name) in repeating
    _positional_descriptions(doc, synopsis, positionals)
    return ParsedHelp(usage, cli_prefix, positionals, options)


def _undocumented(option):
    """Annotate an option that no options-block entry documents, and return it.

    `_documented_options` reads every annotation `parse` lists from an entry, so
    an option that `parse_pattern` appends, or that `_single_dash_spellings` makes
    for a flag the synopsis alone shows, carries none.  The one made there is
    annotated when it is made, since `_without_value_repeats` reads ``repeated``
    from the options it reads a synopsis with.
    """
    option.description = ""
    option.repeated = None
    option.declared_type = None
    option.attached = False
    option.other_spellings = ()
    option.first_long = ""
    option.false_flag = ""
    option.value_optional = False
    return option


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

    Args: the fields of the `ParsedHelp` that `parse` returns, in its order, so
        that ``transform(*parse(doc))`` reads a help text.

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
        takes_value = _takes_value(opt)
        # A repeated placeholder in the entry is argparse's `nargs="+"` or
        # `nargs="*"`: one flag takes the whole list.  A `...` in the synopsis over
        # the option repeats the flag instead, once before each value.  The entry
        # states how many values one occurrence takes, so it wins.
        flag_per_value = opt.repeats_flag and takes_value and opt.repeated is None
        is_array = opt.repeated is not None or flag_per_value
        # The templates' output sections name one file, so an output option that
        # takes a list is an ordinary input.
        if option_flag in _OUTPUT_OPTIONS and takes_value and not is_array:
            # This variable will also be used in the WDL task's `output` section
            name = OUTPUT_FILE_NAME
            has_output_file = True
        else:
            # argparse derives `dest` from the first long flag, and the argparse
            # reader names an input after `dest`, so the first long flag names it
            # here too; the flag passed is still the one the option carries.
            name = _identifier(_passable_flag(opt.first_long) or option_flag)
        # A switch with a flag for false has a third state, passing neither, and help
        # text states no default for it.
        false_flag = "" if takes_value else _passable_flag(opt.false_flag) or ""

        # The options block carries no requiredness, and the synopsis's brackets are
        # read only for positionals, so no option's input is required.  A default
        # makes the input non-optional; `Argument.is_optional` says so, and the
        # writer declares it without `?`.
        if not takes_value:
            # Absent, a valueless flag is never seen by the tool.  The default is
            # not `opt.value`, which for a `value_optional` flag holds the attached
            # declaration's `[default: ...]`.
            value_type, default = type_and_default(
                declared=bool, default=None if false_flag else False
            )
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
                false_flag=false_flag,
                option_has_value=takes_value,
                flag_per_value=flag_per_value,
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
    the closing parenthesis.  An entry that writes its flag both bare and with a
    value attached, as ``value_optional`` records, takes none.  `Option.parse`
    still reads its argument count from the attached declaration, so that a
    synopsis writing ``--check=WORD`` parses: the text with that declaration
    dropped would give argument count 0, which `parse_long` rejects with a value.
    """
    return (
        option.argcount == 1 or option.declared_type is not None
    ) and not option.value_optional


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


def _without_optional_value(flag):
    """`flag` without a value it writes optional and attached, or `flag` itself.

    An entry writes such a value in brackets at the end of the flag, as GNU ls
    documents ``--color[=WHEN]`` and GNU date ``-I[FMT]``.  `Option.parse` splits
    an entry's flags at ``=``, so what it stores of the long form is the name with
    a trailing ``[``, which nothing else writes.  A short flag usually attaches its
    value with no ``=``, so the group is stored whole, but ruby's ``-W[level=2]``
    is split too, leaving ``-W[level``, so the closing bracket is optional.  A
    group holding no ``=`` after a long flag is therefore no value: ruby's
    ``--jit-[option]`` names an optional part of the name, which `_OPTIONAL_PART`
    reads only where more of the name follows it, and ``--jit-`` is no flag.
    """
    if flag.startswith("--"):
        return flag.removesuffix("[")
    return _OPTIONAL_VALUE.sub("", flag)


def _passable_flag(flag):
    """The flag a task passes for an option's documented spelling, or None.

    Help text may write an optional part of a flag's name in brackets, as samtools
    writes ``--region[s]-file`` and git ``--[no-]verbose``, and docopt keeps the
    brackets as part of the flag, which no tool accepts.  A bracketed group of
    letters, digits and hyphens that is followed by more of the name is removed,
    which leaves the shortest spelling the notation names, so ``--[no-]verbose``
    becomes ``--verbose`` rather than its negation.  A group that ends the flag
    writes its value optional and attached, as in ``--color[=WHEN]`` and
    ``-I[FMT]``, and `_without_optional_value` removes it, leaving the bare flag,
    which `_takes_value` reads as the switch it is: the one form of the two a task
    can pass, as `_entry_spellings` reads ``--check, --check=WORD``.  Any other
    bracket is not read, so perl's ``-[mM][-]module``, a set of letters rather
    than an optional part, has no flag to pass.

    Every flag, bracketed or not, must then be a flag as `_PLAIN_FLAG` describes,
    so other notation names no flag either: absl's trailing colon, as in bq's
    ``--apilog:``, whose value bq reads only when attached with ``=``; a placeholder
    in the name, as in gcc's ``-W<warning>`` and zstd's ``-#``; and text that is no
    flag at all, such as the end-of-options marker ``--`` or a ruler of hyphens.
    """
    plain = _without_optional_value(_OPTIONAL_PART.sub("", flag))
    return plain if _PLAIN_FLAG.fullmatch(plain) else None


def _identifier(token):
    """Turn a usage token such as ``<in.bam>`` or ``--min-size`` into an identifier.

    Help text carries punctuation that no target language accepts in a name, and
    `legal_name` decides what becomes of it.

    `OUTPUT_FILE_NAME` is never returned: it belongs to the output option, `--output`
    or `-output` taking a value, whose input the templates' output sections name,
    and no other argument may take it.
    """
    name = legal_name(token, prefix="arg_")
    return name + "_" if name == OUTPUT_FILE_NAME else name


def _read_command_line(printable, usage, options):
    """Read the command words and positional arguments out of the usage line's tree.

    Return the CLI prefix, the positionals, and the flags, as `_passable_flag` gives
    them, of the options the tree repeats, as `_repeated_options` reads them.  The
    flag rather than the option is returned, so that the repetition reaches the
    option `transform` passes it from where the synopsis spells it otherwise, as
    git's synopsis writes ``--trailer`` and its entry ``--[no-]trailer``.

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
    that placeholder as its value, as `_synopsis_valued_options` describes, and so
    does a documented switch the synopsis always writes so, as
    `_synopsis_valued_switches` describes; such a switch then takes a value in
    `options` too.  Where the synopsis does not parse with those values, it is read
    without them, so a usage line that parses neither way is read exactly as it
    would be without the rule.  The options the synopsis alone names follow the
    documented ones in the order the tree first names them, and an option the tree
    never names, because a value-taking option before it took it as its value, is
    dropped.

    A slot that every alternative writes as a choice group, as `_CHOICE_GROUP`
    matches, is left out with a warning on standard error, since it names no
    argument.

    The text is read with the prefixes `_without_value_prefixes` describes
    removed, and then as written, and each loses the values attached to a short
    flag that `_without_attached_shorts` describes, which cannot stop it from
    parsing.  Each text then has its flags respelled as
    `_carried_spellings` and `_single_dash_spellings` describe and the repetition
    `_without_value_repeats` describes removed, none of which can stop it from
    parsing, and is read as `_readings` describes, first with the values that
    `_distributed_values` distributes, then without them.  The first reading that
    parses is kept, so a usage line that parsed as written still parses.  A
    reading that does not parse is read once more as `_read_pattern` describes.
    Each text, with and without the values it distributes, is read once more as
    `_bare_optional_values` describes, after every other reading, so a usage line
    that parses without those readings keeps its reading.
    With no tree to confirm them, the command words are those of the text alone.
    """
    words = _command_words(usage)
    documented = len(options)
    formal = docopt.formal_usage(_CONTINUATION.sub("", printable))
    # The prefix is read first, since every later rewrite joins the tokens with
    # spaces and so loses the text that writes it directly before its value.
    prefixed = _without_value_prefixes(formal, options)
    readings, bare = [], []
    for text in dict.fromkeys([prefixed, formal]):
        text = _without_attached_shorts(text, options)
        text, entries = _single_dash_spellings(
            _carried_spellings(text, options), options
        )
        text = _without_value_repeats(text, entries)
        for shown in dict.fromkeys([_distributed_values(text, entries), text]):
            readings += _readings(shown, entries)
            bare += _bare_optional_values(shown, entries)
    # A usage line that parses under no reading declares what the reading of the
    # text as written registered, which is not the last reading tried, and reports
    # why that reading failed.
    written = readings[-1][1]
    readings += bare
    for text, reading, switches in readings:
        try:
            pattern = _read_pattern(text, reading)
        except docopt.DocoptLanguageError as error:
            if reading is written:
                why = error
        else:
            break
    else:
        print(
            "Warning: could not read positional arguments from the usage line:",
            why,
            file=sys.stderr,
        )
        options[documented:] = written[documented:]
        _spelled_single_dash(options[documented:])
        return " ".join(words), [], set()
    first = {}
    for index, leaf in enumerate(pattern.flat(docopt.Option)):
        first.setdefault((leaf.short, leaf.long), index)
    options[:] = options[:documented] + sorted(
        (opt for opt in reading[documented:] if (opt.short, opt.long) in first),
        key=lambda opt: first[opt.short, opt.long],
    )
    for index in switches:
        options[index].argcount, options[index].value = 1, None
    # The tree's options are copies, and a reading may hold a documented option's
    # copy carrying another spelling, so each is traced to the option kept here.
    kept = {}
    for index, opt in enumerate(reading):
        kept.setdefault(
            (opt.short, opt.long), options[index] if index < documented else opt
        )
    # `kept` is keyed on the spellings the tree holds, so a long spelling made for a
    # single-dash flag is removed once it is built and before any flag is read.
    _spelled_single_dash(options[documented:])
    repeating = {
        _passable_flag(kept[key].name)
        for key in _repeated_options(pattern)
        if key in kept
    } - {None}
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
    return prefix, positionals, repeating


def _repeated_options(pattern):
    """The ``(short, long)`` flags of the options the synopsis repeats with ``...``.

    `parse_seq` wraps in `OneOrMore` whatever atom a ``...`` follows, and an option
    has taken its value by then, so a repetition written after an option's value,
    as ripgrep writes ``-e PATTERN ...``, or after a group holding the option, as
    in ``[-D <name>]...``, repeats the option itself: its flag is written once per
    value, as docopt reads ``[-o <o>]...`` for ``-o a -o b``.  An options-block
    entry instead states how many values one occurrence takes, which `repeated`
    records, so `transform` reads the two together.

    Only a repetition holding exactly one option that takes a value is read, or one
    holding a choice among alternatives, as `_alternatives` finds it, none of which
    names two such options: each occurrence then passes one flag, so every option
    of the choice that takes a value repeats on its own.  That is the shape
    `_distributed_values` gives git's ``(-c | -C) <commit>`` when a ``...`` follows
    the placeholder, ``(-c <commit> | -C <commit>)...``.  A repeated switch is a
    count, which no target language can express, and a repetition over two options
    taking values, as in ``(-a <x> -b <y>)...``, repeats the pair rather than
    either flag, so a choice with such an alternative is not read either.

    A repetition over one option written in two alternatives, as in
    ``(-e <p> | --regexp <p>)...`` for ``-e, --regexp``, is a repetition over that
    option.
    """
    repeated = []
    for node in pattern.flat(docopt.OneOrMore):
        branches = _alternatives(node) or [node]
        valued = [
            {
                (opt.short, opt.long)
                for opt in branch.flat(docopt.Option)
                if opt.argcount
            }
            for branch in branches
        ]
        if all(len(keys) <= 1 for keys in valued):
            repeated.extend(key for keys in valued for key in keys)
    return repeated


def _alternatives(node):
    """The alternatives of the choice a repetition holds, or None where it holds none.

    The choice may be wrapped in groups of one child, as ``[(-a | -b) <x>]...`` is,
    once `_distributed_values` has moved the placeholder into each alternative.
    """
    while isinstance(node, docopt.ParentPattern) and not isinstance(
        node, docopt.Either
    ):
        if len(node.children) != 1:
            return None
        node = node.children[0]
    return node.children if isinstance(node, docopt.Either) else None


def _readings(formal, entries):
    """The readings of `formal` to try in turn: with the values it shows, then without.

    Each is the text to parse, the options to parse it with, and the indices of
    the documented switches that take a value in that reading.  Each reading has a
    list of its own, since `parse_pattern` appends to it the options it meets.  The
    values are those `_synopsis_valued_options` and `_synopsis_valued_switches`
    read from `formal` before the groups `_without_attached_values` describes are
    removed from either reading, so that ``--context[=N] FILE`` does not give
    ``FILE`` to ``--context``, and so are the words
    `_without_literal_alternatives` describes, after each reading's values.
    """
    valued = _synopsis_valued_options(formal, entries)
    switches = _synopsis_valued_switches(formal, entries)
    readings = [(list(entries), [])]
    if valued or switches:
        taking = list(entries)
        for index in switches:
            taking[index] = copy.copy(entries[index])
            taking[index].argcount, taking[index].value = 1, None
        readings.insert(0, ([*taking, *valued], switches))
    return [
        (
            _without_literal_alternatives(
                _without_attached_values(formal, reading), reading
            ),
            reading,
            chosen,
        )
        for reading, chosen in readings
    ]


def _bare_optional_values(formal, entries):
    """The readings of `formal` with each option whose value is optional a switch.

    An entry that writes its flag both bare and with a value attached, as GNU sort
    writes ``-c, --check, --check=diagnose-first``, keeps the argument count of its
    attached form, so that a synopsis writing ``--check=WORD`` parses.  A synopsis
    that writes the flag bare gives `parse_long` or `parse_shorts` the next token
    as its value, and where that token closes a group or separates alternatives,
    the line does not parse: ``[--check] FILE`` leaves the ``[`` unmatched.  The
    same holds for the short flag of an entry that brackets the value at the end
    of its long flag, as ``-j, --jobs[=N]`` does.  These readings copy each such
    option at argument count 0, the count of the switch `_takes_value` passes it
    as, and are otherwise made as `_readings` makes them, so that the values the
    synopsis shows are decided with the lowered count.  None are made where no
    option's value is optional.

    The documented options keep their argument count, so a task passes the flag
    as `transform` decides whichever reading parses.
    """
    bare, found = list(entries), False
    for index, option in enumerate(entries):
        if option.argcount and option.value_optional:
            bare[index] = copy.copy(option)
            bare[index].argcount, bare[index].value = 0, False
            found = True
    return _readings(formal, bare) if found else []


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


def _carried_spellings(formal, options):
    """Rewrite each flag the synopsis names to the spelling its option carries.

    An options-block entry may write several long flags, of which `Option.parse`
    keeps the last and `other_spellings` records the rest, as `_entry_spellings`
    describes.  `parse_long` matches one spelling exactly, so a synopsis naming
    another, as torchrun's ``[--log-dir LOG_DIR]`` names the first of
    ``--log-dir, --log_dir LOG_DIR``, would register an option of its own, which
    `_synopsis_valued_options` gives the placeholder, and the task would pass one
    option as two inputs.

    A spelling is rewritten only where that cannot stop the synopsis from parsing,
    for the reason `_synopsis_valued_options` gives.  Where the carried option takes
    no value, no token spelling it may carry one attached by ``=``, which
    `parse_long` would reject.  Where it takes one, every token spelling it must
    carry its value attached or be directly followed by a placeholder or a choice
    group, which would otherwise be read as a positional, so that the bracket of
    ``[--log-dir]`` is never taken as a value.  A spelling that another option
    carries, or that two entries write, is left as written, and so is one whose
    carried spelling two entries declare, which `parse_long` would then reject as
    ambiguous, and so is one whose carried spelling holds a symbol `parse_pattern`
    pads into a token of its own, as ``--color[`` does, which `Option.parse` keeps
    of ``--color[=WHEN]``.  Such a spelling is read as a flag of its own, so beside
    ``--color[=WHEN], --no-color`` a synopsis's ``--no-color FILE`` declares a
    second input passing ``--no-color`` and taking ``FILE``.  The text is returned
    unchanged where no token is rewritten.
    """
    writers = {}
    for option in options:
        for spelling in option.other_spellings:
            writers.setdefault(spelling, []).append(option)
    declared = collections.Counter(option.long for option in options)
    carried = {
        spelling: owners[0]
        for spelling, owners in writers.items()
        if len(owners) == 1
        and owners[0].long
        and len(_tokens(owners[0].long)) == 1
        and declared[owners[0].long] == 1
        and spelling not in declared
    }
    if not carried:
        return formal
    tokens = _tokens(formal)
    safe = {
        spelling: _rewritable(tokens, spelling, option.argcount)
        for spelling, option in carried.items()
    }
    rewritten = False
    for index, token in enumerate(tokens):
        long, eq, value = token.partition("=")
        if safe.get(long):
            tokens[index] = carried[long].long + eq + value
            rewritten = True
    return " ".join(tokens) if rewritten else formal


def _rewritable(tokens, spelling, argcount):
    """May every token spelling this flag be rewritten to a long flag of `argcount`?

    False where the rewrite could stop the synopsis from parsing.  Where the
    option takes no value, no token spelling it may attach one with ``=``, which
    `parse_long` rejects; where it takes one, every such token must attach its
    value or be directly followed by a placeholder or a choice group, which
    `parse_long` would otherwise take as the value.
    """
    for token, after in zip(tokens, [*tokens[1:], ""]):
        long, eq, _value = token.partition("=")
        if long != spelling:
            continue
        if argcount == 0:
            if eq:
                return False
        elif not (eq or _is_placeholder(after) or _CHOICE_GROUP.fullmatch(after)):
            return False
    return True


def _single_dash_spellings(formal, options):
    """Spell a single-dash flag of several letters so that `parse_long` reads it.

    `parse_shorts` reads every token of one dash as a stack of one-letter flags, so
    bedtools getfasta's ``-fi <fasta> -bed <bed/gff/vcf>``, whose options block
    documents ``-fi`` and ``-bed``, declared the switches ``-f``, ``-i``, ``-b``,
    ``-e`` and ``-d``, which bedtools rejects as unrecognized parameters.  Each
    token an entry documents as its whole flag is rewritten to a long spelling of
    the same name, which `parse_long` matches to that entry's option, so the token
    names the documented option and the letters name nothing.

    A token no entry documents is one flag only where the synopsis shows it taking
    a value, as `_undocumented_single_dash` describes, since BSD's ``[-Ccsu]`` is
    a stack, and cargo's ``-Zscript`` is ``-Z`` with its value attached.  A
    spelling two entries document is left as written, since the token could name
    only the last of them; so is one whose rewrite could stop the synopsis from
    parsing, as `_rewritable` decides.

    Return the rewritten text and the options to read it with: each rewritten
    option is a copy carrying the long spelling, so `options` keeps the flag the
    entry writes and `transform` passes it, and each undocumented flag is a new
    option carrying both spellings, from which `_spelled_single_dash` removes the
    long one once the synopsis is read.  The long spelling is the flag's own name
    under a second dash, lengthened with ``_`` until it is a spelling neither the
    options block nor the synopsis writes.
    """
    tokens = _tokens(formal)
    written = {token.partition("=")[0] for token in tokens}
    taken = written | {
        flag for opt in options for flag in (opt.short, opt.long) if flag
    }
    declared = collections.Counter(opt.short for opt in options if opt.short)
    entries, spellings = list(options), {}

    def respelled(short):
        long = "-" + short
        while long in taken:
            long += "_"
        taken.add(long)
        spellings[short] = long
        return long

    for index, option in enumerate(options):
        short = option.short
        if short is None or len(short) < 3 or declared[short] > 1:
            continue
        if short not in written or not _rewritable(tokens, short, option.argcount):
            continue
        entries[index] = copy.copy(option)
        entries[index].long = respelled(short)
    for short in _undocumented_single_dash(tokens, options):
        stand_in = _undocumented(docopt.Option(short, respelled(short), 1))
        stand_in.spelled = True
        entries.append(stand_in)
    if not spellings:
        return formal, options
    for index, token in enumerate(tokens):
        short, eq, value = token.partition("=")
        if short in spellings:
            tokens[index] = spellings[short] + eq + value
    return " ".join(tokens), entries


def _undocumented_single_dash(tokens, options):
    """The single-dash tokens of several letters the synopsis alone shows as one flag.

    bedtools bamtofastq writes ``-fq <FQ>`` and annotate ``-files FILE1`` with no
    entry for either flag, so `parse_shorts` read each as a stack, declaring the
    switches ``-f`` and ``-q``, which bedtools rejects as unrecognized parameters,
    and the placeholder became a positional.  Such a token names one flag taking a
    value where every token spelling it is directly followed by a placeholder, a
    token `parse_atom` reads as an argument, and three further conditions hold.

    No letter of the token is a documented flag taking a value, since
    `parse_shorts` then reads the token as that flag with its value attached, as
    cargo documents ``-Z <FLAG>`` and writes ``-Zscript <MANIFEST_RS>``.  Not
    every letter is documented, since the options block then names a flag for
    each letter and so explains the token as a stack, as it explains htsfile's
    ``[-chHv]``.  And `_passable_flag` accepts the token, since `transform` leaves
    out an option whose flag it rejects, and the value would go with it.

    A lowercase word after the token is no placeholder here, as it is after a flag
    of one letter in `_flags_before_placeholders`: BSD tr writes ``-ds string1``
    and zip ``-xi list``, which are stacks.  A token attaching a value with ``=`` is
    not considered.  The condition is stronger than `_rewritable` asks of a flag of
    argument count 1, so the rewrite cannot stop the synopsis from parsing: the
    value taken is a token that would otherwise be a positional.
    """
    documented = {opt.short for opt in options if opt.short}
    valued = {opt.short for opt in options if opt.short and opt.argcount}
    stacks = set(_stacks(tokens))
    followed = {}
    for token, after in zip(tokens, [*tokens[1:], ""]):
        if token in stacks and "=" not in token:
            followed[token] = followed.get(token, True) and _is_placeholder(after)
    return [
        token
        for token, always in followed.items()
        if always
        and token not in documented
        and not any("-" + letter in valued for letter in token[1:])
        and not all("-" + letter in documented for letter in token[1:])
        and _passable_flag(token) is not None
    ]


def _spelled_single_dash(options):
    """Remove the long spelling of each flag `_single_dash_spellings` made up.

    Such a flag is read through a long spelling, which `parse_long` matches where
    `parse_shorts` would read a stack, and is passed as the single-dash flag the
    synopsis writes.  The long spelling is removed once the tree is read, from the
    options of a reading that failed as well as one that parsed, since those of a
    failed reading are what a degraded record declares.
    """
    for option in options:
        if getattr(option, "spelled", False):
            option.long = None


def _synopsis_valued_options(formal, options):
    """The options the synopsis alone shows taking a value, as ``-a <bed>`` does.

    `parse_shorts` gives a flag the options block does not document no value, and
    `parse_long` gives one only where its token carries ``=``, so bedtools'
    ``-a <bed/gff/vcf/bam>``, which no entry documents, reads as a switch followed
    by a positional.  A flag gets a value here when no entry documents its exact
    spelling and `_flags_before_placeholders` holds it, counting a lowercase word
    after the flag as a placeholder: BSD synopses, which come with no options
    block, write ``[-f file]`` and ``[-e pattern]``, which docopt reads as a
    switch followed by a command word.  The cost is a switch written directly
    before a lowercase operand, as BSD tr writes ``tr [-Ccu] -d string1``, which
    takes the operand as its value.

    A flag that `_passable_flag` rejects, such as absl's ``--apilog:``, takes no
    value, since `transform` leaves such an option out and its value would go with
    it.  A flag whose spelling matches a documented option only once the notation
    that `_passable_flag` removes is removed, as git's synopsis names ``--trailer``
    and its entry ``--[no-]trailer <trailer>``, takes a value only where that
    option's argument count says it does, since `transform` passes the flag from
    the documented option.  An entry whose value is optional, as
    ``--color[=WHEN]``, keeps the argument count of its attached form, since a
    synopsis writing ``--color <x> --color=Z`` would otherwise register a switch
    at the first and reject the value at the second; the documented switch then
    wins in `transform`, and ``<x>`` reaches no input.
    """
    tokens = _tokens(formal)
    declared, twin_argcount = set(), {}
    for opt in options:
        for flag in (opt.short, opt.long):
            if flag:
                declared.add(flag)
                twin_argcount.setdefault(_passable_flag(flag), opt.argcount)
    return [
        docopt.Option(None, flag, 1)
        if flag.startswith("--")
        else docopt.Option(flag, None, 1)
        for flag in _flags_before_placeholders(tokens, words=True)
        if flag not in declared
        and twin_argcount.get(flag, 1) == 1
        and _passable_flag(flag) is not None
    ]


def _synopsis_valued_switches(formal, options):
    """The indices of the documented switches the synopsis shows taking a value.

    bedtools documents ``-g`` and ``-fi`` with no placeholder, as
    ``-g\\tProvide a genome file``, and writes ``-g <genome>`` and ``-fi <fasta>``
    in its synopsis, so each read as a switch followed by a positional, and the
    task could pass the flag but not the file.  A documented option that takes no
    value takes one here where the synopsis writes at least one of its spellings
    and every spelling it writes is among `_flags_before_placeholders`.  The
    synopsis is the evidence the entry lacks, and the two do not disagree: the
    entry shows no value rather than showing none is taken.  An option
    `_passable_flag` rejects takes none, since `transform` leaves it out.

    A short flag whose letter the synopsis also writes inside a stack of letters
    takes none either: given a value, ``-p`` would take the ``z`` of ``-pz`` as it,
    so a documented ``-z`` taking a value would no longer take the token after the
    stack, and the synopsis could stop parsing.
    """
    tokens = _tokens(formal)
    flags = _flags_before_placeholders(tokens)
    written = {token.partition("=")[0] for token in tokens}
    stack_letters = {"-" + letter for token in _stacks(tokens) for letter in token[1:]}
    switches = []
    for index, opt in enumerate(options):
        spellings = [flag for flag in (opt.short, opt.long) if flag in written]
        if (
            opt.argcount == 0
            and spellings
            and all(flag in flags for flag in spellings)
            and opt.short not in stack_letters
            and _passable_flag(opt.name) is not None
        ):
            switches.append(index)
    return switches


def _flags_before_placeholders(tokens, words=False):
    """The flags of `parse_pattern`'s `tokens` that a placeholder always follows.

    That is, every token spelling the flag directly precedes a placeholder, as
    ``-a <bed>`` does.

    A flag here is a long flag without ``=`` or a short flag of one letter, and a
    placeholder a token `parse_atom` reads as an argument, angle-bracketed or in
    capitals, or, with `words`, a lowercase word, which it reads as a command
    word.  Given a value, such a flag takes a token that would otherwise be a
    positional or a command word, never a bracket, a bar or an ellipsis, so giving
    it one never stops a synopsis from parsing.  A value read from an option's
    description could: given one, bedtools slop's ``-r`` would take the ``)`` of
    ``(-l and -r)``.  For the same reason a short flag whose letter ends a stack of
    letters, as ``z`` ends ``-pz``, is not among them, since the stack would take
    the token after it.  A placeholder followed by ``...`` is the flag's one value,
    and `_repeated_options` reads the repetition, so ``-i <file>...`` passes the
    flag before each file rather than a positional list before the flag.

    A command word taken as a value never shortens the CLI prefix: the command
    words `_command_words` reads follow the program name directly, never a flag.
    """
    followed = {}
    for token, after in zip(tokens, [*tokens[1:], ""]):
        long = token.startswith("--") and token != "--" and "=" not in token
        short = len(token) == 2 and token[0] == "-" and token[1] != "-"
        if long or short:
            taken = _is_placeholder(after) or (words and _is_command_word(after))
            followed[token] = followed.get(token, True) and taken
    stack_ends = {"-" + token[-1] for token in _stacks(tokens)}
    return [
        flag for flag, always in followed.items() if always and flag not in stack_ends
    ]


def _is_placeholder(token):
    """Does `parse_atom` read this synopsis token as an argument?"""
    return not token.startswith("-") and (
        token.startswith("<") and token.endswith(">") or token.isupper()
    )


def _stacks(tokens):
    """The tokens `parse_shorts` may read as a stack of one-letter flags."""
    return [
        token
        for token in tokens
        if token.startswith("-") and not token.startswith("--") and len(token) > 2
    ]


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
    tokens = _tokens(formal)
    removed = set()
    for value in _value_indices(tokens, options):
        closing = _placeholder_group(tokens, value)
        if closing is not None:
            removed.update((value, closing))
    return _without_tokens(formal, tokens, removed)


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
    tokens = _tokens(formal)
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
    return _without_tokens(formal, tokens, removed)


def _without_value_prefixes(formal, options):
    """Remove an optional prefix written inside a value: ``[(amend|reword):]<commit>``.

    git writes ``--fixup [(amend|reword):]<commit>`` and ssh
    ``-D [bind_address:]port``: the brackets mark a part of the value that may be
    left out, written directly before the rest of it, and `parse_pattern` read them
    as a group of its own, its words as positionals, so the flag met a bracket
    rather than its value.  A group that `_VALUE_PREFIX` matches and that is
    written directly before a placeholder or a lowercase word is removed, so the
    flag meets the rest of the value, and the caller writes any prefix into it.

    Only the text shows that the group is written directly before its value, which
    is why this runs on `formal` as `formal_usage` writes it.  The group is removed
    whole and balanced, so every other bracket stays matched, as
    `_without_value_repeats` argues.  A documented flag taking a value takes the
    group's opening bracket as written, which is the defect read here, but where an
    option, as the text reads it, takes a token inside the group, the group is left
    as written, since its closing bracket would then close another.  Where the text
    parses only without the removal, as an unbalanced one can, `_read_command_line`
    reads it so.  The text is returned unchanged where nothing is removed.
    """
    tokens = _tokens(formal)
    taken = _value_indices(tokens, options)

    def without(match):
        opening = len(_tokens(formal[: match.start()]))
        after = opening + len(_tokens(match.group()))
        rest = tokens[after] if after < len(tokens) else ""
        if _is_placeholder(rest) or _is_command_word(rest):
            if taken.isdisjoint(range(opening + 1, after)):
                return ""
        return match.group()

    return _VALUE_PREFIX.sub(without, formal)


def _without_attached_shorts(formal, options):
    """Remove a value attached to a short flag: ``-C[num]`` becomes ``-C``.

    BSD grep writes ``[-C[num]]`` and git ``[-u[<mode>]]`` and ``[-S[<keyid>]]``
    for a flag whose value is optional and must be attached to it.
    `parse_pattern` pads the brackets, so among its tokens ``-C [ num ]`` cannot
    be told from a switch followed by an optional positional, ``[-C] [num]``, and
    each record declared a positional named after the value beside the switch.
    Only the text shows that the group is attached, which is why this runs on
    `formal` as `formal_usage` writes it, before any rewrite joins its tokens with
    spaces, as `_without_value_prefixes` does.

    The flag stays the switch it was, as `_without_attached_values` leaves the long
    form ``--context[=num]``.  git accepts a bare ``-u`` and ``-S`` and reads a
    word after either as a path, so the switch is the one form a task can pass;
    BSD grep 2.6.0 instead rejects a bare ``-C`` and reads ``-C 2`` as the count,
    which the text does not tell apart, so its switch stays as wrong as it was,
    and only the positional the value declared is removed.

    The group is left as written where an option reads its ``[`` as its value,
    for `_without_value_brackets` to read on the retry, as git documents ``-u``
    and ``-S`` with a value; where an option reads the flag itself as its value,
    as in ``-p -C[N]``; where a placeholder or a lowercase word follows it, since
    `_flags_before_placeholders` would read that token as the value of the bare
    flag, so ``-C[num] pattern`` would pass the pattern to ``-C``; and where the
    flag is one of several alternatives, since `_distributed_values` would give it
    the placeholder written after them, so ``(-C[num] | -D) <x>`` would lose
    ``<x>``.  The group is removed whole, so every other bracket stays matched, as
    `_without_value_repeats` argues.  The text is returned unchanged where no
    group is removed.
    """
    tokens = _tokens(formal)
    taken = _value_indices(tokens, options)

    def without(match):
        flag = len(_tokens(formal[: match.start()]))
        after = (_tokens(formal[match.end() :]) + [""])[0]
        if not taken.isdisjoint((flag, flag + 1)):
            return match.group()
        if _is_placeholder(after) or _is_command_word(after):
            return match.group()
        if tokens[flag - 1 : flag] == ["|"] or after == "|":
            return match.group()
        return match.group(1)

    return _ATTACHED_SHORT.sub(without, formal)


def _distributed_values(formal, options):
    """Give each flag of a group of alternatives the placeholder written after it.

    git writes ``(-c | -C | --squash) <commit>`` for three flags that each take the
    commit.  Read as written, the documented ``-c`` and ``-C`` took the bars as
    their values, the alternatives were lost, and ``<commit>`` was a positional.
    The text is rewritten to ``(-c <commit> | -C <commit> | --squash <commit>)``,
    where every alternative of a parenthesized group is one flag, written without
    ``=``, and a placeholder directly follows the group.  Each flag then takes the
    placeholder: a documented flag by its entry, an undocumented one as
    `_synopsis_valued_options` describes.  A bracketed group is left as written,
    since its placeholder is required, and would no longer be once it moved inside.

    No bracket, parenthesis or bar is added or removed, so only a value can change
    how the text parses.  The rewrite is declined where an option before the group
    takes its opening parenthesis as its value, as the text reads it, since the
    group is then no group, and where any of the flags is a documented switch,
    which `_synopsis_valued_switches` would then give the placeholder, although
    the group shows no value for it alone.  The last flag of the group may take
    its closing parenthesis as written, which is the defect read here; where a
    text read so parses only that way, as an unbalanced one can,
    `_read_command_line` reads it without the rewrite.  The text is returned
    unchanged where nothing is rewritten.
    """
    tokens = _tokens(formal)
    taken = _value_indices(tokens, options)
    switches = {
        flag
        for option in options
        if option.argcount == 0
        for flag in (option.short, option.long)
        if flag
    }
    inserted, removed = {}, set()
    for opening, token in enumerate(tokens):
        flags = _flag_alternatives(tokens, opening) if token == "(" else None
        if flags is None:
            continue
        value = flags[-1] + 2
        if value >= len(tokens) or not _is_placeholder(tokens[value]):
            continue
        if opening in taken:
            continue
        if any(tokens[flag] in switches for flag in flags):
            continue
        inserted.update((flag + 1, tokens[value]) for flag in flags)
        removed.add(value)
    if not removed:
        return formal
    parts = []
    for index, token in enumerate(tokens):
        if index in inserted:
            parts.append(inserted[index])
        if index not in removed:
            parts.append(token)
    return " ".join(parts)


def _flag_alternatives(tokens, opening):
    """The indices of the flags of ``( -c | -C | --squash )`` at `opening`, or None.

    None unless the group holds at least two alternatives, each one flag token
    written without ``=``.
    """
    flags, index = [], opening + 1
    while index + 1 < len(tokens):
        token = tokens[index]
        if not token.startswith("-") or token in ("-", "--") or "=" in token:
            return None
        flags.append(index)
        if tokens[index + 1] == ")":
            return flags if len(flags) > 1 else None
        if tokens[index + 1] != "|":
            return None
        index += 2
    return None


def _without_attached_values(formal, options):
    """Remove an optional value attached to a long flag: ``--context[=num]``.

    BSD grep writes ``[--context[=num]]`` and git ``[--exec-path[=<path>]]`` for a
    flag whose value is optional and must be attached with ``=``.  No entry
    documents either flag, so `parse_long` registered a switch and the group became
    an optional positional named after the value.  Without the group the flag
    stays the switch, the one form of the two a task can pass, as `_takes_value`
    reads an entry that writes a flag both bare and with a value attached; a task
    passes a value after a space, which both tools read as an argument of its own.

    The group's one token keeps the ``=`` the text attached to it, since
    `parse_pattern` pads only the brackets, so a bracketed token opening with ``=``
    can come from nothing else.  tree's ``--gitfile[=]file``, whose brackets hold
    the ``=`` alone, writes a required value whose separator is optional, and is
    left as written.

    Removing a whole bracketed group leaves every other bracket matched, as
    `_without_value_repeats` describes, so the removal could stop a synopsis from
    parsing only by taking the ``[`` from an option that reads it as its value, as
    a documented ``--color`` taking a value would in ``[--color[=WHEN]]``.
    `_valued_options` names those tokens, and such a group is left as written for
    `_without_value_brackets` to read on the retry.  The text is returned
    unchanged where no group is removed.
    """
    tokens = _tokens(formal)
    taken = _value_indices(tokens, options)
    removed = set()
    for index, token in enumerate(tokens[:-3]):
        if not token.startswith("--") or token == "--" or "=" in token:
            continue
        opening, value, closing = tokens[index + 1 : index + 4]
        if opening != "[" or closing != "]" or value[:1] != "=" or value == "=":
            continue
        if index + 1 not in taken:
            removed.update(range(index + 1, index + 4))
    return _without_tokens(formal, tokens, removed)


def _without_literal_alternatives(formal, options):
    """Keep only the first of the literal words a group offers an option's value.

    BSD patch writes ``[-V t | nil | never | none]`` and ssh-keygen
    ``[-t ecdsa | ecdsa-sk | rsa]``: a group that opens with a flag whose value is
    one of several lowercase words.  Given its value, the flag takes the first
    word, and the others would remain alternatives of their own, each read as a
    positional.  Each ``| word`` that follows the value within such a group is
    removed, where every alternative is one word.  A group whose alternatives
    hold anything else, as ``[-O fmt | -o fmt]`` does, stays as written.

    Only bars and words are removed, never a bracket, which never changes whether
    the text parses.  The text is returned unchanged where nothing is removed.
    """
    tokens = _tokens(formal)
    removed = set()
    for value in _value_indices(tokens, options):
        if value < 2 or tokens[value - 2] != "[":
            continue
        if not _is_command_word(tokens[value]):
            continue
        end = value + 1
        while tokens[end : end + 1] == ["|"] and _is_command_word(
            tokens[end + 1] if end + 1 < len(tokens) else ""
        ):
            end += 2
        if end > value + 1 and tokens[end : end + 1] == ["]"]:
            removed.update(range(value + 1, end))
    return _without_tokens(formal, tokens, removed)


def _tokens(text):
    """The tokens `parse_pattern` reads `text` as, its symbols padded with spaces."""
    return _PATTERN_SYMBOL.sub(r" \1 ", text).split()


def _without_tokens(formal, tokens, removed):
    """`formal` without the `tokens` whose indices `removed` holds.

    The text is returned unchanged where `removed` is empty.
    """
    if not removed:
        return formal
    return " ".join(token for i, token in enumerate(tokens) if i not in removed)


def _value_indices(tokens, options):
    """The indices of the `tokens` that options take as their values."""
    return {value for _option, value in _valued_options(tokens, options)}


def _valued_options(tokens, options):
    """Yield each option among `tokens` that takes a value, and its value's index.

    The scan runs on a copy of `options`, so that the caller's list receives no
    option from it: its registrations model `parse_long`'s bookkeeping rather than
    options the help text documents, and `_synopsis_valued_options` reads the
    caller's list as what the options block documents, so a registration there
    would change which flags take a value.  The token an option reads as its value
    is never an option itself, so the scan passes over it.
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
    ``attached`` whether the entry writes its flag with a value attached by ``=``.
    An entry writing more than one long flag is read as `_entry_spellings`
    describes, and the option records the flags it does not carry as
    ``other_spellings``, the first long flag it writes other than a flag for false
    as ``first_long``, a flag for false as ``false_flag``, and as ``value_optional``
    whether the flag is written both bare and attached, in two declarations or in
    one, as ``--color[=WHEN]`` writes it.
    """
    options = []
    for entry, description in _option_entries(doc, synopsis):
        flags, gap, text = entry[0].partition("  ")
        flags = _FLAG_REPETITION.sub(r"\1", _FLAG_ALTERNATIVE.sub(r"\1, ", flags))
        spellings = _entry_spellings(flags)
        flags = spellings.text
        option = docopt.Option.parse("\n".join([flags + gap + text, *entry[1:]]))
        option.other_spellings = spellings.other_spellings
        option.first_long = spellings.first_long
        option.false_flag = spellings.false_flag
        # A value written optional and attached to the flag the option carries, as
        # GNU ls documents `--color[=WHEN]` and GNU date `-I[FMT]`, says what a bare
        # and an attached declaration say.  Only the carried flag is read: ruby's
        # `-Eex[:in], --encoding=ex[:in]` brackets an optional part of a required
        # value, and the long flag it carries shows the value required.
        option.value_optional = spellings.value_optional or (
            option.name != _without_optional_value(option.name)
        )
        option.description = _joined(description)
        repeat = _REPEATED_VALUE.search(flags) or _REPEATED_LIST.search(flags)
        option.repeated = repeat.group(1) if repeat else None
        option.declared_type = None if option.argcount else _value_kind(description)
        option.attached = any(
            "=" in spelling
            and spelling.partition("=")[0] in (option.short, option.long)
            for spelling in flags.replace(",", " ").split()
        )
        options.append(option)
    return options


class _Spellings(NamedTuple):
    """What `_entry_spellings` reads from an entry's flags."""

    #: The flag text to give `Option.parse`.
    text: str
    #: The long flags the option does not carry.
    other_spellings: tuple[str, ...] = ()
    #: The first long flag the entry writes other than a flag for false, or ``""``.
    first_long: str = ""
    #: The option's flag for false, or ``""``.
    false_flag: str = ""
    #: Whether one flag is written both bare and with a value attached.
    value_optional: bool = False


def _entry_spellings(flags):
    """Read an entry's flags as one option and return a `_Spellings`.

    `Option.parse` keeps the last long flag an entry writes and gives the option a
    value where any of its flags is written with one, so three shapes are misread.
    Python 3.13's argparse writes an option with two long spellings as
    ``--log-dir, --log_dir LOG_DIR``, and GNU writes ``-u, --utc, --universal``:
    either spelling is the tool's own, the last is the one `Option.parse` keeps
    and the argparse reader passes, the first is the one argparse derives `dest`
    from and so names the input, and the others are returned for
    `_carried_spellings`.  argparse's ``BooleanOptionalAction`` writes the two
    values of a switch as ``--x, --no-x``, and click writes them as
    ``--x / --no-x`` or as ``--fast / --safe``: the flags after click's slash, and
    a second long flag that is the first with ``no-`` inserted, as the action
    generates it, are the flag for false, the last of which is returned, and they
    are removed from the text.  GNU sort writes one flag both bare and with a
    value attached, as ``-c, --check, --check=diagnose-first``, so the flag takes
    its value optionally and only attached; `_takes_value` reads such an option as
    the switch, the one form of the two a task can pass, as `_one_per_flag`
    chooses it across two entries.  One declaration says the same where it
    brackets the value at the end of the flag, as ``--color[=WHEN]`` does, which
    `_documented_options` reads from the flag `Option.parse` stores.

    The text is returned as written unless a flag for false is removed from it, so
    that `Option.parse` reads every other entry's flags, value and default as it
    reads the text.  Text that is no list of declarations at all is returned as
    written, and nothing else is read from it; a line of prose read as an entry,
    such as conda's ``--dry-run or --download-only is specified.``, is no such list.
    """
    sides = _FLAG_SENSES.split(flags, maxsplit=1)
    declarations = _declarations(sides[0])
    negations = _declarations(sides[1]) if len(sides) > 1 else []
    if declarations is None or negations is None:
        return _Spellings(flags)
    # A declaration that is its flag alone, with no placeholder and no `=`, is bare.
    bare = {d for d in declarations if _flag_name(d) == d}
    attached = {_flag_name(d) for d in declarations if "=" in d.split()[0]}
    kept, longs = declarations, _long_flags(declarations)
    # The negation of `--color[=WHEN]` is `--no-color`.
    name = _without_optional_value(longs[0]) if longs else ""
    if not negations and len(longs) == 2 and longs[1] == "--no-" + name[2:]:
        negations = [d for d in declarations if _flag_name(d) == longs[1]]
        kept = [d for d in declarations if _flag_name(d) != longs[1]]
        longs = longs[:1]
    false_flags = _long_flags(negations) or [_flag_name(d) for d in negations]
    carried = longs[-1:]
    others = tuple(
        flag for flag in _long_flags(declarations + negations) if flag not in carried
    )
    return _Spellings(
        text=", ".join(kept) if negations else flags,
        other_spellings=others,
        first_long=longs[0] if longs else "",
        false_flag=false_flags[-1] if false_flags else "",
        value_optional=bool(bare & attached),
    )


def _declarations(flags):
    """The declarations an entry's flags are a comma-separated list of, or None.

    A declaration is a flag, which may carry its value attached by ``=``, followed
    by at most one placeholder, as in ``-U``, ``--file=FILE`` and ``-U FILE``.  The
    text is read token by token rather than split at commas, since a placeholder
    may hold one, as cnvkit's ``{hybrid,amplicon,wgs}`` does.  None where the text
    is no such list, which is how a line of prose read as an entry is told from an
    entry naming one option twice: its words after the first flag are neither.
    """
    declarations, has_placeholder = [], False
    for token in flags.split():
        token = token.removesuffix(",")
        if token.startswith("-") and token != "-":
            declarations.append(token)
            has_placeholder = False
        elif not declarations or has_placeholder:
            return None
        else:
            declarations[-1] += " " + token
            has_placeholder = True
    return declarations or None


def _flag_name(declaration):
    """The flag a declaration writes, without its value, spaced or attached by ``=``."""
    return declaration.split()[0].partition("=")[0]


def _long_flags(declarations):
    """The distinct long flags these declarations write, in order."""
    flags = map(_flag_name, declarations)
    return list(dict.fromkeys(flag for flag in flags if flag.startswith("--")))


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
