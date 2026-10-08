# First-call sales script (conditional)

Status: slice 1 built 2026-10-08 (branch `feat/call-script`). Builds on the
CRM brief (docs/specs/crm-brief-kad.md, app/crm_brief.py). The owner's answers
to §11 are recorded there. Nothing here is customer-facing.

## 1. The problem

The brief tells a salesperson *what we know* about a company. It does not tell
them *what to say*. The people making first calls are salespeople, not tender
experts, so they need the words, the questions and what to do with each
answer. The right words depend on two things the system already knows: how
the contact reached us, and how much we really know about them.

## 2. Decisions so far (owner, 2026-10-08)

| Question | Decision |
|---|---|
| Who uses it | **Salespeople.** Every sentence written out, not bullet hints |
| Record the call result | **Yes, in the first version** (§8) |
| Where it opens | **Both:** a dialog on the CRM card + a printable page |
| Written by | **Fixed rules + fixed wording, no model** (§4) |
| Origin marker | **Yes**: /register writes 'register', admin creation 'admin', backfill §3.1 |
| Wording | **In code first** (call_script_text.py), an editor in slice 2 |
| Result codes | **§8's list as is** |
| Brand | **Promitheies.gr or Tender Service**, a toggle on the script |
| Price on the call | **Never.** O4 always sends it in writing |
| Alert close (C4) | **Shown**, not held back |
| Art. 11 register check | **The salesperson**, reminded on every cold call |
| Language | **Greek by default, English on a toggle** |

## 3. Inputs: everything is already computed

The script is a VIEW over `crm_brief.build(c, uid)` plus a few signals about
the account. It runs no arithmetic of its own: every number it speaks is the
number the brief shows (test-enforced, §10).

### 3.1 Origin: how the contact reached us

| origin | Rule | Today |
|---|---|---|
| `self` | Signed up on /register | No marker yet: /register writes no `creation_source` |
| `contractor_db` | Imported from the contractor database | `customer_profile.creation_source = 'OrgDB'` (leads.py) |
| `external` | Anything else: created by an admin, typed in from a list, a fair, a referral | Admin creation (admin.py) writes no `creation_source` either |

Proposed (open decision §11.1):
- /register writes `creation_source = 'register'`, admin creation writes `'admin'`.
- Backfill for existing accounts: an account holding a `test` grant with
  `granted_by IS NULL` signed up on its own (register_submit grants exactly that;
  admins always set `granted_by`). Everything else not `OrgDB` → `external`.
- `lead_source` stays free text. The script shows it as context ("Πηγή: …")
  but never branches on it.

### 3.2 Basis: how much we know

Straight from `crm_brief.build()["basis"]`:

| basis | What we have | How the script may use it |
|---|---|---|
| `history` | ≥ 5 award acts in the ledger | **Statements** of fact |
| `history+kad` | A few awards + a ΚΑΔ estimate | History as statements, estimate as **questions** |
| `kad` | Only the ΚΑΔ estimate | **Questions** only |
| `none` | Nothing usable | Discovery questions only |

### 3.3 Account signals (read-only)

- `tender_experience` (NULL = never asked, NOT "no"), onboarding completed/skipped.
- `last_login_at`, number of saved searches, an alert on, any favourites.
- Trial: the current `test` grant and its `expires_at`.
- `onboarding.declared_afm`: a CLAIM. Usable only as a confirmation question,
  never as a fact or as the brief's ΑΦΜ (already test-enforced in kad_cpv).
- Earlier calls in `proc.customer_call`.
- ΓΕΜΗ status (`b.gemi.status`).
- Contact name: the main `customer_contact`, else `customer_profile.full_name`.

**The signals CHOOSE a branch; they are never spoken.** A script that says
"I saw you logged in twice and saved a search" sounds like surveillance.
Only things the customer knows we have may be said aloud: that they signed
up, when their trial ends, their public award record, their ΓΕΜΗ entry.

## 4. Why no model writes the script

- A salesperson reads the script aloud as fact. A model working from a page of
  statistics will sometimes round, swap or invent a figure. A fixed template
  with the real value in it cannot.
