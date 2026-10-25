-- A ticket can be refunded at most once, and never more than it cost.
SELECT
    ticket_id,
    ticket_price,
    refunded_amount
FROM {{ ref('fct_tickets') }}
WHERE
    refunded_amount > ticket_price
    OR refunded_amount < 0
