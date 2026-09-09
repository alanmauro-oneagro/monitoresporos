"""Tipo de solo por coordenada, a partir do Mapa de Solos do Brasil
(Embrapa/CNPS, classificacao SiBCS ao 3o nivel categorico, escala
1:5.000.000) -- fonte publica, sem autenticacao, servida via WFS/GeoServer:
https://geoinfo.dados.embrapa.br/geoserver/ows?service=WFS&version=1.0.0&request=GetFeature&typename=geonode:brasil_solos_5m_20201104&outputFormat=json

O geojson bruto (2852 poligonos, 14.77MB) foi baixado UMA VEZ e salvo,
simplificado (shapely.simplify, tolerancia ~1km -- adequado pra escala
1:5.000.000) e com as properties reduzidas ao que interessa aqui, em
`static/soils/br_solos.geojson` (3.99MB) -- mesmo padrao ja usado pras
fronteiras de pais estaticas do Chile (ver `countries._poligono_pais`).
So cobre o Brasil -- fora daqui, quem chama cai pro fallback global
(`soilgrids_service.tipo_solo_soilgrids`, ver `app._auto_detectar_solos_novos`).

Duas classes do mapa NAO sao solo de verdade (corpo d'agua/afloramento de
rocha) -- tratadas como "sem solo" (devolve None), nao como um tipo."""
import json
from pathlib import Path

from shapely.geometry import Point, shape
from shapely.strtree import STRtree

GEOJSON_PATH = Path(__file__).parent / "static" / "soils" / "br_solos.geojson"

# As ~13 ordens do SiBCS + as classes nao-solo do mapa (AGUA/afloramentos/
# dunas) que aparecem no dataset -- cor fixa por ordem pra colorir a camada
# do mapa (aba Solo). Default cinza-claro pra qualquer ordem imprevista.
CORES_POR_ORDEM = {
    "LATOSSOLOS": "#c0392b",
    "ARGISSOLOS": "#e67e22",
    "NEOSSOLOS": "#f1c40f",
    "CAMBISSOLOS": "#8e44ad",
    "GLEISSOLOS": "#16a085",
    "CHERNOSSOLOS": "#2c3e50",
    "ESPODOSSOLOS": "#d35400",
    "LUVISSOLOS": "#c0a062",
    "NITOSSOLOS": "#a93226",
    "ORGANOSSOLOS": "#6e2c00",
    "PLANOSSOLOS": "#7f8c8d",
    "PLINTOSSOLOS": "#af601a",
    "VERTISSOLOS": "#34495e",
    "AFLORAMENTOS DE ROCHAS": "#95a5a6",
    "DUNAS": "#f9e79f",
    "AGUA": "#3498db",
}
COR_DEFAULT = "#bdc3c7"

# Nao sao solo -- resolvem pra "sem dado" em vez de virar um "tipo" (ver
# `tipo_solo_embrapa`).
_ORDENS_SEM_SOLO = {"AGUA"}

_cache = {"tree": None, "poligonos": [], "info": []}


def _carregar_poligonos():
    """Le e cacheia (uma vez por processo) o geojson estatico -- listas
    PARALELAS de poligonos shapely e metadados (o STRtree.query devolve
    indices nessa mesma lista de poligonos, nao os poligonos em si --
    por isso guardamos os dois lado a lado), com um STRtree por cima pra
    busca espacial (2852 poligonos, scan linear seria desnecessario)."""
    if _cache["tree"] is not None:
        return _cache["tree"], _cache["poligonos"], _cache["info"]
    poligonos, info = [], []
    try:
        with open(GEOJSON_PATH, encoding="utf-8") as f:
            dados = json.load(f)
        for feat in dados.get("features", []):
            poligonos.append(shape(feat["geometry"]))
            info.append(feat.get("properties", {}))
    except Exception:
        poligonos, info = [], []
    _cache["tree"] = STRtree(poligonos)
    _cache["poligonos"] = poligonos
    _cache["info"] = info
    return _cache["tree"], _cache["poligonos"], _cache["info"]


def tipo_solo_embrapa(lat, lon):
    """Tipo de solo (classificacao SiBCS) da coordenada informada, pelo
    Mapa de Solos do Brasil -- devolve
    {"classe": <descricao da legenda>, "ordem": <ordem SiBCS>,
     "fonte": "Embrapa/PronaSolos"}, ou None se a coordenada cair fora
    de qualquer poligono (fora do Brasil) OU num poligono de "sem solo"
    (AGUA)."""
    tree, poligonos, info = _carregar_poligonos()
    if not info:
        return None
    ponto = Point(lon, lat)
    for i in tree.query(ponto):
        if not poligonos[i].contains(ponto):
            continue
        props = info[i]
        ordem = props.get("ordem") or ""
        if ordem in _ORDENS_SEM_SOLO:
            return None
        return {
            "classe": props.get("legenda") or props.get("classe") or ordem,
            "ordem": ordem,
            "fonte": "Embrapa/PronaSolos",
        }
    return None
