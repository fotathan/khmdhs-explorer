"""
account_searches.py — /account/searches: a customer's own saved searches and the
email alerts attached to them.

Why this exists
---------------
Both halves were admin-only. A saved search could be created only from the
search page's admin popover, and an alert only from /admin/crm/<uid>. That is a
scaling ceiling rather than a policy: with fifty customers, "email us and we
will set your alert up" is the reason there is no fifty-first. Nothing here is
a new capability — it is the same two tables, reachable by the person the rows
belong to.

What a customer may do here
---------------------------
  * save the filters they are looking at, name them, rename them, delete them;
  * turn an email alert on or off for any saved search they may use, and choose
    its format, cadence, language, how many results it lists, and — for the
    deadline format — how many days ahead it reminds.

What deliberately stays with an admin
-------------------------------------
  * PORTAL profiles and publishing. A portal profile is shared; a customer may
    subscribe to a published one, never edit or unpublish it.
  * The SCHEDULES themselves. A customer picks from the cadences the portal
    offers; inventing "every 5 minutes" is not theirs to do.
  * EXTRA RECIPIENTS. Self-serve "also mail these three addresses" is a way to
    send our mail to people who never asked for it, and deliverability
    (SPF/DKIM/DMARC, unsubscribe) is not done. Colleagues are still added by an
    admin on the CRM card; this page shows who they are, read-only, so the
    customer can at least see the list.

Entitlement
-----------
Mirrors the CRM exactly: the settings SAVE whatever the customer's status is,
and the SEND is gated in digests.active_subscriptions and again in
run_subscription. A lapsed customer keeps their alerts, is told plainly that
nothing is going out, and they resume on the next grant — the same behaviour an
admin sees on the customer card, so the two surfaces cannot disagree.

Ownership, in one place
-----------------------
_owned() is the only door to a profile row: rename and delete demand a profile
this user OWNS, while the alert endpoints demand only one they may APPLY (their
own, or a published portal profile). That distinction is the whole access
model — a customer may be mailed about a shared search without being able to
edit it, and must never be able to subscribe to an UNPUBLISHED portal profile
they were never shown.
"""
from __future__ import annotations

import datetime as dt
import os

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

try:
    from app import auth as _auth
    from app import crm as _crm
    from app import digests as _digests
    from app import i18n as _i18n
    from app import search_profiles as _sp
except ImportError:                      # pragma: no cover — run with --app-dir=app
    import auth as _auth
    import crm as _crm
    import digests as _digests
    import i18n as _i18n
    import search_profiles as _sp

PAGE = "/account/searches"

# How many saved searches one customer may keep. Not a licensing lever — a
# bound on a table anyone signed in can now write to. Twenty-five is far more
# than anybody has asked for and still small enough that a script pointed at
# this endpoint cannot fill the table.
MAX_SAVED_SEARCHES = max(1, int(os.environ.get("MAX_SAVED_SEARCHES") or 25))

# How many past sends to show under an alert. Enough to tell "it is working"
# from "it has never run"; the full history stays an admin view.
RUNS_SHOWN = 3


def _flash_url(msg=None, tab=None):
    url = PAGE
    if msg:
        from urllib.parse import urlencode
        url += "?" + urlencode({"flash": msg})
    return url


