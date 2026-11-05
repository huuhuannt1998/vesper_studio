"""`vesper setup ha-dev-login`: the developer login (admin / admin on VESPER's own Home Assistant, 127.0.0.1 only)
is turned on and off in the configuration; it applies when a home next plays."""
from vesper import cli
from vesper.validation import stack


def test_the_command_turns_the_developer_login_on_and_off(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(stack, "port_free", lambda port, host="127.0.0.1": True)  # no Home Assistant running
    assert not stack.dev_login_on()
    assert cli.main(["setup", "ha-dev-login"]) == 0
    assert stack.dev_login_on()
    out = capsys.readouterr().out
    assert "admin / admin" in out and "127.0.0.1" in out and "next time a home plays" in out
    assert cli.main(["setup", "ha-dev-login", "--off"]) == 0
    assert not stack.dev_login_on() and "off" in capsys.readouterr().out
