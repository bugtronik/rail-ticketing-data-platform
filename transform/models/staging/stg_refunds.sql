SELECT
    refund_id,
    ticket_id,
    payment_id,
    amount,
    reason,
    refund_status,
    {{ to_local_time('requested_at') }} AS requested_at_local,
    {{ to_local_time('processed_at') }} AS processed_at_local,
    -- When this version of the row reached the warehouse (set by the extractor):
    -- what the incremental models compare to find what changed.
    _extracted_at
FROM {{ source('raw', 'refunds') }}
