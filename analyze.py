"""Understands the person's situation, asks only the missing questions, returns matching schemes."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _core import JsonHandler, run_turn  # noqa: E402


class handler(JsonHandler):
    bucket = "analyze"
    limit = 40
    max_body = 20000

    def do_GET(self):
        self.send_json(200, {"ok": True, "service": "analyze", "method": "POST"})

    def do_POST(self):
        if not self.guard():
            return
        try:
            body = self.read_json()
            text = body.get("text", "")
            if not isinstance(text, str):
                raise ValueError("text")
            text = text.strip()[:600]
            out = run_turn(body.get("profile"), text, body.get("asking"), bool(body.get("skip")), body.get("lang"))
        except Exception:
            return self.send_json(400, {"error": "bad_request"})
        self.send_json(200, out)
