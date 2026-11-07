import tinyconf

CONF = "dark_mode = true\nmax_items = 25\n"


def flag_enabled(conf, name):
    # written against tinyconf 1.x, where every value is a string
    return conf.get(name, "false").lower() == "true"


def test_flag_enabled():
    assert flag_enabled(tinyconf.load(CONF), "dark_mode")


def test_max_items_is_parsed_by_us():
    assert int(tinyconf.load(CONF)["max_items"]) == 25
