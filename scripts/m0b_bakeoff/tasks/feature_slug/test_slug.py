from slug import slugify


def test_basic():
    assert slugify("Hello World!") == "hello-world"


def test_trim_and_collapse():
    assert slugify("  A  B ") == "a-b"


def test_symbols():
    assert slugify("C++ & Rust") == "c-rust"
