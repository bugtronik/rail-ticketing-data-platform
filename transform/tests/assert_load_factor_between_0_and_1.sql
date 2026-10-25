-- More passenger-km than seat-km would mean we sold a seat twice.
SELECT
    trip_id,
    seat_km,
    passenger_km,
    load_factor
FROM {{ ref('fct_trip_occupancy') }}
WHERE
    load_factor < 0
    OR load_factor > 1
    OR (trip_status = 'CANCELLED' AND passenger_km > 0)
