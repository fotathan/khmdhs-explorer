"""Merge-aware, customer-safe authority and contractor summary reads."""
from __future__ import annotations

from decimal import Decimal
from urllib.parse import urlparse

from app import mobile_search
from app.act_visibility import VISIBLE_SQL
from app.api_v1.schemas import (
    EntityContact,
    EntityHeadline,
    EntityRef,
    EntitySummary,
    LookupItem,
    Money,
)


_RECENT_SELECT = """
    a.adam, a.type, a.title, a.signed_date, a.submission_date,
    a.final_submission_date, a.total_cost_with_vat,
    proc.resolved_value(a.adam, a.total_cost_with_vat) AS resolved_value,
    (proc.resolved_value(a.adam, a.total_cost_with_vat)
        IS DISTINCT FROM a.total_cost_with_vat) AS is_corrected,
    a.cancelled, a.is_modified, a.contract_type_code,
    a.procedure_type_code, a.nuts_code, a.data_source,
    auth.org_id AS authority_id, auth.name AS authority_name
"""


def _money(value) -> Money:
    return Money(amount=format(Decimal(value or 0), "f"))


def _safe_https(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parsed = urlparse(value)
        if parsed.scheme != "https" or not parsed.hostname:
            return None
        if parsed.username or parsed.password:
            return None
        return value
    except Exception:
        return None


def _address(*parts) -> str | None:
    clean = [str(part).strip() for part in parts if part and str(part).strip()]
    return ", ".join(clean) or None


def _contact(row: dict) -> EntityContact | None:
    contact = EntityContact(
        email=row.get("contact_email"), phone=row.get("contact_phone"),
        website=_safe_https(row.get("contact_url")),
        address=_address(row.get("street_address"), row.get("postal_code"),
                         row.get("city"), row.get("country")),
    )
    return contact if any(contact.model_dump().values()) else None


def resolve_group(c, kind: str, key: str) -> dict | None:
    c.execute("""SELECT group_id FROM proc.entity_member
                 WHERE kind=%s AND member_key=%s""", (kind, key))
    row = c.fetchone()
    if not row:
        return None
    c.execute("""SELECT canonical_key, display_name FROM proc.entity_group
                 WHERE id=%s AND kind=%s""", (row["group_id"], kind))
    group = c.fetchone()
    if not group:
        return None
    c.execute("""SELECT member_key FROM proc.entity_member
                 WHERE group_id=%s AND kind=%s ORDER BY member_key""",
              (row["group_id"], kind))
    return {
        "canonical_key": group["canonical_key"],
        "display_name": group["display_name"],
        "members": [item["member_key"] for item in c.fetchall()],
    }


def _category_rows(c, *, authority_ids=None, operator_ids=None,
                   lang: str) -> list[LookupItem]:
    description = ("coalesce(cc.description_en, cc.description)"
                   if lang == "en" else "cc.description")
    if authority_ids is not None:
        join = ""
        where = f"a.authority_id = ANY(%s) AND a.type='notice' AND {VISIBLE_SQL}"
        args = (authority_ids,)
    else:
        join = "JOIN proc.act_operator ao ON ao.adam=a.adam"
        where = "ao.operator_id = ANY(%s) AND a.type='contract'"
        args = (operator_ids,)
    c.execute(f"""
        WITH divisions AS (
          SELECT substr(oc.cpv_code,1,2) AS code,
                 count(DISTINCT a.adam) AS n
          FROM proc.procurement_act a
          {join}
          JOIN proc.act_object_detail od ON od.adam=a.adam
          JOIN proc.object_detail_cpv oc ON oc.object_detail_id=od.id
          WHERE {where}
          GROUP BY substr(oc.cpv_code,1,2)
          ORDER BY n DESC, code
          LIMIT 8
        )
        SELECT d.code,
               coalesce((SELECT {description} FROM proc.cpv_code cc
                         WHERE substr(cc.cpv_code,1,2)=d.code
                           AND substr(cc.cpv_code,3,6)='000000'
                         LIMIT 1), d.code) AS label
        FROM divisions d ORDER BY d.n DESC, d.code
    """, args)
    return [LookupItem(code=str(row["code"]), label=str(row["label"]))
            for row in c.fetchall()]


def authority_options(c, query: str, *, limit: int = 20) -> list[LookupItem]:
    """Search the full merge-aware authority directory for mobile filters."""
    term = query.strip()
    c.execute(
        """SELECT auth.org_id, auth.name
             FROM proc.authority auth
            WHERE (
              strpos(translate(proc.f_unaccent(lower(auth.name)),'ς','σ'),
                     translate(proc.f_unaccent(lower(%s)),'ς','σ')) > 0
              OR strpos(lower(auth.org_id),lower(%s)) > 0
              OR EXISTS (
                SELECT 1 FROM proc.entity_member m1
                JOIN proc.entity_member m2 ON m2.group_id=m1.group_id
                JOIN proc.authority sibling ON sibling.org_id=m2.member_key
                WHERE m1.kind='authority' AND m1.member_key=auth.org_id
                  AND strpos(
                    translate(proc.f_unaccent(lower(sibling.name)),'ς','σ'),
                    translate(proc.f_unaccent(lower(%s)),'ς','σ')) > 0
              )
            )
              AND NOT EXISTS (
                SELECT 1 FROM proc.entity_member member
                JOIN proc.entity_group entity_group ON entity_group.id=member.group_id
                WHERE member.kind='authority'
                  AND member.member_key=auth.org_id
                  AND entity_group.canonical_key<>auth.org_id
              )
            ORDER BY auth.name, auth.org_id
            LIMIT %s""",
        (term, term, term, int(limit)),
    )
    return [LookupItem(code=str(row["org_id"]), label=str(row["name"]))
            for row in c.fetchall()]


def authority_summary(c, org_id: str, lang: str) -> EntitySummary | None:
    c.execute("""SELECT org_id, name, city, postal_code, country,
                        street_address, contact_email, contact_phone, contact_url
                 FROM proc.authority WHERE org_id=%s""", (org_id,))
    authority = c.fetchone()
    if not authority:
        return None
    group = resolve_group(c, "authority", org_id)
    member_ids = group["members"] if group else [org_id]
    canonical_id = str(group["canonical_key"] if group else org_id)
    canonical_name = None
    display_name = authority["name"]
    if group:
        canonical_name = group.get("display_name")
        if not canonical_name:
            c.execute("SELECT name FROM proc.authority WHERE org_id=%s",
                      (canonical_id,))
            canonical = c.fetchone()
            canonical_name = canonical["name"] if canonical else display_name
        display_name = canonical_name

    c.execute("""SELECT count(*) AS n,
                        coalesce(sum(proc.resolved_value(
                          adam,total_cost_with_vat))
                          FILTER (WHERE type='contract'),0) AS contract_value
                 FROM proc.procurement_act a
                 WHERE a.authority_id=ANY(%s) AND """ + VISIBLE_SQL,
              (member_ids,))
    headline = c.fetchone()
    c.execute(f"""SELECT {_RECENT_SELECT}
                  FROM proc.procurement_act a
                  LEFT JOIN proc.authority auth ON auth.org_id=a.authority_id
                  WHERE a.authority_id=ANY(%s) AND {VISIBLE_SQL}
                  ORDER BY a.submission_date DESC NULLS LAST,
                           a.signed_date DESC NULLS LAST, a.adam
                  LIMIT 20""", (member_ids,))
    recent = [mobile_search.act_item(row, lang=lang) for row in c.fetchall()]
    return EntitySummary(
        kind="authority", id=canonical_id, name=str(display_name),
        canonical_name=canonical_name, merged=bool(group),
        contact=_contact(authority),
        headline=EntityHeadline(
            act_count=int(headline["n"] or 0),
            contract_value=_money(headline["contract_value"])),
        top_categories=_category_rows(
            c, authority_ids=member_ids, lang=lang),
        recent_acts=recent,
    )


def contractor_summary(c, vat: str, lang: str) -> EntitySummary | None:
    c.execute("""SELECT operator_id, vat_number, name, city, postal_code,
                        country, street_address, contact_email, contact_phone,
                        contact_url
                 FROM proc.economic_operator WHERE vat_number=%s""", (vat,))
    operator = c.fetchone()
    if not operator:
        return None
    group = resolve_group(c, "contractor", vat)
    member_vats = group["members"] if group else [vat]
    c.execute("""SELECT operator_id FROM proc.economic_operator
                 WHERE vat_number=ANY(%s)""", (member_vats,))
    operator_ids = [row["operator_id"] for row in c.fetchall()]
    if not operator_ids:
        operator_ids = [operator["operator_id"]]
    canonical_id = str(group["canonical_key"] if group else vat)
    canonical_name = None
    display_name = operator["name"]
    if group:
        canonical_name = group.get("display_name")
        if not canonical_name:
            c.execute("SELECT name FROM proc.economic_operator WHERE vat_number=%s",
                      (canonical_id,))
            canonical = c.fetchone()
            canonical_name = canonical["name"] if canonical else display_name
        display_name = canonical_name

    c.execute("""WITH per_act AS (
                   SELECT a.adam, a.type,
                          max(coalesce(ao.awarded_value_with_vat,
                              proc.resolved_value(a.adam,a.total_cost_with_vat)))
                            AS value
                   FROM proc.act_operator ao
                   JOIN proc.procurement_act a ON a.adam=ao.adam
                   WHERE ao.operator_id=ANY(%s)
                   GROUP BY a.adam,a.type
                 )
                 SELECT count(*) AS n,
                        coalesce(sum(value) FILTER (WHERE type='contract'),0)
                          AS contract_value
                 FROM per_act""", (operator_ids,))
    headline = c.fetchone()
    c.execute("""WITH per_act AS (
                   SELECT a.adam, a.authority_id,
                          max(coalesce(ao.awarded_value_with_vat,
                              proc.resolved_value(a.adam,a.total_cost_with_vat)))
                            AS value
                   FROM proc.act_operator ao
                   JOIN proc.procurement_act a ON a.adam=ao.adam
                   WHERE ao.operator_id=ANY(%s) AND a.type='contract'
                   GROUP BY a.adam,a.authority_id
                 )
                 SELECT auth.org_id,auth.name,sum(pa.value) AS value
                 FROM per_act pa
                 JOIN proc.authority auth ON auth.org_id=pa.authority_id
                 GROUP BY auth.org_id,auth.name
                 ORDER BY value DESC NULLS LAST,auth.name LIMIT 10""",
              (operator_ids,))
    top_buyers = [EntityRef(id=str(row["org_id"]), name=str(row["name"]))
                  for row in c.fetchall()]
    c.execute(f"""SELECT * FROM (
                    SELECT DISTINCT ON (a.adam) {_RECENT_SELECT}
                    FROM proc.act_operator ao
                    JOIN proc.procurement_act a ON a.adam=ao.adam
                    LEFT JOIN proc.authority auth ON auth.org_id=a.authority_id
                    WHERE ao.operator_id=ANY(%s)
                    ORDER BY a.adam,
                             ao.awarded_value_with_vat DESC NULLS LAST
                  ) recent
                  ORDER BY submission_date DESC NULLS LAST,
                           signed_date DESC NULLS LAST,adam LIMIT 20""",
              (operator_ids,))
    recent = [mobile_search.act_item(row, lang=lang) for row in c.fetchall()]
    return EntitySummary(
        kind="contractor", id=canonical_id, name=str(display_name),
        canonical_name=canonical_name, merged=bool(group),
        contact=_contact(operator),
        headline=EntityHeadline(
            act_count=int(headline["n"] or 0),
            contract_value=_money(headline["contract_value"])),
        top_categories=_category_rows(
            c, operator_ids=operator_ids, lang=lang),
        top_buyers=top_buyers, recent_acts=recent,
    )
