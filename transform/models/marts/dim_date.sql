-- One row per day, from the first sale to the last scheduled departure.
-- Labels in French: they are displayed as is in the dashboard.
WITH bounds AS (
    SELECT
        (SELECT MIN(ordered_at_local)::DATE FROM {{ ref('stg_orders') }}) AS first_day,
        (SELECT MAX(departure_at_local)::DATE FROM {{ ref('stg_trips') }}) AS last_day
),

days AS (
    SELECT GENERATE_SERIES(first_day, last_day, INTERVAL '1 day')::DATE AS date_day
    FROM bounds
)

SELECT
    date_day,
    EXTRACT(YEAR FROM date_day)::INT AS year_number,
    EXTRACT(QUARTER FROM date_day)::INT AS quarter_number,
    EXTRACT(MONTH FROM date_day)::INT AS month_number,
    (ARRAY[
        'janvier', 'février', 'mars', 'avril', 'mai', 'juin', 'juillet',
        'août', 'septembre', 'octobre', 'novembre', 'décembre'
    ])[EXTRACT(MONTH FROM date_day)::INT] AS month_name,
    TO_CHAR(date_day, 'YYYY-MM') AS month_key,
    EXTRACT(WEEK FROM date_day)::INT AS iso_week,
    EXTRACT(ISODOW FROM date_day)::INT AS day_of_week,
    (ARRAY['lundi', 'mardi', 'mercredi', 'jeudi', 'vendredi', 'samedi', 'dimanche'])[
        EXTRACT(ISODOW FROM date_day)::INT
    ] AS day_name,
    EXTRACT(ISODOW FROM date_day) IN (6, 7) AS is_weekend
FROM days
