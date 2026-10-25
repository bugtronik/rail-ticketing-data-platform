-- Distance travelled by each ticket: difference between the kilometre
-- points of its origin and destination on the line of its trip.
-- Needed for passenger-km, the numerator of the load factor.
SELECT
    t.ticket_id,
    ABS(d.km_from_origin - o.km_from_origin) AS distance_km
FROM {{ ref('stg_tickets') }} AS t
INNER JOIN {{ ref('stg_trips') }} AS tr ON t.trip_id = tr.trip_id
INNER JOIN {{ ref('stg_line_stops') }} AS o
    ON tr.line_id = o.line_id AND t.origin_station_id = o.station_id
INNER JOIN {{ ref('stg_line_stops') }} AS d
    ON tr.line_id = d.line_id AND t.destination_station_id = d.station_id
