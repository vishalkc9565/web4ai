from web4ai.settings import ServerSettings


def test_dev_settings():
    cfg = ServerSettings.for_dev()
    assert cfg.host == "0.0.0.0"
    assert cfg.port == 8000
    assert cfg.reload is True


def test_container_settings():
    cfg = ServerSettings.for_container()
    assert cfg.host == "10.0.0.1"
    assert cfg.port == 8080
    assert cfg.reload is False
