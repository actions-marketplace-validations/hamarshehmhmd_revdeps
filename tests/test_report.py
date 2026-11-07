from revdeps.report import counts, details, line, markdown, paint, phase_str, summary, use_color
from revdeps.runner import ALREADY_FAILING, BROKEN, ERROR, FIXED, OK, Phase, Result


def results():
    return [
        Result(name="a", status=OK, source="https://github.com/x/a", commit="abc123",
               baseline=Phase(0, 1.23), candidate=Phase(0, 12.4)),
        Result(name="b", status=BROKEN, baseline=Phase(0, 1), candidate=Phase(1, 1), log="/l/b.log",
               new_failures=[f"t{i}" for i in range(10)], messages={"t0": "boom"}, tail=["x"]),
        Result(name="c", status=ERROR, stage="install", detail="install failed (exit 1)", tail=["E404"],
               log="/l/c.log"),
        Result(name="d", status=FIXED, baseline=Phase(1, 1), candidate=Phase(0, 1), new_passes=["accents"]),
        Result(name="e", status=ALREADY_FAILING, baseline=Phase(None, 900, True), candidate=Phase(1, 2)),
    ]


def test_phase_str():
    assert phase_str(None) == "-"
    assert phase_str(Phase(0, 1.234)) == "pass 1.2s"
    assert phase_str(Phase(1, 42.6)) == "fail 43s"
    assert phase_str(Phase(None, 900, True)) == "timeout 900s"


def test_line_and_details_plain():
    a, b, c, d, e = results()
    assert line(a, False).startswith("✓ ok") and "a@abc123" in line(a, False)
    assert "baseline pass 1.2s → candidate pass 12s" in line(a, False)
    det = details(b, False)
    assert "newly failing (10):" in det[0] and "t0  (boom)" in det[1] and "… 2 more" in "\n".join(det)
    assert not any("last log lines" in x for x in det)  # junit messages beat raw logs
    assert any("log: /l/b.log" in x for x in det)
    assert "[install]" in line(c, False)
    assert any("E404" in x for x in details(c, False))
    assert "newly passing (1): accents" in details(d, False)[0]


def test_colors():
    assert paint("x", "31", True) == "\x1b[31mx\x1b[0m" and paint("x", "31", False) == "x"
    assert "\x1b[" in line(results()[1], True)
    assert use_color("always") and not use_color("never")


def test_use_color_respects_no_color(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    assert not use_color("auto")


def test_summary_and_counts():
    rs = results()
    assert counts(rs) == {OK: 1, BROKEN: 1, ERROR: 1, FIXED: 1, ALREADY_FAILING: 1}
    assert summary(rs, False) == "5 dependents: 1 broken, 1 error, 1 ok, 1 fixed, 1 already failing"


def test_markdown():
    md = markdown(results(), "slugkit")
    assert md.startswith("## revdeps report: `slugkit`")
    assert "| ✅ ok | [a](https://github.com/x/a) @`abc123` | pass 1.2s | pass 12s |" in md
    assert "newly failing: `t0`, `t1`, `t2`, `t3`, `t4` +5 more" in md
    assert "stage: install" in md and "newly passing: `accents`" in md
