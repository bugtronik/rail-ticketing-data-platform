-- The versions of a customer follow each other without gap or overlap: each
-- one ends exactly when the next one starts. An order then matches exactly
-- one version, never zero, never two.
WITH ordered AS (
    SELECT
        customer_id,
        version_number,
        valid_from,
        valid_to,
        LEAD(valid_from) OVER (PARTITION BY customer_id ORDER BY version_number) AS next_valid_from
    FROM {{ ref('dim_customer') }}
)

SELECT *
FROM ordered
WHERE
    valid_from >= valid_to
    OR (next_valid_from IS NOT NULL AND next_valid_from <> valid_to)
