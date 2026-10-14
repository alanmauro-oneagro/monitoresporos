"""Dados MEDIDOS hora a hora das estacoes oficiais de cada pais -- pedido
do usuario (14/10/2026): "cada pais usa as suas estacoes para leituras".
Diferente dos modulos `*_stations.py` (so' catalogo/localizacao), aqui
vem o valor lido pela estacao. A PREVISAO continua vindo da Open-Meteo
(`weather_forecast.py`): nenhum orgao nacional publica previsao horaria
legivel por maquina (ver conversa de 14/10/2026).

Paises integrados: Brasil (INMET), Chile (DMC) e Argentina (SMN).

Brasil (INMET):
- Com `INMET_TOKEN` (pedido por e-mail a cadastro.act@inmet.gov.br): rota
  `/token/estacao/{inicio}/{fim}/{codigo}/{token}` da apitempo.
- Sem token o Brasil fica na Open-Meteo: a rota antiga sem token
  (`/estacao/{inicio}/{fim}/{codigo}`) respondeu VAZIA em producao
  (14/10/2026).
- Resposta: lista de registros horarios com DT_MEDICAO ("YYYY-MM-DD"),
  HR_MEDICAO ("HHMM", UTC), TEM_INS (C), UMD_INS (%), CHUVA (mm),
  VEN_DIR (graus) -- nomes de campo conferidos no cliente inmetpy.

Chile (DMC): `getDatosRecientesEma/{codigo}/{ano}/{mes}` (usuario+token
em METEOCHILE_USUARIO/METEOCHILE_TOKEN, os mesmos do catalogo) -- leituras
de 15 em 15 min do mes em `datosEstaciones.datos`, cada uma com `momento`
("YYYY-MM-DD HH:MM:SS", no fuso de `timezone`, UTC), `temperatura`
("8.0 °C" -- texto com unidade), `humedadRelativa`, `aguaCaidaDelMinuto`
e direcao do vento. Agregado por hora (media; chuva somada). Formato
conferido no cliente publico sebastianmibanez/clima-chile (dmc.mjs) e na
documentacao da DMC.

Argentina (SMN): arquivo aberto por dia, sem token,
`descarga_opendata.php?file=observaciones/datohorario{AAAAMMDD}.txt` --
todas as estacoes, uma linha por hora: FECHA (DDMMAAAA), HORA (hora
oficial argentina, UTC-3), TEMP, HUM, PNM, DD (graus), FF, NOMBRE. Sem
chuva. Casado com o catalogo (`smn_stations`) pelo nome. A previsao do
SMN (`zipopendata.php?dato=pron5d`, 5 dias de 3 em 3 horas por
localidade: temperatura, vento e chuva, SEM umidade) entra por
`previsao_smn`. Formatos conferidos no cliente publico fmartin92/smn-api
e num script publico de leitura do datohorario.

`diagnostico()` busca amostras reais em producao (o ambiente de
desenvolvimento nao alcanca esses servidores) pra' conferir os formatos."""
import io
import json
import os
import re
import time
import unicodedata
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timedelta

INMET_URL = "https://apitempo.inmet.gov.br/token/estacao/{inicio}/{fim}/{codigo}/{token}"
# Rota antiga, sem token -- testada em producao em 14/10/2026: responde 200
# VAZIO (0 bytes). So' fica no diagnostico, pra' perceber se um dia voltar.
INMET_URL_PUBLICA = "https://apitempo.inmet.gov.br/estacao/{inicio}/{fim}/{codigo}"
_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

# Fuso por UF (sem horario de verao desde 2019) -- a API do INMET devolve
# a hora em UTC, e o risco de infeccao e' calculado por dia LOCAL.
_UF_UTC_MENOS_4 = {"AM", "RO", "RR", "MT", "MS"}
_UF_UTC_MENOS_5 = {"AC"}


def _offset_uf(uf):
    if uf in _UF_UTC_MENOS_5:
        return -5
    if uf in _UF_UTC_MENOS_4:
        return -4
    return -3


def _float(valor):
    if valor is None or valor == "":
        return None
    try:
        return float(str(valor).replace(",", "."))
    except ValueError:
        return None


def configurado(country_code):
    if country_code == "BR":
        return bool(os.environ.get("INMET_TOKEN"))
    if country_code == "CL":
        return bool(os.environ.get("METEOCHILE_USUARIO") and os.environ.get("METEOCHILE_TOKEN"))
    return country_code == "AR"


