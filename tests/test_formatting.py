from imax_checker.notify.formatting import (
    fmt_availability,
    group_by_date,
    render_html,
    render_subject,
    render_text,
)

from .conftest import make_screening


def test_text_contains_weekday_date_time_and_url(context):
    s = [make_screening("1"), make_screening("2", "2026-12-27T20:40:00")]
    text = render_text(s, {"1", "2"}, context)

    assert "IMAX tickets on sale: Dune: Part Three @ Cinema City Flora" in text
    assert "Sun 27.12.2026" in text  # 27 Dec 2026 is a Sunday
    assert "17:20  IMAX VOLVO  50% available" in text
    assert "20:40" in text
    assert "https://www.cinemacity.cz/cz/booking-router/launch/1052/1?lang=en" in text
    assert "Checked range: 27.12.2026 – 30.12.2026 · cinema 1052" in text
    assert "NEW" not in text  # first alert has no NEW tags


def test_after_midnight_screening_shows_next_calendar_date(context):
    s = [make_screening("1", "2026-12-30T20:40:00"), make_screening("2", "2026-12-31T00:01:00")]
    text = render_text(s, {"1", "2"}, context)
    assert "Thu 31.12.2026" in text
    assert text.index("Wed 30.12.2026") < text.index("Thu 31.12.2026")
    assert "00:01" in text


def test_followup_marks_only_new_screenings(context):
    s = [make_screening("1"), make_screening("2", "2026-12-27T20:40:00")]
    text = render_text(s, {"2"}, context)
    lines = {line.split()[0]: line for line in text.splitlines() if line.startswith("  ")}
    assert "NEW" not in lines["17:20"]
    assert "NEW" in lines["20:40"]
    assert text.startswith("New IMAX screenings on sale")


def test_groups_by_date_in_chronological_order():
    s = [
        make_screening("3", "2026-12-28T10:00:00"),
        make_screening("2", "2026-12-27T20:40:00"),
        make_screening("1", "2026-12-27T17:20:00"),
    ]
    groups = group_by_date(s)
    assert [(g[0].day, [x.event_id for x in g[1]]) for g in groups] == [
        (27, ["1", "2"]),
        (28, ["3"]),
    ]


def test_subject(context):
    s = [make_screening("1"), make_screening("2", "2026-12-29T20:40:00")]
    assert (
        render_subject(s, context, followup=False)
        == "IMAX tickets on sale: Dune: Part Three – 2 screenings 27.12.2026–29.12.2026"
    )
    assert render_subject(s[:1], context, followup=True) == (
        "NEW: IMAX tickets on sale: Dune: Part Three – 1 screening 27.12.2026"
    )


def test_html_has_links_and_escapes(context):
    html = render_html([make_screening("1", auditorium="IMAX <&>")], {"1"}, context)
    assert '<a href="https://www.cinemacity.cz/cz/booking-router/launch/1052/1?lang=en">' in html
    assert "IMAX &lt;&amp;&gt;" in html
    assert "50% available" in html
    assert "Sun 27.12.2026" in html


def test_availability_is_a_whole_percent_and_omitted_when_missing(context):
    assert fmt_availability(0.3195) == "32% available"
    assert fmt_availability(0.2) == "20% available"
    assert fmt_availability(0) == "0% available"
    assert fmt_availability(1) == "100% available"
    assert fmt_availability(None) == ""

    text = render_text([make_screening("1", availability_ratio=0.3195)], {"1"}, context)
    assert "32% available" in text
    assert "50% available" not in text

    bare = render_text([make_screening("1", availability_ratio=None)], {"1"}, context)
    assert "% available" not in bare
