"""Sinal de celular ESTIMADO numa coordenada, a partir da lista oficial de
antenas (ERBs) licenciadas da Anatel (pedido do Alan, 31/10/2026).

O Master envia o arquivo da Anatel (CSV, .zip ou .gz) pelo Mapa
Interpolado; `importar` le linha a linha (o arquivo e' grande), guarda so'
operadora/tecnologia/faixa/coordenada na tabela `erbs` e descarta o resto.
`estimar` acha, por operadora, a antena que da' o melhor sinal provavel
pela distancia e pela faixa de frequencia (faixa baixa alcanca mais longe).
E' estimativa: relevo, mata, altura e direcao da antena mudam o sinal real.
"""
import csv
import gzip
import io
import math
import re
import unicodedata
import zipfile

import models

# Nome da entidade na Anatel -> marca conhecida (o resto fica com o nome
# da propria entidade).
_OPERADORAS = [
    ("TELEFONICA", "Vivo"), ("VIVO", "Vivo"), ("CLARO", "Claro"), ("TIM ", "TIM"), ("TIM S", "TIM"),
    ("OI ", "Oi"), ("OI S", "Oi"), ("TELEMAR", "Oi"), ("ALGAR", "Algar"), ("BRISANET", "Brisanet"),
    ("SERCOMTEL", "Sercomtel"), ("UNIFIQUE", "Unifique"), ("WINITY", "Winity"),
]
_TECNOLOGIAS = [("NR", "5G"), ("5G", "5G"), ("LTE", "4G"), ("4G", "4G"), ("WCDMA", "3G"), ("UMTS", "3G"),
                ("3G", "3G"), ("GSM", "2G"), ("2G", "2G")]
# (limite superior em MHz, faixa exibida)
_FAIXAS = [(800, 700), (890, 850), (960, 900), (1990, 1800), (2300, 2100), (2700, 2600), (4000, 3500)]
# Distancia maxima (km) pra cada nivel, por grupo de faixa.
_ALCANCE = {
    "baixa": [("bom", 8), ("regular", 15), ("fraco", 30)],   # 700-900 MHz
    "media": [("bom", 3), ("regular", 7), ("fraco", 12)],    # 1800-2100 MHz
    "alta": [("bom", 1.5), ("regular", 3), ("fraco", 5)],    # 2600-3500 MHz
}
NIVEIS = ["bom", "regular", "fraco"]
RAIO_BUSCA_KM = 30

_DMS = re.compile(r"^(\d{1,3})([NSEWOL])(\d{2})(\d{2})(\d*)$")


def _sem_acento(texto):
    return unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode().upper().strip()


def _operadora(nome):
    n = _sem_acento(nome) + " "
    for chave, marca in _OPERADORAS:
        if n.startswith(chave) or f" {chave}" in f" {n}":
            return marca
    return (nome or "").strip().title() or None


def _tecnologia(valor):
    v = _sem_acento(valor)
    for chave, rotulo in _TECNOLOGIAS:
        if chave in v:
            return rotulo
    return None


def _faixa(mhz):
    for limite, faixa in _FAIXAS:
        if mhz < limite:
            return faixa
    return None


def _grupo(faixa):
    return "baixa" if faixa <= 900 else ("media" if faixa <= 2100 else "alta")


def _numero(texto):
    try:
        return float(str(texto).strip().replace(",", "."))
    except ValueError:
        return None


def _coordenada(texto):
    """Decimal ("-12,345" / "-12.345") ou o formato da Anatel em graus,
    minutos e segundos sem separador ("12S3456" = 12 graus 34' 56" Sul)."""
    t = _sem_acento(texto).replace(" ", "")
    m = _DMS.match(t)
    if m:
        graus, hemis, minutos, segundos, frac = m.groups()
        seg = float(f"{segundos}.{frac}") if frac else float(segundos)
        valor = int(graus) + int(minutos) / 60 + seg / 3600
        return -valor if hemis in ("S", "W", "O") else valor
    return _numero(t)


