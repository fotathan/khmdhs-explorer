"""The public authority profile: 12 months of an authority's activity, in
figures and in one sentence built from them.

Slice 2 of docs/specs/public-detail-pages.md. Owner decisions (2026-10-09):
  - the description is a sentence built from DATA only, never a model;
  - the sentence, the 12-month figures, the "what it buys" breakdowns and the
    open tenders are PUBLIC; contacts are blurred for a gated reader; the
    main suppliers stay subscriber-only.

The figures come from two histogram views (migration 20261009120000), read
per org and summed here, because the authority page merges entity groups and
histograms add up where medians would not. Which acts count is the view's
rule (spec §7): notices and contracts published in the window, not cancelled,
not hidden duplicates, analytics-allowlisted sources; value = contracts only,
analytics-eligible, corrections applied.

No profile, no customer data, no AI summary is read here (test-enforced).
"""
from __future__ import annotations

import datetime as dt

VIEWS = ("mv_authority_profile", "mv_authority_profile_cpv")

# value_band → label. Contract value WITH VAT, as the page shows it elsewhere.
BANDS = [
    (0, "έως 10 χιλ. €", "under €10K"),
    (1, "10–30 χιλ. €", "€10K–30K"),
    (2, "30–100 χιλ. €", "€30K–100K"),
    (3, "100–500 χιλ. €", "€100K–500K"),
    (4, "0,5–1 εκ. €", "€0.5M–1M"),
    (5, "1 εκ. € και άνω", "€1M and over"),
]

# NUTS-2 regions: label, and the phrase that follows "performed …" (Greek
# needs the article and the accusative). Keys must equal main.NUTS_REGIONS.
REGIONS = {
    "EL30": ("Αττική", "στην Αττική", "Attica"),
    "EL51": ("Αν. Μακεδονία & Θράκη", "στην Ανατολική Μακεδονία και Θράκη",
             "Eastern Macedonia and Thrace"),
    "EL52": ("Κεντρική Μακεδονία", "στην Κεντρική Μακεδονία", "Central Macedonia"),
    "EL53": ("Δυτική Μακεδονία", "στη Δυτική Μακεδονία", "Western Macedonia"),
    "EL54": ("Ήπειρος", "στην Ήπειρο", "Epirus"),
    "EL61": ("Θεσσαλία", "στη Θεσσαλία", "Thessaly"),
    "EL62": ("Ιόνια Νησιά", "στα Ιόνια Νησιά", "the Ionian Islands"),
    "EL63": ("Δυτική Ελλάδα", "στη Δυτική Ελλάδα", "Western Greece"),
    "EL64": ("Στερεά Ελλάδα", "στη Στερεά Ελλάδα", "Central Greece"),
    "EL65": ("Πελοπόννησος", "στην Πελοπόννησο", "the Peloponnese"),
    "EL41": ("Βόρειο Αιγαίο", "στο Βόρειο Αιγαίο", "the North Aegean"),
    "EL42": ("Νότιο Αιγαίο", "στο Νότιο Αιγαίο", "the South Aegean"),
    "EL43": ("Κρήτη", "στην Κρήτη", "Crete"),
}

# How many rows a breakdown shows; the rest fold into "other".
TOP = 5
# A CPV division is named in the sentence only above this share of contracts.
SENTENCE_CPV_MIN_SHARE = 0.20
# "Most are performed in X" needs a majority of the contracts with a region.
REGION_MAJORITY = 0.5
REGION_ALMOST_ALL = 0.9


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #
def views_exist(c) -> bool:
    """Both views exist AND are populated (a view created WITH NO DATA — the
    test schema's state — raises on SELECT)."""
    c.execute("""SELECT count(*) FILTER (WHERE relispopulated) AS n
                 FROM pg_class
                 WHERE oid IN (to_regclass('proc.mv_authority_profile'),
                               to_regclass('proc.mv_authority_profile_cpv'))""")
    return c.fetchone()["n"] == 2


