"""Example emails used across the classification/pipeline tests.

Each fixture is a plain dict with the fields classify_email() /
process_incoming_email() consume, plus an `expected` block describing
what a correct classification should look like - used to build the
canned mock LLM responses in the tests, not to call a real model.
"""
from __future__ import annotations

ANFRAGE_MAIL = {
    "subject": "Anfrage: Angebot für 200 Stück Aluminiumprofile",
    "sender_address": "einkauf@musterkunde.de",
    "sender_name": "Julia Bauer",
    "body": (
        "Sehr geehrte Damen und Herren,\n\n"
        "wir planen ein neues Projekt und benötigen ein Angebot für 200 Stück "
        "Aluminiumprofile Typ AP-40, Länge 3m. Könnten Sie uns Preise und "
        "Lieferzeit nennen?\n\nMit freundlichen Grüßen\nJulia Bauer"
    ),
    "expected": {
        "wichtigkeits_kategorie": "antwort_erforderlich",
        "typ": "anfrage",
    },
}

BESTELLUNG_MAIL = {
    "subject": "Bestellung Nr. 4711",
    "sender_address": "bestellungen@handwerk-schmidt.de",
    "sender_name": "Thomas Schmidt",
    "body": (
        "Hallo,\n\nhiermit bestellen wir verbindlich 50 Stück Winkelverbinder WV-12 "
        "gemäß Ihrem Angebot vom 03.08.2026. Lieferadresse wie gehabt.\n\nGruß, "
        "Thomas Schmidt"
    ),
    "expected": {
        "wichtigkeits_kategorie": "antwort_erforderlich",
        "typ": "bestellung",
    },
}

INFORMATION_MAIL = {
    "subject": "Versandbestätigung Ihrer Bestellung #4711",
    "sender_address": "versand@spedition-nord.de",
    "sender_name": None,
    "body": (
        "Ihre Sendung mit der Trackingnummer DE0012345678 wurde heute versendet "
        "und trifft voraussichtlich am 20.08.2026 ein. Keine Antwort erforderlich."
    ),
    "expected": {
        "wichtigkeits_kategorie": "information",
        "typ": "keiner",
    },
}

NEWSLETTER_MAIL = {
    "subject": "Unser August-Newsletter: Neue Produkte & Aktionen",
    "sender_address": "newsletter@industrie-magazin.de",
    "sender_name": "Industrie Magazin",
    "body": (
        "Liebe Leserinnen und Leser,\n\nin dieser Ausgabe: Trends in der "
        "Fertigungstechnik, neue Whitepapers und unsere Herbst-Messetermine. "
        "Abmelden können Sie sich jederzeit über den Link unten."
    ),
    "expected": {
        "wichtigkeits_kategorie": "newsletter",
        "typ": "keiner",
    },
}

SPAM_MAIL = {
    "subject": "URGENT: Verify your account now or lose access!!!",
    "sender_address": "security-alert@paypa1-secure.ru",
    "sender_name": "PayPal Security",
    "body": (
        "Dear user, we detected unusual activity. Click here immediately and "
        "enter your login credentials and credit card number to avoid account "
        "suspension within 24 hours: http://paypa1-secure.ru/verify"
    ),
    "expected": {
        "wichtigkeits_kategorie": "spam_verdacht",
        "typ": "keiner",
    },
}

ALL_FIXTURES = [ANFRAGE_MAIL, BESTELLUNG_MAIL, INFORMATION_MAIL, NEWSLETTER_MAIL, SPAM_MAIL]
