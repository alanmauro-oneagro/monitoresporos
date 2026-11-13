"""Envio de WhatsApp pela Umbler Talk (pedido do Alan, 13/11/2026) -- provedor
escolhido com WHATSAPP_PROVIDER=umbler. Mesmas funcoes e o mesmo retorno
(ok, mensagem) do bridge e da API oficial da Meta (ver whatsapp.py).

API: https://app-utalk.umbler.com/api/v1/ (documentacao em
/api/docs/index.html). Autenticacao Bearer com o token gerado no perfil da
Umbler Talk. Rotas usadas:
- POST /v1/messages/simplified/  -- texto e arquivo (PDF/imagem) para um numero,
  sem precisar abrir o chat antes (fromPhone = numero do canal, E.164).
- POST /v1/template-messages/simplified/ -- modelo aprovado pela Meta (templateId +
  params, arquivo opcional no cabecalho). Fora da janela de 24h de um canal oficial
  (WABA), so' modelo sai; num canal Starter o texto livre funciona sempre.
- GET /v1/members/me/ -- confere o token (tela Gerenciar WhatsApp).

Variaveis de ambiente (Railway):
- UMBLER_TALK_TOKEN            token da API (Perfil > Tokens de API na Umbler Talk)
- UMBLER_TALK_ORGANIZATION_ID  id da organizacao
- UMBLER_TALK_FROM_PHONE       numero do canal que envia, ex.: +5543991147595
- UMBLER_TALK_TEMPLATE_RELATORIO_ID / _TEXTO_ID / _NDVI_ID  ids dos modelos aprovados
  (opcionais; sem eles o relatorio sai em texto livre + PDF, como no bridge)
- UMBLER_TALK_WEBHOOK_TOKEN    senha que vai na URL do webhook (?token=...)
"""
import json
import os
import urllib.error
import urllib.request
import uuid

API_BASE = "https://app-utalk.umbler.com/api/v1"
TOKEN = os.environ.get("UMBLER_TALK_TOKEN", "")
ORGANIZATION_ID = os.environ.get("UMBLER_TALK_ORGANIZATION_ID", "")
FROM_PHONE = os.environ.get("UMBLER_TALK_FROM_PHONE", "")
TEMPLATE_RELATORIO_ID = os.environ.get("UMBLER_TALK_TEMPLATE_RELATORIO_ID", "")
TEMPLATE_TEXTO_ID = os.environ.get("UMBLER_TALK_TEMPLATE_TEXTO_ID", "")
TEMPLATE_NDVI_ID = os.environ.get("UMBLER_TALK_TEMPLATE_NDVI_ID", "")
WEBHOOK_TOKEN = os.environ.get("UMBLER_TALK_WEBHOOK_TOKEN", "")

# Estado da mensagem na Umbler (MessageStates) -> mesmo status da API da Meta no historico de envios
# (`models.atualizar_status_meta`; "failed" marca o envio como falho).
ESTADOS = {"Sent": "sent", "Received": "delivered", "Read": "read", "Played": "read",
           "Failed": "failed", "Error": "failed"}


def _configurado():
    return bool(TOKEN and ORGANIZATION_ID and FROM_PHONE)


def _erro_configuracao():
    faltando = [n for n, v in (("UMBLER_TALK_TOKEN", TOKEN), ("UMBLER_TALK_ORGANIZATION_ID", ORGANIZATION_ID),
                               ("UMBLER_TALK_FROM_PHONE", FROM_PHONE)) if not v]
    return "Umbler Talk nao configurada -- falta: " + ", ".join(faltando)


def _e164(phone):
    digitos = "".join(c for c in str(phone or "") if c.isdigit())
    return f"+{digitos}" if digitos else ""


def usa_modelos():
    """Relatorio pelos modelos aprovados so' quando os dois ids (com PDF e sem PDF) estao preenchidos."""
    return bool(TEMPLATE_RELATORIO_ID and TEMPLATE_TEXTO_ID)


def _variavel(texto, limite=700):
    """Parametro de modelo nas regras da Meta: sem quebra de linha, sem espacos repetidos, nunca vazio."""
    texto = " - ".join(p.strip() for p in str(texto or "").replace("\t", " ").splitlines() if p.strip())
    texto = " ".join(texto.split())
    if len(texto) > limite:
        texto = texto[: limite - 3].rstrip() + "..."
    return texto or "-"


def _erro_http(exc):
    try:
        body = json.load(exc)
        if isinstance(body, dict):
            detalhes = body.get("errors")
            if isinstance(detalhes, dict):
                return "; ".join(f"{k}: {' '.join(v)}" for k, v in detalhes.items())
            return body.get("detail") or body.get("title") or str(exc)
    except Exception:
        pass
    return str(exc)


