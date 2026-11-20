# Saans – Winter Smog Assistant for Families and Communities

Tell us your city and your household. Get today's plan, member by member, with the
reason and the math behind every suggestion, plus a ready-to-send message for your
building or school.

Works with no API key and no network (sample scenarios for Delhi, Lucknow, Pune);
uses live Open-Meteo data (no key needed) when online. English and Hindi.

## Chosen vertical

An urban family household (adults, children, elders, some with asthma/heart
conditions) in a smog-hit Indian city, with a community output (RWA / school
notice) generated from the same engine.

## Approach and logic

**Principle: the engine decides and computes; the model only rephrases.**

- `app/aqi.py` – CPCB National AQI: PM2.5/PM10 sub-indices (piecewise-linear over
  the official breakpoints), worst-of, band, NCR GRAP stage. Pure.
- `app/engine.py` – filters an action catalog (`app/data/actions.json`) by
  band x household x home x commute. Every action carries a rule id, a "why"
  (trigger, rule, inputs) and an impact. Pure and deterministic.
- `app/impact.py` – CO2e math only where a sourced formula exists (factors in
  `app/data/factors.json`, each with value, unit, source name, URL, year,
  uncertainty). Exposure effects are qualitative by design; nothing is invented.
- `app/air_data.py` – geocode + one live call per city (current + 48h hourly),
  15-minute TTL cache, scenario fallback, 3-hour staleness flag.
- `app/ai.py` – optional Gemini layer that answers questions *grounded on the
  engine JSON*. Output must pass **numguard** (every number must exist in the
  plan) and an action-ID whitelist; otherwise it falls back to deterministic
  templates. Without a key the app is fully functional via templates.

**Rule table** (each rule has tests in `tests/test_engine.py`):

| Rule | Condition → action |
| --- | --- |
| R1 Ventilation | Band ≥ Poor: keep windows shut except the forecast 3-hour minimum-PM2.5 block (computed, with local clock times). Band ≤ Satisfactory: cross-ventilate. |
| R2 CO safety override | Unvented combustion indoors (angithi/charcoal/unvented heater): never seal the room; stop it and ventilate, whatever the AQI. Overrides R1. |
| R3 Purifier | Owns one: run high while cooking and evenings in band ≥ Poor. None: DIY box-fan + MERV-13 (Corsi-Rosenthal) in the sleeping room. |
| R4 Cooking | Always exhaust/chimney or brief window in the best block. Biomass: LPG/PNG swap first. Gas: induction swap with honest CO2e math (see below). |
| R5 Commute | Walk/cycle in band ≥ Very Poor: shift to metro/bus or off-peak. Two-wheeler: fitted N95/FFP2 (not cloth/surgical). Car: carpool/metro swap with CO2e; recirculate cabin air. |
| R6 Children | Band ≥ Very Poor: indoor play; ask school to move PE/assembly indoors. No masks for children aged 5 and under (WHO guidance). |
| R7 Elders/sensitive | Band ≥ Moderate: skip early-morning walks; prefer the best block; keep medicine/inhaler plan; symptoms → doctor. |
| R8 Exercise | Band ≥ Poor: move indoors; otherwise the best block. |
| R9 Heating | Never burn waste/biomass for warmth; layer up; efficient electric heating. |
| R10 Community | Band ≥ Poor: generate the RWA/school notice with the N-households total; in cleaner air no notice is generated. |
| R11 Bad data | Data older than 3 h (local-time aware) shown with a warning; upstream down, garbage, or no network: labelled sample scenario fallback (any city) or manual PM entry in the UI; unreachable data returns 503, never 500. |

**Sensitive shift:** if any member is flagged sensitive, the effective band is
shifted one step worse for the whole plan (stated as an assumption below).

**Honest math note (induction):** on today's Indian grid (CEA v22.0,
0.675 kg CO2e/kWh, FY 2025-26), cooking a meal with induction emits **more**
CO2e than LPG (2.939 kg CO2e/kg at 40% stove efficiency vs 0.75 kg CO2e per
delivered kWh at 90% induction efficiency). The app shows this negative result
with its formula rather than hiding it; the induction recommendation is an
indoor-air (combustion pollutant) play, not a CO2e play.

