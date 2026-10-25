{#
    UTC timestamp (as stored by the source) -> local time of the network.
    `x AT TIME ZONE 'UTC'` reads x as UTC (gives a timestamptz), then
    `AT TIME ZONE <tz>` expresses that instant as a local wall-clock time.
#}
{% macro to_local_time(column) -%}
    (({{ column }} AT TIME ZONE 'UTC') AT TIME ZONE '{{ var("network_timezone") }}')
{%- endmacro %}
