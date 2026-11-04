from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import socket
import subprocess
import threading
import zipfile

import pytest

from algomini.cloud import contracts, downloads
from algomini.common import digest
from test_cloud_contracts import IMAGE, ROOT, SHA


PAYLOAD = b"wget redirect payload\x00\xff"


@contextmanager
def web_server(handler):
    class Server(ThreadingHTTPServer):
        connections = 0
        def get_request(self):
            result = super().get_request()
            self.connections += 1
            return result
    with Server(("127.0.0.1", 0), handler) as server:
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .02}, daemon=True)
        thread.start()
        try:
            yield server
        finally:
            server.shutdown(); thread.join()


class Source(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        if self.path == "/redirect":
            self.send_response(302); self.send_header("Location", "http://another.example/file?signature=example"); self.end_headers()
        elif self.path == "/loop":
            self.send_response(302); self.send_header("Location", "/loop"); self.end_headers()
        elif self.path == "/missing":
            self.send_error(404)
        elif self.path.startswith("/private"):
            scheme = self.path.removeprefix("/private-")
            self.send_response(302)
            self.send_header("Location", f"{scheme}://127.0.0.1:{self.server.server_port}/secret")
            self.end_headers()
        else:
            if self.path == "/secret":
                self.server.secret_read = True
            payload = self.server.payload
            self.send_response(200); self.send_header("Content-Length", str(len(payload))); self.end_headers()
            try:
                self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError):
                pass


@pytest.fixture
def download_site(monkeypatch):
    actual_connect = downloads.public_connection
    with web_server(Source) as server:
        server.payload = PAYLOAD
        server.secret_read = False
        def connect(host, port):
            if host in {"public.example", "another.example"}:
                return socket.create_connection(("127.0.0.1", server.server_port), timeout=5)
            return actual_connect(host, port)
        monkeypatch.setattr(downloads, "public_connection", connect)
        yield server


def test_real_wget_follows_302_across_hosts_and_accepts_queries(download_site):
    assert contracts.download("http://public.example/redirect", {}, 1024, digest(PAYLOAD)) == PAYLOAD


@pytest.mark.parametrize("path,maximum,error", [("/file", 3, "byte limit"), ("/missing", 1024, "HTTP 404"), ("/loop", 1024, "download failed")])
def test_download_limits_and_failures_remain_bounded(download_site, path, maximum, error):
    with pytest.raises(ValueError, match=error):
        contracts.download("http://public.example"+path, {}, maximum)


def test_expected_hash_and_empty_files_are_checked(download_site):
    with pytest.raises(ValueError, match="SHA-256"):
        contracts.download("http://public.example/file", {}, 1024, "a"*64)
    download_site.payload = b""
    assert contracts.download("http://public.example/file", {}, 0, digest(b"")) == b""


def test_opaque_zip_link_with_query_and_fragment_builds_manifest(download_site, tmp_path):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("train.txt", b"larger training sample")
        archive.writestr("test.txt", b"test")
    download_site.payload = stream.getvalue()
    url = "http://public.example/download?file=books&signature=example#download"
    issue = {"number": 7, "body": f"### Plugin name\n\ntext-demo\n\n### Dataset archive URL\n\n{url}"}
    request = contracts.snapshot(ROOT, issue, "akaliutau/algomini", SHA, IMAGE, staging=tmp_path)
    assert request["dataset_manifest_url"] == url
    assert (tmp_path/"dataset/train.bin").read_bytes() == b"larger training sample"
    assert (tmp_path/"dataset/test.bin").read_bytes() == b"test"


@pytest.mark.parametrize("scheme", ["http", "https", "ftp", "ftps"])
def test_redirects_cannot_bypass_the_public_network_boundary(download_site, scheme):
    with pytest.raises(ValueError, match="download failed"):
        contracts.download("http://public.example/private-"+scheme, {}, 1024)
    assert not download_site.secret_read
    assert download_site.connections == 1


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "168.63.129.16", "100.64.0.1", "::1", "fe80::1", "224.0.0.1"])
def test_private_addresses_never_reach_socket_connect(monkeypatch, address):
    monkeypatch.setattr(downloads.socket, "getaddrinfo", lambda *_a, **_k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 80))])
    monkeypatch.setattr(downloads.socket, "socket", lambda *_: pytest.fail("Private destination reached socket creation"))
    with pytest.raises(OSError, match="Private/internal"):
        downloads.public_connection("anything.example", 80)


def test_dns_is_resolved_once_and_the_checked_ip_is_used(monkeypatch):
    calls = []
    def resolve(*args, **kwargs):
        calls.append("resolve")
        assert calls.count("resolve") == 1
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))]
    class Connection:
        def settimeout(self, _): pass
        def connect(self, address): calls.append(address)
    monkeypatch.setattr(downloads.socket, "getaddrinfo", resolve)
    monkeypatch.setattr(downloads.socket, "socket", lambda *_: Connection())
    downloads.public_connection("changing-dns.example", 443)
    assert calls == ["resolve", ("8.8.8.8", 443)]


def test_wget_does_not_inherit_provider_or_repository_credentials(download_site, monkeypatch):
    monkeypatch.setenv("META_API_KEY", "fake-provider-secret")
    monkeypatch.setenv("GH_TOKEN", "fake-repository-secret")
    run = subprocess.run
    def checked(argv, **kwargs):
        assert "wget" in argv and "--no-netrc" in argv and "--no-config" in argv
        assert "--no-check-certificate" not in argv
        assert "META_API_KEY" not in kwargs["env"] and "GH_TOKEN" not in kwargs["env"]
        return run(argv, **kwargs)
    monkeypatch.setattr(downloads.subprocess, "run", checked)
    assert contracts.download("http://public.example/file", {}, 1024) == PAYLOAD


def test_wget_timeout_is_reported_without_leaking_the_link(monkeypatch):
    def timeout(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, 60)
    monkeypatch.setattr(downloads.subprocess, "run", timeout)
    with pytest.raises(ValueError, match="timed out") as error:
        contracts.download("https://example.com/file?signature=private-example", {}, 1024)
    assert "private-example" not in str(error.value)


def test_mixed_public_private_dns_answers_never_open_a_socket(monkeypatch):
    answers = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 80)) for address in ("8.8.8.8", "127.0.0.1")]
    monkeypatch.setattr(downloads.socket, "getaddrinfo", lambda *_a, **_k: answers)
    monkeypatch.setattr(downloads.socket, "socket", lambda *_: pytest.fail("Mixed private DNS reached socket creation"))
    with pytest.raises(OSError, match="Private/internal"):
        downloads.public_connection("mixed.example", 80)


def test_private_initial_urls_do_not_bypass_the_proxy(download_site):
    with pytest.raises(ValueError, match="download failed"):
        contracts.download(f"http://127.0.0.1:{download_site.server_port}/secret", {}, 1024)
    assert download_site.connections == 0
