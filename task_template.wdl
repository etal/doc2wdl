version 1.1

{{ usage }}

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
      {% if arg.option_has_value %}
        ~{"{{ arg.option_flag }} " + {{ arg.name }}} \
      {% elif arg.option_flag is not none %}
        ~{if defined({{ arg.name }}) then "{{ arg.option_flag }}" else ""} \
      {% else %}
        {{ "~{" }}{{ arg.name }}{{ "}" }} \
      {% endif %}
    {% endfor %}

  >>>

  output {
    {% if has_output_file %}
      File output_file = if defined(output_file_name) then "$output_file_name" else stdout()
    {% else %}
      File output_file = stdout()
    {% endif %}
  }

  runtime {
    cpu: "1"
  }
}
