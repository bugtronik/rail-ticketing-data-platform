-- Q7. Average orders per day, month by month, and the change from the month
-- before. Compare it with month_factors in config/generator.toml (October /
-- September = 0.90 / 1.10, i.e. -18 %): the generator builds seasonality in,
-- so a drop between two months may be designed, not a business signal.
-- The first months of a history are incomplete: their trips were sold
-- before the history starts.
WITH daily AS (
    SELECT
        ordered_at_local::DATE AS order_day,
        COUNT(*) AS orders
    FROM {{ ref('stg_orders') }}
    GROUP BY ordered_at_local::DATE
),

monthly AS (
    SELECT
        DATE_TRUNC('month', order_day)::DATE AS month_start,
        COUNT(*) AS days_with_orders,
        AVG(orders) AS orders_per_day
    FROM daily
    GROUP BY DATE_TRUNC('month', order_day)
)

SELECT
    TO_CHAR(month_start, 'YYYY-MM') AS order_month,
    days_with_orders,
    ROUND(orders_per_day) AS orders_per_day,
    ROUND(
        100 * (orders_per_day / LAG(orders_per_day) OVER (ORDER BY month_start) - 1), 1
    ) AS pct_change_vs_previous_month
FROM monthly
ORDER BY month_start
