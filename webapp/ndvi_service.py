"""Busca de imagens NDVI (Sentinel-2, via Copernicus Data Space Ecosystem)
pro contorno KML cadastrado de cada fazenda na aba NDVI.

O SICAR (onde o contorno da propriedade e' obtido a partir do CAR) nao tem
API publica de consulta por ponto -- por isso o contorno precisa ser baixado
manualmente em consulta.car.gov.br (ou consultapublica.car.gov.br) e colado/
enviado aqui uma vez por fazenda; a partir dai a busca do NDVI e' automatica.

Contas Copernicus tambem nao podem ser criadas por este app -- o usuario
precisa criar uma conta gratuita em https://dataspace.copernicus.eu/, gerar
um "OAuth client" (Dashboard > User Settings > OAuth clients) e definir
COPERNICUS_CLIENT_ID / COPERNICUS_CLIENT_SECRET nas variaveis de ambiente
(mesmo padrao usado por ADMIN_BOOTSTRAP_USERNAME/BIOSCOUT_WEB_SECRET). Sem
essas variaveis, `buscar_ndvi` devolve (None, mensagem) em vez de quebrar."""
import io
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timedelta, timezone

import shapefile

TOKEN_URL = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
PROCESS_URL = "https://sh.dataspace.copernicus.eu/api/v1/process"

TOKEN_CACHE_MARGIN_SECONDS = 60  # renova um pouco antes do token expirar de verdade

KML_NS = {"kml": "http://www.opengis.net/kml/2.2"}

_token_cache = {"token": None, "expira_em": 0.0}

# NDVI = (B08 - B04) / (B08 + B04). SCL (Scene Classification, Sentinel-2 L2A):
# 3 sombra de nuvem, 8/9 nuvem media/alta, 10 cirrus, 11 neve -- tudo isso vira
# transparente em vez de colorido, pra nao mostrar "vegetacao" onde na verdade
# tem nuvem cobrindo a imagem.
EVALSCRIPT_NDVI = """
//VERSION=3
function setup() {
  return {
    input: [{ bands: ["B04", "B08", "SCL", "dataMask"] }],
    output: { bands: 4 }
  };
}

var RAMPA = [
  [-1.0, [0.05, 0.05, 0.05]],
  [0.0,  [0.65, 0.35, 0.15]],
  [0.2,  [0.90, 0.80, 0.40]],
  [0.4,  [0.65, 0.85, 0.30]],
  [0.6,  [0.20, 0.65, 0.15]],
  [1.0,  [0.00, 0.30, 0.00]]
];

function corDaRampa(v) {
  for (var i = 1; i < RAMPA.length; i++) {
    if (v <= RAMPA[i][0]) {
      var v0 = RAMPA[i - 1][0], c0 = RAMPA[i - 1][1];
      var v1 = RAMPA[i][0], c1 = RAMPA[i][1];
      var t = (v - v0) / (v1 - v0);
      return [
        c0[0] + t * (c1[0] - c0[0]),
        c0[1] + t * (c1[1] - c0[1]),
        c0[2] + t * (c1[2] - c0[2])
      ];
    }
  }
  return RAMPA[RAMPA.length - 1][1];
}

function evaluatePixel(s) {
  var nublado = [3, 8, 9, 10, 11].indexOf(s.SCL) !== -1;
  if (s.dataMask === 0 || nublado) {
    return [0, 0, 0, 0];
  }
  var ndvi = (s.B08 - s.B04) / (s.B08 + s.B04 + 1e-6);
  var cor = corDaRampa(Math.max(-1, Math.min(1, ndvi)));
  return [cor[0], cor[1], cor[2], 1];
}
"""


def credenciais_configuradas():
    return bool(os.environ.get("COPERNICUS_CLIENT_ID") and os.environ.get("COPERNICUS_CLIENT_SECRET"))


