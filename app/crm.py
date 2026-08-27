"""
crm.py — CRM admin area (mounted at /admin/crm, admin-gated by AuthMiddleware).

Phase 1: list customers segmented by subscription status, and a per-customer
page with an editable profile, product/subscription history, and the grant /
extend / set-expiry controls (reachable here, not only from /admin/users).

Data helpers live in app/auth.py (list_customers, customer_segment_counts,
get_customer, get_profile, upsert_profile, subscription_history, grant_product,
extend_subscription, set_subscription_expiry, current_subscription, product_list).
Phase 2 will add notes / calls / tasks.
"""

from __future__ import annotations

import threading

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

try:
    from app import auth as _auth
except ImportError:  # run with --app-dir=app
    import auth as _auth

try:
    from app import call_pipeline as _pipeline
except ImportError:  # run with --app-dir=app
    import call_pipeline as _pipeline

# Segment values shown as tabs (order matters); mirror auth._status_label.
SEGMENTS = ("all", "prospective", "subscriber", "tester",
            "expired_subscriber", "expired_tester", "none")

try:
    from app import leads as _leads
except ImportError:                      # pragma: no cover
    import leads as _leads

try:
    from app import email_builder as _email
except ImportError:                      # pragma: no cover
    import email_builder as _email

try:
    from app import digests as _digests
except ImportError:                      # pragma: no cover
    import digests as _digests

try:
    from app import mailer as _mailer
except ImportError:                      # pragma: no cover
    import mailer as _mailer

try:
    from app import i18n as _i18n
except ImportError:                      # pragma: no cover
    import i18n as _i18n

try:
    from app import search_profiles as _sp
except ImportError:                      # pragma: no cover
    import search_profiles as _sp


# Which filters a saved search actually sets, in words. The CRM page shows this
# next to each saved search so an admin can tell two of them apart without
# opening either — "q, cpv, date_from" is enough to recognise one.
_FILTER_LABELS = {
    "q": "λέξη-κλειδί", "fulltext": "πλήρες κείμενο", "tables_q": "πίνακες",
    "date_from": "από", "date_to": "έως",
    "deadline_from": "προθεσμία από", "deadline_to": "προθεσμία έως",
    "value_min": "αξία από", "value_max": "αξία έως",
    "status": "κατάσταση", "sort": "ταξινόμηση",
    "type": "είδος", "authority": "αναθέτουσα", "contract_type": "τύπος σύμβασης",
    "procedure_type": "διαδικασία", "nuts": "περιοχή", "cpv": "CPV",
    "cat": "κατηγορία", "source": "πηγή",
}


def describe_params(params, lang="el"):
    """A one-line human summary of a saved search's filters."""
    params = params or {}
    if not params:
        return ""
    bits = []
    for key, value in params.items():
        label = _i18n.translate(_FILTER_LABELS.get(key, key), lang)
        if isinstance(value, (list, tuple)):
            shown = ", ".join(str(v) for v in value[:3])
            if len(value) > 3:
                shown += f" +{len(value) - 3}"
        else:
            shown = str(value)
        bits.append(f"{label}: {shown}")
    return " · ".join(bits)


def _vat_candidates(vat):
    """Normalised forms of an ΑΦΜ to match against proc.economic_operator,
    whose Greek VATs are stored zero-padded to 9 digits (some with an EL
    prefix). Covers raw / EL-stripped / zero-padded / unpadded variants."""
    v = (vat or "").strip().upper().replace(" ", "")
    if not v:
        return []
    core = v[2:] if v.startswith("EL") else v
    cands = {v, core}
    if core.isdigit():
        cands |= {core.zfill(9), core.lstrip("0") or "0",
                  "EL" + core.zfill(9), "EL" + core}
    return list(cands)


