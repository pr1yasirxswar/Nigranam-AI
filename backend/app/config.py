from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    database_url: str = "postgresql://sentinel_user:sentinel_pass@localhost:5432/sentinel_db"
    csv_path: str = "./data/mplads_synthetic_v5.csv"
    # Phase 12 item 1 -- local disk storage for live stage-submission
    # uploads (photos + documents). No new paid/third-party storage
    # service (Rules.md) -- plain local disk, same "hackathon-simple,
    # documented placeholder" pattern already used for auth (Rules.md,
    # models.py's User docstring).
    # DEPLOYMENT NOTE: on Render, local disk is wiped on every redeploy and
    # every restart unless you attach a paid persistent disk mounted at this
    # path. Fine for a demo; for real durability, either add a Render disk
    # (set UPLOAD_DIR to its mount path) or swap this for S3/GCS later.
    upload_dir: str = "./data/uploads"

    class Config:
        env_file = ".env"

settings = Settings()
