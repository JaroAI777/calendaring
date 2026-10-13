import calendaring


def test_version_is_a_string() -> None:
    assert isinstance(calendaring.__version__, str)
    assert calendaring.__version__
