version 1.1

task {{ title }} {
  input {
    {% for arg in cli_args %}
      {{ arg.wdl_type }}{{ '' if arg.is_required else '?' }} {{ arg.name }}{% if arg.default_value is not none %} = {{ arg.default_value }}{% endif +%}
    {% endfor %}
  }
  parameter_meta {
    {% for arg in cli_args %}
      {{ arg.name }}: "{{ arg.doc }}"
    {% endfor %}
  }

  command <<<
    {{ cli_prefix }} \
    {% for arg in cli_args %}
      {% if arg.is_required %}
        {{ "~{" }}{{ arg.name }}{{ "}" }} \
      {% elif arg.option_has_value %}
        ~{"{{ arg.option_flag }}" + {{ arg.name }}} \
      {% else %}
        ~{if defined({{ arg.name }}) then "{{ arg.option_flag }}" else ""} \
      {% endif %}
    {% endfor %}

  >>>

  output {
    {% if has_output_file %}
      # ENH: fall back to stdout if output_file_name not defined
      File? output_file = "$output_file_name"
    {% else %}
      File output_file = stdout()
    {% endif %}
  }

  runtime {
    cpu: "1"
  }
}