"""Catalogo de estacoes do SENAMHI (Servicio Nacional de Meteorologia e
Hidrologia del Peru) -- mesma funcao que `inmet_stations.py`/
`dmc_stations.py`/`smn_stations.py` cumprem: so' geolocalizacao/rotulo da
estacao oficial mais perto de cada fazenda (pra mostrar no mapa), NAO
fonte dos dados de clima -- isso continua vindo 100% da Open-Meteo
(`weather_forecast.py`), que ja e' global.

O SENAMHI NAO tem uma API REST/JSON documentada (diferente do INMET) nem
exige conta (diferente da DMC/Chile) -- o catalogo completo (estacoes
meteorologicas E hidrologicas juntas) vem embutido como um array
Javascript dentro do HTML da propria pagina publica do mapa de estacoes
(`https://www.senamhi.gob.pe/mapas/mapa-estaciones-2/`, confirmado com
uma chamada de verdade em 2026-09, sem autenticacao). Filtra so' as
meteorologicas (`"ico": "M"`, 731 de 976 registros -- o resto, "H", e'
hidrologica, fora do escopo aqui).

Cuidado encontrado na resposta real: alguns numeros vem escritos sem o
zero antes do ponto decimal (`"lat": -.1172` em vez de `-0.1172`) --
JSON valido nao aceita isso (`json.loads` rejeita), mas e' valido em
Javascript puro (de onde a pagina tira o array). `_ARRAY_RE`/o regex de
correcao abaixo insere o zero que falta antes de tentar `json.loads`.

NAO ha' campo de departamento/regiao nessa fonte (so' nome, codigo,
tipo, coordenadas) -- "uf" fica vazio pra essas estacoes (mesma
degradacao graciosa ja aceita pra estacoes argentinas sem provincia
identificada, ver smn_stations.py); nao vale a pena um enriquecimento
geografico (point-in-polygon contra limites departamentais) so' pra um
rotulo cosmetico no tooltip do mapa."""
import json
import re
import urllib.request

import inmet_stations  # reaproveita a mesma matematica de distancia (_haversine_km)
import estacoes_cache_util

PAGINA_URL = "https://www.senamhi.gob.pe/mapas/mapa-estaciones-2/"

_ARRAY_RE = re.compile(r"var\s+PruebaTest\s*=\s*(\[.*?\]);", re.S)
# Insere o "0" que falta antes de ".NNN" (ex. "-.1172" -> "-0.1172",
# "[.5" -> "[0.5") -- ver nota no docstring do modulo.
_NUMERO_SEM_ZERO_RE = re.compile(r"([:\[,]\s*-?)\.(\d)")

_cache = {"timestamp": 0, "estacoes": [], "ultima_tentativa": 0}


def _fetch_estacoes_raw():
    req = urllib.request.Request(PAGINA_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        html = resp.read().decode("utf-8", errors="replace")
    m = _ARRAY_RE.search(html)
    if not m:
        raise RuntimeError("array PruebaTest nao encontrado na pagina do SENAMHI")
    texto_corrigido = _NUMERO_SEM_ZERO_RE.sub(r"\g<1>0.\2", m.group(1))
    return json.loads(texto_corrigido)


def _fetch_estacoes():
    raw = _fetch_estacoes_raw()
    estacoes = []
    codigos_vistos = set()
    for item in raw:
        if item.get("ico") != "M":
            continue
        codigo = item.get("cod")
        lat, lon = item.get("lat"), item.get("lon")
        if not codigo or not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
            continue
        if codigo in codigos_vistos:
            continue
        codigos_vistos.add(codigo)
        estacoes.append({"codigo": codigo, "cidade": item.get("nom") or codigo, "uf": "", "lat": lat, "lon": lon})
    return estacoes


def get_estacoes():
    """Lista de estacoes METEOROLOGICAS do SENAMHI (codigo, cidade, uf,
    lat, lon -- "uf" sempre vazio, ver docstring do modulo) -- cache em
    memoria por 24h (ou o catalogo antigo, com backoff, se a busca falhar
    -- ver estacoes_cache_util.cached_fetch)."""
    return estacoes_cache_util.cached_fetch(_cache, _fetch_estacoes)


def estacoes_mais_proximas(lat, lon, n=2):
    """As `n` estacoes do SENAMHI mais pertas da coordenada informada,
    mais perto primeiro, cada uma com a distancia em km -- lista vazia
    se o catalogo nao estiver disponivel."""
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
    """Estacao do SENAMHI mais perto da coordenada informada, com a
    distancia em km -- ou None se o catalogo nao estiver disponivel."""
    proximas = estacoes_mais_proximas(lat, lon, n=1)
    return proximas[0] if proximas else None
