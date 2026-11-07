"""Cotacao do dolar pra planos com valor em U$ (pedido do Alan, 07/11/2026).

O Asaas so' cobra em reais: plano em dolar e' convertido na hora de
assinar pela PTAX de venda do Banco Central (ultima publicada). Guarda o
valor por `VALIDADE_S` em memoria e o ultimo valor obtido no setting
`cotacao_dolar` -- com o Banco Central fora do ar, vale o ultimo conhecido.
"""
import json
import time
import urllib.request
from datetime import date, timedelta

import models

_URL = ("https://olinda.bcb.gov.br/olinda/servico/PTAX/versao/v1/odata/"
        "CotacaoDolarPeriodo(dataInicial=@dataInicial,dataFinalCotacao=@dataFinalCotacao)"
        "?@dataInicial='{inicio}'&@dataFinalCotacao='{fim}'&$format=json&$select=cotacaoVenda,dataHoraCotacao")
_SETTING = "cotacao_dolar"
VALIDADE_S = 6 * 3600
_cache = {"em": 0.0, "valor": None}


def _buscar():
    """(valor, 'AAAA-MM-DD') da PTAX mais recente dos ultimos 10 dias."""
    d_fim = date.fromisoformat(models._hoje_cuiaba())
    d_ini = d_fim - timedelta(days=10)
    url = _URL.format(inicio=d_ini.strftime("%m-%d-%Y"), fim=d_fim.strftime("%m-%d-%Y"))
    with urllib.request.urlopen(url, timeout=15) as resp:
        linhas = json.load(resp).get("value") or []
    if not linhas:
        return None
    ultima = max(linhas, key=lambda l: l["dataHoraCotacao"])
    return float(ultima["cotacaoVenda"]), ultima["dataHoraCotacao"][:10]


def dolar():
    """{"valor": reais por dolar, "data": 'AAAA-MM-DD'} ou None se nunca
    deu pra obter."""
    if _cache["valor"] and time.time() - _cache["em"] < VALIDADE_S:
        return _cache["valor"]
    try:
        achado = _buscar()
    except Exception:
        achado = None
    if achado:
        valor = {"valor": achado[0], "data": achado[1]}
        models.set_setting(_SETTING, json.dumps(valor))
    else:
        try:
            valor = json.loads(models.get_setting(_SETTING) or "null")
        except ValueError:
            valor = None
    # falhou: tenta de novo em 10 min (nao espera as 6 h da validade)
    _cache.update(em=time.time() if achado else time.time() - VALIDADE_S + 600, valor=valor)
    return valor


def em_reais(valor_usd):
    """Valor em R$ (2 casas) de um valor em U$, ou None sem cotacao."""
    cot = dolar()
    return round(float(valor_usd) * cot["valor"], 2) if cot else None
