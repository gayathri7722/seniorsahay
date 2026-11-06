"""Speech to text with Gnani Prisma. The API key stays on the server."""
import base64
import json
import os
import sys
import time

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _core import JsonHandler  # noqa: E402

URL = "https://api.vachana.ai/stt/v3"
LANGS = {"te-IN,en-IN", "en-IN,te-IN", "te-IN", "en-IN"}
MAX_AUDIO = 1_500_000  # bytes after decoding: about 45 s of 16 kHz mono WAV, API limit is 30 s


class handler(JsonHandler):
    bucket = "stt"
    limit = 15
    max_body = 2_200_000

    def do_GET(self):
        self.send_json(200, {"ok": True, "service": "stt", "key_configured": bool(os.environ.get("GNANI_API_KEY"))})

    def do_POST(self):
        if not self.guard():
            return
        key = os.environ.get("GNANI_API_KEY")
        if not key:
            return self.send_json(500, {"error": "missing_key"})
        try:
            data = self.read_json()
            audio = base64.b64decode(data["audio"], validate=True)
            lang = data.get("language", "te-IN,en-IN")
            if lang not in LANGS or not (1000 < len(audio) <= MAX_AUDIO) or audio[:4] != b"RIFF":
                raise ValueError("input")
        except Exception:
            return self.send_json(400, {"error": "bad_request"})
        model = os.environ.get("STT_MODEL", "prisma-v3.0")
        t0 = time.time()
        try:
            r = requests.post(URL, headers={"X-API-Key-ID": key},
                              files={"audio_file": ("audio.wav", audio, "audio/wav")},
                              data={"model": model, "language_code": lang, "format": "transcribe"}, timeout=25)
        except requests.RequestException:
            return self.send_json(504, {"error": 504})
        try:
            body = r.json()
        except ValueError:
            body = {}
        if r.status_code != 200 or body.get("success") is False:
            code = r.status_code if r.status_code != 200 else 502
            msg = body.get("message") if isinstance(body.get("message"), str) else ""
            return self.send_json(code, {"error": code, "message": msg[:200]})
        self.send_json(200, {"success": True, "transcript": str(body.get("transcript", ""))[:1000],
                             "provider": "gnani-prisma", "model": model, "ms": int((time.time() - t0) * 1000)})
