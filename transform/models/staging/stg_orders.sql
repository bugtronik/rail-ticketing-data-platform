SELECT
    order_id,
    order_reference,
    customer_id,
    channel_id,
    order_status,
    total_amount,
    currency,
    {{ to_local_time('ordered_at') }} AS ordered_at_local,
    -- When this version of the row reached the warehouse (set by the extractor):
    -- what the incremental models compare to find what changed.
    _extracted_at
FROM {{ source('raw', 'orders') }}
