from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker, declarative_base
from app.config import settings


def _build_engine_url_and_args(raw_url: str):
    url = make_url(raw_url.strip())
    if url.drivername.split("+")[0] in ("postgres", "postgresql"):
        url = url.set(drivername="postgresql+psycopg2")
    # Bug fix: psycopg2 has no connect timeout by default, so if Supabase is
    # paused/unreachable or the pooler host is misconfigured, the very first
    # DB call in on_startup() (Base.metadata.create_all / seed_users) hangs
    # forever instead of failing -- the server never reports "ready" and
    # Render eventually times the whole deploy out. 10s is generous for a
    # normal connection but still far short of Render's deploy timeout.
    connect_args = {"connect_timeout": 10}
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
