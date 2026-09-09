"""The AI summary PANEL: the cache, the two routes, and the job it queues.

tests/test_ai_summary.py covers the module in isolation — the quote gate, the
schema, the cache key, the transport. This file covers what the module cannot
test on its own: what a reader actually gets on an act page, and who is allowed
to spend money.

The rules being defended here, all from docs/specs/ai-summary.md:

  §12  the switch fails towards OFF, generation is admins-only, and a reader
       with no entitlement sees no panel at all;
  §9   a cached payload is served ONLY when its input_hash still matches, and a
       superseded payload is kept rather than overwritten;
  §10  a generation is a QUEUED JOB, never work done inside the web request —
       so the POST's observable effect is a row in proc.ai_summary_job carrying
       the db.py argv the worker will run;
  §11  the screening banner is always present, and an item links to the exact
       paragraph its quote came from.

Every payload here is built through ai_summary.verify(), never hand-written, so
a test can only assert on shapes the real pipeline can actually produce.
"""
import pytest

from tests.helpers import connect, get_csrf, grant, login, make_user

ADAM = "TEST-AI-PANEL-0001"

FULL_TEXT = (
    "ΔΙΑΚΗΡΥΞΗ ΑΝΟΙΚΤΟΥ ΔΙΑΓΩΝΙΣΜΟΥ\n"
    "Αντικείμενο του διαγωνισμού είναι η προμήθεια ιατροτεχνολογικού εξοπλισμού.\n"
    "\n"
    "Η προθεσμία υποβολής ερωτημάτων λήγει στις 12/06/2026 και ώρα 15:00.\n"
    "\n"
    "Η εγγύηση συμμετοχής ορίζεται σε ποσοστό 2% της εκτιμώμενης αξίας, ήτοι "
    "ποσού 20.000 ευρώ, και κατατίθεται με την προσφορά.\n"
    "\n"
    "Οι συμμετέχοντες οφείλουν να διαθέτουν πιστοποίηση ISO 9001:2015 σε ισχύ.\n"
)


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture()
def act(db):
    cur = db.cursor()
    cur.execute("DELETE FROM proc.procurement_act WHERE adam=%s", (ADAM,))
    cur.execute("""INSERT INTO proc.procurement_act
                     (adam, type, title, origin, data_source, full_text)
                   VALUES (%s, 'notice', %s, 'import', 'khmdhs', %s)""",
                (ADAM, "Προμήθεια ιατροτεχνολογικού εξοπλισμού", FULL_TEXT))
    yield ADAM
    # Cascades to act_ai_summary and ai_summary_job; history has no FK and is
    # cleared explicitly, because it is meant to outlive the act.
    cur.execute("DELETE FROM proc.act_ai_summary_history WHERE adam=%s", (ADAM,))
    cur.execute("DELETE FROM proc.procurement_act WHERE adam=%s", (ADAM,))


