from app.database import engine
from sqlalchemy import text

with engine.connect() as conn:
    conn.execute(text("ALTER TABLE reviews ADD COLUMN recipient_role VARCHAR;"))
    conn.commit()

print("Done — recipient_role column added.")