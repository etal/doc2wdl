doc2wdl
=======

Generate a WDL task wrapper from a tool's help text, using docopt for parsing.

See:

- https://github.com/openwdl/wdl
- https://github.com/broadinstitute/gatk/wiki/How-to-Prepare-a-GATK-tool-for-WDL-Auto-Generation
- https://support.terra.bio/hc/en-us/articles/360037120252
- http://docopt.org/


Usage
-----

    # Capture the help text from a tool
    mycommand --help > mycommand-help.txt
    # Generate a draft WDL task
    doc2wrapper docopt -f mycommand-help.txt -o mycommand.task.wdl
    # In one shot
    cat example-help.txt | doc2wrapper docopt > example.task.wdl

    # Python-specific introspection
    doc2wrapper argparse -m cnvlib.commands -p AP -o example.wdl


The generated WDL will very likely need further editing to work well, but hopefully this
approach will save you some time over writing each task from scratch.


Conceptual flow
---------------

- Readers (docopt, argparse) parse the given doc, transform contents, and populate the
  object model in memory.
- Writers (WDL, Nextflow) take the populated object model and use the static jinja2
  template to generate a string document.


Known issues
------------

- Type inference is thin. Every positional argument is typed `File`, which is wrong for a
  positional that is not a path — samtools' `region` is a genomic interval, not a file —
  and an option that takes no value is typed `String` rather than `Boolean`.
- Option help text does not reach `param_meta` when reading help text; the argparse
  reader does carry it through.
- Nextflow output is not yet reachable from the command line, and the process template
  still needs rewriting against `nextflow lint`.
- The `argparse` subcommand emits a document that WDL rejects when the parser has
  subcommands, because the version statement is repeated once per task.

Reading help text is best-effort by nature, and the limits are measured rather than
guessed. Over a corpus of 36 help texts, 34 of them captured from real tools, positional
arguments are recovered cleanly from about a quarter and usably from about a third, and
about one in ten produces no usable usage line at all. The reliable shape is a single
usage synopsis terminated by a blank line, naming at most one fixed subcommand, in which
no flag's arity differs between the options list and the synopsis. That describes
argparse-generated help well and hand-written getopt-style help poorly: where the options
list follows the synopsis with no blank line, or a heading reads `USAGE` without a colon,
or the same flag appears both bare and with a value, the result is a warning and a partial
task rather than a complete one.

None of that applies to the `argparse` subcommand, which introspects the parser object
directly and parses no text. It is the better route for a Python tool in principle, but
not yet in practice: the two defects listed above make its output invalid for any parser
carrying subcommands, so use it only for a flat parser until they are fixed.
