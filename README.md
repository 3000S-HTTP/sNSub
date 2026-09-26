# sNSub — Free V2Ray Subscription Builder

An **auto-updating V2Ray/Xray subscription** built from public free-config
repositories. A scheduled GitHub Action fetches every source, merges and
de-duplicates the nodes, groups them by protocol, and re-encodes them as both
plain-text and base64 subscriptions in `output/`.

Based on the approach used by
[patterniha/Free-Configs](https://github.com/patterniha/Free-Configs) and
aggregating nodes from:

- [patterniha/Free-Configs](https://github.com/patterniha/Free-Configs)
- [barry-far/v2ray-config](https://github.com/barry-far/v2ray-config)
- [ebrasha/free-v2ray-public-list](https://github.com/ebrasha/free-v2ray-public-list)

…plus several other well-known public lists (see [`sources.txt`](sources.txt)).

## Subscription URLs

The list refreshes every 6 hours via GitHub Actions.

| Purpose | URL |
| --- | --- |
| **All nodes (base64)** — use this in your client | `https://raw.githubusercontent.com/3000S-HTTP/sNSub/main/output/configs_base64.txt` |
| All nodes (plain text) | `https://raw.githubusercontent.com/3000S-HTTP/sNSub/main/output/configs.txt` |
| VLESS + VMess only (base64) | `https://raw.githubusercontent.com/3000S-HTTP/sNSub/main/output/configs_vless_vmess_base64.txt` |
| VLESS | `https://raw.githubusercontent.com/3000S-HTTP/sNSub/main/output/vless_base64.txt` |
| VMess | `https://raw.githubusercontent.com/3000S-HTTP/sNSub/main/output/vmess_base64.txt` |
| Trojan | `https://raw.githubusercontent.com/3000S-HTTP/sNSub/main/output/trojan_base64.txt` |
| Shadowsocks | `https://raw.githubusercontent.com/3000S-HTTP/sNSub/main/output/ss_base64.txt` |
| SSR | `https://raw.githubusercontent.com/3000S-HTTP/sNSub/main/output/ssr_base64.txt` |
| Hysteria2 | `https://raw.githubusercontent.com/3000S-HTTP/sNSub/main/output/hysteria2_base64.txt` |

**Main subscription (copy this into your client):**

```
https://raw.githubusercontent.com/3000S-HTTP/sNSub/main/output/configs_base64.txt
```

**CDN mirror (faster, cached):**

```
https://cdn.jsdelivr.net/gh/3000S-HTTP/sNSub@main/output/configs_base64.txt
```

## Use in a client

- **v2rayNG / v2rayN / NekoBox / Hiddify / Streisand / Shadowrocket**:
  add the `configs_base64.txt` URL as a *Subscription* and update.
- **Sing-box / Clash Meta**: import the same URL (most clients understand the
  base64 list) or point them at `output/configs.txt`.

## How the update works

```
                sources.txt
                    |   (one URL per line, editable -- no code change)
                    v
   .github/workflows/build.yml  -->  scripts/build.py  -->  output/*.txt
        (every 6h / on push)              |
                                          |- fetch all sources in parallel
                                          |- auto-detect plain vs base64
                                          |- merge + de-duplicate
                                          |- group by protocol
                                          \- write plain + base64 subs
```

- `sources.txt` — the list of upstream lists. Add / remove / comment with `#`.
- `scripts/build.py` — the builder. Python **standard library only**, no deps.
- `.github/workflows/build.yml` — runs on a schedule, on demand, and whenever
  `sources.txt` or `scripts/` change; commits only `output/` back.
- `output/summary.json` — last run time, source health, per-file counts.

A single dead source never breaks the build; it is reported as `[DEAD]` and
the rest still run. The build only fails if **every** source is unreachable.

## Run it locally

```bash
python scripts/build.py                 # all sources -> output/
python scripts/build.py --filter vless_vmess
python scripts/build.py --sources my_sources.txt --workers 16
```

Requires Python 3.9+. Nothing to install.

## Add or change a source

1. Edit `sources.txt` (one URL per line; `#` to disable).
2. Commit and push — the next scheduled run picks it up automatically, or
   trigger the **Build subscriptions** workflow manually from the Actions tab.

## Notes

- Free public nodes are frequently unstable and are not operated by this
  project. Treat them as untrusted; never send sensitive traffic through them.
- De-duplication ignores the `#label` fragment, so the same server listed by
  two sources appears once.