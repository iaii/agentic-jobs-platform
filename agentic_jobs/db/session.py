from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from agentic_jobs.config import settings


engine = create_engine(
    settings.sqlalchemy_database_uri,
    future=True,
    echo=settings.debug,
)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
    future=True,
)

Base = declarative_base()


def get_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
