"""Envio de WhatsApp via o servico local "whatsapp-bridge" (Node.js +
Baileys, pasta whatsapp-bridge/ deste projeto) -- usa o WhatsApp de
verdade do administrador como remetente, mandando pra qualquer numero
cadastrado (nao depende mais de cada destinatario ter uma API key
propria, como era com o CallMeBot).

Precisa do servico rodando (`npm start` dentro de whatsapp-bridge/, ou o
atalho que inicia junto com o app) e pareado uma vez (escaneando o QR
code em Configuracoes > WhatsApp com o celular que vai ser o remetente).

PROVEDOR ALTERNATIVO (API oficial da Meta, `whatsapp_cloud_api.py`):
preparado pra quando o volume de envios crescer o bastante pra o risco
de retencao de entrega do bridge (ver comentario de MIN_DELAY_MS em
whatsapp-bridge/index.js) deixar de valer a pena manter so' com
mitigacoes -- ver docstring de `whatsapp_cloud_api.py` pro que falta
configurar. Enquanto `WHATSAPP_PROVIDER` nao for definido como
"cloud_api", este arquivo continua se comportando exatamente como
sempre (nada muda so' por `whatsapp_cloud_api.py` existir)."""
import base64
import json
import os
import urllib.request
import urllib.error

import whatsapp_cloud_api

# Local: aponta pro whatsapp-bridge rodando na mesma maquina (padrao).
# Hospedado (Railway/Render): aponta pro hostname interno do servico do
# bridge dentro do mesmo projeto (ex.: "http://whatsapp-bridge.railway.internal:3001")
# -- nunca uma URL publica, ja que o /send do bridge nao tem autenticacao.
BRIDGE_URL = os.environ.get("WHATSAPP_BRIDGE_URL", "http://127.0.0.1:3001")

# "bridge" (Baileys, atual, padrao) ou "cloud_api" (API oficial da Meta,
# ver whatsapp_cloud_api.py) -- unico interruptor pra trocar de provedor,
# sem mexer em nenhum lugar que chama send_whatsapp/send_whatsapp_document
# (app.py inteiro so' conhece as funcoes deste modulo, nunca o provedor
# por baixo).
WHATSAPP_PROVIDER = os.environ.get("WHATSAPP_PROVIDER", "bridge")


def get_status():
    """Retorna {"connected": bool, "qr": data-url ou None, "pairingCode": str ou
    None, "error": str opcional} -- do provedor ativo (`WHATSAPP_PROVIDER`)."""
    if WHATSAPP_PROVIDER == "cloud_api":
        return whatsapp_cloud_api.get_status()
    return _get_status_bridge()


def _get_status_bridge():
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
    telefone, no proprio WhatsApp). Retorna (ok, codigo_ou_mensagem_de_erro).
    So' existe no bridge -- a API oficial nao tem conceito de QR/pareamento
    (o numero e' registrado direto no Business Manager da Meta)."""
    if WHATSAPP_PROVIDER == "cloud_api":
        return False, "Pareamento por QR/codigo nao existe na API oficial -- o numero e' registrado no WhatsApp Manager da Meta."
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
    WhatsApp corporativo). Retorna (ok, mensagem_ou_erro). So' existe no
    bridge -- a API oficial nao tem "sessao" pra resetar (e' so' token +
    phone number id, trocados direto nas variaveis de ambiente)."""
    if WHATSAPP_PROVIDER == "cloud_api":
        return False, "Nao ha' sessao pra resetar na API oficial -- troque WHATSAPP_CLOUD_ACCESS_TOKEN/WHATSAPP_CLOUD_PHONE_NUMBER_ID."
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
    """Retorna (ok: bool, mensagem: str) -- do provedor ativo (`WHATSAPP_PROVIDER`)."""
    if WHATSAPP_PROVIDER == "cloud_api":
        return whatsapp_cloud_api.send_whatsapp(phone, text)
    return _send_whatsapp_bridge(phone, text)


def _send_whatsapp_bridge(phone, text):
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


def send_whatsapp_image(phone, imagem_bytes, caption=None):
    """Manda uma imagem (PNG) anexada -- o mesmo /send do bridge, so' que
    com `imageBase64` em vez de `message`/`documentBase64` (o WhatsApp
    trata imagem como um tipo de midia diferente de documento -- vira
    uma foto na conversa, com preview, em vez de um arquivo anexado).
    Usado pelas imagens de NDVI salvas na galeria (ver
    `enviar_ndvi_historico_whatsapp` em app.py). Retorna (ok: bool,
    mensagem: str) -- do provedor ativo (`WHATSAPP_PROVIDER`)."""
    if WHATSAPP_PROVIDER == "cloud_api":
        return whatsapp_cloud_api.send_whatsapp_image(phone, imagem_bytes, caption=caption)
    return _send_whatsapp_image_bridge(phone, imagem_bytes, caption=caption)


def _send_whatsapp_image_bridge(phone, imagem_bytes, caption=None):
    if not phone:
        return False, "Numero de WhatsApp nao informado."
    try:
        payload = json.dumps({
            "phone": phone,
            "imageBase64": base64.b64encode(imagem_bytes).decode("ascii"),
            "caption": caption or "",
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{BRIDGE_URL}/send",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=45) as resp:
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


def send_whatsapp_document(phone, pdf_bytes, filename, caption=None):
    """Manda um PDF como documento anexado (nao so' texto) -- o mesmo
    /send do bridge, so' que com `documentBase64`/`fileName` em vez de
    `message`. Retorna (ok: bool, mensagem: str) -- do provedor ativo
    (`WHATSAPP_PROVIDER`). Timeout maior que `send_whatsapp` porque o
    upload do arquivo pro WhatsApp demora mais que so' mandar texto."""
    if WHATSAPP_PROVIDER == "cloud_api":
        return whatsapp_cloud_api.send_whatsapp_document(phone, pdf_bytes, filename, caption=caption)
    return _send_whatsapp_document_bridge(phone, pdf_bytes, filename, caption=caption)


def _send_whatsapp_document_bridge(phone, pdf_bytes, filename, caption=None):
    if not phone:
        return False, "Numero de WhatsApp nao informado."
    try:
        payload = json.dumps({
            "phone": phone,
            "documentBase64": base64.b64encode(pdf_bytes).decode("ascii"),
            "fileName": filename,
            "caption": caption or "",
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{BRIDGE_URL}/send",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=45) as resp:
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
