-- Each customer has exactly one open version (valid_to = 9999-12-31).
-- None: the customer would vanish from current segments. Two: their sales
-- would be counted twice.
SELECT
    customer_id,
    COUNT(*) FILTER (WHERE is_current) AS current_versions
FROM {{ ref('dim_customer') }}
GROUP BY customer_id
HAVING COUNT(*) FILTER (WHERE is_current) <> 1
