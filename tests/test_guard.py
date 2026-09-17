from lib.guard import hooks_disabled, NO_HOOKS_ENV


def test_hooks_disabled_false_by_default(monkeypatch):
    monkeypatch.delenv(NO_HOOKS_ENV, raising=False)
    assert hooks_disabled() is False


def test_hooks_disabled_true_when_env_set(monkeypatch):
    monkeypatch.setenv(NO_HOOKS_ENV, "1")
    assert hooks_disabled() is True


def test_hooks_disabled_false_for_other_values(monkeypatch):
    monkeypatch.setenv(NO_HOOKS_ENV, "0")
    assert hooks_disabled() is False