- Fixed branches can be measured (§8). Free text from a model cannot: no two
  calls would get the same opener.
- No cost, no wait, no new processor on /ai.

Same isolation as the brief: does not read `act_ai_summary`, imports no model
client (test-enforced).

## 5. The truth rules

The most important part of the design. Each source of a fact has ONE way it
may be used:

| Source | Use | Example |
|---|---|---|
| Award ledger (public) | Statement | «Από τα δημόσια στοιχεία, πέρσι κερδίσατε 14 αναθέσεις σε ιατρικά αναλώσιμα.» |
| ΚΑΔ estimate | Question | «Στο ΓΕΜΗ είστε με ΚΑΔ «Χονδρικό εμπόριο ιατρικών ειδών». Πουλάτε και αναλώσιμα σε νοσοκομεία;» |
| Declared ΑΦΜ | Confirmation | «Στην εγγραφή δώσατε ένα ΑΦΜ. Αντιστοιχεί στην εταιρεία [[company]];» |
| Nothing known | Open question | «Τι πουλάτε κυρίως;» |

Every block carries a kind: `say`, `ask` or `confirm`. Blocks fed by the
estimate can only be `ask`; blocks fed by `declared_afm` only `confirm`
(test-enforced, §10).

Also:
- «αναθέσεις», never «συμβάσεις» (n_awards counts decisions + contracts; same rule as fit).
- **No form of address is guessed from a name.** Greek needs the gender for
  «κύριε/κυρία»; the script uses the neutral «Μιλάω με [[contact_name]];».
  A missing name gives «Με ποιον μιλάω, παρακαλώ;».
- ΚΑΔ descriptions arrive in capitals («ΧΟΝΔΡΙΚΟ ΕΜΠΟΡΙΟ …»); the script puts
  them in sentence case so they can be read aloud.

## 6. Structure of a script

Six parts, always in this order. Each part has a variant per branch; a
question shows answer buttons that reveal the next line. That is the
"conditional" part.

1. **Stop banners** (before anything else, red):
   - ΓΕΜΗ status is not active (dissolved, in liquidation…): «Μην προχωρήσετε σε πώληση.»
   - The customer is a paying subscriber: this is not a sales call. Show a link to the card.
   - `do_not_call` is set (§8): «Ο πελάτης ζήτησε να μην τον ξανακαλέσουμε.» No script.
   - No phone number anywhere: the script still renders (for later), with a notice.
   - A held call already exists: «Δεν είναι η πρώτη κλήση: τελευταίο αποτέλεσμα …». Script shown, banner on top.
2. **Opener**, chosen by origin (§7.1).
3. **Hook**: ONE concrete fact, chosen by basis in a fixed order (§7.2).
4. **Discovery**: 3–4 questions, each with answer buttons (§7.3).
5. **Pitch**: the feature that answers what they said (follows from the answers).
6. **Close**: a next step, plus **objections** as a list beside it (§7.4–7.5).

Every branch also has «Μην πείτε» notes: things not to say on this kind of call.

## 7. Draft wording (Greek, the call language)

Tokens in `[[…]]` come from build() and the signals. A block whose tokens are
missing is not offered; the branch logic picks a different one. Unlike
digests._soft_resolve, an empty number never drops out of a sentence:
«κερδίσατε  αναθέσεις» must be impossible (test-enforced).

`[[brand]]` = Promitheies.gr or Tender Service, chosen on the script (§11.4).
`[[agent_name]]` = the logged-in admin's name; if missing, «(το όνομά σας)».

### 7.1 Opener

**self**
> Καλημέρα σας, [[agent_name]] από [[brand]]. Μιλάω με [[contact_name]];
> Σας καλώ επειδή κάνατε εγγραφή στην πλατφόρμα μας στις [[registered_on]]. Έχετε δύο λεπτά;

- Trial ends in ≤ 3 days, add: «Η δοκιμαστική σας πρόσβαση λήγει στις [[trial_ends]], γι' αυτό ήθελα να σας προλάβω.»
- `declared_afm` present and no company linked, add the confirm block of §5.

