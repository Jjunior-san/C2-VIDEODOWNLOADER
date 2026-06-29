from c2_update import is_newer, version_key


def test_version_key():
    assert version_key("v1.2.3") == (1, 2, 3)
    assert version_key("2026.06.28.234618") == (2026, 6, 28, 234618)


def test_is_newer():
    assert is_newer("1.1.0", "1.0.9")
    assert not is_newer("1.1.0", "1.1.0")
    assert not is_newer("1.0.9", "1.1.0")
