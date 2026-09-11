"""Registro dos paises suportados -- Brasil (padrao/legado, tudo ao vivo
via IBGE/INMET, sem mudanca nenhuma) e Chile (fronteiras estaticas via
geoBoundaries.org, estacoes via DMC -- ver `dmc_stations.py`). Um pais
novo depois disso e' so' uma entrada aqui + um modulo de estacoes, sem
precisar mexer nas rotas/templates que ja iteram sobre `COUNTRIES`.

`station_provider` e' uma referencia direta ao modulo (nao uma string),
pra `/mapa` e `/mapa-interpolado` chamarem `.get_estacoes()`/
`.estacao_mais_proxima()` genericamente sem if/elif por pais."""
import concurrent.futures
import json
from pathlib import Path

from shapely.geometry import Point, shape

import inmet_stations
import dmc_stations
import smn_stations
import senamhi_stations
import senamhi_bolivia_stations
import inumet_stations
import dmh_paraguay_stations
import sem_estacoes

DEFAULT_COUNTRY = "BR"


def _pais_sem_estacoes(nome, sigla, bbox):
    """Pais com fronteira estatica pronta mas SEM integracao de estacoes
    ainda (ver `sem_estacoes.py`) -- so' falta a API de estacoes daquele
    pais especifico pra ficar igual ao Chile. Sigla em minusculo porque
    e' assim que o script de fetch salvou os arquivos
    (webapp/static/boundaries/<sigla>_adm0.geojson etc)."""
    return {
        "nome": nome,
        "boundary_mode": "static_geoboundaries",
        "boundary_files": {
            "adm0": f"boundaries/{sigla}_adm0.geojson",
            "adm1": f"boundaries/{sigla}_adm1.geojson",
        },
        "station_provider": sem_estacoes,
        "cloud_grid_bbox": bbox,
    }


