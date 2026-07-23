# Public demo for the reviewer — quick guide

A one-command way to put the FinPortfolio IR site online (via a temporary
Cloudflare tunnel) so a reviewer can test it in a browser, with nothing to
install. It complements the Big Data write-up in
[BIG_DATA_INFRASTRUCTURE.md](BIG_DATA_INFRASTRUCTURE.md): the site is the
searchable front end over the **26,368-document** corpus this project collects
and processes (macro 18,240 · SEC 7,022 · company IR 1,106).

Full options are in [PUBLIC_DEMO_CLOUDFLARE.md](PUBLIC_DEMO_CLOUDFLARE.md); this
page is the short version.

---

## 1. Student: launch the demo (Windows PowerShell)

From the repository root:

```powershell
.\deploy\cloudflare\start_public_demo.ps1
```

The script:

1. preflights the search index and that `python` can import the app;
2. starts `web_app.py --public-demo` on `127.0.0.1:8780` and waits for `/api/health`;
3. opens a Cloudflare quick tunnel and prints a public
   `https://<random>.trycloudflare.com` URL.

**Copy that URL and send it to the reviewer. Keep the PowerShell window open**
while they test — closing it takes the site down.

Prerequisites (already satisfied on the build machine): `cloudflared` installed,
`python` able to run the app, and `data/search_index/finportfolio_search.sqlite`
present. If a network drops UDP, the default HTTP/2 transport is used; add
`-TunnelProtocol quic` only on networks known to pass QUIC.

### Stop

Press `Ctrl+C` in the window (the script stops the server for you), or:

```powershell
.\deploy\cloudflare\stop_public_demo.ps1
```

### Test locally first (no public URL)

```powershell
.\deploy\cloudflare\start_public_demo.ps1 -NoTunnel   # then open http://127.0.0.1:8780
```

---

## 2. Reviewer: what to try (≈ 5 minutes)

Open the URL you were sent. The site is an English, read-only demo over a local,
reproducible corpus — no live crawling, no accounts, settings are isolated per
browser.

1. **Search a company or ticker** — try `Apple risk factors`, `AAPL`,
   `inflation`, `interest rates`, `Microsoft`. Results come back **grouped by
   evidence**, each with its source family (SEC / macro / company IR), title, and
   timestamps.
2. **Look at the provenance** — every result carries `available_at`; the ranking
   is point-in-time safe (a document is never shown before it was available).
3. **Query intent** — the search response routes the query (e.g. a *risk* query
   prefers an SEC 10-K risk section over a press release).
4. **Dashboard** — the US-macro dashboard and portfolio summary give an at-a-glance
   view of the collected data.
5. **Settings** — set a portfolio (Dow-30 tickers) and a few favorite sources;
   they persist for your browser session only.
6. **My Vibe** — portfolio-aware analysis: the same evidence is re-weighted by what
   your portfolio actually holds.
7. **LLM analysis** *(optional)* — clicking a document explains chart evidence via
   an LLM; without a server key it falls back to deterministic local analysis, and
   full document text is only sent to the model after you click.

Please do **not** enter real API keys or private portfolio data — this is a shared
demo URL with no authentication.

---

## 3. Notes / limitations

- Quick-tunnel URLs are temporary and change on restart; share only with the
  reviewer.
- The demo serves the prebuilt SQLite index; it does not start live crawling.
- For a stable URL, use a named Cloudflare Tunnel (account + domain) instead of
  the quick tunnel — see `PUBLIC_DEMO_CLOUDFLARE.md`.
