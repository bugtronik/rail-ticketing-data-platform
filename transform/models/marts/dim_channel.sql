SELECT
    channel_id,
    channel_code,
    channel_name,
    is_online
FROM {{ ref('stg_sales_channels') }}
