"""WDL serialization from the task object model."""

import math
from dataclasses import replace

from .render import render_block, render_document

RESERVED_WDL_NAMES = """
scatter
""".split()

_ESCAPES = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\r": "\\r", "\t": "\\t"}


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
        [
            render_block("task_template.wdl", RESERVED_WDL_NAMES, _spelled(task))
            for task in tasks
        ],
    )


def _spelled(task):
    """Replace each argument's Python default and doc text with their WDL spelling.

    The arguments are copied, as `tasktree.rename_reserved` copies them, because a
    second writer may render the same model afterwards.
    """
    return {
        **task,
        "cli_args": [
            replace(arg, default_value=_default(arg), doc=_wdl_string(arg.doc))
            for arg in task["cli_args"]
        ],
    }


def _default(arg):
    """The WDL literal for an argument's default, or None if it should have none.

    A default must match the declaration's shape: a scalar default of an array
    input is its sole element, and a list default of a scalar input is dropped.
    """
    value = arg.default_value
    if arg.is_array and value is not None and not isinstance(value, (list, tuple)):
        value = [value]
    elif not arg.is_array and isinstance(value, (list, tuple)):
        return None
    return _wdl_literal(value)


def _wdl_literal(value):
    """Spell a Python value as a WDL literal, or return None if WDL has none for it.

    None is also what an object such as `sys.stdin` or an infinite float yields, so
    a caller treats it as "no default": the input is then optional instead.
    """
    if value is None:
        return None
    # bool before int, since bool is a subclass of int.
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return str(value) if math.isfinite(value) else None
    if isinstance(value, str):
        return _wdl_string(value)
    if isinstance(value, (list, tuple)):
        items = [_wdl_literal(item) for item in value]
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