def parse_kml_poligono(kml_texto):
    """Extrai o anel externo de todo <Polygon> do KML, como lista de aneis
    no formato [[lon, lat], ...] (GeoJSON, sem altitude). Aceita KML com ou
    sem namespace (alguns exports, ex. SICAR, omitem o xmlns). Levanta
    ValueError com mensagem amigavel se nao achar nenhum poligono valido."""
    texto = (kml_texto or "").strip()
    if not texto:
        raise ValueError("Arquivo KML vazio.")
    try:
        root = ET.fromstring(texto)
    except ET.ParseError as exc:
        raise ValueError(f"KML invalido: {exc}") from exc

    def achar_todos(tag):
        achados = root.findall(f".//kml:{tag}", KML_NS)
        return achados if achados else root.findall(f".//{tag}")

    aneis = []
    for poligono in achar_todos("Polygon"):
        outer = poligono.find("kml:outerBoundaryIs/kml:LinearRing/kml:coordinates", KML_NS)
        if outer is None:
            outer = poligono.find("outerBoundaryIs/LinearRing/coordinates")
        if outer is None or not outer.text:
            continue
        anel = []
        for ponto in outer.text.strip().split():
            partes = ponto.split(",")
            if len(partes) < 2:
                continue
            try:
                lon, lat = float(partes[0]), float(partes[1])
            except ValueError:
                continue
            anel.append([lon, lat])
        if len(anel) >= 3:
            aneis.append(anel)

    if not aneis:
        raise ValueError("Nenhum poligono encontrado no KML (esperado <Polygon><outerBoundaryIs>).")
    return aneis


