#!/usr/bin/env python3
"""Console script endpoints.

Code layout:

    argparse2wdl.py -- wraps argparse -> wdl
        --> function in cli.py
    cli.py
        --> expose functions/endpoints {argparse,docopt}2wdl
    docopt2wdl.py -- wraps docopt -> wdl
        --> function in cli.py
    document_template.wdl, task_template.wdl
        --> ensure packaging can handle these (-> ./static/ ?
    wdlgen.py
        --> core module for WDL writer; check modularity

To create via refactor:
    tasktree.py -- core object model
    'argparser' argparse read/transform
    'docopter' docopt read/transform
    'nfgen' nextflow writer


Conceptual flow:
    - Readers (docopt, argparse) parse the given doc, transform contents, and populate
      the object model in memory
    - ENH: save to / load from intermediate .pkl of populated object model
    - Writers (WDL, Nextflow) take the populated object model and use
      the static jinja2 template to generate a string document

"""
import argparse
import sys

from . import docopter
from . import wdlgen


def docopt_to_wdl(filename):
    with open(filename) as inf:
        doc = inf.read()
    usage, positionals, options = docopter.parse(doc)
    print(f"Parsed {len(positionals)} positional and {len(options)} optional CLI arguments.",
          file=sys.stderr)
    out_wdl = wdlgen.render(docopter.transform(usage, positionals, options))
    return out_wdl


def main():
    """Console script for wdlgen."""
    parser = argparse.ArgumentParser()
    parser.add_argument('filename')
    args = parser.parse_args()

    out_wdl = docopt_to_wdl(args.filename)
    print(out_wdl)
    return 0


if __name__ == "__main__":
    sys.exit(main())  # pragma: no cover
