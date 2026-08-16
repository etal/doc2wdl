"""WDL serialization from the task object model."""
import jinja2

from .tasktree import rename_reserved

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
    env = jinja2.Environment(
        loader=jinja2.PackageLoader("doc2wrapper"),
        # Pinned off rather than fixed: select_autoescape() already resolves to
        # False for these extensions, so this preserves behaviour. It guards the
        # case where a template is renamed or loaded from a string and silently
        # starts turning the `&`, `<` and `>` of captured help text into entities.
        autoescape=False,
        lstrip_blocks=True,
        trim_blocks=True,
    )
    template = env.get_template("task_template.wdl")
    out_wdl = template.render(
        **{
            **template_kwargs,
            "cli_args": rename_reserved(
                template_kwargs["cli_args"], RESERVED_WDL_NAMES
            ),
        }
    )
    return out_wdl
