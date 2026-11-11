"""Turn the JSON the SIS-Web (ILYDA) student portal serves into plain dicts.
Nothing here does I/O, so it is all testable with fixtures.

Grades are stored as a fraction of the scale ("base grade", 0..1) and shown
through the record's grading scale, the same way the portal's own
fromBaseGrade() does it."""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal

from bs4 import BeautifulSoup

# webStudentRegistrationStatusEnum in the portal's constant_variables.js,
# labels from its ily_locales.js
DECLARATION_STATUS = {
    "0": "Πρόχειρη (δεν έχει υποβληθεί)",
    "3": "Υποβληθείσα",
    "1": "Οριστικοποιημένη από τη γραμματεία",
    "2": "Εκπρόθεσμη",
}
NO_DECLARATION = "Καμία δήλωση"
BY_SECRETARIAT = "Χωρίς ηλεκτρονική δήλωση, τα μαθήματα τα καταχώρισε η γραμματεία"


def fold(s: str) -> str:
    """Case- and accent-insensitive form for matching course names."""
    s = unicodedata.normalize("NFD", (s or "").casefold())
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def title_of(value) -> str | None:
    return value.get("title") if isinstance(value, dict) else None


# -- grades ---------------------------------------------------------------------

def _round_to_step(value: float, step: float) -> Decimal:
    q = Decimal(str(step))
    return (Decimal(str(value)) / q).quantize(Decimal(1), rounding=ROUND_HALF_UP) * q


def display_grade(base, scale: dict | None, scales: dict[str, dict] | None = None):
    """Base grade (0..1) to what the portal shows: a number on numeric scales,
    the matching label (e.g. Επιτυχώς) otherwise. None when there is no grade."""
    if base is None or not scale:
        return None
    full = (scales or {}).get(str(scale.get("id")), {}) | {k: v for k, v in scale.items() if v is not None}
    lo, hi, step = full.get("minNumber"), full.get("maxNumber"), full.get("step")
    if lo is not None and hi is not None and step:
        value = Decimal(str(lo)) + _round_to_step((hi - lo) * base, step)
        value = min(value, Decimal(str(hi)))
        return float(value.normalize()) if value != value.to_integral() else int(value)
    for g in sorted(full.get("grades") or [], key=lambda g: g.get("val", 0), reverse=True):
        if not g.get("val", 0) > base:
            return g.get("grade")
    return None


def semester_no(record: dict) -> int | None:
    """Curriculum semester of the course (Χ/Ε, free electives, is -1 → None)."""
    order = (record.get("semesterId") or {}).get("sortOrder")
    return order if isinstance(order, int) and order > 0 else None


def course_row(record: dict, scales: dict | None = None) -> dict:
    """One course of the transcript ("Η Καρτέλα μου")."""
    period = record.get("examPeriodId") or {}
    return {
        "code": record.get("courseCode"),
        "title": record.get("title"),
        "semester": semester_no(record),
        "type": title_of(record.get("typeId")),
        "ects": record.get("ects"),
        "grade": display_grade(record.get("grade"), record.get("gradingScaleId"), scales),
        "passed": bool(record.get("isPassed")),
        "exam_period": period.get("title"),
        "counts_in_average": bool(record.get("isCountInGrade")),
    }


def transcript(diploma: list[dict], scales: dict | None = None, only_passed: bool = False) -> list[dict]:
    rows = [course_row(r, scales) for r in diploma
            if r.get("isCountInDiploma") and r.get("idFather") is None]
    if only_passed:
        rows = [r for r in rows if r["passed"]]
    return sorted(rows, key=lambda r: (r["semester"] or 99, r["code"] or ""))


