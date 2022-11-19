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
    doc2wdl.py mycommand-help.txt > mycommand.task.wdl

The generated WDL will very likely need further editing to work well, but hopefully this
approach will save you some time over writing each task from scratch.


Known issues
------------

- Option help text doesn't get transferred to `param_meta` automatically (yet).
- Type inference is not perfect.
- Arrayed types and other non-primitive types won't be detected, e.g. options that can
  be repeated on the command line.
- Only one output file from the command will be picked up in the task output.
- Quirks in the original command's help text may slip through; docopt doesn't attempt to
  catch everything (it really wasn't designed for this purpose).

