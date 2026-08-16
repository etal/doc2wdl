"""Nextflow serialization from the task object model."""
from .render import render as _render

RESERVED_NF_NAMES = """
process
""".split()


def render(template_kwargs):
    """Render the Nextflow process template with the given values.

    Args:
        template_kwargs: dict

    Return:
        out_nf: str
    """
    return _render("process_template.nf", RESERVED_NF_NAMES, template_kwargs)
