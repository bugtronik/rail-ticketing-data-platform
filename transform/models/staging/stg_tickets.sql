SELECT
    ticket_id,
    order_id,
    trip_id,
    origin_station_id,
    destination_station_id,
    fare_class,
    fare_type,
    passenger_type,
    price,
    ticket_status,
    {{ to_local_time('cancelled_at') }} AS cancelled_at_local,
    {{ to_local_time('created_at') }} AS created_at_local,
    -- When this version of the row reached the warehouse (set by the extractor):
    -- what the incremental models compare to find what changed.
    _extracted_at
FROM {{ source('raw', 'tickets') }}
