SELECT
    payment_id,
    order_id,
    payment_method,
    amount,
    payment_status,
    {{ to_local_time('attempted_at') }} AS attempted_at_local,
    {{ to_local_time('settled_at') }} AS settled_at_local
FROM {{ source('raw', 'payments') }}
