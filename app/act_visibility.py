"""Which acts a customer may see listed.

A Tender Service act that is the same tender as another act is kept but hidden:
procurement_act.duplicate_of points at the act we show (tsg_match.py,
docs/specs/tender-service-duplicates.md). Every customer-facing list skips it.

Written as an anti-join on purpose. `a.duplicate_of IS NULL` makes every
sequential scan unpack each row down to its LAST column and measured ~3x slower
on filtered counts over 2.9M acts; this form reads only the adam and the tiny
partial index ix_act_duplicate_of. Requires the act table aliased as `a`.
"""

VISIBLE_SQL = ("NOT EXISTS (SELECT 1 FROM proc.procurement_act hid "
               "WHERE hid.duplicate_of IS NOT NULL AND hid.adam = a.adam)")
