"""Object model for a generated task, shared by every reader and writer."""
from ast import literal_eval
from dataclasses import dataclass, replace


@dataclass
class Argument:
    """One argument of the wrapped command, as every reader and writer sees it.

    `wdl_type` is misnamed: it holds a type name in WDL's vocabulary that the
    Nextflow writer does not read at all. Renaming it is part of the outstanding
    type-inference work, which has to move both readers and both templates at once.
    """

    name: str
    wdl_type: str
    is_array: bool = False
    is_required: bool = False
    default_value: str | None = None
    option_flag: str = ""
    option_has_value: bool = False
    doc: str = ""

    @property
    def is_positional(self):
        """Is this a bare argument rather than one introduced by a flag?

        Templates must ask through this property. Spelling the test inline invites
        `option_flag is not none`, which is true for the empty-string default and so
        silently drops every positional from the rendered command line -- exactly the
        class of defect this property exists to make unspellable.
        """
        return not self.option_flag


def type_and_default(value):
    """Infer value's data type and serialization for use as a WDL declaration."""
    try:
        value = literal_eval(value)
    except SyntaxError:
        # The default is probably calculated by the command
        # -> no default value to apply here; treat the argument as optional instead
        wdl_type = "String"
        default = None
    except ValueError:
        # Default is a valid token, but not a number or other literal -> string'll do
        wdl_type = "String"
        default = f'"{value}"'
    else:
        default = str(value)
        wdl_type = (
            "Boolean"
            if isinstance(value, bool)
            else "Int"
            if isinstance(value, int)
            else "Float"
            if isinstance(value, float)
            else "String"
        )
    return wdl_type, default


def rename_reserved(cli_args, reserved):
    """Suffix any argument whose name collides with a target language's keyword.

    Reserved words differ per target language, so the renaming belongs to the
    writer rather than the reader: sanitizing at read time would bake one
    language's keywords into the model every other writer also renders.  The
    arguments are copied rather than mutated for the same reason -- a second
    writer may render the same model afterwards.
    """
    return [
        replace(arg, name=arg.name + "_") if arg.name in reserved else arg
        for arg in cli_args
    ]
