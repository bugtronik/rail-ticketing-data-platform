-- Reconciliation: the mart's sales must equal, to the franc, the total of
-- the confirmed orders read straight from raw (bypassing every model).
-- Catches joins that lose or duplicate tickets on the way.
WITH from_raw AS (
    SELECT SUM(total_amount) AS amount
    FROM {{ source('raw', 'orders') }}
    WHERE order_status = 'CONFIRMED'
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
