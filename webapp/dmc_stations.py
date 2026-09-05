"""Catalogo de estacoes automaticas (rede EMA) da Direccion Meteorologica
de Chile (DMC) -- mesma funcao que `inmet_stations.py` cumpre pro Brasil:
so' geolocalizacao/rotulo da estacao oficial mais perto de cada fazenda
(pra mostrar no mapa), NAO fonte dos dados de clima -- isso continua
vindo 100% da Open-Meteo (`weather_forecast.py`), que ja e' global.

A API da DMC (https://climatologia.meteochile.gob.cl) e' gratuita mas
exige uma conta registrada (usuario + token) -- sem as variaveis de
ambiente METEOCHILE_USUARIO/METEOCHILE_TOKEN, `get_estacoes()` devolve
lista vazia sem nem tentar a rede (mesmo padrao do Copernicus em
ndvi_service.py: a funcionalidade so' fica indisponivel, o resto do app
continua funcionando normal).

IMPORTANTE: o mapeamento de campos abaixo (nomes das chaves no JSON de
resposta) ainda nao foi confirmado contra uma chamada real -- foi escrito
com base na documentacao publica da API, nao testado. Confirmar assim
que houver credencial de verdade e ajustar `_fetch_estacoes`/`get_estacoes`
conforme o formato real."""
import json
import os
import time
import urllib.parse
import urllib.request

import inmet_stations  # reaproveita a mesma matemática de distância (_haversine_km)

ESTACOES_URL = "https://climatologia.meteochile.gob.cl/application/productos/estacionesRedEma"
CACHE_TTL_SECONDS = 24 * 60 * 60  # catalogo de estacoes quase nunca muda

_cache = {"timestamp": 0, "estacoes": []}


def credenciais_configuradas():
    return bool(os.environ.get("METEOCHILE_USUARIO") and os.environ.get("METEOCHILE_TOKEN"))


def _fetch_estacoes():
    params = urllib.parse.urlencode({
        "usuario": os.environ["METEOCHILE_USUARIO"],
        "token": os.environ["METEOCHILE_TOKEN"],
    })
    req = urllib.request.Request(f"{ESTACOES_URL}?{params}", headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.load(resp)


def get_estacoes():
    """Lista de estacoes automaticas da DMC (codigo, cidade, uf, lat, lon
    -- mesmo formato do INMET; "uf" guarda o nome da regiao chilena,
    reaproveitando a chave pra nao precisar mudar os templates que ja
    leem `estacao.uf`). Cache em memoria por 24h. Sem credencial
    configurada, devolve [] direto, sem tentar rede."""
    if not credenciais_configuradas():
        return []
    now = time.time()
    if _cache["estacoes"] and now - _cache["timestamp"] < CACHE_TTL_SECONDS:
        return _cache["estacoes"]
    try:
        raw = _fetch_estacoes()
    except Exception:
        return _cache["estacoes"]
    estacoes = []
    for item in raw.get("datos", raw) if isinstance(raw, dict) else raw:
        try:
            lat = float(item.get("latitud"))
            lon = float(item.get("longitud"))
        except (TypeError, ValueError):
            continue
        estacoes.append({
            "codigo": item.get("codigoNacional") or item.get("codigo"),
            "cidade": item.get("nombre"),
            "uf": item.get("nombreRegion") or item.get("region"),
            "lat": lat,
            "lon": lon,
        })
    _cache["estacoes"] = estacoes
    _cache["timestamp"] = now
    return estacoes


def estacoes_mais_proximas(lat, lon, n=2):
    """As `n` estacoes da DMC mais pertas da coordenada informada --
    lista vazia se o catalogo nao estiver disponivel (sem credencial, ou
    rede fora do ar)."""
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
    proximas = estacoes_mais_proximas(lat, lon, n=1)
    return proximas[0] if proximas else None
