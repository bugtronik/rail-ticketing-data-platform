-- Every ticket must travel between two stops of its trip's line. A ticket
-- that fails this has no distance, so it silently drops out of
-- int_ticket_journeys (an inner join): this test catches it instead.
SELECT t.ticket_id
FROM {{ ref('stg_tickets') }} AS t
LEFT JOIN {{ ref('int_ticket_journeys') }} AS j ON t.ticket_id = j.ticket_id
WHERE j.ticket_id IS NULL