def attempts(grades: list[dict], scales: dict | None = None, course: str | None = None,
             exam_period: str | None = None) -> list[dict]:
    """Every graded exam attempt, newest exam period first."""
    rows = []
    for r in grades:
        period = r.get("examPeriodId") or {}
        row = {
            "code": r.get("courseCode"),
            "title": r.get("title"),
            "grade": display_grade(r.get("grade"), r.get("gradingScaleId"), scales),
            "passed": bool(r.get("isPassed")),
            "exam_period": period.get("title"),
            "academic_year": period.get("syllabus"),
            "_order": (period.get("syllabus") or 0, period.get("sortOrder") or 0, period.get("code") or ""),
        }
        rows.append(row)
    if course:
        needle = fold(course)
        rows = [r for r in rows if needle in fold(r["title"] or "") or needle == fold(r["code"] or "")]
    if exam_period:
        needle = fold(exam_period)
        rows = [r for r in rows if needle in fold(r["exam_period"] or "")]
    rows.sort(key=lambda r: r["_order"], reverse=True)
    for r in rows:
        del r["_order"]
    return rows


def progress(diploma: list[dict], average, scales: dict | None = None) -> dict:
    """Totals the transcript page shows, plus passed ECTS per curriculum
    semester (the thesis rules count semesters 1–6)."""
    rows = transcript(diploma, scales)
    passed = [r for r in rows if r["passed"]]
    by_semester: dict[int | None, dict] = {}
    for r in rows:
        s = by_semester.setdefault(r["semester"], {"semester": r["semester"], "passed": 0, "ects": 0.0,
                                                    "not_passed": 0})
        if r["passed"]:
            s["passed"] += 1
            s["ects"] += r["ects"] or 0
        else:
            s["not_passed"] += 1
    return {
        "average": average,
        "passed_courses": len(passed),
        "passed_ects": sum(r["ects"] or 0 for r in passed),
        "passed_ects_semesters_1_6": sum(r["ects"] or 0 for r in passed if r["semester"] and r["semester"] <= 6),
        "registered_not_passed": len(rows) - len(passed),
        "by_semester": sorted(by_semester.values(), key=lambda s: s["semester"] or 99),
    }


def curriculum(program_courses: dict, diploma: list[dict], scales: dict | None = None,
               semester: int | None = None) -> list[dict]:
    """The programme's courses with where the student stands on each."""
    standing = {r.get("courseCode"): r for r in diploma if r.get("idFather") is None}
    rows = []
    for c in program_courses.get("programCourse") or []:
        if c.get("isShowWeb") is False:
            continue
        code = c.get("courseCode") or (c.get("courseId") or {}).get("code")
        mine = standing.get(code)
        status, grade = "not taken", None
        if mine:
            grade = display_grade(mine.get("grade"), mine.get("gradingScaleId"), scales)
            status = "passed" if mine.get("isPassed") else ("failed" if grade is not None else "registered, no grade")
        rows.append({"code": code, "title": c.get("title"), "semester": semester_no(c),
                     "type": title_of(c.get("typeId")), "ects": c.get("ects"), "status": status, "grade": grade})
    if semester is not None:
        rows = [r for r in rows if r["semester"] == semester]
    return sorted(rows, key=lambda r: (r["semester"] or 99, r["type"] or "", r["code"] or ""))


# -- registration periods and declarations ----------------------------------------

def local_day(ms) -> date | None:
    return datetime.fromtimestamp(ms / 1000).date() if ms else None


def period_row(p: dict, today: date | None = None) -> dict:
    """A registration period. Like the portal, it counts as open on every
    calendar day from its start to its end date inclusive."""
    today = today or date.today()
    start, end = local_day(p.get("webDateFrom")), local_day(p.get("webDateTo"))
    return {
        "id": p.get("id"),
        "title": " ".join((p.get("title") or "").split()),
        "academic_year": p.get("syllabus"),
        "open_from": start.isoformat() if start else None,
        "open_until": end.isoformat() if end else None,
        "open_now": bool(start and end and start <= today <= end),
        "interim_exams": bool(p.get("isInterpolated")),
    }


def declared_courses(official: list[dict]) -> list[dict]:
    rows = [{"code": r.get("courseCode"), "title": r.get("title"), "semester": semester_no(r),
             "type": title_of(r.get("typeId")), "ects": r.get("ects")} for r in official or []]
    return sorted(rows, key=lambda r: (r["semester"] or 99, r["code"] or ""))


