"""Kept for compatibility: POST a profile, get scheme results. The main endpoint is /api/analyze."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _core import JsonHandler, run_turn  # noqa: E402


class handler(JsonHandler):
    bucket = "match"
    limit = 40

    def do_GET(self):
        self.send_json(200, {"ok": True, "service": "match", "method": "POST"})

    def do_POST(self):
        if not self.guard():
            return
        try:
            body = self.read_json()
            out = run_turn(body.get("profile", body), "", None, False, body.get("lang"))
        except Exception:
            return self.send_json(400, {"error": "bad_request"})
        self.send_json(200, {"results": out["results"], "profile": out["profile"]})
