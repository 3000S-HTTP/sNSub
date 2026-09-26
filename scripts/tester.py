#!/usr/bin/env python3
"""
Pick the N *best* working nodes from the merged subscription.

For each candidate config this script:
  1. parses the share link into an Xray outbound,
  2. starts a throwaway Xray instance with a local SOCKS inbound,
  3. performs a real HTTP round-trip through that SOCKS proxy to a
     generate_204 endpoint (so a node only counts if traffic actually flows),
  4. records the measured latency.

The lowest-latency working nodes win. Output is written next to the full
lists as best<COUNT>.txt / best<COUNT>_base64.txt (+ best<COUNT>.json).

Only Xray-native protocols can be tested (vless / vmess / trojan / ss);
hysteria2 / tuic are skipped because Xray does not speak them.

Standard library only.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import platform
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_DIR = os.path.join(ROOT, "output")
CACHE_DIR = os.path.join(ROOT, ".cache", "xray")

TEST_HOST = "www.gstatic.com"
TEST_PATH = "/generate_204"
TEST_PORT = 80
FALLBACK_HOST = "cp.cloudflare.com"

RELEASE_API = "https://api.github.com/repos/XTLS/Xray-core/releases/latest"


# --------------------------------------------------------------------------- #
# Xray binary
# --------------------------------------------------------------------------- #
def _asset_name() -> str:
    machine = platform.machine().lower()
    arch = "arm64-v8a" if machine in ("arm64", "aarch64") else "64"
    system = platform.system().lower()
    if system == "windows":
        return f"Xray-windows-{arch}.zip"
    if system == "darwin":
        return f"Xray-macos-{arch}.zip"
    return f"Xray-linux-{arch}.zip"


def ensure_xray(explicit: str | None = None) -> str:
    if explicit:
        if os.path.isfile(explicit):
            return explicit
        raise SystemExit(f"xray binary not found: {explicit}")

    exe = "xray.exe" if platform.system().lower() == "windows" else "xray"
    cached = os.path.join(CACHE_DIR, exe)
    if os.path.isfile(cached):
        return cached

    os.makedirs(CACHE_DIR, exist_ok=True)
    asset = _asset_name()
    print(f"Downloading xray-core ({asset})...")

    req = urllib.request.Request(RELEASE_API, headers={"User-Agent": "sNSub-tester"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        release = json.load(resp)
    url = next((a["browser_download_url"] for a in release.get("assets", [])
                if a["name"] == asset), None)
    if not url:
        raise SystemExit(f"no release asset named {asset}")

    archive = os.path.join(CACHE_DIR, asset)
    with urllib.request.urlopen(
        urllib.request.Request(url, headers={"User-Agent": "sNSub-tester"}), timeout=180
    ) as resp, open(archive, "wb") as fh:
        shutil.copyfileobj(resp, fh)

    with zipfile.ZipFile(archive) as zf:
        zf.extractall(CACHE_DIR)
    os.remove(archive)
    if os.path.isfile(cached):
        try:
            os.chmod(cached, 0o755)
        except OSError:
            pass
        return cached
    raise SystemExit("xray binary missing after extraction")


# --------------------------------------------------------------------------- #
# Share-link -> Xray outbound
# --------------------------------------------------------------------------- #
def _q(params: dict[str, list[str]], key: str, default: str = "") -> str:
    values = params.get(key)
    return values[0] if values else default


def _b64_json(text: str) -> dict:
    text = text.strip()
    text += "=" * (-len(text) % 4)
    return json.loads(base64.b64decode(text).decode("utf-8", errors="ignore"))


def _stream_settings(net: str, security: str, params: dict[str, list[str]],
                     header_type: str = "", host: str = "") -> dict:
    net = (net or "tcp").lower()
    security = (security or "none").lower()
    stream: dict = {"network": net, "security": security}

    sni = _q(params, "sni") or _q(params, "serverName") or host
    fp = _q(params, "fp")
    alpn = _q(params, "alpn")
    path = _q(params, "path", "/")
    svc = _q(params, "serviceName")
    mode = _q(params, "mode")

    if security == "tls":
        tls: dict = {"allowInsecure": _q(params, "allowInsecure").lower() in ("1", "true")}
        if sni:
            tls["serverName"] = sni
        if fp:
            tls["fingerprint"] = fp
        if alpn:
            tls["alpn"] = [a for a in alpn.split(",") if a]
        stream["tlsSettings"] = tls
    elif security == "reality":
        stream["realitySettings"] = {
            "serverName": sni,
            "fingerprint": fp or "chrome",
            "publicKey": _q(params, "pbk"),
            "shortId": _q(params, "sid"),
            "spiderX": _q(params, "spx", "/"),
        }

    if net == "ws":
        stream["wsSettings"] = {"path": path or "/",
                                "headers": {"Host": host} if host else {}}
    elif net == "grpc":
        stream["grpcSettings"] = {"serviceName": svc, "multiMode": mode == "multi"}
    elif net == "http" or net == "h2":
        stream["network"] = "http"
        stream["httpSettings"] = {"host": [host] if host else [], "path": path or "/"}
    elif net == "httpupgrade":
        stream["httpupgradeSettings"] = {"path": path or "/", "host": host}
    elif net == "xhttp" or net == "splithttp":
        stream["network"] = "splithttp"
        stream["splithttpSettings"] = {"path": path or "/", "host": host}
    elif net == "quic":
        stream["quicSettings"] = {
            "security": _q(params, "quicSecurity", "none"),
            "key": _q(params, "key"),
            "header": {"type": header_type or "none"},
        }
    elif net == "kcp" or net == "mkcp":
        stream["network"] = "kcp"
        stream["kcpSettings"] = {"header": {"type": header_type or "none"}}
    elif net == "tcp" and header_type == "http":
        stream["tcpSettings"] = {
            "header": {"type": "http", "request": {"path": [path or "/"], "headers": {"Host": [host]}}}
        }
    return stream


def parse_config(config: str) -> dict | None:
    """Return {'outbound': {...}, 'address': ..., 'port': ...} or None."""
    try:
        return _parse_config(config)
    except Exception:
        return None


def _parse_config(config: str) -> dict | None:
    if config.startswith("vless://"):
        u = urllib.parse.urlsplit(config)
        params = urllib.parse.parse_qs(u.query)
        port = u.port or 443
        flow = _q(params, "flow")
        outbound = {
            "protocol": "vless",
            "settings": {"vnext": [{
                "address": u.hostname, "port": port,
                "users": [{"id": urllib.parse.unquote(u.username or ""),
                           "encryption": _q(params, "encryption", "none"),
                           "flow": flow}]}]},
            "streamSettings": _stream_settings(
                _q(params, "type", "tcp"), _q(params, "security", "none"), params,
                _q(params, "headerType"), _q(params, "host")),
        }
        return {"outbound": outbound, "address": u.hostname, "port": port}

    if config.startswith("vmess://"):
        data = _b64_json(config[len("vmess://"):])
        add = str(data.get("add", ""))
        port = int(data.get("port", 443))
        params = {
            "sni": [str(data.get("sni", ""))], "fp": [str(data.get("fp", ""))],
            "alpn": [str(data.get("alpn", ""))], "path": [str(data.get("path", "/"))],
            "host": [str(data.get("host", ""))], "serviceName": [str(data.get("path", ""))],
            "type": [str(data.get("net", "tcp"))],
            "security": ["tls" if str(data.get("tls", "")) == "tls" else "none"],
        }
        outbound = {
            "protocol": "vmess",
            "settings": {"vnext": [{
                "address": add, "port": port,
                "users": [{"id": str(data.get("id", "")),
                           "alterId": int(data.get("aid", 0) or 0),
                           "security": str(data.get("scy", "auto"))}]}]},
            "streamSettings": _stream_settings(
                str(data.get("net", "tcp")),
                "tls" if str(data.get("tls", "")) == "tls" else "none",
                params, str(data.get("type", "")), str(data.get("host", ""))),
        }
        return {"outbound": outbound, "address": add, "port": port}

    if config.startswith("trojan://"):
        u = urllib.parse.urlsplit(config)
        params = urllib.parse.parse_qs(u.query)
        port = u.port or 443
        outbound = {
            "protocol": "trojan",
            "settings": {"servers": [{
                "address": u.hostname, "port": port,
                "password": urllib.parse.unquote(u.username or "")}]},
            "streamSettings": _stream_settings(
                _q(params, "type", "tcp"), _q(params, "security", "tls"), params,
                _q(params, "headerType"), _q(params, "host")),
        }
        return {"outbound": outbound, "address": u.hostname, "port": port}

    if config.startswith("ss://"):
        body = config[len("ss://"):].split("#", 1)[0].split("?", 1)[0]
        method = password = host = ""
        port = 0
        if "@" in body:
            userinfo, hostport = body.rsplit("@", 1)
            host, _, port_s = hostport.rpartition(":")
            port = int(port_s)
            try:
                decoded = base64.b64decode(userinfo + "=" * (-len(userinfo) % 4)).decode()
                method, _, password = decoded.partition(":")
            except Exception:
                method, _, password = userinfo.partition(":")
        else:
            decoded = base64.b64decode(body + "=" * (-len(body) % 4)).decode()
            userinfo, _, hostport = decoded.rpartition("@")
            method, _, password = userinfo.partition(":")
            host, _, port_s = hostport.rpartition(":")
            port = int(port_s)
        if not host or not method:
            return None
        outbound = {
            "protocol": "shadowsocks",
            "settings": {"servers": [{
                "address": host, "port": port, "method": method, "password": password}]},
            "streamSettings": {"network": "tcp"},
        }
        return {"outbound": outbound, "address": host, "port": port}

    return None


# --------------------------------------------------------------------------- #
# SOCKS + HTTP round trip
# --------------------------------------------------------------------------- #
def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise OSError("connection closed")
        buf += chunk
    return buf


def socks5_http_latency(socks_port: int, host: str, port: int, path: str,
                        timeout: float) -> float | None:
    """Open a SOCKS5 tunnel and fetch http://host:port/path. Returns ms or None."""
    start = time.monotonic()
    try:
        sock = socket.create_connection(("127.0.0.1", socks_port), timeout=timeout)
        sock.settimeout(timeout)
        sock.sendall(b"\x05\x01\x00")
        if _recv_exact(sock, 2) != b"\x05\x00":
            return None
        req = b"\x05\x01\x00\x03" + bytes([len(host)]) + host.encode() + port.to_bytes(2, "big")
        sock.sendall(req)
        reply = _recv_exact(sock, 4)
        if reply[1] != 0x00:
            return None
        atyp = reply[3]
        if atyp == 0x01:
            _recv_exact(sock, 4)
        elif atyp == 0x03:
            _recv_exact(sock, _recv_exact(sock, 1)[0])
        elif atyp == 0x04:
            _recv_exact(sock, 16)
        _recv_exact(sock, 2)
        http = (f"GET {path} HTTP/1.1\r\nHost: {host}\r\n"
                "User-Agent: Mozilla/5.0\r\nAccept: */*\r\nConnection: close\r\n\r\n")
        sock.sendall(http.encode())
        head = sock.recv(64)
        sock.close()
        if not head:
            return None
        line = head.split(b"\r\n", 1)[0]
        if b" 20" in line or b" 30" in line:
            return (time.monotonic() - start) * 1000
    except (OSError, ssl.SSLError):
        return None
    return None


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_port(port: int, proc: subprocess.Popen, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return False
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.3).close()
            return True
        except OSError:
            time.sleep(0.1)
    return False


