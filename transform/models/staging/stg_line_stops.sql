SELECT
    line_stop_id,
    line_id,
    station_id,
    stop_sequence,
    km_from_origin
FROM {{ source('raw', 'line_stops') }}