## How the solution works

```
profile + city
  -> air_data.py   live PM2.5/PM10 + hourly forecast (Open-Meteo, no key), cache, offline scenarios
  -> aqi.py        CPCB AQI, band, GRAP stage (pure)
  -> engine.py     filter catalog by band x household x home x commute (pure) -> actions with `why`
  -> impact.py     CO2e (sourced factors) + qualitative exposure effect (pure)
  -> ai.py         optional Gemini, grounded on the engine JSON; numguard + whitelist; template fallback
  -> static UI     EN/HI, member cards, "Why this?", "Show the math", community card
```

Request flow: the browser posts `{city, profile}` to `POST /api/plan`; the server
resolves air data (cache → live → sample scenario), builds the plan with
community message, and returns JSON. The UI renders it with `textContent` only.
`POST /api/ask` re-derives the same plan server-side and answers a question
about it (template or guarded Gemini). `POST /api/plan/manual` accepts a manual
PM2.5/PM10 reading (R11).

The AI's only role: rephrase and answer questions about the plan. It cannot add
an action or a number: guards reject any reply containing numbers absent from
the engine output or action ids absent from the catalog, and fall back to
deterministic templates (adversarially tested in `tests/test_ai.py`).

## Assumptions

- **Sensitive shift**: a sensitive member shifts the effective band one step
  worse (max Severe). This is a design choice, not a published guideline.
- **Factors are proxies**: vehicle/rail/LPG factors are UK DEFRA 2024 (no
  India-specific official per-km factors exist); the grid factor is CEA v22.0
  (FY 2025-26, CO2 only). Every factor carries its source, year and uncertainty
  in `app/data/factors.json`; a test fails if any field is missing.
- **CPCB breakpoints and GRAP stages** as published (citations inside
  `app/data/aqi_bands.json`): CPCB 2014 AQI report; CAQM revised GRAP schedule
  (29.09.2026) – Stage I 201-300, II 301-400, III 401-450, IV >450.
- **GRAP is shown only for NCR cities** (district list from the 2021 Regional
  Plan, in `aqi_bands.json`).
- **Sample scenarios** (Delhi severe, Lucknow very poor, Pune satisfactory) are
  realistic synthetic winter profiles, clearly labelled "sample", for offline
  evaluation; they are not live measurements.
- **Rounding**: sub-index interpolation rounds to the nearest integer (CPCB
  publishes integer AQIs without a rounding rule).
- **Not medical advice**: symptom guidance always defers to "see a doctor".
- Masks: only fitted N95/FFP2-class; the app says plainly that cloth and
  surgical masks do little against PM2.5. No masks for ages 5 and under (WHO).

