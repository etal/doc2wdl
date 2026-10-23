"""Object model for a generated task, shared by every reader and writer."""

import re
from ast import literal_eval
from dataclasses import dataclass, replace
from typing import Any


@dataclass
class Argument:
    """One argument of the wrapped command, as every reader and writer sees it.

    `wdl_type` is misnamed: it holds a type name in WDL's vocabulary that the
    Nextflow writer does not read at all. Renaming it is part of the outstanding
    type-inference work, which has to move both readers and both templates at once.

    `default_value` is a Python value, never text in a target language: a writer
    that declares defaults spells it, and drops it where its language has no
    literal for it.

    `option_has_value` is read only for options, and says whether the flag is
    followed by a value.  One that is not is a switch, typed Boolean by a reader
    that knows it is one.  `option_flag` is then the spelling emitted when the
    input is true and `false_flag` the one emitted when it is false; either may be
    empty, as for `store_false`, which has only a spelling for false.  A switch
    that has a name for both states renders one of them whenever its input is set.

    `flag_per_value` marks an array option whose flag precedes each value, as
    `--tag a --tag b` for argparse's `append`, rather than the whole list, as
    `--tags a b` for `nargs="+"`.  The wrong form still type checks: a `nargs`
    option then keeps only its last value, and an `append` option receives the
    rest of its values as stray positionals.
    """

    name: str
    wdl_type: str
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


def type_and_default(value):
    """Infer a help-text default's data type and Python value.

    The value is returned as Python, not as WDL: spelling it is the business of a
    writer that declares defaults.  A default the command computes at run time has
    no value to give.
    """
    try:
        value = literal_eval(value)
    except SyntaxError:
        # The default is probably calculated by the command
        # -> no default value to apply here; treat the argument as optional instead
        return "String", None
    except ValueError:
        # Default is a valid token, but not a number or other literal -> string'll do
        return "String", value
    wdl_type = (
        "Boolean"
        if isinstance(value, bool)
        else "Int"
        if isinstance(value, int)
        else "Float"
        if isinstance(value, float)
        else "String"
    )
    return wdl_type, value


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
    language's keywords into the model every other writer also renders.  The
    arguments are copied rather than mutated for the same reason -- a second
    writer may render the same model afterwards.
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
