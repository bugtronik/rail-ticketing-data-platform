-- Money actually given back per ticket. Only PAID refunds count: a
-- requested or approved refund has not left the bank account yet.
SELECT
    ticket_id,
    SUM(amount) FILTER (WHERE refund_status = 'PAID') AS refunded_amount,
    COUNT(*) FILTER (WHERE refund_status <> 'REJECTED') AS refund_requests
FROM {{ ref('stg_refunds') }}
GROUP BY ticket_id
