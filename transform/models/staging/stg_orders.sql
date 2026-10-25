SELECT
    order_id,
    order_reference,
    customer_id,
    channel_id,
    order_status,
    total_amount,
    currency,
    {{ to_local_time('ordered_at') }} AS ordered_at_local
FROM {{ source('raw', 'orders') }}
