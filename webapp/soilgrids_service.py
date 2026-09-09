"""Tipo de solo (estimado por textura) pra fora do Brasil, via SoilGrids
(ISRIC -- https://www.isric.org/explore/soilgrids), fallback global usado
so quando `soil_service.tipo_solo_embrapa` devolve None (fora do Brasil,
ou gap no mapa) -- ver `app._auto_detectar_solos_novos`.

IMPORTANTE -- SoilGrids v2 NAO publica mais um mapa de classes de solo
(ao contrario do Mapa de Solos do Brasil, que da' uma classe SiBCS de
verdade) -- so' publica propriedades continuas (% areia/silte/argila, pH,
materia organica etc). Pra chegar a um "tipo" comparavel, classificamos
areia/silte/argila pelo triangulo textural do USDA (formula fixa e
conhecida, sem inferencia nenhuma) -- o resultado ("Franco-argiloso" etc)
e' uma classificacao de TEXTURA, nao equivalente a uma ordem SiBCS. Por
isso a fonte devolvida aqui e' sempre "SoilGrids (textura estimada)",
nunca so' "SoilGrids", pra deixar claro pra quem le' na tela que nao e'
o mesmo tipo de informacao do Embrapa.

Nota conhecida no momento em que isso foi escrito: a API publica REST do
SoilGrids esta fora do ar (sem previsao) -- o codigo abaixo segue o
formato de chamada documentado oficialmente e degrada graciosamente
(devolve None) em qualquer falha, mesmo padrao de
`weather_forecast.get_historico_por_dia`/`ndvi_service.buscar_ndvi` --
so' vai realmente devolver dado quando a ISRIC restaurar o servico."""
import json
import urllib.parse
import urllib.request

PROPERTIES_URL = "https://rest.isric.org/soilgrids/v2.0/properties/query"


def _classificar_textura(areia_pct, silte_pct, argila_pct):
    """Triangulo textural USDA (classes principais, fronteiras oficiais)
    a partir de % areia/silte/argila (devem somar ~100). Implementacao
    direta das regras publicadas pelo USDA-NRCS -- sem biblioteca externa."""
    areia, silte, argila = areia_pct, silte_pct, argila_pct
    if argila >= 40:
        if areia >= 45:
            return "Argiloarenoso" if silte < 40 else "Franco-argiloarenoso"
        if silte >= 40:
            return "Argilossiltoso"
        return "Argiloso"
    if argila >= 27:
        if areia >= 45:
            return "Franco-argiloarenoso"
        if silte >= 28:
            return "Franco-argilossiltoso"
        return "Franco-argiloso"
    if argila >= 20 and areia <= 45 and silte < 28:
        return "Franco-argiloarenoso"
    if silte >= 80 and argila < 12:
        return "Siltoso"
    if silte >= 50 and argila < 27:
        return "Franco-siltoso"
    if areia >= 70 and argila < 15:
        return "Arenoso" if silte < 15 else "Franco-arenoso"
    if areia >= 52 and argila < 20:
        return "Franco-arenoso"
    return "Franco"


def tipo_solo_soilgrids(lat, lon):
    """Consulta areia/silte/argila (0-5cm, media) do SoilGrids pra
    coordenada informada e classifica por textura -- devolve
    {"classe": <textura>, "fonte": "SoilGrids (textura estimada)"} ou
    None em qualquer falha (rede, API fora do ar, resposta sem os 3
    valores) -- nunca levanta excecao."""
    params = {
        "lon": lon, "lat": lat,
        "property": ["sand", "silt", "clay"],
        "depth": "0-5cm",
        "value": "mean",
    }
    url = f"{PROPERTIES_URL}?{urllib.parse.urlencode(params, doseq=True)}"
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            dados = json.load(resp)
    except Exception:
        return None

    try:
        valores = {}
        for camada in dados["properties"]["layers"]:
            nome = camada["name"]
            # SoilGrids devolve g/kg (ou g/100g conforme d_factor) --
            # normaliza pra % dividindo pelo d_factor da propria resposta.
            profundidade = camada["depths"][0]
            bruto = profundidade["values"]["mean"]
            d_factor = camada.get("unit_measure", {}).get("d_factor", 10)
            valores[nome] = bruto / d_factor
        areia, silte, argila = valores["sand"], valores["silt"], valores["clay"]
    except (KeyError, IndexError, TypeError, ZeroDivisionError):
        return None

    total = areia + silte + argila
    if total <= 0:
        return None
    # Renormaliza pra somar 100 (arredondamento da API pode desviar um
    # pouco) antes de classificar.
    fator = 100 / total
    textura = _classificar_textura(areia * fator, silte * fator, argila * fator)
    return {"classe": textura, "ordem": None, "fonte": "SoilGrids (textura estimada)"}
