SELECT
    trip_id,
    service_number,
    line_id,
    train_id,
    direction,
    trip_status,
    {{ to_local_time('scheduled_departure_at') }} AS departure_at_local,
    {{ to_local_time('scheduled_arrival_at') }} AS arrival_at_local,
    -- When this version of the row reached the warehouse (set by the extractor):
    -- what the incremental models compare to find what changed.
    _extracted_at
FROM {{ source('raw', 'trips') }}
