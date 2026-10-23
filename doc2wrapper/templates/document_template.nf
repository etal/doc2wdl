#!/usr/bin/env nextflow
// A process input cannot be left out: pass '' to leave a value or switch unset,
// and [] to give a list input no values.

{{ blocks | join("\n\n") }}
