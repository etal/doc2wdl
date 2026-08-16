"""WDL serialization from the task object model."""
from .render import render_block, render_document

RESERVED_WDL_NAMES = """
scatter
""".split()


def render(tasks):
    """Render a WDL document wrapping one task per given set of values.

    Args:
        tasks: iterable of template-keyword dicts, one per task, in the order the
            reader yielded them

    Return:
        out_wdl: str
    """
    return render_document(
        "document_template.wdl",
        [render_block("task_template.wdl", RESERVED_WDL_NAMES, task) for task in tasks],
    )