def load(c, member_ids: list[str], lang: str = "el") -> dict | None:
    """The summed profile of an authority (or its entity group).

    None when the views do not exist yet (migration not applied): the page
    then shows no profile at all, rather than a misleading "nothing published".
    """
    if not views_exist(c):
        return None
    c.execute("""SELECT act_type, contract_type, procedure_family, nuts2,
                        value_band, n, n_valued, value, period_start, period_end
                 FROM proc.mv_authority_profile
                 WHERE authority_id = ANY(%s)""", (member_ids,))
    rows = c.fetchall()
    c.execute("""SELECT division, sum(n)::int AS n, sum(value) AS value
                 FROM proc.mv_authority_profile_cpv
                 WHERE authority_id = ANY(%s)
                 GROUP BY division""", (member_ids,))
    cpv_rows = c.fetchall()
    labels = {}
    divisions = [r["division"] for r in cpv_rows]
    if divisions:
        col = ("coalesce(description_en, description)" if lang == "en"
               else "description")
        c.execute(f"""SELECT substr(cpv_code, 1, 2) AS division, {col} AS label
                      FROM proc.cpv_code
                      WHERE substr(cpv_code, 1, 2) = ANY(%s)
                        AND substr(cpv_code, 3, 6) = '000000'""", (divisions,))
        labels = {r["division"]: r["label"] for r in c.fetchall()}
    if not rows:
        # The views exist, but nothing in the window. Read the window itself,
        # so the page can still say which 12 months it looked at.
        c.execute("""SELECT period_start, period_end FROM proc.mv_authority_profile
                     LIMIT 1""")
        p = c.fetchone()
        return summarise([], [], {}, period=(p["period_start"], p["period_end"]) if p else None)
    return summarise(rows, cpv_rows, labels)


def open_tenders(c, member_ids: list[str], limit: int = 5) -> list[dict]:
    """Notices still open for submissions, closing soonest first. Titles and
    deadlines are in every act page's public hero already."""
    try:
        from app.act_visibility import VISIBLE_SQL
    except ImportError:  # flat layout
        from act_visibility import VISIBLE_SQL
    c.execute(f"""SELECT a.adam, a.title, a.final_submission_date,
                         a.total_cost_with_vat
                  FROM proc.procurement_act a
                  WHERE a.authority_id = ANY(%s) AND a.type = 'notice'
                    AND a.final_submission_date >= now()
                    AND NOT coalesce(a.cancelled, false)
                    AND {VISIBLE_SQL}
                  ORDER BY a.final_submission_date
                  LIMIT %s""", (member_ids, limit))
    return c.fetchall()


# --------------------------------------------------------------------------- #
# Arithmetic (pure)
# --------------------------------------------------------------------------- #
def _breakdown(counts: dict, total: int) -> dict:
    """{key: n} → the TOP rows by count, an 'other' total and the unknown
    count ('' key). Shares are of the KNOWN total, so an undeclared field does
    not shrink every bar."""
    unknown = counts.pop("", 0)
    known = sum(counts.values())
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    rows = [{"key": k, "n": n, "share": n / known} for k, n in ranked[:TOP]]
    other = sum(n for _, n in ranked[TOP:])
    return {"rows": rows, "other": other,
            "other_share": (other / known) if known else 0,
            "unknown": unknown, "known": known, "total": total}


