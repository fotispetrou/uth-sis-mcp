"""Fixtures below copy the shape of the JSON the SIS-Web (ILYDA) portal
returns, with made-up values."""

from datetime import date, datetime

import pytest

from uth_sis_mcp import parsers

NUMERIC = {"id": "1", "title": "0-10", "minNumber": 0.0, "maxNumber": 10.0, "step": 0.01}
PASS_FAIL = {"id": "2", "title": "Επιτυχώς/Ανεπιτυχώς", "minNumber": None, "maxNumber": None, "step": None,
             "grades": [{"val": 0.0, "grade": "Ανεπιτυχώς"}, {"val": 0.5, "grade": "Επιτυχώς"}]}
SCALES = {"1": NUMERIC, "2": PASS_FAIL}


def sem(n):
    return {"id": str(n), "title": f"{n}ο εξάμηνο", "sortOrder": n, "abbr": f"{n}ο"}


def record(code, title, semester, grade, passed, ects=5.0, weight=5.0, kind="Υποχρεωτικό", scale=NUMERIC,
           period=("2025-2026 ΣΕΠΤΕΜΒΡΙΟΣ", 2025, 3, "202503"), in_diploma=True):
    return {"courseCode": code, "title": title, "semesterId": sem(semester) if semester else
            {"id": "-1", "title": "Χ/Ε", "sortOrder": -1}, "typeId": {"title": kind}, "ects": ects,
            "gradeWeight": weight, "grade": grade, "isPassed": passed, "isCountInDiploma": in_diploma,
            "isCountInGrade": True, "idFather": None, "gradingScaleId": scale,
            "examPeriodId": {"title": period[0], "syllabus": period[1], "sortOrder": period[2], "code": period[3]}}


DIPLOMA = [
    record("Υ101", "Μαθηματική Ανάλυση", 1, 0.65, True),
    record("Υ100", "Αγγλική Γλώσσα", 2, 1.0, True, ects=2.0, weight=2.0, scale={"id": "2"}),
    record("Υ403", "Ψηφιακή Επεξεργασία Σήματος", 4, 0.3, False),
    record("Ε501", "Αρχές Εφοδιαστικής Αλυσίδας", 5, 0.5, True, kind="Επιλογής"),
    record("Ε702", "Σχεδιασμός Συστημάτων Υλικού", 7, None, False, kind="Επιλογής"),
    record("Ε1000", "Πρακτική Άσκηση", None, None, False, in_diploma=False),
]


def test_display_grade():
    assert parsers.display_grade(0.565, NUMERIC) == 5.65
    assert parsers.display_grade(0.5, NUMERIC) == 5
    assert parsers.display_grade(0.6, {"id": "1"}, SCALES) == 6
    assert parsers.display_grade(1.0, {"id": "2"}, SCALES) == "Επιτυχώς"
    assert parsers.display_grade(0.2, {"id": "2"}, SCALES) == "Ανεπιτυχώς"
    assert parsers.display_grade(None, NUMERIC) is None
    assert parsers.display_grade(0.5, None) is None
    # the portal rounds to the scale's step
    assert parsers.display_grade(0.56789, NUMERIC) == 5.68
    assert parsers.display_grade(0.5, {"id": "11", "minNumber": 0.0, "maxNumber": 10.0, "step": 0.1}) == 5


def test_transcript():
    rows = parsers.transcript(DIPLOMA, SCALES)
    assert [r["code"] for r in rows] == ["Υ101", "Υ100", "Υ403", "Ε501", "Ε702"]  # not counted in the degree: left out
    assert rows[0] == {"code": "Υ101", "title": "Μαθηματική Ανάλυση", "semester": 1, "type": "Υποχρεωτικό",
                       "ects": 5.0, "grade": 6.5, "passed": True, "exam_period": "2025-2026 ΣΕΠΤΕΜΒΡΙΟΣ",
                       "counts_in_average": True}
    assert rows[1]["grade"] == "Επιτυχώς"
    assert [r["code"] for r in parsers.transcript(DIPLOMA, SCALES, only_passed=True)] == ["Υ101", "Υ100", "Ε501"]


def test_progress():
    p = parsers.progress(DIPLOMA, 5.94, SCALES)
    assert p["average"] == 5.94
    assert p["passed_courses"] == 3 and p["passed_ects"] == 12.0
    assert p["passed_ects_semesters_1_6"] == 12.0
    assert p["registered_not_passed"] == 2
    by = {s["semester"]: s for s in p["by_semester"]}
    assert by[4] == {"semester": 4, "passed": 0, "ects": 0.0, "not_passed": 1}
    assert by[7]["not_passed"] == 1