## Run

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload   # http://127.0.0.1:8000
```

Optional: `cp .env.example .env` and set `GEMINI_API_KEY` for the AI answers
layer. Without it, everything works with deterministic template answers.

## Test

```bash
pip install -r requirements.txt
python -m pytest --cov=app --cov-report=term
```

Measured on the final code (Python 3.12): **205 tests, 93% coverage** (aqi 95,
air_data 95, engine 97, impact 98, main 98, models 96, ai 81 – the uncovered ai
lines are the real-Gemini call path, exercised only with a live key).

Measured latency (single uvicorn worker, localhost, `scripts/bench_latency.py`
methodology; 200 samples for the engine, 15 for cached, 4 for first-hit):

| Path | Median | p95 |
| --- | --- | --- |
| `build_plan` (pure engine) | 0.2 ms | 0.2 ms |
| `POST /api/plan` (cache hit) | 7.7 ms | 8.8 ms |
| `POST /api/plan` (first hit, live fetch) | ~980 ms | ~2060 ms (4 samples) |

## Security

- Pydantic validation on every input: enums for modes/fuels, numeric ranges,
  members ≤ 8, city ≤ 80 chars with a character whitelist, question ≤ 300 chars.
- Output rendered with `textContent` only; no `innerHTML` in the frontend.
- The AI guards are syntactic guardrails, not a full content filter: numguard
  blocks numbers absent from the plan and the action guard blocks invented
  action ids and imperative "buy/move to" advice, but a paraphrase attack can
  still slip advice past them; the template fallback is always safe.
- Headers on every response: `Content-Security-Policy: default-src 'self'`,
  `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`,
  `X-Frame-Options: DENY`, `Cache-Control: no-store`. No CORS.
- Fixed-window rate limit (20 req/min per IP) on `/api/plan` and `/api/ask`;
  unresolvable upstreams return 503 with a clear message, never a stack trace.
- httpx timeouts (8 s) on upstream calls; upstream failure falls back to sample
  scenarios rather than retry loops.
- Secrets only from environment; `.env` is gitignored; `.env.example` documents
  the single optional key. `git log -p` scanned for secret patterns: clean.
- No profile data is stored or logged; plans are computed per request and
  discarded.
- Prompt-injection containment: the user question is wrapped in `<<DATA>>`
  delimiters as data, the model gets no tools, and its reply must pass numguard
  and the action-id whitelist (adversarial tests included).
- `pip-audit` on pinned requirements: no known vulnerabilities.

## Accessibility

Checked with a headless browser (accessibility tree + 360px viewport) against
the running app:

- Skip link, landmarks (header/main/footer/regions), heading hierarchy.
- A label or aria-label for every input; errors use `role="alert"`.
- Focus moves to the results heading after the plan renders; updates use
  `aria-live="polite"`.
- AQI band shown as icon + text (○ ◔ ◑ ◕ ● ◉), never colour alone.
- Large-text toggle; `lang` attribute switches with the EN/HI toggle.
- Native `<details>` for "Show the math"; print-friendly community message.
- Contrast ≥ 4.5:1 on all text colours (computed per WCAG formula, including the six band chips); touch targets ≥ 44px; full keyboard use;
  `prefers-reduced-motion` respected; no horizontal scroll at 360px.

Known gap: a formal axe/Lighthouse audit report is not included because this
sandbox could not run those tools end-to-end; the list above was verified
manually against the running app (reproducible in any browser).

## Limitations

- Air data is one model grid point per city (Open-Meteo/CAMS), not a station
  average; CPCB bulletin values can differ. Manual entry (`/api/plan/manual`, also in the UI) is offered when the
  data is stale or absent (R11).
- CO2e factors are UK proxies for vehicles and rail (no official Indian per-km
  factors); uncertainties are printed with every number.
- The forecast best-block uses model hourly PM2.5; real indoor-outdoor
  dynamics depend on the building.
- Exposure effects are qualitative ("lower", "much lower") – no invented
  percentages.
- The app does not diagnose or treat; it defers symptoms to a doctor.
- PM2.5 above 250 µg/m³ / PM10 above 430 µg/m³ map straight to AQI 500 (the
  CPCB scale has no published extrapolation above the top breakpoint).
- If any household uses unvented combustion (R2), the close-windows advice
  (R1) is suppressed entirely rather than contradicted.
- Single-process rate limiter and cache (in-memory) – fine for a demo, not a
  multi-worker deployment.
- Hindi translations are machine-drafted; proofreading by a native speaker is
  welcome (see `app/data/strings.json` and `actions.json`).

---

Sources for the numbers (also embedded in the data files with URLs and years):
CPCB National AQI report (2014) and AQI Hub transcription; CAQM revised GRAP
schedule (29.09.2026); CEA CO2 Baseline Database v22.0 (FY 2025-26); UK
DEFRA/DESNZ GHG conversion factors 2024 (condensed set); ACEEE Summer Study
2014 (induction vs gas cooking efficiency); WHO "Children and masks" Q&A
(2022); DMRC carbon-lite metro page (TERI study figure, for context).
