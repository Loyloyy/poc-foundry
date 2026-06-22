import json


def parse_config(text):
    # BUG: the json module has no `parse`; the correct API is json.loads
    return json.parse(text)
