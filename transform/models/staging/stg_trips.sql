SELECT
    trip_id,
    service_number,
    line_id,
    train_id,
    direction,
    trip_status,
    {{ to_local_time('scheduled_departure_at') }} AS departure_at_local,
    {{ to_local_time('scheduled_arrival_at') }} AS arrival_at_local
FROM {{ source('raw', 'trips') }}
