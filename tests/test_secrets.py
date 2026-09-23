import pytest

from skillordeal.config import AuthConfig
from skillordeal.secrets import REDACTED, AuthError, Scrubber, resolve_credentials


def test_oauth_named_token_from_env_file(tmp_path):
    f = tmp_path / ".env"
    f.write_text("WORK_CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-abcdefghijklmnop\n")
    c = resolve_credentials(
        AuthConfig(mode="oauth", token_env="WORK_CLAUDE_CODE_OAUTH_TOKEN", env_file=str(f)),
        environ={},
    )
    assert c.env == {"CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat01-abcdefghijklmnop"}


def test_env_overrides(tmp_path):
    f = tmp_path / "x.env"
    f.write_text("MY_TOKEN=tok-1234567890\n")  # gitleaks:allow (fake test value)
    c = resolve_credentials(
        AuthConfig(mode="oauth"),
        environ={"SKILLORDEAL_TOKEN_ENV": "MY_TOKEN", "SKILLORDEAL_ENV_FILE": str(f)},
    )
    assert c.env["CLAUDE_CODE_OAUTH_TOKEN"] == "tok-1234567890"


def test_api_key_mode():
    c = resolve_credentials(
        AuthConfig(mode="api_key"), environ={"ANTHROPIC_API_KEY": "sk-ant-api03-zzzzzzzzzz"}
    )
    assert c.env == {"ANTHROPIC_API_KEY": "sk-ant-api03-zzzzzzzzzz"}


def test_missing_token_errors():
    with pytest.raises(AuthError):
        resolve_credentials(AuthConfig(mode="oauth"), environ={})


def test_scrubber():
    s = Scrubber(["supersecretvalue123"])
    out = s(
        '{"t":"supersecretvalue123","k":"sk-ant-oat01-AAAAAAAAAAAA","h":"Authorization: Bearer abcdefghijklmnopqrstu"}'
    )
    assert (
        "supersecretvalue123" not in out
        and "sk-ant-oat01" not in out
        and "abcdefghijklmnop" not in out
    )
    assert out.count(REDACTED) == 3
