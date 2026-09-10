"""Catalogo de estacoes da DMH (Direccion de Meteorologia e Hidrologia,
subordinada a DINAC -- Paraguai) -- mesma funcao que
`inmet_stations.py`/`dmc_stations.py`/`smn_stations.py`/
`senamhi_stations.py`/`inumet_stations.py` cumprem: so' geolocalizacao/
rotulo da estacao oficial mais perto de cada fazenda (pra mostrar no
mapa), NAO fonte dos dados de clima -- isso continua vindo 100% da
Open-Meteo (`weather_forecast.py`), que ja e' global.

Fonte: `https://www.meteorologia.gov.py/emas/data.json` (dominio proprio
da DMH, distinto do site institucional da DINAC) -- JSON publico,
SEM autenticacao, referenciado pela propria pagina publica de mapa de
estacoes do orgao. Formato (confirmado via snapshot arquivado do Wayback
Machine, 2026-06-07 -- ver ressalva abaixo):
{
  "estaciones": {
    "<codigo>": {
      "metadatos": {"codigo", "codigo_omm", "nombre", "ciudad",
                     "departamento", "latitud", "longitud", ...},
      "ultima_observacion": {...}  # ignorado aqui de proposito -- so'
                                    # cuidamos do catalogo, nao do dado
                                    # de leitura (isso e' Open-Meteo)
    },
    ...
  }
}
E' o UNICO dos provedores da regiao cuja fonte ja' traz "departamento"
nativamente (Argentina/Peru/Uruguai nao tem esse campo na fonte deles).

RESSALVA IMPORTANTE: o dominio `meteorologia.gov.py` estava FORA DO AR
(timeout de conexao, nao bloqueio de rede -- confirmado testando de duas
redes diferentes) no momento em que este modulo foi escrito -- a
estrutura acima foi confirmada contra um snapshot arquivado, nao uma
chamada ao vivo. O modulo segue o mesmo padrao de degradacao graciosa de
todo provedor aqui (`get_estacoes()` devolve [] / mantem cache antigo se
a busca falhar) -- se o servidor continuar fora do ar, o Paraguai
simplesmente continua funcionando como ate agora (fronteira + Open-Meteo,
sem pino de estacao oficial), sem quebrar nada. Assim que o servico
voltar, o catalogo passa a aparecer sozinho, sem precisar mudar nada
aqui -- mas vale reconferir o formato real numa proxima chamada bem-
sucedida, por garantia."""
import json
import time
import urllib.request

import inmet_stations  # reaproveita a mesma matematica de distancia (_haversine_km)

ESTACOES_URL = "https://www.meteorologia.gov.py/emas/data.json"
CACHE_TTL_SECONDS = 24 * 60 * 60  # catalogo de estacoes quase nunca muda

_cache = {"timestamp": 0, "estacoes": []}


def _fetch_estacoes_raw():
    req = urllib.request.Request(ESTACOES_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.load(resp)
    return data.get("estaciones", {})


def get_estacoes():
    """Lista de estacoes da DMH (codigo, cidade, uf/departamento, lat,
    lon) -- cache em memoria por 24h; mantem o cache antigo se a busca
    falhar (servidor fora do ar, ver ressalva no docstring do modulo)."""
    now = time.time()
    if _cache["estacoes"] and now - _cache["timestamp"] < CACHE_TTL_SECONDS:
        return _cache["estacoes"]
    try:
        raw = _fetch_estacoes_raw()
    except Exception:
        return _cache["estacoes"]
    estacoes = []
    codigos_vistos = set()
    for item in raw.values():
        meta = item.get("metadatos") or {}
        codigo = meta.get("codigo")
        lat, lon = meta.get("latitud"), meta.get("longitud")
        if not codigo or not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
            continue
        if codigo in codigos_vistos:
            continue
        codigos_vistos.add(codigo)
        estacoes.append({
            "codigo": codigo,
            "cidade": meta.get("ciudad") or meta.get("nombre") or codigo,
            "uf": meta.get("departamento") or "",
            "lat": lat,
            "lon": lon,
        })
    if not estacoes:
        return _cache["estacoes"]
    _cache["estacoes"] = estacoes
    _cache["timestamp"] = now
    return estacoes


def estacoes_mais_proximas(lat, lon, n=2):
    """As `n` estacoes da DMH mais pertas da coordenada informada, mais
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
    """Estacao da DMH mais perto da coordenada informada, com a
    distancia em km -- ou None se o catalogo nao estiver disponivel."""
    proximas = estacoes_mais_proximas(lat, lon, n=1)
    return proximas[0] if proximas else None
