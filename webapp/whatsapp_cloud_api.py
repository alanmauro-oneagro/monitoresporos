"""Envio de WhatsApp via a API oficial da Meta (WhatsApp Business
Platform / Cloud API) -- alternativa ao whatsapp-bridge (Baileys, um
"aparelho vinculado" nao-oficial) pra quando o volume de envios crescer
o bastante pra' o risco de retencao de entrega do bridge (ver comentario
de MIN_DELAY_MS em whatsapp-bridge/index.js, e o combinar-texto-e-pdf-
num-so-envio em app.py._send_site_whatsapp) deixar de valer a pena.

ANTES DE USAR (fora do escopo deste modulo, so' a Meta resolve):
1. Criar/verificar uma conta comercial no Meta Business Manager
   (business.facebook.com) -- exige CNPJ, pode levar dias.
2. Criar um WhatsApp Business Account (WABA) e registrar um NUMERO NOVO
   (nao da pra migrar o numero pessoal que o bridge usa hoje).
3. Gerar um token de acesso (permanente, de um System User -- nao o
   token temporario de teste) com permissao `whatsapp_business_messaging`.
4. Preencher as variaveis de ambiente:
   - WHATSAPP_PROVIDER=cloud_api (troca o dispatch em whatsapp.py --
     SEM isso, nada aqui e' usado, o sistema continua no bridge normal)
   - WHATSAPP_CLOUD_ACCESS_TOKEN
   - WHATSAPP_CLOUD_PHONE_NUMBER_ID (o ID interno do numero, NAO o
     numero de telefone em si -- aparece no painel do WABA)

O QUE FUNCIONA DESDE JA' (mensagem live, sem nada alem das variaveis
acima): texto/documento/imagem em FORMATO LIVRE -- exatamente igual ao
bridge hoje, mesma assinatura (`send_whatsapp`, `send_whatsapp_document`,
`send_whatsapp_image`). Serve pra testar a integracao e pra responder
dentro de uma janela de conversa aberta (ultimas 24h desde a ultima
mensagem QUE O CLIENTE mandou pro numero).

O QUE AINDA FALTA (o motivo de nao dar so' pra trocar WHATSAPP_PROVIDER
e sair rodando os relatorios agendados): a API oficial NAO permite
mensagem em formato livre iniciada pela empresa fora dessa janela de
24h -- exatamente o caso dos relatorios agendados (`_run_scheduled_whatsapp_sends`
em app.py, ninguem mandou nada pro numero antes). Pra isso, a Meta exige
um MODELO DE MENSAGEM (template) pre-aprovado (Business Manager >
WhatsApp Manager > Modelos de mensagem -- avaliacao pode levar de
minutos a alguns dias). `send_whatsapp_template` abaixo ja' implementa a
chamada de API pra mandar um template (funciona assim que houver um
aprovado) -- mas `_send_site_whatsapp` em app.py ainda manda o relatorio
como texto corrido, nao como variaveis de template, entao adaptar o
relatorio agendado pra usar template e' o proximo passo, depois de ter
um modelo aprovado com o formato de variaveis que fizer sentido pro
relatorio."""
import json
import mimetypes
import os
import urllib.error
import urllib.request
import uuid

CLOUD_API_VERSION = os.environ.get("WHATSAPP_CLOUD_API_VERSION", "v21.0")
ACCESS_TOKEN = os.environ.get("WHATSAPP_CLOUD_ACCESS_TOKEN")
PHONE_NUMBER_ID = os.environ.get("WHATSAPP_CLOUD_PHONE_NUMBER_ID")
GRAPH_BASE = f"https://graph.facebook.com/{CLOUD_API_VERSION}"


def _configurado():
    return bool(ACCESS_TOKEN and PHONE_NUMBER_ID)


def _normalize_phone(phone):
    """So' digitos (com codigo do pais) -- a Cloud API nao quer "+" nem
    espacos/parenteses."""
    return "".join(c for c in str(phone) if c.isdigit())


def _erro_configuracao():
    faltando = []
    if not ACCESS_TOKEN:
        faltando.append("WHATSAPP_CLOUD_ACCESS_TOKEN")
    if not PHONE_NUMBER_ID:
        faltando.append("WHATSAPP_CLOUD_PHONE_NUMBER_ID")
    return f"API oficial do WhatsApp nao configurada -- falta(m): {', '.join(faltando)}."


def _post_json(path, payload, timeout=25):
    """POST autenticado (Bearer) com corpo JSON -- devolve (ok, body_dict
    ou mensagem de erro). Formato de erro da Graph API:
    {"error": {"message": ..., "type": ..., "code": ..., "fbtrace_id": ...}}."""
    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{GRAPH_BASE}/{path}",
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {ACCESS_TOKEN}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return True, json.load(resp)
    except urllib.error.HTTPError as exc:
        try:
            body = json.load(exc)
            return False, body.get("error", {}).get("message", str(exc))
        except Exception:
            return False, str(exc)
    except Exception as exc:
        return False, f"API oficial do WhatsApp nao respondeu: {exc}"