def test_attempts_order_and_filters():
    grades = [
        record("Ε501", "Αρχές Εφοδιαστικής Αλυσίδας", 5, 0.3, False, period=("2024-2025 ΙΑΝΟΥΑΡΙΟΣ", 2024, 1, "202401")),
        record("Ε501", "Αρχές Εφοδιαστικής Αλυσίδας", 5, 0.5, True, period=("2025-2026 ΣΕΠΤΕΜΒΡΙΟΣ", 2025, 3, "202503")),
        record("Υ101", "Μαθηματική Ανάλυση", 1, 0.65, True, period=("2022-2023 ΙΟΥΝΙΟΣ", 2022, 2, "202202")),
    ]
    rows = parsers.attempts(grades, SCALES)
    assert [(r["code"], r["exam_period"]) for r in rows] == [
        ("Ε501", "2025-2026 ΣΕΠΤΕΜΒΡΙΟΣ"), ("Ε501", "2024-2025 ΙΑΝΟΥΑΡΙΟΣ"), ("Υ101", "2022-2023 ΙΟΥΝΙΟΣ")]
    assert [r["grade"] for r in parsers.attempts(grades, SCALES, course="αρχες εφοδιαστικης")] == [5, 3]
    assert len(parsers.attempts(grades, SCALES, course="Ε501")) == 2
    assert [r["code"] for r in parsers.attempts(grades, SCALES, exam_period="ιουν")] == ["Υ101"]


def test_curriculum_standing():
    program = {"programCourse": [
        {"courseCode": "Υ101", "title": "Μαθηματική Ανάλυση", "semesterId": sem(1), "typeId": {"title": "Υποχρεωτικό"}, "ects": 5.0},
        {"courseCode": "Υ403", "title": "Ψηφιακή Επεξεργασία Σήματος", "semesterId": sem(4), "typeId": {"title": "Υποχρεωτικό"}, "ects": 5.0},
        {"courseCode": "Ε702", "title": "Σχεδιασμός Συστημάτων Υλικού", "semesterId": sem(7), "typeId": {"title": "Επιλογής"}, "ects": 5.0},
        {"courseCode": "Ε706", "title": "Διασφάλιση Ποιότητας", "semesterId": sem(7), "typeId": {"title": "Επιλογής"}, "ects": 5.0},
        {"courseCode": "Χ000", "title": "κρυφό", "semesterId": sem(7), "isShowWeb": False},
    ]}
    rows = parsers.curriculum(program, DIPLOMA, SCALES)
    status = {r["code"]: (r["status"], r["grade"]) for r in rows}
    assert status == {"Υ101": ("passed", 6.5), "Υ403": ("failed", 3), "Ε702": ("registered, no grade", None),
                      "Ε706": ("not taken", None)}
    assert [r["code"] for r in parsers.curriculum(program, DIPLOMA, SCALES, semester=7)] == ["Ε702", "Ε706"]


def ms(day: date) -> int:
    return int(datetime(day.year, day.month, day.day).timestamp() * 1000)


PERIODS = [
    {"id": "A", "title": "2026-2027 ΧΕΙΜ", "syllabus": 2026, "isInterpolated": False,
     "webDateFrom": ms(date(2026, 10, 21)), "webDateTo": ms(date(2026, 11, 15))},
    {"id": "B", "title": "2026-2027 ΧΕΙΜ ΕΜΒΟΛΙΜΗ", "syllabus": 2026, "isInterpolated": True,
     "webDateFrom": ms(date(2026, 10, 21)), "webDateTo": ms(date(2026, 11, 15))},
    {"id": "C", "title": "2025-2026  ΕΑΡ", "syllabus": 2025, "isInterpolated": False,
     "webDateFrom": ms(date(2026, 4, 6)), "webDateTo": ms(date(2026, 5, 17))},
]


def test_period_row_open_days_are_inclusive():
    row = parsers.period_row(PERIODS[0], today=date(2026, 11, 15))
    assert row == {"id": "A", "title": "2026-2027 ΧΕΙΜ", "academic_year": 2026, "open_from": "2026-10-21",
                   "open_until": "2026-11-15", "open_now": True, "interim_exams": False}
    assert parsers.period_row(PERIODS[0], today=date(2026, 11, 16))["open_now"] is False
    assert parsers.period_row(PERIODS[2])["title"] == "2025-2026 ΕΑΡ"


def test_pick_period():
    today = date(2026, 11, 11)
    assert parsers.pick_period(PERIODS, None, today)["id"] == "A"
    assert parsers.pick_period(PERIODS, "εμβολιμη", today)["id"] == "B"
    assert parsers.pick_period(PERIODS, "2025-2026 ΕΑΡ", today)["id"] == "C"
    assert parsers.pick_period(PERIODS, None, date(2026, 12, 1))["id"] in ("A", "B")  # newest when none is open
    with pytest.raises(ValueError, match="periods: 2026-2027 ΧΕΙΜ"):
        parsers.pick_period(PERIODS, "2019", today)


