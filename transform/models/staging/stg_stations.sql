SELECT
    station_id,
    station_code,
    station_name,
    city,
    region AS province,
    is_active
FROM {{ source('raw', 'stations') }}
