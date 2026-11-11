"""HTTP side: CAS login, the student profile, and GETs of the portal's JSON
API, one method per thing the tools need."""

from __future__ import annotations

import logging
import re
import threading
import time
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from . import parsers, vpn
from .config import Settings

log = logging.getLogger(__name__)

USER_AGENT = "uth-sis-mcp/0.1 (https://github.com/fotispetrou/uth-sis-mcp)"
# the university blocks an account after repeated failed logins, so after a
# rejected password we stop trying for a while instead of failing on every call
LOGIN_RETRY_DELAY = 5 * 60

# Opening the declaration editor turns a submitted declaration back into a
# draft that has to be submitted again, so this client can only reach these
# read-only endpoints (the same GETs the portal's own list pages make) and
# never sends anything but GET.
ALLOWED = [re.compile(p) for p in (
    r"api/person/profiles",
    r"api/general/grading_scale",
    r"feign/student/student_data",
    r"feign/student/grades/(all|diploma|average_student_course_grades)",
    r"feign/student/(registration_periods_all|active_registration_periods)",
    r"feign/student/student_registration/registration_period/[0-9A-Fa-f-]+",
    r"feign/student/web_student_registration/registration_period/[0-9A-Fa-f-]+(/history)?",
    r"feign/student/program_courses",
    r"feign/student/announcements",
)]


class SisError(Exception):
    pass


class LoginError(SisError):
    pass


