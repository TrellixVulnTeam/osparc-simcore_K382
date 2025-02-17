from typing import Any

import pytest
from pydantic import ValidationError
from settings_library.email import SMTPSettings


@pytest.mark.parametrize(
    "cfg",
    [
        {"SMTP_HOST": "test", "SMTP_PORT": 113},
        {
            "SMTP_HOST": "test",
            "SMTP_PORT": 113,
            "SMTP_USERNAME": "test",
            "SMTP_PASSWORD": "test",
        },
        {
            "SMTP_HOST": "test",
            "SMTP_PORT": 113,
            "SMTP_USERNAME": "test",
            "SMTP_PASSWORD": "test",
            "SMTP_TLS_ENABLED": False,
        },
        {
            "SMTP_HOST": "test",
            "SMTP_PORT": 113,
            "SMTP_USERNAME": "test",
            "SMTP_PASSWORD": "test",
            "SMTP_TLS_ENABLED": True,
        },
    ],
)
def test_smtp_configuration_ok(cfg: dict[str, Any]):
    assert SMTPSettings.parse_obj(cfg)


@pytest.mark.parametrize(
    "cfg",
    [
        {"SMTP_HOST": "test", "SMTP_PORT": 113, "SMTP_USERNAME": "test"},
        {"SMTP_HOST": "test", "SMTP_PORT": 113, "SMTP_PASSWORD": "test"},
        {"SMTP_HOST": "test", "SMTP_PORT": 113, "SMTP_TLS_ENABLED": True},
        {
            "SMTP_HOST": "test",
            "SMTP_PORT": 113,
            "SMTP_TLS_ENABLED": True,
            "SMTP_PASSWORD": "test",
        },
        {
            "SMTP_HOST": "test",
            "SMTP_PORT": 113,
            "SMTP_TLS_ENABLED": True,
            "SMTP_USERNAME": "test",
        },
        {
            "SMTP_HOST": "test",
            "SMTP_PORT": 113,
            "SMTP_USERNAME": "",
            "SMTP_PASSWORD": "test",
            "SMTP_TLS_ENABLED": True,
        },
    ],
)
def test_smtp_configuration_fails(cfg: dict[str, Any]):
    with pytest.raises(ValidationError):
        assert SMTPSettings.parse_obj(cfg)
