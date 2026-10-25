-- Origin and destination must differ: a 0 km ticket would carry revenue
-- but no passenger-km, and bias the load factor.
SELECT
    ticket_id,
    distance_km
FROM {{ ref('fct_tickets') }}
WHERE distance_km <= 0
