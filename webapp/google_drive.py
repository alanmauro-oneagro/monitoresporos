"""Copia diaria dos relatorios (PDF, TXT e NDVI) pra pasta "Relatórios OneAgro" do Google Drive do Alan
(pedido do Alan, 26/11/2026). Conexao por OAuth: o Master clica em "Conectar Google Drive" em Gerenciar
Exportações, entra com a conta Google e autoriza uma vez; o refresh token fica no banco (setting
`gdrive_refresh_token`) e o site pega um token de acesso novo sozinho a cada envio.

Variaveis no Railway (do "ID do cliente OAuth" criado no Google Cloud Console, tipo "Aplicativo da Web"):
`GOOGLE_DRIVE_CLIENT_ID` e `GOOGLE_DRIVE_CLIENT_SECRET`. So' urllib (sem biblioteca do Google)."""
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

CLIENT_ID = os.environ.get("GOOGLE_DRIVE_CLIENT_ID", "").strip()
CLIENT_SECRET = os.environ.get("GOOGLE_DRIVE_CLIENT_SECRET", "").strip()
# Acesso ao Drive inteiro: a pasta foi criada pelo Alan (nao pelo site), e o escopo "drive.file" so'
# enxerga arquivos criados pelo proprio site.
ESCOPO = "https://www.googleapis.com/auth/drive"
PASTA_RAIZ = "Relatórios OneAgro"
SUBPASTAS = ("PDF", "TXT", "NDVI")
_PASTA_MIME = "application/vnd.google-apps.folder"
_TIMEOUT = 60

_cache_token = {"refresh": None, "token": None, "expira": 0}
_trava_token = threading.Lock()


class ErroDrive(Exception):
    pass


def configurado():
    return bool(CLIENT_ID and CLIENT_SECRET)


def url_autorizacao(redirect_uri, state):
    return "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
        "client_id": CLIENT_ID, "redirect_uri": redirect_uri, "response_type": "code", "scope": ESCOPO,
        "access_type": "offline", "prompt": "consent", "include_granted_scopes": "true", "state": state,
    })


def _post_form(url, dados):
    req = urllib.request.Request(url, data=urllib.parse.urlencode(dados).encode(),
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            return json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        raise ErroDrive(_mensagem_http(e)) from e
    except urllib.error.URLError as e:
        raise ErroDrive(f"sem conexão com o Google ({e.reason})") from e


def _mensagem_http(e):
    try:
        corpo = json.loads(e.read().decode() or "{}")
        erro = corpo.get("error")
        if isinstance(erro, dict):
            return f"{e.code}: {erro.get('message') or erro}"
        return f"{e.code}: {corpo.get('error_description') or erro or corpo}"
    except Exception:
        return f"erro {e.code} do Google"


def trocar_codigo(code, redirect_uri):
    """Codigo da volta do login -> {"refresh_token", "access_token", "expires_in"}."""
    resp = _post_form("https://oauth2.googleapis.com/token", {
        "code": code, "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
        "redirect_uri": redirect_uri, "grant_type": "authorization_code",
    })
    if not resp.get("refresh_token"):
        raise ErroDrive("o Google não devolveu a autorização permanente -- tente conectar de novo")
    return resp


def token_de_acesso(refresh_token):
    with _trava_token:
        if _cache_token["refresh"] == refresh_token and _cache_token["expira"] > time.time() + 60:
            return _cache_token["token"]
        resp = _post_form("https://oauth2.googleapis.com/token", {
            "refresh_token": refresh_token, "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
            "grant_type": "refresh_token",
        })
        _cache_token.update(refresh=refresh_token, token=resp["access_token"],
                            expira=time.time() + int(resp.get("expires_in", 3600)))
        return resp["access_token"]


def revogar(refresh_token):
    try:
        _post_form("https://oauth2.googleapis.com/revoke", {"token": refresh_token})
    except ErroDrive:
        pass
    _cache_token.update(refresh=None, token=None, expira=0)


def _api(token, metodo, url, corpo=None, tipo=None):
    headers = {"Authorization": f"Bearer {token}"}
    if tipo:
        headers["Content-Type"] = tipo
    req = urllib.request.Request(url, data=corpo, headers=headers, method=metodo)
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            return json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        raise ErroDrive(_mensagem_http(e)) from e
    except urllib.error.URLError as e:
        raise ErroDrive(f"sem conexão com o Google Drive ({e.reason})") from e


def email_da_conta(token):
    return _api(token, "GET", "https://www.googleapis.com/drive/v3/about?fields=user(emailAddress)")\
        .get("user", {}).get("emailAddress", "")


def _aspas(texto):
    return texto.replace("\\", "\\\\").replace("'", "\\'")


def _buscar(token, consulta):
    url = "https://www.googleapis.com/drive/v3/files?" + urllib.parse.urlencode({
        "q": consulta, "fields": "files(id,name)", "pageSize": 10, "orderBy": "createdTime",
        "supportsAllDrives": "true", "includeItemsFromAllDrives": "true",
    })
    return _api(token, "GET", url).get("files", [])


def _pasta(token, nome, pai=None):
    consulta = f"name = '{_aspas(nome)}' and mimeType = '{_PASTA_MIME}' and trashed = false"
    if pai:
        consulta += f" and '{pai}' in parents"
    achadas = _buscar(token, consulta)
    return achadas[0]["id"] if achadas else None


def _criar_pasta(token, nome, pai=None):
    meta = {"name": nome, "mimeType": _PASTA_MIME}
    if pai:
        meta["parents"] = [pai]
    return _api(token, "POST", "https://www.googleapis.com/drive/v3/files?supportsAllDrives=true",
                json.dumps(meta).encode(), "application/json")["id"]


def pastas(token):
    """{"raiz", "PDF", "TXT", "NDVI": id}. A pasta "Relatórios OneAgro" que o Alan criou e' achada pelo nome;
    subpasta que faltar e' criada dentro dela (a raiz tambem, se nao existir)."""
    raiz = _pasta(token, PASTA_RAIZ) or _criar_pasta(token, PASTA_RAIZ)
    ids = {"raiz": raiz}
    for nome in SUBPASTAS:
        ids[nome] = _pasta(token, nome, raiz) or _criar_pasta(token, nome, raiz)
    return ids


def enviar_arquivo(token, pasta_id, nome, conteudo, mime):
    """Grava `nome` na pasta; se ja' existe um arquivo com esse nome la' (ex.: "Enviar agora" duas vezes no dia),
    substitui o conteudo em vez de criar outro igual."""
    existente = _buscar(token, f"name = '{_aspas(nome)}' and '{pasta_id}' in parents and trashed = false")
    fronteira = uuid.uuid4().hex
    meta = {"name": nome} if existente else {"name": nome, "parents": [pasta_id]}
    corpo = (f"--{fronteira}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n{json.dumps(meta)}\r\n"
             f"--{fronteira}\r\nContent-Type: {mime}\r\n\r\n").encode() + conteudo + f"\r\n--{fronteira}--".encode()
    tipo = f"multipart/related; boundary={fronteira}"
    if existente:
        url = (f"https://www.googleapis.com/upload/drive/v3/files/{existente[0]['id']}"
               "?uploadType=multipart&supportsAllDrives=true")
        return _api(token, "PATCH", url, corpo, tipo)["id"]
    url = "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart&supportsAllDrives=true"
    return _api(token, "POST", url, corpo, tipo)["id"]
