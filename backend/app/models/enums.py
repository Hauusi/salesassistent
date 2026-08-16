"""Enum types used across the domain model.

Values are the German business terms from the concept scope so that DB
content, logs and API payloads stay consistent with the terminology the
product/user thinks in.
"""
from __future__ import annotations

import enum


class WichtigkeitsKategorie(str, enum.Enum):
    ANTWORT_ERFORDERLICH = "antwort_erforderlich"
    INFORMATION = "information"
    NEWSLETTER = "newsletter"
    SPAM_VERDACHT = "spam_verdacht"


class TypKategorie(str, enum.Enum):
    BESTELLUNG = "bestellung"
    ANFRAGE = "anfrage"
    KEINER = "keiner"


class EmailStatus(str, enum.Enum):
    NEU = "neu"
    ABGELEGT = "abgelegt"
    WARTET_AUF_FREIGABE = "wartet_auf_freigabe"
    ERLEDIGT = "erledigt"
    AUSGEBLENDET = "ausgeblendet"  # spam_verdacht: hidden from default view, never deleted


class DraftStatus(str, enum.Enum):
    ENTWURF = "entwurf"
    FREIGEGEBEN = "freigegeben"
    ABGELEHNT = "abgelehnt"
    VERSENDET = "versendet"


class CaseStatus(str, enum.Enum):
    OFFEN = "offen"
    GESCHLOSSEN = "geschlossen"


class ActionActor(str, enum.Enum):
    SYSTEM = "system"
    USER = "user"


class MailboxProvider(str, enum.Enum):
    GMAIL = "gmail"
