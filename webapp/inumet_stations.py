"""Catalogo de estacoes do INUMET (Instituto Uruguayo de Meteorologia) --
mesma funcao que `inmet_stations.py` (Brasil!) / `dmc_stations.py` /
`smn_stations.py` / `senamhi_stations.py` cumprem: so' geolocalizacao/
rotulo da estacao oficial mais perto de cada fazenda (pra mostrar no
mapa), NAO fonte dos dados de clima -- isso continua vindo 100% da
Open-Meteo (`weather_forecast.py`), que ja e' global.

ATENCAO NO NOME: este modulo e' do URUGUAI (INUMET, com "U"), NAO
confundir com `inmet_stations.py` (BRASIL, INMET, sem "U") -- acronimos
quase identicos, arquivos DIFERENTES.

O INUMET NAO publica uma API REST/JSON documentada de catalogo (o
portal de dados abertos oficial, catalogodatos.gub.uy, so' tem datasets
de OBSERVACAO por variavel, sem coordenada/codigo de estacao -- inutil
pra esse proposito). O que existe e' um endpoint interno usado pelo
proprio site do INUMET pra' popular a pagina "Datos 24h"
(https://www.inumet.gub.uy/reportes/estadoActual/datos_inumet_ui_publica.mch,
extensao ".mch" mas conteudo e' JSON puro), confirmado com uma chamada
de verdade em 2026-09, SEM autenticacao. Nao e' uma API anunciada/
documentada -- pode mudar de formato sem aviso (tratado com o mesmo
cuidado de qualquer fonte fragil: timeout curto, fallback silencioso
pro cache antigo, nunca derruba o resto do app).

A resposta traz uma REDE REGIONAL combinada (nao so' Uruguai) no mesmo
array `estaciones` -- cada registro tem "gerencia" identificando de
quem e' (visto na pratica: "INUMET", "SMN" [Argentina], "BR" [Brasil],
"INIA" [Uruguai, outro orgao]) -- filtra so' `gerencia == "INUMET"`
(53 estacoes reais do Uruguai) pra nao duplicar cobertura que ja vem
de `smn_stations.py`/`inmet_stations.py`.

NAO ha' campo de departamento nessa fonte -- "uf" fica vazio (mesma
degradacao graciosa ja aceita em `smn_stations.py`/`senamhi_stations.py`
quando a fonte nao tem esse dado)."""
import json
import urllib.request

import inmet_stations  # reaproveita a mesma matematica de distancia (_haversine_km) -- modulo do BRASIL, so' a funcao utilitaria
import estacoes_cache_util

DADOS_URL = "https://www.inumet.gub.uy/reportes/estadoActual/datos_inumet_ui_publica.mch"

_cache = {"timestamp": 0, "estacoes": [], "ultima_tentativa": 0}


def _fetch_estacoes_raw():
    req = urllib.request.Request(DADOS_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.load(resp)
    return data.get("estaciones", [])


def _fetch_estacoes():
    raw = _fetch_estacoes_raw()
    estacoes = []
    codigos_vistos = set()
    for item in raw:
        if item.get("gerencia") != "INUMET":
            continue
        codigo = item.get("idStr")
        lat, lon = item.get("latitud"), item.get("longitud")
        if not codigo or not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
            continue
        if codigo in codigos_vistos:
            continue
        codigos_vistos.add(codigo)
        estacoes.append({
            "codigo": codigo,
            "cidade": item.get("displayName") or item.get("nombre") or codigo,
            "uf": "",
            "lat": lat,
            "lon": lon,
        })
    return estacoes


def get_estacoes():
    """Lista de estacoes do INUMET (codigo, cidade, uf, lat, lon -- "uf"
    sempre vazio, ver docstring do modulo) -- cache em memoria por 24h
    (ou o catalogo antigo, com backoff, se a busca falhar -- ver
    estacoes_cache_util.cached_fetch)."""
    return estacoes_cache_util.cached_fetch(_cache, _fetch_estacoes)


def estacoes_mais_proximas(lat, lon, n=2):
    """As `n` estacoes do INUMET mais pertas da coordenada informada,
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
    """Estacao do INUMET mais perto da coordenada informada, com a
    distancia em km -- ou None se o catalogo nao estiver disponivel."""
    proximas = estacoes_mais_proximas(lat, lon, n=1)
    return proximas[0] if proximas else None