def test_declaration_status():
    assert parsers.declaration({}) == {"status": parsers.NO_DECLARATION, "courses": []}
    assert parsers.declaration({}, [{"courseCode": "Υ203"}])["status"] == parsers.BY_SECRETARIAT
    web = {"webStudentRegistration": {"status": 3},
           "webStudentRegistrationPcs": [{"programCourseId": {"title": "Θεωρία Πληροφορίας", "ects": 5.0,
                                                              "courseId": {"code": "Ε701"}}}]}
    assert parsers.declaration(web) == {"status": "Υποβληθείσα",
                                        "courses": [{"code": "Ε701", "title": "Θεωρία Πληροφορίας", "ects": 5.0}]}
    assert parsers.declaration({"webStudentRegistration": {"status": 0}})["status"].startswith("Πρόχειρη")
    assert "9" in parsers.declaration({"webStudentRegistration": {"status": 9}})["status"]


def test_declared_courses():
    rows = parsers.declared_courses([record("Ε802", "Β", 8, None, False), record("Υ203", "Α", 2, None, False)])
    assert [r["code"] for r in rows] == ["Υ203", "Ε802"]
    assert rows[0] == {"code": "Υ203", "title": "Α", "semester": 2, "type": "Υποχρεωτικό", "ects": 5.0}


PROFILES = [
    {"id": "P1", "departmentCode": "610", "departmentTitle": "ΤΜΗΜΑ ΨΗΦΙΑΚΩΝ ΣΥΣΤΗΜΑΤΩΝ",
     "studentStatusTitle": "Ενεργός", "lastSyllabus": 2026},
    {"id": "P2", "departmentCode": "20610", "departmentTitle": "ΠΑΙΔΑΓΩΓΙΚΗ ΕΠΑΡΚΕΙΑ ΤΜΗΜΑΤΟΣ ΨΗΦΙΑΚΩΝ ΣΥΣΤΗΜΑΤΩΝ",
     "studentStatusTitle": "Ενεργός", "lastSyllabus": 2025},
]


def test_pick_profile():
    assert parsers.pick_profile(PROFILES, None)["id"] == "P1"
    assert parsers.pick_profile(PROFILES, "20610")["id"] == "P2"
    assert parsers.pick_profile(PROFILES, "παιδαγωγικη")["id"] == "P2"
    assert parsers.pick_profile(PROFILES, "P2")["id"] == "P2"
    with pytest.raises(ValueError):
        parsers.pick_profile(PROFILES, "ΙΑΤΡΙΚΗΣ")
    with pytest.raises(ValueError):
        parsers.pick_profile([], None)


def test_student():
    data = {"firstName": "ΓΕΩΡΓΙΟΣ", "lastName": "ΠΑΠΑΔΟΠΟΥΛΟΣ", "studentInstNo": "3120000", "username": "student",
            "departmentTitle": "ΤΜΗΜΑ ΨΗΦΙΑΚΩΝ ΣΥΣΤΗΜΑΤΩΝ", "programTitle": "ΠΡΟΓΡΑΜΜΑ ΣΠΟΥΔΩΝ 2019-2020",
            "studentStatusId": {"title": "Ενεργός"}, "lastSemester": 9, "lastPeriodTitle": "Χειμερινό",
            "lastSyllabus": 2026}
    s = parsers.student(data, PROFILES[0], PROFILES)
    assert s["name"] == "ΓΕΩΡΓΙΟΣ ΠΑΠΑΔΟΠΟΥΛΟΣ" and s["student_id"] == "3120000" and s["semester"] == 9
    assert s["current_period"] == "Χειμερινό 2026"
    assert s["other_profiles"] == ["ΠΑΙΔΑΓΩΓΙΚΗ ΕΠΑΡΚΕΙΑ ΤΜΗΜΑΤΟΣ ΨΗΦΙΑΚΩΝ ΣΥΣΤΗΜΑΤΩΝ"]


def test_announcement_double_escaped_html():
    # as stored by the portal: the HTML itself escaped once more
    content = ('&nbsp; &nbsp;&lt;p&gt;&lt;strong&gt;&amp;Alpha;&amp;Nu;&amp;Alpha;&amp;Kappa;&amp;Omicron;'
               '&amp;Iota;&amp;Nu;&amp;Omega;&amp;Sigma;&amp;Eta;&lt;/strong&gt;&lt;/p&gt;'
               '&lt;p&gt;&amp;Pi;&amp;alpha;&amp;rho;&amp;alpha;&amp;tau;&amp;epsilon;ί&amp;nu;&amp;epsilon;'
               '&amp;tau;&amp;alpha;&amp;iota; έως 15/11/2026&lt;/p&gt;')
    row = parsers.announcement_row({"title": "ΔΗΛΩΣΗ ΜΑΘΗΜΑΤΩΝ", "content": content,
                                    "datePublished": ms(date(2026, 10, 30)), "isHighImportance": True})
    assert row == {"title": "ΔΗΛΩΣΗ ΜΑΘΗΜΑΤΩΝ", "date": "2026-10-30", "important": True,
                   "text": "ΑΝΑΚΟΙΝΩΣΗ\nΠαρατείνεται έως 15/11/2026"}
