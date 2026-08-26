"""Envio de WhatsApp via o servico local "whatsapp-bridge" (Node.js +
Baileys, pasta whatsapp-bridge/ deste projeto) -- usa o WhatsApp de
verdade do administrador como remetente, mandando pra qualquer numero
cadastrado (nao depende mais de cada destinatario ter uma API key
propria, como era com o CallMeBot).

Precisa do servico rodando (`npm start` dentro de whatsapp-bridge/, ou o
atalho que inicia junto com o app) e pareado uma vez (escaneando o QR
code em Configuracoes > WhatsApp com o celular que vai ser o remetente).
"""
import json
import os
import urllib.request
import urllib.error

# Local: aponta pro whatsapp-bridge rodando na mesma maquina (padrao).
# Hospedado (Railway/Render): aponta pro hostname interno do servico do
# bridge dentro do mesmo projeto (ex.: "http://whatsapp-bridge.railway.internal:3001")
# -- nunca uma URL publica, ja que o /send do bridge nao tem autenticacao.
BRIDGE_URL = os.environ.get("WHATSAPP_BRIDGE_URL", "http://127.0.0.1:3001")


def get_status():
    """Retorna {"connected": bool, "qr": data-url ou None, "pairingCode": str ou
    None, "error": str opcional}."""
    try:
        with urllib.request.urlopen(f"{BRIDGE_URL}/status", timeout=5) as resp:
            return json.load(resp)
    except Exception:
        return {
            "connected": False,
            "qr": None,
            "pairingCode": None,
            "error": "Servico do WhatsApp (whatsapp-bridge) nao esta rodando.",
        }


def request_pairing_code(phone):
    """Pede ao whatsapp-bridge um codigo de pareamento pro numero informado --
    alternativa ao QR code (Aparelhos conectados > Conectar com numero de
    telefone, no proprio WhatsApp). Retorna (ok, codigo_ou_mensagem_de_erro)."""
    try:
        payload = json.dumps({"phone": phone}).encode("utf-8")
        req = urllib.request.Request(
            f"{BRIDGE_URL}/pair-code",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.load(resp)
            return (True, body.get("code")) if body.get("ok") else (False, body.get("error", "falha desconhecida"))
    except urllib.error.HTTPError as exc:
        try:
            body = json.load(exc)
            return False, body.get("error", str(exc))
        except Exception:
            return False, str(exc)
    except Exception as exc:
        return False, f"Servico do WhatsApp (whatsapp-bridge) nao respondeu: {exc}"


def reset_session():
    """Desconecta o numero atual (se houver) e apaga a sessao salva --
    depois disso, `get_status()` volta a trazer um QR code/codigo de
    pareamento novo, pra conectar um numero diferente (ex.: trocar o
    WhatsApp corporativo). Retorna (ok, mensagem_ou_erro)."""
    try:
        req = urllib.request.Request(
            f"{BRIDGE_URL}/reset", data=b"{}",
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.load(resp)
            return (True, "ok") if body.get("ok") else (False, body.get("error", "falha desconhecida"))
    except urllib.error.HTTPError as exc:
        try:
            body = json.load(exc)
            return False, body.get("error", str(exc))
        except Exception:
            return False, str(exc)
    except Exception as exc:
        return False, f"Servico do WhatsApp (whatsapp-bridge) nao respondeu: {exc}"


def send_whatsapp(phone, text):
    """Retorna (ok: bool, mensagem: str)."""
    if not phone:
        return False, "Numero de WhatsApp nao informado."
    try:
        payload = json.dumps({"phone": phone, "message": text}).encode("utf-8")
        req = urllib.request.Request(
            f"{BRIDGE_URL}/send",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=25) as resp:
            body = json.load(resp)
            return (True, "enviado") if body.get("ok") else (False, body.get("error", "falha desconhecida"))
    except urllib.error.HTTPError as exc:
        try:
            body = json.load(exc)
            return False, body.get("error", str(exc))
        except Exception:
            return False, str(exc)
    except Exception as exc:
        return False, f"Servico do WhatsApp (whatsapp-bridge) nao respondeu: {exc}"
