# sNSub

Auto-updating V2Ray subscription built from public free-config lists, plus a live-tested **Best 59**. [فارسی](README_fa.md)

## Subscribe

Base URL: `https://raw.githubusercontent.com/3000S-HTTP/sNSub/main/output/`

- **★ Best 59 (live-tested):** `best59_base64.txt`
- All nodes: `configs_base64.txt`
- VLESS + VMess: `configs_vless_vmess_base64.txt`
- Per protocol: `vless` · `vmess` · `trojan` · `ss` · `ssr` · `hysteria2` (`_base64.txt` for base64)

CDN mirror: `https://cdn.jsdelivr.net/gh/3000S-HTTP/sNSub@main/output/best59_base64.txt`

## How it works

- GitHub Action runs every 6h: `scripts/build.py` fetches [`sources.txt`](sources.txt), merges and de-duplicates.
- `scripts/tester.py` live-tests nodes through **Xray-core** and keeps the **59 fastest working** → `output/best59.*`.
- Full lists are still published. Edit `sources.txt` to add/remove sources (first listed = tested first).

## Local

```bash
python scripts/build.py
python scripts/tester.py --best 59
```

Based on [patterniha/Free-Configs](https://github.com/patterniha/Free-Configs) + [barry-far/v2ray-config](https://github.com/barry-far/v2ray-config) + [ebrasha/free-v2ray-public-list](https://github.com/ebrasha/free-v2ray-public-list).

Free public nodes are untrusted — never send sensitive traffic through them. `hysteria2`/`tuic`/`ssr` are not live-tested.