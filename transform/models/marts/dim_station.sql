SELECT
    station_id,
    station_code,
    station_name,
    city,
    province,
    is_active
FROM {{ ref('stg_stations') }}
