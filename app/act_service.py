"""Reusable act reads and the explicit customer-safe mobile detail mapper."""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from urllib.parse import urlparse

from app import interconnect
from app import search_match
from app.api_v1.schemas import (
    ActDetail,
    DetailSection,
    Link,
    LookupItem,
    MatchReason,
    Money,
)


def _desc_col(lang, alias="c", col="description"):
    return (f"coalesce({alias}.{col}_en, {alias}.{col})"
            if lang == "en" else f"{alias}.{col}")


def fetch_core(c, adam: str, lang: str) -> dict | None:
    """The shared core row used by both the browser and native detail views."""
    c.execute(f"""
        SELECT
               a.adam, a.type, a.title, a.signed_date, a.submission_date,
               a.final_submission_date, a.total_cost_with_vat,
               proc.resolved_value(a.adam, a.total_cost_with_vat) AS resolved_value,
               (proc.resolved_value(a.adam, a.total_cost_with_vat)
                   IS DISTINCT FROM a.total_cost_with_vat) AS is_corrected,
               a.cancelled, a.is_modified, a.contract_type_code,
               a.procedure_type_code, a.procedure_family, a.nuts_code,
               a.data_source, auth.org_id AS authority_id,
               auth.name AS authority_name,
               a.type AS act_type, a.budget, a.total_cost_without_vat,
               a.criteria_code, a.legal_context_code, a.notice_type_code,
               a.conducting_proceedings_code, a.digital_platform_code,
               a.contracting_authority_activity_code, a.award_procedure_code,
               a.contract_duration, a.contract_duration_unit,
               a.offers_valid_time, a.offers_valid_time_unit,
               a.framework_agreement_adam, a.bidding_website,
               a.amended_adam, a.cancellation_reason, a.cancellation_date,
               a.contract_number, a.contract_signed_date,
               a.start_date, a.end_date, a.no_end_date,
               a.assign_criteria_code, a.assign_criteria_label,
               a.bids_submitted, a.max_bids_submitted,
               a.is_credit, a.payment_commitment_code, a.contract_value,
               a.full_text, a.full_text_html,
               a.full_text_extracted_at, a.full_text_source,
               a.ingested_at, a.last_update_date,
               a.last_edited_at, a.last_edited_by, a.source_url,
               a.divided_into_lots, a.is_framework_agreement,
               a.type_of_bid_required, a.alternative_offers_allowed,
               a.number_of_offers, a.prolongation_option, a.prolongation_in_months,
               a.vat_rate, a.vat_included, a.value_eur, a.value_usd,
               a.estimated_price_min, a.estimated_price_max,
               a.yearly_budget, a.bid_bond_amount, a.price_weighting,
               a.eligibility_criteria, a.eligibility_category,
               a.journal_number, a.eprocurement_portal,
               a.contact_email, a.contact_phone, a.contact_fax,
               a.street_address, a.contact_url,
               a.source_status, a.type_of_document, a.subtype_of_document,
               a.duplicate_of,
               nuts.label AS nuts_label
        FROM proc.procurement_act a
        LEFT JOIN proc.authority auth ON auth.org_id = a.authority_id
        LEFT JOIN proc.nuts_code nuts ON nuts.nuts_code = a.nuts_code
        WHERE a.adam = %s
    """, (adam,))
    return c.fetchone()


def fetch_line_items(c, adam: str, lang: str) -> list[dict]:
    c.execute(f"""
        SELECT od.line_no, od.short_description, od.quantity, od.unit_code,
               u.name AS unit_name, od.cost_without_vat,
               proc.resolved_item_cost(
                   od.adam, od.line_no, od.cost_without_vat) AS resolved_cost,
               (proc.resolved_item_cost(od.adam, od.line_no, od.cost_without_vat)
                   IS DISTINCT FROM od.cost_without_vat) AS cost_corrected,
               od.vat_rate, od.currency_code,
               od.delivery_address, od.delivery_city, od.delivery_street,
               od.delivery_postal_code, od.delivery_country,
               od.city_of_construction,
               coalesce(array_agg(jsonb_build_object(
                   'code', cpv.cpv_code, 'description', {_desc_col(lang, 'cpv')})
                   ORDER BY cpv.cpv_code)
                   FILTER (WHERE cpv.cpv_code IS NOT NULL), '{{}}') AS cpvs
        FROM proc.act_object_detail od
        LEFT JOIN proc.object_detail_cpv x ON x.object_detail_id = od.id
        LEFT JOIN proc.cpv_code cpv ON cpv.cpv_code = x.cpv_code
        LEFT JOIN proc.unit_code u ON upper(u.code) = upper(od.unit_code)
        WHERE od.adam = %s
        GROUP BY od.id, od.line_no, u.name
        ORDER BY od.line_no
    """, (adam,))
    return c.fetchall()


