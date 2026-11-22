# Tilia candle relay

A single stdlib-only Python file (`tilia_relay.py`, needs only `python3`). The candle page
hands it a sealed blob; the owner's Mac collects it, answers with another sealed blob, and
the page collects that. The relay never decrypts anything. Memory only; asks and answers
expire after 120 s; bodies are capped at 4 KB; 10 asks/min per IP; 20 queued asks per Mac.
Logs hold method, route name, status and duration, never a body or an id.

## Run

    TILIA_RELAY_MACS='sam-mac-1:<sha256hex>' python3 tilia_relay.py

Binds 127.0.0.1 only (default port 8787). Put Caddy or a Cloudflare tunnel in front.

| Env | Meaning |
| --- | --- |
| `TILIA_RELAY_MACS` | `id:sha256hex,id:sha256hex`; id is `[A-Za-z0-9_-]{6,32}`, the hash is sha256 of that Mac's secret |
| `TILIA_RELAY_ORIGINS` | extra CORS origins (comma separated), e.g. a dev origin. `https://mydude-industries.github.io` is always allowed |
| `TILIA_RELAY_TRUST_PROXY` | `1` to rate-limit on `CF-Connecting-IP`, else the last `X-Forwarded-For` hop (the one your proxy appended); leave unset without a proxy |
| `PORT` | default 8787 |

## In front

Caddy:

    relay.example.com {
        reverse_proxy 127.0.0.1:8787
    }

or a Cloudflare tunnel: `cloudflared tunnel --url http://127.0.0.1:8787`. Set
`TILIA_RELAY_TRUST_PROXY=1` behind either.

With `TILIA_RELAY_TRUST_PROXY=1`, firewall the origin so only Cloudflare (or the tunnel,
or Caddy on the same box) can reach it. The relay then believes `CF-Connecting-IP`, and
anyone who can reach it directly can set that header to any address and dodge the rate
limit. Behind Caddy with no Cloudflare in front, also have Caddy drop the header:
`reverse_proxy 127.0.0.1:8787 { header_up -CF-Connecting-IP }`.

## Service

Copy `tilia_relay.py` to `/opt/tilia-relay/`, `tilia-relay.service` to
`/etc/systemd/system/`, write the env file `/etc/tilia-relay.env` (mode 600), then
`systemctl enable --now tilia-relay`. It runs as a `DynamicUser` and restarts always.

## Add a Mac

Run `./setup relay` on the Mac. It prints that Mac's `id:sha256hex`; append it to
`TILIA_RELAY_MACS` (comma separated) and restart the service. The secret itself stays in
that Mac's Keychain.

## Routes

- `POST /v1/ask/<mac_id>` `{"entry": 12 chars, "sealed": str}` returns `{"ask_id"}` (page)
- `GET /v1/answer/<ask_id>?wait=N` returns 200 `{"sealed"}` or `{}`, 204 (not yet), 404 (gone) (page; N is 0..25)
- `GET /v1/pending/<mac_id>?wait=N` with `Authorization: Bearer <secret>` returns `{"asks": [...]}`, each ask once
- `POST /v1/answer/<ask_id>` with Bearer, body `{"sealed": str}` or `{}`, returns 204
