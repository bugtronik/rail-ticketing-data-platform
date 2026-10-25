-- Q2. Load factor by line and month of DEPARTURE, departed trips only.
-- A ratio of sums: long full trips weigh more than short empty ones.
SELECT
    d.month_key,
    l.line_name,
    COUNT(*) AS trips,
    ROUND(SUM(o.passenger_km) / NULLIF(SUM(o.seat_km), 0), 3) AS load_factor
FROM {{ ref('fct_trip_occupancy') }} AS o
INNER JOIN {{ ref('dim_date') }} AS d ON o.departure_date = d.date_day
INNER JOIN {{ ref('dim_line') }} AS l ON o.line_id = l.line_id
WHERE o.trip_status = 'COMPLETED'
GROUP BY d.month_key, l.line_name
ORDER BY d.month_key, l.line_name
