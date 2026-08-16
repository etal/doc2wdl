#!/usr/bin/env nextflow
nextflow.enable.dsl=2

{{ blocks | join("\n\n") }}
