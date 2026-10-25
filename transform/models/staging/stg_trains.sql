SELECT
    train_id,
    train_code,
    train_model,
    seats_first,
    seats_second,
    seats_first + seats_second AS seats_total,
    is_active
FROM {{ source('raw', 'trains') }}
