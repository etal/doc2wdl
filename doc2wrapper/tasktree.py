"""Object model for a generated task, shared by every reader and writer."""

import re
from ast import literal_eval
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Any


class ValueType(Enum):
    """What kind of value an argument takes, in the model's own vocabulary.

    Neutral on purpose: how a target language spells a type is its writer's
    business, as the spelling of a default and of a comment already are.  WDL's
    `File` and Nextflow's `path` are one idea under two names, and WDL declares a
    NUMBER as `String` because its placeholder coercion would print `1e-10` as
    `0.000000`.
    """

    BOOLEAN = "boolean"
    INTEGER = "integer"
    NUMBER = "number"
    TEXT = "text"
    FILE = "file"


#: The types an interface can declare, as the Python callable a parser converts
#: with.  `argparse.FileType` opens the path it is given, so a reader passes
#: `Path` for it: the model has one name for a file however the parser spells it.
_DECLARED_TYPES = {
    bool: ValueType.BOOLEAN,
    int: ValueType.INTEGER,
    float: ValueType.NUMBER,
    str: ValueType.TEXT,
    Path: ValueType.FILE,
}


@dataclass(frozen=True)
class Argument:
    """One argument of the wrapped command, as every reader and writer sees it.

    Frozen, because one model may be rendered by several writers in turn: a
    writer changes copies made with `dataclasses.replace`, never the model.

    `value_type` is a `ValueType`, in the model's own vocabulary, which a writer
    replaces with its target language's spelling -- the same shape `default_value`
    and `doc` take, and the reason the Nextflow writer need not read a WDL type
    name it has no use for.

    `default_value` is a Python value, never text in a target language: a writer
    that declares defaults spells it, and drops it where its language has no
    literal for it.

    `option_has_value` is read only for options, and says whether the flag is
    followed by a value.  One that is not is a switch, which `type_and_default`
    types BOOLEAN.  `option_flag` is then the spelling emitted when the
    input is true and `false_flag` the one emitted when it is false; either may be
    empty, as for `store_false`, which has only a spelling for false.  A switch
    that has a name for both states renders one of them whenever its input is set.

    `flag_per_value` marks an array option whose flag precedes each element, as
    `--tag a --tag b` for argparse's `append`, rather than the whole list, as
    `--tags a b` for `nargs="+"`.  An element is one occurrence's value, or its
    values separated by spaces where one occurrence takes several, as for
    `append` with `nargs=2`.  The wrong form still type checks: a `nargs` option
    then keeps only its last value, and an `append` option receives the rest of
    its values as stray positionals.
    """

    name: str
    value_type: ValueType | str
    is_array: bool = False
    is_required: bool = False
    default_value: Any = None
    option_flag: str = ""
    false_flag: str = ""
    option_has_value: bool = False
    flag_per_value: bool = False
    doc: str = ""

    @property
    def is_positional(self):
        """Is this a bare argument rather than one introduced by a flag?

        Templates must ask through this property. Spelling the test inline invites
        `option_flag is not none`, which is true for the empty-string default and so
        silently drops every positional from the rendered command line -- exactly the
        class of defect this property exists to make unspellable.
        """
        return not (self.option_flag or self.false_flag)

    @property
    def is_optional(self):
        """May the task's caller leave this input unset altogether?

        Not the negation of `is_required`: an input with a default is never unset, so
        it is declared without WDL's `?` -- which on a defaulted declaration is
        redundant, and behaves differently between engines -- and a template must not
        test it with `defined()`, which is then always true.  That test is how every
        bare flag of the argparse path came to be emitted unconditionally.
        """
        return not self.is_required and self.default_value is None


def type_and_default(declared=None, default=None, is_array=False, is_positional=False):
    """Infer one argument's value type and the Python value of its default.

    The one place either reader decides a type, so that the same interface typed
    from help text and from a live parser agrees.  Precedence: a valueless flag is
    BOOLEAN, then a declared type, then the type of a stated default, then FILE for
    a positional and TEXT for anything else -- a positional is the input file far
    more often than not, while an option is a setting, and neither reader has any
    further evidence (`<brackets>` do not mark a file: samtools' `region` and
    cnvkit's `targets` are both bare words and only one is a path).

    A stated default arrives as a Python value -- `literal_default` reads help
    text's notation, and a parser's own default is already one -- and is dropped
    unless it fits the type.  Dropping is what makes
    `type=int, default=266.6666666666667` representable: no WDL declaration can
    hold that pair, the tool applies the float itself when the flag is absent, and
    an input left unset passes no flag.

    Args:
        declared: the type the interface states, as the Python callable a parser
            would convert with, or None where it states none.  A valueless flag
            states `bool`.
        default: the default the interface states, as a Python value, or None for
            none stated.  A parser may give text for a declared `int` or `float`,
            which is read as a Python literal, as argparse's `type` reads an
            ordinary numeral.
        is_array: whether the argument takes several values.
        is_positional: whether it is written bare, with no flag.

    Return:
        (value_type, default_value), the default as Python and never as text of a
        target language.
    """
    value_type = _DECLARED_TYPES.get(declared)
    if isinstance(default, str) and value_type in (ValueType.INTEGER, ValueType.NUMBER):
        # A declared number reads its own text default, as argparse calls `type`
        # on a string default and nothing else.  A valueless flag is left alone:
        # argparse calls no converter for it.
        default = literal_default(default)
    if value_type is None:
        value_type = _value_type_of(default, is_array)
    if value_type is None:
        value_type = ValueType.FILE if is_positional else ValueType.TEXT
    return value_type, default if _fits(value_type, default, is_array) else None


