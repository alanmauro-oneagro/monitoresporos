"""Catalogo de estacoes do INMET (Instituto Nacional de Meteorologia) --
usado so pra achar a estacao oficial mais perto de cada fazenda (nome da
cidade e a coordenada real da estacao, pra mostrar no mapa). NAO busca o
valor lido por nenhuma estacao -- a API publica de dados por estacao do
INMET parou de funcionar (o site atual busca os valores por uma URL com
hash gerado por sessao, sem rota fixa que de pra chamar direto); os valores
de clima (atual e previsao) continuam vindo 100% da Open-Meteo
(weather_forecast.py). Aqui e' so geolocalizacao/rotulo.

Junta as DUAS redes do INMET, mesma API/formato de resposta, so' o tipo na
URL muda: "T" = automaticas (mais novas, leitura por sensor) e "M" =
convencionais/manuais (mais antigas, leitura humana) -- muitas areas do
interior/Norte tem so' estacao convencional por perto, entao usar so' "T"
deixava essas fazendas sem estacao oficial de referencia dentro da distancia
razoavel. Isso NAO muda de onde vem o dado de clima (continua 100%
Open-Meteo) -- so' aumenta a densidade do catalogo usado pro rotulo/mapa."""
import math
import json
import urllib.request

import estacoes_cache_util

ESTACOES_URL = "https://apitempo.inmet.gov.br/estacoes/{tipo}"
TIPOS_REDE = ("T", "M")
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

_cache = {"timestamp": 0, "estacoes": [], "ultima_tentativa": 0}


def _fetch_estacoes(tipo):
    req = urllib.request.Request(
        ESTACOES_URL.format(tipo=tipo), headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.load(resp)


def _fetch_todas_redes():
    """Junta as duas redes (T/M) -- se so' uma responder, o catalogo
    parcial ja' conta como sucesso (fica em cache normal); so' lanca
    excecao (vira "sem sucesso", ver estacoes_cache_util) se NENHUMA
    rede responder."""
    estacoes = []
    codigos_vistos = set()
    alguma_rede_respondeu = False
    for tipo in TIPOS_REDE:
        try:
            raw = _fetch_estacoes(tipo)
        except Exception:
            continue
        alguma_rede_respondeu = True
        for s in raw:
            if s.get("CD_SITUACAO") != "Operante":
                continue
            codigo = s.get("CD_ESTACAO")
            if codigo in codigos_vistos:
                continue
            try:
                lat = float(s["VL_LATITUDE"])
                lon = float(s["VL_LONGITUDE"])
            except (TypeError, ValueError, KeyError):
                continue
            codigos_vistos.add(codigo)
            estacoes.append({
                "codigo": codigo,
                "cidade": s.get("DC_NOME"),
                "uf": s.get("SG_ESTADO"),
                "lat": lat,
                "lon": lon,
            })
    if not alguma_rede_respondeu:
        raise RuntimeError("nenhuma rede do INMET respondeu")
    return estacoes


def get_estacoes():
    """Lista de estacoes do INMET em operacao, automaticas + convencionais
    (codigo, cidade, uf, lat, lon) -- cache em memoria por 24h (ou o
    catalogo antigo, com backoff, se a busca falhar -- ver
    estacoes_cache_util.cached_fetch)."""
    return estacoes_cache_util.cached_fetch(_cache, _fetch_todas_redes)


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