def fetch_operators(c, adam: str) -> list[dict]:
    c.execute("""
        SELECT eo.vat_number, eo.name, eo.is_greek_vat, eo.country,
               ao.role, ao.awarded_value_without_vat, ao.awarded_value_with_vat
        FROM proc.act_operator ao
        JOIN proc.economic_operator eo ON eo.operator_id = ao.operator_id
        WHERE ao.adam = %s
        ORDER BY ao.role, eo.name
    """, (adam,))
    return c.fetchall()


def fetch_taxonomy(c, adam: str, lang: str) -> tuple[list[dict], list[dict]]:
    c.execute(f"""SELECT ac.cpv_code, {_desc_col(lang, 'cc')} AS description
                  FROM proc.act_cpv ac
                  LEFT JOIN proc.cpv_code cc ON cc.cpv_code = ac.cpv_code
                  WHERE ac.adam=%s ORDER BY ac.ord, ac.cpv_code""", (adam,))
    cpvs = c.fetchall()
    c.execute(f"""
        SELECT DISTINCT cat.id AS category_id,
               {_desc_col(lang, 'cat', 'name')} AS category_name,
               sub.id AS subcategory_id,
               {_desc_col(lang, 'sub', 'name')} AS subcategory_name
        FROM proc.act_object_detail od
        JOIN proc.object_detail_cpv oc ON oc.object_detail_id = od.id
        JOIN proc.cpv_category_map m ON m.cpv_code = oc.cpv_code
        JOIN proc.tender_category cat ON cat.id = m.category_id
        JOIN proc.tender_subcategory sub ON sub.id = m.subcategory_id
        WHERE od.adam=%s
        ORDER BY category_name, subcategory_name
    """, (adam,))
    categories = []
    by_id = {}
    for row in c.fetchall():
        category = by_id.get(row["category_id"])
        if category is None:
            category = {
                "id": str(row["category_id"]), "name": row["category_name"],
                "subcategories": [],
            }
            by_id[row["category_id"]] = category
            categories.append(category)
        category["subcategories"].append({
            "id": str(row["subcategory_id"]), "name": row["subcategory_name"],
        })
    return cpvs, categories


def fetch_relations(c, adam: str) -> dict:
    c.execute("""
        WITH RECURSIVE chain AS (
            SELECT %s::text AS adam, 0 AS depth,
                   ARRAY[%s::text] AS path, NULL::text AS via
            UNION ALL
            SELECT l.target_adam, chain.depth + 1,
                   chain.path || l.target_adam, l.relation::text
            FROM chain
            JOIN proc.act_link l ON l.source_adam = chain.adam
            WHERE chain.depth < 12
              AND NOT (l.target_adam = ANY (chain.path))
              AND l.relation = ANY (ARRAY[
                    'request_to_notice','request_to_auction',
                    'request_to_contract','request_to_payment',
                    'notice_to_auction','auction_to_contract',
                    'auction_to_payment','contract_to_payment','contract_next'
                  ]::proc.link_relation[])
        )
        SELECT chain.adam, chain.depth, chain.via,
               a.type, a.title, a.signed_date, a.total_cost_with_vat, a.cancelled
        FROM chain
        LEFT JOIN proc.procurement_act a ON a.adam = chain.adam
        WHERE chain.depth > 0
        ORDER BY chain.depth, chain.adam
    """, (adam, adam))
    downstream = c.fetchall()
    c.execute("""
        SELECT l.source_adam AS adam, l.relation, a.type, a.title, a.signed_date
        FROM proc.act_link l
        LEFT JOIN proc.procurement_act a ON a.adam = l.source_adam
        WHERE l.target_adam=%s
        ORDER BY l.relation, a.signed_date DESC NULLS LAST
    """, (adam,))
    return {"downstream": downstream, "incoming": c.fetchall()}


