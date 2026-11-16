import hashlib
import hmac
import time
import re
import zipfile
import zlib
from io import BytesIO

from fastapi.testclient import TestClient
from openpyxl import Workbook
from pypdf import PdfWriter
from pypdf.generic import EncodedStreamObject, NameObject
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app.config import settings
from app.main import app
from app.services.auth import SESSION_COOKIE_NAME, create_admin_session_token, get_secret_key


def test_machine_credential_cannot_create_human_session_or_reuse_old_cookie(client, monkeypatch):
    machine_credential = settings.ADMIN_RESET_TOKEN
    for payload in ({"password": machine_credential}, {"token": machine_credential}):
        refused = client.post("/api/auth/login", json=payload)
        assert refused.status_code == 401 and "set-cookie" not in refused.headers
    monkeypatch.setattr(settings, "ADMIN_LOGIN_PASSWORD", machine_credential)
    assert client.post("/api/auth/login", json={"password": machine_credential}).status_code == 403

    legacy = f"admin:{int(time.time())}:old-session"
    signature = hmac.new(get_secret_key().encode(), legacy.encode(), hashlib.sha256).hexdigest()
    client.cookies.set(SESSION_COOKIE_NAME, f"{legacy}.{signature}")
    assert client.get("/api/surveys/dashboard/summary").status_code == 401


def test_cookie_csrf_always_requires_valid_origin_or_real_header_auth(client):
    token = create_admin_session_token()
    client.cookies.set(SESSION_COOKIE_NAME, token)
    client.headers.pop("Origin", None)
    reset = "/api/surveys/reset-survey-data"
    for headers in ({}, {"X-Admin-Session-Token": "fake"}, {"Origin": "http://localhost:9000"},
                    {"Origin": "https://localhost"}, {"Origin": "null", "Referer": "http://localhost/"}):
        assert client.post(reset, headers=headers).status_code == 403
    assert client.post("/api/auth/logout").status_code == 403
    assert client.post(reset, headers={"Authorization": f"Bearer {token}"}).status_code == 200
    assert client.post("/api/auth/logout", headers={"Referer": "http://localhost/dashboard"}).status_code == 200


def test_trusted_proxy_normalizes_scheme_and_client_but_ignores_forwarded_host(monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "ADMIN_ALLOWED_ORIGINS", ["https://survey.example.test"])
    proxy = ProxyHeadersMiddleware(app, trusted_hosts=["10.1.0.2"])
    trusted = TestClient(proxy, base_url="http://app.internal:8000", client=("10.1.0.2", 50000))
    direct = TestClient(proxy, base_url="http://app.internal:8000", client=("203.0.113.100", 50000))
    cookie = f"{SESSION_COOKIE_NAME}={create_admin_session_token()}"
    headers = {"Origin": "https://survey.example.test", "X-Forwarded-Proto": "https", "Cookie": cookie,
               "X-Forwarded-Host": "survey.example.test"}
    path = "/api/surveys/upload-qq"
    content = {"file": ("survey.txt", b"A long introduction. Q1. What is your age? Please respond accurately.")}
    assert trusted.post(path, headers=headers, files=content).status_code == 200
    assert direct.post(path, headers=headers, files=content).status_code == 403
    spoofed = {**headers, "Origin": "https://evil.example.test", "X-Forwarded-Host": "evil.example.test"}
    assert trusted.post(path, headers=spoofed, files=content).status_code == 403

    headers.pop("Cookie")
    for i in range(6):
        # The trusted proxy added the nearest hop on the right. The left entry can be user-controlled.
        forwarded = {**headers, "X-Forwarded-For": f"192.0.2.{i+1}, 203.0.113.200"}
        response = trusted.post("/api/auth/login", headers=forwarded, json={"password": "wrong"})
        assert response.status_code == (429 if i == 5 else 401)
    other = {**headers, "X-Forwarded-For": "203.0.113.201"}
    response = trusted.post("/api/auth/login", headers=other, json={"password": settings.ADMIN_LOGIN_PASSWORD})
    assert response.status_code == 200 and "Secure" in response.headers["set-cookie"]


def test_public_demo_reset_rejects_remote_peers_and_nonloopback_hosts(client):
    reset = "/api/dashboard/reset-data"
    remote = TestClient(app, base_url="http://localhost", client=("203.0.113.25", 50000))
    assert remote.post(reset, headers={"X-Forwarded-For": "127.0.0.1"}).status_code == 403
    assert client.post(reset, headers={"Host": "attacker.example.test"}).status_code == 403


def test_sparse_workbook_dimension_and_actual_cells_are_bounded(auth_client, tmp_path):
    book = Workbook()
    book.active.append(["ResponseId", "Q1"])
    book.active.append(["A", "Yes"])
    book.active.cell(row=20_000, column=1000, value="Far data cell")
    source = BytesIO()
    book.save(source)
    assert len(source.getvalue()) < 8_000
    rewritten = BytesIO()
    with zipfile.ZipFile(source) as original, zipfile.ZipFile(rewritten, "w", zipfile.ZIP_DEFLATED) as adjusted:
        for name in original.namelist():
            data = original.read(name)
            if name.endswith("/sheet1.xml"):
                data = re.sub(rb'<dimension ref="[^"]+"', b'<dimension ref="A1:B2"', data)
            adjusted.writestr(name, data)
    for content in (source.getvalue(), rewritten.getvalue()):
        for endpoint in ("upload-raw", "upload-qq"):
            result = auth_client.post(f"/api/surveys/{endpoint}", files={"file": ("sparse.xlsx", content)})
            assert result.status_code == 422 and "cell processing limit" in result.json()["detail"]
    assert not list((tmp_path / "surveys").glob("*"))


def test_pdf_upload_limits_pages_and_compressed_streams(auth_client):
    too_many_pages = PdfWriter()
    for _ in range(101):
        too_many_pages.add_blank_page(72, 72)
    oversized_stream = PdfWriter()
    page = oversized_stream.add_blank_page(72, 72)
    encoded = EncodedStreamObject()
    encoded._data = zlib.compress(b" " * (5 * 1024 * 1024))
    encoded[NameObject("/Filter")] = NameObject("/FlateDecode")
    page[NameObject("/Contents")] = oversized_stream._add_object(encoded)
    for writer in (too_many_pages, oversized_stream):
        buffer = BytesIO()
        writer.write(buffer)
        resp = auth_client.post("/api/surveys/upload-qq", files={"file": ("bounded.pdf", buffer.getvalue())})
        assert resp.status_code == 422, resp.text
        assert "PDF exceeds" in resp.json()["detail"]
