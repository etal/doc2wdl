"""Nextflow serialization from the task object model."""

from .render import render_block, render_document

# Measured on Nextflow 25.10.4.  `nextflow lint` rejects the Groovy keywords listed
# first as `val NAME`; the rest lint clean but misbehave.  `true`, `false`, `null`,
# `threadsafe` and `strictfp` fail under `nextflow run`, and the others resolve to an
# implicit variable or a class, so the command line silently receives, say, the task
# map instead of the input's value.  Every class a Nextflow script imports by default
# resolves this way, so the class names here are the commonly used ones measured, not
# a complete set.
RESERVED_NF_NAMES = """
abstract as assert break case catch class const continue def default do else enum
extends final finally for goto if implements import instanceof interface native new
package private protected public return static super switch synchronized this throw
throws transient try void volatile while
true false null threadsafe strictfp
task it log session Channel channel System
boolean byte char double float int long short
Boolean Byte Character Double Float Integer Long Number Short String Object Math
Process Runtime Thread Collection List Map Set Date File Path Duration MemoryUnit
""".split()

# Process names are a different position: every capitalized name above lints and
# runs as a process name.  `Session` lints, but `nextflow run` cannot call a process
# of that name ("Missing process or function call"), measured on the same release.
RESERVED_NF_TITLES = ["Session"]


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
            render_block(
                "process_template.nf",
                reserved=RESERVED_NF_NAMES,
                reserved_titles=RESERVED_NF_TITLES,
                template_kwargs=task,
            )
            for task in tasks
        ],
    )
