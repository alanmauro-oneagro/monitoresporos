"""Registro dos paises suportados -- Brasil (padrao/legado, tudo ao vivo
via IBGE/INMET, sem mudanca nenhuma) e Chile (fronteiras estaticas via
geoBoundaries.org, estacoes via DMC -- ver `dmc_stations.py`). Um pais
novo depois disso e' so' uma entrada aqui + um modulo de estacoes, sem
precisar mexer nas rotas/templates que ja iteram sobre `COUNTRIES`.

`station_provider` e' uma referencia direta ao modulo (nao uma string),
pra `/mapa` e `/mapa-interpolado` chamarem `.get_estacoes()`/
`.estacao_mais_proxima()` genericamente sem if/elif por pais."""
import inmet_stations
import dmc_stations

DEFAULT_COUNTRY = "BR"

COUNTRIES = {
    "BR": {
        "nome": "Brasil",
        "boundary_mode": "live_ibge",  # busca ao vivo na API do IBGE, ver mapa.html/mapa_interpolado.html
        "station_provider": inmet_stations,
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
        "cloud_grid_bbox": {"lat_min": -56, "lat_max": -17, "lon_min": -76, "lon_max": -66},
    },
}


def get_country(code):
    return COUNTRIES.get(code, COUNTRIES[DEFAULT_COUNTRY])


def estacoes_mais_proximas_global(lat, lon, n=2):
    """As `n` estacoes oficiais mais pertas de uma coordenada, buscando em
    TODO provedor cadastrado (INMET, DMC, ...) e misturando por distancia
    -- ao contrario de so' olhar o catalogo do pais da propria fazenda,
    isso deixa escolher a estacao de referencia realmente mais perto
    mesmo quando ela e' de outro pais (ex.: fazenda perto de fronteira).
    Cada estacao devolvida ganha "country_code" (o pais do PROVEDOR
    daquela estacao especifica -- usado por `set_weather_station_override`
    pra saber em qual catalogo procurar o codigo de novo depois; nao tem
    nada a ver com o pais da fazenda que fez a busca)."""
    candidatas = []
    for code, info in COUNTRIES.items():
        for e in info["station_provider"].estacoes_mais_proximas(lat, lon, n=n):
            candidatas.append({**e, "country_code": code})
    candidatas.sort(key=lambda e: e["distancia_km"])
    return candidatas[:n]


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
