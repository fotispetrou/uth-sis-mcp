"""MCP tool definitions. Every tool is a thin wrapper over SisClient so the
server stays easy to read; the parsing lives in parsers.py."""

from __future__ import annotations

import functools

import httpx
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from .client import SisClient, SisError
from .config import ConfigError, Settings
from .vpn import VpnError

INSTRUCTIONS = """Read-only access to a University of Thessaly student's record in SIS-Web
(Ηλεκτρονική Γραμματεία): grades, transcript, progress and ECTS, the
curriculum, course registration periods and what is declared in them.

Nothing can be changed through this server: course declarations
(δηλώσεις μαθημάτων) are made by the student on https://sis-web.uth.gr.
SIS-Web is only reachable through the university VPN, which the server
connects on its own when needed. Grades are shown as on the site (0-10)."""

server = MCPServer("uth-sis", instructions=INSTRUCTIONS)
READS = ToolAnnotations(read_only_hint=True, open_world_hint=True)

_client: SisClient | None = None


def client() -> SisClient:
    global _client
    if _client is None:
        _client = SisClient(Settings.from_env())
    return _client


def tool(fn):
    """Register fn as a tool and turn the failures we expect (bad login, VPN,
    network) into ToolError so the model sees the message instead of a
    generic 'error executing tool'."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (SisError, ConfigError, VpnError, ValueError, httpx.HTTPError) as exc:
            raise ToolError(f"{type(exc).__name__}: {exc}") from exc

    return server.tool(annotations=READS)(wrapper)


@tool
def whoami() -> dict:
    """The student: name, student id (ΑΜ), department, programme, status and
    current semester. Also checks that the login works."""
    return client().student()


@tool
def get_progress() -> dict:
    """Degree progress: grade average, passed courses and ECTS, passed ECTS
    in semesters 1-6, and counts per curriculum semester."""
    return client().progress()


@tool
def get_transcript(only_passed: bool = False) -> list[dict]:
    """The transcript ("Η Καρτέλα μου"): every course registered for the
    degree with its grade, ECTS, semester and whether it is passed."""
    return client().transcript(only_passed)


@tool
def get_grades(course: str | None = None, exam_period: str | None = None) -> list[dict]:
    """Every graded exam attempt, newest first. Filter by course title or
    code (case and accents do not matter) and/or exam period, e.g. 'ΣΕΠΤ'."""
    return client().attempts(course, exam_period)


@tool
def get_curriculum(semester: int | None = None) -> list[dict]:
    """The study programme's courses (code, semester, compulsory/elective,
    ECTS) with the student's standing on each: passed, failed, registered
    without a grade, or not taken."""
    return client().curriculum(semester)


@tool
def get_registration_periods() -> list[dict]:
    """Course registration periods (δηλώσεις μαθημάτων) with their dates. For
    the ones open now, also whether a declaration has been made and its
    status."""
    return client().periods()


@tool
def get_registration(period: str | None = None) -> dict:
    """What is declared for a registration period: the status of the web
    declaration and the courses registered. `period` is part of its title
    (e.g. '2025-2026 ΕΑΡ' or 'ΕΜΒΟΛΙΜΗ'); default is the main period open
    now, or the latest one."""
    return client().registration(period)


@tool
def get_announcements() -> list[dict]:
    """Announcements posted in SIS-Web for the student's department."""
    return client().announcements()


def run() -> None:
    server.run()
