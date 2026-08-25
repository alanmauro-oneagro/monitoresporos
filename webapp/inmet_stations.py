"""Catalogo de estacoes automaticas do INMET (Instituto Nacional de
Meteorologia) -- usado so pra achar a estacao oficial mais perto de cada
fazenda (nome da cidade e a coordenada real da estacao, pra mostrar no
mapa). NAO busca o valor lido por nenhuma estacao -- a API publica de dados
por estacao do INMET parou de funcionar (o site atual busca os valores por
uma URL com hash gerado por sessao, sem rota fixa que de pra chamar direto);
os valores de clima (atual e previsao) continuam vindo 100% da Open-Meteo
(weather_forecast.py). Aqui e' so geolocalizacao/rotulo."""
import json
import math
import time
import urllib.request

ESTACOES_URL = "https://apitempo.inmet.gov.br/estacoes/T"
CACHE_TTL_SECONDS = 24 * 60 * 60  # catalogo de estacoes quase nunca muda
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

_cache = {"timestamp": 0, "estacoes": []}


def _fetch_estacoes():
    req = urllib.request.Request(
        ESTACOES_URL, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.load(resp)


def get_estacoes():
    """Lista de estacoes automaticas do INMET em operacao (codigo, cidade,
    uf, lat, lon) -- cache em memoria por 24h; mantem o cache antigo (ou
    lista vazia) se a busca falhar."""
    now = time.time()
    if _cache["estacoes"] and now - _cache["timestamp"] < CACHE_TTL_SECONDS:
        return _cache["estacoes"]
    try:
        raw = _fetch_estacoes()
    except Exception:
        return _cache["estacoes"]
    estacoes = []
    for s in raw:
        if s.get("CD_SITUACAO") != "Operante":
            continue
        try:
            lat = float(s["VL_LATITUDE"])
            lon = float(s["VL_LONGITUDE"])
        except (TypeError, ValueError, KeyError):
            continue
        estacoes.append({
            "codigo": s.get("CD_ESTACAO"),
            "cidade": s.get("DC_NOME"),
            "uf": s.get("SG_ESTADO"),
            "lat": lat,
            "lon": lon,
        })
    _cache["estacoes"] = estacoes
    _cache["timestamp"] = now
    return estacoes


def _haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def estacoes_mais_proximas(lat, lon, n=2):
    """As `n` estacoes automaticas do INMET mais pertas da coordenada
    informada, mais perto primeiro, cada uma com a distancia em km --
    lista vazia se o catalogo nao estiver disponivel."""
    estacoes = get_estacoes()
    if not estacoes:
        return []
    com_distancia = [
        dict(e, distancia_km=round(_haversine_km(lat, lon, e["lat"], e["lon"]), 1)) for e in estacoes
    ]
    com_distancia.sort(key=lambda e: e["distancia_km"])
    return com_distancia[:n]


def estacao_mais_proxima(lat, lon):
    """Estacao automatica do INMET mais perto da coordenada informada, com
    a distancia em km -- ou None se o catalogo nao estiver disponivel."""
    proximas = estacoes_mais_proximas(lat, lon, n=1)
    return proximas[0] if proximas else None
