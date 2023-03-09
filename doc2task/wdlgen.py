"""Main module."""
from dataclasses import dataclass

import jinja2


RESERVED_WDL_NAMES = set("""
scatter
""".split())

@dataclass
class Argument:
    """A command argument for a WDL task."""
    name: str
    wdl_type: str
    is_array: bool = False
    is_required: bool = False
    default_value: str | None = None
    option_flag: str = ""
    option_has_value: bool = False
    doc: str = ""


def render(template_kwargs):
    """Render the WDL task template with the given values.

    Args:
        template_kwargs: dict

    Return:
        out_wdl: str
    """
    env = jinja2.Environment(
        #loader=jinja2.PackageLoader("doc2wdl"),
        loader=jinja2.FileSystemLoader("."),
        autoescape=jinja2.select_autoescape(),
        lstrip_blocks=True, trim_blocks=True
    )
    template = env.get_template("task_template.wdl")
    out_wdl = template.render(**template_kwargs)
    return out_wdl
