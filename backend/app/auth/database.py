from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker, declarative_base
from app.config import settings


def _build_engine_url_and_args(raw_url: str):
    url = make_url(raw_url.strip())
    if url.drivername.split("+")[0] in ("postgres", "postgresql"):
        url = url.set(drivername="postgresql+psycopg2")
    connect_args = {}
    is_local = (url.host or "") in ("localhost", "127.0.0.1", "db", "")
    if not is_local and "sslmode" not in (url.query or {}):
        connect_args["sslmode"] = "require"
    return url, connect_args


_url, _connect_args = _build_engine_url_and_args(settings.database_url)

engine = create_engine(
    _url,
    connect_args=_connect_args,
    pool_pre_ping=True,
    pool_recycle=300,
    pool_size=5,
    max_overflow=5,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