def _filters(request):
    """Read the shared activity-search query params into the search_* kwargs
    (maps ?from/?to to date_from/date_to)."""
    qp = request.query_params
    return {
        "q": (qp.get("q") or "").strip(),
        "status": (qp.get("status") or "").strip(),
        "assigned_to": (qp.get("assigned_to") or "").strip(),
        "date_from": (qp.get("from") or "").strip(),
        "date_to": (qp.get("to") or "").strip(),
    }


def find_contractor_by_vat(c, vat):
    """The procurement contractor whose VAT matches this customer's ΑΦΜ, or
    None. Indexed lookup via = ANY(candidates)."""
    cands = _vat_candidates(vat)
    if not cands:
        return None
    c.execute("""SELECT vat_number, name, is_greek_vat
                 FROM proc.economic_operator
                 WHERE vat_number = ANY(%s) LIMIT 1""", (cands,))
    return c.fetchone()


def merge_values(cust, profile):
    """The [[field]] vocabulary a template may draw on: the customer's profile
    fields plus the account's own email/username. Profile wins over the account
    for full_name, since the profile is what the CRM edits."""
    values = {k: (profile or {}).get(k) for k in _auth.PROFILE_FIELDS}
    values["email"] = (cust or {}).get("email")
    values["username"] = (cust or {}).get("username")
    if not values.get("full_name"):
        values["full_name"] = (cust or {}).get("full_name")
    return values


