# Sales-Assistent — Mail-Assistent (MVP, Modul 1)

Erstes Modul der geplanten Sales-Assistent-SaaS-Plattform: ein Mail-Assistent
für Gmail, der eingehende Mails automatisch klassifiziert, ablegt oder
Antwortentwürfe erstellt — Versand immer erst nach expliziter menschlicher
Freigabe.

## Ablauf im Überblick

```
Gmail-Postfach --(Polling)--> EmailMessage
                                  |
                                  v
                     Klassifikation (Claude, tool-use)
                     Wichtigkeit x Typ (orthogonal)
                                  |
                                  v
                     Case-Zuordnung (Voyage-Embeddings + pgvector)
                                  |
              +-------------------+-------------------+----------------+
              v                   v                   v                v
        antwort_erforderlich  information          newsletter     spam_verdacht
        -> Entwurf (RAG)      -> abgelegt,          -> Label,      -> ausgeblendet,
        -> wartet_auf_freigabe   durchsuchbar           fertig        nicht gelöscht
              |
              v
        Freigabe-Dashboard (Next.js)
        bearbeiten / freigeben (-> Versand über Gmail API) / ablehnen
```

Jede automatisierte Entscheidung und jede Freigabe/Ablehnung landet im
`ActionLog` (Audit-Trail).

## Stack

- **Backend**: Python, FastAPI (async), SQLAlchemy 2.0, Alembic
- **Datenbank**: PostgreSQL + `pgvector` (Case-Matching/RAG-Embeddings)
- **Queue**: Redis + RQ (Polling-Worker + Scheduler-Prozess)
- **Frontend**: Next.js (App Router, TypeScript) — Freigabe-Dashboard
- **LLM**: Anthropic Claude (Klassifikation, Entwurfserstellung)
- **Embeddings**: Voyage AI (von Anthropic empfohlener Embedding-Partner —
  Claude selbst bietet keine Embeddings-API)
- **Mail-Zugriff**: Gmail API via OAuth2 (kein Passwort-Storage)

## Repo-Layout

```
backend/    FastAPI-App, SQLAlchemy-Modelle, Alembic-Migrationen, RQ-Worker, Tests
frontend/   Next.js Freigabe-Dashboard
docker-compose.yml   Postgres(pgvector) + Redis + backend + worker + scheduler + frontend
```

## Voraussetzungen

- Docker & Docker Compose (empfohlen), **oder** lokal: Python 3.11+,
  PostgreSQL 16 mit `pgvector`-Extension, Redis, Node.js 20+
- Ein Google-Cloud-Projekt mit aktivierter Gmail API und einem
  OAuth2-Client (Web Application)
