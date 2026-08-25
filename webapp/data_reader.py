"""Le os CSVs gerados pelo pipeline existente (Fetch-BioScoutData.ps1) e monta
os dados do dashboard web -- mesma logica de status/cores do BioScoutDashboard.xlsx
(aba Alertas do Dia), para nao duplicar regras de negocio em dois lugares."""
import csv
import os
from pathlib import Path
from datetime import datetime

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
DEFAULT_CULTURAS = ["Soja", "Milho", "Algodao", "Feijao", "Citrus", "Cana", "Batata", "", "", ""]

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
    """translations (dict opcional, vindo do banco) tem prioridade sobre o
    mapa padrao abaixo -- e' o que a aba de admin "Doencas" edita. Se a
    doenca for desconhecida dos dois, mostra o nome em ingles mesmo (nunca
    quebra por causa de uma doenca nova no BioScout)."""
    if translations and display_name in translations:
        return translations[display_name]
    return DOENCA_MAP.get(display_name, display_name)


def read_unique_display_names():
    """Nomes de doenca em ingles (displayName) que ja apareceram nos dados
    -- usado pela aba de admin para saber quais existem e podem ser
    traduzidas, incluindo doencas novas que ainda nao estao no DOENCA_MAP."""
    names = set()
    for row in read_spore_counts():
        name = row.get("displayName")
        if name:
            names.add(name)
    return sorted(names)


def read_scientific_names():
    """displayName (ingles) -> nome cientifico do fungo (scientificName), que
    o BioScout ja manda por leitura -- usado para pre-preencher a aba
    Doencas com o nome cientifico, sem precisar digitar na mao."""
    names = {}
    for row in read_spore_counts():
        display_name = row.get("displayName")
        scientific = row.get("scientificName")
        if display_name and scientific and display_name not in names:
            names[display_name] = scientific
    return names


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
    dispositivo, e cada fazenda usa sempre a mesma)."""
    coords = {}
    for row in read_spore_counts():
        site = row.get("siteName")
        if site and site not in coords:
            lat = _to_float(row.get("latitude"))
            lon = _to_float(row.get("longitude"))
            if lat is not None and lon is not None:
                coords[site] = (lat, lon)
    return coords


def read_sites():
    path = DATA_DIR / "sites.csv"
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        return [row["siteName"] for row in reader]


def read_spore_counts():
    path = DATA_DIR / "spore_counts.csv"
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        return list(reader)


def read_weather():
    path = DATA_DIR / "weather.csv"
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        return list(reader)


def build_weather_lookup(weather_rows):
    """chave: (deviceUserFriendlyId, data-yyyy-mm-dd) -> {umidade, chuva}"""
    grouped = {}
    for row in weather_rows:
        try:
            dt = _parse_dt(row["dateMeasured"])
        except (KeyError, ValueError):
            continue
        key = (row.get("deviceUserFriendlyId"), dt.date().isoformat())
        bucket = grouped.setdefault(key, {"humidity": [], "rain": []})
        hum = _to_float(row.get("humidity"))
        rain = _to_float(row.get("rainFall"))
        if hum is not None:
            bucket["humidity"].append(hum)
        if rain is not None:
            bucket["rain"].append(rain)

    lookup = {}
    for key, vals in grouped.items():
        umidade = round(sum(vals["humidity"]) / len(vals["humidity"])) if vals["humidity"] else None
        chuva = round(sum(vals["rain"]), 1) if vals["rain"] else 0
        lookup[key] = {"umidade": umidade, "chuva": chuva}
    return lookup


def compute_status(concentration, warning_threshold, danger_threshold):
    if danger_threshold and danger_threshold > 0 and concentration >= danger_threshold:
        return "Perigo"
    if warning_threshold and warning_threshold > 0 and concentration >= warning_threshold:
        return "Atencao"
    return "Normal"


def get_dashboard_data(permitted_site_names=None, translations=None):
    """Por fazenda permitida, retorna a ultima leitura de cada doenca com
    status, umidade e chuva do dia daquela leitura."""
    spore_rows = read_spore_counts()
    weather_rows = read_weather()
    weather_lookup = build_weather_lookup(weather_rows)

    latest = {}
    for row in spore_rows:
        site = row.get("siteName")
        if permitted_site_names is not None and site not in permitted_site_names:
            continue
        disease = row.get("displayName")
        try:
            dt = _parse_dt(row["samplingStartTime"])
        except (KeyError, ValueError):
            continue
        key = (site, disease)
        if key not in latest or dt > latest[key]["_dt"]:
            latest[key] = {"_dt": dt, "row": row}

    cards_by_site = {}
    for (site, disease), info in latest.items():
        row = info["row"]
        dt = info["_dt"]
        conc = _to_float(row.get("concentration"))
        warn = _to_float(row.get("warningConcentrationThreshold"))
        danger = _to_float(row.get("dangerConcentrationThreshold"))
        status = compute_status(conc or 0, warn, danger)
        w = weather_lookup.get((row.get("deviceUserFriendlyId"), dt.date().isoformat()))
        card = {
            "doenca": get_doenca(disease, translations),
            "doenca_en": disease,
            "cientifico": row.get("scientificName"),
            "concentracao": round(conc, 1) if conc is not None else None,
            "status": status,
            "data": dt.date().isoformat(),
            "umidade": w["umidade"] if w else None,
            "chuva": w["chuva"] if w else None,
            "warn": warn,
            "danger": danger,
        }
        cards_by_site.setdefault(site, []).append(card)

    for site in cards_by_site:
        cards_by_site[site].sort(key=lambda c: c["doenca"])

    return cards_by_site
