{% for line in usage.splitlines() %}
# {{ line }}
{% endfor %}

task {{ title }} {
  input {
    {% for arg in cli_args %}
      {% if arg.is_array %}
        {% if arg.is_required %}
    Array[{{ arg.value_type }}]+ {{ arg.name }}
        {% else %}
    Array[{{ arg.value_type }}] {{ arg.name }} = {{ arg.default_value if arg.default_value is not none else '[]' }}
        {% endif %}
      {% else %}
    {{ arg.value_type }}{{ '?' if arg.is_optional else '' }} {{ arg.name }}{% if arg.default_value is not none %} = {{ arg.default_value }}{% endif +%}
      {% endif %}
    {% endfor %}
  }
  parameter_meta {
    {% for arg in cli_args %}
    {{ arg.name }}: {{ arg.doc }}
    {% endfor %}
  }

  command <<<
    {{ cli_prefix }} \
    {% for arg in cli_args %}
      {% if arg.is_positional %}
        {% if arg.is_array %}
    ~{sep(" ", {{ arg.name }})} \
        {% elif not arg.is_optional %}
    ~{ {{ arg.name }} } \
        {% else %}
    ~{select_first([{{ arg.name }}, ""])} \
        {% endif %}
      {% elif arg.option_has_value %}
        {% if arg.is_array and arg.flag_per_value %}
    ~{sep(" ", prefix("{{ arg.option_flag }} ", {{ arg.name }}))} \
        {% elif arg.is_array and arg.is_required %}
    {{ arg.option_flag }} ~{sep(" ", {{ arg.name }})} \
        {% elif arg.is_array %}
    ~{if length({{ arg.name }}) > 0 then "{{ arg.option_flag }} " + sep(" ", {{ arg.name }}) else ""} \
        {% elif not arg.is_optional %}
    {{ arg.option_flag }} ~{ {{ arg.name }} } \
        {% else %}
    ~{"{{ arg.option_flag }} " + {{ arg.name }}} \
        {% endif %}
      {% else %}
        {% if arg.is_optional %}
    ~{if defined({{ arg.name }}) then (if select_first([{{ arg.name }}]) then "{{ arg.option_flag }}" else "{{ arg.false_flag }}") else ""} \
        {% else %}
    ~{if {{ arg.name }} then "{{ arg.option_flag }}" else "{{ arg.false_flag }}"} \
        {% endif %}
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