def literal_default(text):
    """The Python value a help text's `[default: ...]` literal states.

    None where it states none, and None where what it states is prose rather than
    a value: `1/16 avg size, calculated` describes a default the command computes,
    so there is nothing to declare.  The help-text reader calls this for its
    notation, and `type_and_default` for the text default of a declared number.
    No other string default is read: a parser's is not notation but the value
    itself, as argparse hands the program `.` for `default="."`, parsing nothing.
    """
    if text is None:
        return None
    try:
        return literal_eval(text)
    except SyntaxError:
        return None
    except ValueError:
        # A valid token that is not a literal, such as `tab`, is a string default.
        return text


def _value_type_of(value, is_array):
    """The value type a stated default is evidence of, or None for no evidence.

    An array's elements type the array, and only when they agree; a list default
    on a scalar argument says nothing about the scalar, so it types nothing.
    """
    if is_array and isinstance(value, (list, tuple)):
        element_types = {_value_type_of(item, False) for item in value}
        return element_types.pop() if len(element_types) == 1 else None
    # bool before int, since bool is a subclass of int.
    if isinstance(value, bool):
        return ValueType.BOOLEAN
    if isinstance(value, int):
        return ValueType.INTEGER
    if isinstance(value, float):
        return ValueType.NUMBER
    if isinstance(value, str):
        return ValueType.TEXT
    return None


#: The value types a declaration of each type can hold.  A NUMBER holds an
#: integer as well.  A FILE holds no path as a default, since a path the
#: interface names, such as `-` for standard input, is not a file the engine can
#: provide.
_ACCEPTS = {
    ValueType.BOOLEAN: {ValueType.BOOLEAN},
    ValueType.INTEGER: {ValueType.INTEGER},
    ValueType.NUMBER: {ValueType.INTEGER, ValueType.NUMBER},
    ValueType.TEXT: {ValueType.TEXT},
    ValueType.FILE: set(),
}


def _fits(value_type, value, is_array):
    """Can a declaration of this type hold this default?

    A default that does not fit is dropped rather than coerced or widened: the
    declaration states what the tool accepts, and the tool keeps applying its own
    default to an option nobody passes.
    """
    if value is None:
        return True
    if is_array and isinstance(value, (list, tuple)):
        return all(_fits(value_type, item, False) for item in value)
    return _value_type_of(value, False) in _ACCEPTS[value_type]


def legal_name(name, prefix):
    """Keep only the characters a target language accepts, and start with a letter.

    WDL requires an identifier to begin with a LETTER: a leading underscore is
    rejected exactly as a leading digit is, so the prefix has to be a real word.
    Applied by the readers because a name legal in WDL is also legal in Groovy,
    which keeps one rule rather than one per writer.
    """
    kept = re.sub(r"[^A-Za-z0-9_]", "", re.sub(r"[-.]", "_", name))
    return kept if kept[:1].isalpha() else prefix + kept


def task_title(command):
    """Name the task wrapping a command, such as `cnvkit.py antitarget`.

    Each word is capitalized before punctuation is removed, so that boundaries the
    removal erases still show: `CnvkitAntitarget`.  Unlike `str.title`, letters
    that are not at a word boundary keep their case, so that subcommands `runAll`
    and `runall` name two tasks rather than one, and `SAMtools` stays as written.
    Only ASCII letters count as part of a word, because `legal_name` removes any
    other letter, which then cannot join what follows: a letter after `ñ` starts a
    word, and `ñdef` cannot become the Groovy keyword `def`.
    """
    capitalized = re.sub(
        r"(?<![A-Za-z])[a-z]",
        lambda match: match.group().upper(),
        command.replace(".py", ""),
    )
    return legal_name(capitalized.replace(" ", "").replace("-", ""), prefix="Task")


def rename_arguments(cli_args, reserved):
    """Give every argument a name the target language accepts and no other has.

    A name in `reserved` takes the suffix `_`, and a name an earlier argument
    already holds takes `_2`, `_3` and so on, so the first argument wanting a name
    keeps it.  Uniqueness is decided over the final names, after the keyword
    rename, because the rename can itself create a duplicate: `scatter` becomes
    `scatter_` beside an argument already named that.

    Reserved words differ per target language, so the renaming belongs to the
    writer rather than the reader: sanitizing at read time would bake one
    language's keywords into the model every other writer also renders.
    """
    taken = set()
    return [
        replace(arg, name=unique_name(rename_if_reserved(arg.name, reserved), taken))
        for arg in cli_args
    ]


def rename_if_reserved(name, reserved):
    """Suffix one identifier if the target language will not accept it as a name."""
    return name + "_" if name in reserved else name


def unique_name(name, taken):
    """Return `name`, or the first of `name_2`, `name_3`, ... not in `taken`.

    The result is added to `taken`, so successive calls never return one name twice.
    """
    unique = name
    number = 1
    while unique in taken:
        number += 1
        unique = f"{name}_{number}"
    taken.add(unique)
    return unique
