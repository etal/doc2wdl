# Provenance of the captured help texts used as test fixtures.
#
# Generation and checking now live in the pytest suite (tests/test_generate.py),
# which drives the readers and writers in process and runs `miniwdl check` and
# `nextflow lint` itself.  What is left here is the record of where each fixture
# came from, which nothing else captures.

all: help-cnvkit-antitarget.txt help-samtools-view.txt

help-samtools-view.txt:
	samtools help view > $@

help-cnvkit-antitarget.txt:
	cnvkit.py antitarget -h > $@
