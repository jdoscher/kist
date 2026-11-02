"""Guarded outbound HTTP for "add from URL".

Kist fetches URLs on the server, inside the user's network. Without a guard,
a web page whose preview image points at http://192.168.1.1/… (or a redirect
there) makes Kist request an internal device and store the reply. This module:

- allows only http/https;
- resolves the host once and refuses private, loopback, link-local, CGNAT,
  multicast, reserved and unique-local addresses (unless allowlisted);
- connects to the exact IP it checked (the Host header and TLS name stay the
  original hostname), so a DNS answer can't change between check and connect;
- follows redirects itself and re-checks every hop;
- enforces an overall deadline as well as a per-read timeout.
"""

import ipaddress
import socket
import time
from collections.abc import Iterator
from contextlib import contextmanager
from urllib.parse import urljoin, urlsplit

import httpx

from ..config import get_settings

MAX_REDIRECTS = 5
READ_TIMEOUT = 30.0
CONNECT_TIMEOUT = 10.0


class BlockedURLError(ValueError):
    """The URL points somewhere Kist refuses to fetch."""


class DownloadTimeout(ValueError):
    """The whole download took longer than the configured deadline."""


IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
Network = ipaddress.IPv4Network | ipaddress.IPv6Network


def _allowlist() -> tuple[set[str], list[Network]]:
    hosts: set[str] = set()
    nets: list[Network] = []
    for raw in get_settings().url_allow_hosts.split(","):
        entry = raw.strip().lower()
        if not entry:
            continue
        try:
            nets.append(ipaddress.ip_network(entry, strict=False))
        except ValueError:
            hosts.add(entry.rstrip("."))
    return hosts, nets


def _effective(ip: IPAddress) -> IPAddress:
    """Unwrap IPv6 forms that embed an IPv4 address."""
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return ip.ipv4_mapped
        if ip.sixtofour is not None:
            return ip.sixtofour
        if ip.teredo is not None:
            return ip.teredo[1]
    return ip


def is_public(ip: IPAddress) -> bool:
    ip = _effective(ip)
    return ip.is_global and not ip.is_multicast and not ip.is_reserved


def check_address(host: str, ip: IPAddress) -> None:
    settings = get_settings()
    if settings.url_allow_private:
        return
    hosts, nets = _allowlist()
    if host.lower().rstrip(".") in hosts or any(_effective(ip) in n or ip in n for n in nets):
        return
    if not is_public(ip):
        raise BlockedURLError(
            "refusing to download from a private or local network address "
            f"({host}); allow it with KIST_URL_ALLOW_HOSTS if you trust it"
        )


def resolve_all(host: str, port: int) -> list[IPAddress]:
    """Resolve host and vet EVERY address it returns (in resolver order)."""
    try:
        literal = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        literal = None
    if literal is not None:
        check_address(host, literal)
        return [literal]
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise httpx.ConnectError(f"could not resolve {host}") from exc
    addrs: list[IPAddress] = []
    for info in infos:
        ip = ipaddress.ip_address(str(info[4][0]).split("%", 1)[0])
        if ip not in addrs:
            addrs.append(ip)
    if not addrs:
        raise httpx.ConnectError(f"could not resolve {host}")
    for ip in addrs:  # a name that resolves to ANY private address is refused
        check_address(host, ip)
    return addrs


def resolve(host: str, port: int) -> IPAddress:
    return resolve_all(host, port)[0]


def _target(url: str) -> tuple[str, str, int, str]:
    """(scheme, host, port, Host header) of url, or BlockedURLError."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise BlockedURLError("only http:// and https:// URLs can be added")
    host = parts.hostname
    if not host:
        raise BlockedURLError("that URL has no host name")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    host_header = host if parts.port is None else f"{host}:{parts.port}"
    return parts.scheme, host, port, host_header


def _send_pinned(client: httpx.Client, url: str, headers: dict) -> httpx.Response:
    """GET url connecting only to vetted addresses, trying each in turn
    (a name often resolves to IPv6 and IPv4; either may be unreachable)."""
    scheme, host, port, host_header = _target(url)
    last_error: Exception | None = None
    for ip in resolve_all(host, port):
        request = client.build_request(
            "GET",
            httpx.URL(url).copy_with(host=str(ip)),
            headers={**headers, "Host": host_header},
            # TLS: SNI and certificate checks use the real host name
            extensions={"sni_hostname": host} if scheme == "https" else {},
        )
        try:
            return client.send(request, stream=True)
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            last_error = exc
    assert last_error is not None
    raise last_error


@contextmanager
def guarded_get(
    url: str,
    headers: dict,
    *,
    transport: httpx.BaseTransport | None = None,
) -> Iterator[tuple[httpx.Response, str, float]]:
    """Stream a GET of url through the guard.

    Yields (response, final_url, deadline) where deadline is a
    time.monotonic() value the caller must stop reading at.
    """
    deadline = time.monotonic() + get_settings().download_timeout_seconds
    timeout = httpx.Timeout(READ_TIMEOUT, connect=CONNECT_TIMEOUT)
    with httpx.Client(
        timeout=timeout, follow_redirects=False, trust_env=False, transport=transport
    ) as client:
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            response = _send_pinned(client, current, headers)
            if response.is_redirect and "location" in response.headers:
                nxt = urljoin(current, response.headers["location"])
                response.close()
                if time.monotonic() > deadline:
                    raise DownloadTimeout("download took too long")
                current = nxt
                continue
            try:
                response.raise_for_status()
                yield response, current, deadline
            finally:
                response.close()
            return
        raise BlockedURLError("too many redirects")