def csrf_header(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    token, name = soup.find("meta", attrs={"name": "_csrf"}), soup.find("meta", attrs={"name": "_csrf_header"})
    return {name["content"]: token["content"]} if token and name else {}


def cas_error(html: str) -> str | None:
    node = BeautifulSoup(html, "html.parser").select_one("#msg, .errors, #status")
    return node.get_text(" ", strip=True) if node else None


class SisClient:
    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None):
        self.settings = settings
        self.http = httpx.Client(base_url=settings.base_url, follow_redirects=True, transport=transport,
                                 timeout=settings.timeout, headers={"User-Agent": USER_AGENT})
        self.logged_in = False
        self._csrf: dict = {}
        self._retry_login_at = 0.0
        self._lock = threading.RLock()
        self._profiles: list[dict] | None = None
        self._profile: dict | None = None
        self._scales: dict | None = None

    def close(self) -> None:
        self.http.close()

    # -- session ------------------------------------------------------------

    def _at_cas(self, r: httpx.Response) -> bool:
        """The login form lives on cas.uth.gr; protected pages bounce there."""
        return r.url.host != urlparse(self.settings.base_url).hostname and r.url.path.endswith("/login")

    def login(self) -> None:
        with self._lock:
            if time.monotonic() < self._retry_login_at:
                raise LoginError("CAS rejected the last login; not retrying for a few minutes so the "
                                 "account does not get blocked. Check UTHSIS_USERNAME/UTHSIS_PASSWORD.")
            vpn.ensure(self.settings)
            r = self.http.get("/")
            if self._at_cas(r):
                soup = BeautifulSoup(r.text, "html.parser")
                form = soup.find("form", id="fm1") or soup.find("form")
                if form is None:
                    raise SisError("the CAS login page has no form")
                data = {i["name"]: i.get("value", "") for i in form.find_all("input")
                        if i.get("name") and i.get("type") != "submit"}
                data.update(username=self.settings.username, password=self.settings.password)
                r = self.http.post(urljoin(str(r.url), form.get("action") or ""), data=data)
                if self._at_cas(r):
                    self._retry_login_at = time.monotonic() + LOGIN_RETRY_DELAY
                    raise LoginError(cas_error(r.text) or "CAS rejected the login")
            r.raise_for_status()
            self._csrf = csrf_header(r.text)
            self.logged_in = True
            log.info("logged in to %s as %s", self.settings.base_url, self.settings.username)

    def get(self, path: str, _retry: bool = True):
        if not any(p.fullmatch(path) for p in ALLOWED):
            raise SisError(f"refusing to request {path}: not one of the read-only endpoints")
        if not self.logged_in:
            self.login()
        headers = {"Accept": "application/json", "X-Requested-With": "XMLHttpRequest", **self._csrf}
        if self._profile:
            headers["X-Profile"] = self._profile["id"]
        try:
            r = self.http.get(path, headers=headers)
        except httpx.TransportError:
            if not _retry:
                raise
            # most likely the VPN dropped; ensure() brings it back
            vpn.ensure(self.settings)
            return self.get(path, _retry=False)
        expired = (self._at_cas(r) or r.headers.get("Access-Status-Custom-Header") == "UNAUTHORIZED"
                   or "json" not in r.headers.get("content-type", ""))
        if expired:
            if not _retry:
                raise SisError(f"{path} did not return JSON")
            # session timed out server-side; log in again, once
            self.logged_in = False
            return self.get(path, _retry=False)
        if r.status_code >= 400:
            try:
                message = r.json().get("message")
            except (ValueError, AttributeError):
                message = None
            raise SisError(f"{path}: HTTP {r.status_code} {message or ''}".strip())
        return r.json() if r.content else None

    # -- profile and reference data -------------------------------------------

    def profile(self) -> dict:
        with self._lock:
            if self._profile is None:
                self._profiles = (self.get("api/person/profiles") or {}).get("studentProfiles") or []
                self._profile = parsers.pick_profile(self._profiles, self.settings.profile)
            return self._profile

    def scales(self) -> dict:
        if self._scales is None:
            self._scales = {str(s["id"]): s for s in self.get("api/general/grading_scale") or []}
        return self._scales

    # -- data ------------------------------------------------------------------

    def student(self) -> dict:
        profile = self.profile()
        return parsers.student(self.get("feign/student/student_data") or {}, profile, self._profiles or [])

    def _diploma(self) -> list[dict]:
        self.profile()
        return self.get("feign/student/grades/diploma") or []

    def transcript(self, only_passed: bool = False) -> list[dict]:
        return parsers.transcript(self._diploma(), self.scales(), only_passed)

    def progress(self) -> dict:
        diploma = self._diploma()
        average = self.get("feign/student/grades/average_student_course_grades")
        return parsers.progress(diploma, average, self.scales())

    def attempts(self, course: str | None = None, exam_period: str | None = None) -> list[dict]:
        self.profile()
        return parsers.attempts(self.get("feign/student/grades/all") or [], self.scales(), course, exam_period)

    def curriculum(self, semester: int | None = None) -> list[dict]:
        diploma = self._diploma()
        program = self.get("feign/student/program_courses") or {}
        return parsers.curriculum(program, diploma, self.scales(), semester)

    def _periods(self) -> list[dict]:
        self.profile()
        return self.get("feign/student/registration_periods_all") or []

    def _status(self, period_id: str) -> tuple[dict, list[dict]]:
        web = self.get(f"feign/student/web_student_registration/registration_period/{period_id}") or {}
        official = self.get(f"feign/student/student_registration/registration_period/{period_id}") or []
        return parsers.declaration(web, official), parsers.declared_courses(official)

    def periods(self) -> list[dict]:
        rows = []
        for p in self._periods():
            row = parsers.period_row(p)
            if row["open_now"]:
                decl, courses = self._status(p["id"])
                row.update(declaration=decl["status"], declared_courses=len(courses) or len(decl["courses"]))
            rows.append(row)
        return sorted(rows, key=lambda r: r["open_from"] or "", reverse=True)

    def registration(self, period: str | None = None) -> dict:
        p = parsers.pick_period(self._periods(), period)
        decl, courses = self._status(p["id"])
        row = parsers.period_row(p)
        return {
            "period": row["title"], "open_now": row["open_now"], "open_from": row["open_from"],
            "open_until": row["open_until"], "declaration": decl["status"],
            "courses": courses, "ects": sum(c["ects"] or 0 for c in courses),
            "web_declaration_courses": decl["courses"],
        }

    def announcements(self) -> list[dict]:
        self.profile()
        return [parsers.announcement_row(a) for a in self.get("feign/student/announcements") or []]
