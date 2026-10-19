"""Dependency injection (corresponds to @Autowired)."""
from sqlalchemy.orm import Session
from core.db import get_db


def get_db_session() -> Session:
    """Yield a database session for request scope."""
    yield from get_db()
