-- Nobody buys a ticket for a train that has already left.
SELECT
    t.ticket_id,
    o.ordered_at_local,
    tr.departure_at_local
FROM {{ ref('stg_tickets') }} AS t
INNER JOIN {{ ref('stg_orders') }} AS o ON t.order_id = o.order_id
INNER JOIN {{ ref('stg_trips') }} AS tr ON t.trip_id = tr.trip_id
WHERE o.ordered_at_local > tr.departure_at_local
