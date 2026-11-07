import tinyconf

CONF = """
region = eu-west-1
service = api   # comment
"""


def test_reads_region():
    assert tinyconf.load(CONF)["region"] == "eu-west-1"


def test_strips_comments():
    assert tinyconf.load(CONF)["service"] == "api"
