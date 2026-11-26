"""Envio de e-mail (Gmail conectado pela tela "Recuperação de senha", ou SMTP) (pedido do Alan, 26/11/2026): senha temporaria do "Esqueci a senha" -- o WhatsApp saiu.

Variaveis no Railway: `SMTP_HOST`, `SMTP_PORT` (587 = STARTTLS, 465 = SSL), `SMTP_USER`, `SMTP_PASSWORD` e, opcional,
`SMTP_FROM` (remetente; padrao = SMTP_USER). Serve qualquer provedor (Gmail com senha de app, e-mail do dominio...)."""
import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr

_TIMEOUT = 30


def _cfg():
    host = os.environ.get("SMTP_HOST", "").strip()
    usuario = os.environ.get("SMTP_USER", "").strip()
    return {
        "host": host, "usuario": usuario, "senha": os.environ.get("SMTP_PASSWORD", ""),
        "porta": int(os.environ.get("SMTP_PORT", "587").strip() or 587),
        "remetente": os.environ.get("SMTP_FROM", "").strip() or usuario,
    }


def _smtp_configurado():
    c = _cfg()
    return bool(c["host"] and c["usuario"] and c["senha"])


def _gmail():
    """(refresh token, e-mail) da conta Google conectada em "Recuperação de senha" -- ou (None, "")."""
    try:
        import models
        refresh = models.get_setting("gmail_refresh_token") or ""
        return (refresh or None), (models.get_setting("gmail_email") or "")
    except Exception:  # noqa: BLE001
        return None, ""


def configurado():
    return bool(_gmail()[0]) or _smtp_configurado()


def enviar(para, assunto, texto):
    """(ok, mensagem). Nunca levanta excecao. Gmail conectado (API, HTTPS) primeiro; senao SMTP."""
    refresh, remetente = _gmail()
    if refresh:
        try:
            import google_drive
            google_drive.enviar_gmail(refresh, para, assunto, texto, remetente)
            return True, "enviado pelo Gmail"
        except Exception as e:  # noqa: BLE001
            if not _smtp_configurado():
                return False, f"Gmail: {e}"
    c = _cfg()
    if not _smtp_configurado():
        return False, "e-mail não configurado (SMTP_HOST/SMTP_USER/SMTP_PASSWORD)"
    msg = EmailMessage()
    msg["Subject"] = assunto
    msg["From"] = formataddr(("OneAgro", c["remetente"])) if "<" not in c["remetente"] else c["remetente"]
    msg["To"] = para
    msg.set_content(texto)
    try:
        contexto = ssl.create_default_context()
        if c["porta"] == 465:
            with smtplib.SMTP_SSL(c["host"], c["porta"], timeout=_TIMEOUT, context=contexto) as s:
                s.login(c["usuario"], c["senha"])
                s.send_message(msg)
        else:
            with smtplib.SMTP(c["host"], c["porta"], timeout=_TIMEOUT) as s:
                s.starttls(context=contexto)
                s.login(c["usuario"], c["senha"])
                s.send_message(msg)
        return True, "enviado"
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"