def summarise(rows, cpv_rows, cpv_labels, period=None) -> dict:
    notices = contracts = valued = 0
    value = 0.0
    ctype: dict = {}
    proc_: dict = {}
    region: dict = {}
    bands = {b: 0 for b, _, _ in BANDS}
    for r in rows:
        n = int(r["n"] or 0)
        if period is None and r.get("period_start"):
            period = (r["period_start"], r["period_end"])
        if r["act_type"] == "notice":
            notices += n
            continue
        contracts += n
        valued += int(r["n_valued"] or 0)
        value += float(r["value"] or 0)
        ctype[r["contract_type"]] = ctype.get(r["contract_type"], 0) + n
        proc_[r["procedure_family"]] = proc_.get(r["procedure_family"], 0) + n
        region[r["nuts2"]] = region.get(r["nuts2"], 0) + n
        if r["value_band"] is not None and r["value_band"] >= 0:
            bands[int(r["value_band"])] += int(r["n_valued"] or 0)
    cpv = sorted(({"division": r["division"], "label": cpv_labels.get(r["division"]),
                   "n": int(r["n"] or 0), "value": float(r["value"] or 0),
                   "share": (int(r["n"] or 0) / contracts) if contracts else 0}
                  for r in cpv_rows), key=lambda x: (-x["n"], x["division"]))[:TOP]
    band_total = sum(bands.values())
    return {
        "period": period,
        "notices": notices, "contracts": contracts,
        "value": value, "valued": valued,
        "empty": notices == 0 and contracts == 0,
        "contract_type": _breakdown(ctype, contracts),
        "procedure": _breakdown(proc_, contracts),
        "region": _breakdown(region, contracts),
        "bands": [{"band": b, "el": el, "en": en, "n": bands[b],
                   "share": (bands[b] / band_total) if band_total else 0}
                  for b, el, en in BANDS],
        "band_total": band_total,
        "cpv": cpv,
    }


# --------------------------------------------------------------------------- #
# Words
# --------------------------------------------------------------------------- #
def fmt_int(n, lang="el") -> str:
    s = f"{int(n):,}"
    return s if lang == "en" else s.replace(",", ".")


def fmt_money(v, lang="el") -> str:
    """Compact euro amount: 933,5 εκ. € / €933.5M."""
    v = float(v or 0)
    for div, el, en in ((1e9, "δισ.", "B"), (1e6, "εκ.", "M"), (1e3, "χιλ.", "K")):
        if abs(v) >= div:
            x = v / div
            num = f"{x:.1f}" if x < 100 else f"{x:.0f}"
            if num.endswith(".0"):
                num = num[:-2]
            return f"€{num}{en}" if lang == "en" else f"{num.replace('.', ',')} {el} €"
    num = f"{v:.0f}"
    return f"€{num}" if lang == "en" else f"{num} €"


def fmt_pct(share) -> str:
    """A share that is not zero never prints as 0%, and one that is not
    everything never prints as 100%."""
    share = float(share or 0)
    if 0 < share < 0.005:
        return "<1%"
    if 0.995 <= share < 1:
        return ">99%"
    return f"{round(share * 100):.0f}%"


def fmt_date(d, lang="el") -> str:
    if not isinstance(d, (dt.date, dt.datetime)):
        return ""
    return d.strftime("%-d %b %Y") if lang == "en" else f"{d.day}/{d.month}/{d.year}"


def period_label(p: dict, lang="el") -> str:
    if not p.get("period"):
        return ""
    a, b = p["period"]
    return f"{fmt_date(a, lang)} – {fmt_date(b, lang)}"


def region_label(code: str, lang="el") -> str:
    r = REGIONS.get(code)
    if not r:
        return code
    return r[2] if lang == "en" else r[0]


def _count(n, one_el, many_el, one_en, many_en, lang):
    if lang == "en":
        return f"{fmt_int(n, lang)} {one_en if n == 1 else many_en}"
    return f"{fmt_int(n, lang)} {one_el if n == 1 else many_el}"