COUNTRIES = {
    "BR": {
        "nome": "Brasil",
        "boundary_mode": "live_ibge",  # busca ao vivo na API do IBGE, ver mapa.html/mapa_interpolado.html
        "station_provider": inmet_stations,
        "agencia_estacoes": "INMET",  # rotulo mostrado no tooltip da estacao, ver mapa.html/mapa_interpolado.html
        "cloud_grid_bbox": {"lat_min": -34, "lat_max": 6, "lon_min": -74, "lon_max": -34},
    },
    "CL": {
        "nome": "Chile",
        "boundary_mode": "static_geoboundaries",  # arquivos estaticos, ver webapp/static/boundaries/
        "boundary_files": {
            "adm0": "boundaries/cl_adm0.geojson",
            "adm1": "boundaries/cl_adm1.geojson",
            "adm2": "boundaries/cl_adm2.geojson",
        },
        "station_provider": dmc_stations,
        "agencia_estacoes": "DMC",
        "cloud_grid_bbox": {"lat_min": -56, "lat_max": -17, "lon_min": -76, "lon_max": -66},
    },
    "AR": {
        "nome": "Argentina",
        "boundary_mode": "static_geoboundaries",
        "boundary_files": {
            "adm0": "boundaries/ar_adm0.geojson",
            "adm1": "boundaries/ar_adm1.geojson",
        },
        "station_provider": smn_stations,  # SMN -- API publica, sem conta/token (ver smn_stations.py)
        "agencia_estacoes": "SMN",
        "cloud_grid_bbox": {"lat_min": -55, "lat_max": -21, "lon_min": -73, "lon_max": -53},
    },
    # Resto da America do Sul -- fronteira (pais + regiao/provincia) ja'
    # pronta (geoBoundaries, mesmo processo do Chile), mas SEM estacao
    # oficial integrada ainda (ver `_pais_sem_estacoes`/`sem_estacoes.py`)
    # -- cada uma precisa de uma API de agencia meteorologica nacional
    # propria, um trabalho por pais que so' acontece quando/se uma fazenda
    # de verdade aparecer la'.
    "BO": {
        "nome": "Bolivia",
        "boundary_mode": "static_geoboundaries",
        "boundary_files": {
            "adm0": "boundaries/bo_adm0.geojson",
            "adm1": "boundaries/bo_adm1.geojson",
        },
        # SENAMHI Bolivia -- ver senamhi_bolivia_stations.py (achado via
        # GeoNode do Ministerio de Planificacion, nao confundir com
        # senamhi_stations.py, que e' o SENAMHI do Peru).
        "station_provider": senamhi_bolivia_stations,
        "agencia_estacoes": "SENAMHI",
        "cloud_grid_bbox": {"lat_min": -23, "lat_max": -9, "lon_min": -69, "lon_max": -57},
    },
    "CO": _pais_sem_estacoes("Colombia", "co", {"lat_min": -4, "lat_max": 13, "lon_min": -79, "lon_max": -66}),
    "EC": _pais_sem_estacoes("Equador", "ec", {"lat_min": -5, "lat_max": 1.5, "lon_min": -81, "lon_max": -75}),
    "GY": _pais_sem_estacoes("Guiana", "gy", {"lat_min": 1, "lat_max": 9, "lon_min": -61, "lon_max": -56}),
    "PY": {
        "nome": "Paraguai",
        "boundary_mode": "static_geoboundaries",
        "boundary_files": {
            "adm0": "boundaries/py_adm0.geojson",
            "adm1": "boundaries/py_adm1.geojson",
        },
        # DMH -- ver dmh_paraguay_stations.py: confirmado ao vivo (o
        # servidor esteve fora do ar durante a integracao, mas ja voltou
        # e o formato bateu exatamente com o documentado no modulo).
        "station_provider": dmh_paraguay_stations,
        "agencia_estacoes": "DMH",
        "cloud_grid_bbox": {"lat_min": -27.5, "lat_max": -19, "lon_min": -63, "lon_max": -54},
    },
    "PE": {
        "nome": "Peru",
        "boundary_mode": "static_geoboundaries",
        "boundary_files": {
            "adm0": "boundaries/pe_adm0.geojson",
            "adm1": "boundaries/pe_adm1.geojson",
        },
        "station_provider": senamhi_stations,  # SENAMHI Peru -- ver senamhi_stations.py
        "agencia_estacoes": "SENAMHI",
        "cloud_grid_bbox": {"lat_min": -18.5, "lat_max": 0, "lon_min": -81.5, "lon_max": -68.5},
    },
    "SR": _pais_sem_estacoes("Suriname", "sr", {"lat_min": 1.8, "lat_max": 6, "lon_min": -58, "lon_max": -54}),
    "UY": {
        "nome": "Uruguai",
        "boundary_mode": "static_geoboundaries",
        "boundary_files": {
            "adm0": "boundaries/uy_adm0.geojson",
            "adm1": "boundaries/uy_adm1.geojson",
        },
        "station_provider": inumet_stations,  # INUMET -- ver inumet_stations.py (nao confundir com o INMET do Brasil)
        "agencia_estacoes": "INUMET",
        "cloud_grid_bbox": {"lat_min": -35, "lat_max": -30, "lon_min": -58.5, "lon_max": -53},
    },
    "VE": _pais_sem_estacoes("Venezuela", "ve", {"lat_min": 0.5, "lat_max": 12.5, "lon_min": -73.5, "lon_max": -59.5}),
}


def get_country(code):
    return COUNTRIES.get(code, COUNTRIES[DEFAULT_COUNTRY])


_STATIC_DIR = Path(__file__).parent / "static"
_poligono_pais_cache = {}


def _poligono_pais(code):
    """Poligono ADM0 (shapely) de um pais com fronteira estatica --
    cacheado em memoria pro processo inteiro (o arquivo praticamente
    nunca muda, e reparsear ~300KB de GeoJSON a cada fazenda nova seria
    desperdicio). None se o pais nao tiver `boundary_mode` estatico
    (Brasil usa IBGE ao vivo -- nao faz sentido testar contorno do
    Brasil aqui, ele ja' e' o palpite padrao quando nada mais bate) ou
    se o arquivo nao existir/nao carregar."""
    if code in _poligono_pais_cache:
        return _poligono_pais_cache[code]
    info = COUNTRIES.get(code, {})
    poligono = None
    if info.get("boundary_mode") == "static_geoboundaries":
        try:
            caminho = _STATIC_DIR / info["boundary_files"]["adm0"]
            with open(caminho, encoding="utf-8") as f:
                geojson = json.load(f)
            poligono = shape(geojson["features"][0]["geometry"])
        except Exception:
            poligono = None
    _poligono_pais_cache[code] = poligono
    return poligono


