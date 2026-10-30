# Provenance of the captured help texts used as test fixtures.
#
# Generation and checking now live in the pytest suite (tests/test_generate.py),
# which drives the readers and writers in process and runs `miniwdl check` and
# `nextflow lint` itself.  What is left here is the record of where each fixture
# came from, which nothing else captures.
#
# Both fixtures were captured on 2022-11-18 and are kept as captured, because the
# test suite asserts on what they generate.  Running these recipes today produces
# different bytes, as each note below records.

all: help-cnvkit-antitarget.txt help-samtools-view.txt

# samtools 1.15, 1.15.1, 1.16 or 1.16.1, whose usage text for `view` is identical:
# the fixture lists --fetch-pairs, added in 1.15, and lacks --sanitize, added in
# 1.17.  `samtools help view` runs `samtools view` with no arguments, which prints
# this short usage only when standard input is a terminal and otherwise reads
# alignments from standard input; `samtools view --help` prints a longer usage.
help-samtools-view.txt:
	samtools help view > $@

# CNVkit 0.9.12 or earlier, most likely 0.9.9, the current release on that date,
# under Python 3.12 or earlier.  Python 3.13 writes an option's placeholder once,
# as in `-g, --access FILENAME`, and CNVkit 0.9.13 rewrote the help of --access.
help-cnvkit-antitarget.txt:
	cnvkit.py antitarget -h > $@
