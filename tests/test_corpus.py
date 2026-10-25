"""Conformance over help texts captured from real tools.

`tests/corpus/manifest.tsv` names each capture, the command that produced it, and
the outcome the help-text reader reaches on it.  The outcome is a class rather than
generated text, so a template edit never touches it, while a reader change that
moves a file from one class to another fails here.  The two fixtures in
`tests/example` yield exactly their positionals; most of these files do not, and
that is the point.
"""

import csv
from pathlib import Path

import pytest

from doc2wrapper import docopter

CORPUS = Path(__file__).parent / "corpus"


def _manifest():
    with open(CORPUS / "manifest.tsv", newline="", encoding="utf-8") as handle:
        lines = [line for line in handle if not line.startswith("#")]
    return list(csv.DictReader(lines, delimiter="\t"))


MANIFEST = _manifest()


def _outcome(doc, capsys):
    """Classify what `docopter.parse` makes of one help text.

    A degraded parse returns normally, with no positionals, and says so only on
    standard error; an empty list alone is no signal, since a tool may take none.
    """
    try:
        docopter.parse(doc)
    except docopter.UsageNotFound:
        return "usage-not-found"
    if "could not read positional arguments" in capsys.readouterr().err:
        return "degraded"
    return "parsed"


@pytest.mark.parametrize("row", MANIFEST, ids=lambda row: row["name"])
def test_capture_keeps_its_outcome_class(row, capsys):
    doc = (CORPUS / f"help-{row['name']}.txt").read_text(encoding="utf-8")
    assert _outcome(doc, capsys) == row["outcome"]


def test_manifest_lists_every_capture():
    """A capture with no manifest row has no provenance and is never classified."""
    captured = {path.name for path in CORPUS.glob("help-*.txt")}
    assert captured == {f"help-{row['name']}.txt" for row in MANIFEST}
