"""Fills the database with demo data for clicking through the UI.

Lets you exercise every screen without connecting a real Gmail account or
spending a single token: one mail per category, a case spanning two
contacts, a product catalog, and an open draft waiting for approval.

    python -m scripts.seed_demo            # add demo data
    python -m scripts.seed_demo --reset    # delete it first, then add

Only ever touches the demo tenant (slug: DEMO_TENANT_SLUG below), so it
cannot disturb real data sitting next to it. It writes no mailbox tokens,
so the poll worker will never try to talk to Gmail on its behalf.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select

from app.db import async_session_factory, engine
from app.logging_config import configure_logging
from app.models.action_log import ActionLog
from app.models.attachment import Attachment
from app.models.case import Case, CaseContact
from app.models.contact import Contact
from app.models.draft import Draft
from app.models.email_message import EmailMessage
from app.models.enums import (
    ActionActor,
    CaseStatus,
    DraftStatus,
    EmailStatus,
    TypKategorie,
    WichtigkeitsKategorie,
)
from app.models.mailbox import Mailbox
from app.models.product import Product
from app.models.tenant import Tenant
from app.models.user import User

logger = logging.getLogger("seed")

DEMO_TENANT_SLUG = "demo"

# The embedding column is NOT NULL-able but semantically meaningless here:
# nothing in the UI reads it, and case assignment is set explicitly below
# rather than computed. A constant vector avoids needing a Voyage key just
# to look at the dashboard.
_PLACEHOLDER_EMBEDDING = [0.0] * 1024


def _minutes_ago(minutes: int) -> datetime:
    return datetime.now(UTC) - timedelta(minutes=minutes)


async def _reset(db) -> None:
    """Removes everything belonging to the demo tenant."""
    tenant = (
        await db.execute(select(Tenant).where(Tenant.slug == DEMO_TENANT_SLUG))
    ).scalar_one_or_none()
    if tenant is None:
        return

    # Ordered by dependency; the FKs cascade, but being explicit keeps this
    # readable and independent of cascade configuration.
    await db.execute(delete(ActionLog).where(ActionLog.tenant_id == tenant.id))
    await db.execute(delete(Attachment).where(Attachment.tenant_id == tenant.id))
    await db.execute(delete(Draft).where(Draft.tenant_id == tenant.id))
    await db.execute(delete(EmailMessage).where(EmailMessage.tenant_id == tenant.id))
    case_ids = (
        await db.execute(select(Case.id).where(Case.tenant_id == tenant.id))
    ).scalars().all()
    if case_ids:
        await db.execute(delete(CaseContact).where(CaseContact.case_id.in_(case_ids)))
    await db.execute(delete(Case).where(Case.tenant_id == tenant.id))
    await db.execute(delete(Contact).where(Contact.tenant_id == tenant.id))
    await db.execute(delete(Product).where(Product.tenant_id == tenant.id))
    await db.execute(delete(Mailbox).where(Mailbox.tenant_id == tenant.id))
    await db.execute(delete(User).where(User.tenant_id == tenant.id))
    await db.execute(delete(Tenant).where(Tenant.id == tenant.id))
    await db.commit()
    logger.info("Demo-Daten entfernt.")


async def _seed(db) -> dict:
    tenant = (
        await db.execute(select(Tenant).where(Tenant.slug == DEMO_TENANT_SLUG))
    ).scalar_one_or_none()
    if tenant is not None:
        logger.warning(
            "Demo-Tenant existiert bereits - mit --reset neu aufsetzen. Nichts geändert."
        )
        return {}

    tenant = Tenant(name="Demo GmbH", slug=DEMO_TENANT_SLUG)
    db.add(tenant)
    await db.flush()

    user = User(tenant_id=tenant.id, email="vertrieb@demo-gmbh.de", name="Sabine Vertrieb")
    db.add(user)
    await db.flush()

    # No tokens: the poll worker skips a mailbox it cannot authenticate,
    # so this never triggers an outbound Gmail call.
    mailbox = Mailbox(
        tenant_id=tenant.id,
        user_id=user.id,
        email_address="vertrieb@demo-gmbh.de",
        is_active=False,
        last_synced_at=_minutes_ago(12),
    )
    db.add(mailbox)
    await db.flush()

    julia = Contact(
        tenant_id=tenant.id, email_address="j.bauer@musterkunde.de",
        name="Julia Bauer", company="Musterkunde AG",
    )
    thomas = Contact(
        tenant_id=tenant.id, email_address="t.klein@musterkunde.de",
        name="Thomas Klein", company="Musterkunde AG",
    )
    buchhaltung = Contact(
        tenant_id=tenant.id, email_address="buchhaltung@lieferant.de", name="Lieferant Buchhaltung"
    )
    for contact in (julia, thomas, buchhaltung):
        db.add(contact)
    await db.flush()

    # A case with two contacts at the same customer - the multi-contact
    # shape the case model exists for.
    angebots_case = Case(
        tenant_id=tenant.id,
        title="Angebotsanfrage Aluminiumprofile",
        status=CaseStatus.OFFEN,
    )
    db.add(angebots_case)
    await db.flush()
    db.add(CaseContact(case_id=angebots_case.id, contact_id=julia.id))
    db.add(CaseContact(case_id=angebots_case.id, contact_id=thomas.id))

    ablage_case = Case(tenant_id=tenant.id, title="Rechnungen Lieferant", status=CaseStatus.OFFEN)
    db.add(ablage_case)
    await db.flush()
    db.add(CaseContact(case_id=ablage_case.id, contact_id=buchhaltung.id))
    await db.flush()

    for product in (
        Product(
            tenant_id=tenant.id, name="Aluminiumprofil 40x40", category="Profile", sku="ALU-4040",
            price=12.50, currency="EUR", availability="3-5 Werktage",
            description="Strangpressprofil, eloxiert, Nut 8.",
            specs={"Gewicht": "1.6 kg/m", "Material": "EN AW-6060", "Nut": "8 mm"},
        ),
        Product(
            tenant_id=tenant.id, name="Aluminiumprofil 80x40", category="Profile", sku="ALU-8040",
            price=23.90, currency="EUR", availability="3-5 Werktage",
            description="Schwereres Strangpressprofil für tragende Konstruktionen.",
            specs={"Gewicht": "3.1 kg/m", "Material": "EN AW-6060"},
        ),
        Product(
            tenant_id=tenant.id, name="Nutenstein M8", category="Verbindungselemente",
            sku="NST-M8", price=0.65, currency="EUR", availability="ab Lager",
            description="Nutenstein für Nut 8, verzinkt.", specs={"Gewinde": "M8"},
        ),
        Product(
            tenant_id=tenant.id, name="Winkelverbinder 40", category="Verbindungselemente",
            sku="WKL-40", price=4.20, currency="EUR", availability="ab Lager",
            description="Verbindungswinkel inkl. Schrauben und Nutensteinen.", specs={},
        ),
    ):
        db.add(product)

    def _mail(**kwargs) -> EmailMessage:
        base = {
            "tenant_id": tenant.id,
            "mailbox_id": mailbox.id,
            "embedding": _PLACEHOLDER_EMBEDDING,
            "typ": TypKategorie.KEINER,
            "processed_at": kwargs.get("received_at"),
        }
        return EmailMessage(**{**base, **kwargs})

    # One mail per category, so every filter in the inbox has something.
    anfrage = _mail(
        contact_id=julia.id, case_id=angebots_case.id,
        gmail_message_id="demo-anfrage", gmail_thread_id="demo-thread-1",
        rfc822_message_id="<demo-anfrage@musterkunde.de>",
        subject="Angebotsanfrage Aluminiumprofile 40x40",
        sender_address=julia.email_address, sender_name=julia.name,
        raw_content=(
            "Guten Tag,\n\n"
            "für ein Projekt benötigen wir 200 Aluminiumprofile 40x40 sowie passende "
            "Nutensteine M8.\n\n"
            "Können Sie uns bitte ein Angebot inklusive Lieferzeit zusenden?\n\n"
            "Viele Grüße\nJulia Bauer\nMusterkunde AG"
        ),
        snippet="Für ein Projekt benötigen wir 200 Aluminiumprofile 40x40 …",
        wichtigkeits_kategorie=WichtigkeitsKategorie.ANTWORT_ERFORDERLICH,
        typ=TypKategorie.ANFRAGE,
        classification_confidence=0.94,
        classification_reasoning="Konkrete Angebotsanfrage mit Mengenangabe",
        status=EmailStatus.WARTET_AUF_FREIGABE,
        received_at=_minutes_ago(35),
    )
    bestellung = _mail(
        contact_id=thomas.id, case_id=angebots_case.id,
        gmail_message_id="demo-bestellung", gmail_thread_id="demo-thread-1",
        rfc822_message_id="<demo-bestellung@musterkunde.de>",
        subject="AW: Angebotsanfrage - wir bestellen",
        sender_address=thomas.email_address, sender_name=thomas.name,
        raw_content=(
            "Hallo,\n\nwir bestellen wie besprochen 200 Stück ALU-4040.\n"
            "Lieferadresse wie immer.\n\nBeste Grüße\nThomas Klein"
        ),
        snippet="Wir bestellen wie besprochen 200 Stück ALU-4040.",
        wichtigkeits_kategorie=WichtigkeitsKategorie.ANTWORT_ERFORDERLICH,
        typ=TypKategorie.BESTELLUNG,
        classification_confidence=0.91,
        classification_reasoning="Verbindliche Bestellung",
        status=EmailStatus.WARTET_AUF_FREIGABE,
        received_at=_minutes_ago(20),
    )
    information = _mail(
        contact_id=buchhaltung.id, case_id=ablage_case.id,
        gmail_message_id="demo-info", gmail_thread_id="demo-thread-2",
        rfc822_message_id="<demo-info@lieferant.de>",
        subject="Rechnung 2026-4711",
        sender_address=buchhaltung.email_address, sender_name=buchhaltung.name,
        raw_content=(
            "Sehr geehrte Damen und Herren,\n\nanbei erhalten Sie die Rechnung 2026-4711 "
            "über 2.480,00 EUR.\n\nMit freundlichen Grüßen\nBuchhaltung"
        ),
        snippet="Anbei erhalten Sie die Rechnung 2026-4711 über 2.480,00 EUR.",
        wichtigkeits_kategorie=WichtigkeitsKategorie.INFORMATION,
        classification_confidence=0.88,
        classification_reasoning="Reine Mitteilung, ablagewürdig",
        status=EmailStatus.ABGELEGT,
        received_at=_minutes_ago(180),
    )
    newsletter = _mail(
        contact_id=None, case_id=None,
        gmail_message_id="demo-newsletter", gmail_thread_id="demo-thread-3",
        subject="Unsere Neuheiten im August",
        sender_address="newsletter@grosshandel.example", sender_name="Großhandel",
        raw_content="Viele neue Artikel im Sortiment.\n\nHier abmelden.",
        snippet="Viele neue Artikel im Sortiment.",
        wichtigkeits_kategorie=WichtigkeitsKategorie.NEWSLETTER,
        classification_confidence=0.97,
        classification_reasoning=(
            "Regelbasiert erkannt: List-Unsubscribe-Header vorhanden, kein Claude-Aufruf."
        ),
        status=EmailStatus.ERLEDIGT,
        received_at=_minutes_ago(300),
    )
    spam = _mail(
        contact_id=None, case_id=None,
        gmail_message_id="demo-spam", gmail_thread_id="demo-thread-4",
        subject="Ihr Konto wurde gesperrt - jetzt handeln",
        sender_address="security@paypa1-verify.example", sender_name="Sicherheit",
        raw_content="Ihr Konto wurde gesperrt. Bestätigen Sie sofort Ihre Zugangsdaten.",
        snippet="Ihr Konto wurde gesperrt. Bestätigen Sie sofort Ihre Zugangsdaten.",
        wichtigkeits_kategorie=WichtigkeitsKategorie.SPAM_VERDACHT,
        classification_confidence=0.96,
        classification_reasoning="Phishing-Muster, gefälschte Absenderdomain",
        status=EmailStatus.AUSGEBLENDET,
        received_at=_minutes_ago(400),
    )
    for mail in (anfrage, bestellung, information, newsletter, spam):
        db.add(mail)
    await db.flush()

    db.add(
        Attachment(
            tenant_id=tenant.id, email_message_id=information.id, filename="rechnung-4711.pdf",
            content_type="application/pdf", size_bytes=84213, gmail_attachment_id="demo-att-1",
        )
    )

    # One open draft and one already sent, so both states are visible.
    offener_draft = Draft(
        tenant_id=tenant.id, email_message_id=anfrage.id,
        subject="Re: Angebotsanfrage Aluminiumprofile 40x40",
        body=(
            "Guten Tag Frau Bauer,\n\n"
            "vielen Dank für Ihre Anfrage. Gerne unterbreiten wir Ihnen folgendes Angebot:\n\n"
            "- 200 x Aluminiumprofil 40x40 (ALU-4040): 12,50 EUR/Stück, lieferbar in 3-5 Werktagen\n"
            "- Nutenstein M8 (NST-M8): 0,65 EUR/Stück, ab Lager verfügbar\n\n"
            "Die benötigte Menge der Nutensteine ist noch zu prüfen - nennen Sie uns gerne "
            "die gewünschte Stückzahl.\n\n"
            "Mit freundlichen Grüßen\nSabine Vertrieb"
        ),
        status=DraftStatus.ENTWURF,
        rag_context_summary=(
            "1 vorherige Mail(s) als Kontext verwendet. Produkte aus der Wissensbasis "
            "herangezogen: Aluminiumprofil 40x40, Nutenstein M8."
        ),
    )
    db.add(offener_draft)

    versendeter_draft = Draft(
        tenant_id=tenant.id, email_message_id=bestellung.id,
        subject="Re: AW: Angebotsanfrage - wir bestellen",
        body="Guten Tag Herr Klein,\n\nvielen Dank für Ihre Bestellung. Wir bestätigen …",
        status=DraftStatus.VERSENDET,
        rag_context_summary="2 vorherige Mail(s) als Kontext verwendet.",
        approved_by_user_id=user.id,
        approved_at=_minutes_ago(15),
        sent_at=_minutes_ago(15),
        gmail_sent_message_id="demo-sent-1",
    )
    db.add(versendeter_draft)
    await db.flush()

    for entity_id, action, detail in (
        (anfrage.id, "classified", {"wichtigkeits_kategorie": "antwort_erforderlich", "typ": "anfrage"}),
        (anfrage.id, "case_created", {"case_id": str(angebots_case.id)}),
        (offener_draft.id, "draft_generated", {"email_message_id": str(anfrage.id)}),
        (versendeter_draft.id, "draft_sent", {"gmail_sent_message_id": "demo-sent-1"}),
    ):
        db.add(
            ActionLog(
                tenant_id=tenant.id, actor=ActionActor.SYSTEM,
                entity_type="demo", entity_id=entity_id, action=action, detail=detail,
            )
        )

    await db.commit()
    return {
        "tenant": tenant.slug,
        "mails": 5,
        "cases": 2,
        "produkte": 4,
        "offene_entwuerfe": 1,
    }


async def main_async(reset: bool) -> None:
    async with async_session_factory() as db:
        if reset:
            await _reset(db)
        summary = await _seed(db)

    if summary:
        logger.info(
            "Demo-Daten angelegt: %s Mails, %s Cases, %s Produkte, %s offener Entwurf "
            "(Tenant '%s').",
            summary["mails"], summary["cases"], summary["produkte"],
            summary["offene_entwuerfe"], summary["tenant"],
        )
        logger.info("Hinweis: Die Standard-Ansicht zeigt den Tenant aus DEFAULT_TENANT_SLUG.")
        logger.info("Zum Anschauen: DEFAULT_TENANT_SLUG=%s setzen und die API neu starten.", DEMO_TENANT_SLUG)
    await engine.dispose()


def main() -> None:
    configure_logging()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--reset", action="store_true", help="Vorhandene Demo-Daten vorher löschen."
    )
    args = parser.parse_args()
    asyncio.run(main_async(reset=args.reset))


if __name__ == "__main__":
    main()
