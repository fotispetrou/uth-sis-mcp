"""SisClient against a stand-in for sis-web.uth.gr and cas.uth.gr built on
httpx.MockTransport: the CAS redirect dance, the X-Profile header the API
needs, session expiry, and the guard that keeps the client away from the
declaration editor."""

import json
from urllib.parse import parse_qs

import httpx
import pytest

from uth_sis_mcp import client as client_mod
from uth_sis_mcp import vpn
from uth_sis_mcp.client import LoginError, SisClient, SisError
from uth_sis_mcp.config import Settings

CAS_FORM = """<html><body><form id="fm1" action="/login?service=https%3A%2F%2Fsis-web.uth.gr%2Flogin%2Fcas" method="post">
<input name="username"/><input name="password" type="password"/>
<input type="hidden" name="lt" value="LT-1"/><input type="hidden" name="execution" value="e1s1"/>
<input type="hidden" name="_eventId" value="submit"/><input name="submitForm" type="submit" value="Login"/>
</form>{error}</body></html>"""
HOME = """<html><head><meta name="_csrf" content="tok"/><meta name="_csrf_header" content="X-CSRF-TOKEN"/>
</head><body>Πύλη Φοιτητολογίου</body></html>"""
PROFILES = {"studentProfiles": [{"id": "P1", "departmentCode": "610", "departmentTitle": "ΤΜΗΜΑ ΨΗΦΙΑΚΩΝ ΣΥΣΤΗΜΑΤΩΝ",
                                 "studentStatusTitle": "Ενεργός", "lastSyllabus": 2026}]}
STUDENT = {"firstName": "ΓΕΩΡΓΙΟΣ", "lastName": "ΠΑΠΑΔΟΠΟΥΛΟΣ", "studentInstNo": "3120000", "lastSemester": 9}


class FakeSite:
    def __init__(self):
        self.sessions = set()
        self.requests = []
        self.expire_next_api_call = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append((request.method, request.url.host, request.url.path, dict(request.headers)))
        host, path = request.url.host, request.url.path
        if host == "cas.uth.gr":
            if request.method == "GET":
                return httpx.Response(200, html=CAS_FORM.format(error=""))
            form = parse_qs(request.content.decode())
            if form.get("username") == ["student"] and form.get("password") == ["secret"] and form.get("lt") == ["LT-1"]:
                return httpx.Response(302, headers={"Location": "https://sis-web.uth.gr/login/cas?ticket=ST-1"})
            return httpx.Response(200, html=CAS_FORM.format(
                error='<div id="msg" class="errors">Λάθος όνομα χρήστη ή κωδικός.</div>'))
        session = request.headers.get("cookie", "")
        if path == "/login/cas":
            self.sessions.add("S1")
            return httpx.Response(302, headers={"Location": "/", "Set-Cookie": "JSESSIONID=S1; Path=/"})
        logged_in = "JSESSIONID=S1" in session and "S1" in self.sessions
        if not logged_in:
            return httpx.Response(302, headers={"Location": "https://cas.uth.gr/login?service=x"})
        if path == "/":
            return httpx.Response(200, html=HOME)
        if self.expire_next_api_call:
            self.expire_next_api_call = False
            self.sessions.discard("S1")
            return httpx.Response(302, headers={"Location": "https://cas.uth.gr/login?service=x"})
        if path == "/api/person/profiles":
            return httpx.Response(200, json=PROFILES)
        if request.headers.get("X-Profile") != "P1":
            return httpx.Response(200, html=HOME)  # what the real site does without a profile
        if path == "/feign/student/student_data":
            return httpx.Response(200, json=STUDENT)
        if path == "/feign/student/registration_periods_all":
            return httpx.Response(200, json=[])
        return httpx.Response(404, json={"status": 404, "message": "Not Found"})


def make(monkeypatch, password="secret"):
    monkeypatch.setattr(client_mod.vpn, "ensure", lambda settings: None)
    site = FakeSite()
    c = SisClient(Settings(username="student", password=password), transport=httpx.MockTransport(site))
    return c, site


