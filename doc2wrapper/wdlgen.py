"""WDL serialization from the task object model."""
from ast import literal_eval

import jinja2


RESERVED_WDL_NAMES = set(
    """
scatter
""".split()
)


def render(template_kwargs):
    """Render the WDL task template with the given values.

    Args:
        template_kwargs: dict

    Return:
        out_wdl: str
    """
    env = jinja2.Environment(
        # loader=jinja2.PackageLoader("doc2wrapper"),
        loader=jinja2.FileSystemLoader("."),
        autoescape=jinja2.select_autoescape(),
        lstrip_blocks=True,
        trim_blocks=True,
    )
    template = env.get_template("doc2wrapper/task_template.wdl")
    out_wdl = template.render(**template_kwargs)
    return out_wdl


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