def detectar_pais_por_coordenada(lat, lon):
    """Pais cujo contorno estatico contem essa coordenada (Chile, e
    qualquer outro pais com `boundary_mode: static_geoboundaries` que
    entrar depois) -- usado pra marcar sozinho o pais de uma fazenda
    nova, sem precisar de ninguem escolhendo na aba Fazendas na mao
    (ver `app._auto_detectar_paises_novos`). Devolve `DEFAULT_COUNTRY`
    ('BR') se a coordenada nao cair em nenhum contorno conhecido --
    Brasil e' sempre o palpite padrao, nunca testado por poligono
    proprio aqui."""
    ponto = Point(lon, lat)
    for code in COUNTRIES:
        if code == DEFAULT_COUNTRY:
            continue
        poligono = _poligono_pais(code)
        if poligono is not None and poligono.contains(ponto):
            return code
    return DEFAULT_COUNTRY


# Distancia maxima (km) pra uma estacao de OUTRO pais (diferente do
# pais cadastrado da propria fazenda) ainda contar como "fazenda perto
# de fronteira" em `estacoes_mais_proximas_global` -- candidata do
# MESMO pais da fazenda nunca tem esse teto (uma fazenda remota, longe
# de toda estacao do proprio pais, ainda deve cair na mais proxima
# disponivel, por mais longe que esteja -- comportamento de sempre).
# Sem esse teto pras candidatas de FORA, um pais cujo provedor devolve
# lista vazia (ex.: DMC do Chile sem credencial configurada, ou
# qualquer pais em `sem_estacoes.py`) deixava a "mais proxima entre as
# poucas que sobraram" vencer por padrao, nao importa a distancia real
# -- bug real ja visto em producao (fazenda no Chile herdou uma
# estacao INMET a mais de 1500km, no Rio Grande do Sul). Reduzido de
# 150 pra 50km (pedido explicito do usuario) -- com mais paises vizinhos
# ja tendo estacao propria integrada (Argentina, e os que entrarem a
# seguir), faz mais sentido so' cruzar fronteira pra uma estacao BEM
# perto, e nao um raio tao largo.
DISTANCIA_MAXIMA_ESTACAO_VIZINHA_KM = 50


def estacoes_mais_proximas_global(lat, lon, site_country, n=2):
    """As `n` estacoes oficiais mais pertas de uma coordenada, buscando em
    TODO provedor cadastrado (INMET, DMC, ...) e misturando por distancia
    -- ao contrario de so' olhar o catalogo do pais da propria fazenda,
    isso deixa escolher a estacao de referencia realmente mais perto
    mesmo quando ela e' de outro pais (ex.: fazenda perto de fronteira).
    `site_country` e' o pais CADASTRADO da fazenda que esta' pedindo
    (`models.get_site_country`/`site_country_overrides`) -- candidata de
    um pais DIFERENTE desse so' entra se estiver dentro de
    `DISTANCIA_MAXIMA_ESTACAO_VIZINHA_KM` (ver comentario la'); candidata
    do MESMO pais nunca tem esse teto. Pode devolver menos de `n` (ou
    nenhuma) se nao houver estacao nenhuma perto o suficiente nem no
    proprio pais nem fora, o que e' o comportamento certo (melhor cair
    pra "coordenada propria" do que usar uma estacao de outro
    continente). Cada estacao devolvida ganha "country_code" (o pais do
    PROVEDOR daquela estacao especifica -- usado por
    `set_weather_station_override` pra saber em qual catalogo procurar
    o codigo de novo depois; pode ser igual ou diferente de
    `site_country`).

    Busca em PARALELO (thread por pais) -- com 7+ provedores reais, cada
    um uma chamada de rede (com o proprio timeout e cache/backoff, ver
    `estacoes_cache_util.py`), fazer sequencial somaria o tempo de todos;
    em paralelo, o tempo total fica limitado ao mais lento so' (bug real
    de performance descoberto medindo o site -- aba Mapa levando 40+
    segundos com um pais fora do ar)."""
    def _buscar(item):
        code, info = item
        return [{**e, "country_code": code} for e in info["station_provider"].estacoes_mais_proximas(lat, lon, n=n)]

    candidatas = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(COUNTRIES)) as executor:
        for lista in executor.map(_buscar, COUNTRIES.items()):
            candidatas.extend(lista)
    candidatas = [
        e for e in candidatas
        if e["country_code"] == site_country or e["distancia_km"] <= DISTANCIA_MAXIMA_ESTACAO_VIZINHA_KM
    ]
    candidatas.sort(key=lambda e: e["distancia_km"])
    return candidatas[:n]


