import logging
import smtplib
from collections.abc import Sequence
from collections.abc import Set as AbstractSet
from email.message import EmailMessage

from imax_checker.config import Settings
from imax_checker.models import RunContext, Screening
from imax_checker.notify.formatting import (
    is_followup,
    render_html,
    render_subject,
    render_text,
)

log = logging.getLogger(__name__)


class EmailNotifier:
    name = "email"

    def __init__(
        self,
        *,
        host: str,
        port: int,
        security: str,
        username: str | None,
        password: str | None,
        sender: str,
        recipients: Sequence[str],
        use_bcc: bool = True,
        timeout: float = 30.0,
    ) -> None:
        self.host = host
        self.port = port
        self.security = security
        self.username = username
        self.password = password
        self.sender = sender
        self.recipients = list(recipients)
        self.use_bcc = use_bcc
        self.timeout = timeout

    @classmethod
    def from_settings(cls, settings: Settings) -> EmailNotifier:
        assert settings.smtp_host and settings.email_sender
        return cls(
            host=settings.smtp_host,
            port=settings.smtp_port,
            security=settings.smtp_security,
            username=settings.smtp_username,
            password=settings.smtp_password.get_secret_value() if settings.smtp_password else None,
            sender=settings.email_sender,
            recipients=[str(r) for r in settings.email_recipients],
            use_bcc=settings.email_use_bcc,
            timeout=settings.request_timeout_s * 2,
        )

    def build_message(
        self, screenings: Sequence[Screening], new_ids: AbstractSet[str], context: RunContext
    ) -> EmailMessage:
        msg = EmailMessage()
        msg["Subject"] = render_subject(
            screenings, context, followup=is_followup(screenings, new_ids)
        )
        msg["From"] = self.sender
        msg["Bcc" if self.use_bcc else "To"] = ", ".join(self.recipients)
        msg.set_content(render_text(screenings, new_ids, context))
        msg.add_alternative(render_html(screenings, new_ids, context), subtype="html")
        return msg

    def _connect(self) -> smtplib.SMTP:
        if self.security == "ssl":
            return smtplib.SMTP_SSL(self.host, self.port, timeout=self.timeout)
        smtp = smtplib.SMTP(self.host, self.port, timeout=self.timeout)
        if self.security == "starttls":
            try:
                smtp.starttls()
            except Exception:
                smtp.close()
                raise
        return smtp

    def send(
        self, screenings: Sequence[Screening], new_ids: AbstractSet[str], context: RunContext
    ) -> None:
        msg = self.build_message(screenings, new_ids, context)
        smtp = self._connect()
        try:
            if self.username:
                smtp.login(self.username, self.password or "")
            smtp.send_message(msg)
        finally:
            try:
                smtp.quit()
            except smtplib.SMTPException:
                smtp.close()
        log.info("Email sent to %d recipient(s)", len(self.recipients))
