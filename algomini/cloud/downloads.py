"""Wget transport for arbitrary public web links, without inherited credentials."""

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
from pathlib import Path
import re
import select
import socket
import subprocess
import tempfile
import threading
from urllib.parse import urljoin, urlsplit

from ..common import worker_environment


def public_connection(host, port):
    """Resolve once, then connect to that exact public IP (including after redirects)."""
    addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    for _, _, _, _, address in addresses:
        ip = ipaddress.ip_address(address[0])
        # Azure's host-node WireServer is internal despite its public-looking IP.
        # https://learn.microsoft.com/azure/virtual-network/what-is-ip-address-168-63-129-16
        if not ip.is_global or ip.is_multicast or ip.is_reserved or str(ip) == "168.63.129.16":
            raise OSError("Private/internal network destinations are unavailable")
    for family, kind, protocol, _, address in addresses:
        connection = socket.socket(family, kind, protocol)
        connection.settimeout(20)
        try:
            connection.connect(address)  # No second DNS lookup in wget or create_connection.
            return connection
        except OSError:
            connection.close()
    raise OSError("Public destination is unavailable")


class PublicProxy(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass  # Download links may contain signed query parameters.

    def do_CONNECT(self):
        established = False
        try:
            target = urlsplit("//" + self.path)
            with public_connection(target.hostname, target.port or 443) as upstream:
                self.send_response(200, "Connection established")
                self.end_headers()
                self.wfile.flush()
                established = True
                self.close_connection = True
                while True:
                    readable, _, _ = select.select([self.connection, upstream], [], [], 20)
                    if not readable:
                        return
                    for source in readable:
                        data = source.recv(65536)
                        if not data:
                            return
                        (upstream if source is self.connection else self.connection).sendall(data)
        except (OSError, ValueError):
            self.close_connection = True
            # Before a tunnel is established, report a normal proxy error.
            if not established and not self.wfile.closed:
                try:
                    self.send_error(403, "Public web destination unavailable")
                except OSError:
                    pass

    def do_GET(self):
        started = False
        try:
            target = urlsplit(self.path)
            if target.scheme != "http" or not target.hostname:
                self.send_error(400, "HTTP or HTTPS web links are required")
                return
            with public_connection(target.hostname, target.port or 80) as upstream:
                path = target.path or "/"
                if target.query:
                    path += "?" + target.query
                headers = [(key, value) for key, value in self.headers.items()
                           if key.lower() not in {"connection", "proxy-connection", "proxy-authorization"}]
                request = f"{self.command} {path} HTTP/1.1\r\n"
                request += "".join(f"{key}: {value}\r\n" for key, value in headers) + "Connection: close\r\n\r\n"
                upstream.sendall(request.encode("latin-1"))
                self.close_connection = True
                while data := upstream.recv(65536):
                    started = True
                    self.connection.sendall(data)
        except (OSError, ValueError):
            self.close_connection = True
            if not started:
                try:
                    self.send_error(403, "Public web destination unavailable")
                except OSError:
                    pass

    do_HEAD = do_GET


@contextmanager
def public_proxy():
    # Wget never resolves target hosts itself: both HTTP and TLS CONNECT go here.
    with ThreadingHTTPServer(("127.0.0.1", 0), PublicProxy) as server:
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .05}, daemon=True)
        thread.start()
        try:
            yield "http://127.0.0.1:" + str(server.server_port)
        finally:
            server.shutdown()
            thread.join()


def fetch(url, maximum):
    with tempfile.TemporaryDirectory(prefix="algomini-download-") as temporary, public_proxy() as proxy:
        destination = Path(temporary) / "download"
        env = {**worker_environment(), "http_proxy": proxy, "https_proxy": proxy, "ftp_proxy": proxy, "no_proxy": ""}
        for _ in range(11):
            if urlsplit(url).scheme.lower() not in {"http", "https"}:
                raise ValueError("Dataset download failed (HTTP/HTTPS web links required)")
            # Follow each web redirect explicitly: wget's FTPS redirects can bypass proxies.
            argv = ["prlimit", f"--fsize={max(65536, maximum+1)}", "--", "wget", "--no-config", "--no-netrc",
                    "--no-cookies", "--no-hsts", "--no-verbose", "--server-response", "--max-redirect=0",
                    "--timeout=20", "--tries=2", "--execute=use_proxy=on", "--output-document=" + str(destination), "--", url]
            with (Path(temporary) / "stderr").open("w+b") as log:
                try:
                    result = subprocess.run(argv, stdout=subprocess.DEVNULL, stderr=log, cwd=temporary, env=env, timeout=60)
                except subprocess.TimeoutExpired:
                    raise ValueError("Dataset download timed out") from None
                log.seek(0)
                headers = log.read(65536)
            statuses = re.findall(rb"(?m)^[ \t]+HTTP/\S+\s+(\d{3})\b", headers)
            status = int(statuses[-1]) if statuses else None
            if status in {301, 302, 303, 307, 308}:
                locations = re.findall(rb"(?mi)^[ \t]+Location:[ \t]*(\S+)[ \t]*$", headers)
                if not locations:
                    raise ValueError("Dataset download failed (redirect has no Location)")
                url = urljoin(url, locations[-1].decode("latin-1"))
                continue
            if destination.is_file() and destination.stat().st_size > maximum:
                raise ValueError("Dataset download exceeded its byte limit")
            if result.returncode:
                detail = "HTTP " + str(status) if status else "wget exit " + str(result.returncode)
                raise ValueError("Dataset download failed (" + detail + ")")
            return destination.read_bytes()
        raise ValueError("Dataset download failed (too many redirects)")
