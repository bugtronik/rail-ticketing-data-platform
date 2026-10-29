-- Refunds paid back, read straight from raw, must equal those in the mart.
-- A refund is often paid weeks after the sale: if the incremental run
-- missed it, the ticket's row would keep its old refunded_amount and only
-- this test would notice (the sales reconciliation would still pass).
WITH from_raw AS (
    SELECT SUM(amount) AS amount
    FROM {{ source('raw', 'refunds') }}
    WHERE refund_status = 'PAID'
),

from_mart AS (
    SELECT SUM(refunded_amount) AS amount
    FROM {{ ref('fct_tickets') }}
)

SELECT
    from_raw.amount AS raw_amount,
    from_mart.amount AS mart_amount
FROM from_raw CROSS JOIN from_mart
WHERE from_raw.amount IS DISTINCT FROM from_mart.amount
