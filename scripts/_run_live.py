"""Start the live demo server: fresh DB, seeded, port 5217."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

db_dir = tempfile.mkdtemp(prefix="er-live-")
os.environ["DATABASE_PATH"] = os.path.join(db_dir, "live.db")
os.environ["FLASK_SECRET_KEY"] = "vis2"
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

from app import create_app
from app.seed import seed

app = create_app()
with app.app_context():
    seed()

from app.db import query_one

with app.app_context():
    print("DB:", os.environ["DATABASE_PATH"])
    print("admins:", [r["username"] for r in __import__("app.db", fromlist=["query"]).query(
        "SELECT username FROM admins ORDER BY id")])

app.run(host="127.0.0.1", port=5217, debug=False, use_reloader=False)