def test_one(xray_bin: str, config: str, timeout: float) -> dict | None:
    parsed = parse_config(config)
    if not parsed:
        return None
    port = free_port()
    cfg = {
        "log": {"loglevel": "warning"},
        "inbounds": [{"listen": "127.0.0.1", "port": port, "protocol": "socks",
                      "settings": {"udp": False}}],
        "outbounds": [parsed["outbound"]],
    }
    tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
    json.dump(cfg, tmp)
    tmp.close()
    proc = None
    try:
        proc = subprocess.Popen(
            [xray_bin, "run", "-c", tmp.name],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if not _wait_for_port(port, proc):
            return None
        latency = socks5_http_latency(port, TEST_HOST, TEST_PORT, TEST_PATH, timeout)
        if latency is None:
            latency = socks5_http_latency(port, FALLBACK_HOST, TEST_PORT, TEST_PATH, timeout)
        if latency is None:
            return None
        return {"config": config, "latency_ms": round(latency, 1),
                "address": parsed["address"], "port": parsed["port"]}
    finally:
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
        try:
            os.remove(tmp.name)
        except OSError:
            pass


def test_many(xray_bin: str, configs: list[str], workers: int,
              timeout: float) -> list[dict]:
    results: list[dict] = []
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(test_one, xray_bin, c, timeout): c for c in configs}
        for future in as_completed(futures):
            done += 1
            try:
                r = future.result()
            except Exception:
                r = None
            if r:
                results.append(r)
            if done % 25 == 0 or done == len(configs):
                print(f"  tested {done}/{len(configs)}  passing={len(results)}", flush=True)
    results.sort(key=lambda r: r["latency_ms"])
    return results