def sentence(p: dict | None, name: str, lang: str = "el") -> str | None:
    """The authority described from its own figures. Every number in it is a
    number the page also shows; nothing is said that the data does not say."""
    if p is None:
        return None
    name = " ".join((name or "").split())
    if p["empty"]:
        if lang == "en":
            return (f"The contracting authority «{name}» published no contract "
                    f"notices or contracts in the last 12 months.")
        return (f"Η αναθέτουσα αρχή «{name}» δεν δημοσίευσε προκηρύξεις ή "
                f"συμβάσεις τους τελευταίους 12 μήνες.")

    parts = []
    if p["notices"]:
        parts.append(_count(p["notices"], "προκήρυξη", "προκηρύξεις",
                            "contract notice", "contract notices", lang))
    if p["contracts"]:
        c = _count(p["contracts"], "σύμβαση", "συμβάσεις", "contract", "contracts", lang)
        if p["valued"] and p["value"] > 0:
            c += (f" worth {fmt_money(p['value'], lang)} in total" if lang == "en"
                  else f" συνολικής αξίας {fmt_money(p['value'], lang)}")
        parts.append(c)
    joiner = " and " if lang == "en" else " και "
    period = period_label(p, lang)
    if lang == "en":
        out = [f"Over the last 12 months ({period}), the contracting authority "
               f"«{name}» published {joiner.join(parts)}."]
    else:
        out = [f"Τους τελευταίους 12 μήνες ({period}), η αναθέτουσα αρχή "
               f"«{name}» δημοσίευσε {joiner.join(parts)}."]

    named = [x for x in p["cpv"] if x["label"] and x["share"] >= SENTENCE_CPV_MIN_SHARE][:2]
    if named:
        q = joiner.join(f"«{x['label']}»" for x in named)
        out.append(f"Its contracts are mostly for {q}." if lang == "en"
                   else f"Οι συμβάσεις της αφορούν κυρίως {q}.")

    reg = p["region"]
    if reg["rows"]:
        top = reg["rows"][0]
        r = REGIONS.get(top["key"])
        # Of ALL the contracts, not only those that state a region: "all" must
        # mean all.
        share = top["n"] / reg["total"] if reg["total"] else 0
        if r:
            where = f"in {r[2]}" if lang == "en" else r[1]
            pct = fmt_pct(share)
            if share == 1:
                out.append(f"All of them are performed {where}." if lang == "en"
                           else f"Όλες εκτελούνται {where}.")
            elif share >= REGION_ALMOST_ALL:
                out.append(f"Almost all of them ({pct}) are performed {where}." if lang == "en"
                           else f"Σχεδόν όλες ({pct}) εκτελούνται {where}.")
            elif share >= REGION_MAJORITY:
                out.append(f"Most of them ({pct}) are performed {where}." if lang == "en"
                           else f"Οι περισσότερες ({pct}) εκτελούνται {where}.")
            else:
                out.append(f"They are performed in several regions, most often {where} ({pct})."
                           if lang == "en" else
                           f"Εκτελούνται σε περισσότερες περιφέρειες, συχνότερα {where} ({pct}).")
    return " ".join(out)


# --------------------------------------------------------------------------- #
# The redesigned page's extra blocks (spec slice 5). All public, all data-only.
# --------------------------------------------------------------------------- #
def open_count(c, member_ids: list[str]) -> int:
    """How many of its notices are open right now (the hero's 4th figure)."""
    try:
        from app.act_visibility import VISIBLE_SQL
    except ImportError:  # flat layout
        from act_visibility import VISIBLE_SQL
    c.execute(f"""SELECT count(*) AS n FROM proc.procurement_act a
                  WHERE a.authority_id = ANY(%s) AND a.type = 'notice'
                    AND a.final_submission_date >= now()
                    AND NOT coalesce(a.cancelled, false) AND {VISIBLE_SQL}""",
              (member_ids,))
    return int(c.fetchone()["n"])


def latest_acts(c, member_ids: list[str], limit: int = 5) -> list[dict]:
    """Its most recent notices, award decisions and contracts (requests and
    payments are noise here): title, type, date, value — the facts
    every act page's public hero already shows."""
    try:
        from app.act_visibility import VISIBLE_SQL
    except ImportError:  # flat layout
        from act_visibility import VISIBLE_SQL
    c.execute(f"""SELECT a.adam, a.type, a.title, a.submission_date,
                         a.total_cost_with_vat, a.cancelled
                  FROM proc.procurement_act a
                  WHERE a.authority_id = ANY(%s) AND {VISIBLE_SQL}
                    AND a.type IN ('notice', 'auction', 'contract')
                  ORDER BY a.submission_date DESC NULLS LAST
                  LIMIT %s""", (member_ids, limit))
    return c.fetchall()


