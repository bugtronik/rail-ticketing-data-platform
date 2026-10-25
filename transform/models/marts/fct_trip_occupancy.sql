-- Grain: one trip. Supply (seat-km) against demand (passenger-km).
--
-- Load factor = passenger-km / seat-km: a passenger going half the line
-- fills half a seat-journey. To aggregate it, SUM both and divide: an
-- AVERAGE of per-trip ratios would give a short empty trip the same weight
-- as a full long one.
-- A cancelled trip offered no seats: its seat-km is 0.
-- A SCHEDULED trip has not left yet: its load factor is "booked so far",
-- so occupancy KPIs keep COMPLETED trips only (see analyses/).
WITH demand AS (
    SELECT
        trip_id,
        COUNT(*) FILTER (WHERE occupies_seat) AS tickets_sold,
        SUM(passenger_km) AS passenger_km
    FROM {{ ref('fct_tickets') }}
    GROUP BY trip_id
)

SELECT
    tr.trip_id,
    tr.line_id,
    tr.departure_date,
    tr.departure_hour,
    tr.direction,
    tr.trip_status,
    tr.seats_total AS seats_offered,
    CASE WHEN tr.trip_status = 'CANCELLED' THEN 0 ELSE tr.seats_total * l.length_km END AS seat_km,
    COALESCE(d.tickets_sold, 0) AS tickets_sold,
    COALESCE(d.passenger_km, 0) AS passenger_km,
    CASE
        WHEN tr.trip_status <> 'CANCELLED'
            THEN ROUND(COALESCE(d.passenger_km, 0) / (tr.seats_total * l.length_km), 4)
    END AS load_factor
FROM {{ ref('dim_trip') }} AS tr
INNER JOIN {{ ref('dim_line') }} AS l ON tr.line_id = l.line_id
LEFT JOIN demand AS d ON tr.trip_id = d.trip_id
