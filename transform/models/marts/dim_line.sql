-- A line is a service pattern on the corridor (express, stopping, night).
WITH stops AS (
    SELECT
        line_id,
        COUNT(*) AS stop_count,
        MAX(km_from_origin) AS length_km
    FROM {{ ref('stg_line_stops') }}
    GROUP BY line_id
)

SELECT
    l.line_id,
    l.line_code,
    l.line_name,
    CASE l.line_code
        WHEN 'EXP' THEN 'Express'
        WHEN 'OMN' THEN 'Omnibus'
        WHEN 'NUI' THEN 'Nuit'
        ELSE 'Autre'
    END AS service_type,
    s.stop_count,
    s.length_km
FROM {{ ref('stg_rail_lines') }} AS l
INNER JOIN stops AS s ON l.line_id = s.line_id
