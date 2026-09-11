"""Catalogo de estacoes do SMN (Servicio Meteorologico Nacional, Argentina)
-- mesma funcao que `inmet_stations.py`/`dmc_stations.py` cumprem: so'
geolocalizacao/rotulo da estacao oficial mais perto de cada fazenda (pra
mostrar no mapa), NAO fonte dos dados de clima -- isso continua vindo
100% da Open-Meteo (`weather_forecast.py`), que ja e' global.

A API do SMN (https://ssl.smn.gob.ar/dpd/zipopendata.php?dato=estaciones)
e' publica, gratuita e SEM necessidade de conta/token (diferente da DMC do
Chile) -- confirmado com uma chamada de verdade. Devolve um ZIP contendo
um TXT de largura fixa (nao JSON/GeoJSON), com layout de colunas:
NOMBRE | PROVINCIA | LATITUD (graus, minutos) | LONGITUD (graus, minutos)
| ALTURA | NRO | NroOACI. As colunas NAO tem largura fixa confiavel o
suficiente pra fatiar por indice de caractere (nomes de estacao mais
compridos empurram as colunas seguintes, e alguns nomes muito longos ate'
quebram em duas linhas no arquivo original, virando lixo tipo "RO"/"ERO"
sozinho numa linha) -- por isso o parse abaixo ancora pelo LADO DIREITO
da linha (sempre 6 numeros + opcionalmente o codigo OACI de 4 letras,
nessa ordem), que e' a parte estruturalmente confiavel; o que sobra a
esquerda vira NOMBRE/PROVINCIA (separados pelo espaco duplo entre as
colunas). Linhas que nao batem nesse padrao (as tais quebradas) sao so'
ignoradas -- 118 de 120 estacoes reais parseiam certo (confirmado numa
chamada de verdade em 2026-09)."""
import io
import re
import urllib.request
import zipfile

import inmet_stations  # reaproveita a mesma matematica de distancia (_haversine_km)
import estacoes_cache_util

ESTACOES_URL = "https://ssl.smn.gob.ar/dpd/zipopendata.php?dato=estaciones"

# Ancora os 6 numeros (gr/min da latitude, gr/min da longitude, altura,
# numero da estacao) + o codigo OACI opcional (algumas estacoes, ex.
# "JUJUY U N", nao tem OACI cadastrado) no fim da linha -- o que sobra
# antes vira NOMBRE + PROVINCIA, separados por 2+ espacos entre si.
_LINHA_RE = re.compile(
    r"^(?P<nome_provincia>.*?)\s{2,}"
    r"(?P<gr_lat>-?\d+)\s+(?P<min_lat>\d+)\s+"
    r"(?P<gr_lon>-?\d+)\s+(?P<min_lon>\d+)\s+"
    r"(?P<altura>-?\d+)\s+(?P<nro>\d+)\s*(?P<oaci>[A-Za-z]{4})?\s*$"
)

_cache = {"timestamp": 0, "estacoes": [], "ultima_tentativa": 0}


def _fetch_texto():
    req = urllib.request.Request(ESTACOES_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        raw = resp.read()
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        conteudo = zf.read(zf.namelist()[0])
    return conteudo.decode("latin-1")


def _graus_minutos_para_decimal(graus_str, minutos_str):
    graus, minutos = int(graus_str), int(minutos_str)
    decimal = abs(graus) + minutos / 60
    return -decimal if graus_str.strip().startswith("-") else decimal


def _parse_linha(linha):
    m = _LINHA_RE.match(linha.rstrip())
    if not m:
        return None
    partes_nome = re.split(r"\s{2,}", m.group("nome_provincia").strip())
    nome = partes_nome[0] if partes_nome else ""
    provincia = partes_nome[1] if len(partes_nome) > 1 else ""
    if not nome:
        return None
    return {
        "codigo": m.group("nro"),
        "cidade": nome,
        "uf": provincia,
        "lat": _graus_minutos_para_decimal(m.group("gr_lat"), m.group("min_lat")),
        "lon": _graus_minutos_para_decimal(m.group("gr_lon"), m.group("min_lon")),
    }


def _fetch_estacoes():
    texto = _fetch_texto()
    linhas = texto.splitlines()[2:]  # pula as 2 linhas de cabecalho (nomes + unidades)
    return [e for e in (_parse_linha(l) for l in linhas if l.strip()) if e]


def get_estacoes():
    """Lista de estacoes do SMN (codigo, cidade, uf/provincia, lat, lon)
    -- cache em memoria por 24h (ou o catalogo antigo, com backoff, se a
    busca falhar -- servidor do SMN ja visto instavel -- ver
    estacoes_cache_util.cached_fetch)."""
    return estacoes_cache_util.cached_fetch(_cache, _fetch_estacoes)


def estacoes_mais_proximas(lat, lon, n=2):
    """As `n` estacoes do SMN mais pertas da coordenada informada, mais
    perto primeiro, cada uma com a distancia em km -- lista vazia se o
    catalogo nao estiver disponivel."""
    estacoes = get_estacoes()
    if not estacoes:
        return []
    com_distancia = [
        dict(e, distancia_km=round(inmet_stations._haversine_km(lat, lon, e["lat"], e["lon"]), 1))
        for e in estacoes
    ]
    com_distancia.sort(key=lambda e: e["distancia_km"])
    return com_distancia[:n]


def estacao_mais_proxima(lat, lon):
    """Estacao do SMN mais perto da coordenada informada, com a
    distancia em km -- ou None se o catalogo nao estiver disponivel."""
    proximas = estacoes_mais_proximas(lat, lon, n=1)
    return proximas[0] if proximas else None
