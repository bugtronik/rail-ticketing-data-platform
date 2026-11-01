-- Reconciliation: the mart's sales must equal, to the franc, the price of
-- the tickets of confirmed orders, read straight from raw (bypassing every
-- model). Catches joins that lose or duplicate tickets on the way.
--
-- Counted per ticket, not with orders.total_amount: the tables are extracted
-- one after the other while the application writes, so an order created
-- during an extraction can be in raw before its tickets. It then counts
-- for 0 on both sides until the next run brings the tickets.
WITH from_raw AS (
    SELECT SUM(t.price) AS amount
    FROM {{ source('raw', 'tickets') }} AS t
    INNER JOIN {{ source('raw', 'orders') }} AS o ON t.order_id = o.order_id
    WHERE o.order_status = 'CONFIRMED'
),

from_mart AS (
    SELECT SUM(sales_amount) AS amount
    FROM {{ ref('fct_tickets') }}
)

SELECT
    from_raw.amount AS raw_amount,
    from_mart.amount AS mart_amount
FROM from_raw CROSS JOIN from_mart
WHERE from_raw.amount IS DISTINCT FROM from_mart.amount
