-- Grain: one ticket. The main fact table of the dashboard.
--
-- Two dates, two questions:
--   order_date      when it was SOLD: "ventes" (the commercial team's main view)
--   departure_date  when the passenger TRAVELS: realised revenue, load factor
-- Refunds stay attached to the original sale (net_sales_amount), so a period's
-- net sales can still move when its tickets are refunded later.
SELECT
    t.ticket_id,
    t.order_id,
    t.trip_id,
    tr.line_id,
    t.origin_station_id,
    t.destination_station_id,
    o.channel_id,
    COALESCE(o.customer_id, -1) AS customer_id,
    o.ordered_at_local::DATE AS order_date,
    EXTRACT(HOUR FROM o.ordered_at_local)::INT AS order_hour,
    tr.departure_at_local::DATE AS departure_date,
    t.fare_class,
    t.fare_type,
    t.passenger_type,
    t.ticket_status,
    s.is_paid,
    s.is_cancelled,
    s.is_used,
    s.occupies_seat,
    t.price AS ticket_price,
    CASE WHEN s.is_paid THEN t.price ELSE 0 END AS sales_amount,
    COALESCE(r.refunded_amount, 0) AS refunded_amount,
    CASE WHEN s.is_paid THEN t.price ELSE 0 END
    - COALESCE(r.refunded_amount, 0) AS net_sales_amount,
    j.distance_km,
    CASE WHEN s.occupies_seat THEN j.distance_km ELSE 0 END AS passenger_km
FROM {{ ref('stg_tickets') }} AS t
INNER JOIN {{ ref('stg_orders') }} AS o ON t.order_id = o.order_id
INNER JOIN {{ ref('stg_trips') }} AS tr ON t.trip_id = tr.trip_id
INNER JOIN {{ ref('int_ticket_status') }} AS s ON t.ticket_id = s.ticket_id
INNER JOIN {{ ref('int_ticket_journeys') }} AS j ON t.ticket_id = j.ticket_id
LEFT JOIN {{ ref('int_ticket_refunds') }} AS r ON t.ticket_id = r.ticket_id
