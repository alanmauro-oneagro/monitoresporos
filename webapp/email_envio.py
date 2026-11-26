"""Envio de e-mail por SMTP (pedido do Alan, 26/11/2026): senha temporaria do "Esqueci a senha" -- o WhatsApp saiu.

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


def configurado():
    c = _cfg()
    return bool(c["host"] and c["usuario"] and c["senha"])


def enviar(para, assunto, texto):
    """(ok, mensagem). Nunca levanta excecao."""
    c = _cfg()
    if not configurado():
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
