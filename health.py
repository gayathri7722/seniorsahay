"""Shows whether the server is set up. Never reveals any key."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _core import JsonHandler, load_db  # noqa: E402


class handler(JsonHandler):
    def do_GET(self):
        try:
            db = load_db()
            counts = {}
            ver = {}
            for s in db["schemes"]:
                counts[s["category"]] = counts.get(s["category"], 0) + 1
                ver[s["verification"]] = ver.get(s["verification"], 0) + 1
            info = {"schemes": len(db["schemes"]), "by_category": counts, "by_verification": ver}
        except Exception:
            info = {"schemes": 0, "error": "database_not_found"}
        self.send_json(200, {"ok": True, "gnani_key_configured": bool(os.environ.get("GNANI_API_KEY")),
                             "language_model_configured": bool(os.environ.get("ANTHROPIC_API_KEY")),
                             "database": info})
