# oc-telemetry — shared interaction telemetry for OpenConverters

A tiny FastAPI receiver that records **"what the user adds or touches, event
type"** across the three OpenConverters sites (Kelvin, Kirchhoff, Heaviside) and
mirrors the OpenMagnetics telemetry model:

- **Same DB, another schema.** Writes to the existing managed Postgres
  (`OM_DB_*`, `195.154.71.72:16265/OpenMagnetics`) under a **dedicated
  `openconverters_telemetry` schema** — never `public` / `telemetry` /
  `heaviside_telemetry` / `umami`. The schema + `sessions`/`events` tables are
  **auto-created on the first event** (like `heaviside/api/telemetry.py`), so
  there is no manual DDL step.
- **Umami on top.** Product analytics (pageviews + the same events, lightweight)
  go to the **existing OM Umami** instance, reused by registering three new
  websites. Served same-origin under `/stats` on each site (adblock-resistant).

Kelvin and Kirchhoff are static WASM SPAs with no backend, so this service is
their event sink. Heaviside also posts its *frontend interaction* events here;
its server-side *job* telemetry stays in `heaviside_telemetry` (untouched).

## Architecture

```
browser (kelvin/kirchhoff/heaviside.openconverters.com)
  │  navigator.sendBeacon('/telemetry', {site, session_id, event_type, target, props})
  ▼  (same-origin — nginx location /telemetry)
nginx  ──proxy──▶  127.0.0.1:8787  (this service, systemd: oc-telemetry)
                          │  INSERT
                          ▼
             Postgres  openconverters_telemetry.{sessions,events}

browser  ──▶  /stats/script.js  ──proxy──▶  127.0.0.1:3001 (existing OM Umami) ──▶ umami schema
```

## Files

| file | purpose |
|------|---------|
| `app.py` | FastAPI: `POST /telemetry` (204, fire-and-forget), `GET /telemetry/health` |
| `db.py` | Postgres layer — auto-creates `openconverters_telemetry`, upserts session + appends event; silent on failure |
| `oc-telemetry.service` | systemd unit (uvicorn on `127.0.0.1:8787`, env from `/etc/oc-telemetry.env`) |
| `nginx-telemetry.snippet` | `location /telemetry` + `location /stats/` blocks to `include` per vhost |
| `deploy.sh` | rsync + venv + env file + systemd + nginx snippet + health check |

## Event contract (what the browser POSTs)

```json
{
  "site": "kelvin | kirchhoff | heaviside",
  "session_id": "<tab-scoped uuid>",
  "event_type": "drawer_open | recommend_run | topology_select | solve | export | design_submit | ...",
  "target": "the thing touched — an MPN, a topology, a filter column …",
  "props": { "family": "mosfet", "topology": "buck", "...": "small metadata only" },
  "environment": "production",
  "app_version": null
}
```

The shared frontend helper is `telemetry.js` in each repo (`initTelemetry()` +
`trackEvent()`), production-gated to `*.openconverters.com` so dev never reports.

## Schema

```sql
CREATE SCHEMA IF NOT EXISTS openconverters_telemetry;

CREATE TABLE openconverters_telemetry.sessions (
  session_id  TEXT PRIMARY KEY,
  site        TEXT NOT NULL,
  environment TEXT NOT NULL DEFAULT 'production',
  app_version TEXT,
  user_agent  TEXT,
  first_seen  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  last_seen   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE openconverters_telemetry.events (
  id          BIGSERIAL PRIMARY KEY,
  occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  site        TEXT NOT NULL,
  session_id  TEXT NOT NULL,
  event_type  TEXT NOT NULL,
  target      TEXT,
  props       JSONB,
  environment TEXT NOT NULL DEFAULT 'production',
  app_version TEXT
);
```

(You do **not** run this by hand — `db.py` creates it. It is here for reference.)

## Deploy

```bash
# From a shell with OM_DB_* exported (source ~/.bashrc):
./deploy.sh
```

Then, on the box, add one line to each site's **443** vhost `server { }` block and
reload nginx:

```nginx
include /etc/nginx/snippets/oc-telemetry.conf;
```

- **Kelvin**: already carried inline in `Kelvin/web/scripts/nginx-kelvin.conf`
  (the `/telemetry` + `/stats` blocks) — its own deploy ships them, so the
  `include` is optional there.
- **Kirchhoff** & **Heaviside**: add the `include` to their existing vhosts
  (Heaviside already proxies `/stats`; it just needs `/telemetry`).

`nginx -t && systemctl reload nginx` after editing.

## Umami websites (reuse the existing OM instance)

Heaviside is already registered (website-id `2e9c5afa-bf1f-41ee-949f-62fa9e0639f5`,
injected by `App.vue`). Kelvin and Kirchhoff need websites created:

1. Log into the OM Umami dashboard (`https://openmagnetics.com/stats`, admin).
2. Settings → Websites → **Add website** twice:
   - name `Kelvin`, domain `kelvin.openconverters.com`
   - name `Kirchhoff`, domain `kirchhoff.openconverters.com`
3. Copy each **Website ID** (UUID) and set it as `UMAMI_WEBSITE_ID` in:
   - `Kelvin/web/src/main.js`
   - `Kirchhoff/web/src/main.js`
4. Rebuild + redeploy each SPA.

Until the ids are set, Umami simply no-ops on those two sites — the
`/telemetry` → Postgres pipeline records everything regardless.

## Verify (end to end)

```bash
# Service up
ssh -i ~/.ssh/om_scaleway root@51.15.253.66 'systemctl is-active oc-telemetry'

# A synthetic event lands in Postgres (site must be one of the allowed three):
curl -sf -X POST https://kelvin.openconverters.com/telemetry \
  -H 'Content-Type: application/json' \
  -d '{"site":"kelvin","session_id":"smoke","event_type":"deploy_smoke","target":"readme"}'

psql "host=$OM_DB_ADDRESS port=$OM_DB_PORT dbname=$OM_DB_NAME user=$OM_DB_USER sslmode=require" \
  -c "SELECT site,event_type,target,occurred_at FROM openconverters_telemetry.events ORDER BY id DESC LIMIT 5;"
```

## Rollback

```bash
ssh -i ~/.ssh/om_scaleway root@51.15.253.66 \
  'systemctl disable --now oc-telemetry && rm -f /etc/systemd/system/oc-telemetry.service /etc/oc-telemetry.env && systemctl daemon-reload'
# remove the include line from each vhost, reload nginx
# (optional) DROP SCHEMA openconverters_telemetry CASCADE;  -- destroys collected data
```
