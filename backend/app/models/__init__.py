"""Import all models so Alembic autogenerate and Base.metadata see them."""
from app.models.action_log import ActionLog
from app.models.attachment import Attachment
from app.models.case import Case, CaseContact
from app.models.contact import Contact
from app.models.draft import Draft
from app.models.email_message import EmailMessage
from app.models.mailbox import Mailbox
from app.models.product import Product
from app.models.tenant import Tenant
from app.models.user import User

__all__ = [
    "ActionLog",
    "Attachment",
    "Case",
    "CaseContact",
    "Contact",
    "Draft",
    "EmailMessage",
    "Mailbox",
    "Product",
    "Tenant",
    "User",
]