def fetch_content_availability(c, adam: str, core: dict) -> dict:
    c.execute("""SELECT count(*) AS n FROM proc.extracted_table
                 WHERE adam=%s AND is_published""", (adam,))
    row = c.fetchone()
    return {
        "full_text_available": bool(core.get("full_text")),
        "full_text_html_available": bool(core.get("full_text_html")),
        "full_text_source": core.get("full_text_source"),
        "full_text_extracted_at": core.get("full_text_extracted_at"),
        "published_table_count": int(row["n"] or 0),
    }


def fetch_lots(c, adam: str) -> dict | None:
    try:
        group_id = interconnect.group_of(c, adam)
        if not group_id:
            return None
        panel = interconnect.group_lot_panel(c, group_id)
        if not panel:
            return None
        scope = interconnect.scope_for_act(c, adam)
        lots = [{key: lot.get(key) for key in (
            "id", "source", "source_key", "lot_number", "title", "description",
            "status", "estimated_value", "awarded_value", "currency_code", "cpvs",
            "nuts", "n_acts",
        )} for lot in panel["lots"]]
        return {"scope": {
            "kind": scope.get("kind"), "lot_ids": scope.get("lot_ids", []),
            "source": scope.get("source"),
        }, "lots": lots}
    except Exception:  # optional overlay; absence must not hide the core act
        return None


def _money(value) -> Money | None:
    if value is None:
        return None
    return Money(amount=format(Decimal(value), "f"))


def _date(value):
    return value.date() if isinstance(value, dt.datetime) else value


def _clean(data: dict) -> dict:
    return {key: value for key, value in data.items()
            if value is not None and value != [] and value != {}}


