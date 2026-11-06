"""Text to speech with Gnani Timbre. Returns WAV audio. The API key stays on the server."""
import os
import sys
import time

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _core import JsonHandler  # noqa: E402

URL = "https://api.vachana.ai/api/v1/tts/inference"


class handler(JsonHandler):
    bucket = "tts"
    limit = 40
    max_body = 4000

    def do_GET(self):
        self.send_json(200, {"ok": True, "service": "tts", "key_configured": bool(os.environ.get("GNANI_API_KEY"))})

    def do_POST(self):
        if not self.guard():
            return
        key = os.environ.get("GNANI_API_KEY")
        if not key:
            return self.send_json(500, {"error": "missing_key"})
        try:
            data = self.read_json()
            text = data["text"]
            lang = data.get("lang", "te")
            if not isinstance(text, str) or not text.strip() or lang not in ("te", "en"):
                raise ValueError("input")
            text = text.strip()[:500]
        except Exception:
            return self.send_json(400, {"error": "bad_request"})
        te_voice = os.environ.get("TTS_VOICE_TE") or os.environ.get("TTS_VOICE", "Lavanya")
        voice = te_voice if lang == "te" else (os.environ.get("TTS_VOICE_EN") or te_voice)
        model = os.environ.get("TTS_MODEL", "timbre-v2.5")
        payload = {"text": text, "voice": voice, "model": model,
                   "language": "te-IN" if lang == "te" else "en-IN", "speed": 1.0,
                   "audio_config": {"sample_rate": 48000, "num_channels": 1, "sample_width": 2,
                                    "encoding": "linear_pcm", "container": "wav"}}
        t0 = time.time()
        try:
            r = requests.post(URL, headers={"X-API-Key-ID": key, "Content-Type": "application/json"},
                              json=payload, timeout=25)
        except requests.RequestException:
            return self.send_json(504, {"error": 504})
        if r.status_code != 200:
            return self.send_json(r.status_code, {"error": r.status_code})
        self.send_bytes(200, r.content, "audio/wav",
                        {"X-Provider": "gnani-timbre", "X-Model": model, "X-Elapsed-Ms": str(int((time.time() - t0) * 1000))})