**contractor_db**
> Καλημέρα σας, [[agent_name]] από [[brand]]. Μιλάω με την εταιρεία [[company]];
> Παρακολουθούμε όλους τους δημόσιους διαγωνισμούς της χώρας, και από τα
> δημόσια στοιχεία βλέπω ότι συμμετέχετε σε διαγωνισμούς για [[top_group]].
> Έχετε δύο λεπτά να σας πω τι βλέπουμε ανοιχτό αυτή τη στιγμή για εσάς;

Μην πείτε: ονόματα ανταγωνιστών στην πρώτη κλήση. «Σας παρακολουθούμε».

**external**
> Καλημέρα σας, [[agent_name]] από [[brand]]. Μιλάω με την εταιρεία [[company]];
> Μια σύντομη ερώτηση: συμμετέχετε σε δημόσιους διαγωνισμούς;

[Ναι] → hook. [Όχι] → discovery Q5. [Δεν ξέρω / δεν είμαι αρμόδιος] → «Με ποιον θα μπορούσα να μιλήσω γι' αυτό;» (result: wrong person, §8).

### 7.2 Hook: the first available, in this order

| # | Needs | Kind | Wording |
|---|---|---|---|
| H1 | history, a good open tender closing ≤ 14 days | say | «Αυτή τη στιγμή υπάρχει ανοιχτός διαγωνισμός από [[top_authority]] για «[[top_title]]», που κλείνει στις [[top_deadline]]. Ταιριάζει σε όσα έχετε ήδη κερδίσει.» |
| H2 | history, n_good ≥ 1 | say | «Μετρήσαμε [[n_good]] ανοιχτούς διαγωνισμούς που ταιριάζουν με όσα έχετε κερδίσει, αξίας [[value_good]] συνολικά.» (+ «[[n_soon]] κλείνουν μέσα σε 14 ημέρες.» only when n_soon ≥ 1) |
| H3 | history, a top buyer | say | «Από τα δημόσια στοιχεία, η αναθέτουσα με την οποία δουλεύετε πιο συχνά είναι [[top_buyer]].» |
| H4 | estimate usable | ask | «Στο ΓΕΜΗ είστε με ΚΑΔ «[[kad_label]]». Πουλάτε και [[est_group]];» [Ναι] → «Τότε υπάρχουν [[n_good_kad]] ανοιχτοί διαγωνισμοί που ίσως σας ενδιαφέρουν.» [Όχι] → «Τι πουλάτε κυρίως;» (write it in the note: the admin corrects the profile) |
| H5 | nothing | ask | «Τι πουλάτε κυρίως, και σε ποιους πελάτες;» |

The hook never uses the competitor lists or the peer/leader lists: they load
lazily and take seconds, and naming rivals on a first call is §7.1's «Μην πείτε».

### 7.3 Discovery: chosen by basis and signals

| Q | When | Question | Answers → pitch |
|---|---|---|---|
| Q1 | always | «Πώς βρίσκετε σήμερα τους διαγωνισμούς που σας αφορούν;» | [Μόνοι μας, ΕΣΗΔΗΣ/ΚΗΜΔΗΣ] → αναζήτηση + ειδοποιήσεις · [Άλλη υπηρεσία] → objection O1 · [Σύμβουλος] → «ο σύμβουλος βλέπει ό,τι βλέπετε κι εσείς» · [Δεν ψάχνουμε συστηματικά] → ειδοποιήσεις |
| Q2 | always | «Σας έχει τύχει να δείτε έναν διαγωνισμό αργά ή να χάσετε προθεσμία;» | [Ναι] → ημερολόγιο + υπενθυμίσεις προθεσμίας · [Όχι] → next |
| Q3 | history | «Θέλετε να βρείτε νέους φορείς ή νέες περιοχές;» | [Ναι] → Ταίριασμα + δείκτης ανταγωνισμού · [Όχι] → next |
| Q4 | history+kad, kad | «Πόσοι άνθρωποι ασχολούνται με τις προσφορές;» | [1] → λίστα ελέγχου ανά διαγωνισμό · [Ομάδα] → αγαπημένα + στάδια προσφοράς |
| Q5 | none, or tender_experience = false | «Έχετε σκεφτεί να συμμετάσχετε σε δημόσιους διαγωνισμούς; Τι σας έχει κρατήσει μέχρι τώρα;» | [Δεν ξέρουμε από πού να ξεκινήσουμε] → γλωσσάρι + λίστα ελέγχου · [Δεν μας ενδιαφέρει] → polite close, result «Δεν ενδιαφέρεται» |

