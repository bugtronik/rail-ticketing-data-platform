SELECT
    refund_id,
    ticket_id,
    payment_id,
    amount,
    reason,
    refund_status,
    {{ to_local_time('requested_at') }} AS requested_at_local,
    {{ to_local_time('processed_at') }} AS processed_at_local
FROM {{ source('raw', 'refunds') }}
