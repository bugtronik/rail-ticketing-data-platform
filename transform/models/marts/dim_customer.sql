-- No personal data: the dashboard analyses segments, not people.
-- Row -1 stands for anonymous purchases, so that every ticket has a customer
-- and BI tools never show a blank member.
SELECT
    customer_id,
    city,
    loyalty_tier,
    CASE
        WHEN age < 25 THEN '18-24'
        WHEN age < 35 THEN '25-34'
        WHEN age < 50 THEN '35-49'
        WHEN age < 65 THEN '50-64'
        ELSE '65+'
    END AS age_band,
    created_at_local::DATE AS customer_since
FROM (
    SELECT
        *,
        DATE_PART('year', AGE(CURRENT_DATE, birth_date))::INT AS age
    FROM {{ ref('stg_customers') }}
) AS c

UNION ALL

SELECT
    -1 AS customer_id,
    'Inconnue' AS city,
    'ANONYME' AS loyalty_tier,
    'Inconnu' AS age_band,
    NULL AS customer_since