Each pitch line is one or two sentences naming ONE feature in the customer's
terms («κάθε πρωί σας έρχονται οι νέοι διαγωνισμοί για …»), never a feature list.

### 7.4 Close

| Close | When | Wording |
|---|---|---|
| C1 Demo | always | «Να κλείσουμε 20 λεπτά να σας δείξω την πλατφόρμα με τους δικούς σας διαγωνισμούς; Σας βολεύει [[slot]];» |
| C2 Trial | contractor_db / external (no access) | «Σας ανοίγω δοκιμαστική πρόσβαση. Θα χρειαστώ ένα email.» |
| C3 Extend | self, trial ending | «Μπορώ να σας επεκτείνω τη δοκιμή ώστε να τη δείτε με την ησυχία σας.» |
| C4 Alert | entitled, no alert on | «Μπορώ τώρα, όσο μιλάμε, να σας ρυθμίσω μια ειδοποίηση…» (§11.5) |

C2 with a contractor-database lead: that account was created with a random
password and maybe a generated `@prospective.com` address. Sign-in links are
OFF in production (render.yaml, until email deliverability is done; owner,
2026-10-08), so the note says: set the real email on «Στοιχεία», grant the
test product, issue a «προσωρινό συνθηματικό», and send the username + the
temporary password from «Σύνθεση email». The customer sets their own password
at the first sign-in. A test pins that every «name» a note quotes exists on
the card.

### 7.5 Objections (always visible beside the script)

| O | They say | Answer |
|---|---|---|
| O1 | «Έχουμε ήδη άλλη υπηρεσία» | «Ποια χρησιμοποιείτε; Τι θα θέλατε να κάνει καλύτερα;» Then ONE difference matching the answer |
| O2 | «Δεν έχω χρόνο τώρα» | «Κανένα πρόβλημα. Πότε να σας ξανακαλέσω;» → result Callback + a task with the date |
| O3 | «Στείλτε μου ένα email» | «Βεβαίως. Σε ποια διεύθυνση;» → result Send material, opens the card's Compose tab |
| O4 | «Πόσο κοστίζει;» | «Την τιμή θα σας τη στείλω γραπτώς, μαζί με την προσφορά…» Never a price on the call (§11.4) |
| O5 | «Πού βρήκατε το τηλέφωνό μου;» | **Truthful, per origin.** self: «Από την εγγραφή σας στην πλατφόρμα.» contractor_db: «Η εταιρεία σας εμφανίζεται σε δημόσιες αναθέσεις στο ΚΗΜΔΗΣ, και το τηλέφωνο είναι από [[phone_source]].» external: «Από [[lead_source]].» |
| O6 | «Μη με ξανακαλέσετε» | «Κατανοητό, σας ζητώ συγγνώμη για την ενόχληση. Δεν θα σας ξανακαλέσουμε.» No argument → result Do not call |

## 8. Recording the result

Buttons at the end of the dialog (card only; the print page has a paper box):

| Code | Label | Also |
|---|---|---|
| `no_answer` | Δεν απάντησε | call status `not_answered` |
| `wrong_person` | Λάθος αριθμός / όχι αρμόδιος | note box for the right contact |
| `callback` | Ξανακαλέστε | opens a date → `customer_task` |
| `not_interested` | Δεν ενδιαφέρεται | |
| `send_material` | Θέλει υλικό με email | jumps to Compose tab |
| `demo_booked` | Κλείστηκε παρουσίαση | date → `customer_task` |
| `trial_started` | Ξεκίνησε δοκιμή | |
| `do_not_call` | Να μην ξανακληθεί | sets `customer_profile.do_not_call_at` |

