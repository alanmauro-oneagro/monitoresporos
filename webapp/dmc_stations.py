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

Mapeamento de campos abaixo CONFIRMADO contra uma chamada de verdade
(705 estacoes, testado em 2026-09) ao servico getCatastroEstacionesGeo
(https://climatologia.meteochile.gob.cl/application/documentacion/getDocumento/6,
"Catastro Estaciones Meteorologicas de la DMC"): devolve um GeoJSON
FeatureCollection, cada "feature" com geometry.coordinates=[longitude,
latitude] e um "properties". O aninhamento estranho do exemplo da
propria documentacao (`feature["features"]["geometry"/"properties"]`
em vez do GeoJSON padrao `feature["geometry"/"properties"]`) e' real,
nao erro de digitacao do exemplo -- `_propriedades_de` abaixo aceita os
dois formatos, testando o padrao primeiro. Duas diferencas encontradas
entre a documentacao (que usa camelCase minusculo) e a resposta real:
- As chaves "codigoNacional" e "numeroRegion" vem capitalizadas
  ("CodigoNacional", "NumeroRegion") na resposta de verdade -- so essas
  duas, as outras (nombreEstacion, latitud, longitud, comuna, provincia)
  batem com a documentacao.
- O campo "region" (nome da regiao) vem SEMPRE vazio ("") nas 705
  estacoes testadas -- inutilizavel. Uso "NumeroRegion" (sempre
  presente, 1-16) com uma tabela fixa (`_NOME_REGIAO`) em vez disso,
  ja que o numero-pro-nome das 16 regioes oficiais do Chile e' estavel
  (a doc do proprio servico usa exatamente esse par -- regiao 13 =
  "Metropolitana de Santiago" -- no unico exemplo que ela mostra)."""
import json
import os
import urllib.parse
import urllib.request

import inmet_stations  # reaproveita a mesma matemática de distância (_haversine_km)
import estacoes_cache_util

ESTACOES_URL = "https://climatologia.meteochile.gob.cl/application/geoservicios/getCatastroEstacionesGeo"

# As 16 regioes oficiais do Chile, por numero -- ver nota no docstring
# do modulo sobre o campo "region" da API vir sempre vazio.
_NOME_REGIAO = {
    1: "Tarapacá", 2: "Antofagasta", 3: "Atacama", 4: "Coquimbo",
    5: "Valparaíso", 6: "Libertador General Bernardo O'Higgins", 7: "Maule",
    8: "Biobío", 9: "La Araucanía", 10: "Los Lagos",
    11: "Aysén del General Carlos Ibáñez del Campo",
    12: "Magallanes y de la Antártica Chilena", 13: "Metropolitana de Santiago",
    14: "Los Ríos", 15: "Arica y Parinacota", 16: "Ñuble",
}

_cache = {"timestamp": 0, "estacoes": [], "ultima_tentativa": 0}


def credenciais_configuradas():
    return bool(os.environ.get("METEOCHILE_USUARIO") and os.environ.get("METEOCHILE_TOKEN"))


def _fetch_estacoes_raw():
    params = urllib.parse.urlencode({
        "usuario": os.environ["METEOCHILE_USUARIO"],
        "token": os.environ["METEOCHILE_TOKEN"],
    })
    req = urllib.request.Request(f"{ESTACOES_URL}?{params}", headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.load(resp)


def _propriedades_de(feature):
    """Ver nota no docstring do modulo sobre o aninhamento estranho do
    exemplo oficial -- tenta o GeoJSON padrao primeiro, cai pro aninhado
    se as chaves esperadas nao estiverem la'."""
    props = feature.get("properties") or {}
    if "CodigoNacional" in props:
        return props
    return (feature.get("features") or {}).get("properties") or {}


def _fetch_estacoes():
    raw = _fetch_estacoes_raw()
    estacoes = []
    for feature in raw.get("features", []):
        props = _propriedades_de(feature)
        try:
            lat = float(props["latitud"])
            lon = float(props["longitud"])
        except (TypeError, ValueError, KeyError):
            continue
        codigo = props.get("CodigoNacional")
        if codigo is None:
            continue
        # str() -- "CodigoNacional" e' puramente numerico (ex. 410002), ao
        # contrario do codigo do INMET (alfanumerico, ex. "A950"), entao a
        # resposta JSON da DMC pode vir como numero sem aspas (int em vez
        # de str). Se guardasse assim, comparacoes depois (aba Fazendas
        # decidindo qual radio marcar, `estacao_selecionada == estacao.codigo`)
        # comparariam a STRING vinda do banco (SQLite TEXT, sempre str)
        # contra um INT vindo daqui de novo a cada request -- nunca bate,
        # o radio da estacao escolhida nunca aparece marcado mesmo com a
        # escolha salva certinho (bug real visto em producao com fazenda
        # do Chile).
        estacoes.append({
            "codigo": str(codigo),
            "cidade": props.get("nombreEstacion"),
            "uf": _NOME_REGIAO.get(props.get("NumeroRegion"), ""),
            "lat": lat,
            "lon": lon,
        })
    return estacoes


def get_estacoes():
    """Lista de estacoes da DMC (codigo, cidade, uf, lat, lon -- mesmo
    formato do INMET; "cidade" guarda o nome da propria estacao
    (nombreEstacion, ex. "Quinta Normal, Santiago") e "uf" guarda o nome
    da regiao chilena, reaproveitando as chaves do INMET pra nao precisar
    mudar os templates que ja leem `estacao.cidade`/`estacao.uf`). Cache
    em memoria por 24h (ou o catalogo antigo, com backoff, se a busca
    falhar -- ver estacoes_cache_util.cached_fetch). Sem credencial
    configurada, devolve [] direto, sem tentar rede."""
    if not credenciais_configuradas():
        return []
    return estacoes_cache_util.cached_fetch(_cache, _fetch_estacoes)


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
