#!/usr/bin/env python3
import sys

from wdlgen.doc2wdl import *

if __name__ == '__main__':
    with open(sys.argv[1]) as inf:
        #args = docopt.docopt(inf.read())
        doc = inf.read()
    usage, positionals, options = parse_doc(doc)
    print(f"Parsed {len(positionals)} positional and {len(options)} optional CLI arguments.",
          file=sys.stderr)
    out_wdl = render(transform(usage, positionals, options))
    print(out_wdl)
