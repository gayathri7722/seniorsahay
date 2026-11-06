# SeniorSahay

A voice-first assistant that helps people in India discover government schemes from their real situation, in Telugu or English.
Say or type your situation → it asks only the missing questions → it shows matching schemes, the documents needed, how to apply and the
official website → it reads everything aloud.

**Not a government website.** Information only. Final eligibility and approval are decided by the relevant government authority.

## How it works
1. `public/index.html` – the page: language picker, big microphone, text box, follow-up questions, scheme cards, read-aloud.
2. `api/stt.py` – sends the recording to **Gnani Prisma** (speech-to-text). The key stays on the server.
3. `api/analyze.py` – understands the situation (keywords in Telugu/English; an optional language model if `ANTHROPIC_API_KEY` is set),
   asks the next missing question, runs the eligibility rules.
4. `api/_core.py` – the engine. **Plain Python rules decide eligibility; no AI decides it.**
5. `api/_data/schemes.json` – the 25-scheme database. Add schemes here (or in `tools/build_schemes.py`) without touching code.
6. `api/tts.py` – reads text aloud with **Gnani Timbre**. `api/health.py` – shows whether the server is set up (never shows keys).

Nothing is stored on the server. The conversation lives in the browser tab. Voice recordings are not saved.

## Deploy (Vercel + GitHub)
1. Upload this folder's files to your GitHub repo (same paths replace the old files; keep the folder names `api`, `public`, `tests`, `tools`).
2. Vercel → your project → Settings → Environment Variables:
   - `GNANI_API_KEY` (required)
   - optional: `TTS_VOICE_TE` (default `Lavanya`), `TTS_VOICE_EN`, `TTS_MODEL` (default `timbre-v2.5`), `STT_MODEL` (default `prisma-v3.0`)
   - optional: `ANTHROPIC_API_KEY` (better understanding of unusual wording; eligibility is still decided by the Python rules)
3. Redeploy after any change to a variable. Never commit a `.env` file.
4. Open `https://YOUR-SITE/api/health`. It must show `"gnani_key_configured": true` and 25 schemes.

## Test
- Engine tests (no key needed): `pip install pytest` then `python -m pytest tests -q`
- Live voice test: see "Proving Prisma and Timbre" below.

## Proving Prisma and Timbre
- The page shows a "Voice check" line after each real call: model name and response time. It appears only if the API call succeeded.
- Put a wrong key in Vercel and redeploy: speech and microphone must fail with a clear message (there is no browser-voice fallback).
- Open browser DevTools → Network: `/api/tts` returns `audio/wav` with header `X-Provider: gnani-timbre`; `/api/stt` returns a transcript.
- Compare your credits on app.gnani.ai before and after.

## Known limits
- Only 1 scheme is marked **verified** and 2 **partial**; 22 are **unverified** (written from general knowledge). See `verification_report.md`. Check each on its official page before relying on it.
- Benefit amounts are shown only where seen; many say "to be confirmed".
- Telugu text was written by an AI and must be checked by a Telugu reader.
- English read-aloud needs an English voice: set `TTS_VOICE_EN` from the Gnani voice catalog, otherwise the Telugu voice is used for English text.
- Rate limiting is per server instance (best effort).
- Eligibility rules are simplified (for example income limits are reduced to "low income"). The official site decides.
