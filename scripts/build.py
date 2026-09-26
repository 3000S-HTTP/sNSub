#!/usr/bin/env python3
"""
Aggregate free V2Ray/Xray configs from a list of sources and build
ready-to-use subscription files.

Design goals (borrowed from patterniha/Free-Configs):
  * Sources live in sources.txt, one URL per line. No code edit to add one.
  * Plain-text and base64 subscription formats are auto-detected.
  * Every source is optional: if one dies the build continues with the rest.
  * Nodes are merged, de-duplicated, grouped by protocol, and re-encoded as
    both plain text and base64 subscriptions.

Only the Python standard library is used, so it runs anywhere with Python 3
and no `pip install` step.
"""

from __future__ import annotations

import argparse
import base64
import datetime as _dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

try:  # Python 3.7+
    import ssl

    _SSL_CTX = ssl.create_default_context()
except Exception:  # pragma: no cover
    _SSL_CTX = None

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_DIR = os.path.join(ROOT, "output")
SOURCES_FILE = os.path.join(ROOT, "sources.txt")

PROTOCOLS = ("vless", "vmess", "trojan", "ss", "ssr", "hysteria", "hysteria2", "tuic")
SCHEME_RE = re.compile(r"^([a-zA-Z][a-zA-Z0-9+.\-]*)://")
BASE64_RE = re.compile(r"^[A-Za-z0-9+/=\s]+$")
USER_AGENT = "Mozilla/5.0 (compatible; sNSub-builder/1.0; +https://github.com)"


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
def http_get(url: str, timeout: int = 60, retries: int = 3) -> str | None:
    """Fetch a URL and return decoded text, or None on failure."""
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "*/*",
                    "Cache-Control": "no-cache",
                },
            )
            with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
                raw = resp.read()
            text = raw.decode("utf-8", errors="ignore")
            return text
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as err:
            last_err = err
            if attempt < retries:
                time.sleep(min(2 ** attempt, 8))
    print(f"  ! failed to fetch {url} ({last_err})", file=sys.stderr)
    return None


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #
def _b64decode(data: str) -> str:
    data = data.strip()
    data += "=" * (-len(data) % 4)
    return base64.b64decode(data, validate=False).decode("utf-8", errors="ignore")


def extract_configs(text: str) -> list[str]:
    """Pull every `scheme://...` config out of arbitrary text.

    Handles: plain newline lists, whitespace/comma separated blobs and
    base64-encoded subscription bodies (including base64-encoded ones that
    wrap another base64 layer for vmess).
    """
    if not text:
        return []

    candidates: list[str] = []

    stripped = text.strip()
    # Unwrap a whole-body base64 subscription (only if no plain config is present).
    if "://" not in text and stripped and BASE64_RE.match(stripped):
        try:
            decoded = _b64decode(stripped)
            if "://" in decoded:
                text = decoded
        except Exception:
            pass

    # Normalise separators, then let the regex find every config.
    for chunk in re.split(r"[\r\n]+", text):
        if "://" not in chunk:
            continue
        for match in re.finditer(r"[a-zA-Z][a-zA-Z0-9+.\-]*://[^\s\"'<>\\]+", chunk):
            candidates.append(match.group(0).strip())

    return candidates


def protocol_of(config: str) -> str | None:
    match = SCHEME_RE.match(config)
    if not match:
        return None
    scheme = match.group(1).lower()
    if scheme == "vmess":
        return "vmess"
    return scheme


def dedupe_key(config: str) -> str:
    """Normalise a config so trivially different duplicates collapse."""
    body = config.split("://", 1)[-1]
    return body.split("#", 1)[0].strip().rstrip("/").lower()


def load_sources(path: str) -> list[str]:
    urls: list[str] = []
    if not os.path.exists(path):
        return urls
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            urls.append(line)
    return urls


