"""Shared Jinja2 plumbing for the target-language writers.

Each writer keeps its own module, templates and reserved-word list, because that is
what "adding a target language means adding a writer plus a template" is supposed to
cost. What it should not also cost is a copy of the environment setup and of the
reasoning below, which is how the two existing writers came to be twenty-odd
byte-identical lines apart.

A writer needing different environment settings should stop calling this and build
its own, rather than growing this one a parameter.  Per-language *data* is a
different matter and already flows through as arguments: the template name and the
reserved-word list are how a writer says what its target language wants.

Rendering happens at two levels because a document is not a task.  Statements such
as WDL's `version` or Nextflow's shebang may appear once per document, so they
belong to `render_document`, which is the only layer that knows how many blocks are
being emitted.  Keeping them in the block template is how a document with two tasks
came to carry two version statements.
"""
import jinja2

from .tasktree import rename_reserved


def _environment():
    """Build the environment the writers' templates are rendered in.

    Constructed per call rather than cached, which costs one template compilation
    per block: a shared instance would be process-global mutable state, and
    generation happens once per invocation of a command-line tool.  Measured at
    roughly 3 ms per environment, so the price is paid by the largest parsers --
    around 165 ms for fifty subcommands -- and is invisible for ordinary ones.
    """
    return jinja2.Environment(
        loader=jinja2.PackageLoader("doc2wrapper"),
        # Pinned off rather than fixed: select_autoescape() already resolves to False
        # for these extensions, so this preserves behaviour. It guards the case where
        # a template is renamed or loaded from a string and silently starts turning
        # the `&`, `<` and `>` of captured help text into HTML entities.
        autoescape=False,
        lstrip_blocks=True,
        trim_blocks=True,
    )


def render_block(template_name, reserved, template_kwargs):
    """Render one task or process from the populated object model.

    Args:
        template_name: file name within `doc2wrapper/templates/`
        reserved: words the target language will not accept as identifiers
        template_kwargs: dict carrying title, usage, cli_prefix, cli_args and
            has_output_file

    Return:
        The rendered block, as a string.  It is a fragment: the document statements
        `render_document` supplies are missing.  Do not be reassured by a checker
        accepting one on its own -- `miniwdl check` accepts a version-less WDL block
        and falls back to draft-2, where `~{...}` is not interpolation at all, so
        every declaration silently goes unreferenced instead of being rejected.
    """
    template = _environment().get_template(template_name)
    return template.render(
        **{
            **template_kwargs,
            "cli_args": rename_reserved(template_kwargs["cli_args"], reserved),
        }
    )


def render_document(template_name, blocks):
    """Wrap already-rendered blocks in their target language's document preamble.

    Args:
        template_name: file name within `doc2wrapper/templates/`
        blocks: rendered task or process blocks, in the reader's emission order,
            which the document preserves exactly -- ordering is part of the
            determinism guarantee.  Note that emission order is not always source
            order: see `argparser.unpack_tasks`.

    Return:
        The complete document, as a string.
    """
    template = _environment().get_template(template_name)
    return template.render(blocks=list(blocks))