def parse_shapefile_zip(zip_bytes, _profundidade=0):
    """Extrai o(s) anel(is) externo(s) de um shapefile (.zip com .shp/.shx/
    .dbf/.prj) -- formato que o SICAR baixa quando "Baixar feicoes" nao
    oferece a opcao de KML. So' aceita shapefile em coordenadas geograficas
    (lat/lon, datum SIRGAS2000 no caso do CAR) -- projetado (ex. UTM) nao e'
    suportado, pra nao precisar de uma biblioteca de reprojecao (pyproj) so'
    pra esse caso raro. Se o .shp nao estiver na raiz (o SICAR as vezes
    entrega um .zip com outro .zip dentro, por feicao), procura recursivamente
    dentro de qualquer .zip aninhado, ate 3 niveis de profundidade."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as exc:
        raise ValueError(f"Arquivo .zip invalido: {exc}") from exc

    por_extensao_lower = {n.lower(): n for n in zf.namelist()}
    shp_key = next((k for k in por_extensao_lower if k.endswith(".shp")), None)
    if not shp_key:
        if _profundidade < 3:
            for nome_interno in zf.namelist():
                if nome_interno.lower().endswith(".zip"):
                    try:
                        return parse_shapefile_zip(zf.read(nome_interno), _profundidade + 1)
                    except ValueError:
                        continue
        raise ValueError("Nenhum arquivo .shp encontrado dentro do .zip.")
    base = shp_key[:-4]
    shp_nome = por_extensao_lower[shp_key]
    shx_nome = por_extensao_lower.get(base + ".shx")
    dbf_nome = por_extensao_lower.get(base + ".dbf")
    prj_nome = por_extensao_lower.get(base + ".prj")

    if prj_nome:
        prj_texto = zf.read(prj_nome).decode("ascii", errors="replace").upper()
        if "PROJCS" in prj_texto:
            raise ValueError(
                "Shapefile esta em coordenadas projetadas (ex. UTM) -- so' e' "
                "suportado shapefile em coordenadas geograficas (lat/lon)."
            )

    leitor = shapefile.Reader(
        shp=io.BytesIO(zf.read(shp_nome)),
        shx=io.BytesIO(zf.read(shx_nome)) if shx_nome else None,
        dbf=io.BytesIO(zf.read(dbf_nome)) if dbf_nome else None,
    )

    aneis = []
    for forma in leitor.shapes():
        if forma.shapeType not in (shapefile.POLYGON, shapefile.POLYGONZ, shapefile.POLYGONM):
            continue
        limites = list(forma.parts) + [len(forma.points)]
        for i in range(len(limites) - 1):
            anel = [[pt[0], pt[1]] for pt in forma.points[limites[i]:limites[i + 1]]]
            if len(anel) >= 3:
                aneis.append(anel)

    if not aneis:
        raise ValueError("Nenhum poligono encontrado no shapefile.")
    return aneis


def aneis_para_kml(aneis):
    """Serializa aneis [[lon, lat], ...] como KML minimo -- usado pra sempre
    guardar o mesmo formato em `farm_ndvi_area.kml`, independente da origem
    ter sido um KML de verdade ou um shapefile convertido."""
    placemarks = "".join(
        "<Placemark><Polygon><outerBoundaryIs><LinearRing><coordinates>"
        + " ".join(f"{lon},{lat},0" for lon, lat in anel)
        + "</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>"
        for anel in aneis
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
        f"{placemarks}</Document></kml>"
    )


def _bbox(aneis):
    lons = [pt[0] for anel in aneis for pt in anel]
    lats = [pt[1] for anel in aneis for pt in anel]
    return min(lons), min(lats), max(lons), max(lats)


def _obter_token():
    """Devolve (token, erro) -- so' um dos dois e' preenchido. Antes essa
    funcao devolvia so' None em qualquer falha (env var ausente, credencial
    errada, rede fora), e quem chamava sempre mostrava a mensagem de "nao
    configurado" mesmo quando as variaveis estavam certas mas a autenticacao
    falhava por outro motivo (ex. client_secret invalido/revogado) --
    confirmado com um caso real onde as variaveis estavam corretas no
    Railway mas a mensagem de "nao configurado" continuava aparecendo."""
    agora = time.time()
    if _token_cache["token"] and agora < _token_cache["expira_em"]:
        return _token_cache["token"], None

    client_id = os.environ.get("COPERNICUS_CLIENT_ID")
    client_secret = os.environ.get("COPERNICUS_CLIENT_SECRET")
    if not client_id or not client_secret:
        return None, (
            "credenciais nao configuradas (variaveis de ambiente "
            "COPERNICUS_CLIENT_ID / COPERNICUS_CLIENT_SECRET)"
        )

    corpo = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
    }).encode()
    req = urllib.request.Request(TOKEN_URL, data=corpo, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            resposta = json.load(resp)
    except urllib.error.HTTPError as exc:
        detalhe = exc.read().decode(errors="replace")[:300]
        return None, f"Copernicus recusou a autenticacao (HTTP {exc.code}): {detalhe}"
    except Exception as exc:
        return None, f"falha de rede ao autenticar na Copernicus: {exc}"

    token = resposta.get("access_token")
    if not token:
        return None, "resposta da Copernicus sem access_token"
    _token_cache["token"] = token
    _token_cache["expira_em"] = agora + resposta.get("expires_in", 300) - TOKEN_CACHE_MARGIN_SECONDS
    return token, None


def _orientacao(a, b, c):
    val = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    if abs(val) < 1e-12:
        return 0
    return 1 if val > 0 else 2


def _no_segmento(a, b, c):
    return min(a[0], b[0]) <= c[0] <= max(a[0], b[0]) and min(a[1], b[1]) <= c[1] <= max(a[1], b[1])


def _segmentos_se_cruzam(p1, p2, p3, p4):
    o1, o2 = _orientacao(p1, p2, p3), _orientacao(p1, p2, p4)
    o3, o4 = _orientacao(p3, p4, p1), _orientacao(p3, p4, p2)
    if o1 != o2 and o3 != o4:
        return True
    if o1 == 0 and _no_segmento(p1, p2, p3):
        return True
    if o2 == 0 and _no_segmento(p1, p2, p4):
        return True
    if o3 == 0 and _no_segmento(p3, p4, p1):
        return True
    if o4 == 0 and _no_segmento(p3, p4, p2):
        return True
    return False


def anel_auto_intersecta(anel):
    """Testa (O(n^2), pontos suficientes pra um contorno de propriedade)
    se algum par de lados nao-adjacentes do anel se cruza -- usado so' pra
    diagnostico (rota /ndvi/debug), ja que a Copernicus rejeita poligono
    auto-intersectante sem dizer QUAIS vertices sao o problema."""
    n = len(anel) - 1  # ultimo ponto repete o primeiro (anel fechado)
    for i in range(n):
        for j in range(i + 1, n):
            if j == i + 1 or (i == 0 and j == n - 1):
                continue  # lados adjacentes sempre "se tocam" no vertice em comum
            if _segmentos_se_cruzam(anel[i], anel[i + 1], anel[j], anel[j + 1]):
                return True
    return False


def _anti_horario(anel):
    """GeoJSON (RFC 7946) exige o anel externo no sentido anti-horario --
    shapefile (ESRI) usa o sentido horario por convencao, entao um contorno
    vindo de .zip/shapefile chega invertido e a Copernicus recusa o
    poligono como invalido (COMMON_BAD_PAYLOAD) sem essa correcao. KML ja
    costuma vir no sentido certo, mas normalizar sempre e' inofensivo (so'
    inverte a ordem dos pontos, nao muda a forma)."""
    soma = sum((anel[i + 1][0] - anel[i][0]) * (anel[i + 1][1] + anel[i][1]) for i in range(len(anel) - 1))
    return anel if soma < 0 else list(reversed(anel))


def _resumir_erro_process_api(corpo_erro):
    """A resposta de erro da Process API inclui o poligono inteiro em
    "invalidValue" -- isso enchia os 300 caracteres que a mensagem de erro
    mostrava antes, escondendo o motivo especifico (que costuma vir depois
    das coordenadas). Aqui remove "invalidValue" de cada item de erro antes
    de montar a mensagem, pra sobrar espaco pro que realmente importa."""
    try:
        dados = json.loads(corpo_erro)
    except ValueError:
        return corpo_erro[:500]
    erro = dados.get("error", dados)
    partes = [erro.get("message", "erro sem mensagem")]
    for item in erro.get("errors", []):
        resumo = {k: v for k, v in item.items() if k != "invalidValue"}
        if resumo:
            partes.append(str(resumo))
    return " -- ".join(partes)


def buscar_ndvi(aneis, dias_historico=30, largura_px=512):
    """Busca a imagem NDVI (menor cobertura de nuvem nos ultimos
    `dias_historico` dias) pro poligono dado. Devolve (bytes_png, None) em
    caso de sucesso, ou (None, mensagem_de_erro) -- nunca levanta excecao,
    pra rota poder mostrar uma mensagem amigavel em vez de quebrar a
    pagina."""
    token, erro = _obter_token()
    if not token:
        return None, f"Nao foi possivel autenticar na Copernicus Data Space Ecosystem: {erro}."

    aneis = [_anti_horario(anel) for anel in aneis]
    min_lon, min_lat, max_lon, max_lat = _bbox(aneis)
    largura_graus = max(max_lon - min_lon, 0.0005)
    altura_graus = max(max_lat - min_lat, 0.0005)
    altura_px = max(1, round(largura_px * altura_graus / largura_graus))

    hoje = datetime.now(timezone.utc).date()
    desde = hoje - timedelta(days=dias_historico)

    if len(aneis) == 1:
        # GeoJSON Polygon: 1 anel externo (buracos exigiriam aneis
        # aninhados DENTRO dele, o que nao suportamos).
        geometria = {"type": "Polygon", "coordinates": [aneis[0]]}
    else:
        # Mais de 1 anel = propriedade com partes separadas (ex. talhoes
        # nao contiguos no mesmo registro CAR) -- cada anel vira um Polygon
        # independente dentro de um MultiPolygon. Empilhar todos como se
        # fossem "buracos" de um unico Polygon (como era antes) e' invalido
        # quando os aneis nao estao aninhados um dentro do outro, e a
        # Copernicus rejeitava com "Polygon rings are intersecting".
        geometria = {"type": "MultiPolygon", "coordinates": [[anel] for anel in aneis]}

    corpo = {
        "input": {
            "bounds": {
                "geometry": geometria,
                "properties": {"crs": "http://www.opengis.net/def/crs/OGC/1.3/CRS84"},
            },
            "data": [{
                "type": "sentinel-2-l2a",
                "dataFilter": {
                    "timeRange": {
                        "from": f"{desde.isoformat()}T00:00:00Z",
                        "to": f"{hoje.isoformat()}T23:59:59Z",
                    },
                    "mosaickingOrder": "leastCC",
                },
            }],
        },
        "output": {
            "width": largura_px,
            "height": altura_px,
            "responses": [{"identifier": "default", "format": {"type": "image/png"}}],
        },
        "evalscript": EVALSCRIPT_NDVI,
    }

    req = urllib.request.Request(
        PROCESS_URL,
        data=json.dumps(corpo).encode(),
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "image/png",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.read(), None
    except urllib.error.HTTPError as exc:
        detalhe = _resumir_erro_process_api(exc.read().decode(errors="replace"))
        return None, f"Copernicus recusou o pedido (HTTP {exc.code}): {detalhe}"
    except Exception as exc:
        return None, f"Falha ao buscar imagem NDVI: {exc}"
