"""Dados MEDIDOS hora a hora das estacoes oficiais de cada pais -- pedido
do usuario (14/10/2026): "cada pais usa as suas estacoes para leituras".
Diferente dos modulos `*_stations.py` (so' catalogo/localizacao), aqui
vem o valor lido pela estacao. A PREVISAO continua vindo da Open-Meteo
(`weather_forecast.py`): nenhum orgao nacional publica previsao horaria
legivel por maquina (ver conversa de 14/10/2026).

Hoje so' o Brasil (INMET) esta integrado:
- Rota `/token/estacao/{inicio}/{fim}/{codigo}/{token}` da apitempo --
  exige token (pedido por e-mail a cadastro.act@inmet.gov.br), lido de
  `INMET_TOKEN`. Sem token a rota devolve 404 (por isso o comentario
  antigo em inmet_stations.py dizia que a API "parou de funcionar").
- Resposta: lista de registros horarios com DT_MEDICAO ("YYYY-MM-DD"),
  HR_MEDICAO ("HHMM", UTC), TEM_INS (C), UMD_INS (%), CHUVA (mm),
  VEN_DIR (graus) -- nomes de campo conferidos no cliente inmetpy.

Chile (DMC) e Argentina (SMN): ainda sem formato confirmado -- ver
`diagnostico()`, que busca amostras reais em producao pra' fechar o
formato antes de integrar."""
import io
import json
import os
import re
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timedelta

INMET_URL = "https://apitempo.inmet.gov.br/token/estacao/{inicio}/{fim}/{codigo}/{token}"
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
    return country_code == "BR" and bool(os.environ.get("INMET_TOKEN"))


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


def buscar_horas(country_code, codigo, uf, inicio, fim):
    """{"YYYY-MM-DDTHH:00" (hora local da estacao): [temp, umidade, chuva,
    vento]} entre `inicio` e `fim` ("YYYY-MM-DD"). Lanca excecao em
    falha de rede/formato (quem chama trata e faz backoff)."""
    if country_code == "BR":
        return _inmet_horas(codigo, uf, inicio, fim)
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

    if os.environ.get("INMET_TOKEN") and estacoes_em_uso.get("BR"):
        amostras.append(_amostra(
            "INMET -- dados horarios (com token)",
            INMET_URL.format(inicio=ontem, fim=hoje.isoformat(), codigo=estacoes_em_uso["BR"], token=os.environ["INMET_TOKEN"]),
        ))
    else:
        amostras.append({"titulo": "INMET -- dados horarios", "url": "-", "status": "sem INMET_TOKEN ou sem estacao BR em uso", "conteudo": ""})

    usuario, token = os.environ.get("METEOCHILE_USUARIO"), os.environ.get("METEOCHILE_TOKEN")
    if usuario and token:
        cred = urllib.parse.urlencode({"usuario": usuario, "token": token})
        base = "https://climatologia.meteochile.gob.cl/application"
        codigo_cl = estacoes_em_uso.get("CL", "330020")
        amostras.append(_amostra("DMC -- dados recentes de uma estacao", f"{base}/servicios/getDatosRecientesEma/{codigo_cl}?{cred}"))
        # Indice da documentacao: lista os servicos disponiveis (endereco +
        # descricao) -- precisamos de um que traga o dia ANTERIOR inteiro.
        for n in range(1, 13):
            item = _amostra(f"DMC -- documentacao {n} (enderecos de servico)", f"{base}/documentacao/getDocumento/{n}", limite=200000)
            if item["status"].startswith("ok"):
                servicos = sorted(set(re.findall(r"application/servicios/[A-Za-z]+[^\"'<\s]*", item["conteudo"])))
                titulo = re.search(r"<title>(.*?)</title>", item["conteudo"], re.S)
                item["conteudo"] = (titulo.group(1).strip() if titulo else "") + "\n" + "\n".join(servicos)
            amostras.append(item)
    else:
        amostras.append({"titulo": "DMC (Chile)", "url": "-", "status": "sem METEOCHILE_USUARIO/METEOCHILE_TOKEN", "conteudo": ""})

    smn = "https://ssl.smn.gob.ar/dpd/zipopendata.php?dato="
    for dato in ("datohorario", "pron5d", "tiepre"):
        amostras.append(_amostra(f"SMN -- arquivo aberto '{dato}'", smn + dato))
    return amostras