def test_login_and_student(monkeypatch):
    c, site = make(monkeypatch)
    s = c.student()
    assert s["name"] == "ΓΕΩΡΓΙΟΣ ΠΑΠΑΔΟΠΟΥΛΟΣ" and s["student_id"] == "3120000"
    assert s["department"] is None and s["profile"] == "ΤΜΗΜΑ ΨΗΦΙΑΚΩΝ ΣΥΣΤΗΜΑΤΩΝ"
    api = [r for r in site.requests if r[2].startswith("/feign/")]
    assert api and all(h.get("x-profile") == "P1" and h.get("x-csrf-token") == "tok" for _, _, _, h in api)
    # the CAS form is the only thing ever posted
    assert [(m, host) for m, host, _, _ in site.requests if m != "GET"] == [("POST", "cas.uth.gr")]


def test_wrong_password_is_not_retried_right_away(monkeypatch):
    c, site = make(monkeypatch, password="wrong")
    with pytest.raises(LoginError, match="Λάθος όνομα χρήστη"):
        c.student()
    with pytest.raises(LoginError, match="not retrying"):
        c.student()
    assert sum(1 for m, *_ in site.requests if m == "POST") == 1


def test_expired_session_logs_in_again(monkeypatch):
    c, site = make(monkeypatch)
    c.student()
    site.expire_next_api_call = True
    assert c.student()["student_id"] == "3120000"
    assert sum(1 for m, *_ in site.requests if m == "POST") == 2


@pytest.mark.parametrize("path", [
    "feign/student/web_student_registration/registration_period/0A1B2C3D/draft",
    "feign/student/web_student_registration/registration_period/0A1B2C3D/submit_for_student",
    "feign/student/web_student_registration/registration_period/0A1B2C3D/validate",
    "feign/student/web_student_registration/registration_period/0A1B2C3D/preview",
    "student/student_registration/edit/0A1B2C3D",
    "feign/student/certificate/create_request",
])
def test_declaration_editor_is_out_of_reach(monkeypatch, path):
    c, site = make(monkeypatch)
    with pytest.raises(SisError, match="not one of the read-only endpoints"):
        c.get(path)
    assert site.requests == []


def test_api_errors_carry_the_message(monkeypatch):
    c, _ = make(monkeypatch)
    c.profile()
    with pytest.raises(SisError, match="HTTP 404 Not Found"):
        c.get("feign/student/announcements")


def test_vpn_is_connected_when_the_site_does_not_answer(monkeypatch, tmp_path):
    gui = tmp_path / "openvpn-gui.exe"
    gui.write_bytes(b"")
    answers = iter([False, False, True])
    calls = []
    monkeypatch.setattr(vpn, "reachable", lambda url, timeout=3.0: next(answers))
    monkeypatch.setattr(vpn, "connect", lambda settings: calls.append(settings.vpn_profile))
    monkeypatch.setattr(vpn.time, "sleep", lambda s: None)
    monkeypatch.setattr(vpn.os, "name", "nt")
    vpn.ensure(Settings(username="u", password="p", openvpn_gui=gui))
    assert calls == ["UTH Intranet"]


def test_vpn_off_gives_a_clear_error(monkeypatch):
    monkeypatch.setattr(vpn, "reachable", lambda url, timeout=3.0: False)
    with pytest.raises(vpn.VpnError, match="UTH VPN"):
        vpn.ensure(Settings(username="u", password="p", auto_vpn=False))


def test_tools_over_mcp(monkeypatch):
    from mcp.client import Client

    from uth_sis_mcp import server

    c, _ = make(monkeypatch)
    monkeypatch.setattr(server, "_client", c)

    async def run():
        async with Client(server.server) as mcp:
            tools = await mcp.list_tools()
            who = await mcp.call_tool("whoami", {})
            periods = await mcp.call_tool("get_registration_periods", {})
            return tools, who, periods

    import asyncio
    tools, who, periods = asyncio.run(run())
    assert {t.name for t in tools.tools} == {"whoami", "get_progress", "get_transcript", "get_grades",
                                             "get_curriculum", "get_registration_periods", "get_registration",
                                             "get_announcements"}
    assert all(t.annotations.read_only_hint for t in tools.tools)
    assert not who.is_error and json.loads(who.content[0].text)["student_id"] == "3120000"
    assert not periods.is_error
