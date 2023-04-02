"""Nextflow serialization from the task object model."""
import jinja2

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
    env = jinja2.Environment(
        loader=jinja2.PackageLoader("doc2wrapper"),
        autoescape=jinja2.select_autoescape(),
        lstrip_blocks=True,
        trim_blocks=True,
    )
    template = env.get_template("process_template.nf")
    out_nf = template.render(**template_kwargs)
    return out_nf
