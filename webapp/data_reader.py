"""Le os CSVs gerados pelo pipeline existente (Fetch-BioScoutData.ps1) e monta
os dados do dashboard web -- mesma logica de status/cores do BioScoutDashboard.xlsx
(aba Alertas do Dia), para nao duplicar regras de negocio em dois lugares."""
import csv
import math
import os
from collections import Counter
from pathlib import Path
from datetime import datetime, timedelta

DATA_DIR = Path(os.environ.get("BIOSCOUT_DATA_DIR", str(Path(__file__).parent.parent / "data")))

DOENCA_MAP = {
    "General Alternaria": "Mancha de Alternaria",
    "General Rust": "Ferrugem do Milho",
    "Target Spot": "Mancha Alvo",
    "Powdery Mildew": "Oidio",
    "Moniliophthora spp. BETA": "Moniliophthora",
    "Soybean Rust": "Ferrugem da Soja",
    "Anthracnose": "Antracnose",
    "Dry rot": "Fusarium",
    "Septoria": "Septoriose",
}

# Valores iniciais (semente) das 10 caixas de "Nome Culturas" (menu Opcoes)
# -- so usado na primeira vez que o banco e' criado; depois disso e'
# 100% editavel pelo admin e vive na tabela `culturas`.
DEFAULT_CULTURAS = ["Soja", "Milho", "Algodao", "Feijao", "Citrus", "Cana", "Batata", "", "", "", "", ""]

# Semente inicial da matriz doenca x cultura (aba Doencas) -- chave = nome
# original em ingles (mesmo de DOENCA_MAP), valor = uma cultura de
# DEFAULT_CULTURAS. So usado na primeira vez que o banco e' criado; depois
# vira 100% editavel na matriz (tabela `doenca_cultura`, muitos-para-muitos
# -- uma doenca pode valer para mais de uma cultura). Doenca sem nenhuma
# marcacao na matriz NUNCA e' escondida pelo filtro de cultura -- so
# filtramos o que foi marcado explicitamente, pra nao esconder um alerta
# relevante por engano.
DEFAULT_DOENCA_CULTURA = {
    "Soybean Rust": "Soja",
    "Target Spot": "Soja",
    "Powdery Mildew": "Soja",
    "Septoria": "Soja",
    "General Alternaria": "Soja",
    "Anthracnose": "Soja",
    "Dry rot": "Soja",
    "General Rust": "Milho",
    # "Moniliophthora spp. BETA" e' doenca de cacau -- fora das culturas
    # foco, deixada sem mapeamento de proposito.
}


def get_doenca(display_name, translations=None):
    """translations (dict {en: {"nome_pt", "nome_cientifico"}}, vindo do
    banco -- ver `models.get_all_disease_translations`) tem prioridade
    sobre o mapa padrao abaixo -- e' o que a aba de admin "Doencas"
    edita. Se a doenca for desconhecida dos dois, mostra o nome em
    ingles mesmo (nunca quebra por causa de uma doenca nova no
    BioScout)."""
    if translations and display_name in translations:
        return translations[display_name]["nome_pt"]
    return DOENCA_MAP.get(display_name, display_name)


_derivados_cache = {}  # nome -> (lista do _csv_cache que gerou, resultado)


def _derivado_das_leituras(nome, calcular):
    """Resultado de `calcular(linhas do spore_counts.csv)` guardado ate' o
    CSV mudar -- varrer as ~14 mil linhas a cada chamada pesava em toda
    troca de aba (mesmo esquema de `read_site_coordinates`)."""
    linhas = read_spore_counts()
    cache = _derivados_cache.get(nome)
    if cache is None or cache[0] is not linhas:
        cache = _derivados_cache[nome] = (linhas, calcular(linhas))
    return cache[1]


def read_unique_display_names():
    """Nomes de doenca em ingles (displayName) que ja apareceram nos dados
    -- usado pela aba de admin para saber quais existem e podem ser
    traduzidas, incluindo doencas novas que ainda nao estao no DOENCA_MAP."""
    return list(_derivado_das_leituras(
        "display_names", lambda linhas: sorted({row["displayName"] for row in linhas if row.get("displayName")}),
    ))


def read_scientific_names():
    """displayName (ingles) -> nome cientifico do fungo (scientificName), que
    o BioScout ja manda por leitura -- usado para pre-preencher a aba
    Doencas com o nome cientifico, sem precisar digitar na mao."""
    def calcular(linhas):
        names = {}
        for row in linhas:
            display_name = row.get("displayName")
            scientific = row.get("scientificName")
            if display_name and scientific and display_name not in names:
                names[display_name] = scientific
        return names
    return dict(_derivado_das_leituras("cientificos", calcular))


