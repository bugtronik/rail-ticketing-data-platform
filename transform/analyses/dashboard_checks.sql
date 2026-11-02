-- Expected values of the dashboard's measures, over all the data (no
-- filter): each card of the Power BI report must show exactly these
-- numbers. Same definitions as powerbi/README.md, computed in SQL: if a
-- visual disagrees, the DAX (or a relationship) is wrong, not the data.
--
--   dbt compile --select dashboard_checks  ... then run the compiled file
WITH tickets AS (
    SELECT
        COUNT(*) FILTER (WHERE f.is_paid) AS tickets_sold,
        COUNT(*) FILTER (WHERE f.is_cancelled) AS tickets_cancelled,
        SUM(f.sales_amount) AS gross_sales,
        SUM(f.refunded_amount) AS refunds,
        SUM(f.net_sales_amount) AS net_sales,
        SUM(f.sales_amount) FILTER (WHERE c.is_online) AS online_sales
    FROM {{ ref('fct_tickets') }} AS f
    INNER JOIN {{ ref('dim_channel') }} AS c ON f.channel_id = c.channel_id
),

trips AS (
    SELECT
        SUM(passenger_km) AS passenger_km,
        SUM(seat_km) AS seat_km
    FROM {{ ref('fct_trip_occupancy') }}
    WHERE trip_status = 'COMPLETED'
)

SELECT
    v.measure,
    v.expected_value
FROM tickets
CROSS JOIN trips
CROSS JOIN LATERAL (
    VALUES
    ('Billets vendus', tickets.tickets_sold::NUMERIC),
    ('CA brut (XAF)', tickets.gross_sales),
    ('Remboursements (XAF)', tickets.refunds),
    ('CA net (XAF)', tickets.net_sales),
    ('Prix moyen du billet (XAF)', ROUND(tickets.gross_sales / tickets.tickets_sold, 0)),
    ('Taux d''annulation (%)', ROUND(100.0 * tickets.tickets_cancelled / tickets.tickets_sold, 2)),
    ('Taux de remboursement (%)', ROUND(100.0 * tickets.refunds / tickets.gross_sales, 2)),
    ('Part du CA en ligne (%)', ROUND(100.0 * tickets.online_sales / tickets.gross_sales, 2)),
    ('Taux de remplissage (%)', ROUND(100.0 * trips.passenger_km / trips.seat_km, 2))
) AS v (measure, expected_value)
