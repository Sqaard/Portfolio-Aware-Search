# Public Demo Via Cloudflare Tunnel

This is a bug-bash deployment path, not production hosting. It exposes the
local Python dashboard through a temporary Cloudflare quick tunnel.

## What Is Safe In This Mode

- The backend runs with `--public-demo`.
- Portfolio and favorite website settings are isolated by a browser cookie.
- LLM API keys are not persisted by the app and are never sent to the browser.
- If `.env` or the process environment contains server LLM settings, testers
  can use LLM features through the backend without seeing the key.
- Favorite website validation rejects localhost/private-network targets.
- The app keeps using the local, reproducible document corpus and SQLite search
  index; it does not start live crawling.

Do not set server-side `MISTRAL_API_KEY`, `OPENAI_API_KEY`, or `LLM_API_KEY`
when sharing a public tunnel unless you intentionally want testers to spend that
key. To force a no-server-LLM demo, pass `-DisableServerLlm`.

## Start

From `FinPortfolio_IR`:

```powershell
.\deploy\cloudflare\start_public_demo.ps1
```

The script starts:

1. `python web_app.py --public-demo` on `127.0.0.1:8780`;
2. a Cloudflare quick tunnel to `http://127.0.0.1:8780`.

The launcher defaults to Cloudflare Tunnel `http2` transport. This is slower
than QUIC in ideal networks, but it is usually more stable on Wi-Fi, corporate
networks, and providers that drop or throttle UDP traffic. If your network is
known to handle QUIC well, you can switch back:

```powershell
.\deploy\cloudflare\start_public_demo.ps1 -TunnelProtocol quic
```

Tunnel runner selection:

- default: use `cloudflared` if installed;
- fallback: use `npx --yes wrangler@latest tunnel quick-start ...`;
- force Wrangler if needed:

```powershell
.\deploy\cloudflare\start_public_demo.ps1 -TunnelRunner wrangler
```

If logs contain repeated QUIC messages such as `timeout: no recent network
activity`, use the default `http2` mode or run explicitly:

```powershell
.\deploy\cloudflare\start_public_demo.ps1 -TunnelProtocol http2
```

Copy the generated `https://*.trycloudflare.com` URL and share it with testers.
Keep the PowerShell window open while the demo is live.

If you only want to test locally without Cloudflare:

```powershell
.\deploy\cloudflare\start_public_demo.ps1 -NoTunnel
```

Then open:

```text
http://127.0.0.1:8780
```

## Stop

Press `Ctrl+C` in the Cloudflare terminal window. The launch script will try to
stop the Python demo server automatically. If you started with `-NoTunnel` or
need cleanup:

```powershell
.\deploy\cloudflare\stop_public_demo.ps1
```

The stop script also stops stale `cloudflared` processes that target
`http://127.0.0.1:8780`. If you used a different port, pass the same port:

```powershell
.\deploy\cloudflare\stop_public_demo.ps1 -Port 8790
```

## Bug Report Template For Testers

Ask testers to send:

- public URL path where the issue happened;
- query or action they tried;
- expected behavior;
- actual behavior;
- screenshot if possible;
- browser name;
- whether they used My Vibe, Settings, Search, or LLM Analysis.

They should not send API keys or private portfolio data in bug reports.

## Current Limitations

- Quick tunnels are temporary and change URL on restart.
- There is no authentication. Share only with trusted testers.
- Settings are per browser session, not real user accounts.
- The site is a demo over local files; use a named Cloudflare Tunnel or a real
  app deployment before broader distribution.