def _to_float(value):
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(",", "."))
    except ValueError:
        return None


def _parse_dt(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def read_site_coordinates():
    """siteName -> (latitude, longitude) -- uma coordenada por fazenda,
    tirada direto do spore_counts.csv (o BioScout ja manda isso por
    dispositivo, e cada fazenda usa sempre a mesma). Cacheado pelo mtime
    do CSV -- roda varias vezes por request (inclusive no before_request)
    e varria as ~14 mil linhas a cada chamada."""
    linhas = read_spore_counts()
    if _site_coordinates_cache.get("linhas") is linhas:
        return dict(_site_coordinates_cache["coords"])
    coords = {}
    for row in linhas:
        site = row.get("siteName")
        if site and site not in coords:
            lat = _to_float(row.get("latitude"))
            lon = _to_float(row.get("longitude"))
            if lat is not None and lon is not None:
                coords[site] = (lat, lon)
    _site_coordinates_cache["linhas"] = linhas
    _site_coordinates_cache["coords"] = coords
    return dict(coords)


_site_coordinates_cache = {}  # {"linhas": lista do _csv_cache que gerou, "coords": ...}


def read_site_device_ids():
    """siteName -> deviceUserFriendlyId -- spore_counts.csv tem os dois
    campos por linha (o nome de exibicao da fazenda e o identificador cru
    do dispositivo), mas weather.csv SO' tem deviceUserFriendlyId (sem
    siteName) -- essa e' a ponte pra cruzar leitura de esporo (por
    siteName) com clima historico (por deviceUserFriendlyId), usada no
    grafico de risco de germinacao da aba Graficos."""
    def calcular(linhas):
        ids = {}
        for row in linhas:
            site = row.get("siteName")
            device = row.get("deviceUserFriendlyId")
            if site and device and site not in ids:
                ids[site] = device
        return ids
    return dict(_derivado_das_leituras("device_ids", calcular))


_csv_cache = {}  # path -> (mtime, linhas)


_DOENCA_MAP_POR_MINUSCULO = {nome.lower(): nome for nome in DOENCA_MAP}
_ultimo_mapa_canonico = {}  # minusculo -> grafia canonica, ver _normalizar_display_names


def _build_canonical_map(linhas):
    """minusculo -> grafia canonica: se bater com uma doenca ja conhecida
    em DOENCA_MAP, usa a grafia de la'; senao, usa a PRIMEIRA grafia
    vista pra esse nome (ordem do arquivo)."""
    canonico_por_minusculo = {}
    for linha in linhas:
        nome = linha.get("displayName")
        if not nome:
            continue
        chave = nome.lower()
        if chave not in canonico_por_minusculo:
            canonico_por_minusculo[chave] = _DOENCA_MAP_POR_MINUSCULO.get(chave, nome)
    return canonico_por_minusculo


def _normalizar_display_names(linhas):
    """O mesmo fungo pode vir com capitalizacao diferente conforme o
    dispositivo/regiao que gerou a leitura -- confirmado na pratica:
    "Grey Mould" (dispositivo no Brasil) x "Grey mould" (dispositivo no
    Chile), a mesma doenca (Botrytis spp.) virando DUAS linhas na aba
    Doencas (e, pior, dois cartoes de alerta separados pra mesma
    fazenda/doenca se ela relatar as duas grafias ao longo do tempo).
    Uniformiza `displayName` por comparacao sem diferenciar
    maiuscula/minuscula, assim toda leitura da mesma doenca sempre vira
    o mesmo `displayName`, nao importa a grafia original. Guarda o mapa
    usado em `_ultimo_mapa_canonico`, pra' `nome_canonico_para` poder
    resolver ate' uma grafia antiga que nao aparece mais como valor de
    nenhuma linha (ver `models.merge_duplicate_disease_translations`,
    que junta linhas duplicadas salvas ANTES dessa normalizacao
    existir)."""
    global _ultimo_mapa_canonico
    mapa = _build_canonical_map(linhas)
    for linha in linhas:
        nome = linha.get("displayName")
        if nome:
            linha["displayName"] = mapa[nome.lower()]
    _ultimo_mapa_canonico = mapa


def nome_canonico_para(display_name_en):
    """Grafia canonica atual (ver `_normalizar_display_names`) pra'
    qualquer displayName, mesmo uma grafia antiga que nao aparece mais
    como valor de nenhuma linha do CSV depois da normalizacao."""
    read_spore_counts()  # garante que _ultimo_mapa_canonico esta' atualizado
    chave = display_name_en.lower()
    if chave in _ultimo_mapa_canonico:
        return _ultimo_mapa_canonico[chave]
    return _DOENCA_MAP_POR_MINUSCULO.get(chave, display_name_en)


def _read_csv_cached(path, postprocess=None):
    """Le e faz o parse do CSV, cacheado em memoria pelo mtime do arquivo
    -- MUITAS funcoes diferentes chamam read_spore_counts/read_weather
    (direto ou indireto, via read_site_coordinates/read_site_device_ids/
    etc.) dentro do MESMO request; sem cache, cada chamada reabria e
    reparseava o CSV inteiro do zero so' pra' devolver de novo o mesmo
    resultado da chamada anterior (medido: a aba Manejo Safra sozinha
    lia spore_counts.csv 5 vezes numa unica pagina, quase 1s so' nisso).
    O pipeline (Fetch-BioScoutData.ps1) so' reescreve esses arquivos de
    vez em quando -- o cache invalida sozinho assim que o mtime do
    arquivo mudar, entao nunca fica com dado desatualizado. Os `dict` de
    cada linha sao compartilhados entre chamadas (mesma lista, nao uma
    copia) -- nenhum consumidor deste modulo ou de app.py altera uma
    linha depois de ler, so' faz `.get()`/leitura, entao compartilhar e'
    seguro e evita copiar a lista inteira a toa. `postprocess` (se dado)
    roda so' UMA VEZ por leitura de verdade do arquivo (cache miss), nao
    a cada chamada -- pra normalizacao (ver `_normalizar_display_names`)
    nao repetir um trabalho O(n) toda vez que alguem pedir os dados ja'
    em cache."""
    mtime = path.stat().st_mtime
    cached = _csv_cache.get(path)
    if cached and cached[0] == mtime:
        return cached[1]
    with open(path, encoding="utf-8-sig", newline="") as f:
        linhas = list(csv.DictReader(f))
    if postprocess:
        postprocess(linhas)
    _csv_cache[path] = (mtime, linhas)
    return linhas


def read_sites():
    return [row["siteName"] for row in _read_csv_cached(DATA_DIR / "sites.csv")]


def read_spore_counts():
    return _read_csv_cached(DATA_DIR / "spore_counts.csv", postprocess=_normalizar_display_names)


_disease_concentration_lookup_cache = {}


def build_disease_concentration_lookup(spore_rows):
    """(siteName, displayName) -> lista de pontos diarios (uma leitura por
    dia, a mais recente do dia quando ha mais de uma) ordenados por data,
    cada um com concentracao + os 3 limites (warning/danger/maximum) da
    leitura -- mesma logica de `get_site_disease_history`, mas numa unica
    passada sobre o CSV inteiro (usado pelo grafico da aba Graficos, que
    precisa disso pra TODAS as fazendas x doencas de uma vez, nao so uma
    fazenda por vez como o PDF). Cacheado pelo mtime de spore_counts.csv
    -- mesmo motivo de `build_weather_lookup` acima."""
    mtime = (DATA_DIR / "spore_counts.csv").stat().st_mtime
    cached = _disease_concentration_lookup_cache.get(mtime)
    if cached is not None:
        return cached
    grouped = {}
    for row in spore_rows:
        site, doenca = row.get("siteName"), row.get("displayName")
        try:
            dt = _parse_dt(row["samplingStartTime"])
        except (KeyError, ValueError):
            continue
        conc = _to_float(row.get("concentration"))
        if conc is None:
            continue
        key = (site, doenca)
        por_dia = grouped.setdefault(key, {})
        data_iso = dt.date().isoformat()
        if data_iso not in por_dia or dt > por_dia[data_iso]["_dt"]:
            por_dia[data_iso] = {
                "_dt": dt, "data": data_iso, "concentracao": conc,
                "warn": _to_float(row.get("warningConcentrationThreshold")),
                "danger": _to_float(row.get("dangerConcentrationThreshold")),
                "maximo": _to_float(row.get("maximumConcentrationThreshold")),
            }
    lookup = {}
    for key, por_dia in grouped.items():
        dias_ordenados = sorted(por_dia.values(), key=lambda r: r["_dt"])
        lookup[key] = [{k: v for k, v in r.items() if k != "_dt"} for r in dias_ordenados]
    _disease_concentration_lookup_cache.clear()
    _disease_concentration_lookup_cache[mtime] = lookup
    return lookup


_DIRECOES_VENTO = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]


