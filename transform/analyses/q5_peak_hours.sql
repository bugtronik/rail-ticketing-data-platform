-- Q5. Peak hours: when do people BUY (order_hour) and when do they TRAVEL
-- (departure hour of the trains they fill)? Local time.
WITH buying AS (
    SELECT
        order_hour AS hour_of_day,
        COUNT(*) FILTER (WHERE is_paid) AS tickets_bought
    FROM {{ ref('fct_tickets') }}
    GROUP BY order_hour
),

travelling AS (
    SELECT
        departure_hour AS hour_of_day,
        SUM(tickets_sold) AS passengers_departing
    FROM {{ ref('fct_trip_occupancy') }}
    WHERE trip_status = 'COMPLETED'
    GROUP BY departure_hour
)

SELECT
    b.hour_of_day,
    b.tickets_bought,
    COALESCE(t.passengers_departing, 0) AS passengers_departing
FROM buying AS b
LEFT JOIN travelling AS t ON b.hour_of_day = t.hour_of_day
ORDER BY b.hour_of_day