def declaration(web: dict | None, registered: list[dict] | None = None) -> dict:
    """The web declaration of a period: its status and the courses on it.
    Courses can also be registered by the secretariat with no web
    declaration at all, as after a missed deadline."""
    sr = (web or {}).get("webStudentRegistration")
    if not sr:
        return {"status": BY_SECRETARIAT if registered else NO_DECLARATION, "courses": []}
    code = str(sr.get("status"))
    courses = []
    for pc in (web.get("webStudentRegistrationPcs") or []):
        prog = pc.get("programCourseId") or {}
        course = prog.get("courseId") or {}
        courses.append({"code": course.get("code") or prog.get("courseCode"), "title": prog.get("title"),
                        "ects": prog.get("ects")})
    return {"status": DECLARATION_STATUS.get(code, f"άγνωστη κατάσταση ({code})"), "courses": courses}


def pick_period(periods: list[dict], wanted: str | None, today: date | None = None) -> dict:
    """By (part of) its title, or else the main period open now, or else the
    newest one."""
    if not periods:
        raise ValueError("no registration periods")
    if wanted:
        needle = fold(" ".join(wanted.split()))
        hits = [p for p in periods if needle in fold(" ".join((p.get("title") or "").split()))]
        if not hits:
            names = ", ".join(" ".join(p["title"].split()) for p in periods)
            raise ValueError(f"no registration period matching {wanted!r}; periods: {names}")
        return hits[0]
    rows = [(p, period_row(p, today)) for p in periods]
    open_main = [p for p, r in rows if r["open_now"] and not r["interim_exams"]]
    if open_main:
        return open_main[0]
    return max(periods, key=lambda p: p.get("webDateFrom") or 0)


# -- the student ------------------------------------------------------------------

def pick_profile(profiles: list[dict], wanted: str | None) -> dict:
    """The student profile to use: the one asked for (id, department code or
    title), else the active one studied most recently."""
    if not profiles:
        raise ValueError("this account has no student profile in SIS-Web")
    if wanted:
        needle = fold(wanted)
        for p in profiles:
            if wanted in (p.get("id"), p.get("departmentCode")) or needle in fold(p.get("departmentTitle") or ""):
                return p
        raise ValueError(f"no student profile matching {wanted!r}")
    active = [p for p in profiles if fold(p.get("studentStatusTitle") or "") == fold("Ενεργός")] or profiles
    return max(active, key=lambda p: p.get("lastSyllabus") or 0)


def student(data: dict, profile: dict, profiles: list[dict]) -> dict:
    return {
        "name": f"{data.get('firstName', '')} {data.get('lastName', '')}".strip(),
        "student_id": data.get("studentInstNo"),
        "username": data.get("username"),
        "department": data.get("departmentTitle"),
        "programme": data.get("programTitle"),
        "status": title_of(data.get("studentStatusId")) or data.get("studentStatusTitle"),
        "semester": data.get("lastSemester"),
        "current_period": f"{data.get('lastPeriodTitle') or ''} {data.get('lastSyllabus') or ''}".strip(),
        "profile": profile.get("departmentTitle"),
        "other_profiles": [p.get("departmentTitle") for p in profiles if p.get("id") != profile.get("id")],
    }


def html_text(markup: str) -> str:
    """Text of announcement HTML. The portal stores some of it escaped twice
    (literal '<p>' and '&Alpha;' after one pass), so parse until it is plain."""
    text = markup or ""
    for _ in range(3):
        text = BeautifulSoup(text, "html.parser").get_text("\n", strip=True)
        if not re.search(r"</?[a-zA-Z][^>]*>|&[a-zA-Z]+;|&#\d+;", text):
            break
    return re.sub(r"[ \t\xa0]+", " ", text).strip()


def announcement_row(a: dict) -> dict:
    text = html_text(a.get("content") or "")
    return {"title": a.get("title"), "date": local_day(a.get("datePublished")).isoformat()
            if a.get("datePublished") else None, "important": bool(a.get("isHighImportance")), "text": text}