def _abrir(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _inmet_horas(codigo, uf, inicio, fim):
    url = INMET_URL.format(inicio=inicio, fim=fim, codigo=codigo, token=os.environ["INMET_TOKEN"])
    registros = json.loads(_abrir(url) or b"[]")
    offset = timedelta(hours=_offset_uf(uf))
    horas = {}
    for r in registros:
        try:
            dt_utc = datetime.strptime(f"{r['DT_MEDICAO']} {str(r['HR_MEDICAO']).zfill(4)}", "%Y-%m-%d %H%M")
        except (KeyError, TypeError, ValueError):
            continue
        temp = _float(r.get("TEM_INS"))
        if temp is None:
            continue
        horas[(dt_utc + offset).strftime("%Y-%m-%dT%H:00")] = [
            temp, _float(r.get("UMD_INS")), _float(r.get("CHUVA")), _float(r.get("VEN_DIR")),
        ]
    return horas


DMC_URL = "https://climatologia.meteochile.gob.cl/application/servicios/getDatosRecientesEma/{codigo}/{ano}/{mes:02d}?{cred}"


def _numero(valor):
    """Primeiro numero de um valor da DMC ("8.0 °C", "93 %", 12.5, None)."""
    if valor is None:
        return None
    m = re.search(r"-?\d+(?:[.,]\d+)?", str(valor))
    return float(m.group(0).replace(",", ".")) if m else None


def _campo(leitura, *trechos):
    """Valor do primeiro campo cujo nome contem todos os `trechos`
    (ignorando maiusculas) -- a DMC muda a capitalizacao de campo de
    servico pra servico (ver nota em dmc_stations.py)."""
    for chave, valor in leitura.items():
        nome = chave.lower()
        if all(t in nome for t in trechos):
            return valor
    return None


def _dmc_horas(codigo, inicio, fim):
    cred = urllib.parse.urlencode({"usuario": os.environ["METEOCHILE_USUARIO"], "token": os.environ["METEOCHILE_TOKEN"]})
    ini, fim_d = datetime.fromisoformat(inicio), datetime.fromisoformat(fim)
    meses = []
    ano, mes = ini.year, ini.month
    while (ano, mes) <= (fim_d.year, fim_d.month):
        meses.append((ano, mes))
        ano, mes = (ano + 1, 1) if mes == 12 else (ano, mes + 1)
    por_hora = {}
    sem_dado = None
    for ano, mes in meses:
        corpo = _abrir(DMC_URL.format(codigo=codigo, ano=ano, mes=mes, cred=cred), timeout=60) or b"{}"
        try:
            bruto = json.loads(corpo)
        except ValueError:
            # A DMC responde texto puro ("Sin Información") quando a estacao
            # nao tem dado nesse servico -- visto em producao em 14/10/2026.
            sem_dado = f"DMC estacao {codigo} {mes:02d}/{ano}: {corpo.decode('utf-8', errors='replace').strip()[:80]}"
            continue
        if bruto.get("mensaje") and not (bruto.get("datosEstaciones") or {}).get("datos"):
            raise RuntimeError(f"DMC: {bruto['mensaje']}")
        utc = re.search(r"utc|gmt", str(bruto.get("timezone", "UTC")), re.I)
        for leitura in (bruto.get("datosEstaciones") or {}).get("datos") or []:
            m = re.match(r"(\d{4}-\d{2}-\d{2})[ T](\d{2})", str(leitura.get("momento", "")))
            if not m:
                continue
            dt = datetime.fromisoformat(f"{m.group(1)}T{m.group(2)}:00")
            if utc:
                # Mesma aproximacao de fuso do Chile ja usada no BioScout
                # (verao set-abr UTC-3, inverno UTC-4).
                dt += timedelta(hours=-3 if dt.month in (9, 10, 11, 12, 1, 2, 3, 4) else -4)
            hora = dt.strftime("%Y-%m-%dT%H:00")
            if not (inicio <= hora[:10] <= fim):
                continue
            temp = _numero(leitura.get("temperatura"))
            if temp is None:
                continue
            item = por_hora.setdefault(hora, {"temp": [], "umidade": [], "chuva": [], "vento": None})
            item["temp"].append(temp)
            umidade = _numero(leitura.get("humedadRelativa") if "humedadRelativa" in leitura else _campo(leitura, "humedad"))
            if umidade is not None:
                item["umidade"].append(umidade)
            chuva = _numero(_campo(leitura, "agua", "minuto"))
            if chuva is not None:
                item["chuva"].append(chuva)
            vento = _numero(_campo(leitura, "direcc", "viento"))
            if vento is not None:
                item["vento"] = vento
    if not por_hora and sem_dado:
        raise RuntimeError(sem_dado)
    return {
        hora: [
            round(sum(v["temp"]) / len(v["temp"]), 1),
            round(sum(v["umidade"]) / len(v["umidade"])) if v["umidade"] else None,
            round(sum(v["chuva"]), 1) if v["chuva"] else None,
            v["vento"],
        ]
        for hora, v in por_hora.items()
    }


SMN_DATOHORARIO_URL = "https://ssl.smn.gob.ar/dpd/descarga_opendata.php?file=observaciones/datohorario{data}.txt"
SMN_PRON5D_URL = "https://ssl.smn.gob.ar/dpd/zipopendata.php?dato=pron5d"
_smn_arquivos = {}  # url -> (timestamp, conteudo) -- um arquivo serve todas as estacoes da Argentina
_MESES_SMN = ["ENE", "FEB", "MAR", "ABR", "MAY", "JUN", "JUL", "AGO", "SEP", "OCT", "NOV", "DIC"]


def _normalizar_nome(nome):
    sem_acento = unicodedata.normalize("NFKD", nome or "").encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", sem_acento.upper()).strip()


def _texto_smn(url):
    """Conteudo (texto latin-1) de um arquivo do SMN -- ZIP ou texto puro
    -- com cache de 6h em memoria (o mesmo arquivo do dia serve pra todas
    as estacoes argentinas da rodada)."""
    agora = time.time()
    cached = _smn_arquivos.get(url)
    if cached and agora - cached[0] < 6 * 3600:
        return cached[1]
    corpo = _abrir(url, timeout=60)
    if corpo[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(corpo)) as z:
            corpo = z.read(z.namelist()[0])
    texto = corpo.decode("latin-1")
    if len(_smn_arquivos) > 120:
        _smn_arquivos.clear()
    _smn_arquivos[url] = (agora, texto)
    return texto


def _nome_smn(codigo):
    import smn_stations
    estacao = next((e for e in smn_stations.get_estacoes() if e["codigo"] == codigo), None)
    if not estacao:
        raise RuntimeError(f"SMN: estacao {codigo} fora do catalogo")
    return _normalizar_nome(estacao["cidade"])


def _smn_horas(codigo, inicio, fim):
    nome = _nome_smn(codigo)
    dia, ultimo = datetime.fromisoformat(inicio).date(), datetime.fromisoformat(fim).date()
    horas, falhas, dias = {}, 0, 0
    while dia <= ultimo:
        dias += 1
        try:
            texto = _texto_smn(SMN_DATOHORARIO_URL.format(data=dia.strftime("%Y%m%d")))
        except Exception:
            falhas += 1  # arquivo do dia ainda nao publicado / fora do ar
            dia += timedelta(days=1)
            continue
        for linha in texto.splitlines():
            partes = linha.split()
            if len(partes) < 8 or not re.fullmatch(r"\d{8}", partes[0]):
                continue
            if _normalizar_nome(" ".join(partes[7:])) != nome:
                continue
            try:
                dt = datetime.strptime(partes[0], "%d%m%Y") + timedelta(hours=int(partes[1]))
                temp = float(partes[2])
            except ValueError:
                continue
            horas[dt.strftime("%Y-%m-%dT%H:00")] = [temp, _float(partes[3]), None, _float(partes[5])]
        dia += timedelta(days=1)
    if dias and falhas == dias:
        raise RuntimeError("SMN: nenhum arquivo datohorario disponivel no periodo")
    return horas


def previsao_smn(codigo):
    """Previsao do SMN (pron5d) da localidade com o mesmo nome da estacao:
    {"YYYY-MM-DDTHH:00": [temp, chuva_mm]} de 3 em 3 horas (hora oficial
    argentina) -- {} se a localidade nao estiver no arquivo. Lanca
    excecao em falha de rede."""
    nome = _nome_smn(codigo)
    previsao, atual = {}, None
    for linha in _texto_smn(SMN_PRON5D_URL).splitlines():
        partes = linha.split()
        if not partes:
            continue
        m = re.fullmatch(r"(\d{2})/([A-Z]{3})/(\d{4})", partes[0])
        if not m:
            # Nome da localidade = linha de texto solta antes do bloco;
            # cabecalhos de coluna tem "FECHA", "=", "(", "|" etc.
            if not re.search(r"FECHA|HORA|[=\[(|:]|-{3}", linha):
                atual = _normalizar_nome(linha)
            continue
        if atual != nome or len(partes) < 4 or m.group(2) not in _MESES_SMN:
            continue
        try:
            dt = datetime(int(m.group(3)), _MESES_SMN.index(m.group(2)) + 1, int(m.group(1)), int(partes[1][:2]))
            previsao[dt.strftime("%Y-%m-%dT%H:00")] = [float(partes[2]), float(partes[-1])]
        except ValueError:
            continue
    return previsao


def buscar_horas(country_code, codigo, uf, inicio, fim):
    """{"YYYY-MM-DDTHH:00" (hora local da estacao): [temp, umidade, chuva,
    vento]} entre `inicio` e `fim` ("YYYY-MM-DD"). Lanca excecao em
    falha de rede/formato (quem chama trata e faz backoff)."""
    if country_code == "BR":
        return _inmet_horas(codigo, uf, inicio, fim)
    if country_code == "CL":
        return _dmc_horas(codigo, inicio, fim)
    if country_code == "AR":
        return _smn_horas(codigo, inicio, fim)
    raise ValueError(f"pais sem integracao de dados de estacao: {country_code}")


def _ocultar_credenciais(texto):
    for nome in ("INMET_TOKEN", "METEOCHILE_TOKEN", "METEOCHILE_USUARIO"):
        valor = os.environ.get(nome)
        if valor:
            texto = texto.replace(valor, f"<{nome}>").replace(urllib.parse.quote(valor), f"<{nome}>")
    return texto


def _amostra(titulo, url, limite=3000):
    """Busca `url` e devolve {"titulo", "url", "status", "conteudo"} com o
    comeco da resposta (ZIP: lista os arquivos e o comeco do primeiro).
    Credenciais nunca aparecem no resultado."""
    item = {"titulo": titulo, "url": _ocultar_credenciais(url)}
    try:
        corpo = _abrir(url, timeout=30)
    except Exception as exc:
        item["status"] = "erro"
        item["conteudo"] = _ocultar_credenciais(str(exc))[:500]
        return item
    item["status"] = f"ok ({len(corpo)} bytes)"
    if corpo[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(corpo)) as z:
            nomes = z.namelist()
            texto = z.read(nomes[0]).decode("latin-1", errors="replace") if nomes else ""
        item["conteudo"] = f"ZIP: {nomes}\n\n{texto[:limite]}"
    else:
        item["conteudo"] = corpo.decode("utf-8", errors="replace")[:limite]
    item["conteudo"] = _ocultar_credenciais(item["conteudo"])
    return item


def diagnostico(estacoes_em_uso):
    """Amostras reais das APIs de dados de estacao (so' leitura) -- pra
    fechar o formato do Chile e da Argentina antes de integrar, ja que o
    ambiente de desenvolvimento nao alcanca esses servidores.
    `estacoes_em_uso`: {country_code: codigo} de uma estacao de referencia
    real por pais (a primeira escolhida em alguma fazenda)."""
    hoje = datetime.now().date()
    ontem = (hoje - timedelta(days=1)).isoformat()
    amostras = []

    codigo_br = estacoes_em_uso.get("BR", "A901")
    amostras.append(_amostra(
        "INMET -- dados horarios, rota publica (sem token)",
        INMET_URL_PUBLICA.format(inicio=ontem, fim=hoje.isoformat(), codigo=codigo_br),
    ))
    if os.environ.get("INMET_TOKEN"):
        amostras.append(_amostra(
            "INMET -- dados horarios (com token)",
            INMET_URL.format(inicio=ontem, fim=hoje.isoformat(), codigo=codigo_br, token=os.environ["INMET_TOKEN"]),
        ))

    usuario, token = os.environ.get("METEOCHILE_USUARIO"), os.environ.get("METEOCHILE_TOKEN")
    if usuario and token:
        cred = urllib.parse.urlencode({"usuario": usuario, "token": token})
        dmc_recentes = "https://climatologia.meteochile.gob.cl/application/servicios/getDatosRecientesEma/{codigo}?{cred}"
        codigo_cl = estacoes_em_uso.get("CL")
        if codigo_cl:
            amostras.append(_amostra(f"DMC -- estacao em uso {codigo_cl}, mes atual", DMC_URL.format(codigo=codigo_cl, ano=hoje.year, mes=hoje.month, cred=cred)))
            amostras.append(_amostra(f"DMC -- estacao em uso {codigo_cl}, ultimas 12h", dmc_recentes.format(codigo=codigo_cl, cred=cred)))
        # Quinta Normal (Santiago) -- estacao automatica de referencia da
        # propria documentacao da DMC: se ESTA responder e a de cima nao,
        # o problema e' a estacao escolhida (nao automatica), nao o servico.
        amostras.append(_amostra("DMC -- Quinta Normal 330020, mes atual", DMC_URL.format(codigo="330020", ano=hoje.year, mes=hoje.month, cred=cred)))
        amostras.append(_amostra("DMC -- Quinta Normal 330020, ultimas 12h", dmc_recentes.format(codigo="330020", cred=cred)))
    else:
        amostras.append({"titulo": "DMC (Chile)", "url": "-", "status": "sem METEOCHILE_USUARIO/METEOCHILE_TOKEN", "conteudo": ""})

    amostras.append(_amostra("SMN -- datohorario de ontem", SMN_DATOHORARIO_URL.format(data=(hoje - timedelta(days=1)).strftime("%Y%m%d"))))
    amostras.append(_amostra("SMN -- previsao pron5d", SMN_PRON5D_URL))
    return amostras
