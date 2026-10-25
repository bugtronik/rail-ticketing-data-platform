-- One scheduled run of a train. Used by both fact tables.
SELECT
    tr.trip_id,
    tr.service_number,
    tr.line_id,
    tr.direction,
    tr.trip_status,
    tr.departure_at_local,
    tr.departure_at_local::DATE AS departure_date,
    EXTRACT(HOUR FROM tr.departure_at_local)::INT AS departure_hour,
    t.train_code,
    t.train_model,
    t.seats_total
FROM {{ ref('stg_trips') }} AS tr
INNER JOIN {{ ref('stg_trains') }} AS t ON tr.train_id = t.train_id
