{% for line in usage.splitlines() %}
// {{ line }}
{% endfor %}
process {{ title }} {

    {% if cli_args %}
    input:
    {% for arg in cli_args %}
    val {{ arg.name }}
    {% endfor %}

    {% endif %}
    output:
    {% if has_output_file %}
    path "${output_file_name}", emit: output_file, optional: true
    {% endif %}
    stdout emit: stdout

    script:
    """
    {{ cli_prefix }} \
    {% for arg in cli_args %}
      {% if arg.is_positional %}
        {% if arg.is_array %}
    ${ {{ arg.name }}.join(' ') } \
        {% else %}
    ${ {{ arg.name }} } \
        {% endif %}
      {% elif arg.option_has_value %}
        {% if arg.is_array and arg.flag_per_value %}
    ${ {{ arg.name }} ? '{{ arg.option_flag }} ' + {{ arg.name }}.join(' {{ arg.option_flag }} ') : '' } \
        {% elif arg.is_array and arg.is_required %}
    {{ arg.option_flag }} ${ {{ arg.name }}.join(' ') } \
        {% elif arg.is_array %}
    ${ {{ arg.name }} ? '{{ arg.option_flag }} ' + {{ arg.name }}.join(' ') : '' } \
        {% elif arg.is_required %}
    {{ arg.option_flag }} ${ {{ arg.name }} } \
        {% else %}
    ${ {{ arg.name }} != '' ? '{{ arg.option_flag }} ' + {{ arg.name }} : '' } \
        {% endif %}
      {% elif arg.false_flag %}
    ${ {{ arg.name }} == true ? '{{ arg.option_flag }}' : {{ arg.name }} == false ? '{{ arg.false_flag }}' : '' } \
      {% else %}
    ${ {{ arg.name }} ? '{{ arg.option_flag }}' : '' } \
      {% endif %}
    {% endfor %}
    """
}
