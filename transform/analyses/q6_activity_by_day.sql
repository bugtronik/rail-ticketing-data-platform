-- Q6. Is every day complete? Orders per day over the last 45 days, the
-- number of hours with at least one order, and the longest silence.
-- At night the generator still sells online every few minutes: a silence of
-- several hours means the generator (or the machine running it) was stopped,
-- and the day is not comparable with the others.
WITH latest AS (
    SELECT MAX(ordered_at_local) AS last_order_at
    FROM {{ ref('stg_orders') }}
),

orders AS (
    SELECT o.ordered_at_local
    FROM {{ ref('stg_orders') }} AS o
    CROSS JOIN latest AS l
    WHERE o.ordered_at_local >= l.last_order_at - INTERVAL '45 days'
),

gaps AS (
    SELECT
        ordered_at_local,
        ordered_at_local - LAG(ordered_at_local) OVER (ORDER BY ordered_at_local) AS silence_before
    FROM orders
)

SELECT
    ordered_at_local::DATE AS order_day,
    TO_CHAR(ordered_at_local::DATE, 'Dy') AS day_name,
    COUNT(*) AS orders,
    COUNT(DISTINCT EXTRACT(HOUR FROM ordered_at_local)) AS hours_with_orders,
    ROUND(EXTRACT(EPOCH FROM MAX(silence_before)) / 3600, 1) AS longest_silence_hours
FROM gaps
GROUP BY ordered_at_local::DATE
ORDER BY order_day