- Ein Anthropic-API-Key
- Ein Voyage-AI-API-Key ([voyageai.com](https://www.voyageai.com))

## 1. Gmail-OAuth einrichten

1. In der [Google Cloud Console](https://console.cloud.google.com/) ein
   Projekt anlegen (oder ein bestehendes verwenden).
2. **APIs & Dienste → Bibliothek**: "Gmail API" aktivieren.
3. **APIs & Dienste → OAuth-Zustimmungsbildschirm**: Typ "Extern" (oder
   "Intern" bei Google Workspace), Testnutzer hinzufügen (die
   Gmail-Adresse, die du verbinden willst, solange die App im
   Testing-Status ist).
4. **APIs & Dienste → Anmeldedaten → Anmeldedaten erstellen → OAuth-Client-ID**,
   Typ "Webanwendung". Als autorisierte Redirect-URI eintragen:
   `http://localhost:8000/api/auth/gmail/callback`
5. Client-ID und Client-Secret in `backend/.env` eintragen (siehe unten).

Benötigte Scopes (bereits in `app/config.py` vorkonfiguriert):
`gmail.readonly`, `gmail.send`, `gmail.modify`, `userinfo.email`.

## 2. Backend einrichten

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt   # inkl. Test-Abhängigkeiten
cp .env.example .env
```

`.env` ausfüllen:

- `DATABASE_URL` — Standard passt zu `docker-compose.yml`
- `CORS_ALLOWED_ORIGINS` — nur für Deployments nötig. Leer gelassen, erlaubt
  die API `FRONTEND_BASE_URL` **und** dessen `localhost`/`127.0.0.1`-Variante;
  das sind für den Browser verschiedene Origins, und nur eine davon zu
  erlauben macht die App auf der anderen komplett funktionsunfähig.
- `TOKEN_ENCRYPTION_KEY` — generieren mit:
  ```bash
  python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
  ```
- `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` — aus Schritt 1
- `ANTHROPIC_API_KEY`
- `VOYAGE_API_KEY`

### Datenbank starten & migrieren

Mit Docker Compose (Postgres+pgvector, Redis):

```bash
cd ..
docker compose up -d postgres redis
```

Migrationen anwenden:

```bash
cd backend
alembic upgrade head
```

### Backend, Worker, Scheduler lokal starten

Drei Prozesse (je eigenes Terminal, `.venv` aktiviert):

```bash
uvicorn app.main:app --reload --port 8000       # API
python -m app.workers.run_worker                # RQ-Worker (verarbeitet Mails)
python -m app.workers.run_scheduler              # Polling-Trigger, alle 60s (konfigurierbar)
```

Health-Check: `curl http://localhost:8000/api/health`

## 3. Frontend einrichten

```bash
cd frontend
npm install
cp .env.local.example .env.local   # NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
npm run dev
```

Dashboard: [http://localhost:3000](http://localhost:3000)

## 4. Alles mit Docker Compose

```bash
cp backend/.env.example backend/.env      # ausfüllen, siehe oben
docker compose up --build
```

Startet Postgres, Redis, Backend, Worker, Scheduler und Frontend zusammen.
Die Migrationen laufen automatisch: ein eigener `migrate`-Service führt
`alembic upgrade head` aus, und Backend, Worker und Scheduler starten erst,
wenn er erfolgreich durchgelaufen ist.

Die API-URL des Frontends wird zur **Build**-Zeit ins Bundle geschrieben
(so funktioniert `NEXT_PUBLIC_*`), nicht zur Laufzeit gelesen. Für ein
anderes Deployment:

```bash
NEXT_PUBLIC_API_BASE_URL=https://api.example.com docker compose build frontend
```

## 5. End-to-End durchspielen (Abnahmekriterium)

1. Dashboard öffnen → **Postfach verbinden** → "Mit Gmail verbinden" →
   Google-Consent durchlaufen.
2. Testmail an das verbundene Postfach senden.
3. Im Connect-Screen bei Bedarf **"Jetzt abrufen"** klicken (statt auf den
   nächsten Scheduler-Tick zu warten).
4. **Posteingang** öffnen → Mail erscheint mit Kategorie
   (`antwort_erforderlich` / `information` / `newsletter` /
   `spam_verdacht`) und ggf. Typ (`bestellung`/`anfrage`).
5. Bei `antwort_erforderlich`: unter **Freigaben** den generierten Entwurf
   öffnen, bearbeiten, **"Freigeben & senden"** klicken.
6. Die freigegebene Antwort wird über die Gmail API tatsächlich versendet
   (Thread-Antwort mit korrektem `In-Reply-To`).

## Tests

```bash
cd backend
source .venv/bin/activate
createdb salesassistent_test   # einmalig - die DB selbst muss existieren
pytest
```

Die Tests laufen gegen eine echte Postgres+pgvector-Datenbank (Case-Matching
ist eine SQL/pgvector-Abfrage — ein DB-loser Test würde die eigentliche
Logik nicht prüfen). Die `pgvector`-Extension wird von der `_schema`-Fixture
(`tests/conftest.py`) selbst per `CREATE EXTENSION IF NOT EXISTS vector`
aktiviert, dafür ist kein manueller Schritt mehr nötig — vorausgesetzt, das
verwendete Postgres-Image/-Paket enthält `pgvector` überhaupt (bei
`docker-compose.yml` der Fall: `pgvector/pgvector:pg16`; bei einer
System-Postgres-Installation ggf. das `postgresql-<version>-pgvector`-Paket
nachinstallieren). Anthropic-Aufrufe sind durchgängig gemockt
(`tests/mocks.py`), es werden keine echten LLM-Calls ausgeführt. Abgedeckt:

- `test_classification.py` — Mapping LLM-Tool-Antwort → `ClassificationResult`,
  insbesondere die Unterscheidung `bestellung` vs. `anfrage`
- `test_case_matching.py` — Schwellenwert-Verhalten, Lookback-Fenster,
  Mehrkontakt-Cases (tenant-weiter Fallback)
- `test_pipeline.py` — volle Aktionslogik pro Kategorie (5.3), Idempotenz

## Offene Punkte / bewusste Vereinfachungen (MVP-Scope)

- **Case-Ähnlichkeitsschwelle**: Das Konzept nennt keinen konkreten
  Schwellenwert für die semantische Case-Zuordnung. `CASE_SIMILARITY_THRESHOLD`
  (Default `0.78`, Cosinus-Ähnlichkeit) und `CASE_LOOKBACK_DAYS` (Default
  `180`) sind daher dokumentierte, konfigurierbare Defaults in
  `backend/.env.example` — kein aus dem Konzept abgeleiteter Wert. Die
  Heuristik selbst (erst Absender-Historie, dann mandantenweiter
  Fallback für Mehrkontakt-Cases) ist in `app/services/case_matching.py`
  dokumentiert.
- **Kein Login/Multi-User-Auth**: Das MVP läuft mit einem einzelnen
  Default-Tenant/-User (siehe `app/services/tenant_bootstrap.py`); die
  Datenmodell-Mandantentrennung (`tenant_id` auf jeder Tabelle) ist bereits
  vorhanden, ein echtes Auth-System ist nicht Teil dieses Scopes.
- **Verschlüsselung at rest**: OAuth-Tokens sind mit Fernet
  (symmetrisch, Schlüssel aus `TOKEN_ENCRYPTION_KEY`) verschlüsselt
  gespeichert. Volldatenbank-Verschlüsselung (z. B. transparente
  Postgres-Verschlüsselung) ist Infrastruktur-/Deployment-Scope, nicht
  Teil dieser Session.
- **Gmail Push/Pub-Sub**: Nicht implementiert — Polling reicht für den
  MVP-Start (Intervall über `MAIL_POLL_INTERVAL_SECONDS`). Der
  Umstieg auf Push würde primär `app/workers/` betreffen.
- **Anhänge**: Metadaten (Dateiname, Typ, Größe, Gmail-Attachment-ID)
  werden beim Verarbeiten in `attachments` gespeichert; der Anhang-Inhalt
  selbst wird noch nicht in einem Objektspeicher abgelegt
  (`Attachment.storage_path` ist dafür vorbereitet).
- **Vorbereitete, aber noch nicht befüllte Felder**: `Case.summary` (eine
  Case-Zusammenfassung würde einen weiteren LLM-Aufruf bedeuten) und
  `CaseStatus.GESCHLOSSEN` (es gibt noch keinen Endpunkt, der einen Case
  schließt). Beide sind im Datenmodell und in der API vorhanden, damit sie
  ohne Schema- und Vertragsänderung nachgezogen werden können.
- **Volltextsuche-Sprache**: `PRODUCT_SEARCH_TEXT_CONFIG` (Default
  `german`) gilt deploymentweit, nicht pro Mandant. Der GIN-Index in
  `alembic/versions/a1b2c3d4e5f6_*` ist für diesen Wert gebaut; eine
  andere Konfiguration funktioniert weiterhin, fällt aber auf einen
  sequentiellen Scan zurück, bis ein passender Index angelegt wird.
- **Nicht gebaut (laut Auftrag bewusst out of scope)**: automatischer
  Versand ohne Freigabe, automatisches Löschen, Outlook/IMAP, vollautomatische
  Angebotserstellung, Follow-up-Reminder/Digest, Mehrsprachigkeits-Logik.

## Demo-Daten (Durchklicken ohne Gmail)

Legt Kontakte, Cases, je eine Mail pro Kategorie, einen Produktkatalog und
einen offenen Entwurf an — ohne Gmail-Verbindung und ohne einen einzigen
Token zu verbrauchen. Betrifft nur den Tenant `demo`, echte Daten daneben
bleiben unberührt.

```bash
cd backend
python -m scripts.seed_demo            # anlegen
python -m scripts.seed_demo --reset    # vorher löschen, dann anlegen
```

Danach die API mit `DEFAULT_TENANT_SLUG=demo` starten, um die Daten im
Dashboard zu sehen.

## Qualitätssicherung

```bash
cd backend
ruff check app tests scripts     # Linting
pytest -q                        # Testsuite (braucht Postgres+pgvector, siehe unten)

cd ../frontend
npm run typecheck                # tsc --noEmit
npm run lint
npm run generate:api-types       # TS-Typen aus dem OpenAPI-Dokument des Backends
```

Beides läuft in CI (`.github/workflows/ci.yml`), inklusive eines Jobs, der
fehlschlägt, wenn die committeten API-Typen nicht mehr zum Backend passen.

Die TypeScript-Typen in `frontend/lib/api-schema.ts` sind **generiert** —
nicht von Hand bearbeiten. Nach einer Änderung an einem Response-Schema
`npm run generate:api-types` ausführen und das Ergebnis mitcommitten.

## Sicherheit

- OAuth-Tokens: verschlüsselt (Fernet), nie im Klartext.
- Keine Zugangsdaten im Code — alles über `.env` / Umgebungsvariablen,
  `.env.example` als Referenz gepflegt.
- Jede automatisierte Aktion (Klassifikation, Entwurf, Ablage) sowie jede
  Freigabe/Ablehnung wird im `ActionLog` protokolliert (`entity_type`,
  `entity_id`, `actor`, `action`, `detail`).
- Mandantentrennung (`tenant_id`) auf jeder tenant-eigenen Tabelle, auch
  wenn im MVP nur ein Tenant existiert.
