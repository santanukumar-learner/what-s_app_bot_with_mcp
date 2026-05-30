"""
db.py
=====
Database layer for the MULTI-USER version.

Holds:
  * the SQLAlchemy connection/session,
  * the relational tables (User, Document, Message),
  * helper functions to look up / create users by WhatsApp number and to
    log conversation history.

The embeddings themselves live in pgvector tables managed by LangChain's
PGVector store (see rag_multi.py) inside the SAME Postgres database.

Configure the connection in .env via DATABASE_URL, e.g.
    DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/whatsapp_bot
"""

import os
from datetime import datetime

from dotenv import load_dotenv
from sqlalchemy import (
    DateTime,
    ForeignKey,
    String,
    Text,
    create_engine,
    select,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
    sessionmaker,
)

load_dotenv()

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg://postgres:postgres@localhost:5432/whatsapp_bot",
)

# `future=True` engine; pool_pre_ping avoids stale connections.
engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class User(Base):
    """One row per end user, identified by their WhatsApp number."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    whatsapp_number: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    documents: Mapped[list["Document"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    messages: Mapped[list["Message"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Document(Base):
    """A source file that was ingested for a specific user."""

    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    num_chunks: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    user: Mapped["User"] = relationship(back_populates="documents")


class Message(Base):
    """Conversation history (one row per inbound/outbound message)."""

    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    role: Mapped[str] = mapped_column(String(16))  # "user" or "bot"
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    user: Mapped["User"] = relationship(back_populates="messages")


def init_db() -> None:
    """Create all tables (idempotent). Call once at startup / setup."""
    Base.metadata.create_all(engine)


# --- Helper functions ------------------------------------------------------

def normalize_number(number: str) -> str:
    """Strip 'whatsapp:' prefixes, spaces, and a leading '+' for consistency."""
    return number.replace("whatsapp:", "").replace(" ", "").lstrip("+").strip()


def get_or_create_user(whatsapp_number: str, name: str = "") -> User:
    """Return the user for this WhatsApp number, creating the row if needed."""
    number = normalize_number(whatsapp_number)
    with SessionLocal() as session:
        user = session.scalar(
            select(User).where(User.whatsapp_number == number)
        )
        if user is None:
            user = User(whatsapp_number=number, name=name)
            session.add(user)
            session.commit()
            session.refresh(user)
        return user


def get_user_by_number(whatsapp_number: str) -> User | None:
    """Return the user for this number, or None if not registered."""
    number = normalize_number(whatsapp_number)
    with SessionLocal() as session:
        return session.scalar(select(User).where(User.whatsapp_number == number))


def log_message(user_id: int, role: str, text: str) -> None:
    """Save one message to the conversation history."""
    with SessionLocal() as session:
        session.add(Message(user_id=user_id, role=role, text=text))
        session.commit()
