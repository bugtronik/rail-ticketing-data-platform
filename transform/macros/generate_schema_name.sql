{#
    By default dbt prefixes custom schemas with the target schema
    ("marts_staging"). Here each layer has a fixed schema created by the
    warehouse init script, so the custom name is used as is.
#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {{ custom_schema_name | trim if custom_schema_name else target.schema }}
{%- endmacro %}