def _upload_media(file_bytes, mime_type, filename):
    """Sobe um arquivo (PDF/imagem) pro endpoint de midia da Cloud API
    ANTES de poder mandar como documento/imagem -- a API nao aceita bytes
    direto na mensagem, so' um `media id` ja' hospedado por ela (valido
    por tempo limitado). Precisa de multipart/form-data -- montado na mao
    (sem lib externa, mesmo padrao "so' stdlib" do resto do projeto).
    Retorna (ok, media_id ou mensagem de erro)."""
    boundary = uuid.uuid4().hex
    partes = []

    def _campo(nome, valor):
        partes.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{nome}"\r\n\r\n{valor}\r\n'.encode("utf-8")
        )

    _campo("messaging_product", "whatsapp")
    _campo("type", mime_type)
    partes.append(
        (
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            f"Content-Type: {mime_type}\r\n\r\n"
        ).encode("utf-8")
    )
    partes.append(file_bytes)
    partes.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))
    corpo = b"".join(partes)

    try:
        req = urllib.request.Request(
            f"{GRAPH_BASE}/{PHONE_NUMBER_ID}/media",
            data=corpo,
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Authorization": f"Bearer {ACCESS_TOKEN}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=45) as resp:
            body = json.load(resp)
            media_id = body.get("id")
            return (True, media_id) if media_id else (False, "Upload nao devolveu media id.")
    except urllib.error.HTTPError as exc:
        try:
            body = json.load(exc)
            return False, body.get("error", {}).get("message", str(exc))
        except Exception:
            return False, str(exc)
    except Exception as exc:
        return False, f"API oficial do WhatsApp nao respondeu (upload): {exc}"


