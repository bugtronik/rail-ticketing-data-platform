-- Q3. Cancellation and refund rates by fare type.
-- Denominator: paid tickets. An order whose payment failed was never a sale.
SELECT
    fare_type,
    COUNT(*) FILTER (WHERE is_paid) AS paid_tickets,
    ROUND(
        100.0 * COUNT(*) FILTER (WHERE is_cancelled)
        / NULLIF(COUNT(*) FILTER (WHERE is_paid), 0), 2
    ) AS cancellation_rate_pct,
    ROUND(
        100.0 * COUNT(*) FILTER (WHERE refunded_amount > 0)
        / NULLIF(COUNT(*) FILTER (WHERE is_paid), 0), 2
    ) AS refunded_tickets_pct,
    ROUND(100.0 * SUM(refunded_amount) / NULLIF(SUM(sales_amount), 0), 2) AS refunded_amount_pct
FROM {{ ref('fct_tickets') }}
GROUP BY fare_type
ORDER BY fare_type
