"""WDL serialization from the task object model."""
from .render import render as _render

RESERVED_WDL_NAMES = """
scatter
""".split()


def render(template_kwargs):
    """Render the WDL task template with the given values.

    Args:
        template_kwargs: dict

    Return:
        out_wdl: str
    """
    return _render("task_template.wdl", RESERVED_WDL_NAMES, template_kwargs)
