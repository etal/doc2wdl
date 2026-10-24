"""WDL serialization from the task object model."""

import math
from dataclasses import replace

from .render import render_document
from .tasktree import ValueType

# The WDL 1.1 specification's Reserved Keywords, including the three it says to treat
# as reserved for a future version.  WDL is case-sensitive, so `Object` and `object`
# are separate entries.  miniwdl 1.15 rejects 30 of these as names and accepts the
# rest: `Boolean`, `Object`, `after`, `in`, `version` and the three future ones.  The
# specification is what another engine reads the `version 1.1` document against.
# Task names and declaration names share the keyword rule, so one list serves both.
RESERVED_WDL_NAMES = """
Array Boolean File Float Int Map None Object Pair String
alias after as call command else false if in import input left meta object output
parameter_meta right runtime scatter struct task then true version workflow
Directory hints requirements
""".split()

# Names that task_template.wdl declares itself, which an input may not also take.
# They are legal identifiers rather than keywords, and task names are a separate
# namespace, so only the argument list includes them.
TEMPLATE_WDL_NAMES = ["output_file"]

_ESCAPES = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\r": "\\r", "\t": "\\t"}

# How WDL spells each of the model's value types.  Two map onto `String`: WDL's
# placeholder coercion prints a Float "in the style [-]ddd.dddddd, with 6 digits
# after the decimal point" (specification, Expression Placeholder Coercion), so a
# `Float` input of 1e-10 reaches the command line as `0.000000` and 1e+16 gains six
# zeros.  WDL 1.1 has no formatting function to undo that -- every Float-to-String
# path, `sub`, `sep` and concatenation alike, sees the coerced text -- so a number
# is declared as the text the caller wrote, which passes through exactly.  The cost
# is that a caller supplies it as text: an inputs file giving a JSON number for a
# String is rejected, and a Float passed from a workflow, where an engine accepts it
# at all, arrives with six decimals.
_WDL_TYPES = {
    ValueType.BOOLEAN: "Boolean",
    ValueType.INTEGER: "Int",
    ValueType.NUMBER: "String",
    ValueType.TEXT: "String",
    ValueType.FILE: "File",
}


def render(tasks):
    """Render a WDL document wrapping one task per given set of values.

    Args:
        tasks: iterable of template-keyword dicts, one per task, in the order the
            reader yielded them

    Return:
        out_wdl: str
    """
    return render_document(
        "document_template.wdl",
        "task_template.wdl",
        reserved=RESERVED_WDL_NAMES + TEMPLATE_WDL_NAMES,
        reserved_titles=RESERVED_WDL_NAMES,
        tasks=(_spelled(task) for task in tasks),
    )


def _spelled(task):
    """Replace each argument's model type, Python default and doc text with WDL's.

    The arguments are copied, as `tasktree.rename_arguments` copies them, because a
    second writer may render the same model afterwards.
    """
    return {
        **task,
        "cli_args": [
            replace(
                arg,
                value_type=_WDL_TYPES[arg.value_type],
                default_value=_default(arg),
                doc=_wdl_string(arg.doc),
            )
            for arg in task["cli_args"]
        ],
    }


def _default(arg):
    """The WDL literal for an argument's default, or None if it should have none.

    A scalar default of an array input is its sole element; a default that does
    not fit the declaration at all is already dropped by `tasktree.type_and_default`.
    """
    value = arg.default_value
    if arg.is_array and value is not None and not isinstance(value, (list, tuple)):
        value = [value]
    return _wdl_literal(value, arg.value_type)


def _wdl_literal(value, value_type):
    """Spell a Python value as a WDL literal, or return None if WDL has none for it.

    None is also what a non-finite float yields, so a caller treats it as "no
    default": the input is then optional instead.  A NUMBER is declared as a
    String, so its default is quoted text; `str` of a float is the shortest string
    that reads back as the same float, which is what keeps 1e-10 out of the
    six-decimal form.
    """
    if value is None:
        return None
    # bool before int, since bool is a subclass of int.
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, (int, float)):
        return _wdl_string(str(value)) if value_type is ValueType.NUMBER else str(value)
    if isinstance(value, str):
        return _wdl_string(value)
    if isinstance(value, (list, tuple)):
        items = [_wdl_literal(item, value_type) for item in value]
        if any(item is None for item in items):
            return None
        return "[" + ", ".join(items) + "]"
    return None


def _wdl_string(text):
    r"""Spell text as a double-quoted WDL string literal that evaluates back to it.

    Backslash, double quote and control characters take their escapes, the common
    ones by name so that multi-line help stays readable.  The placeholder openers
    `~{` and `${` are written with their first character as a `\u` escape, which is
    the one spelling both WDL string contexts read as plain text: in an expression
    `\~{` is a syntax error and the raw `~{` an interpolation, while in
    `parameter_meta` the raw `~{` is a syntax error and `\~{` keeps its backslash.
    """
    escaped = "".join(
        _ESCAPES.get(c, c if c >= " " else f"\\x{ord(c):02x}") for c in text
    )
    escaped = escaped.replace("~{", "\\u007E{").replace("${", "\\u0024{")
    return f'"{escaped}"'