@pytest.fixture()
def ai_on(monkeypatch):
    """Feature on, with a key. Both layers: the env the routes read, and the
    template global that decides whether the act page mounts the panel at all
    (frozen at import, like login_links_enabled)."""
    monkeypatch.setenv("AI_SUMMARY_ENABLED", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-used")
    from app.main import templates
    monkeypatch.setitem(templates.env.globals, "ai_summary_enabled", True)


@pytest.fixture()
def admin(client):
    make_user("aipanel_admin", "goodpassword1", role="admin")
    login(client, "aipanel_admin", "goodpassword1")
    return client


@pytest.fixture()
def customer(client):
    uid = make_user("aipanel_cust", "goodpassword1")
    grant(uid, "pro", days=365)
    login(client, "aipanel_cust", "goodpassword1")
    return client


def _sources(cur, adam):
    from app import ai_summary as ai
    act_row, sources = ai.load_inputs(cur, adam)
    return act_row, sources


def _store_payload(cur, adam, *, raw=None, hash_=None):
    """Put a real, verified payload in the cache. Returns it."""
    from app import ai_summary as ai
    act_row, sources = _sources(cur, adam)
    raw = raw if raw is not None else {
        "timeline": [{
            "label": "Προθεσμία ερωτημάτων",
            "value": "12/06/2026, 15:00",
            "obligation": "mandatory",
            "confidence": "high",
            "source": "full_text",
            "quote": "Η προθεσμία υποβολής ερωτημάτων λήγει στις 12/06/2026",
        }],
        "eligibility": [{
            "label": "Πιστοποίηση ποιότητας",
            "value": "ISO 9001:2015",
            "obligation": "mandatory",
            "confidence": "high",
            "source": "full_text",
            "quote": "πιστοποίηση ISO 9001:2015 σε ισχύ",
        }],
        "not_found": ["Ημερομηνία επιτόπιας επίσκεψης"],
    }
    payload = ai.verify(raw, sources, act_row)
    ai.store(cur, adam, payload=payload,
             hash_=hash_ or ai.input_hash(sources),
             usage={"input_tokens": 4000, "output_tokens": 900},
             by="aipanel_admin")
    return payload


# --------------------------------------------------------------------------- #
# §9 — the cache is only ever served for the inputs it answers
# --------------------------------------------------------------------------- #
def test_a_stored_payload_is_served_for_the_same_inputs(db, act):
    from app import ai_summary as ai
    cur = db.cursor()
    _store_payload(cur, act)
    _row, sources = _sources(cur, act)
    hit = ai.cached(cur, act, ai.input_hash(sources))
    assert hit is not None
    assert hit["payload"]["n_items"] == 2


def test_editing_the_full_text_invalidates_the_cached_payload(db, act):
    from app import ai_summary as ai
    cur = db.cursor()
    _store_payload(cur, act)
    cur.execute("UPDATE proc.procurement_act SET full_text = full_text || %s "
                "WHERE adam=%s", ("\nΠροστέθηκε παράγραφος.\n", act))
    _row, sources = _sources(cur, act)
    # The row is still there — it is simply not an answer to this question any
    # more, because its offsets point into text that no longer exists.
    assert ai.cached(cur, act, ai.input_hash(sources)) is None
    cur.execute("SELECT count(*) AS n FROM proc.act_ai_summary WHERE adam=%s", (act,))
    assert cur.fetchone()["n"] == 1


def test_regeneration_keeps_the_payload_it_replaces(db, act):
    """A customer who asks 'your summary said X' needs an answer."""
    cur = db.cursor()
    _store_payload(cur, act)
    _store_payload(cur, act)
    cur.execute("SELECT count(*) AS n FROM proc.act_ai_summary_history WHERE adam=%s",
                (act,))
    assert cur.fetchone()["n"] == 1
    cur.execute("SELECT count(*) AS n FROM proc.act_ai_summary WHERE adam=%s", (act,))
    assert cur.fetchone()["n"] == 1


# --------------------------------------------------------------------------- #
# §12 — the switch, and who may read
# --------------------------------------------------------------------------- #
def test_the_panel_is_empty_when_the_switch_is_off(admin, act, monkeypatch):
    monkeypatch.delenv("AI_SUMMARY_ENABLED", raising=False)
    r = admin.get(f"/act/{act}/ai")
    assert r.status_code == 200 and r.text.strip() == ""


@pytest.mark.parametrize("value", ["0", "false", "FALSE", "off", "no", "disabled", "1 "])
def test_only_an_affirmative_value_turns_the_panel_on(admin, act, monkeypatch, value):
    """Anything not recognisably 'yes' is off — including a typo. The one
    surprise in the list is '1 ' with a space: it IS on, because the value is
    stripped before it is read."""
    monkeypatch.setenv("AI_SUMMARY_ENABLED", value)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-used")
    r = admin.get(f"/act/{act}/ai")
    if value.strip() == "1":
        assert r.text.strip() != ""
    else:
        assert r.text.strip() == ""


def test_the_panel_is_empty_for_an_anonymous_reader(client, ai_on, act):
    """The full text is behind the paywall; so is a reading of it."""
    r = client.get(f"/act/{act}/ai")
    assert r.status_code == 200 and r.text.strip() == ""


def test_the_panel_is_empty_for_anything_but_a_notice(db, admin, ai_on, act):
    db.cursor().execute("UPDATE proc.procurement_act SET type='contract' WHERE adam=%s",
                        (act,))
    assert admin.get(f"/act/{act}/ai").text.strip() == ""


def test_a_notice_with_no_text_offers_nothing_to_extract(db, admin, ai_on, act):
    db.cursor().execute("UPDATE proc.procurement_act SET full_text=NULL WHERE adam=%s",
                        (act,))
    assert admin.get(f"/act/{act}/ai").text.strip() == ""


def test_a_customer_with_no_summary_sees_no_panel_at_all(db, customer, ai_on, act):
    """An empty AI panel is a promise the page cannot keep: a reader who may not
    generate one gets no element."""
    assert customer.get(f"/act/{act}/ai").text.strip() == ""


def test_an_admin_with_no_summary_gets_the_generate_button(admin, ai_on, act):
    body = admin.get(f"/act/{act}/ai").text
    assert 'hx-post="/admin/act/' in body
    assert "Δημιουργία σύνοψης" in body


def test_without_an_api_key_cached_rows_still_render_and_the_button_is_gone(
        db, admin, ai_on, act, monkeypatch):
    _store_payload(db.cursor(), act)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    body = admin.get(f"/act/{act}/ai").text
    assert "ISO 9001:2015" in body               # the payload still reads
    assert "Δημιουργία σύνοψης" not in body      # nothing offers to spend money


# --------------------------------------------------------------------------- #
# §11 — what the panel says, and what it never says
# --------------------------------------------------------------------------- #
def test_the_screening_banner_is_present_in_every_state(db, admin, ai_on, act):
    banner = "Εργαλείο πρώτης αξιολόγησης"
    assert banner in admin.get(f"/act/{act}/ai").text        # absent state
    _store_payload(db.cursor(), act)
    assert banner in admin.get(f"/act/{act}/ai").text        # present state


def test_a_stored_payload_renders_its_sections_with_their_evidence(db, admin, ai_on, act):
    _store_payload(db.cursor(), act)
    body = admin.get(f"/act/{act}/ai").text
    assert "Χρονοδιάγραμμα" in body and "Πότε πρέπει να ενεργήσω;" in body
    assert "Κριτήρια συμμετοχής" in body
    assert "ISO 9001:2015" in body
    # The quote is not decoration: no item may render without the words that
    # produced it.
    assert "πιστοποίηση ISO 9001:2015 σε ισχύ" in body


def test_the_provenance_badge_marks_every_section(db, admin, ai_on, act):
    _store_payload(db.cursor(), act)
    body = admin.get(f"/act/{act}/ai").text
    assert body.count("AI εξαγωγή") >= 3        # panel heading + two sections


def test_what_the_notice_does_not_say_is_reported(db, admin, ai_on, act):
    _store_payload(db.cursor(), act)
    body = admin.get(f"/act/{act}/ai").text
    assert "Δεν αναφέρονται στην προκήρυξη" in body
    assert "Ημερομηνία επιτόπιας επίσκεψης" in body


def test_a_customer_sees_the_summary_but_not_the_rejection_count(db, customer, ai_on, act):
    """A bidder has no use for a quote-gate rejection count, and a rising one is
    our problem, not theirs."""
    _store_payload(db.cursor(), act)
    body = customer.get(f"/act/{act}/ai").text
    assert "ISO 9001:2015" in body
    assert "απορρίφθηκαν" not in body
    assert "αναδημιουργία" not in body


def test_an_item_links_to_the_paragraph_its_quote_came_from(db, admin, ai_on, act):
    """The quote gate's third product (§8): the offset becomes a deep link, and
    the anchor it names must exist on the act page itself."""
    _store_payload(db.cursor(), act)
    panel = admin.get(f"/act/{act}/ai").text
    import re
    anchors = re.findall(r'data-ai-anchor="(ft-p-\d+)"', panel)
    assert anchors, "no citation linked back into the full text"
    page = admin.get(f"/act/{act}").text
    for anchor in anchors:
        assert f'id="{anchor}"' in page


def test_the_act_page_mounts_the_panel_lazily(admin, ai_on, act):
    page = admin.get(f"/act/{act}").text
    assert f'hx-get="/act/{act}/ai"' in page
    assert 'id="ai-summary-mount"' in page
    # ...and pays nothing for it up front: the payload is not in this response.
    assert "Χρονοδιάγραμμα" not in page


# --------------------------------------------------------------------------- #
# §10 / §12 — generation is a queued job, and only an admin may ask for one
# --------------------------------------------------------------------------- #
def _post(cl, adam):
    return cl.post(f"/admin/act/{adam}/ai", headers={"X-CSRF-Token": get_csrf(cl)},
                   follow_redirects=False)


def test_a_customer_may_not_generate(customer, ai_on, act):
    assert _post(customer, act).status_code == 403


def test_an_anonymous_visitor_may_not_generate(client, ai_on, act):
    assert _post(client, act).status_code in (303, 403)


def test_generation_is_gone_when_the_switch_is_off(admin, act, monkeypatch):
    monkeypatch.delenv("AI_SUMMARY_ENABLED", raising=False)
    assert _post(admin, act).status_code == 404


def test_generation_is_refused_without_an_api_key(admin, ai_on, act, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert _post(admin, act).status_code == 400


def test_generation_queues_a_job_carrying_the_argv_the_worker_will_run(
        db, admin, ai_on, act):
    from app import ai_summary as ai
    r = _post(admin, act)
    assert r.status_code == 200
    cur = db.cursor()
    cur.execute("SELECT * FROM proc.ai_summary_job WHERE adam=%s", (act,))
    job = cur.fetchone()
    assert job["status"] == "queued"
    assert job["command"] == ["ai-summary", "--job", str(job["id"])]
    assert job["requested_by"] == "aipanel_admin"
    # The hash the job was queued FOR, so a re-ingest mid-queue is detectable.
    _row, sources = _sources(cur, act)
    assert job["input_hash"] == ai.input_hash(sources)
    # No model was called in the request itself.
    assert "Η σύνοψη δημιουργείται" in r.text


def test_two_clicks_do_not_buy_the_same_summary_twice(db, admin, ai_on, act):
    _post(admin, act)
    _post(admin, act)
    cur = db.cursor()
    cur.execute("SELECT count(*) AS n FROM proc.ai_summary_job WHERE adam=%s", (act,))
    assert cur.fetchone()["n"] == 1


def test_the_daily_cap_stops_generation(db, admin, ai_on, act, monkeypatch):
    from app import ai_summary as ai
    monkeypatch.setattr(ai, "DAILY_CAP", 0)
    assert _post(admin, act).status_code == 429
    cur = db.cursor()
    cur.execute("SELECT count(*) AS n FROM proc.ai_summary_job WHERE adam=%s", (act,))
    assert cur.fetchone()["n"] == 0


def test_a_running_job_makes_the_panel_poll_itself(db, admin, ai_on, act):
    _post(admin, act)
    body = admin.get(f"/act/{act}/ai").text
    assert f'hx-get="/act/{act}/ai"' in body and "hx-trigger=\"load delay:" in body


def test_a_failed_job_offers_a_retry_and_shows_the_reason_to_an_admin(
        db, admin, ai_on, act):
    _post(admin, act)
    db.cursor().execute("""UPDATE proc.ai_summary_job
                             SET status='error', last_error='Anthropic API error 529'
                           WHERE adam=%s""", (act,))
    body = admin.get(f"/act/{act}/ai").text
    assert "Anthropic API error 529" in body
    assert "Νέα προσπάθεια" in body


def test_a_failed_job_is_invisible_to_a_customer(db, admin, customer, ai_on, act):
    """Our job errors are not a bidder's business — and with no payload there is
    nothing for them to see."""
    cur = db.cursor()
    cur.execute("""INSERT INTO proc.ai_summary_job (adam, status, last_error)
                   VALUES (%s, 'error', 'Anthropic API error 529')""", (act,))
    assert customer.get(f"/act/{act}/ai").text.strip() == ""


# --------------------------------------------------------------------------- #
# Abandoned jobs — a 'running' row whose worker died
#
# prod drains this queue with worker.py running INLINE in the web dyno
# (RUN_INLINE_WORKER=1), on a free Render instance that restarts on every deploy
# and sleeps when idle. So a container dying mid-call is not a rare accident,
# it is a weekly event, and "status says running" stops being evidence that
# anything is running. What makes that dangerous is the duplicate guard: an
# abandoned row counted as live refuses every FUTURE generation for that act.
# --------------------------------------------------------------------------- #
def _abandon(cur, adam, *, age="10 minutes"):
    """A job claimed by a worker that then died: running, heartbeat gone cold."""
    cur.execute(f"""INSERT INTO proc.ai_summary_job
                      (adam, status, worker_id, started_at, heartbeat_at)
                    VALUES (%s, 'running', 'dead-container:1',
                            now() - interval '{age}', now() - interval '{age}')
                    RETURNING id""", (adam,))
    return cur.fetchone()["id"]


def test_a_live_job_is_one_with_a_warm_heartbeat(db, admin, ai_on, act):
    cur = db.cursor()
    cur.execute("""INSERT INTO proc.ai_summary_job
                     (adam, status, heartbeat_at) VALUES (%s, 'running', now())""",
                (act,))
    assert "Η σύνοψη δημιουργείται" in admin.get(f"/act/{act}/ai").text


def test_an_abandoned_job_reads_as_failed_not_as_still_running(db, admin, ai_on, act):
    """Otherwise the panel polls that act forever, on a promise nothing is
    keeping."""
    _abandon(db.cursor(), act)
    body = admin.get(f"/act/{act}/ai").text
    assert "Η σύνοψη δημιουργείται" not in body
    assert "Η δημιουργία διακόπηκε πριν ολοκληρωθεί" in body
    assert "Νέα προσπάθεια" in body


def test_an_abandoned_job_does_not_lock_the_act_forever(db, admin, ai_on, act):
    """The regression that matters. Before the heartbeat check, a container
    that died mid-call left a row that refused every later generation for that
    act — a permanent lock, fixable only by hand."""
    dead = _abandon(db.cursor(), act)
    assert _post(admin, act).status_code == 200
    cur = db.cursor()
    cur.execute("""SELECT id, status FROM proc.ai_summary_job
                    WHERE adam=%s ORDER BY id""", (act,))
    rows = cur.fetchall()
    assert len(rows) == 2, "the retry did not enqueue"
    assert rows[0]["id"] == dead and rows[0]["status"] == "error"   # buried
    assert rows[1]["status"] == "queued"                            # the retry


def test_burying_an_abandoned_job_says_why(db, admin, ai_on, act):
    _abandon(db.cursor(), act)
    _post(admin, act)
    cur = db.cursor()
    cur.execute("""SELECT last_error, finished_at FROM proc.ai_summary_job
                    WHERE adam=%s ORDER BY id LIMIT 1""", (act,))
    row = cur.fetchone()
    assert "abandoned" in row["last_error"]
    assert row["finished_at"] is not None


def test_a_genuinely_running_job_still_blocks_a_second_click(db, admin, ai_on, act):
    """The grace period must not reopen the door the duplicate guard closes:
    a warm heartbeat means someone IS spending money on this act right now."""
    cur = db.cursor()
    cur.execute("""INSERT INTO proc.ai_summary_job
                     (adam, status, heartbeat_at) VALUES (%s, 'running', now())""",
                (act,))
    _post(admin, act)
    cur.execute("SELECT count(*) AS n FROM proc.ai_summary_job WHERE adam=%s", (act,))
    assert cur.fetchone()["n"] == 1


def test_a_queued_job_is_live_without_any_heartbeat(db, admin, ai_on, act):
    """A queued row has no heartbeat by definition — it is claimed whenever a
    worker next starts, which on a sleeping free instance may be a while. It
    must not be mistaken for abandoned and re-enqueued."""
    cur = db.cursor()
    cur.execute("""INSERT INTO proc.ai_summary_job (adam, status, queued_at)
                   VALUES (%s, 'queued', now() - interval '3 hours')""", (act,))
    _post(admin, act)
    cur.execute("SELECT count(*) AS n FROM proc.ai_summary_job WHERE adam=%s", (act,))
    assert cur.fetchone()["n"] == 1


# --------------------------------------------------------------------------- #
# The runner and the queue it is drained from
# --------------------------------------------------------------------------- #
def test_the_worker_drains_the_ai_summary_queue():
    import worker
    assert "proc.ai_summary_job" in worker.QUEUES


def test_the_runner_refuses_a_job_whose_act_changed_under_it(db, act, monkeypatch):
    """A result about the OLD text is not worth caching under the NEW hash, and
    under the old one it would never be read again. Give up instead."""
    import db as db_cli
    from app import ai_summary as ai

    cur = db.cursor()
    cur.execute("""INSERT INTO proc.ai_summary_job (adam, input_hash, status)
                   VALUES (%s, 'a-hash-from-before-the-edit', 'running')
                   RETURNING id""", (act,))
    job_id = cur.fetchone()["id"]

    def _explode(*a, **kw):
        raise AssertionError("the model must not be called for a stale job")
    monkeypatch.setattr(ai, "generate", _explode)

    db_cli.cmd_ai_summary(type("Args", (), {"job": job_id})())

    cur.execute("SELECT status FROM proc.ai_summary_job WHERE id=%s", (job_id,))
    assert cur.fetchone()["status"] == "stale"
    cur.execute("SELECT count(*) AS n FROM proc.act_ai_summary WHERE adam=%s", (act,))
    assert cur.fetchone()["n"] == 0


def test_the_runner_records_a_failure_on_the_job_row(db, act, monkeypatch):
    import db as db_cli
    from app import ai_summary as ai

    cur = db.cursor()
    cur.execute("""INSERT INTO proc.ai_summary_job (adam, status)
                   VALUES (%s, 'running') RETURNING id""", (act,))
    job_id = cur.fetchone()["id"]

    def _fail(*a, **kw):
        raise ai.SummaryError("Anthropic API error 500: upstream")
    monkeypatch.setattr(ai, "generate", _fail)

    with pytest.raises(ai.SummaryError):
        db_cli.cmd_ai_summary(type("Args", (), {"job": job_id})())

    cur.execute("SELECT status, last_error FROM proc.ai_summary_job WHERE id=%s",
                (job_id,))
    row = cur.fetchone()
    assert row["status"] == "error"
    assert "upstream" in row["last_error"]


def test_the_runner_stores_what_the_model_returned(db, act, monkeypatch):
    """The happy path, with the API call replaced — everything downstream of the
    model (verify → store → cached) is real."""
    import db as db_cli
    from app import ai_summary as ai

    cur = db.cursor()
    cur.execute("""INSERT INTO proc.ai_summary_job (adam, input_hash, requested_by, status)
                   VALUES (%s, %s, 'aipanel_admin', 'running') RETURNING id""",
                (act, ai.input_hash(_sources(cur, act)[1])))
    job_id = cur.fetchone()["id"]

    def _fake_call(sources, record, *, model=None):
        return ({"eligibility": [{
            "label": "Πιστοποίηση ποιότητας", "value": "ISO 9001:2015",
            "obligation": "mandatory", "confidence": "high",
            "source": "full_text", "quote": "πιστοποίηση ISO 9001:2015 σε ισχύ",
        }]}, {"input_tokens": 5000, "output_tokens": 800}, None)
    monkeypatch.setattr(ai, "call_model", _fake_call)

    db_cli.cmd_ai_summary(type("Args", (), {"job": job_id})())

    cur.execute("SELECT status FROM proc.ai_summary_job WHERE id=%s", (job_id,))
    assert cur.fetchone()["status"] == "done"
    hit = ai.cached(cur, act, ai.input_hash(_sources(cur, act)[1]))
    assert hit is not None
    assert hit["payload"]["sections"][0]["key"] == "eligibility"
    assert hit["generated_by"] == "aipanel_admin"
    # $5.00/MTok in + $25.00/MTok out on Opus 5 → 5000*5 + 800*25 = 45,000 µ$.
    assert hit["cost_micro_usd"] == 45000
