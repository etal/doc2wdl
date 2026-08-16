{% for line in usage.splitlines() %}
# {{ line }}
{% endfor %}

task {{ title }} {
  input {
    {% for arg in cli_args %}
      {% if arg.is_array %}
        {% if arg.is_required %}
    Array[{{ arg.wdl_type }}]+ {{ arg.name }}
        {% else %}
    Array[{{ arg.wdl_type }}] {{ arg.name }} = []
        {% endif %}
      {% else %}
    {{ arg.wdl_type }}{{ '' if arg.is_required else '?' }} {{ arg.name }}{% if arg.default_value is not none %} = {{ arg.default_value }}{% endif +%}
      {% endif %}
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
      {% if arg.is_positional %}
        {% if arg.is_array %}
    ~{sep(" ", {{ arg.name }})} \
        {% elif arg.is_required %}
    ~{ {{ arg.name }} } \
        {% else %}
    ~{select_first([{{ arg.name }}, ""])} \
        {% endif %}
      {% elif arg.option_has_value %}
        {% if arg.is_required %}
    {{ arg.option_flag }} ~{ {{ arg.name }} } \
        {% else %}
    ~{"{{ arg.option_flag }} " + {{ arg.name }}} \
        {% endif %}
      {% else %}
    ~{if defined({{ arg.name }}) then "{{ arg.option_flag }}" else ""} \
      {% endif %}
    {% endfor %}

  >>>

  output {
    {% if has_output_file %}
    File output_file = if defined(output_file_name) then select_first([output_file_name]) else stdout()
    {% else %}
    File output_file = stdout()
    {% endif %}
  }

  runtime {
    cpu: "1"
  }
}