def _bucket_direcao_vento(graus):
    if graus is None:
        return None
    idx = round((graus % 360) / 45) % 8
    return _DIRECOES_VENTO[idx]


def vento_predominante_do_dia(horas_do_dia):
    """Direcao de vento mais frequente (moda, 8 pontos) entre as leituras
    horarias de um dia (`build_hourly_weather_lookup`) -- None se nao
    houver nenhuma leitura de vento nesse dia."""
    if not horas_do_dia:
        return None
    direcoes = [d for d in (_bucket_direcao_vento(h.get("vento")) for h in horas_do_dia) if d]
    if not direcoes:
        return None
    return Counter(direcoes).most_common(1)[0][0]


def media_circular_direcoes(direcoes):
    """Media circular de uma lista de direcoes ja' bucketadas (N/NE/E...)
    -- usada pra combinar a direcao predominante de varias estacoes num
    unico dia (grafico de risco/vento da aba Graficos, quando mais de
    uma fazenda esta selecionada no filtro). Nao da' pra' so' somar os
    graus e dividir (media de 350 e 10 graus tem que dar 0, nao 180) --
    converte cada direcao pro angulo central do seu bucket, tira a media
    vetorial (seno/cosseno) e rebucketiza o resultado pras mesmas 8
    direcoes. None se a lista vier vazia ou sem nenhuma direcao valida."""
    graus_por_direcao = {d: i * 45 for i, d in enumerate(_DIRECOES_VENTO)}
    soma_sin = soma_cos = 0.0
    n = 0
    for d in direcoes:
        graus = graus_por_direcao.get(d)
        if graus is None:
            continue
        rad = math.radians(graus)
        soma_sin += math.sin(rad)
        soma_cos += math.cos(rad)
        n += 1
    if n == 0 or (soma_sin == 0 and soma_cos == 0):
        return None
    media_graus = math.degrees(math.atan2(soma_sin, soma_cos))
    return _bucket_direcao_vento(media_graus)


