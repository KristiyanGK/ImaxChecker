import json
from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import EmailStr, PositiveInt, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

type SmtpSecurity = Literal["starttls", "ssl", "none"]


def parse_date(value: Any) -> Any:
    """Accept `DD.MM.YYYY` as well as ISO `YYYY-MM-DD` (other values pass through)."""
    if isinstance(value, str):
        text = value.strip()
        if "." in text:
            try:
                return datetime.strptime(text, "%d.%m.%Y").date()
            except ValueError as exc:
                raise ValueError(f"invalid date {value!r}, expected DD.MM.YYYY") from exc
        return text
    return value


def split_list(value: Any) -> Any:
    """Turn a comma-separated string or a JSON list string into a list."""
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        if text.startswith("["):
            return json.loads(text)
        return [item.strip() for item in text.split(",") if item.strip()]
    return value


class Settings(BaseSettings):
    """Precedence: CLI > environment (`IMAX_` prefix) > `.env` > defaults."""

    model_config = SettingsConfigDict(
        env_prefix="IMAX_",
        env_file=".env",
        extra="ignore",
        cli_parse_args=True,
        cli_kebab_case=True,
        cli_implicit_flags=True,
    )

    movie: str = "Dune: Part Three"
    cinema_id: str = "1052"
    date_from: date = date(2026, 12, 27)
    date_to: date = date(2026, 12, 30)
    interval_minutes: PositiveInt = 60
    once: bool = False
    dry_run: bool = False
    list_films: bool = False
    lang: str = "en_GB"
    state_file: Path = Path(".state/notified.json")
    request_timeout_s: float = 15.0

    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_security: SmtpSecurity = "starttls"
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    email_from: str | None = None
    email_recipients: Annotated[list[EmailStr], NoDecode] = []
    email_use_bcc: bool = True

    discord_webhook_urls: Annotated[list[SecretStr], NoDecode] = []

    log_level: str = "INFO"

    @field_validator("date_from", "date_to", mode="before")
    @classmethod
    def _parse_dates(cls, value: Any) -> Any:
        return parse_date(value)

    @field_validator("email_recipients", "discord_webhook_urls", mode="before")
    @classmethod
    def _split_lists(cls, value: Any) -> Any:
        return split_list(value)

    @model_validator(mode="after")
    def _check_range(self) -> Settings:
        if self.date_from > self.date_to:
            raise ValueError(
                f"date_from ({self.date_from}) must not be after date_to ({self.date_to})"
            )
        return self

    @property
    def email_sender(self) -> str | None:
        return self.email_from or self.smtp_username

    @property
    def email_enabled(self) -> bool:
        return bool(self.smtp_host and self.email_recipients and self.email_sender)

    @property
    def discord_enabled(self) -> bool:
        return bool(self.discord_webhook_urls)


def load_settings(argv: Sequence[str] | None = None) -> Settings:
    """Load settings. `argv=None` parses `sys.argv`; pass a list (even empty) to override."""
    if argv is None:
        return Settings()
    return Settings(_cli_parse_args=list(argv))  # type: ignore[call-arg]
