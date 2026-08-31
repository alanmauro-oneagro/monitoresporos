"""Fazendas virtuais/estimadas -- pontos sem estacao fisica onde a
concentracao de esporo de cada doenca e' estimada por interpolacao (IDW,
Inverse Distance Weighting) a partir das fazendas reais dentro de um raio
escolhido pelo usuario. Util pra ter uma ideia do risco numa area sem
estacao propria (fazenda vizinha, area de expansao), mas NUNCA e' leitura
de verdade -- so entra na conta a fazenda real que estiver dentro do
raio (senao mostraria numero baseado em estacao longe demais pra fazer
sentido), e o cadastro (`models.create_virtual_farm`) sempre nomeia a
fazenda no padrao '"{nome}" - OneAgro', pra aparecer em toda tela sem
ser confundida com uma fazenda de verdade (ver
`models.virtual_farm_site_names`)."""
import math

import data_reader

IDW_POTENCIA = 2  # expoente da distancia no IDW -- padrao da literatura
DISTANCIA_MINIMA_KM = 0.05  # evita divisao por zero se o ponto cair em cima de uma estacao


def _haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def estacoes_no_raio(lat, lon, raio_km, coords_reais):
    """Lista de (site, distancia_km) das fazendas reais dentro do raio,
    mais perto primeiro."""
    vizinhas = []
    for site, (flat, flon) in coords_reais.items():
        d = _haversine_km(lat, lon, flat, flon)
        if d <= raio_km:
            vizinhas.append((site, round(d, 1)))
    vizinhas.sort(key=lambda item: item[1])
    return vizinhas


def interpolar_serie_diaria(vizinhas, dias, valor_do_dia):
    """Extensao do mesmo IDW de `interpolar_cards`, mas dia a dia -- usado
    pro grafico de Esporos e de Risco de Infeccao da aba Graficos
    mostrarem uma serie pra fazenda virtual/estimada (que nao tem leitura
    propria nenhuma). `vizinhas` e' o retorno de `estacoes_no_raio`
    (fixo, independente do filtro Estado/Estacao da pagina -- a
    interpolacao de um ponto virtual usa sempre as MESMAS fazendas reais
    dentro do raio dele, nao so as que estao selecionadas no filtro no
    momento). `valor_do_dia(site, dia_iso)` busca o valor (numero) de uma
    fazenda real vizinha naquele dia, ou None se nao tiver leitura --
    callback generico pra reusar com esporos e com risco (cada um busca
    o valor de um jeito diferente). Retorna lista alinhada com `dias`,
    com None nos dias em que NENHUMA fazenda da vizinhanca tem leitura."""
    pesos = [(site, 1 / (max(d, DISTANCIA_MINIMA_KM) ** IDW_POTENCIA)) for site, d in vizinhas]
    resultado = []
    for dia_iso in dias:
        entradas = [(peso, valor_do_dia(site, dia_iso)) for site, peso in pesos]
        entradas = [(p, v) for p, v in entradas if v is not None]
        if not entradas:
            resultado.append(None)
            continue
        peso_total = sum(p for p, _ in entradas)
        resultado.append(sum(p * v for p, v in entradas) / peso_total)
    return resultado


def interpolar_cards(lat, lon, raio_km, cards_by_site_reais, coords_reais):
    """Estima, por IDW, a concentracao de cada doenca no ponto (lat, lon)
    usando so as fazendas reais dentro de `raio_km`. Retorna
    (cards, estacoes_usadas) -- `cards` no mesmo formato de
    `data_reader.get_dashboard_data` (lista vazia se nenhuma fazenda real
    estiver dentro do raio) e `estacoes_usadas` a lista de (site,
    distancia_km) que entrou na conta, pra mostrar de onde veio a
    estimativa."""
    vizinhas = estacoes_no_raio(lat, lon, raio_km, coords_reais)
    if not vizinhas:
        return [], []

    por_doenca = {}
    for site, dist in vizinhas:
        for card in cards_by_site_reais.get(site, []):
            if card.get("concentracao") is None:
                continue
            por_doenca.setdefault(card["doenca_en"], []).append((dist, card))

    cards = []
    for doenca_en, entradas in sorted(por_doenca.items()):
        pesos = [1 / (max(d, DISTANCIA_MINIMA_KM) ** IDW_POTENCIA) for d, _ in entradas]
        peso_total = sum(pesos)
        conc = sum(p * c["concentracao"] for p, (_, c) in zip(pesos, entradas)) / peso_total
        modelo = entradas[0][1]
        # A mais recente, nao a mais antiga -- uma fazenda distante com
        # leitura parada nao deve prender a data (e o bloqueio por dado
        # velho) das outras fazendas que estao alimentando essa doenca.
        data_mais_recente = max(c["data"] for _, c in entradas)
        cards.append({
            "doenca": modelo["doenca"],
            "doenca_en": doenca_en,
            "cientifico": modelo.get("cientifico"),
            "concentracao": round(conc, 1),
            "status": data_reader.compute_status(conc, modelo.get("warn"), modelo.get("danger")),
            "data": data_mais_recente,
            "umidade": None,
            "chuva": None,
        })
    cards.sort(key=lambda c: c["doenca"])
    return cards, vizinhas
