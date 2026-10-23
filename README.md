doc2wdl
=======

Generate a WDL task or Nextflow process wrapper from a tool's help text, using docopt for parsing.

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

    # Nextflow instead of WDL, for either subcommand
    doc2wrapper docopt -t nextflow -f mycommand-help.txt -o mycommand.nf


The generated wrapper will very likely need further editing to work well, but hopefully this
approach will save you some time over writing each task from scratch.

An argument whose name the target language reserves takes a trailing underscore, as `--output` becomes the WDL input `output_`, and the command line still passes the tool's own flag.
Task names are checked against a separate list per language, so a tool named `file` becomes the WDL task `File_` but keeps the Nextflow process name `File`.

A Nextflow process input cannot be left out, and passing null fails the run, so a generated process expects `''` for an option or switch left unset and `[]` for a list with no values; the flag is then omitted and the tool's own default applies.
Every input is a `val`, so a file input is passed as a path string rather than staged; change it to `path` by hand where staging is wanted.


Conceptual flow
---------------

- Readers (docopt, argparse) parse the given doc, transform contents, and populate the
  object model in memory.
- Writers (WDL, Nextflow) take the populated object model and render it through the static jinja2 templates; the WDL writer also spells the model's Python values as WDL literals.


Known issues
------------

- Type inference is thin.
  Every positional argument read from help text is typed `File`, which is wrong for a positional that is not a path — samtools' `region` is a genomic interval, not a file — and an option that takes no value is typed `String` rather than `Boolean` there.
- Option help text does not reach `parameter_meta` when reading help text; the argparse
  reader does carry it through.
- A `count` option such as `-v` becomes a Boolean, so the generated task can pass it at most once.

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

None of that applies to the `argparse` subcommand, which introspects the parser object directly and parses no text.
It is the better route for a Python tool: it emits one document for a parser with subcommands.
Its inferred types still follow each argument's declared `type`, so an `int` option whose default is a float produces a declaration WDL rejects; check the output before trusting it.
