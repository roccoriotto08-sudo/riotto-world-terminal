# Deploying Riotto World Terminal

The terminal is a live app: a Python server polls the data providers on a schedule and keeps a
SQLite database. It needs **one always-on instance with a small persistent disk**, so free tiers that
sleep or wipe the disk won't work. Expect roughly **$5–10/month**; check the host's current pricing.

The container runs with `PUBLIC_MODE=true`:

* **Licence-restricted series are never fetched or served:** S&P 500 (S&P Dow Jones Indices), ICE BofA
  HY OAS, Cboe VIX.
* **Vendor market data and ACLED stay off** unless you set `MARKET_REDISTRIBUTION_LICENSED=true` /
  `ACLED_PUBLIC_LICENSED=true`, which you should do only if your plan allows public display.
* **Manual "poll now" is disabled**, so visitors can't spend your API budgets.
* **ABOUT** (header button, or `ABOUT <GO>`) shows data credits and a not-investment-advice disclaimer.

Your API keys live only in the host's environment settings. `.env` is git-ignored and
docker-ignored.

---

## 1. Put the code on GitHub

1. On github.com, create a **new empty repository** (e.g. `riotto-world-terminal`), with no README
   and no .gitignore.
2. In this folder:

   ```bash
   git remote add origin https://github.com/<your-username>/riotto-world-terminal.git
   ```

   ```bash
   git push -u origin main
   ```

   When asked for a password, use a GitHub **personal access token** (Settings → Developer settings →
   Tokens). Alternatively, open the folder in GitHub Desktop and click "Publish repository".

## 2a. Deploy on Railway (recommended)

1. railway.com → **New Project → Deploy from GitHub repo** → pick the repo. Railway reads
   `railway.json` and builds the `Dockerfile`.
2. **Add a volume**: service → Settings → Volumes → mount path **`/data`** (1 GB is plenty).
3. **Variables**: add `FRED_API_KEY` (and `EIA_API_KEY`, `AISSTREAM_API_KEY` if you have them).
   `PUBLIC_MODE=true` is already set in the image.
4. **Networking → Generate Domain** to get a public `*.up.railway.app` URL.
5. The first boot backfills history for 1–3 minutes. The globe, map and tables fill in as each feed
   completes; the data-health strip in the header shows progress.

## 2b. Or deploy on Render

1. render.com → **New → Blueprint** → pick the repo. Render reads `render.yaml`: a Docker web service
   with a 1 GB disk at `/data`.
2. Enter `FRED_API_KEY` (and the optional keys) when prompted.
3. Use a paid instance. Free instances sleep and have no disk.

## 3. Custom domain (optional, ~$10–15/year)

Buy a domain (e.g. from Cloudflare or Namecheap), then add it under the host's custom-domain settings
and create the CNAME record it shows you. HTTPS is automatic on both hosts.

## Updating the live site

Commit and push. Both hosts redeploy automatically from `main`, and the volume keeps the history.

## Notes

* **Run exactly one instance / replica.** The data scheduler runs inside the web process.
* **Logs:** the host's log view. Provider failures also appear in the header health strip and at
  `/api/audit`.
* **Health check:** `GET /healthz`.