def _safe_official_url(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parsed = urlparse(value)
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or parsed.username or parsed.password:
            return None
        if not (host.endswith(".gov.gr") or host == "gov.gr"
                or host.endswith(".europa.eu") or host == "europa.eu"):
            return None
        return value
    except Exception:
        return None


_TITLES = {
    "el": {
        "screening": "Σύνοψη ελέγχου", "facts": "Βασικά στοιχεία",
        "parties": "Αναθέτουσα αρχή και ανάδοχοι", "objects": "Αντικείμενο",
        "procedure": "Διαδικασία και προθεσμίες", "lots": "Τμήματα",
        "lifecycle": "Σχετικές πράξεις", "content": "Έγγραφα και δεδομένα",
        "provenance": "Προέλευση δεδομένων", "official": "Επίσημο έγγραφο",
    },
    "en": {
        "screening": "Screening summary", "facts": "Core facts",
        "parties": "Authority and contractors", "objects": "Procurement object",
        "procedure": "Procedure and deadlines", "lots": "Lots",
        "lifecycle": "Related acts", "content": "Documents and data",
        "provenance": "Data provenance", "official": "Official document",
    },
}


def mobile_detail(c, adam: str, lang: str, *, q: str = "",
                  cpv: list[str] | None = None) -> ActDetail | None:
    """Build the allowlisted native DTO; internal/admin fields never enter it."""
    from app import main as web

    core = fetch_core(c, adam, lang)
    if not core:
        return None
    line_items = fetch_line_items(c, adam, lang)
    operators = fetch_operators(c, adam)
    act_cpvs, categories = fetch_taxonomy(c, adam, lang)
    relations = fetch_relations(c, adam)
    availability = fetch_content_availability(c, adam, core)
    lots = fetch_lots(c, adam)

    match = search_match.detail_match(c, core, q, cpv or [], lang) if (q or cpv) else None
    reasons = [MatchReason(
        kind="cpv" if chip.kind == "cpv" else "text",
        label=str(chip.label or chip.code or chip.term),
    ) for chip in (match.chips if match else [])]
    titles = _TITLES.get(lang, _TITLES["el"])
    sections = [
        DetailSection(kind="screening", title=titles["screening"],
                      source_class="calculated", data=_clean({
                          "value_corrected": bool(core.get("is_corrected")),
                          "cancelled": bool(core.get("cancelled")),
                          "modified": bool(core.get("is_modified")),
                      })),
        DetailSection(kind="facts", title=titles["facts"], data=_clean({
            "signed_date": core.get("signed_date"),
            "publication_at": core.get("submission_date"),
            "deadline_at": core.get("final_submission_date"),
            "budget": core.get("budget"),
            "value_without_vat": core.get("total_cost_without_vat"),
            "contract_number": core.get("contract_number"),
            "contract_signed_date": core.get("contract_signed_date"),
            "start_date": core.get("start_date"), "end_date": core.get("end_date"),
            "no_end_date": core.get("no_end_date"),
        })),
        DetailSection(kind="parties", title=titles["parties"], data=_clean({
            "authority": ({"id": str(core["authority_id"]),
                            "name": core.get("authority_name")}
                           if core.get("authority_id") else None),
            "operators": operators,
        })),
        DetailSection(kind="objects", title=titles["objects"], data=_clean({
            "line_items": line_items, "act_cpvs": act_cpvs,
            "categories": categories,
        })),
        DetailSection(kind="procedure", title=titles["procedure"], data=_clean({
            "contract_type_code": core.get("contract_type_code"),
            "procedure_type_code": core.get("procedure_type_code"),
            "procedure_family": core.get("procedure_family"),
            "criteria_code": core.get("criteria_code"),
            "assign_criteria_code": core.get("assign_criteria_code"),
            "assign_criteria_label": core.get("assign_criteria_label"),
            "nuts_code": core.get("nuts_code"), "nuts_label": core.get("nuts_label"),
            "bids_submitted": core.get("bids_submitted"),
            "max_bids_submitted": core.get("max_bids_submitted"),
        })),
        DetailSection(kind="lifecycle", title=titles["lifecycle"],
                      data=relations),
        DetailSection(kind="content", title=titles["content"],
                      data=_clean(availability)),
        DetailSection(kind="provenance", title=titles["provenance"], data=_clean({
            "source": core.get("data_source"),
            "source_status": core.get("source_status"),
            "ingested_at": core.get("ingested_at"),
            "last_update_at": core.get("last_update_date"),
        })),
    ]
    if lots:
        sections.insert(5, DetailSection(
            kind="lots", title=titles["lots"], data=lots))
    sections = [section for section in sections if section.data]

    official = _safe_official_url(web.source_doc_url(core))
    links = [Link(label=titles["official"], url=official)] if official else []
    type_code = str(core.get("type") or "")
    source_code = str(core.get("data_source") or "")
    source_label = {
        "el": {"khmdhs": "ΚΗΜΔΗΣ", "diavgeia": "Διαύγεια", "ted": "TED", "tsg": "Tender Service"},
        "en": {"khmdhs": "KIMDIS", "diavgeia": "Diavgeia", "ted": "TED", "tsg": "Tender Service"},
    }.get(lang, {}).get(source_code, source_code)
    authority = None
    if core.get("authority_id") and core.get("authority_name"):
        authority = {"id": str(core["authority_id"]), "name": core["authority_name"]}
    return ActDetail(
        adam=str(core["adam"]),
        type=LookupItem(
            code=type_code,
            label=web._i18n.enum_label("type", type_code, web.TYPE_LABELS, lang)),
        title=str(core.get("title") or ""),
        source=LookupItem(code=source_code, label=source_label),
        authority=authority,
        publication_date=_date(core.get("submission_date")),
        deadline_at=core.get("final_submission_date"),
        value=_money(core.get("resolved_value")),
        cancelled=bool(core.get("cancelled")),
        modified=bool(core.get("is_modified")),
        match_reasons=reasons,
        sections=sections, links=links,
        updated_at=core.get("last_update_date") or core.get("ingested_at"),
    )
