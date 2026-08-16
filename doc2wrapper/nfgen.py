"""Nextflow serialization from the task object model."""
from .render import render_block, render_document

RESERVED_NF_NAMES = """
process
""".split()


def render(tasks):
    """Render a Nextflow script wrapping one process per given set of values.

    Args:
        tasks: iterable of template-keyword dicts, one per process, in the order
            the reader yielded them

    Return:
        out_nf: str
    """
    return render_document(
        "document_template.nf",
        [
            render_block("process_template.nf", RESERVED_NF_NAMES, task)
            for task in tasks
        ],
    )