def related(c, member_ids: list[str], p: dict | None, name: str,
            limit: int = 5) -> list[dict]:
    """Other authorities whose contracts are performed in the same main
    region, the same KIND first (same first word: ΔΗΜΟΣ, ΝΟΣΟΚΟΜΕΙΟ…), then by
    how many contracts they published in the window."""
    if not p or not p["region"]["rows"] or not views_exist(c):
        return []
    region = p["region"]["rows"][0]["key"]
    first = (name or "").split()[0].upper() if (name or "").split() else ""
    c.execute("""
        SELECT x.authority_id AS org_id, au.name, sum(x.n)::int AS n
        FROM proc.mv_authority_profile x
        JOIN proc.authority au ON au.org_id = x.authority_id
        WHERE x.nuts2 = %s AND x.act_type = 'contract'
          AND NOT x.authority_id = ANY(%s)
        GROUP BY 1, 2
        ORDER BY (upper(split_part(au.name, ' ', 1)) = %s) DESC, 3 DESC
        LIMIT %s""", (region, member_ids, first, limit))
    return c.fetchall()


def faq(p: dict | None, name: str, org_id: str, lang: str = "el", *,
        label_ct=lambda k: k, label_pr=lambda k: k) -> list[dict]:
    """Questions a visitor asks about an authority, answered from its figures
    only. Nothing is said that the page does not show."""
    if not p:
        return []
    en = lang == "en"
    out = []
    n = p["notices"]
    if en:
        a = (f"In the last 12 months it published {fmt_int(n, lang)} contract "
             f"notice{'' if n == 1 else 's'}"
             + (f", about {fmt_int(int(n / 12 + 0.5), lang)} a month." if n >= 12 else ".")
             if n else "It published no contract notices in the last 12 months.")
        out.append({"q": f"How often does «{name}» publish tenders?", "a": a})
    else:
        a = (f"Τους τελευταίους 12 μήνες δημοσίευσε {fmt_int(n, lang)} "
             f"{'προκήρυξη' if n == 1 else 'προκηρύξεις'}"
             + (f", περίπου {fmt_int(int(n / 12 + 0.5), lang)} τον μήνα." if n >= 12 else ".")
             if n else "Δεν δημοσίευσε προκηρύξεις τους τελευταίους 12 μήνες.")
        out.append({"q": f"Πόσο συχνά προκηρύσσει διαγωνισμούς η αναθέτουσα «{name}»;", "a": a})

    ct, pr = p["contract_type"]["rows"], p["procedure"]["rows"]
    if p["contracts"] and (ct or pr):
        bits = []
        if ct:
            bits.append(("Most of its contracts are «{l}» ({s})" if en
                         else "Οι περισσότερες συμβάσεις της είναι «{l}» ({s})")
                        .format(l=label_ct(ct[0]["key"]), s=fmt_pct(ct[0]["share"])))
        if pr:
            bits.append(("most often awarded by «{l}» ({s})" if en
                         else "συχνότερα με «{l}» ({s})")
                        .format(l=label_pr(pr[0]["key"]), s=fmt_pct(pr[0]["share"])))
        out.append({"q": (f"What kind of contracts does «{name}» award?" if en
                          else f"Τι είδους συμβάσεις αναθέτει;"),
                    "a": ", ".join(bits) + "."})

    out.append({"q": ("How can I get alerts for its new tenders?" if en
                      else "Πώς λαμβάνω ειδοποιήσεις για τους νέους διαγωνισμούς της;"),
                "a": ("Create a free account and save a search filtered on this "
                      "authority: every new notice it publishes reaches you by email." if en
                      else "Δημιουργήστε δωρεάν λογαριασμό και αποθηκεύστε μια αναζήτηση "
                           "με αυτή την αναθέτουσα: κάθε νέα προκήρυξή της θα σας έρχεται με email."),
                "href": f"/?authority={org_id}"})
    return out