def _todos_catalogos_estacoes():
    """Catalogo de estacoes de TODO pais cadastrado, buscado uma unica
    vez (em paralelo, thread por pais) -- separado de
    `estacoes_mais_proximas_global` pra' `estacoes_mais_proximas_global_lote`
    poder reaproveitar o mesmo catalogo pra' varias fazendas sem recriar
    um pool de threads inteiro por fazenda (bug de performance real: a
    aba Fazendas com 11 fazendas cadastradas criava 11 pools de thread
    so' pra' repetir a mesma busca -- o catalogo de cada pais ja vinha
    cacheado, `estacoes_cache_util.cached_fetch`, mas o overhead de
    criar/destruir threads 11x ainda pesava)."""
    def _buscar(item):
        code, info = item
        return code, info["station_provider"].get_estacoes()

    catalogos = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(COUNTRIES)) as executor:
        for code, estacoes in executor.map(_buscar, COUNTRIES.items()):
            catalogos[code] = estacoes
    return catalogos


def estacoes_mais_proximas_global_lote(pontos, n=2):
    """Versao em lote de `estacoes_mais_proximas_global` -- `pontos` e'
    uma lista de (lat, lon, site_country); busca o catalogo de estacoes
    de cada pais UMA UNICA VEZ (`_todos_catalogos_estacoes`, so' esse
    passo usa threads) e depois calcula a distancia pra cada ponto em
    Python puro (sem thread nenhuma -- e' so' matematica sobre um
    catalogo ja em memoria, rapido mesmo pra dezenas de fazendas x
    centenas de estacoes). Mesma regra de filtro/corte da versao
    original (distancia maxima pra estacao de pais diferente da
    fazenda, top `n` por distancia), so' devolvida como uma lista
    PARALELA a `pontos` (resultado[i] corresponde a pontos[i])."""
    catalogos = _todos_catalogos_estacoes()
    resultados = []
    for lat, lon, site_country in pontos:
        candidatas = []
        for code, estacoes in catalogos.items():
            for e in estacoes:
                distancia_km = round(inmet_stations._haversine_km(lat, lon, e["lat"], e["lon"]), 1)
                candidatas.append({**e, "country_code": code, "distancia_km": distancia_km})
        candidatas = [
            e for e in candidatas
            if e["country_code"] == site_country or e["distancia_km"] <= DISTANCIA_MAXIMA_ESTACAO_VIZINHA_KM
        ]
        candidatas.sort(key=lambda e: e["distancia_km"])
        resultados.append(candidatas[:n])
    return resultados


def active_country_codes(site_country_map):
    """Codigos de pais realmente em uso (valores de `site_country_map`,
    ver `models.get_all_site_countries`) -- sempre inclui 'BR', mesmo sem
    nenhuma fazenda, pra manter o comportamento de sempre (fronteira/
    estacoes do Brasil aparecem incondicionalmente, como antes dessa
    funcionalidade existir)."""
    codes = {DEFAULT_COUNTRY}
    for code in site_country_map.values():
        if code in COUNTRIES:
            codes.add(code)
    return codes


def paises_com_estacoes():
    """Codigos de pais cujo provedor de estacoes ja' devolve pelo menos
    uma estacao de verdade -- ou seja, ja tem integracao funcionando
    (INMET, e agora DMC do Chile com credencial configurada), nao so'
    fronteira cadastrada. Pais com `sem_estacoes.py` como provedor nunca
    entra aqui (devolve [] sempre), entao um pais "so com fronteira
    pronta" (a maioria da America do Sul agora) nunca aparece no
    Mapa/Mapa Interpolado incondicionalmente -- so' quando tiver fazenda
    de verdade la (`active_country_codes`). Usado junto com
    `active_country_codes` pra decidir quais paises mostrar mesmo sem
    fazenda ainda: "mostra se ja tem estacao oficial de verdade pra
    referenciar", pedido explicito do usuario.

    Busca em PARALELO (thread por pais) -- mesmo motivo de
    `estacoes_mais_proximas_global`: sequencial somaria o timeout de
    cada provedor (chega a acontecer todo dia -- o cache de 24h expira
    e essa funcao e' chamada de novo em toda visita a /mapa e
    /mapa-interpolado)."""
    def _tem_estacoes(item):
        code, info = item
        return code if info["station_provider"].get_estacoes() else None

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(COUNTRIES)) as executor:
        resultados = executor.map(_tem_estacoes, COUNTRIES.items())
    return {code for code in resultados if code}