def get_status():
    """Equivalente ao `whatsapp.get_status()` do bridge, no mesmo formato
    ({"connected", "qr", "pairingCode", "error"}) pra' a tela
    Configuracoes > WhatsApp continuar funcionando sem mudanca -- aqui
    "connected" significa "as variaveis de ambiente estao preenchidas E a
    Graph API confirmou o numero" (nao ha' QR code/pareamento na API
    oficial, os dois campos ficam sempre None)."""
    if not _configurado():
        return {"connected": False, "qr": None, "pairingCode": None, "error": _erro_configuracao()}
    try:
        req = urllib.request.Request(
            f"{GRAPH_BASE}/{PHONE_NUMBER_ID}?fields=display_phone_number,verified_name",
            headers={"Authorization": f"Bearer {ACCESS_TOKEN}"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.load(resp)
        numero = body.get("display_phone_number", "?")
        nome = body.get("verified_name", "?")
        return {"connected": True, "qr": None, "pairingCode": None, "error": f"Conectado: {nome} ({numero})"}
    except urllib.error.HTTPError as exc:
        try:
            body = json.load(exc)
            erro = body.get("error", {}).get("message", str(exc))
        except Exception:
            erro = str(exc)
        return {"connected": False, "qr": None, "pairingCode": None, "error": f"Token/ID configurados, mas a Graph API recusou: {erro}"}
    except Exception as exc:
        return {"connected": False, "qr": None, "pairingCode": None, "error": f"Nao foi possivel confirmar com a Graph API: {exc}"}


def send_whatsapp(phone, text):
    """Mensagem de texto livre -- MESMA assinatura/retorno (ok, mensagem)
    de `whatsapp.send_whatsapp`. So' funciona dentro da janela de 24h de
    conversa aberta (ver docstring do modulo) -- fora dela a Graph API
    recusa com um erro claro (ex. code 131047), que volta como a
    `mensagem` de retorno."""
    if not phone:
        return False, "Numero de WhatsApp nao informado."
    if not _configurado():
        return False, _erro_configuracao()
    ok, resultado = _post_json(f"{PHONE_NUMBER_ID}/messages", {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": _normalize_phone(phone),
        "type": "text",
        "text": {"body": text, "preview_url": False},
    })
    return (True, "enviado") if ok else (False, resultado)


def send_whatsapp_document(phone, pdf_bytes, filename, caption=None):
    """PDF anexado -- MESMA assinatura/retorno de
    `whatsapp.send_whatsapp_document`. Sobe o arquivo primeiro
    (`_upload_media`), so' depois manda a mensagem referenciando o media
    id -- a Cloud API nao aceita bytes direto na mensagem."""
    if not phone:
        return False, "Numero de WhatsApp nao informado."
    if not _configurado():
        return False, _erro_configuracao()
    ok, media_id_ou_erro = _upload_media(pdf_bytes, "application/pdf", filename)
    if not ok:
        return False, f"Falha no upload do PDF: {media_id_ou_erro}"
    documento = {"id": media_id_ou_erro, "filename": filename}
    if caption:
        documento["caption"] = caption
    ok, resultado = _post_json(f"{PHONE_NUMBER_ID}/messages", {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": _normalize_phone(phone),
        "type": "document",
        "document": documento,
    })
    return (True, "enviado") if ok else (False, resultado)


def send_whatsapp_image(phone, imagem_bytes, caption=None):
    """Imagem anexada (vira foto com preview, nao arquivo) -- MESMA
    assinatura/retorno de `whatsapp.send_whatsapp_image`."""
    if not phone:
        return False, "Numero de WhatsApp nao informado."
    if not _configurado():
        return False, _erro_configuracao()
    mime_type = mimetypes.guess_type("img.png")[0] or "image/png"
    ok, media_id_ou_erro = _upload_media(imagem_bytes, mime_type, "img.png")
    if not ok:
        return False, f"Falha no upload da imagem: {media_id_ou_erro}"
    imagem = {"id": media_id_ou_erro}
    if caption:
        imagem["caption"] = caption
    ok, resultado = _post_json(f"{PHONE_NUMBER_ID}/messages", {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": _normalize_phone(phone),
        "type": "image",
        "image": imagem,
    })
    return (True, "enviado") if ok else (False, resultado)


# Modelo do relatorio (aprovado no WhatsApp Manager -- categoria Utilidade,
# cabecalho DOCUMENTO com o PDF, corpo com 6 variaveis, botao de URL fixa
# pro site). Nome/idioma configuraveis, pra' trocar sem mexer no codigo.
TEMPLATE_RELATORIO = os.environ.get("WHATSAPP_CLOUD_TEMPLATE_RELATORIO", "relatorio_fazenda")
TEMPLATE_IDIOMA = os.environ.get("WHATSAPP_CLOUD_TEMPLATE_IDIOMA", "pt_BR")


def _variavel(texto, limite=200):
    """Valor de variavel de modelo dentro das regras da Meta: sem quebra
    de linha nem tab, sem mais de 4 espacos seguidos, nunca vazio (a API
    recusa parametro vazio) e curto."""
    texto = " - ".join(p.strip() for p in str(texto or "").replace("\t", " ").splitlines() if p.strip())
    texto = " ".join(texto.split())
    if len(texto) > limite:
        texto = texto[: limite - 3].rstrip() + "..."
    return texto or "-"


def send_relatorio_modelo(phone, pdf_bytes, filename, variaveis):
    """Relatorio agendado/manual pelo MODELO aprovado (`TEMPLATE_RELATORIO`):
    PDF completo como cabecalho DOCUMENTO + resumo nas variaveis do corpo
    ({{1}}..{{6}}, na ordem de `variaveis`). E' o unico jeito da empresa
    INICIAR a conversa fora da janela de 24h. Mesmo retorno (ok, mensagem)
    das outras funcoes de envio."""
    if not phone:
        return False, "Numero de WhatsApp nao informado."
    if not _configurado():
        return False, _erro_configuracao()
    if not pdf_bytes:
        return False, "Modelo do relatorio precisa do PDF (cabecalho documento) -- PDF nao foi gerado."
    ok, media_id_ou_erro = _upload_media(pdf_bytes, "application/pdf", filename)
    if not ok:
        return False, f"Falha no upload do PDF: {media_id_ou_erro}"
    components = [
        {"type": "header", "parameters": [
            {"type": "document", "document": {"id": media_id_ou_erro, "filename": filename}},
        ]},
        {"type": "body", "parameters": [{"type": "text", "text": _variavel(v)} for v in variaveis]},
    ]
    return send_whatsapp_template(phone, TEMPLATE_RELATORIO, TEMPLATE_IDIOMA, components)


def send_whatsapp_template(phone, template_name, language_code="pt_BR", components=None):
    """Mensagem via MODELO pre-aprovado -- o UNICO tipo que a Meta deixa
    a empresa iniciar fora da janela de 24h (ver docstring do modulo,
    "O QUE AINDA FALTA"). `template_name` precisa bater EXATAMENTE com o
    nome do modelo aprovado no WhatsApp Manager; `components` (lista, no
    formato que a Graph API espera pra' header/body/button) preenche as
    variaveis do modelo -- None quando o modelo nao tem nenhuma. Usado
    pelo relatorio (`send_relatorio_modelo`), chamado por
    `app._send_site_whatsapp` quando WHATSAPP_PROVIDER=cloud_api."""
    if not phone:
        return False, "Numero de WhatsApp nao informado."
    if not _configurado():
        return False, _erro_configuracao()
    template = {"name": template_name, "language": {"code": language_code}}
    if components:
        template["components"] = components
    ok, resultado = _post_json(f"{PHONE_NUMBER_ID}/messages", {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": _normalize_phone(phone),
        "type": "template",
        "template": template,
    })
    return (True, "enviado") if ok else (False, resultado)