Each press writes ONE `proc.customer_call` row (direction outgoing, status
held / not_answered, `outcome` = the label so the existing Activity tab shows
it), plus three new columns:

- `script_version`: the wording version, bumped when the text changes.
- `script_branch`: e.g. `contractor_db|history|H1`, **frozen at call time**.
  The brief changes daily; a result must keep the branch it was given.
- `script_result`: one of the codes above (CHECK constraint).

Plus `customer_profile.do_not_call_at / do_not_call_by`. Set only by the
`do_not_call` result or by an admin on the card; cleared only by an admin.
It removes the script and shows the banner; it is never set automatically.

Slice 2 reads these columns into a small report: results per origin × basis ×
hook. Under 10 calls in a cell, nothing is shown (the same rule as the
competition indicator).

## 9. Surfaces

- **Card**: «Σενάριο κλήσης →» in the at-a-glance strip, next to «Σύνοψη
  πελάτη». Opens `#script-dlg`, which sits OUTSIDE the tab panels (the
  #brief-dlg lesson: a modal inside a display:none panel never shows). The
  dialog loads via HTMX from `/admin/crm/<uid>/script/panel` (build() takes
  seconds on a big supplier). Answer buttons are client-side only: they reveal
  the next line and write nothing.
- **Print page**: a plain GET `/admin/crm/<uid>/script`. EVERY branch is
  expanded (paper cannot click), with check boxes and a results box.
- Result POST: `/admin/crm/<uid>/script/result`, accepting only the codes of §8.
- Admin-only (everything under /admin). Computed per request, stored nowhere
  apart from §8's result rows.
- Later: the telephony caller-ID popup opens the dialog directly.

## 10. Code and tests

- `app/call_script.py`: `signals(c, uid)` (reads §3.1, §3.3) and a PURE
  `build(brief, signals, lang)` → the list of blocks. Pure, so most tests need
  no database.
- `app/call_script_text.py`: the wording, el + en, like glossary.py (it is
  content, not UI strings).
- Migration: `customer_call` + 3 columns, `customer_profile` +
  `do_not_call_at/_by`, the `creation_source` backfill of §3.1. Local AND
  Supabase, `migrate.py up --only <file>`, before the push. Regenerate
  tests/proc_schema.sql.

Tests (tests/test_call_script.py):
- Every origin × basis × signal combination renders, with no `[[` left and no
  empty number in a sentence.
- Every number in the script equals the brief's own for the same customer (no
  second arithmetic).
- Blocks fed by the estimate are only `ask`; blocks fed by `declared_afm`
  only `confirm`.
- Account signals never appear in spoken text (no login count, no search names).
- Stop banners: an inactive ΓΕΜΗ status, a subscriber and `do_not_call` show
  no sales script.
- O5 is present for every origin and names the right source.
- The result POST refuses unknown codes, freezes `script_branch`, 403s a
  non-admin; `do_not_call` sets the flag and only an admin clears it.
- The print page contains every branch; the dialog is outside the tab panels.
- el and en have the same keys.
- Isolation: no `act_ai_summary`, no model client import.
- `/register` writes `creation_source = 'register'`; the backfill rule
  classifies self-granted test accounts as `self`.

## 11. Decisions (owner, answered 2026-10-08)

1. Origin marker: **yes**, with the §3.1 backfill.
2. Wording: **in code first**; an editor in slice 2.
3. Result codes: **§8 as is**.
4. Brand: **Promitheies.gr or Tender Service** (a toggle on the script). **No
   price on the introductory call**: O4 always answers "in writing".
5. Alert close (C4): **shown**. Note: digests still lack SPF/DKIM/DMARC and
   unsubscribe, and EMAIL_BACKEND decides whether anything leaves at all.
6. Art. 11 register (Law 3471/2006): **the salesperson checks**. Every cold
   call (origin ≠ self) carries the reminder; the script records nothing.
7. Language: **Greek by default, English on a toggle**.

## 12. Slices

1. Script dialog + print page + result buttons + origin marker + do-not-call.
2. Results report per branch; wording editor.
3. Telephony popup; the call summary checks which questions were actually asked.
