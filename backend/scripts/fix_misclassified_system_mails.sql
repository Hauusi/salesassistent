-- Corrects the two Google system notifications that were misclassified as
-- typ='ANFRAGE' before the classification.py fix (see commit that added
-- the automated-sender heuristic and the sharpened system prompt):
--   - "Google Play Datenschutzeinstellungen Update"
--   - "Google-Konto Aktivierungsbestätigung"
--
-- Adjust the `subject ILIKE` patterns in _fix_target below if your actual
-- subjects differ, or add more (e.g. a Microsoft example) the same way.
--
-- Run against the real database, e.g.:
--   psql "$DATABASE_URL" -f backend/scripts/fix_misclassified_system_mails.sql
-- (strip the asyncpg driver prefix from DATABASE_URL if needed - psql wants
-- postgresql://, not postgresql+asyncpg://).
--
-- Safe to run more than once: each step's WHERE clause only ever matches
-- rows still in the wrong state.

BEGIN;

-- Step 1: pin down exactly which emails this run will touch, so the
-- UPDATE/DELETE below share one fixed target list instead of three
-- separately-evaluated (and potentially drifting) WHERE clauses.
CREATE TEMP TABLE _fix_target AS
SELECT id, case_id
FROM email_messages
WHERE typ = 'ANFRAGE'
  AND (
      subject ILIKE '%Google Play Datenschutzeinstellungen%'
      OR subject ILIKE '%Google-Konto Aktivierungsbestätigung%'
  );

-- Sanity check: inspect this before trusting the COMMIT at the bottom.
-- If this is not exactly the rows you expect, ROLLBACK instead and adjust
-- the WHERE clause above.
SELECT em.id, em.case_id, em.subject, em.sender_address, em.wichtigkeits_kategorie, em.typ
FROM email_messages em
JOIN _fix_target t ON t.id = em.id;

-- Step 2: correct the classification itself.
UPDATE email_messages
SET
    typ = 'KEINER',
    wichtigkeits_kategorie = 'INFORMATION',
    status = 'ABGELEGT',
    classification_reasoning = 'Manuell korrigiert: automatisierte No-Reply-Systembenachrichtigung, keine Kundenanfrage.'
WHERE id IN (SELECT id FROM _fix_target);

-- Step 3: a draft may already have been generated off the wrong
-- classification (antwort_erforderlich -> wartet_auf_freigabe). Reject any
-- that hasn't been sent yet - a sent one cannot be undone, only prevented
-- from happening again (which the classification.py fix already does).
UPDATE drafts
SET
    status = 'ABGELEHNT',
    rejected_reason = 'Automatisch korrigiert: Quelle war eine System-/Sicherheitsmail, keine echte Anfrage.'
WHERE email_message_id IN (SELECT id FROM _fix_target)
  AND status IN ('ENTWURF', 'FREIGEGEBEN');

-- Step 4: a Case is created for every new mail thread regardless of typ
-- (see app/services/pipeline.py::_get_or_create_case), so these system
-- notifications got their own Case with deal_stage=ANFRAGE too - that is
-- the "existing Cases in the pipeline" from the request. Delete it,
-- rather than relabeling it GEWONNEN/VERLOREN (those are reserved for a
-- human's real sales-outcome decision, not "this was never a deal") - but
-- only if the case consists *entirely* of mails this run just corrected;
-- never touch a case that also holds genuine correspondence.
DELETE FROM cases
WHERE id IN (SELECT case_id FROM _fix_target WHERE case_id IS NOT NULL)
  AND NOT EXISTS (
      SELECT 1 FROM email_messages em
      WHERE em.case_id = cases.id
        AND em.id NOT IN (SELECT id FROM _fix_target)
  );

COMMIT;