def contar_direcoes_vento(hourly_lookup, devices, dias):
    """Conta quantas leituras horarias (das estacoes/dias dados) vieram
    de cada uma das 8 direcoes -- usado pela rosa dos ventos da aba
    Graficos, que agrega TODAS as estacoes/dias visiveis no filtro atual
    num unico grafico (ao contrario da setinha por dia, que e' por
    estacao). Retorna um dict na mesma ordem de `_DIRECOES_VENTO`
    (N, NE, E, SE, S, SW, W, NW) -- inclusive as com contagem zero, pra'
    o grafico sempre desenhar os 8 gomos."""
    contagem = {d: 0 for d in _DIRECOES_VENTO}
    for device in devices:
        for dia_iso in dias:
            for h in hourly_lookup.get((device, dia_iso), []):
                direcao = _bucket_direcao_vento(h.get("vento"))
                if direcao:
                    contagem[direcao] += 1
    return contagem


def relatorio_diario_de_horas(hourly_lookup, ur_limiares=(80, 85, 90, 95), ur_molhamento=90):
    """Mesmas linhas de `build_daily_weather_report` (temp min/max, horas
    com UR >= cada limiar, horas de molhamento, vento predominante), a
    partir de um lookup {(estacao, dia): [{"temp", "umidade", "chuva",
    "vento"}, ...]} -- o clima horario de fonte externa
    (`app._completar_clima_horario`), ja' que o clima nao vem mais do
    BioScout."""
    rows = []
    for (estacao, data_iso), horas in hourly_lookup.items():
        temps = [h["temp"] for h in horas if h.get("temp") is not None]
        urs = [h["umidade"] for h in horas if h.get("umidade") is not None]
        direcoes = [d for d in (_bucket_direcao_vento(h.get("vento")) for h in horas) if d]
        rows.append({
            "estacao": estacao,
            "data": data_iso,
            "temp_min": min(temps) if temps else None,
            "temp_max": max(temps) if temps else None,
            "ur_counts": {l: sum(1 for ur in urs if ur >= l) for l in ur_limiares},
            "horas_molhamento": sum(1 for ur in urs if ur >= ur_molhamento),
            "vento_predominante": Counter(direcoes).most_common(1)[0][0] if direcoes else None,
        })
    rows.sort(key=lambda r: (r["estacao"] or "").lower())
    rows.sort(key=lambda r: r["data"], reverse=True)
    return rows


