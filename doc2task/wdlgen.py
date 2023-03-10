"""WDL serialization from the task object model."""
import jinja2


RESERVED_WDL_NAMES = set("""
scatter
""".split())


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
