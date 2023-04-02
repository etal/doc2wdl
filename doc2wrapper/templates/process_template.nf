#!/usr/bin/env nextflow
nextflow.enable.dsl=2

/*
 * {{ usage }}
 */
process {{ title }} {

    input:
      {% for arg in cli_args %}
      val {{ arg.name }}
      {% endfor %}

    output:
      {% if has_output_file %}
        val output_file = "$output_file_name"
      {% else %}
        stdout emit: verbiage
      {% endif %}

    script:
      """
      {{ cli_prefix }} \
      {% for arg in cli_args %}
        {% if arg.option_has_value %}
          ${"{{ arg.option_flag }} " + {{ arg.name }}} \
        {% elif arg.option_flag is not none %}
          ${if defined({{ arg.name }}) then "{{ arg.option_flag }}" else ""} \
        {% else %}
          {{ "${" }}{{ arg.name }}{{ "}" }} \
        {% endif %}
      {% endfor %}
      """
}
