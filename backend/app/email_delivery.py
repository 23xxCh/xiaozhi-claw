import asyncio
import smtplib
from email.message import EmailMessage

from .config import Settings


def _send_smtp(settings: Settings, recipient: str, code: str) -> None:
    message = EmailMessage()
    message["Subject"] = "Hensun AI 登录验证码"
    message["From"] = settings.smtp_from_email
    message["To"] = recipient
    message.set_content(
        "你的 Hensun AI 登录验证码是："
        f"{code}\n\n验证码 {settings.email_otp_ttl_seconds // 60} 分钟内有效。"
        "如果不是你本人操作，请忽略这封邮件。"
    )

    smtp_class = smtplib.SMTP_SSL if settings.smtp_use_ssl else smtplib.SMTP
    with smtp_class(settings.smtp_host, settings.smtp_port, timeout=10) as smtp:
        if settings.smtp_use_tls and not settings.smtp_use_ssl:
            smtp.starttls()
        smtp.login(settings.smtp_username, settings.smtp_password)
        smtp.send_message(message)


async def deliver_login_code(settings: Settings, recipient: str, code: str) -> None:
    if settings.email_delivery_mode == "development":
        return
    await asyncio.to_thread(_send_smtp, settings, recipient, code)
