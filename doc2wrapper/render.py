"""Shared Jinja2 plumbing for the target-language writers.

Each writer keeps its own module, templates and name lists, because that is
what "adding a target language means adding a writer plus a template" is supposed to
cost. What it should not also cost is a copy of the environment setup and of the
reasoning below, which is how the two existing writers came to be twenty-odd
byte-identical lines apart.

A writer needing different environment settings should stop calling this and build
its own, rather than growing this one a parameter.  Per-language *data* is a
different matter and already flows through as arguments: the template names and the
lists of names a target language will not accept, one for arguments (keywords and
names the block template declares) and one for task names, are how a writer says
what its target language wants.

A document is not a task, so it has a template of its own.  Statements such as WDL's
`version` or Nextflow's shebang may appear once per document, so they belong to the
document template, which is the only one that knows how many blocks are being
emitted.  Keeping them in the block template is how a document with two tasks came
to carry two version statements.  Blocks are rendered only inside `render_document`,
so no caller can obtain one and concatenate it with another: a lone WDL block would
still pass `miniwdl check`, because a version-less document falls back to
draft-2, where `~{...}` is not interpolation at all, so every declaration silently
goes unreferenced instead of being rejected.
"""

import jinja2

from .tasktree import rename_arguments, rename_if_reserved, unique_name


def _environment():
    """Build the environment the writers' templates are rendered in.

    Constructed per document rather than cached: a shared instance would be
    process-global mutable state, and generation happens once per invocation of a
    command-line tool.
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


def render_document(
    document_template, block_template, reserved, reserved_titles, tasks
):
    """Render a document wrapping one task or process per populated object model.

    Args:
        document_template: file name within `doc2wrapper/templates/` of the
            document, which receives the rendered blocks as `blocks`
        block_template: file name of one task or process
        reserved: names the target language will not accept for an argument,
            whether keywords or names the block template declares itself
        reserved_titles: words it will not accept as a task or process name, which
            need not be the same set: Nextflow resolves an input named `File` to a
            class, yet accepts a process of that name
        tasks: iterable of dicts carrying title, usage, cli_prefix, cli_args and
            has_output_file, in the reader's emission order, which the document
            preserves exactly -- ordering is part of the determinism guarantee.
            Note that emission order is not always source order: see
            `argparser.unpack_tasks`.

    Return:
        The complete document, as a string.  Argument names are unique within each
        block and titles within the document, the first holder of a name keeping
        it, because both languages reject a name declared twice.
    """
    environment = _environment()
    block = environment.get_template(block_template)
    titles = set()
    blocks = []
    for task in tasks:
        title = rename_if_reserved(task["title"], reserved_titles)
        blocks.append(
            block.render(
                **{
                    **task,
                    "title": unique_name(title, titles),
                    "cli_args": rename_arguments(task["cli_args"], reserved),
                }
            )
        )
    return environment.get_template(document_template).render(blocks=blocks)