# --------------------------------------------------------------------------- #
# Build
# --------------------------------------------------------------------------- #
def collect(urls: list[str], workers: int = 12) -> tuple[dict[str, str], list[str]]:
    """Fetch all sources concurrently. Returns (unique key->config, source stats).

    The returned dict preserves *source order*: nodes from earlier lines in
    sources.txt come first. This matters because scripts/tester.py tests only
    the leading slice, so trusted / already-verified sources should be listed
    first. Within a source the original order is preserved too.
    """
    fetched: dict[str, str | None] = {}

    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(urls) or 1))) as pool:
        futures = {pool.submit(http_get, url): url for url in urls}
        for future in as_completed(futures):
            url = futures[future]
            fetched[url] = future.result()

    unique: dict[str, str] = {}
    stats: list[str] = []
    for url in urls:  # deterministic, source-priority order
        text = fetched.get(url)
        if text is None:
            stats.append(f"[DEAD] {url}")
            continue
        found = extract_configs(text)
        kept = 0
        for config in found:
            proto = protocol_of(config)
            if proto not in PROTOCOLS:
                continue
            key = dedupe_key(config)
            if key and key not in unique:
                unique[key] = config
                kept += 1
        stats.append(f"[ OK ] {url} -> {len(found)} found, {kept} new")

    return unique, stats


def write_outputs(unique: dict[str, str], protocol_filter: str = "all") -> dict[str, int]:
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    by_proto: dict[str, list[str]] = {p: [] for p in PROTOCOLS}
    for config in unique.values():
        proto = protocol_of(config)
        if proto in by_proto:
            by_proto[proto].append(config)

    written: dict[str, int] = {}

    def _dump(basename: str, configs: list[str], with_b64: bool = True) -> None:
        plain_name = f"{basename}.txt"
        plain_path = os.path.join(OUTPUT_DIR, plain_name)
        body = "\n".join(configs) + ("\n" if configs else "")
        with open(plain_path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(body)
        written[plain_name] = len(configs)

        if with_b64:
            b64_name = f"{basename}_base64.txt"
            b64_path = os.path.join(OUTPUT_DIR, b64_name)
            encoded = base64.b64encode(body.encode("utf-8")).decode("ascii")
            with open(b64_path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(encoded)
            written[b64_name] = len(configs)

    if protocol_filter == "all":
        _dump("configs", list(unique.values()))
        _dump("configs_vless_vmess", by_proto["vless"] + by_proto["vmess"])
    elif protocol_filter == "vless_vmess":
        _dump("configs_vless_vmess", list(unique.values()))
    else:
        _dump("configs", list(unique.values()))

    for proto, configs in by_proto.items():
        if configs:
            _dump(proto, configs)

    return written


def fetch_remote_meta(url: str) -> tuple[str, str]:
    """Return (name, url) for building the mirror sub files."""
    return (url, url)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build V2Ray subscription files.")
    parser.add_argument("--sources", default=SOURCES_FILE, help="path to sources.txt")
    parser.add_argument("--filter", default="all", choices=("all", "vless_vmess"),
                        help="protocol filter for the primary output")
    parser.add_argument("--workers", type=int, default=12, help="parallel fetch workers")
    args = parser.parse_args()

    urls = load_sources(args.sources)
    if not urls:
        print("No sources found in", args.sources, file=sys.stderr)
        return 1

    print(f"Fetching {len(urls)} source(s)...")
    unique, stats = collect(urls, workers=args.workers)

    for line in stats:
        print(" ", line)

    if not unique:
        print("No configs collected from any source.", file=sys.stderr)
        return 1

    written = write_outputs(unique, protocol_filter=args.filter)

    now = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    counts = {}
    for name, n in written.items():
        if name.endswith(".txt") and not name.endswith("_base64.txt"):
            counts[name] = n

    summary = {
        "updated": now,
        "total_unique": len(unique),
        "sources_total": len(urls),
        "sources_ok": sum(1 for s in stats if s.startswith("[ OK ]")),
        "files": counts,
    }
    with open(os.path.join(OUTPUT_DIR, "summary.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2)

    print(f"\nDone. {len(unique)} unique nodes -> {OUTPUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())