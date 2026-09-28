from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker, declarative_base
from app.config import settings


def _build_engine_url_and_args(raw_url: str):
    """Normalises DATABASE_URL for hosted Postgres (Supabase).

    - Some providers hand out `postgres://`, which SQLAlchemy 1.4+ rejects;
      rewritten to `postgresql://`.
    - Supabase requires TLS; `sslmode=require` is added for any non-local
      host unless the URL already specifies an sslmode.
    """
    url = make_url(raw_url.strip())
        if url.drivername.split("+")[0] in ("postgres", "postgresql"):
        url = url.set(drivername="postgresql+psycopg2")
    connect_args = {}
    is_local = (url.host or "") in ("localhost", "127.0.0.1", "db", "")
    if not is_local and "sslmode" not in (url.query or {}):
        connect_args["sslmode"] = "require"
    return url, connect_args


_url, _connect_args = _build_engine_url_and_args(settings.database_url)

# pool_pre_ping: Supabase's pooler and Render's free tier both drop idle
# connections; without this the first request after idle raises
# OperationalError. pool_recycle keeps connections younger than the
# pooler's idle timeout. Pool is kept small -- Supabase pooler has a low
# client-connection cap and this app runs 3 background loops + requests.
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
