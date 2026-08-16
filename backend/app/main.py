from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import auth, cases, drafts, emails, knowledge, mailboxes
from app.config import get_settings

settings = get_settings()

app = FastAPI(title="Sales-Assistent - Mail-Modul", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_base_url],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(mailboxes.router)
app.include_router(drafts.router)
app.include_router(emails.router)
app.include_router(cases.router)
app.include_router(knowledge.router)


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok"}