def _dump(basename: str, configs: list[str]) -> None:
    body = "\n".join(configs) + ("\n" if configs else "")
    with open(os.path.join(OUTPUT_DIR, f"{basename}.txt"), "w",
              encoding="utf-8", newline="\n") as fh:
        fh.write(body)
    with open(os.path.join(OUTPUT_DIR, f"{basename}_base64.txt"), "w",
              encoding="utf-8", newline="\n") as fh:
        fh.write(base64.b64encode(body.encode()).decode("ascii"))


def main() -> int:
    ap = argparse.ArgumentParser(description="Select the best working nodes.")
    ap.add_argument("--input", default=os.path.join(OUTPUT_DIR, "configs.txt"))
    ap.add_argument("--best", type=int, default=59)
    ap.add_argument("--limit", type=int, default=800, help="max candidates to test")
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--timeout", type=float, default=6.0)
    ap.add_argument("--xray", default=os.environ.get("XRAY_BIN"))
    args = ap.parse_args()

    if not os.path.isfile(args.input):
        raise SystemExit(f"input not found: {args.input} (run build.py first)")

    with open(args.input, "r", encoding="utf-8") as fh:
        configs = [ln.strip() for ln in fh if ln.strip()]

    # Testable protocols first (xray-native), preserving order otherwise.
    testable = [c for c in configs if parse_config(c) is not None]
    candidates = testable[:args.limit]
    if not candidates:
        raise SystemExit("no testable candidates (xray-native protocols) found")

    xray_bin = ensure_xray(args.xray)
    print(f"Testing {len(candidates)} candidate(s) with {args.workers} workers, "
          f"{args.timeout}s timeout...")
    results = test_many(xray_bin, candidates, args.workers, args.timeout)

    if not results:
        print("No working nodes found; leaving previous best files untouched.",
              file=sys.stderr)
        return 1

    best = results[:args.best]
    _dump(f"best{args.best}", [r["config"] for r in best])
    with open(os.path.join(OUTPUT_DIR, f"best{args.best}.json"), "w", encoding="utf-8") as fh:
        json.dump({
            "tested": len(candidates),
            "passed": len(results),
            "selected": len(best),
            "nodes": [{"latency_ms": r["latency_ms"], "address": r["address"],
                       "port": r["port"]} for r in best],
        }, fh, indent=2)

    print(f"\nSelected {len(best)} best of {len(results)} working "
          f"({len(candidates)} tested). Latency {best[0]['latency_ms']}-"
          f"{best[-1]['latency_ms']} ms.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())