def _colunas(cabecalho):
    """Indice de cada campo, achado pelo nome (sem acento/maiusculas)."""
    nomes = [_sem_acento(c).replace(" ", "").replace("_", "") for c in cabecalho]

    def achar(*opcoes):
        for opcao in opcoes:
            for i, n in enumerate(nomes):
                if n == opcao:
                    return i
        for opcao in opcoes:
            for i, n in enumerate(nomes):
                if opcao in n:
                    return i
        return None

    return {
        "entidade": achar("NOMEENTIDADE", "ENTIDADE", "PRESTADORA", "OPERADORA"),
        "tecnologia": achar("TECNOLOGIA", "TIPOTECNOLOGIA"),
        "freq": achar("FREQTXMHZ", "FREQTX", "FREQUENCIA", "FAIXA"),
        "lat": achar("LATITUDE", "LAT"),
        "lon": achar("LONGITUDE", "LON"),
    }


def _abrir_texto(caminho):
    """Abre .zip (primeiro .csv dentro), .gz ou .csv como texto, em fluxo
    (o arquivo da Anatel tem centenas de MB -- nunca le tudo na memoria)."""
    with open(caminho, "rb") as f:
        inicio = f.read(2)
    if inicio == b"PK":
        z = zipfile.ZipFile(caminho)
        nome = next((n for n in z.namelist() if n.lower().endswith(".csv")), z.namelist()[0])
        bruto = z.open(nome)
    elif inicio == b"\x1f\x8b":
        bruto = gzip.open(caminho, "rb")
    else:
        bruto = open(caminho, "rb")
    amostra = bruto.peek(65536)[:65536] if hasattr(bruto, "peek") else b""
    try:
        amostra.decode("utf-8")
        codificacao = "utf-8-sig"
    except UnicodeDecodeError:
        codificacao = "latin-1"
    return io.TextIOWrapper(bruto, encoding=codificacao, errors="replace", newline="")


def importar(caminho):
    """Le o arquivo da Anatel e substitui a base `erbs`. Devolve (antenas
    gravadas, linhas lidas) ou levanta ValueError se faltar coluna."""
    with _abrir_texto(caminho) as texto:
        primeira = texto.readline()
        if not primeira.strip():
            raise ValueError("arquivo vazio")
        separador = ";" if primeira.count(";") >= primeira.count(",") else ","
        col = _colunas(next(csv.reader([primeira], delimiter=separador)))
        faltando = [k for k, v in col.items() if v is None]
        if faltando:
            raise ValueError("coluna(s) nao encontrada(s): " + ", ".join(faltando))
        antenas, lidas = set(), 0
        for linha in csv.reader(texto, delimiter=separador):
            lidas += 1
            try:
                operadora = _operadora(linha[col["entidade"]])
                tecnologia = _tecnologia(linha[col["tecnologia"]])
                mhz = _numero(linha[col["freq"]])
                lat, lon = _coordenada(linha[col["lat"]]), _coordenada(linha[col["lon"]])
            except IndexError:
                continue
            faixa = _faixa(mhz) if mhz else None
            if not (operadora and tecnologia and faixa and lat is not None and lon is not None):
                continue
            if not (-35 <= lat <= 6 and -75 <= lon <= -28):  # fora do Brasil = coordenada mal lida
                continue
            antenas.add((operadora, tecnologia, faixa, round(lat, 5), round(lon, 5)))
    models.substituir_erbs(antenas)
    return len(antenas), lidas


def _distancia_km(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(a))


def _nivel(faixa, km):
    for nivel, limite in _ALCANCE[_grupo(faixa)]:
        if km <= limite:
            return nivel
    return None


def estimar(lat, lon):
    """Por operadora (melhor primeiro): nivel provavel e a antena que da'
    esse nivel. [{"operadora", "nivel", "tecnologia", "faixa", "km"}].
    Operadora sem antena util no raio fica de fora. None se a base de
    antenas ainda nao foi carregada."""
    if not models.erbs_carregadas():
        return None
    dlat = RAIO_BUSCA_KM / 111
    dlon = RAIO_BUSCA_KM / (111 * max(math.cos(math.radians(lat)), 0.2))
    melhor = {}
    for e in models.get_erbs_na_caixa(lat - dlat, lat + dlat, lon - dlon, lon + dlon):
        km = _distancia_km(lat, lon, e["lat"], e["lon"])
        nivel = _nivel(e["faixa"], km)
        if not nivel:
            continue
        chave = (NIVEIS.index(nivel), km)
        atual = melhor.get(e["operadora"])
        if atual is None or chave < atual[0]:
            melhor[e["operadora"]] = (chave, {
                "operadora": e["operadora"], "nivel": nivel, "tecnologia": e["tecnologia"],
                "faixa": e["faixa"], "km": round(km, 1),
            })
    return [d for _c, d in sorted(melhor.values(), key=lambda x: x[0])]