def _post_form(path, campos, arquivo=None, timeout=60):
    """POST multipart/form-data (o unico formato em que a Umbler aceita o arquivo binario).
    `campos` = lista de (nome, valor); `arquivo` = (nome_do_arquivo, mime, bytes) ou None."""
    fronteira = uuid.uuid4().hex
    partes = []
    for nome, valor in campos:
        if valor is None:
            continue
        partes.append(
            f'--{fronteira}\r\nContent-Disposition: form-data; name="{nome}"\r\n\r\n{valor}\r\n'.encode("utf-8"))
    if arquivo:
        nome_arq, mime, dados = arquivo
        partes.append(
            f'--{fronteira}\r\nContent-Disposition: form-data; name="File"; filename="{nome_arq}"\r\n'
            f"Content-Type: {mime}\r\n\r\n".encode("utf-8") + dados + b"\r\n")
    corpo = b"".join(partes) + f"--{fronteira}--\r\n".encode("utf-8")
    req = urllib.request.Request(
        f"{API_BASE}{path}", data=corpo, method="POST",
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": f"multipart/form-data; boundary={fronteira}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return True, json.load(resp)
    except urllib.error.HTTPError as exc:
        return False, f"Umbler Talk recusou ({exc.code}): {_erro_http(exc)}"
    except Exception as exc:
        return False, f"Umbler Talk nao respondeu: {exc}"


def _resultado(ok, resultado):
    """(ok, mensagem). O id da mensagem vai no texto do historico ("enviado [id]"): o webhook acha a
    linha por ele pra marcar entregue/lido/falhou -- mesmo esquema da API oficial."""
    if not ok:
        return False, resultado
    mid = resultado.get("id") if isinstance(resultado, dict) else None
    return True, f"enviado [{mid}]" if mid else "enviado"


def _base(phone):
    return [("ToPhone", _e164(phone)), ("FromPhone", FROM_PHONE), ("OrganizationId", ORGANIZATION_ID),
            ("SkipReassign", "true")]


def _checar(phone):
    if not phone:
        return "Numero de WhatsApp nao informado."
    if not _configurado():
        return _erro_configuracao()
    return None


def send_whatsapp(phone, text):
    erro = _checar(phone)
    if erro:
        return False, erro
    return _resultado(*_post_form("/messages/simplified/", _base(phone) + [("Message", (text or "")[:4096])]))


def send_whatsapp_document(phone, pdf_bytes, filename, caption=None):
    erro = _checar(phone)
    if erro:
        return False, erro
    campos = _base(phone) + ([("Message", caption[:4096])] if caption else [])
    return _resultado(*_post_form("/messages/simplified/", campos, (filename, "application/pdf", pdf_bytes)))


def send_whatsapp_image(phone, imagem_bytes, caption=None):
    erro = _checar(phone)
    if erro:
        return False, erro
    campos = _base(phone) + ([("Message", caption[:4096])] if caption else [])
    return _resultado(*_post_form("/messages/simplified/", campos, ("ndvi.png", "image/png", imagem_bytes)))


def _send_modelo(phone, template_id, variaveis, arquivo=None, limite=700):
    erro = _checar(phone)
    if erro:
        return False, erro
    campos = [c for c in _base(phone)] + [("TemplateId", template_id)]
    campos += [("Params", _variavel(v, limite)) for v in variaveis]
    return _resultado(*_post_form("/template-messages/simplified/", campos, arquivo))


def send_relatorio_modelo(phone, pdf_bytes, filename, variaveis):
    """Modelo com PDF no cabecalho: {{1}} nome, {{2}} fazenda, {{3}} data, {{4}} doencas."""
    if not TEMPLATE_RELATORIO_ID:
        return False, "Falta UMBLER_TALK_TEMPLATE_RELATORIO_ID."
    if not pdf_bytes:
        return False, "Modelo do relatorio precisa do PDF -- PDF nao foi gerado."
    return _send_modelo(phone, TEMPLATE_RELATORIO_ID, variaveis, (filename, "application/pdf", pdf_bytes))


def send_relatorio_texto_modelo(phone, variaveis):
    """Modelo sem cabecalho (plano so' com texto, ponto so' clima), mesmas 4 variaveis."""
    if not TEMPLATE_TEXTO_ID:
        return False, "Falta UMBLER_TALK_TEMPLATE_TEXTO_ID."
    return _send_modelo(phone, TEMPLATE_TEXTO_ID, variaveis)


def send_ndvi_modelo(phone, imagem_bytes, variaveis):
    """NDVI: com o modelo configurado, imagem no cabecalho + {{1}} nome, {{2}} fazenda, {{3}} data;
    sem ele, imagem com legenda (texto livre)."""
    if not TEMPLATE_NDVI_ID:
        nome, fazenda, data = (list(variaveis) + ["", "", ""])[:3]
        return send_whatsapp_image(phone, imagem_bytes, caption=f"🛰️ NDVI - {fazenda} - {data}")
    return _send_modelo(phone, TEMPLATE_NDVI_ID, variaveis, ("ndvi.png", "image/png", imagem_bytes), 200)


def get_status():
    """Mesmo formato do bridge ({"connected", "qr", "pairingCode", "error"}): confere o token na
    Umbler (GET /members/me/) e se a organizacao configurada esta entre as do token."""
    if not _configurado():
        return {"connected": False, "qr": None, "pairingCode": None, "error": _erro_configuracao()}
    req = urllib.request.Request(f"{API_BASE}/members/me/", headers={"Authorization": f"Bearer {TOKEN}"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.load(resp)
    except urllib.error.HTTPError as exc:
        return {"connected": False, "qr": None, "pairingCode": None,
                "error": f"Umbler Talk recusou o token ({exc.code}): {_erro_http(exc)}"}
    except Exception as exc:
        return {"connected": False, "qr": None, "pairingCode": None, "error": f"Umbler Talk nao respondeu: {exc}"}
    orgs = body.get("organizations") or []
    org = next((o for o in orgs if (o.get("id") or (o.get("organization") or {}).get("id")) == ORGANIZATION_ID), None)
    if orgs and not org:
        return {"connected": False, "qr": None, "pairingCode": None,
                "error": "Token aceito, mas UMBLER_TALK_ORGANIZATION_ID nao e' uma organizacao desse token."}
    nome = (org or {}).get("name") or ((org or {}).get("organization") or {}).get("name") or "Umbler Talk"
    return {"connected": True, "qr": None, "pairingCode": None, "error": f"Conectado: {nome} ({FROM_PHONE})"}


def parametros():
    """Variaveis da Umbler Talk preenchidas (tela Gerenciar WhatsApp) -- nunca o valor."""
    return [
        ("WHATSAPP_PROVIDER", "Liga a Umbler Talk (valor umbler)", os.environ.get("WHATSAPP_PROVIDER") == "umbler"),
        ("UMBLER_TALK_TOKEN", "Token da API (Perfil > Tokens de API)", bool(TOKEN)),
        ("UMBLER_TALK_ORGANIZATION_ID", "ID da organização", bool(ORGANIZATION_ID)),
        ("UMBLER_TALK_FROM_PHONE", "Número do canal que envia, ex.: +5543991147595", bool(FROM_PHONE)),
        ("UMBLER_TALK_TEMPLATE_RELATORIO_ID", "ID do modelo do relatório com PDF (opcional)", bool(TEMPLATE_RELATORIO_ID)),
        ("UMBLER_TALK_TEMPLATE_TEXTO_ID", "ID do modelo do relatório só texto (opcional)", bool(TEMPLATE_TEXTO_ID)),
        ("UMBLER_TALK_TEMPLATE_NDVI_ID", "ID do modelo do NDVI (opcional)", bool(TEMPLATE_NDVI_ID)),
        ("UMBLER_TALK_WEBHOOK_TOKEN", "Senha do webhook (você inventa; vai na URL)", bool(WEBHOOK_TOKEN)),
    ]


def ler_evento(evento):
    """Webhook da Umbler ({"Type", "EventDate", "Payload": {"Content": {...}}}): devolve
    (atualizacoes, recebidas) -- atualizacoes = [(id, estado_meta, erro)], recebidas =
    [(telefone, nome, texto)] das mensagens que o CONTATO mandou."""
    def pegar(d, *chaves):
        for c in chaves:
            if isinstance(d, dict) and c in d:
                return d[c]
        return None
    conteudo = pegar(pegar(evento, "Payload", "payload") or {}, "Content", "content") or {}
    msg = pegar(conteudo, "LastMessage", "lastMessage") or {}
    contato = pegar(conteudo, "Contact", "contact") or {}
    atualizacoes, recebidas = [], []
    if not msg:
        return atualizacoes, recebidas
    origem = (pegar(msg, "Source", "source") or "").lower()
    if origem == "contact":
        texto = pegar(msg, "Content", "content") or f"[{pegar(msg, 'MessageType', 'messageType') or 'mensagem'}]"
        recebidas.append((pegar(contato, "PhoneNumber", "phoneNumber"), pegar(contato, "Name", "name") or "",
                          str(texto)[:1000]))
    else:
        estado = ESTADOS.get(pegar(msg, "MessageState", "messageState") or "")
        mid = pegar(msg, "Id", "id")
        if estado and mid:
            atualizacoes.append((mid, estado, None))
    return atualizacoes, recebidas
