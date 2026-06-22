from cfg import parse_config


def test_parse():
    assert parse_config('{"a": 1, "b": [2, 3]}') == {"a": 1, "b": [2, 3]}
