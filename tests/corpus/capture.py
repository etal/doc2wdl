"""Recapture the help texts listed in `manifest.tsv` from the tools themselves.

Usage: python tests/corpus/capture.py [NAME ...]

Each manifest row names a shell command and the stream its help text is written
to; the text read from that stream becomes ``help-<name>.txt`` beside the manifest.
With no arguments every row is recaptured, otherwise only the rows named.  Every
tool must be on ``PATH``.

The environment is pinned because help text depends on it: argparse and clap wrap
to ``COLUMNS``, Python orders some listings by string hash, and a tool may color
its output or translate it.  Commands run with standard input from ``/dev/null`` in
an empty directory, so that no tool reads the caller's files.  The exit status is
ignored, since many tools exit nonzero after printing their help.

The test suite never runs this script.  A recapture changes bytes whenever a tool
or Python release does, so it is reviewed as a diff, and the outcome classes in the
manifest are checked by `tests/test_corpus.py`.
"""

import csv
import os
import subprocess
import sys
import tempfile
from pathlib import Path

CORPUS = Path(__file__).resolve().parent
PINNED = {
    "COLUMNS": "80",
    "LC_ALL": "C",
    "NO_COLOR": "1",
    "PYTHONHASHSEED": "0",
    "TERM": "dumb",
}


def read_manifest():
    """Return the manifest's rows as dicts, skipping its comment lines."""
    with open(CORPUS / "manifest.tsv", newline="", encoding="utf-8") as handle:
        lines = [line for line in handle if not line.startswith("#")]
    return list(csv.DictReader(lines, delimiter="\t"))


def capture(row, workdir):
    """Run one row's command and return the text of its help stream."""
    done = subprocess.run(
        row["command"],
        shell=True,
        cwd=workdir,
        env={**os.environ, **PINNED},
        stdin=subprocess.DEVNULL,
        capture_output=True,
        encoding="utf-8",
        check=False,
    )
    return getattr(done, row["stream"])


def main(names):
    rows = read_manifest()
    unknown = set(names) - {row["name"] for row in rows}
    if unknown:
        sys.exit(f"Not in the manifest: {', '.join(sorted(unknown))}")
    with tempfile.TemporaryDirectory() as workdir:
        for row in rows:
            if names and row["name"] not in names:
                continue
            text = capture(row, workdir)
            if not text.strip():
                sys.exit(f"{row['name']}: `{row['command']}` wrote no {row['stream']}")
            path = CORPUS / f"help-{row['name']}.txt"
            path.write_text(text, encoding="utf-8")
            print(f"{path.name}: {len(text.splitlines())} lines")


if __name__ == "__main__":
    main(sys.argv[1:])
