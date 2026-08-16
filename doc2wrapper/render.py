"""Shared Jinja2 plumbing for the target-language writers.

Each writer keeps its own module, template and reserved-word list, because that is
what "adding a target language means adding a writer plus a template" is supposed to
cost. What it should not also cost is a copy of the environment setup and of the
reasoning below, which is how the two existing writers came to be twenty-odd
byte-identical lines apart.

A writer needing different environment settings should stop calling this and build
its own, rather than growing this one a parameter.
"""
import jinja2

from .tasktree import rename_reserved


def render(template_name, reserved, template_kwargs):
    """Render one task or process from the populated object model.

    Args:
        template_name: file name within `doc2wrapper/templates/`
        reserved: words the target language will not accept as identifiers
        template_kwargs: dict carrying title, usage, cli_prefix, cli_args and
            has_output_file

    Return:
        The rendered document, as a string.
    """
    env = jinja2.Environment(
        loader=jinja2.PackageLoader("doc2wrapper"),
        # Pinned off rather than fixed: select_autoescape() already resolves to False
        # for these extensions, so this preserves behaviour. It guards the case where
        # a template is renamed or loaded from a string and silently starts turning
        # the `&`, `<` and `>` of captured help text into HTML entities.
        autoescape=False,
        lstrip_blocks=True,
        trim_blocks=True,
    )
    return env.get_template(template_name).render(
        **{
            **template_kwargs,
            "cli_args": rename_reserved(template_kwargs["cli_args"], reserved),
        }
    )
