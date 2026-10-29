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

Both readers type arguments by one rule: a flag that takes no value is a `Boolean`, then a declared `type` decides, then the type of a stated default, and otherwise a positional argument is a `File` and an option a `String`.
A default the type cannot hold is dropped, and the tool's own default then applies.
A WDL task declares a floating-point number as a `String`, because WDL prints a `Float` with exactly six decimal places, so `1e-10` would reach the tool as `0.000000`; supply such a value as text, as in `"1e-10"`, in an inputs file.


Conceptual flow
---------------

- Readers (docopt, argparse) parse the given doc, transform contents, and populate the
  object model in memory.
- Writers (WDL, Nextflow) take the populated object model and render it through the static jinja2 templates; the WDL writer also spells the model's value types as WDL types and its Python values as WDL literals.


Known issues
------------

- A positional argument with no other evidence of its type is a `File`, which is right for most bioinformatics tools and wrong for one that is not a path, such as samtools' `region`, a genomic interval; change it to `String` by hand.
  Help text shows no type for an option, so an option that takes a value is typed from help text only by its `[default: ...]` literal, or by a type its description names on a line of its own, as bedtools writes `- FLOAT (e.g. 0.50)`, and is otherwise a `String`.
  An option whose entry writes no placeholder for its value and names no such type, as bedtools' `-g` does, becomes a Boolean, so the generated task cannot pass its value.
  A flag that no entry documents takes a value where the synopsis writes a placeholder directly after every mention of it, as bedtools writes `-a <bed/gff/vcf/bam>`.
- Help text gives `parameter_meta` an option's description when it follows the flags on the same line after at least two spaces, which is docopt's rule, or when it begins on the lines after the flags; samtools writes `-R, --read-group-file FILE ...are in a read group listed in FILE` with one space, so that description is empty.
  A description ends at a blank line or at a line indented no deeper than its option, so only its first paragraph is kept.
  A positional argument takes the description of an entry that names it in the same layout, as argparse lists it under "positional arguments:", followed by its alternative spellings, if it has any.
- A `count` option such as `-v` becomes a Boolean, so the generated task can pass it at most once.
- An option whose flag carries brackets other than an optional part of its name, such as `--color[=WHEN]`, is left out with a warning, because a task cannot pass a value attached to its flag optionally; an optional part, as in samtools' `--region[s]-file`, is dropped from the flag instead.
  Other notation that names no flag the task can pass, such as absl's trailing colon in bq's `--apilog:`, a placeholder in the name, as in gcc's `-W<warning>`, or the end-of-options marker `--`, is left out the same way, and two spellings joined by a bar, as in `-A|--accession`, pass the long one.
- Where the help text declares one flag more than once, the task passes it from one input, the first declaration with a description, and leaves the others out with a warning.

Reading help text is best-effort by nature, and its limits are recorded rather than guessed.
`tests/corpus/` holds 14 help texts captured from real tools, chosen to represent the shapes the reader meets rather than as a sample, and `tests/corpus/manifest.tsv` records the command that produced each and what the reader makes of it.
Three of the 14 produce no usable usage line at all, and eleven parse, of which three yield exactly the tool's positional arguments; the others add placeholders such as `[OPTIONS]` or option values, or split one argument in two.
The reliable shape is a single usage synopsis naming at most one fixed subcommand, written directly after the program name, in which no flag's arity differs between the options list and the synopsis, and which ends at a blank line, at a line indented less than the program name, or, where the `Usage:` marker stands alone on its line, at a line of prose.
A Cobra heading that reads `USAGE` without a colon is not found at all.

None of that applies to the `argparse` subcommand, which introspects the parser object directly and parses no text.
It is the better route for a Python tool: it emits one document for a parser with subcommands.
A default that does not fit its argument's declared `type`, such as a float default of an `int` option, is dropped rather than declared, so the generated task leaves that option unset.
An option declared with an action class of the tool's own is passed by its command-line shape alone, without its default, and with a warning naming the class; a version flag declared that way, as mypy's is, becomes a switch like any other.
