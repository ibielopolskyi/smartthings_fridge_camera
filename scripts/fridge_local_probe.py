#!/usr/bin/env python3
"""Look for a Samsung Family Hub fridge on the local network (read-only).

Run on a machine at the same house/network as the fridge (e.g. the Home
Assistant server):  python3 fridge_local_probe.py [fridge-ip]

It only listens for discovery replies and makes simple read requests; it
changes nothing. Uses only the Python standard library. Takes ~30 seconds.
"""
from __future__ import annotations

import re
import socket
import ssl
import struct
import sys
import time

TIMEOUT = 4
HINTS = re.compile(r"samsung|tizen|refrig|family ?hub|fridge|oic\.d\.refrigerator", re.I)
PORTS = [80, 443, 7676, 8000, 8001, 8002, 8080, 8443, 8888, 9090, 9197, 9443,
         49152, 49153, 49154, 55000]


def strings(data: bytes, minlen: int = 4) -> list[str]:
    """Printable text inside a binary (e.g. CBOR) reply."""
    return [s.decode() for s in re.findall(rb"[ -~]{%d,}" % minlen, data)]


# ---------------------------------------------------------------- SSDP / UPnP
def ssdp() -> dict[str, list[str]]:
    print("== 1. SSDP / UPnP discovery (4 s)")
    msg = ("M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\n"
           'MAN: "ssdp:discover"\r\nMX: 2\r\nST: ssdp:all\r\n\r\n').encode()
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    s.settimeout(0.5)
    s.sendto(msg, ("239.255.255.250", 1900))
    s.sendto(msg, ("239.255.255.250", 1900))
    found: dict[str, list[str]] = {}
    end = time.time() + TIMEOUT
    while time.time() < end:
        try:
            data, (ip, _) = s.recvfrom(4096)
        except socket.timeout:
            continue
        text = data.decode(errors="replace")
        keep = [ln.strip() for ln in text.splitlines()
                if ln.upper().startswith(("SERVER", "ST:", "LOCATION", "USN"))]
        found.setdefault(ip, [])
        for line in keep:
            if line not in found[ip]:
                found[ip].append(line)
    for ip, lines in sorted(found.items()):
        flag = "  <-- looks Samsung/Tizen/fridge" if any(HINTS.search(l) for l in lines) else ""
        print(f"   {ip}{flag}")
        for line in lines[:6]:
            print(f"      {line[:150]}")
    if not found:
        print("   no SSDP replies")
    return found


# ---------------------------------------------------------------- CoAP / OCF
def coap_get(path: str, msg_id: int) -> bytes:
    """Non-confirmable CoAP GET with Uri-Path options."""
    pkt = bytes([0x50, 0x01]) + struct.pack(">H", msg_id)
    last = 0
    for seg in [p for p in path.strip("/").split("/") if p]:
        b = seg.encode()
        delta = 11 - last  # Uri-Path option number 11
        last = 11
        pkt += bytes([(delta << 4) | len(b)]) + b
    return pkt


def coap_code(data: bytes) -> str:
    return f"{data[1] >> 5}.{data[1] & 0x1F:02d}" if len(data) > 1 else "?"


def coap_payload(data: bytes) -> bytes:
    i = data.find(b"\xff", 4)
    return data[i + 1:] if i >= 0 else b""


def ocf_discovery() -> dict[str, bytes]:
    print("\n== 2. OCF / CoAP discovery of /oic/res (4 s)")
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    s.settimeout(0.5)
    s.sendto(coap_get("/oic/res", 0x4242), ("224.0.1.187", 5683))
    found: dict[str, bytes] = {}
    end = time.time() + TIMEOUT
    while time.time() < end:
        try:
            data, (ip, _) = s.recvfrom(65535)
        except socket.timeout:
            continue
        found[ip] = found.get(ip, b"") + coap_payload(data)
        print(f"   {ip} replied {coap_code(data)} ({len(data)} bytes)")
    for ip, payload in found.items():
        texts = strings(payload)
        flag = "  <-- looks like the fridge" if any(HINTS.search(t) for t in texts) else ""
        print(f"   {ip}{flag}: resources/types advertised:")
        for t in texts[:40]:
            if t.startswith(("/", "oic", "x.com", "core", "coap")):
                print(f"      {t[:120]}")
    if not found:
        print("   no OCF replies (devices may only answer on IPv6, or not at all)")
    return found


def coap_unicast(ip: str, path: str) -> None:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(3)
    try:
        s.sendto(coap_get(path, 0x1234), (ip, 5683))
        data, _ = s.recvfrom(65535)
        texts = [t for t in strings(coap_payload(data)) if len(t) > 3][:8]
        print(f"   coap://{ip}{path}: {coap_code(data)} {len(data)} bytes {texts}")
    except socket.timeout:
        print(f"   coap://{ip}{path}: no reply")
    except OSError as err:
        print(f"   coap://{ip}{path}: {err}")


# ---------------------------------------------------------------- ports
def scan(ip: str) -> None:
    print(f"\n== 3. Open TCP ports on {ip}")
    open_ports = []
    for port in PORTS:
        try:
            with socket.create_connection((ip, port), timeout=1):
                open_ports.append(port)
        except OSError:
            pass
    print("   open:", open_ports or "none of", PORTS if not open_ports else "")
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    for port in open_ports:
        for scheme in ("http", "https"):
            try:
                raw = socket.create_connection((ip, port), timeout=3)
                conn = ctx.wrap_socket(raw, server_hostname=ip) if scheme == "https" else raw
                conn.sendall(f"GET / HTTP/1.1\r\nHost: {ip}\r\nConnection: close\r\n\r\n".encode())
                conn.settimeout(3)
                reply = b""
                while len(reply) < 2000:
                    chunk = conn.recv(2000)
                    if not chunk:
                        break
                    reply += chunk
                conn.close()
                head = reply.decode(errors="replace").splitlines()
                server = [h for h in head if h.lower().startswith("server")]
                title = re.search(r"<title>(.*?)</title>", reply.decode(errors="replace"), re.I | re.S)
                print(f"   {scheme}://{ip}:{port}/ -> {head[0] if head else '(no reply)'} "
                      f"{server[0] if server else ''} {('title: ' + title.group(1).strip()[:60]) if title else ''}")
                break
            except (OSError, ssl.SSLError):
                continue


def main() -> None:
    print("Family Hub local probe — read-only\n")
    ssdp_found = ssdp()
    ocf_found = ocf_discovery()

    targets = set(sys.argv[1:])
    for ip, lines in ssdp_found.items():
        if any(HINTS.search(l) for l in lines):
            targets.add(ip)
    for ip, payload in ocf_found.items():
        if any(HINTS.search(t) for t in strings(payload)):
            targets.add(ip)
    if not targets:
        print("\nNo fridge recognised automatically. Re-run with its IP address:")
        print("   python3 fridge_local_probe.py 192.168.x.y")
        print("(Find it in your router's list of connected devices, or on the fridge's")
        print(" screen: Settings > Connections / About.)")
        return
    for ip in sorted(targets):
        scan(ip)
        print(f"\n== 4. Asking {ip} directly over CoAP (unencrypted)")
        for path in ("/oic/res", "/oic/d", "/udo/contents/provider/vs/0"):
            coap_unicast(ip, path)
    print("\nDone. Copy everything above and send it back.")


if __name__ == "__main__":
    main()
