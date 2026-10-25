SELECT
    channel_id,
    channel_code,
    channel_name,
    is_online
FROM {{ source('raw', 'sales_channels') }}
