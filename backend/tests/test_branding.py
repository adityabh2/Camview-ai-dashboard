"""Organisation branding: name, subtitle and logo (Settings › Branding)."""

import base64

import db

PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
       b"\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xa7\x35\x81\x84\x00\x00\x00\x00IEND\xaeB`\x82")
SVG = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><rect width="10" height="10" fill="#36c"/></svg>'


def data_url(mime, raw):
    return f"data:{mime};base64,{base64.b64encode(raw).decode()}"


def test_defaults_signed_out(client):
    code, b = client.get("/api/branding")
    assert code == 200
    assert b == {"name": "CAMVIEW", "subtitle": "Command Center", "hasLogo": False, "logoVersion": None}
    r = client.c.get("/api/branding/logo")
    assert r.status_code == 404


def test_admin_updates_name_and_subtitle(admin, client):
    code, b = admin.put("/api/branding", {"name": "  Acme   Security ", "subtitle": "Operations Hub"})
    assert code == 200, b
    assert b["name"] == "Acme Security" and b["subtitle"] == "Operations Hub"
    code, b = client.get("/api/branding")                   # visible signed out (sign-in page)
    assert b["name"] == "Acme Security" and b["subtitle"] == "Operations Hub"
    row = db.one("SELECT new_value FROM audit_events WHERE action='settings.branding' ORDER BY id DESC LIMIT 1")
    assert row and "Acme Security" in row["new_value"]
    # empty resets to the default; too long is refused
    code, b = admin.put("/api/branding", {"name": ""})
    assert code == 200 and b["name"] == "CAMVIEW"
    code, _ = admin.put("/api/branding", {"name": "x" * 41})
    assert code == 400
    code, _ = admin.put("/api/branding", {"subtitle": "y" * 61})
    assert code == 400


def test_png_upload_served_back(admin, client):
    code, b = admin.put("/api/branding", {"logo": data_url("image/png", PNG)})
    assert code == 200, b
    assert b["hasLogo"] and b["logoVersion"]
    r = client.c.get(f"/api/branding/logo?v={b['logoVersion']}")
    assert r.status_code == 200
    assert r.mimetype == "image/png"
    assert r.data == PNG
    assert r.headers["Cache-Control"] == "public, max-age=86400"
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    # the audit row never holds the image bytes
    row = db.one("SELECT old_value, new_value FROM audit_events WHERE action='settings.branding' ORDER BY id DESC LIMIT 1")
    assert base64.b64encode(PNG).decode() not in (row["new_value"] or "")
    # remove
    code, b = admin.put("/api/branding", {"removeLogo": True})
    assert code == 200 and not b["hasLogo"] and b["logoVersion"] is None
    assert client.c.get("/api/branding/logo").status_code == 404


def test_oversize_and_wrong_type_rejected(admin):
    big = b"\x89PNG\r\n\x1a\n" + b"\x00" * (512 * 1024 + 1)
    code, b = admin.put("/api/branding", {"logo": data_url("image/png", big)})
    assert code in (400, 413) and b["error"] == "logo_too_large"
    code, _ = admin.put("/api/branding", {"logo": data_url("image/gif", b"GIF89a\x01\x00\x01\x00")})
    assert code == 400
    code, _ = admin.put("/api/branding", {"logo": data_url("text/html", b"<script>alert(1)</script>")})
    assert code == 400
    # declared PNG but actually JPEG / HTML
    code, _ = admin.put("/api/branding", {"logo": data_url("image/png", b"\xff\xd8\xff\xe0rest")})
    assert code == 400
    code, _ = admin.put("/api/branding", {"logo": data_url("image/jpeg", b"<html></html>")})
    assert code == 400
    code, _ = admin.put("/api/branding", {"logo": "data:image/png;base64,@@@notbase64"})
    assert code == 400
    # a valid JPEG and WEBP header pass the magic-byte check
    code, _ = admin.put("/api/branding", {"logo": data_url("image/jpeg", b"\xff\xd8\xff\xe0\x00\x10JFIF")})
    assert code == 200
    code, b = admin.put("/api/branding", {"logo": data_url("image/webp", b"RIFF\x10\x00\x00\x00WEBPVP8 ")})
    assert code == 200 and b["hasLogo"]


def test_svg_active_content_rejected(admin):
    bad = [
        b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
        b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"><rect/></svg>',
        b'<svg xmlns="http://www.w3.org/2000/svg"><rect onclick="x()"/></svg>',
        b'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">'
        b'<image xlink:href="https://evil.example/x.png"/></svg>',
        b'<svg xmlns="http://www.w3.org/2000/svg"><a href="javascript:alert(1)"><rect/></a></svg>',
        b'<svg xmlns="http://www.w3.org/2000/svg"><foreignObject><div/></foreignObject></svg>',
        b'<!DOCTYPE svg [<!ENTITY x "y">]><svg xmlns="http://www.w3.org/2000/svg">&x;</svg>',
        b'<html><body/></html>',
        b'<svg><unclosed></svg>',
    ]
    for raw in bad:
        code, b = admin.put("/api/branding", {"logo": data_url("image/svg+xml", raw)})
        assert code == 400, (raw, b)
    assert db.get_setting("branding") is None                # nothing was stored


def test_svg_served_with_sandbox_csp(admin, client):
    ok = SVG.replace(b"<rect", b'<use href="#r"/><rect id="r"')
    code, b = admin.put("/api/branding", {"logo": data_url("image/svg+xml", ok), "name": "Acme"})
    assert code == 200, b
    r = client.c.get("/api/branding/logo")
    assert r.status_code == 200 and r.mimetype == "image/svg+xml"
    csp = r.headers["Content-Security-Policy"]
    assert "sandbox" in csp and "default-src 'none'" in csp and "script-src" not in csp
    assert r.headers["X-Content-Type-Options"] == "nosniff"


def test_permissions(supervisor, operator, client_a, client):
    for c in (supervisor, operator):
        code, _ = c.put("/api/branding", {"name": "Nope"})
        assert code == 403
    code, _ = client_a.put("/api/branding", {"name": "Nope"})
    assert code == 404
    code, _ = client.put("/api/branding", {"name": "Nope"})
    assert code == 401
    code, b = client_a.get("/api/branding")                 # everybody may read it
    assert code == 200 and b["name"] == "CAMVIEW"


def test_put_requires_json(admin):
    r = admin.c.put("/api/branding", data="name=x", content_type="application/x-www-form-urlencoded")
    assert r.status_code == 415
