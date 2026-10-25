-- Business status of each ticket, decided ONCE for every report.
--
-- A ticket only counts as sold if its order was paid (CONFIRMED). Tickets of
-- an order whose payment failed are cancelled by the application, but they
-- were never sold: counting them would inflate the cancellation rate.
SELECT
    t.ticket_id,
    o.order_status = 'CONFIRMED' AS is_paid,
    o.order_status = 'CONFIRMED' AND t.ticket_status = 'CANCELLED' AS is_cancelled,
    o.order_status = 'CONFIRMED' AND t.ticket_status = 'USED' AS is_used,
    -- Holds a seat: paid and not cancelled (travelled, or still to travel).
    o.order_status = 'CONFIRMED' AND t.ticket_status <> 'CANCELLED' AS occupies_seat
FROM {{ ref('stg_tickets') }} AS t
INNER JOIN {{ ref('stg_orders') }} AS o ON t.order_id = o.order_id