def get_site_disease_history(site, doenca_en, dias=15):
    """Serie historica de concentracao (uma leitura por dia, a mais recente
    do dia quando ha mais de uma) de uma doenca numa fazenda -- usado pra
    montar o graficozinho de concentracao no PDF de recomendacao (mesmo
    eixo colorido do dashboard do BioScout: verde/amarelo/vermelho
    conforme warningConcentrationThreshold/dangerConcentrationThreshold/
    maximumConcentrationThreshold, que sao fixos por doenca -- pega os do
    ultimo dia com leitura). SEMPRE cobre uma janela de `dias` dias
    CORRIDOS (calendario) terminando no dia da leitura mais recente --
    dia sem leitura entra com concentracao=None (gap no grafico, ver
    `export_pdf._build_spore_chart`) em vez de "pular" pro dia com
    leitura anterior; antes, um sensor com falha intermitente esticava a
    janela silenciosamente pra' mais de `dias` dias corridos (contava
    dias COM leitura, nao dias corridos), fazendo o grafico do PDF as
    vezes olhar bem mais longe no passado do que os `dias` pedidos."""
    por_dia = {}
    for row in read_spore_counts():
        if row.get("siteName") != site or row.get("displayName") != doenca_en:
            continue
        try:
            dt = _parse_dt(row["samplingStartTime"])
        except (KeyError, ValueError):
            continue
        conc = _to_float(row.get("concentration"))
        if conc is None:
            continue
        data_iso = dt.date().isoformat()
        if data_iso not in por_dia or dt > por_dia[data_iso]["_dt"]:
            por_dia[data_iso] = {
                "_dt": dt,
                "data": data_iso,
                "concentracao": conc,
                "warn": _to_float(row.get("warningConcentrationThreshold")),
                "danger": _to_float(row.get("dangerConcentrationThreshold")),
                "maximo": _to_float(row.get("maximumConcentrationThreshold")),
            }
    if not por_dia:
        return []
    mais_recente = max(por_dia.values(), key=lambda r: r["_dt"])
    ultima_data = mais_recente["_dt"].date()
    limites = {k: mais_recente[k] for k in ("warn", "danger", "maximo")}
    resultado = []
    for i in range(dias):
        dia = ultima_data - timedelta(days=dias - 1 - i)
        iso = dia.isoformat()
        r = por_dia.get(iso)
        if r:
            resultado.append({"data": iso, "concentracao": r["concentracao"], **limites})
        else:
            resultado.append({"data": iso, "concentracao": None, **limites})
    return resultado


def compute_status(concentration, warning_threshold, danger_threshold):
    if danger_threshold and danger_threshold > 0 and concentration >= danger_threshold:
        return "Perigo"
    if warning_threshold and warning_threshold > 0 and concentration >= warning_threshold:
        return "Atencao"
    return "Normal"


def get_dashboard_data(permitted_site_names=None, translations=None):
    """Por fazenda permitida, retorna a ultima leitura de cada doenca com
    status."""
    def ultimas(linhas):
        latest = {}
        for row in linhas:
            try:
                dt = _parse_dt(row["samplingStartTime"])
            except (KeyError, ValueError):
                continue
            key = (row.get("siteName"), row.get("displayName"))
            if key not in latest or dt > latest[key]["_dt"]:
                latest[key] = {"_dt": dt, "row": row}
        return latest

    cards_by_site = {}
    for (site, disease), info in _derivado_das_leituras("ultimas_leituras", ultimas).items():
        if permitted_site_names is not None and site not in permitted_site_names:
            continue
        row = info["row"]
        dt = info["_dt"]
        conc = _to_float(row.get("concentration"))
        warn = _to_float(row.get("warningConcentrationThreshold"))
        danger = _to_float(row.get("dangerConcentrationThreshold"))
        status = compute_status(conc or 0, warn, danger)
        cientifico_salvo = translations.get(disease, {}).get("nome_cientifico") if translations else None
        card = {
            "doenca": get_doenca(disease, translations),
            "doenca_en": disease,
            "cientifico": cientifico_salvo or row.get("scientificName"),
            "concentracao": round(conc, 1) if conc is not None else None,
            "status": status,
            "data": dt.date().isoformat(),
            "warn": warn,
            "danger": danger,
        }
        cards_by_site.setdefault(site, []).append(card)

    for site in cards_by_site:
        cards_by_site[site].sort(key=lambda c: c["doenca"])

    return cards_by_site
