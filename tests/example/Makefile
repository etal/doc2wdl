
all: help-cnvkit-antitarget.txt help-samtools-view.txt

.PSEUDO: test check clean
test: samtools-view.task.wdl cnvkit-antitarget.task.wdl cnvkit-antitarget-argparse.task.wdl

check: samtools-view.wdl-check.txt cnvkit-antitarget.wdl-check.txt cnvkit-antitarget-argparse.wdl-check.txt

clean:
	rm -vf samtools-view.task.wdl samtools-view.wdl-check.txt \
		cnvkit-antitarget.task.wdl cnvkit-antitarget.wdl-check.txt \
		cnvkit-antitarget-argparse.task.wdl cnvkit-antitarget-argparse.wdl-check.txt


cnvkit-antitarget.wdl-check.txt cnvkit-antitarget-argparse.wdl-check.txt samtools-view.wdl-check.txt: \
	%.wdl-check.txt: %.task.wdl
	miniwdl check $< > $@


cnvkit-antitarget.task.wdl samtools-view.task.wdl: \
	%.task.wdl: help-%.txt
	doc2wrapper docopt -f $< -o $@


cnvkit-antitarget-argparse.task.wdl:
	doc2wrapper argparse -m cnvlib.commands -p AP -o $@


help-samtools-view.txt:
	samtools help view > $@

help-cnvkit-antitarget.txt:
	cnvkit.py antitarget -h > $@

