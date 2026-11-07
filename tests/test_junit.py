from revdeps.junit import failed_tests

PYTEST = """<?xml version="1.0"?><testsuites><testsuite>
<testcase classname="tests.test_a" name="test_ok"/>
<testcase classname="tests.test_a" name="test_bad"><failure message="assert 1 == 2">trace</failure></testcase>
<testcase classname="tests.test_b" name="test_err"><error message="">Boom
second line</error></testcase>
<testcase classname="tests.test_b" name="test_skip"><skipped/></testcase>
</testsuite></testsuites>"""

NODE = """<?xml version="1.0" encoding="utf-8"?><testsuites>
<testcase name="caps at 12" classname="test"><failure message="Expected values to be strictly equal:+ actual - expected+ 'a'- 'b'   ^"/></testcase>
<testcase name="fine" classname="test"/></testsuites>"""


def test_pytest_style(tmp_path):
    f = tmp_path / "j.xml"
    f.write_text(PYTEST)
    assert failed_tests(str(f)) == {"tests.test_a::test_bad": "assert 1 == 2", "tests.test_b::test_err": "Boom"}


def test_node_style_uses_bare_names_and_tidies_message(tmp_path):
    f = tmp_path / "j.xml"
    f.write_text(NODE)
    got = failed_tests(str(f))
    assert list(got) == ["caps at 12"]
    assert got["caps at 12"] == "Expected values to be strictly equal: + actual - expected + 'a' - 'b'"


def test_missing_or_broken(tmp_path):
    assert failed_tests(str(tmp_path / "nope.xml")) is None
    assert failed_tests("") is None
    f = tmp_path / "bad.xml"
    f.write_text("<testsuites><oops")
    assert failed_tests(str(f)) is None


def test_long_message_truncated(tmp_path):
    f = tmp_path / "j.xml"
    f.write_text(f'<testsuite><testcase name="t"><failure message="{"x" * 500}"/></testcase></testsuite>')
    assert len(failed_tests(str(f))["t"]) == 160