def make_router(templates: Jinja2Templates, cursor) -> APIRouter:
    router = APIRouter(prefix="/account/searches", tags=["account"])

    def _signed_in(request):
        """POST guard. A signed-out POST is a stale tab or a forgery, not a
        person who needs the login page — the GET handler redirects, this one
        refuses."""
        u = getattr(request.state, "user", None)
        if not u:
            raise HTTPException(403, "sign in first")
        return u

    def _owned(c, user, pid, *, mode="manage"):
        """The profile, if this user may act on it — and 404 if they may not.

        404 rather than 403 on purpose: an id that belongs to someone else must
        not be distinguishable from one that does not exist, or this endpoint
        becomes a way to count other customers' saved searches.

        mode='manage' — rename/delete: their OWN customer-scoped profile only.
        mode='apply'  — the alert: anything they may run, which includes a
                        PUBLISHED portal profile. can_apply_profile is the same
                        check the search page's apply button makes.
        """
        profile = _auth.get_search_profile(c, pid)
        if not profile:
            raise HTTPException(404, "saved search not found")
        if mode == "apply":
            if not _auth.can_apply_profile(user, profile):
                raise HTTPException(404, "saved search not found")
        else:
            own = (profile["scope"] == "customer"
                   and profile["owner_user_id"] == user["id"])
            if not own:
                raise HTTPException(404, "saved search not found")
        return profile

    # ---- the page ---------------------------------------------------------- #
    @router.get("", response_class=HTMLResponse)
    def page(request: Request, flash: str = ""):
        user = getattr(request.state, "user", None)
        if not user:
            return RedirectResponse(url=f"/login?next={PAGE}", status_code=303)
        lang = _i18n.lang_from_request(request)
        now = dt.datetime.now(dt.timezone.utc)
        with cursor() as c:
            schedules = [dict(s) for s in _digests.list_schedules(c)
                         if s["is_active"]]
            for sched in schedules:
                sched["label"] = _digests.describe_schedule(sched, lang)
            default = next((s for s in schedules if s["is_default"]), None)

            subs = {s["search_profile_id"]: dict(s)
                    for s in _digests.list_subscriptions(c, user_id=user["id"])}
            rows = []
            for profile in _auth.customer_search_profiles(c, user["id"]):
                row = dict(profile)
                params = _auth.effective_params(c, profile)
                row["filters"] = _crm.describe_params(params, lang)
                row["apply_qs"] = _sp.params_to_qs(params)
                sub = subs.get(profile["id"])
                if sub:
                    by_id = {s["id"]: s for s in schedules}
                    sched = by_id.get(sub["schedule_id"]) or default
                    sub["schedule_label"] = _digests.describe_schedule(sched, lang)
                    sub["inherited"] = sub["schedule_id"] is None
                    sub["next_run_at"] = (_digests.next_occurrence(sched, now)
                                          if sched else None)
                    sub["recipients"] = _digests.list_recipients(
                        c, sub["id"], active_only=True)
                    sub["runs"] = _digests.list_runs(c, subscription_id=sub["id"],
                                                     limit=RUNS_SHOWN)
                row["sub"] = sub
                rows.append(row)
        return templates.TemplateResponse(request, "account_searches.html", {
            "rows": rows, "schedules": schedules, "default_schedule": default,
            "layouts": _digests.LAYOUTS,
            "default_lead_days": ", ".join(str(n) for n in _digests.DEFAULT_LEAD_DAYS),
            # The same gate the CRM card shows, worded for the person it is
            # about: settings are kept, sending is what stops.
            "entitled": bool(user.get("has_access")),
            "max_saved": MAX_SAVED_SEARCHES,
            "n_own": sum(1 for r in rows if r.get("is_own")),
            "flash": flash or None,
            "nav_active": "search",
        })

    # ---- create ------------------------------------------------------------ #
    @router.post("")
    async def create(request: Request, name: str = Form(...),
                     params_qs: str = Form(""), next: str = Form("")):
        """Save the filters the customer is looking at.

        Always scope='customer', always owned by the person posting — there is
        no field to say otherwise, which is why this endpoint is safe to expose
        where the admin one was not.
        """
        user = _signed_in(request)
        name = (name or "").strip()
        if not name:
            raise HTTPException(400, "a name is required")
        params = _sp.params_from_qs(params_qs or "")
        if not params:
            # A profile with no filters would match the entire corpus, and an
            # alert on it would mail thousands of acts a day.
            return RedirectResponse(
                url=_flash_url("Προσθέστε τουλάχιστον ένα φίλτρο πριν την αποθήκευση."),
                status_code=303)
        with cursor() as c:
            c.execute("""SELECT count(*) AS n FROM proc.search_profile
                          WHERE scope = 'customer' AND owner_user_id = %s""",
                      (user["id"],))
            if c.fetchone()["n"] >= MAX_SAVED_SEARCHES:
                return RedirectResponse(
                    url=_flash_url("Έχετε φτάσει το όριο αποθηκευμένων αναζητήσεων. "
                                   "Διαγράψτε μία για να προσθέσετε νέα."),
                    status_code=303)
            _auth.create_search_profile(
                c, name=name[:120], scope="customer", owner_id=user["id"],
                params=params, based_on_id=None, created_by=user["id"])
        # Back where they were (the search they just saved), not to this page:
        # saving is something you do mid-search.
        return RedirectResponse(url=(next or PAGE), status_code=303)

    # ---- rename / delete --------------------------------------------------- #
    @router.post("/{pid}/rename")
    async def rename(pid: int, request: Request, name: str = Form(...)):
        user = _signed_in(request)
        name = (name or "").strip()
        with cursor() as c:
            profile = _owned(c, user, pid)
            if name:
                _auth.update_search_profile(
                    c, pid, name=name[:120], params=profile["params"],
                    based_on_id=profile["based_on_id"],
                    is_published=profile["is_published"])
        return RedirectResponse(url=PAGE, status_code=303)

    @router.post("/{pid}/delete")
    async def delete(pid: int, request: Request):
        """Deleting the search deletes its alert with it (the subscription FK
        cascades), which is what someone clicking "delete" means."""
        user = _signed_in(request)
        with cursor() as c:
            _owned(c, user, pid)
            _auth.delete_search_profile(c, pid)
        return RedirectResponse(url=_flash_url("Η αποθηκευμένη αναζήτηση διαγράφηκε."),
                                status_code=303)

    # ---- the alert --------------------------------------------------------- #
    @router.post("/{pid}/alert")
    async def save_alert(pid: int, request: Request,
                         is_active: str = Form(""),
                         layout: str = Form("list"),
                         schedule_id: str = Form(""),
                         lang: str = Form("el"),
                         max_results: str = Form("25"),
                         lead_days: str = Form(""),
                         send_empty: str = Form("")):
        """Create or edit the alert for one saved search.

        `mode='apply'` and not 'manage': being mailed about a shared portal
        search is not editing it. include_primary is not offered — this is the
        customer's own alert and the account address is the whole point of it.
        """
        user = _signed_in(request)
        with cursor() as c:
            _owned(c, user, pid, mode="apply")
            # A schedule id the customer did not get from this page's select
            # (or one that has since been deactivated) falls back to the portal
            # default rather than pinning the alert to a schedule nobody runs.
            sid = None
            if (schedule_id or "").strip():
                sched = _digests.get_schedule(c, int(schedule_id))
                sid = sched["id"] if (sched and sched["is_active"]) else None
            existing = None
            c.execute("""SELECT lead_days FROM proc.digest_subscription
                          WHERE user_id = %s AND search_profile_id = %s""",
                      (user["id"], pid))
            row = c.fetchone()
            existing = row["lead_days"] if row else None
            try:
                _digests.upsert_subscription(
                    c, user_id=user["id"], search_profile_id=pid,
                    schedule_id=sid, is_active=bool(is_active),
                    send_empty=bool(send_empty),
                    max_results=max(1, min(200, int(max_results or 25))),
                    lang=lang, layout=layout, include_primary=True,
                    lead_days=(lead_days.strip() or existing),
                    created_by=user["id"])
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
        return RedirectResponse(url=_flash_url("Οι ρυθμίσεις ειδοποίησης αποθηκεύτηκαν."),
                                status_code=303)

    @router.post("/{pid}/alert/delete")
    async def delete_alert(pid: int, request: Request):
        """Stop being mailed entirely. Deactivating would keep the row (and the
        window it has consumed); a customer clicking "remove the alert" means
        remove it, and setting it up again later starts a fresh window."""
        user = _signed_in(request)
        with cursor() as c:
            _owned(c, user, pid, mode="apply")
            c.execute("""DELETE FROM proc.digest_subscription
                          WHERE user_id = %s AND search_profile_id = %s""",
                      (user["id"], pid))
        return RedirectResponse(url=_flash_url("Η ειδοποίηση καταργήθηκε."),
                                status_code=303)

    return router
