"""Catalogo de estacoes do SENAMHI (Servicio Nacional de Meteorologia e
Hidrologia) da BOLIVIA -- mesma funcao que
`inmet_stations.py`/`dmc_stations.py`/`smn_stations.py`/
`senamhi_stations.py`/`inumet_stations.py`/`dmh_paraguay_stations.py`
cumprem: so' geolocalizacao/rotulo da estacao oficial mais perto de cada
fazenda (pra mostrar no mapa), NAO fonte dos dados de clima -- isso
continua vindo 100% da Open-Meteo (`weather_forecast.py`), que ja e'
global.

ATENCAO NO NOME: o Peru TAMBEM tem um orgao chamado SENAMHI (mesma sigla,
pais diferente) -- esse aqui e' `senamhi_bolivia_stations.py`, NAO
confundir com `senamhi_stations.py` (Peru).

O site institucional do SENAMHI Bolivia (senamhi.gob.bo) e o antigo
GeoServer dedicado do orgao (geo.gob.bo / geobolivia.gob.bo, citados em
pesquisas antigas) NAO tem mais DNS valido (confirmado via resolvedor
publico, nao e' bloqueio local) -- o catalogo de estacoes foi encontrado
via um caminho indireto: o GeoNode do Ministerio de Planificacion del
Desarrollo (`https://geobolivia.planificacion.gob.bo/geovisor/`, o
"geovisor" nacional da Bolivia) referencia
`geonode.planificacion.gob.bo`, cujo catalogo publico (`/api/v2/resources/`)
tem o dataset "Estaciones Meteorologicas del Estado Plurinacional de
Bolivia" (id 484, `geonode:estaciones_meteorologicas_4bbbdeee`) -- um
WFS do GeoServer por tras do GeoNode, publico, SEM autenticacao,
confirmado com uma chamada de verdade em 2026-09 (319 estacoes reais,
codigos/nomes/coordenadas todos validos). Dataset datado de 2016 -- mais
antigo que os outros provedores da regiao, mas e' o unico catalogo
nacional publico encontrado; nao ha' indicio de uma fonte mais recente
com API aberta."""
import json
import time
import urllib.request

import inmet_stations  # reaproveita a mesma matematica de distancia (_haversine_km)

ESTACOES_URL = (
    "https://geonode.planificacion.gob.bo/geoserver/ows"
    "?service=WFS&version=1.0.0&request=GetFeature"
    "&typename=geonode%3Aestaciones_meteorologicas_4bbbdeee"
    "&outputFormat=json&srs=EPSG%3A4326&srsName=EPSG%3A4326"
)
CACHE_TTL_SECONDS = 24 * 60 * 60  # catalogo de estacoes quase nunca muda

_cache = {"timestamp": 0, "estacoes": []}


def _fetch_estacoes_raw():
    req = urllib.request.Request(ESTACOES_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.load(resp)
    return data.get("features", [])


def get_estacoes():
    """Lista de estacoes do SENAMHI Bolivia (codigo, cidade, uf/
    departamento, lat, lon) -- cache em memoria por 24h; mantem o cache
    antigo se a busca falhar."""
    now = time.time()
    if _cache["estacoes"] and now - _cache["timestamp"] < CACHE_TTL_SECONDS:
        return _cache["estacoes"]
    try:
        raw = _fetch_estacoes_raw()
    except Exception:
        return _cache["estacoes"]
    estacoes = []
    codigos_vistos = set()
    for feature in raw:
        props = feature.get("properties") or {}
        codigo = props.get("nro")
        lat, lon = props.get("lat"), props.get("lon")
        if codigo is None or not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
            continue
        codigo = str(codigo)
        if codigo in codigos_vistos:
            continue
        codigos_vistos.add(codigo)
        estacoes.append({
            "codigo": codigo,
            "cidade": props.get("esta") or codigo,
            "uf": props.get("dep") or "",
            "lat": lat,
            "lon": lon,
        })
    if not estacoes:
        return _cache["estacoes"]
    _cache["estacoes"] = estacoes
    _cache["timestamp"] = now
    return estacoes


def estacoes_mais_proximas(lat, lon, n=2):
    """As `n` estacoes do SENAMHI Bolivia mais pertas da coordenada
    informada, mais perto primeiro, cada uma com a distancia em km --
    lista vazia se o catalogo nao estiver disponivel."""
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
    """Estacao do SENAMHI Bolivia mais perto da coordenada informada,
    com a distancia em km -- ou None se o catalogo nao estiver
    disponivel."""
    proximas = estacoes_mais_proximas(lat, lon, n=1)
    return proximas[0] if proximas else None