def make_crm_router(templates: Jinja2Templates, cursor) -> APIRouter:
    router = APIRouter(prefix="/admin/crm", tags=["crm"])

    def _admin_uid(request):
        u = getattr(request.state, "user", None)
        return u.get("id") if u else None

    @router.get("", response_class=HTMLResponse)
    def crm_list(request: Request, segment: str = "all", q: str = ""):
        if segment not in SEGMENTS:
            segment = "all"
        q = (q or "").strip()
        with cursor() as c:
            counts = _auth.customer_segment_counts(c, q=q)
            customers = _auth.list_customers(c, segment, q=q)
        return templates.TemplateResponse(request, "admin_crm.html", {
            "customers": customers, "counts": counts, "segment": segment,
            "segments": SEGMENTS, "q": q, "admin_tab": "crm"})

    # ---- import contractors as prospective leads (OrgDB → CRM) --------- #
    def _op(c, oid):
        c.execute("SELECT * FROM proc.economic_operator WHERE operator_id = %s", (oid,))
        return c.fetchone()

    @router.post("/leads/review", response_class=HTMLResponse)
    async def leads_review(request: Request):
        """Map the selected contractors + detect duplicates, then show the
        three-group conflict-resolution table (clean / conflict / hard-blocked)."""
        form = await request.form()
        ids = [int(x) for x in form.getlist("operator_id") if str(x).strip().lstrip("-").isdigit()]
        vats = [v.strip() for v in form.getlist("vat") if v and v.strip()]
        q = (form.get("q") or "").strip()
        with cursor() as c:
            if vats:      # the contractors list is ΑΦΜ-keyed → resolve to operators
                c.execute("SELECT operator_id FROM proc.economic_operator "
                          "WHERE vat_number = ANY(%s)", (vats,))
                ids += [r["operator_id"] for r in c.fetchall()]
            if form.get("select_all") == "1" and not ids:
                c.execute("""SELECT operator_id FROM proc.economic_operator
                             WHERE (%(q)s = '' OR name ILIKE %(like)s OR vat_number ILIKE %(like)s)
                             ORDER BY operator_id LIMIT %(cap)s""",
                          {"q": q, "like": f"%{q}%", "cap": _leads.MAX_BATCH})
                ids = [r["operator_id"] for r in c.fetchall()]
            ids = ids[:_leads.MAX_BATCH]
            clean, conflicts, blocked = [], [], []
            for oid in ids:
                op = _op(c, oid)
                if not op:
                    continue
                lead = _leads.map_operator(c, op)
                conf = _leads.detect_conflict(c, lead)
                bucket = {"op": op, "lead": lead, "conf": conf}
                (clean if conf["bucket"] == "clean"
                 else conflicts if conf["bucket"] == "conflict" else blocked).append(bucket)
        return templates.TemplateResponse(request, "admin_crm_leads_review.html", {
            "clean": clean, "conflicts": conflicts, "blocked": blocked,
            "n_total": len(ids), "capped": len(ids) >= _leads.MAX_BATCH,
            "admin_tab": "crm"})

    @router.post("/leads/import", response_class=HTMLResponse)
    async def leads_import(request: Request):
        """Execute the per-row decisions in one transaction; re-map + re-detect so
        the allowed actions are enforced server-side."""
        form = await request.form()
        by = _admin_uid(request)
        ids = [int(x) for x in form.getlist("operator_id") if str(x).strip().lstrip("-").isdigit()]
        created, updated, skipped, errors = [], [], [], []
        with cursor() as c, c.connection.transaction():
            for oid in ids[:_leads.MAX_BATCH]:
                op = _op(c, oid)
                if not op:
                    continue
                name = op.get("name") or f"#{oid}"
                action = (form.get(f"action_{oid}") or "skip").strip()
                new_email = (form.get(f"email_{oid}") or "").strip()
                lead = _leads.map_operator(c, op)
                conf = _leads.detect_conflict(c, lead)
                allowed = set(conf["allowed"])
                if action == "create":
                    if "create" not in allowed and "create_new_email" not in allowed:
                        skipped.append(name)
                        continue
                    override = None
                    if "create_new_email" in allowed:      # exact-email conflict → new email required
                        if not new_email:
                            errors.append((name, "νέο email απαιτείται"))
                            continue
                        override = new_email
                    uid = _leads.create_lead(c, lead, by=by, override_email=override)
                    created.append({"uid": uid, "name": name})
                elif action == "update":
                    if "update" not in allowed or not conf.get("existing_uid"):
                        skipped.append(name)
                        continue
                    _leads.update_existing(c, conf["existing_uid"], lead, by=by)
                    updated.append({"uid": conf["existing_uid"], "name": name})
                else:
                    skipped.append(name)
        return templates.TemplateResponse(request, "admin_crm_leads_result.html", {
            "created": created, "updated": updated, "skipped": skipped,
            "errors": errors, "admin_tab": "crm"})

    # ---- freemail-domain settings (used by lead duplicate detection) ---- #
    @router.get("/freemail", response_class=HTMLResponse)
    def crm_freemail(request: Request, ok: str = None, err: str = None):
        with cursor() as c:
            domains = _leads.list_freemail(c)
        return templates.TemplateResponse(request, "admin_crm_freemail.html", {
            "domains": domains, "ok": ok, "err": err, "admin_tab": "freemail"})

    @router.post("/freemail/add")
    async def crm_freemail_add(request: Request):
        form = await request.form()
        try:
            with cursor() as c:
                _leads.add_freemail(c, form.get("domain") or "")
        except ValueError:
            return RedirectResponse("/admin/crm/freemail?err=invalid", status_code=303)
        return RedirectResponse("/admin/crm/freemail?ok=added", status_code=303)

    @router.post("/freemail/delete")
    async def crm_freemail_delete(request: Request):
        form = await request.form()
        with cursor() as c:
            _leads.remove_freemail(c, form.get("domain") or "")
        return RedirectResponse("/admin/crm/freemail?ok=removed", status_code=303)

    # ---- cross-customer activity lists (declared before /{uid}) -------- #
    @router.get("/calls", response_class=HTMLResponse)
    def crm_calls(request: Request):
        f = _filters(request)
        with cursor() as c:
            rows = _auth.search_calls(c, **f)
            admins = _auth.admin_options(c)
        return templates.TemplateResponse(request, "admin_crm_calls.html", {
            "rows": rows, "admins": admins, "f": f,
            "statuses": _auth.CALL_STATUSES, "admin_tab": "crm-calls"})

    @router.get("/tasks", response_class=HTMLResponse)
    def crm_tasks(request: Request):
        f = _filters(request)
        with cursor() as c:
            rows = _auth.search_tasks(c, **f)
            admins = _auth.admin_options(c)
        return templates.TemplateResponse(request, "admin_crm_tasks.html", {
            "rows": rows, "admins": admins, "f": f,
            "statuses": _auth.TASK_STATUSES, "admin_tab": "crm-tasks"})

    @router.get("/notes", response_class=HTMLResponse)
    def crm_notes(request: Request):
        f = _filters(request)
        f.pop("status", None)   # notes have no status
        with cursor() as c:
            rows = _auth.search_notes(c, **f)
            admins = _auth.admin_options(c)
        return templates.TemplateResponse(request, "admin_crm_notes.html", {
            "rows": rows, "admins": admins, "f": f, "admin_tab": "crm-notes"})

    def _digest_ctx(c, uid, lang):
        """Everything the customer card needs to show and edit their result
        emails, plus the saved searches those emails are about.

        This lives on the customer's own page rather than in a portal-wide list:
        with more than a handful of customers, "who gets what" is a per-customer
        question, and the admin asking it is already looking at their card. The
        portal-wide side of the feature — the cadences themselves — stays at
        /admin/digests."""
        schedules = _digests.list_schedules(c)
        by_id = {sc["id"]: sc for sc in schedules}
        default = next((sc for sc in schedules if sc["is_default"]), None)
        now = _digests.dt.datetime.now(_digests.dt.timezone.utc)
        for sc in schedules:
            sc["label"] = _digests.describe_schedule(sc, lang)

        subs = _digests.list_subscriptions(c, user_id=uid)
        for sub in subs:
            sched = by_id.get(sub["schedule_id"]) or default
            sub["schedule_label"] = _digests.describe_schedule(sched, lang)
            sub["inherited"] = sub["schedule_id"] is None
            sub["next_run_at"] = _digests.next_occurrence(sched, now) if sched else None
            sub["runs"] = _digests.list_runs(c, subscription_id=sub["id"], limit=5)
            # The named readers beyond the account address. Listed even when
            # inactive: an admin who switched one off has to be able to see it
            # is there, or they will add the same person again.
            sub["recipients"] = _digests.list_recipients(c, sub["id"])

        # Saved searches: the customer's own, plus any portal profile they are
        # already mailed about. `sub_id` on each row ties the two lists together.
        searches = [dict(r) for r in _auth.customer_search_profiles(c, uid)]
        for row in searches:
            params = _auth.effective_params(c, row)
            row["filters"] = describe_params(params, lang)
            row["apply_qs"] = _sp.params_to_qs(params)

        # What may be subscribed: this customer's own profiles + every portal
        # profile. Offering someone else's private saved search here would only
        # produce a 400 from the endpoint, which already refuses it.
        c.execute("""SELECT sp.id, sp.name, sp.scope
                     FROM proc.search_profile sp
                     WHERE sp.scope = 'portal' OR sp.owner_user_id = %s
                     ORDER BY sp.scope, lower(sp.name)""", (uid,))
        options = c.fetchall()
        return {"digest_subs": subs, "digest_schedules": schedules,
                "digest_default_schedule": default,
                "digest_profile_options": options,
                "saved_searches": searches,
                "digest_mail": _mailer.describe(),
                "digest_layouts": _digests.LAYOUTS,
                "digest_langs": ("el", "en")}

    def _customer_ctx(request, uid, error=None, ok=None, warn=None, flash=None):
        lang = _i18n.lang_from_request(request)
        with cursor() as c:
            cust = _auth.get_customer(c, uid)
            if not cust:
                raise HTTPException(404, "customer not found")
            profile = _auth.get_profile(c, uid)
            history = _auth.subscription_history(c, uid)
            products = _auth.product_list(c)
            current = _auth.current_subscription(c, uid)
            notes = _auth.list_notes(c, uid)
            calls = _auth.list_calls(c, uid)
            tasks = _auth.list_tasks(c, uid)
            admins = _auth.admin_options(c)
            contacts = _auth.list_customer_contacts(c, uid)
            email_templates = _auth.email_template_options(c)
            # Link the customer's ΑΦΜ to a procurement contractor, if one matches.
            pvat = profile["vat_number"] if profile and profile.get("vat_number") else None
            linked_contractor = find_contractor_by_vat(c, pvat) if pvat else None
            # The contractor this lead was created from (customer_profile.operator_id).
            lead_operator = None
            if profile and profile.get("operator_id"):
                c.execute("SELECT operator_id, vat_number, name FROM proc.economic_operator "
                          "WHERE operator_id = %s", (profile["operator_id"],))
                lead_operator = c.fetchone()
            digest = _digest_ctx(c, uid, lang)
        # Entitlement drives the banner on the alerts panel: a subscription on a
        # lapsed account is kept, but nothing is sent until they are re-granted,
        # and the page has to say so rather than look like it is working.
        digest["digest_entitled"] = cust["status"] in _auth.ENTITLED_STATUSES
        return {**digest,
                "cust": cust, "profile": profile or {}, "history": history,
                "products": products, "current": current,
                "fields": _auth.PROFILE_FIELDS,
                "contacts": contacts, "lead_operator": lead_operator,
                "linked_contractor": linked_contractor, "profile_vat": pvat,
                "notes": notes, "calls": calls, "tasks": tasks, "admins": admins,
                "call_directions": _auth.CALL_DIRECTIONS,
                "call_statuses": _auth.CALL_STATUSES,
                "task_statuses": _auth.TASK_STATUSES,
                "summary_configured": _pipeline.feature_configured(),
                "email_templates": email_templates,
                "email_langs": _auth.EMAIL_TEMPLATE_LANGS,
                "error": error, "ok": ok, "warn": warn, "flash": flash,
                "admin_tab": "crm"}

    @router.get("/{uid}", response_class=HTMLResponse)
    def crm_customer(uid: int, request: Request, ok: str = None,
                     expired: str = None, flash: str = None):
        """`flash` is what the shared digest endpoints redirect back with (the
        outcome of a test/real send). `tab` is read by the page's own script and
        deliberately not a parameter here — the server renders every panel."""
        warn = ("Η προηγούμενη συνδρομή έληξε αυτόματα — μόνο ένα ενεργό προϊόν ανά πελάτη."
                if expired else None)
        return templates.TemplateResponse(
            request, "admin_crm_customer.html",
            _customer_ctx(request, uid, ok=ok, warn=warn, flash=flash))

    @router.post("/{uid}/profile")
    async def crm_profile_save(uid: int, request: Request):
        form = await request.form()
        values = {k: (form.get(k) or "").strip() for k in _auth.PROFILE_FIELDS}
        email = (form.get("email") or "").strip()
        try:
            with cursor() as c:
                if not _auth.get_customer(c, uid):
                    raise HTTPException(404, "customer not found")
                _auth.upsert_profile(c, uid, values,
                                     updated_by=_admin_uid(request))
                _auth.set_email(c, uid, email)
        except HTTPException:
            raise
        except Exception:   # unique email clash etc.
            return templates.TemplateResponse(
                request, "admin_crm_customer.html",
                _customer_ctx(request, uid,
                              error="Το email χρησιμοποιείται ήδη ή είναι μη έγκυρο."),
                status_code=400)
        return RedirectResponse(f"/admin/crm/{uid}?ok=profile", status_code=303)

    @router.post("/{uid}/grant")
    async def crm_grant(uid: int, request: Request):
        form = await request.form()
        product = form.get("product") or ""
        days_raw = (form.get("days") or "").strip()
        try:
            period_days = int(days_raw) if days_raw else None
            with cursor() as c:
                _, n_expired = _auth.grant_product(c, uid, product,
                                                   granted_by=_admin_uid(request),
                                                   period_days=period_days)
        except (ValueError, TypeError):
            return templates.TemplateResponse(
                request, "admin_crm_customer.html",
                _customer_ctx(request, uid, error="Μη έγκυρη ανάθεση."),
                status_code=400)
        suffix = "&expired=1" if n_expired else ""
        return RedirectResponse(f"/admin/crm/{uid}?ok=granted{suffix}", status_code=303)

    @router.post("/{uid}/subscription")
    async def crm_subscription(uid: int, request: Request):
        form = await request.form()
        extend_days = (form.get("extend_days") or "").strip()
        expires_at = (form.get("expires_at") or "").strip()
        with cursor() as c:
            sub = _auth.current_subscription(c, uid)
            if not sub:
                return templates.TemplateResponse(
                    request, "admin_crm_customer.html",
                    _customer_ctx(request, uid,
                                  error="Δεν υπάρχει ενεργό προϊόν — αναθέστε πρώτα."),
                    status_code=400)
            try:
                if extend_days:
                    _auth.extend_subscription(c, sub["id"], int(extend_days))
                elif expires_at:
                    _auth.set_subscription_expiry(c, sub["id"], expires_at + " 23:59:59")
            except (ValueError, TypeError):
                return templates.TemplateResponse(
                    request, "admin_crm_customer.html",
                    _customer_ctx(request, uid, error="Μη έγκυρη ημερομηνία/διάρκεια."),
                    status_code=400)
        return RedirectResponse(f"/admin/crm/{uid}?ok=subscription", status_code=303)

    # ---- activities: notes / calls / tasks --------------------------- #
    def _ensure_customer(c, uid):
        if not _auth.get_customer(c, uid):
            raise HTTPException(404, "customer not found")

    @router.post("/{uid}/note")
    async def crm_note(uid: int, request: Request):
        form = await request.form()
        try:
            with cursor() as c:
                _ensure_customer(c, uid)
                _auth.add_note(c, uid, form.get("body"), _admin_uid(request))
        except HTTPException:
            raise
        except ValueError:
            return RedirectResponse(f"/admin/crm/{uid}", status_code=303)
        return RedirectResponse(f"/admin/crm/{uid}?ok=note", status_code=303)

    @router.post("/{uid}/call")
    async def crm_call(uid: int, request: Request):
        form = await request.form()
        with cursor() as c:
            _ensure_customer(c, uid)
            _auth.add_call(c, uid,
                           subject=form.get("subject"),
                           direction=form.get("direction"),
                           status=form.get("status"),
                           scheduled_at=form.get("scheduled_at"),
                           outcome=form.get("outcome"),
                           assigned_to=form.get("assigned_to"),
                           created_by=_admin_uid(request))
        return RedirectResponse(f"/admin/crm/{uid}?ok=call", status_code=303)

    @router.post("/{uid}/call/{cid}/status")
    async def crm_call_status(uid: int, cid: int, request: Request):
        form = await request.form()
        try:
            with cursor() as c:
                _auth.set_call_status(c, cid, form.get("status"), form.get("outcome"))
        except ValueError:
            pass
        return RedirectResponse(f"/admin/crm/{uid}?ok=call", status_code=303)

    @router.post("/{uid}/call/{cid}/summarize")
    async def crm_call_summarize(uid: int, cid: int, request: Request):
        """Kick off transcription + AI summary for one recorded call. Runs in a
        background thread so the request returns immediately; the row's
        summary_status ('queued'→'running'→'done'/'error') drives the UI."""
        if not _pipeline.feature_configured():
            return RedirectResponse(f"/admin/crm/{uid}?warn=summary_off", status_code=303)
        with cursor() as c:
            c.execute("SELECT summary_status, recording_path FROM proc.customer_call "
                      "WHERE id = %s AND user_id = %s", (cid, uid))
            row = c.fetchone()
            if not row:
                raise HTTPException(404, "call not found")
            if row["summary_status"] in ("queued", "running"):
                return RedirectResponse(f"/admin/crm/{uid}?ok=summary", status_code=303)
            if not row["recording_path"]:
                return RedirectResponse(f"/admin/crm/{uid}?warn=no_recording", status_code=303)
            c.execute("UPDATE proc.customer_call "
                      "SET summary_status = 'queued', summary_error = NULL WHERE id = %s",
                      (cid,))
        threading.Thread(target=_pipeline.run_summary, args=(cursor, cid),
                         daemon=True).start()
        return RedirectResponse(f"/admin/crm/{uid}?ok=summary", status_code=303)

    @router.post("/{uid}/task")
    async def crm_task(uid: int, request: Request):
        form = await request.form()
        try:
            with cursor() as c:
                _ensure_customer(c, uid)
                _auth.add_task(c, uid,
                               subject=form.get("subject"),
                               body=form.get("body"),
                               status=form.get("status"),
                               due_at=form.get("due_at"),
                               outcome=form.get("outcome"),
                               assigned_to=form.get("assigned_to"),
                               created_by=_admin_uid(request))
        except HTTPException:
            raise
        except ValueError:
            return RedirectResponse(f"/admin/crm/{uid}", status_code=303)
        return RedirectResponse(f"/admin/crm/{uid}?ok=task", status_code=303)

    @router.post("/{uid}/task/{tid}/status")
    async def crm_task_status(uid: int, tid: int, request: Request):
        form = await request.form()
        try:
            with cursor() as c:
                _auth.set_task_status(c, tid, form.get("status"), form.get("outcome"))
        except ValueError:
            pass
        return RedirectResponse(f"/admin/crm/{uid}?ok=task", status_code=303)

    # ---- email builder: merge pasted text into a stored template ---------- #
    @router.post("/{uid}/email/preview", response_class=HTMLResponse)
    async def crm_email_preview(uid: int, request: Request):
        """Merge the pasted text into the chosen template and return the output
        pane. An HTMX partial rather than the 303-redirect the other CRM forms
        use, because a redirect would throw away what the admin just pasted.

        Nothing is sent or stored — the panel ends at copy/download.
        """
        form = await request.form()
        slug = (form.get("template") or "").strip()
        lang = (form.get("lang") or "el").strip()
        source = form.get("text") or ""

        with cursor() as c:
            cust = _auth.get_customer(c, uid)
            if not cust:
                raise HTTPException(404, "customer not found")
            profile = _auth.get_profile(c, uid)
            try:
                tpl = _auth.get_email_template(c, slug, lang)
            except ValueError:           # unsupported language
                tpl = None

        ctx = {"cust": cust, "result": None, "subject": None, "error": None}
        if not tpl:
            ctx["error"] = "Δεν βρέθηκε πρότυπο για αυτόν τον συνδυασμό γλώσσας."
            return templates.TemplateResponse(request, "_crm_email.html", ctx)

        values = merge_values(cust, profile)
        try:
            ctx["result"] = _email.build_email(source, tpl["body_html"], values)
            ctx["subject"] = _email.resolve_fields(tpl["subject"] or "", values)
        except _email.UnresolvedFieldsError as exc:
            # Refuse rather than send a half-filled greeting; the admin fills the
            # profile in and retries.
            ctx["error"] = ("Λείπουν στοιχεία του πελάτη για τα πεδία: "
                            + ", ".join(exc.fields))
        except ValueError as exc:        # over MAX_SOURCE_CHARS
            ctx["error"] = str(exc)

        return templates.TemplateResponse(request, "_crm_email.html", ctx)

    @router.post("/{uid}/email/count", response_class=HTMLResponse)
    async def crm_email_count(uid: int, request: Request):
        """Block counts for the paste box, as the admin types. Served rather
        than counted in the browser so the number shown is the one the merge
        will actually produce — segment_text is the single source of truth."""
        form = await request.form()
        paragraphs, lists = _email.segment_counts(form.get("text") or "")
        return templates.TemplateResponse(request, "_crm_email_count.html",
                                          {"paragraphs": paragraphs, "lists": lists})

    return router
