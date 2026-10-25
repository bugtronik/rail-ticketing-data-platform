SELECT
    line_id,
    line_code,
    line_name,
    is_active
FROM {{ source('raw', 'rail_lines') }}
