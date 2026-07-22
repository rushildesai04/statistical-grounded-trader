"""Create all tables. Run once after configuring SGT_DATABASE_URL."""
from sgt.db.session import init_db

if __name__ == "__main__":
    init_db()
    print("DB initialized.")
