"""BioScout Web -- painel movel com login e permissoes por fazenda.

Le os mesmos CSVs que o Fetch-BioScoutData.ps1 mantem -- o botao "Atualizar
dados" do painel dispara esse script em segundo plano (busca na API do
BioScout, que e' lenta -- varios minutos por causa da latencia da propria
API do BioScout, chamada site a site). O clique NAO espera o fim da busca
(evita matar o processo no meio e corromper os CSVs, algo que ja aconteceu
antes nesse projeto); a pagina volta na hora avisando que a busca comecou,
e os dados novos aparecem quando a pessoa atualizar a pagina de novo.
"""
import concurrent.futures
import functools
import gzip
import io
import json
import os
import re
import secrets
import sqlite3
import subprocess
import tempfile
import threading
import time
import unicodedata
import zipfile
from pathlib import Path

from flask import Flask, render_template, request, redirect, url_for, flash, abort, send_file, send_from_directory, jsonify, session, g, get_template_attribute
from flask_compress import Compress
from flask_login import (
    LoginManager, UserMixin, login_user, logout_user, login_required,
    current_user,
)
from werkzeug.security import check_password_hash, generate_password_hash

from datetime import datetime, timedelta, timezone

import models
import fungicida_data
import agrofit_dados
import adapar
import asaas
import data_reader
import bioscout_fetch
import export_excel
import export_pdf
import whatsapp
import weather_forecast
import inmet_stations
import virtual_farms
import sinal_celular
import ndvi_service
import countries
import dmc_stations
import estacoes_dados
import soil_service
import soilgrids_service
from data_reader import read_sites, get_dashboard_data

# Fuso horario do site: Cuiaba-MT (America/Cuiaba, UTC-4 o ano todo -- Brasil
# aboliu o horario de verao em 2019). Hospedado (Railway/Linux), o container
# roda em UTC por padrao, entao todo datetime.now() (rodape de relatorio,
# horario do envio agendado, timestamps do log de WhatsApp) apareceria 4h
# adiantado sem isso. os.environ + time.tzset() muda o fuso do PROCESSO
# inteiro (biblioteca C por baixo do datetime.now()) -- tzset() so existe em
# Linux/Mac, no Windows (dev local) fica sem efeito e usa o fuso do proprio
# Windows, que ja deve estar certo pra quem roda localmente no Brasil.
os.environ["TZ"] = "America/Cuiaba"
if hasattr(time, "tzset"):
    time.tzset()

# Fica America/Cuiaba pra TODAS as fazendas, mesmo as do Chile
# (America/Santiago) -- decisao consciente, nao esquecimento: a
# diferenca real e' so' ~0-1h (Chile tem horario de verao, o Brasil nao
# desde 2019), entao o impacto pratico e' pequeno. Afeta hora do envio
# agendado de WhatsApp (`models.get_whatsapp_send_hour`), timestamps de log e
# rodape de relatorio pra TODOS os paises igual -- nao (ainda) por
# fazenda/pais. Um ajuste fino por pais e' possivel depois se isso vier
# a importar de verdade.

WEEKDAY_LABELS = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sab", "Dom"]  # 0=Segunda ... 6=Domingo (Python date.weekday())
NDVI_AGENDAMENTO_COBERTURA_MAXIMA = 3  # % -- mais estrito que o preview manual (5%), pedido explicito do usuario pro envio automatico
INTERPOLACAO_SENTINEL = "__interp3__"  # valor especial de weather_station_overrides.estacao_codigo -- "media das 3 estacoes mais proximas" em vez de uma estacao so (ver _weather_coords_all/_get_weather_for_site)
WEATHER_CACHE_TTL_SECONDS = 30 * 60  # nao busca de novo na Open-Meteo antes disso, por fazenda
WEATHER_FALHA_BACKOFF_SECONDS = 5 * 60  # busca que falhou (timeout, 429...) so' tenta de novo depois disso -- senao cada pagina esperava o timeout de novo
NDVI_PREVIEW_TTL_SECONDS = 30 * 60  # pre-visualizacao de NDVI expira sozinha se ninguem confirmar/descartar
DADOS_AVISO_DIAS = 7  # ate isso = verde (ok); acima = aviso (amarelo)
DADOS_BLOQUEIO_DIAS = 15  # acima disso (16+ dias) = vermelho, WhatsApp/PDF/copia da recomendacao viram so' clima (sem doenca)
CULTURA_TODOS = "Todos"  # valor especial do seletor "Cultura atual" -- mostra toda doenca, sem filtrar pela matriz (ver _filter_cards_by_cultura)
SENHA_TEMPORARIA_VALIDADE_MINUTOS = 30  # "Esqueci a senha" -- ver models.set_user_temp_password/rota esqueci_senha
ESQUECI_SENHA_COOLDOWN_SEGUNDOS = 5 * 60  # nao reenvia WhatsApp pro mesmo usuario antes disso -- protege o numero dele de spam e o bridge de abuso
_esqueci_senha_ultima_solicitacao = {}  # {username.lower(): datetime} -- em memoria, mesmo padrao do _weather_cache


def _dias_sem_leitura(cards):
    """Dias desde a leitura mais recente entre TODOS os cartoes da fazenda
    (nao so os que estao em Atencao/Perigo) -- usado pra saber se a
    estacao fisica parou de mandar dado novo pra API do BioScout,
    independente do status de cada doenca no momento."""
    if not cards:
        return None
    ultima = max(datetime.strptime(c["data"], "%Y-%m-%d").date() for c in cards)
    return (datetime.now().date() - ultima).days


_BLOQUEIO_LIBERADO_KEY = "bloqueio_liberado"


def _bloqueios_liberados():
    """{site: data da ultima leitura quando foi reativada} -- fazenda sem
    leitura ha mais de DADOS_BLOQUEIO_DIAS que o Alan reativou na tela
    Fazendas ocultas. Vale so' enquanto nao chegar leitura mais nova que
    essa: com leitura nova a liberacao perde efeito (se ficar velha de
    novo, volta a bloquear). Cache por requisicao."""
    val = getattr(g, "_bloqueios_liberados", None)
    if val is None:
        try:
            val = json.loads(models.get_setting(_BLOQUEIO_LIBERADO_KEY) or "{}")
        except ValueError:
            val = {}
        g._bloqueios_liberados = val
    return val


def _nivel_dados_site(site, cards):
    """`_nivel_dados_defasados` da fazenda, respeitando a reativacao manual
    (ver `_bloqueios_liberados`): reativada vira so' 'atencao'."""
    nivel = _nivel_dados_defasados(_dias_sem_leitura(cards))
    if nivel == "bloqueado" and cards:
        liberada_ate = _bloqueios_liberados().get(site)
        if liberada_ate and max(c["data"] for c in cards) <= liberada_ate:
            return "atencao"
    return nivel


def _nivel_dados_defasados(dias_sem_leitura):
    """None (ok), 'atencao' (mostra aviso) ou 'bloqueado' (dado de doenca
    velho demais pra confiar -- WhatsApp/PDF/copia da recomendacao caem
    pro relatorio SO' de clima, ver `_send_site_whatsapp`/
    `recommendation_pdf`) de acordo com `dias_sem_leitura`."""
    if dias_sem_leitura is None:
        return None
    if dias_sem_leitura > DADOS_BLOQUEIO_DIAS:
        return "bloqueado"
    if dias_sem_leitura > DADOS_AVISO_DIAS:
        return "atencao"
    return None


def _coords_all():
    """Coordenadas de TODAS as fazendas, reais + virtuais/estimadas (ver
    virtual_farms.py) -- usado sempre que precisar plotar no mapa ou
    achar a estacao INMET/clima mais perto de um "site" qualquer,
    independente dele ser uma estacao de verdade ou um ponto estimado."""
    coords = dict(data_reader.read_site_coordinates())
    for vf in models.get_all_virtual_farms():
        coords[vf["site_name"]] = (vf["lat"], vf["lon"])
    return coords


def _sites_ativos_para_admin():
    """Fazendas (reais do CSV + virtuais) que o admin deve enxergar,
    excluindo as desativadas (`models.get_deactivated_site_names`) --
    substitui o sentinel `permitted=None` nos lugares que hoje mostram
    "tudo" pro admin (Painel, Mapa, Graficos, Recomendacoes, NDVI,
    Envios, Exportar). `fazendas()` (Manejos) e' a UNICA excecao de
    proposito -- continua mostrando fazenda desativada tambem, pra' dar
    pra' reativar."""
    return (set(read_sites()) | models.virtual_farm_site_names()) - models.get_deactivated_site_names()


def _cards_by_site_all(permitted, translations):
    """cards_by_site real (`get_dashboard_data`) + uma entrada por
    fazenda virtual/estimada, calculada na hora por interpolacao (IDW)
    das fazendas reais dentro do raio dela -- pra fazenda virtual se
    comportar como uma fazenda de verdade em qualquer tela (Painel,
    Mapa, Recomendacoes). `permitted=None` (admin) ve tudo; senao so o
    que estiver na lista. A interpolacao sempre usa a base de fazendas
    reais INTEIRA como entrada (nao so as permitidas), pra nao dar
    estimativa incompleta so porque o usuario nao tem acesso a alguma
    fazenda vizinha -- so o resultado final (o numero estimado) e'
    exposto, nunca o dado bruto da fazenda real por tras."""
    cards_reais_todos = get_dashboard_data(None, translations)
    coords_reais = data_reader.read_site_coordinates()
    resultado = {}
    for site, cards in cards_reais_todos.items():
        if permitted is None or site in permitted:
            resultado[site] = cards
    for vf in models.get_all_virtual_farms():
        if permitted is not None and vf["site_name"] not in permitted:
            continue
        if vf.get("tipo") == "clima":
            continue  # ponto so' de clima -- nunca tem card de doenca (ver mapa_interpolado)
        cards, _ = virtual_farms.interpolar_cards(vf["lat"], vf["lon"], vf["raio_km"], cards_reais_todos, coords_reais)
        if cards:
            resultado[vf["site_name"]] = cards
    return resultado


def _resolve_site_cards(site, translations):
    """Cards de UM site so -- se for fazenda virtual, interpola das
    fazendas reais dentro do raio; senao busca normal
    (`get_dashboard_data`). Usado pelo envio de WhatsApp, que trabalha
    fazenda por fazenda."""
    vf = models.get_virtual_farm(site)
    if vf is None:
        return get_dashboard_data({site}, translations).get(site, [])
    if vf.get("tipo") == "clima":
        return []  # ponto so' de clima -- nunca tem card de doenca (ver mapa_interpolado)
    cards_reais_todos = get_dashboard_data(None, translations)
    coords_reais = data_reader.read_site_coordinates()
    cards, _ = virtual_farms.interpolar_cards(vf["lat"], vf["lon"], vf["raio_km"], cards_reais_todos, coords_reais)
    return cards


_weather_cache = {}  # site_name -> (timestamp, latlon, dados)
_ndvi_preview_cache = {}  # site_name -> (timestamp, {"imagem":..., "data":..., "cobertura_nuvens":...})


def _rotulo_estacao_clima(site, latlon):
    """Cidade/UF (regiao) pra mostrar como referencia do clima de uma
    fazenda -- so' rotulo, o valor numerico do clima sempre vem da
    Open-Meteo. Usa a estacao REALMENTE escolhida em
    `weather_station_overrides` quando houver, pra o nome mostrado no
    relatorio NUNCA divergir do que a aba Fazendas/Alertas Clima marca
    como selecionado -- antes recalculava "estacao mais proxima dessa
    coordenada" de novo aqui, que podia devolver um nome DIFERENTE do
    escolhido quando o catalogo tem mais de uma estacao bem perto uma da
    outra (bug real relatado: estacao marcada como Diamantino, relatorio
    mostrando outro nome). So' cai pra "mais proxima da coordenada" (via
    `estacao_mais_proxima`, so' INMET/Brasil) quando a fazenda usa a
    coordenada propria (sem override) -- ai' e' so' referencia
    geografica mesmo, nao uma escolha do cliente pra' repetir."""
    escolha = models.get_all_weather_station_overrides().get(site)
    if escolha and escolha["codigo"]:
        provider = countries.get_country(escolha["country_code"])["station_provider"]
        estacao = next(
            (e for e in provider.get_estacoes() if e["codigo"] == escolha["codigo"]), None
        )
        if estacao:
            return estacao["cidade"], estacao["uf"]
    estacao = inmet_stations.estacao_mais_proxima(*latlon)
    return (estacao["cidade"], estacao["uf"]) if estacao else (None, None)


def _get_weather_for_site(site, coords):
    """Cache simples em memoria por fazenda -- evita bater na Open-Meteo a
    cada carregamento da pagina de Recomendacoes. Alem do clima em si,
    marca de onde veio (`fonte`) e a cidade/UF de referencia
    (`cidade`/`uf`, ver `_rotulo_estacao_clima`). O cache guarda TAMBEM o
    `latlon` que gerou aquele dado -- nao so' tempo decorrido -- pra um
    chamador que (por engano, ex. bug ja visto em `graficos_dados`)
    passar a coordenada ERRADA de uma fazenda (a propria em vez da
    estacao escolhida em `weather_station_overrides`, ver
    `_weather_coords_all`) nunca "vazar" esse dado errado pros chamadores
    CORRETOS -- cada combinacao (site, latlon) tem sua propria entrada
    logica, uma simplesmente nao invalida a outra."""
    latlon = coords.get(site)
    if not latlon:
        return None
    cached = _weather_cache.get(site)
    now = time.time()
    if cached and cached[1] == latlon:
        ttl = WEATHER_CACHE_TTL_SECONDS if cached[2] else WEATHER_FALHA_BACKOFF_SECONDS
        if now - cached[0] < ttl:
            return cached[2]
    if latlon[0] == INTERPOLACAO_SENTINEL:
        # "Interpolacao das 3 estacoes mais proximas" (ver _weather_coords_all)
        # -- `latlon` aqui NAO e' uma coordenada, e' (SENTINEL, "pais:codigo" x3).
        estacoes_info = []
        for par in latlon[1:]:
            country_code, codigo = par.split(":", 1)
            provider = countries.get_country(country_code)["station_provider"]
            estacao = next((e for e in provider.get_estacoes() if e["codigo"] == codigo), None)
            if estacao:
                estacoes_info.append(estacao)
        data = weather_forecast.get_weather_forecast_interpolado([(e["lat"], e["lon"]) for e in estacoes_info])
        if data:
            nomes = ", ".join(f"{e['cidade']}/{e['uf']}" for e in estacoes_info)
            data["cidade"] = f"Media de {len(estacoes_info)} estacoes: {nomes}"
            data["uf"] = None
            data["fonte"] = "Open-Meteo (media de estacoes proximas)"
        _weather_cache[site] = (now, latlon, data)
        return data
    data = weather_forecast.get_weather_forecast(*latlon)
    if data:
        data["cidade"], data["uf"] = _rotulo_estacao_clima(site, latlon)
        data["fonte"] = data.get("fonte_previsao", "Open-Meteo")
    data = _aplicar_leitura_estacao_oficial(site, data, latlon)
    _weather_cache[site] = (now, latlon, data)
    return data


def _estacao_oficial_do_site(site):
    """(country_code, codigo, uf) da estacao de referencia escolhida pra'
    fazenda (`weather_station_overrides`), so' quando o pais dela tem
    integracao de DADOS medidos (`estacoes_dados.configurado`) -- None
    caso contrario. Na opcao "interpolacao das 3 estacoes", a mais perto."""
    escolha = models.get_all_weather_station_overrides().get(site)
    if not escolha or not escolha["codigo"]:
        return None
    if escolha["codigo"] == INTERPOLACAO_SENTINEL:
        # "Interpolacao das 3 estacoes": pro dado MEDIDO usa a mais perto
        # das 3 (pedido do usuario, 15/10/2026). A previsao continua sendo
        # a media das 3 (`_get_weather_for_site`).
        latlon = _coords_all().get(site)
        if not latlon:
            return None
        pais = models.get_all_site_countries().get(site, countries.DEFAULT_COUNTRY)
        proximas = countries.estacoes_mais_proximas_global(*latlon, pais, n=1)
        if not proximas or not estacoes_dados.configurado(proximas[0]["country_code"]):
            return None
        mais_perto = proximas[0]
        if mais_perto["country_code"] == "CL":
            escolha = {"codigo": mais_perto["codigo"], "country_code": "CL"}
        else:
            return (mais_perto["country_code"], mais_perto["codigo"], mais_perto.get("uf"))
    if not estacoes_dados.configurado(escolha["country_code"]):
        return None
    provider = countries.get_country(escolha["country_code"])["station_provider"]
    estacao = next((e for e in provider.get_estacoes() if e["codigo"] == escolha["codigo"]), None)
    if escolha["country_code"] == "CL":
        # Dado medido da DMC so' existe pra estacao AUTOMATICA (EMA) -- se a
        # de referencia e' manual/desativada, usa a EMA em servico mais perto.
        automaticas = {e["codigo"] for e in dmc_stations.estacoes_automaticas()}
        if automaticas and escolha["codigo"] not in automaticas:
            latlon = (estacao["lat"], estacao["lon"]) if estacao else _coords_all().get(site)
            ema = dmc_stations.automatica_mais_proxima(*latlon) if latlon else None
            return ("CL", ema["codigo"], None) if ema else None
    return (escolha["country_code"], escolha["codigo"], (estacao or {}).get("uf"))


def _aplicar_leitura_estacao_oficial(site, data, latlon):
    """Troca o clima "agora"/ultimas 24h (Open-Meteo) pelo MEDIDO na
    estacao oficial da fazenda, quando o pais tem integracao e a estacao
    mandou leitura nas ultimas 6h. A previsao continua da Open-Meteo.
    Se a Open-Meteo falhou (data None), ainda devolve a leitura medida,
    so' sem previsao."""
    estacao = _estacao_oficial_do_site(site)
    if not estacao:
        return data
    orgao = weather_forecast.fonte_estacao(estacao[0], estacao[1]) or {"BR": "INMET", "CL": "DMC", "AR": "SMN"}.get(estacao[0], "estacao oficial")
    fonte_previsao = (data or {}).get("fonte_previsao", "Open-Meteo")
    previsao_orgao = weather_forecast.previsao_estacao(estacao[0], estacao[1])
    if previsao_orgao:
        if not data:
            data = {"previsao_5_dias": [], "previsao_horaria_por_dia": {}}
            data["cidade"], data["uf"] = _rotulo_estacao_clima(site, latlon)
        _aplicar_previsao_orgao(data, previsao_orgao)
        fonte_previsao = orgao
    ultimas = weather_forecast.ultimas_24h_estacao(*estacao)
    leitura_recente = ultimas and datetime.fromisoformat(ultimas[-1]["hora"]) >= _agora_cuiaba_dt().replace(tzinfo=None) - timedelta(hours=6)
    if leitura_recente:
        if not data:
            data = {"previsao_5_dias": [], "previsao_horaria_por_dia": {}}
            data["cidade"], data["uf"] = _rotulo_estacao_clima(site, latlon)
        ultima = ultimas[-1]
        data.update({
            "temperatura_atual": ultima["temp"],
            "umidade_atual": ultima["umidade"],
            "chuva_atual_mm": ultima["chuva"],
            "chuva_24h_mm": round(sum(h["chuva"] or 0 for h in ultimas), 1) if any(h["chuva"] is not None for h in ultimas) else data.get("chuva_24h_mm"),
            "ultimas_24h": ultimas,
        })
    if data and (leitura_recente or previsao_orgao):
        leitura_fonte = f"{orgao} (estacao)" if leitura_recente else "Open-Meteo"
        data["fonte"] = f"Leitura: {leitura_fonte} / Previsao: {fonte_previsao}"
    return data


def _aplicar_previsao_orgao(data, previsao):
    """Troca a previsao (Open-Meteo) pela do orgao nacional -- hoje so' o
    SMN, de 3 em 3 horas, com temperatura e chuva mas SEM umidade. Cada
    ponto de 3h vira 3 horas (chuva dividida entre elas); a umidade de
    cada hora vem da previsao horaria da Open-Meteo quando ela existir,
    senao fica None (o risco de infeccao entao so' considera a chuva)."""
    umidade_open_meteo = {
        p["hora"]: p.get("umidade")
        for pontos in (data.get("previsao_horaria_por_dia") or {}).values() for p in pontos
    }
    agora = _agora_cuiaba_dt().replace(tzinfo=None).strftime("%Y-%m-%dT%H:00")
    hoje = agora[:10]
    por_dia = {}
    for hora_iso in sorted(previsao):
        temp, chuva = previsao[hora_iso]
        base = datetime.fromisoformat(hora_iso)
        for i in range(3):
            h = (base + timedelta(hours=i)).strftime("%Y-%m-%dT%H:00")
            if h <= agora:
                continue
            por_dia.setdefault(h[:10], []).append({
                "hora": h, "temp": temp, "umidade": umidade_open_meteo.get(h),
                "chuva": round((chuva or 0) / 3, 2),
            })
    nuvens_open_meteo = {d["data"]: d.get("nuvens_pct") for d in data.get("previsao_5_dias") or []}
    previsao_5_dias = []
    for dia in sorted(d for d in por_dia if d > hoje)[:5]:
        pontos = [previsao[h] for h in previsao if h[:10] == dia]
        previsao_5_dias.append({
            "data": dia,
            "chuva_mm": round(sum(p[1] or 0 for p in pontos), 1),
            "temp_max": max(p[0] for p in pontos),
            "temp_min": min(p[0] for p in pontos),
            "nuvens_pct": nuvens_open_meteo.get(dia),
        })
    data["previsao_horaria_por_dia"] = por_dia
    data["previsao_5_dias"] = previsao_5_dias


_historico_horario_cache = {}  # (lat, lon, inicio, fim) -> (timestamp, {dia: [horas]})


def _historico_horario_open_meteo(latlon, inicio, fim):
    """`weather_forecast.get_historico_horario` com cache em memoria
    (mesmo TTL/backoff de `_get_weather_for_site`)."""
    chave = (latlon[0], latlon[1], inicio, fim)
    cached = _historico_horario_cache.get(chave)
    now = time.time()
    if cached:
        ttl = WEATHER_CACHE_TTL_SECONDS if cached[1] else WEATHER_FALHA_BACKOFF_SECONDS
        if now - cached[0] < ttl:
            return cached[1]
    por_dia = weather_forecast.get_historico_horario(latlon[0], latlon[1], inicio, fim)
    if len(_historico_horario_cache) > 500:
        _historico_horario_cache.clear()
    _historico_horario_cache[chave] = (now, por_dia)
    return por_dia


def _completar_clima_horario(hourly_lookup, device_by_site, sites, dias):
    """Copia de `hourly_lookup` com os dias que faltam preenchidos pelo
    clima horario de FONTE EXTERNA da fazenda, chaveado pelo device dela.
    Clima nunca vem do BioScout (decisao do usuario, 16/10/2026 -- o
    clima de la' e' modelo, nao sensor; ver
    .claude/memory/oneagro_clima_bioscout_modelo.md): os chamadores
    passam `hourly_lookup` vazio. Ordem de preferencia: estacao oficial
    da fazenda (`_estacao_oficial_do_site`, pais com integracao de dados
    medidos) > Open-Meteo na coordenada de clima da fazenda
    (`_weather_coords_all`, mesma estacao de referencia do resto do site)."""
    if not dias:
        return hourly_lookup
    coords_clima = _weather_coords_all()
    coords_proprias = None
    pendentes = {}
    dias_faltando = set()
    hoje = _hoje_cuiaba().isoformat()
    completo = dict(hourly_lookup)
    for site in sites:
        device = device_by_site.get(site)
        faltando = [d for d in dias if not completo.get((device, d))] if device else []
        if not faltando:
            continue
        estacao = _estacao_oficial_do_site(site)
        if estacao:
            por_dia = weather_forecast.horas_estacao_por_dia(*estacao, min(faltando), max(faltando))
            for dia in faltando:
                if por_dia.get(dia):
                    completo[(device, dia)] = por_dia[dia]
            faltando = [d for d in faltando if not completo.get((device, d))]
            if not faltando:
                continue
        latlon = coords_clima.get(site)
        if latlon and latlon[0] == INTERPOLACAO_SENTINEL:
            if coords_proprias is None:
                coords_proprias = _coords_all()
            latlon = coords_proprias.get(site)
        if latlon:
            pendentes[device] = latlon
            dias_faltando.update(faltando)
    dias_faltando = {d for d in dias_faltando if d <= hoje}
    if not pendentes or not dias_faltando:
        return completo
    inicio, fim = min(dias_faltando), max(dias_faltando)
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        resultados = dict(zip(
            pendentes,
            executor.map(lambda ll: _historico_horario_open_meteo(ll, inicio, fim), pendentes.values()),
        ))
    for device, por_dia in resultados.items():
        for dia, horas in por_dia.items():
            if not completo.get((device, dia)):
                completo[(device, dia)] = horas
    return completo


def _prefetch_weather(sites, coords):
    """Busca o clima (Open-Meteo) de varias fazendas em PARALELO, so' pra'
    aquecer `_weather_cache` -- `_get_weather_for_site` sozinho busca uma
    fazenda de cada vez (rede, ~1-1.5s cada); numa pagina com varias
    fazendas (ex.: Manejo Safra, ~9 fazendas) isso somava alguns
    segundos de espera SEQUENCIAL toda vez que o cache de 30min expirava.
    So' preenche o cache; quem chama continua usando
    `_get_weather_for_site` normalmente depois (ja' encontra tudo
    pronto). Fazenda que ja' estava com cache valido nao gera chamada de
    rede nova (o proprio `_get_weather_for_site` confere isso)."""
    pendentes = [s for s in sites if coords.get(s)]
    if not pendentes:
        return
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(lambda s: _get_weather_for_site(s, coords), pendentes))

def _weather_coords_all():
    """Coordenada usada pra buscar a previsao (Open-Meteo) de cada site --
    usa a estacao oficial (INMET ou DMC, conforme o pais da escolha)
    escolhida em `weather_station_overrides` quando houver -- por padrao
    ja' e' a mais proxima, escolhida sozinha por `_auto_detectar_estacoes_novas`
    assim que a fazenda aparece, mas o cliente pode trocar (aba Fazendas/
    Alertas Clima) por outra estacao ou pela coordenada da propria
    fazenda (`_coords_all`), reservada pra quem tem estacao meteorologica
    propria."""
    coords = _coords_all()
    overrides = models.get_all_weather_station_overrides()
    if not overrides:
        return coords
    catalogos = {}
    resultado = dict(coords)
    site_countries = None
    for site, escolha in overrides.items():
        if site not in resultado:
            continue
        if escolha["codigo"] == INTERPOLACAO_SENTINEL:
            # "Interpolacao das 3 estacoes mais proximas" -- nao e' uma
            # coordenada de verdade, e' um marcador que `_get_weather_for_site`
            # reconhece pra' buscar e MEDIAR as 3 (ver weather_forecast.
            # get_weather_forecast_interpolado). Recalculado a partir do
            # pais REAL da fazenda (nao do country_code guardado no
            # override, que nao tem sentido pra' esse sentinel). Sem
            # nenhuma estacao por perto (pais sem catalogo ainda), cai de
            # volta pra' coordenada da propria fazenda -- mesma
            # degradacao graciosa de sempre.
            if site_countries is None:
                site_countries = models.get_all_site_countries()
            pais = site_countries.get(site, countries.DEFAULT_COUNTRY)
            estacoes = countries.estacoes_mais_proximas_global(*resultado[site], pais, n=3)
            if estacoes:
                # "pais:codigo" por estacao (mesma convencao ja usada no
                # radio da tela, ver save_weather_station_override) -- nao
                # da' pra assumir que as 3 sao sempre do mesmo provedor
                # (fazenda perto de fronteira pode misturar paises).
                resultado[site] = (INTERPOLACAO_SENTINEL,) + tuple(
                    f"{e['country_code']}:{e['codigo']}" for e in estacoes
                )
            continue
        code = escolha["country_code"]
        if code not in catalogos:
            provider = countries.get_country(code)["station_provider"]
            catalogos[code] = {e["codigo"]: e for e in provider.get_estacoes()}
        estacao = catalogos[code].get(escolha["codigo"])
        if estacao:
            resultado[site] = (estacao["lat"], estacao["lon"])
    return resultado


def _load_or_create_secret_key():
    if "BIOSCOUT_WEB_SECRET" in os.environ:
        return os.environ["BIOSCOUT_WEB_SECRET"]
    key_path = Path(__file__).parent / "secret_key.bin"
    if key_path.exists():
        return key_path.read_bytes()
    key = os.urandom(32)
    key_path.write_bytes(key)
    return key


app = Flask(__name__)
app.secret_key = _load_or_create_secret_key()
app.jinja_env.filters["data_br"] = models.fmt_data_br
app.jinja_env.filters["telefone_br"] = models.fmt_telefone_br

# A aba Fungicidas (autosave) manda o form inteiro (todas as doencas) a
# cada edicao -- com os checkboxes de "Registrado para" (uma por
# cultura x item quimico) e o minimo de 4 linhas em Biologicos, passa
# facil de 1000 campos, o limite padrao do Werkzeug 3.1+ (RequestEntityTooLarge/413).
# Flask 3.0 nao expoe isso via app.config ainda, entao ajusta direto na
# classe de request.
app.request_class.max_form_parts = 20_000

# Sem limite, um upload de KML/shapefile grande (ou malicioso) e' lido
# inteiro em memoria (ver save_ndvi_area / parse_shapefile_zip) -- no
# Railway isso pode derrubar o processo inteiro. 25MB cobre folgado um
# shapefile de contorno de fazenda real (que normalmente fica na casa dos
# KB/poucos MB).
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024


class _Request(app.request_class):
    # Excecao escopada: o arquivo do Agrofit (CSV de produtos formulados)
    # passa bem de 25MB.
    @property
    def max_content_length(self):
        if self.path == "/admin/fungicidas/agrofit-enviar":
            return 600 * 1024 * 1024
        if self.path in ("/admin/restaurar-backup", "/qap/primeiro-acesso"):
            return 300 * 1024 * 1024
        return super().max_content_length


app.request_class = _Request

# Ambiente de teste do Railway (Environment "QAP", plano de migracao no
# CLAUDE.md) -- o Railway preenche RAILWAY_ENVIRONMENT_NAME sozinho.
AMBIENTE_QAP = os.environ.get("RAILWAY_ENVIRONMENT_NAME", "").strip().lower() == "qap"


@app.context_processor
def _inject_ambiente_qap():
    return {"ambiente_qap": AMBIENTE_QAP}

# Sem isso, arquivo estatico (logo, icones, rodovias_br.json) so' ganha
# Cache-Control condicional (ETag/Last-Modified) -- ainda precisa de uma
# ida-e-volta ao servidor toda vez pra confirmar que nao mudou. Com
# max-age, o navegador usa a copia local direto por 12h sem nem perguntar.
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 43200
# ?v= nos .js do static: o navegador guarda 12h (linha acima), e sem isso
# continuava com a versao antiga do arquivo depois de um deploy.
app.jinja_env.globals["versao_estaticos"] = int(time.time())

# Paginas (Manejos/Fungicidas ~400-500KB de HTML) e GeoJSON/rodovias
# (1.5-4MB cada) iam sem compressao nenhuma -- gzip/brotli reduz ~10x.
app.config["COMPRESS_MIMETYPES"] = [
    "text/html", "text/css", "text/javascript", "application/javascript",
    "application/json", "application/geo+json", "image/svg+xml",
]
Compress(app)


@app.after_request
def _no_cache(response):
    """Sem isso, o botao 'voltar' do navegador depois de 'Sair' mostra a
    pagina anterior direto do cache local, sem pedir login de novo. So se
    aplica as paginas/rotas dinamicas -- arquivos estaticos (JS, CSS,
    rodovias_br.json etc, servidos por send_static_file) nao tem dado de
    sessao e ficam de fora, senao o navegador tem que rebaixar tudo de novo
    a cada carregamento de pagina (pesava bastante no Mapa/Mapa Interpolado,
    que carregam ~2MB de rodovias). Tambem fica de fora qualquer rota que ja
    tenha definido seu proprio Cache-Control (ex. imagem_ndvi_historico,
    imutavel por id -- ver a rota)."""
    if request.endpoint == "static" or "Cache-Control" in response.headers:
        return response
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response


# Personalizacoes de tela salvas na conta (pedido do usuario, 16/10/2026)
# -- antes ficavam so' no navegador (localStorage), sem acompanhar a pessoa
# em outro aparelho e misturando quem usa o mesmo computador.
PREFERENCIAS_PERMITIDAS = {"graficos_filtros", "rec_abertas", "rec_formatos", "mapa_chuva"}


@app.context_processor
def _inject_user_prefs():
    if not current_user.is_authenticated:
        return {"user_prefs": {}}
    prefs = getattr(g, "_user_prefs", None)
    if prefs is None:
        prefs = g._user_prefs = models.get_user_prefs(int(current_user.id))
    return {"user_prefs": prefs}


@app.route("/preferencias", methods=["POST"])
@login_required
def salvar_preferencia():
    corpo = request.get_json(silent=True) or {}
    chave = corpo.get("chave")
    if chave not in PREFERENCIAS_PERMITIDAS or "valor" not in corpo:
        abort(400)
    if len(json.dumps(corpo["valor"])) > 8000:
        abort(413)
    models.set_user_pref(int(current_user.id), chave, corpo["valor"])
    return jsonify({"ok": True})

FETCH_SCRIPT = Path(__file__).parent.parent / "Fetch-BioScoutData.ps1"
POWERSHELL_EXE = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
SPORE_CSV = data_reader.DATA_DIR / "spore_counts.csv"
ULTIMA_BUSCA_MARCA = data_reader.DATA_DIR / ".ultima_busca_bioscout"
AUTO_REFRESH_MAX_AGE_SECONDS = 15 * 60  # se os dados tiverem mais que isso, atualiza sozinho ao abrir a pagina
BIOSCOUT_USERNAME = os.environ.get("BIOSCOUT_USERNAME")
BIOSCOUT_PASSWORD = os.environ.get("BIOSCOUT_PASSWORD")

_fetch_lock = threading.Lock()
_fetch_state = {"running": False, "last_error": None}


def _fetch_configured():
    """True se tem algum jeito de buscar dados novos: credenciais em
    variavel de ambiente (funciona em qualquer lugar, inclusive no site
    hospedado) ou o script PowerShell local (so existe no Windows, onde
    roda o `bioscout_cred.xml`)."""
    return bool(BIOSCOUT_USERNAME and BIOSCOUT_PASSWORD) or Path(POWERSHELL_EXE).exists()


def _run_fetch_in_background():
    try:
        if BIOSCOUT_USERNAME and BIOSCOUT_PASSWORD:
            erros = bioscout_fetch.fetch_recent(
                data_reader.DATA_DIR, BIOSCOUT_USERNAME, BIOSCOUT_PASSWORD, log=lambda msg: None
            )
            error = "; ".join(erros)[:300] if erros else None
        else:
            result = subprocess.run(
                [POWERSHELL_EXE, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(FETCH_SCRIPT), "-SkipExtras"],
                capture_output=True, text=True,
            )
            error = None if result.returncode == 0 else (result.stderr[-300:] or result.stdout[-300:])
        with _fetch_lock:
            _fetch_state["last_error"] = error
    except Exception as exc:  # nunca deixa a thread morrer silenciosamente
        with _fetch_lock:
            _fetch_state["last_error"] = str(exc)
    finally:
        with _fetch_lock:
            _fetch_state["running"] = False
        try:
            ULTIMA_BUSCA_MARCA.touch()
        except OSError:
            pass


def _start_fetch_if_not_running():
    """Retorna True se uma nova busca foi iniciada agora, False se ja tinha uma rodando."""
    with _fetch_lock:
        if _fetch_state["running"]:
            return False
        _fetch_state["running"] = True
        _fetch_state["last_error"] = None
    threading.Thread(target=_run_fetch_in_background, daemon=True).start()
    return True


def _data_age_seconds():
    """Tempo desde a ULTIMA BUSCA no BioScout (marca gravada no fim de
    cada busca) -- antes era a data do spore_counts.csv, que nao muda
    quando o BioScout nao traz leitura nova: ai' toda abertura de pagina
    disparava outra busca completa, sem parar (deixava a navegacao lenta)."""
    for caminho in (ULTIMA_BUSCA_MARCA, SPORE_CSV):
        try:
            return time.time() - caminho.stat().st_mtime
        except FileNotFoundError:
            continue
    return None  # nunca foi buscado ainda


def _maybe_auto_refresh():
    """Chamado ao abrir Painel/Recomendacoes: se os dados estao velhos e
    nenhuma busca esta rodando, dispara uma atualizacao sozinha em segundo
    plano (nao trava a pagina -- ela mostra os dados que tiver agora).
    Retorna True se uma busca nova foi iniciada agora. So funciona se
    `_fetch_configured()` -- ou BIOSCOUT_USERNAME/BIOSCOUT_PASSWORD (busca
    em Python, roda em qualquer lugar) ou o script PowerShell local."""
    if not _fetch_configured():
        return False
    age = _data_age_seconds()
    if age is None or age > AUTO_REFRESH_MAX_AGE_SECONDS:
        return _start_fetch_if_not_running()
    return False


def _load_translations():
    """Garante que toda doenca ja vista nos dados tenha uma linha na tabela
    de traducao (doencas novas do BioScout entram prontas para editar, com
    o nome em ingles ou o padrao conhecido, e o nome cientifico ja
    preenchido quando o BioScout mandar), junta linha duplicada de doenca
    que sobrou de antes da normalizacao de capitalizacao existir (ver
    `data_reader.nome_canonico_para`/`models.merge_duplicate_disease_translations`)
    e devolve o mapa atual."""
    try:
        display_names = data_reader.read_unique_display_names()
        models.ensure_disease_translations(display_names, data_reader.DOENCA_MAP, data_reader.read_scientific_names())
        canonico_por_atual = {
            en: data_reader.nome_canonico_para(en) for en in models.get_all_disease_translations()
        }
        models.merge_duplicate_disease_translations(canonico_por_atual)
    except FileNotFoundError:
        pass
    return models.get_all_disease_translations()


def _filter_cards_by_cultura(cards_by_site, culturas_by_site, doenca_culturas, safra=None):
    """Se a fazenda tiver uma cultura atual definida numa safra (aba
    Fazendas/Recomendacoes), mostra so os cartoes de doencas marcadas para
    essa cultura na matriz da aba Doencas (`doenca_culturas`) -- doenca sem
    nenhuma marcacao na matriz ainda NUNCA e' escondida, pra nao sumir um
    alerta novo/desconhecido por engano (segue a hierarquia da aba
    Doencas). Cultura "" (vazio, ainda nao decidida) NAO mostra doenca
    nenhuma -- forca decidir "Todos" (mostra tudo, sem filtro nenhum,
    equivalente ao comportamento antigo do vazio) ou uma cultura
    especifica, de proposito, pedido explicito do usuario.

    Com `safra` definido (Recomendacoes Safra / 2a Safra), usa so a
    cultura daquela safra. Sem `safra` (envio agendado, sem uma safra
    especifica escolhida), usa a uniao das culturas de todas as safras da
    fazenda -- fazenda com Soja na Safra e Milho na 2a Safra mostra as
    doencas das duas; se qualquer uma das safras estiver em "Todos", vale
    "Todos" pra fazenda inteira nesse caso."""
    filtered = {}
    for site, cards in cards_by_site.items():
        if safra:
            valores = {culturas_by_site.get((site, safra), {}).get("cultura")}
        else:
            valores = {
                culturas_by_site.get((site, s), {}).get("cultura") for s, _ in models.SAFRAS
            }
        valores.discard(None)
        if CULTURA_TODOS in valores:
            filtered[site] = cards
            continue
        culturas_ativas = {v for v in valores if v}
        if not culturas_ativas:
            filtered[site] = []  # "(vazio)" em todas as safras consideradas -- ainda nao decidiram, nao mostra nada
            continue
        filtered[site] = [
            c for c in cards
            if not doenca_culturas.get(c["doenca_en"]) or doenca_culturas[c["doenca_en"]] & culturas_ativas
        ]
    return filtered


def _anotacoes_da_fazenda(site, culturas, translations, doenca_culturas, notes, notas_padrao):
    """Anotacao por doenca na aba Manejos (antes so' na aba Recomendacoes):
    as doencas das culturas das safras da fazenda, pela mesma regra de
    `_filter_cards_by_cultura` (doenca sem cultura marcada sempre entra;
    nenhuma cultura escolhida = nenhuma doenca). Mesma chave (fazenda,
    nome da doenca) e mesma anotacao padrao da Biblioteca."""
    culturas = {c for c in culturas if c}
    if not culturas:
        return []
    linhas = []
    for doenca_en, info in translations.items():
        if CULTURA_TODOS in culturas or not doenca_culturas.get(doenca_en) or doenca_culturas[doenca_en] & culturas:
            padrao = notas_padrao.get(doenca_en, "")
            linhas.append({
                "doenca": info["nome_pt"], "nota_padrao": padrao,
                "nota": notes.get((site, info["nome_pt"]), "") or padrao,
            })
    return sorted(linhas, key=lambda l: _chave_alfabetica(l["doenca"]))


def _cultura_label(site, safra, culturas_by_site):
    """Nome da cultura atual pra mostrar no relatorio (WhatsApp/"Copiar
    recomendacao"). Com `safra` definida, usa so a cultura daquela safra;
    sem `safra` (envio agendado, uniao das safras), junta os nomes
    diferentes com " / " (ex.: "Soja / Milho"). "" se estiver "(vazio)"
    em todas as safras consideradas."""
    if safra:
        return culturas_by_site.get((site, safra), {}).get("cultura") or ""
    nomes = []
    for s, _ in models.SAFRAS:
        c = culturas_by_site.get((site, s), {}).get("cultura")
        if c and c not in nomes:
            nomes.append(c)
    return " / ".join(nomes)


def _fungicida_ordem_all_cached():
    """Cache por requisicao (flask.g, ver `_nome_exibicao` pro mesmo
    padrao) de `models.get_all_fungicida_ordem()` -- montar a
    recomendacao de VARIAS fazendas na mesma requisicao (aba
    Recomendacoes) chamava `models.get_fungicida_ordem` uma vez por
    doenca/tipo/fazenda (bug de performance real: 32 conexoes SQLite so'
    pra isso numa unica carga da pagina, com so' 11 fazendas). Reseta
    sozinho a cada requisicao nova (`g` e' por-requisicao)."""
    val = getattr(g, "_fungicida_ordem_all", None)
    if val is None:
        val = models.get_all_fungicida_ordem()
        g._fungicida_ordem_all = val
    return val


def _ordem_fungicida(doenca, tipo, n):
    """Mesmo resultado de `models.get_fungicida_ordem(doenca, tipo, n)`,
    so' que a partir do cache por-requisicao acima (sem abrir conexao
    nova) -- ver `_apply_fungicida_overrides`."""
    order = [i for i in _fungicida_ordem_all_cached().get((doenca, tipo), []) if i < n]
    faltando = [i for i in range(n) if i not in order]
    return order + faltando


def _fungicida_proibido(tipo, ingrediente):
    """Produto que nunca aparece (Biblioteca, Recomendacoes, texto, PDF,
    relatorios): quimico banido/sem registro vigente no Agrofit, ou
    "biologico" que nao e' microrganismo (oleo mineral, neem...)."""
    if not ingrediente:
        return False
    if tipo == "quimico":
        return agrofit_dados.produto_sem_registro(ingrediente)
    return not agrofit_dados.eh_biologico(ingrediente)


def _apply_fungicida_overrides(doenca, tipo, itens, overrides, cultura=None, bloqueios=None):
    """Aplica edicoes feitas pelo admin (tela Fungicidas) sobre a lista
    padrao de ingredientes ativos daquela doenca -- itens sem edicao saem
    exatamente como em fungicida_data.py -- e depois reordena conforme os
    botoes "mover pra cima/baixo" (models.get_fungicida_ordem), para que o
    admin possa colocar os melhores tratamentos da regiao no topo (so os 3
    primeiros aparecem nas abas Recomendacoes). `cultura`/`bloqueios`
    (so' para tipo="quimico"): um item marcado como sem registro pra
    aquela cultura (models.get_all_fungicida_registro_bloqueado) e' tratado
    como removido nessa fazenda -- sem marcacao nenhuma, continua
    aparecendo normalmente (nunca esconde por omissao, so' quando alguem
    desmarca explicitamente). Consideram tambem as linhas extras em branco
    da aba Fungicidas (`fungicida_data.n_linhas`: 10 quimicos, 4
    biologicos), entao um produto cadastrado numa dessas linhas extras aparece aqui
    normalmente; linhas deixadas vazias sao ignoradas."""
    bloqueios = bloqueios or {}
    n_real = len(itens)
    n = fungicida_data.n_linhas(tipo, n_real)
    if n == 0:
        return []
    content = []
    for idx in range(n):
        item = itens[idx] if idx < n_real else {"ingrediente": "", "classe": None}
        override = overrides.get((doenca, tipo, idx))
        culturas_bloqueadas = bloqueios.get((doenca, tipo, idx))
        if override and override["removido"]:
            content.append(None)  # historico de remocoes antigas, se houver
        elif cultura and culturas_bloqueadas and cultura in culturas_bloqueadas:
            content.append(None)  # sem registro pra essa cultura -- nao recomenda
        elif override:
            content.append(None if _fungicida_proibido(tipo, override["ingrediente"])
                           else {"ingrediente": override["ingrediente"], "classe": override["classe"]})
        elif not item["ingrediente"] or _fungicida_proibido(tipo, item["ingrediente"]):
            content.append(None)  # linha vazia ou produto proibido -- nao aparece na recomendacao
        else:
            content.append(item)
    order = _ordem_fungicida(doenca, tipo, n)
    return [content[i] for i in order if content[i] is not None]


def _build_recomendacao_grupo(doenca, tipo, grupo, overrides, cultura=None, bloqueios=None):
    """Monta {fonte, fonte_url, itens} de um grupo (quimico/biologico) de uma
    doenca, ja com as edicoes/remocoes do admin (e o filtro de registro por
    cultura, so' pra quimicos) aplicados."""
    if not grupo:
        return None
    itens = _apply_fungicida_overrides(doenca, tipo, grupo["itens"], overrides, cultura=cultura, bloqueios=bloqueios)
    return {"fonte": grupo["fonte"], "fonte_url": grupo["fonte_url"], "itens": itens}


def _fmt_num(n):
    return f"{n:g}"


def _formatar_condicoes_germinacao(info):
    """Monta o texto exibido na aba Manejo (e na propria aba Doencas) a
    partir dos limites numericos cadastrados (germ_temp_min/max, germ_ur_min,
    germ_molhamento_horas, germ_agua_livre_inibe) -- coluna unica que serve
    tanto de texto legivel quanto de base de calculo da luz de risco (ver
    `_calc_risco_germinacao`), pra nunca ficar um dessincronizado do outro."""
    temp_min, temp_max = info.get("germ_temp_min"), info.get("germ_temp_max")
    if temp_min is None or temp_max is None:
        return ""
    partes = [f"{_fmt_num(temp_min)}-{_fmt_num(temp_max)}°C"]
    ur_min = info.get("germ_ur_min")
    if ur_min is not None:
        partes.append(f"UR≥{_fmt_num(ur_min)}%")
    if info.get("germ_agua_livre_inibe"):
        partes.append("chuva/agua livre atrapalha a germinacao")
    else:
        molhamento = info.get("germ_molhamento_horas")
        if molhamento:
            partes.append(f"molhamento foliar {_fmt_num(molhamento)}h+")
    return ", ".join(partes)


def _fungicida_overrides_cached():
    """Cache por requisicao (ver `_fungicida_ordem_all_cached`) de
    `models.get_all_fungicida_overrides()`."""
    val = getattr(g, "_fungicida_overrides", None)
    if val is None:
        val = models.get_all_fungicida_overrides()
        g._fungicida_overrides = val
    return val


def _fungicida_bloqueios_cached():
    """Cache por requisicao (ver `_fungicida_ordem_all_cached`) de
    `models.get_all_fungicida_registro_bloqueado()`."""
    val = getattr(g, "_fungicida_bloqueios", None)
    if val is None:
        val = models.get_all_fungicida_registro_bloqueado()
        g._fungicida_bloqueios = val
    return val


def _fungicida_notas_cached():
    """Cache por requisicao de `models.get_all_fungicida_notas()`."""
    val = getattr(g, "_fungicida_notas", None)
    if val is None:
        val = models.get_all_fungicida_notas()
        g._fungicida_notas = val
    return val


def _disease_info_cached():
    """Cache por requisicao (ver `_fungicida_ordem_all_cached`) de
    `models.get_all_disease_info()`."""
    val = getattr(g, "_disease_info", None)
    if val is None:
        val = models.get_all_disease_info()
        g._disease_info = val
    return val


def _build_site_diseases(site, cards, notes, fungicida_overrides=None, cultura=None):
    """A partir dos cartoes (todas as doencas) de uma fazenda, monta a lista
    das que estao em Atencao/Perigo com a recomendacao de fungicida (quando
    houver) e a anotacao manual salva -- usado pela tela de Recomendacoes,
    pelo envio manual de WhatsApp e pelo envio agendado. `cultura` (cultura
    atual da fazenda/safra) filtra os quimicos sem registro pra ela -- ver
    `_apply_fungicida_overrides`. Chamado uma vez POR FAZENDA na aba
    Recomendacoes (varias fazendas na mesma requisicao) -- os "get_all"
    abaixo usam cache por-requisicao (`g`, mesmo padrao de
    `_nome_exibicao`) pra nao reabrir conexao a cada fazenda (bug de
    performance real: era a maior fatia do tempo dessa pagina)."""
    if fungicida_overrides is None:
        fungicida_overrides = _fungicida_overrides_cached()
    bloqueios = _fungicida_bloqueios_cached()
    disease_info = _disease_info_cached()
    notas_padrao = _fungicida_notas_cached()
    diseases = []
    for card in cards:
        if card["status"] not in ("Perigo", "Atencao"):
            continue
        doenca_en = card["doenca_en"]
        rec = fungicida_data.get_recomendacao(doenca_en)
        quimicos = _build_recomendacao_grupo(
            doenca_en, "quimico", rec["quimicos"], fungicida_overrides, cultura=cultura, bloqueios=bloqueios,
        ) if rec else None
        biologicos = _build_recomendacao_grupo(doenca_en, "biologico", rec["biologicos"], fungicida_overrides) if rec else None
        info = disease_info.get(doenca_en, {})
        diseases.append({
            "doenca": card["doenca"],
            "doenca_en": doenca_en,
            "status": card["status"],
            "concentracao": card["concentracao"],
            "data": card["data"],
            "rotulo": card["doenca"],
            "cientifico": info.get("nome_cientifico", ""),
            "germinacao": _formatar_condicoes_germinacao(info),
            "germ_temp_min": info.get("germ_temp_min"),
            "germ_temp_max": info.get("germ_temp_max"),
            "germ_ur_min": info.get("germ_ur_min"),
            "germ_agua_livre_inibe": info.get("germ_agua_livre_inibe", False),
            "germ_molhamento_horas": info.get("germ_molhamento_horas"),
            "quimicos": quimicos,
            "biologicos": biologicos,
            "classe_label": fungicida_data.CLASSE_LABEL,
            # Sem anotacao propria da fazenda, vale a anotacao padrao da
            # doenca na Biblioteca de fungicidas.
            "nota": notes.get((site, card["doenca"]), "") or notas_padrao.get(doenca_en, ""),
            "nota_padrao": notas_padrao.get(doenca_en, ""),
        })
    diseases.sort(key=lambda d: (0 if d["status"] == "Perigo" else 1, d["doenca"]))
    return diseases


def _build_riscos_climaticos_sem_leitura(cards):
    """Uma linha por doenca ja monitorada nessa fazenda (a partir de
    TODOS os cards, sem exigir status Atencao/Perigo -- ao contrario de
    `_build_site_diseases`), com nome cientifico + condicoes de
    germinacao, mas SEM status/concentracao/recomendacao de fungicida.
    Usado quando a estacao esta ha muito tempo sem leitura nova
    (`bloqueado`, ver `_nivel_dados_defasados`) -- nesse caso nao da'
    mais pra confiar no ultimo status conhecido (pode estar bem
    desatualizado), mas o RISCO CLIMATICO (`_calc_risco_germinacao`,
    100% baseado no clima da Open-Meteo, nunca depende do sensor
    BioScout) continua valido e pode avisar a fazenda de uma condicao
    favoravel a' germinacao mesmo sem confirmar a presenca do fungo --
    pedido explicito do usuario. NAO inclui recomendacao de
    fungicida/observacao (isso pressupoe deteccao confirmada, que aqui
    nao existe) -- so' o alerta climatico. O chamador ainda precisa
    calcular `risco`/`previsao_risco` (mesma `_calc_risco_germinacao`/
    `_calc_previsao_risco_germinacao` usada pra `diseases`) e filtrar
    pra quem esta em risco medio/alto, senao a lista sai cheia de
    doenca com risco baixo (nao acionavel)."""
    disease_info = _disease_info_cached()
    vistos = {}
    for card in cards:
        doenca_en = card["doenca_en"]
        if doenca_en in vistos:
            continue
        info = disease_info.get(doenca_en, {})
        if info.get("germ_temp_min") is None or info.get("germ_temp_max") is None:
            continue  # sem germinacao cadastrada -- risco climatico nunca calcula, nao vale colocar na lista
        vistos[doenca_en] = {
            "doenca": card["doenca"], "doenca_en": doenca_en, "rotulo": card["doenca"],
            "cientifico": info.get("nome_cientifico", ""),
            "germinacao": _formatar_condicoes_germinacao(info),
            "germ_temp_min": info.get("germ_temp_min"), "germ_temp_max": info.get("germ_temp_max"),
            "germ_ur_min": info.get("germ_ur_min"), "germ_agua_livre_inibe": info.get("germ_agua_livre_inibe", False),
            "germ_molhamento_horas": info.get("germ_molhamento_horas"),
        }
    return list(vistos.values())


_MOLHAMENTO_PADRAO_HORAS = 6  # usado quando a doenca nao tem um numero de horas proprio cadastrado


def _calc_risco_germinacao(disease, weather):
    """Luz de risco de germinacao do esporo (verde/amarelo/vermelho) pra
    aba Manejo, WhatsApp e PDF -- calibrado com o horario OBSERVADO das
    ultimas 24h (Open-Meteo `past_days=1`, ver `weather_forecast.py`), nao
    so' a leitura do instante: conta quantas dessas 24h tiveram
    temperatura E umidade/chuva favoraveis AO MESMO TEMPO pra germinacao
    dessa doenca, e compara com o numero de horas de molhamento foliar
    minimo cadastrado na aba Doencas (ou 6h, se a doenca nao tiver um
    numero proprio). E' um indicador rapido baseado no clima de superficie
    (a rede nao tem sensor de molhamento foliar de verdade) -- nao
    substitui avaliacao agronomica. Retorna None quando falta clima ou
    limite cadastrado (esconde a luz).

    Regra: horas favoraveis >= limiar de molhamento -> vermelho (alto,
    ja' acumulou o tempo minimo pro fungo germinar); >= metade do limiar
    -> amarelo (medio, caminho pra' completar); menos que isso -> verde
    (baixo). Pra fungos onde agua livre atrapalha em vez de ajudar (ex.
    oidio), uma hora com chuva CONTA CONTRA a hora favoravel, nao a favor."""
    temp_min, temp_max, ur_min = disease.get("germ_temp_min"), disease.get("germ_temp_max"), disease.get("germ_ur_min")
    if temp_min is None or temp_max is None or not weather:
        return None
    agua_livre_inibe = disease.get("germ_agua_livre_inibe")

    horas = weather.get("ultimas_24h")
    if horas:
        favoraveis = 0
        for h in horas:
            temp, umidade, chuva = h.get("temp"), h.get("umidade"), h.get("chuva") or 0
            if temp is None:
                continue
            temp_ok = temp_min <= temp <= temp_max
            umidade_ok = False
            if ur_min is not None and umidade is not None:
                if agua_livre_inibe:
                    umidade_ok = umidade >= ur_min and chuva <= 0.2
                else:
                    umidade_ok = umidade >= ur_min or chuva > 0.2
            if temp_ok and umidade_ok:
                favoraveis += 1
        limiar = disease.get("germ_molhamento_horas") or _MOLHAMENTO_PADRAO_HORAS
        if favoraveis >= limiar:
            return "vermelho"
        if favoraveis >= limiar / 2:
            return "amarelo"
        return "verde"

    # sem horario (weather antigo em cache, ou falha parcial da API) --
    # cai pra' comparacao simples com a leitura do instante.
    temp = weather.get("temperatura_atual")
    if temp is None:
        return None
    umidade = weather.get("umidade_atual")
    chuva = weather.get("chuva_atual_mm") or 0
    temp_ok = temp_min <= temp <= temp_max
    umidade_ok = False
    if ur_min is not None and umidade is not None:
        if agua_livre_inibe:
            umidade_ok = umidade >= ur_min and chuva <= 0.2
        else:
            umidade_ok = umidade >= ur_min or chuva > 0.2
    if temp_ok and umidade_ok:
        return "amarelo"
    return "verde"


def calc_risco_diario_pct(disease, horas_do_dia):
    """Mesma regra de `_calc_risco_germinacao` (temp E umidade/chuva
    favoraveis ao mesmo tempo, ver comentario la' pra detalhe de
    `agua_livre_inibe`), mas aplicada num dia HISTORICO (clima horario de
    fonte externa, ver `_completar_clima_horario`) em vez do forecast ao
    vivo, e devolvendo um percentual 0-100 (horas favoraveis / horas de
    molhamento minimo da doenca, capado em 100%) em vez da luz
    verde/amarelo/vermelho -- usado no grafico de risco de infeccao da
    aba Graficos. Retorna None quando falta clima ou limite cadastrado."""
    temp_min, temp_max, ur_min = disease.get("germ_temp_min"), disease.get("germ_temp_max"), disease.get("germ_ur_min")
    if temp_min is None or temp_max is None or not horas_do_dia:
        return None
    agua_livre_inibe = disease.get("germ_agua_livre_inibe")
    favoraveis = 0
    for h in horas_do_dia:
        temp, umidade, chuva = h.get("temp"), h.get("umidade"), h.get("chuva") or 0
        if temp is None:
            continue
        temp_ok = temp_min <= temp <= temp_max
        umidade_ok = False
        if ur_min is not None and umidade is not None:
            if agua_livre_inibe:
                umidade_ok = umidade >= ur_min and chuva <= 0.2
            else:
                umidade_ok = umidade >= ur_min or chuva > 0.2
        if temp_ok and umidade_ok:
            favoraveis += 1
    limiar = disease.get("germ_molhamento_horas") or _MOLHAMENTO_PADRAO_HORAS
    return round(min(100, favoraveis / limiar * 100))


_RISCO_LABELS = {"vermelho": "Alto", "amarelo": "Médio", "verde": "Baixo"}
_RISCO_EMOJI = {"vermelho": "🔴", "amarelo": "🟡", "verde": "🟢"}


def _risco_label(risco):
    """'vermelho'/'amarelo'/'verde' -> 'Alto'/'Médio'/'Baixo' (Manejo,
    WhatsApp e PDF usam o mesmo rotulo pro Risco climático)."""
    return _RISCO_LABELS.get(risco)


def _calc_previsao_risco_germinacao(disease, weather):
    """Extensao do Risco de Infeccao (`_calc_risco_germinacao`) pros
    proximos 5 dias, pro WhatsApp e o PDF -- mesma conta do grafico de
    Risco de Infeccao da aba Graficos (`calc_risco_diario_pct`, que usa a
    previsao horaria da Open-Meteo em `previsao_horaria_por_dia`, ja
    incluida em `weather` por `_get_weather_for_site`), so' que o
    percentual e' convertido pro rotulo Baixo/Médio/Alto (mesmas faixas:
    100%+ = Alto, 50%+ = Medio, resto = Baixo) em vez do numero bruto --
    sem percentual nenhum aparecendo pro produtor. Os dias vem de
    `weather["previsao_5_dias"]` (NAO recalculados a partir de
    `_hoje_cuiaba()`) pra' bater exatamente com os mesmos dias da linha
    de previsao de chuva -- `_hoje_cuiaba()` assume um deslocamento fixo
    (UTC-4) igual pra toda fazenda, enquanto a Open-Meteo calcula o "hoje"
    de cada `previsao_5_dias` pelo fuso horario real da coordenada
    (`timezone: "auto"`); fazenda fora do fuso de Cuiaba divergia 1 dia
    entre as duas linhas. Retorna [] quando falta previsao ou limite
    cadastrado -- nesse caso quem chama cai de volta pro Risco de
    Infeccao "de agora" (`_calc_risco_germinacao`, baseado no horario ja
    observado)."""
    if not weather:
        return []
    previsao_dias = weather.get("previsao_5_dias") or []
    previsao_horas = weather.get("previsao_horaria_por_dia") or {}
    if not previsao_dias or not previsao_horas:
        return []
    resultado = []
    for dia in previsao_dias:
        data_iso = dia.get("data")
        horas = previsao_horas.get(data_iso)
        if not horas:
            continue
        pct = calc_risco_diario_pct(disease, horas)
        if pct is None:
            continue
        if pct >= 100:
            risco = "vermelho"
        elif pct >= 50:
            risco = "amarelo"
        else:
            risco = "verde"
        resultado.append({"data_fmt": f"{data_iso[8:10]}/{data_iso[5:7]}", "risco": risco})
    return resultado


def _doencas_manuais_do_site(site, safra=None):
    """Doencas cadastradas a mao na aba Doencas (sem leitura do BioScout)
    que valem pra essa fazenda, no formato minimo de card
    ({"doenca_en", "doenca"}) que `_build_riscos_climaticos_sem_leitura`
    usa -- mesmas regras de cultura (`_filter_cards_by_cultura`) e de pais
    (doenca sem pais marcado vale pra todos) das doencas do BioScout."""
    info = _disease_info_cached()
    cards = [{"doenca_en": en, "doenca": d["nome_pt"]} for en, d in info.items() if models.is_manual_disease(en)]
    if not cards:
        return []
    cache = getattr(g, "_doencas_manuais_ctx", None)
    if cache is None:
        cache = g._doencas_manuais_ctx = (
            models.get_all_farm_culturas(), models.get_doenca_culturas(),
            models.get_all_site_countries(), models.get_doenca_paises(),
        )
    culturas_by_site, doenca_culturas, site_countries, doenca_paises = cache
    cards = _filter_cards_by_cultura({site: cards}, culturas_by_site, doenca_culturas, safra=safra)[site]
    pais = countries.get_country(site_countries.get(site, countries.DEFAULT_COUNTRY))["nome"]
    return [c for c in cards if not doenca_paises.get(c["doenca_en"]) or pais in doenca_paises[c["doenca_en"]]]


def _com_doencas_manuais(site, safra, weather, riscos):
    """`riscos` (quadro "Risco de infeccao pelo clima") com TODAS as
    doencas cadastradas a mao que valem pra fazenda, inclusive risco
    baixo -- tela de Recomendacoes e PDF (pedido do usuario, 16/10/2026);
    o texto do WhatsApp continua so' com risco medio/alto. Doenca manual
    sem germinacao cadastrada fica de fora (nao da' pra calcular)."""
    manuais = _build_riscos_climaticos_sem_leitura(_doencas_manuais_do_site(site, safra))
    for d in manuais:
        d["risco"] = _calc_risco_germinacao(d, weather)
        d["previsao_risco"] = _calc_previsao_risco_germinacao(d, weather)
    return [d for d in (riscos or []) if not models.is_manual_disease(d["doenca_en"])] + manuais


def _riscos_climaticos_relevantes(cards, weather, site=None, safra=None):
    """`_build_riscos_climaticos_sem_leitura` ja' com `risco`/`previsao_risco`
    calculados (mesma `_calc_risco_germinacao`/`_calc_previsao_risco_germinacao`
    de `diseases`), filtrado pra quem esta em risco medio/alto agora OU
    em algum dia da previsao dos proximos 5 dias -- risco baixo o tempo
    todo nao e' acionavel, so' vira ruido no relatorio."""
    if site:
        cards = list(cards) + _doencas_manuais_do_site(site, safra)
    riscos = _build_riscos_climaticos_sem_leitura(cards)
    for d in riscos:
        d["risco"] = _calc_risco_germinacao(d, weather)
        d["previsao_risco"] = _calc_previsao_risco_germinacao(d, weather)
    return [
        d for d in riscos
        if d["risco"] in ("amarelo", "vermelho")
        or any(p["risco"] in ("amarelo", "vermelho") for p in (d.get("previsao_risco") or []))
    ]


def _riscos_climaticos_sem_alerta(cards, weather, diseases, site=None, safra=None):
    """Doencas da fazenda com risco de infeccao MEDIO/ALTO (agora ou em
    algum dos proximos 5 dias) que NAO estao em alerta de esporos
    (`diseases`, Atencao/Perigo) -- pedido do usuario (15/10/2026): toda
    doenca com risco alto/moderado tem que aparecer com o risco por dia,
    nao so' as com concentracao alta de esporos. Mesma conta e filtro de
    `_riscos_climaticos_relevantes`."""
    em_alerta = {d["doenca_en"] for d in diseases}
    return [d for d in _riscos_climaticos_relevantes(cards, weather, site, safra) if d["doenca_en"] not in em_alerta]


_STATUS_LABELS = {"Perigo": "Alta concentração de esporos", "Atencao": "Moderada concentração de esporos"}
# Mesma cor da bolinha do PDF (Perigo #ff6b6b -> vermelho, Atencao
# #e6ac00 -> laranja/amarelo), so' que em emoji pro WhatsApp (texto puro
# nao tem cor de verdade).
_STATUS_EMOJI = {"Perigo": "🔴", "Atencao": "🟠"}


def _status_label(status):
    """'Perigo'/'Atencao' -> 'Alta/Moderada concentração de esporos'
    (Manejo, WhatsApp e PDF usam o mesmo rotulo)."""
    return _STATUS_LABELS.get(status, status)


def _fmt_ingrediente(item, classe_label):
    if item.get("classe"):
        return f"{item['ingrediente']} ({classe_label.get(item['classe'], item['classe'])})"
    return item["ingrediente"]


_WHATSAPP_SEPARADOR = "━" * 15


def _nome_exibicao(site):
    """Nome "bonito" da fazenda pra mostrar em qualquer tela/relatorio --
    fonte UNICA usada em todo o site (Painel, Mapa, Manejo, WhatsApp, PDF,
    Graficos), pra uma troca na aba Fazendas refletir em todo lugar de
    uma vez so'. NUNCA usar pra casar com o CSV do BioScout/tabelas do
    banco -- so' pra EXIBICAO; o `site_name` real continua sendo a chave
    em todo lugar. Fazenda virtual/estimada usa o nome cadastrado direto
    no banco (`nome` em `virtual_farms`, editavel desde sempre em Mapa
    Interpolado) -- o padrao de site_name difere por tipo ('"{nome}" -
    OneAgro' pra doenca, '{nome} - Clima - OneAgro' pra clima, ver
    `models._virtual_farm_site_name`), entao so' esse lookup direto
    funciona pros dois sem quebrar. Fazenda real (site_name imutavel, e' a
    chave de casamento com o CSV do BioScout) usa a escolha manual da aba
    Fazendas (`site_display_names`) quando existir, senao cai no nome
    derivado do proprio site_name (tira o prefixo 'OneAgro - ', mesmo
    padrao de sempre). Chamada em loop sobre varias fazendas em quase
    toda tela -- em vez de reler as 2 tabelas inteiras do banco a cada
    fazenda, cacheia as duas em `flask.g` (reiniciado a cada
    requisicao, nunca serve dado de uma requisicao anterior) na
    primeira chamada dessa requisicao."""
    virtual_farms_by_site = getattr(g, "_virtual_farms_by_site", None)
    if virtual_farms_by_site is None:
        virtual_farms_by_site = {vf["site_name"]: vf for vf in models.get_all_virtual_farms()}
        g._virtual_farms_by_site = virtual_farms_by_site
    vf = virtual_farms_by_site.get(site)
    if vf:
        return vf["nome"]
    site_display_names = getattr(g, "_site_display_names", None)
    if site_display_names is None:
        site_display_names = models.get_all_site_display_names()
        g._site_display_names = site_display_names
    override = site_display_names.get(site)
    if override:
        return override
    return site.split(" - ", 1)[1] if " - " in site else site


def _whatsapp_titulo(site):
    """'OneAgro - Grupo PIVA' -> '*GRUPO PIVA - OneAgro*' -- nome da
    fazenda em destaque, sem repetir 'OneAgro' duas vezes."""
    return f"*{_nome_exibicao(site).upper()} - OneAgro*"


def _format_whatsapp_message(
    site, diseases, weather=None, produtos=None, is_virtual=False, cultura=None,
    safra_label=None, plantio_linhas=None, aplicacoes_linhas=None, leitura_bioscout=None,
    mostrar_secao_doencas=True, anotacao=None, riscos_climaticos=None,
    vento_dia=None, ponto_clima=False,
):
    """Monta o relatorio inteiro de uma fazenda numa unica mensagem de
    texto -- MESMO conteudo e ordem do PDF (`export_pdf.build_recommendation_pdf`),
    so' sem o graficozinho de concentracao (texto puro nao desenha grafico),
    agrupado POR DOENCA (status, contagem, recomendacao e observacao
    juntos num bloco so, em vez de secoes separadas repetindo o nome da
    doenca) -- mais facil do cliente ler e agir. `weather` e' o retorno de
    `_get_weather_for_site` (ou None); `produtos` e' {"quimico": [...],
    "biologico": [...]} com os itens que a fazenda ja tem comprado (aba
    Recomendacoes > Produtos Fazenda); `safra_label`/`plantio_linhas`/
    `aplicacoes_linhas` vem do mesmo lugar que alimenta o PDF (ver
    `_build_site_pdf`/`_farm_plantio_aplicacoes_estoque`) -- omitidos
    (None), a mensagem so' fica sem essas partes, sem quebrar.
    `leitura_bioscout` (dd/mm/aa, ja formatada) e' a data da ultima
    leitura de doenca do dispositivo BioScout -- None se nao houver
    nenhuma (ponto "so clima"). `anotacao` (texto livre, ver
    `models.get_site_climate_note`) aparece logo apos o clima, quando
    preenchida -- hoje so' usado pelos pontos "so clima" da aba Alertas
    Clima, mas funciona pra qualquer site. Sem
    nenhuma doenca em Atencao/Perigo, a mensagem e' o clima (ver
    `_linhas_clima`) seguido do aviso de que esta tudo tranquilo (mesmo
    texto usado na tela de Recomendacoes) -- e' o caminho SEMPRE usado
    pelos pontos "so clima" da aba Alertas Clima (nunca tem doenca), por
    isso o clima nao pode faltar aqui. `mostrar_secao_doencas=False`
    omite esse aviso "tudo tranquilo" (nao so' o do PDF, ver
    `export_pdf.build_recommendation_pdf`) -- usado quando `diseases`
    esta vazio por nao haver monitoramento de doenca de verdade
    acontecendo (ponto "so clima", ou fazenda real com estacao sem
    leitura ha muito tempo), pra nao sugerir que a fazenda foi checada e
    esta tudo bem quando na verdade nao foi checada. Esse caminho curto NAO repete
    Produtos (ao contrario do PDF, que sempre mostra essa secao), de proposito, pra' manter o aviso "esta tudo tranquilo"
    curto. Anotacoes das doencas vem num bloco so' ("Doenca: nota", so'
    as preenchidas), igual a caixa unica da tela de Recomendacoes.
    `is_virtual` (fazenda virtual/estimada, ver `virtual_farms.py`) so
    muda o jeito de extrair o nome do site_name pro titulo
    (`_whatsapp_titulo`) -- a mensagem em si nao tem nenhum aviso de que
    o valor e' estimado, pro relatorio ficar igual ao de uma fazenda
    real. `cultura` (nome da cultura atual definida na aba Manejo, ou
    None/"" se ainda estiver "(vazio)") aparece em destaque (negrito,
    maiuscula), logo abaixo do titulo, antes do clima. Ordem do
    relatorio (mesma da tela): titulo -> safra/cultura -> Clima agora ->
    Previsao de chuva -> Doencas em Atencao/Perigo (um bloco por doenca:
    nome, risco, recomendacao) -> Anotacoes -> Produtos disponiveis ->
    rodape. Datas de Plantio, Datas de Pulverizacao e o
    Risco de infeccao pelo clima NAO entram no texto (pedido do usuario,
    16/10/2026 -- o PDF continua com eles); `plantio_linhas`,
    `aplicacoes_linhas` e `riscos_climaticos` sao aceitos e ignorados. Rodape
    (so' o aviso de nao substituir agronomo -- pedido explicito do
    usuario pra' tirar fonte do clima/atualizado em/instituicoes
    consultadas/"Powered by BioScout" do relatorio de TEXTO, mantendo so'
    o aviso de responsabilidade; o PDF continua com essa info completa,
    ver `_build_site_pdf`/`export_pdf.build_recommendation_pdf`).
    `vento_dia` (retorno de `_rosa_ventos` do dia, ou None) entra no
    Clima agora como direcao predominante do vento. `ponto_clima=True`
    (pontos "so clima") tira o aviso de agronomo (pedido do Alan,
    19/10/2026)."""
    _AVISO_AGRONOMO = "Isso não substitui a avaliação de um agrônomo responsável."

    def _linhas_clima():
        """Clima agora + previsao de chuva (sem min/max, igual a tela/PDF)
        -- tambem usado no caminho curto (sem doenca em Atencao/Perigo,
        sempre o caso dos pontos "so clima" da aba Alertas Clima)."""
        linhas = []
        if weather:
            partes = []
            if weather.get("temperatura_atual") is not None:
                partes.append(f"🌡️ {weather['temperatura_atual']} °C")
            if weather.get("umidade_atual") is not None:
                partes.append(f"💧 {weather['umidade_atual']}%")
            if weather.get("chuva_atual_mm") is not None:
                partes.append(f"🌧️ {weather['chuva_atual_mm']} mm agora")
            if weather.get("chuva_24h_mm") is not None:
                partes.append(f"☔ {weather['chuva_24h_mm']} mm em 24h")
            linhas.append("🌤️ *Clima agora*")
            if partes:
                linhas.append(" · ".join(partes))
            if vento_dia:
                linhas.append(f"🧭 Vento predominante (hoje): de {vento_dia['nome']}")
            if weather.get("previsao_5_dias"):
                linhas.append("")
                linhas.append("🌧️ *Previsão de chuva*")
                linhas.extend(
                    f"{d['data'][8:10]}/{d['data'][5:7]}: {d['chuva_mm']} mm" for d in weather["previsao_5_dias"]
                )
        if leitura_bioscout:
            linhas.append("")
            linhas.append(f"_Última leitura BioScout: {leitura_bioscout}_")
        if anotacao:
            linhas.append("")
            linhas.append(f"📝 *Anotação:* {anotacao}")
        return linhas

    cabecalho = [_whatsapp_titulo(site)]
    subtitulo = " · ".join(
        parte for parte in (
            f"Recomendações {safra_label}" if safra_label else "",
            f"Cultura: {cultura.upper()}" if cultura else "",
        ) if parte
    )
    if subtitulo:
        cabecalho.append(f"_{subtitulo}_")
    cabecalho.append("")

    if not diseases:
        partes = cabecalho + _linhas_clima()
        if mostrar_secao_doencas:
            partes += ["", "✅ Nenhuma doença em atenção ou perigo nessa fazenda no momento."]
        if not ponto_clima:
            partes += ["", f"_{_AVISO_AGRONOMO}_"]
        return "\n".join(partes)

    lines = cabecalho + _linhas_clima()
    lines += ["", _WHATSAPP_SEPARADOR, "⚠️ *DOENÇAS EM ATENÇÃO / PERIGO*", "_Recomendações das instituições de pesquisa_"]

    for d in diseases:
        lines.append("")
        emoji_status = _STATUS_EMOJI.get(d["status"], "")
        titulo = f"{emoji_status} *{d['rotulo']}* — {_status_label(d['status']).lower()}"
        previsao_risco = d.get("previsao_risco")
        if not previsao_risco:
            # Sem previsao (falha da Open-Meteo, ou doenca sem limite
            # cadastrado) -- cai pro Risco de Infeccao "de agora", baseado
            # no horario ja observado (`_calc_risco_germinacao`).
            risco_label = _risco_label(d.get("risco"))
            if risco_label:
                titulo += f"\nRisco de infecção: {risco_label}"
        lines.append(titulo)
        if previsao_risco:
            lines.append("Risco de infecção: " + " · ".join(
                f"{_RISCO_EMOJI.get(p['risco'], '')} {p['data_fmt']} {_risco_label(p['risco'])}" for p in previsao_risco
            ))
        biologicos_itens = d["biologicos"]["itens"][:3] if d["biologicos"] else None
        quimicos_itens = d["quimicos"]["itens"][:3] if d["quimicos"] else None
        if biologicos_itens:
            lines.append("🧪 Biológicos: " + " // ".join(_fmt_ingrediente(p, d["classe_label"]) for p in biologicos_itens))
        if quimicos_itens:
            lines.append("⚗️ Químicos: " + " // ".join(_fmt_ingrediente(p, d["classe_label"]) for p in quimicos_itens))

    # Anotacoes das doencas num bloco so' (igual a caixa unica da tela),
    # so' as preenchidas.
    anotacoes = [(d["rotulo"], d.get("nota")) for d in diseases if (d.get("nota") or "").strip()]
    if anotacoes:
        lines += ["", _WHATSAPP_SEPARADOR, "📝 *ANOTAÇÕES*"]
        lines += [f"• *{rotulo}:* {nota.strip()}" for rotulo, nota in anotacoes]

    produtos = produtos or {}
    linhas_produtos = []
    for tipo, titulo, emoji in (("biologico", "Biológicos", "🧪"), ("quimico", "Químicos", "⚗️")):
        nomes = [
            f"{p['nome']} ({p['data_anotacao']})" if p.get("data_anotacao") else p["nome"]
            for p in (produtos.get(tipo) or []) if p.get("nome")
        ]
        if nomes:
            linhas_produtos.append(f"{emoji} {titulo}: {', '.join(nomes)}")
    lines += ["", _WHATSAPP_SEPARADOR, "📦 *PRODUTOS DISPONÍVEIS NA FAZENDA*"]
    lines += linhas_produtos or ["Nenhum produto cadastrado."]

    lines += ["", f"_{_AVISO_AGRONOMO}_"]
    return "\n".join(lines).strip()


def _site_whatsapp_destinations(site):
    """Lista de (phone, rotulo) que devem receber relatorio dessa fazenda --
    gerada a partir do cadastro de usuario (`get_site_whatsapp_recipients`:
    quem tiver essa fazenda marcada na coluna "Receber relatorios" da aba
    Usuarios E ja tiver telefone cadastrado -- independente de admin ou de
    acesso normal a fazenda). O envio de verdade sai sempre do mesmo
    numero (o WhatsApp do administrador, pareado no servico
    whatsapp-bridge) -- nao tem mais "numero global" separado, ja que so
    existe um remetente agora."""
    return [(r["telefone"], r["username"]) for r in models.get_site_whatsapp_recipients(site)]


# Intervalo minimo entre 2 mensagens pro MESMO numero -- usado em 3
# lugares: entre destinatarios da MESMA fazenda no envio manual
# (`_enviar_whatsapp_manual_escalonado`) e no agendado
# (`_horario_efetivo_destinatario`), E entre fazendas DIFERENTES que
# mandam pro MESMO numero no agendado (`_horarios_efetivos_por_telefone`).
# Mandar pra' varios numeros NOVOS em sequencia rapida, ou mandar VARIAS
# mensagens novas pro MESMO numero em sequencia rapida (mesmo vindas de
# fazendas diferentes) -- mesmo com o espacamento de MIN_DELAY_MS do
# bridge entre cada mensagem individual -- e' um padrao de RAJADA que o
# WhatsApp tambem penaliza retendo a entrega. Constatado em 2026-09-16:
# numa fazenda com 5 destinatarios, so' o 1o (mandado na hora) chegou,
# os outros 4 (mandados em sequencia, minutos depois) ficaram presos em
# "Aguardando mensagem". Constatado de novo em 2026-09-17: um numero que
# recebe de VARIAS fazendas (9 fazendas, horarios auto-sugeridos so' 3
# min um do outro) toma a MESMA rajada, mesmo sem nenhuma fazenda ter
# mais de 1 destinatario. Espalhar por HORAS (nao so' segundos) quebra
# esse padrao de rajada nos dois casos.
# Substituido em 16/10/2026 (pedido do usuario): em vez de espalhar por
# horas, todo envio sai um de cada vez com 15s entre um e outro
# (`whatsapp.INTERVALO_ENTRE_ENVIOS_S`). 0 desliga o escalonamento por hora
# sem mexer na logica de agenda/dedup por telefone que usa esta constante.
RECIPIENT_STAGGER_HORAS = 0
JANELA_ENVIO_AGENDADO_MIN = 60
# Nenhum envio AUTOMATICO sai antes disso (minutos do dia, Cuiaba) -- a
# primeira leitura de clima do dia e' as 04:45 (weather_forecast.
# HORARIOS_LEITURA); antes de 5h30 o relatorio sairia com o clima das
# 17h45 do dia anterior. Pedido do usuario (15/10/2026). Horario configurado
# antes disso passa a valer 5h30 (o escalonamento por destinatario/
# telefone continua somando a partir dai').
ENVIO_AUTOMATICO_MINIMO_MIN = 5 * 60 + 30


def _enviar_whatsapp_manual_escalonado(site, safra, enviar_texto, enviar_pdf, destinos=None):
    """Envio manual (botao "Enviar por WhatsApp") pra' uma fazenda: manda
    pra cada destinatario em sequencia, numa thread de fundo -- a tela
    responde na hora, e cada mensagem sai so' 15s depois da anterior
    terminar, sem nunca coincidir com outro envio (agendado ou de outra
    fazenda), ver `whatsapp._um_envio_por_vez`. Sai SO' neste clique --
    nao existe fila que dispare depois. `destinos` [(telefone, rotulo)] = so' os escolhidos na tela
    (e/ou um numero digitado); None = todos os cadastrados da fazenda.
    Retorna (ok_texto, ok_pdf, mensagem-resumo), mesma assinatura de
    `_send_site_whatsapp`."""
    if destinos is None:
        destinos = _site_whatsapp_destinations(site)
    if not destinos:
        return _send_site_whatsapp(site, safra=safra, enviar_texto=enviar_texto, enviar_pdf=enviar_pdf)

    def enviar_todos():
        with app.app_context():
            for telefone, rotulo in destinos:
                try:
                    _send_site_whatsapp(
                        site, safra=safra, enviar_texto=enviar_texto, enviar_pdf=enviar_pdf,
                        destinos_override=[(telefone, rotulo)],
                    )
                except Exception as exc:
                    models.log_whatsapp_envio(site, rotulo, telefone, False, f"erro inesperado: {exc}")

    threading.Thread(target=enviar_todos, daemon=True).start()
    nomes = ", ".join(rotulo for _, rotulo in destinos)
    return True, True, (
        f"{len(destinos)} destinatario(s) ({nomes}) -- sai um de cada vez, "
        f"{whatsapp.INTERVALO_ENTRE_ENVIOS_S}s entre mensagens"
    )


def _all_farm_produtos_cached():
    """Cache por requisicao (ver `_fungicida_ordem_all_cached`) de
    `models.get_all_farm_produtos()` -- `_farm_produtos_estoque` e'
    chamado uma vez POR FAZENDA na aba Recomendacoes, reabrindo conexao
    pra reler o produto de TODAS as fazendas a cada chamada."""
    val = getattr(g, "_all_farm_produtos", None)
    if val is None:
        val = models.get_all_farm_produtos()
        g._all_farm_produtos = val
    return val


def _farm_produtos_estoque(site, safra):
    """{"quimico": [...], "biologico": [...]} com os itens que a fazenda
    ja tem comprado (aba Recomendacoes > Produtos Fazenda -- mesma
    secao Folha da aba Fazendas, momento "folha") -- sem `safra` (envio
    agendado), junta os itens das duas safras."""
    existentes = _all_farm_produtos_cached().get(site, {})
    safras_a_considerar = [safra] if safra else [s for s, _ in models.SAFRAS]
    produtos = {}
    for tipo in models.TIPOS_PRODUTO:
        itens = []
        for s in safras_a_considerar:
            itens.extend(existentes.get((s, "folha", tipo), []))
        produtos[tipo] = itens
    return produtos


def _farm_plantio_aplicacoes_estoque(site, safra):
    """(plantio_linhas, aplicacoes_linhas) pro PDF -- mesma logica de uniao
    entre safras que `_farm_produtos_estoque` ja faz quando `safra` e' None
    (envio agendado, sem uma safra especifica escolhida)."""
    plantio_por_safra = models.get_all_farm_plantio().get(site, {})
    aplicacoes_por_safra = models.get_all_farm_aplicacoes().get(site, {})
    safras_a_considerar = [safra] if safra else [s for s, _ in models.SAFRAS]
    plantio, aplicacoes = [], []
    for s in safras_a_considerar:
        plantio.extend(plantio_por_safra.get(s, []))
        aplicacoes.extend(aplicacoes_por_safra.get(s, []))
    return plantio, aplicacoes


def _no_brasil(lat, lon):
    """Sinal de celular so' no Brasil (mapas da Anatel; pedido do Alan,
    01/11/2026) -- ponto solto (clique no mapa, lavoura de cliente) pelo
    contorno dos paises cadastrados."""
    return countries.detectar_pais_por_coordenada(lat, lon) == countries.DEFAULT_COUNTRY


def _sinal_das_fazendas(sites):
    """site -> sinal de celular (`sinal_celular.estimar`) das fazendas
    cadastradas no Brasil -- fazenda de outro pais fica de fora. As
    frequencias que faltam sao consultadas em segundo plano."""
    coords = _coords_all()
    paises = models.get_all_site_countries()
    resultado, faltando = {}, []
    for site in sites:
        if site not in coords or paises.get(site, countries.DEFAULT_COUNTRY) != countries.DEFAULT_COUNTRY:
            continue
        lat, lon = coords[site]
        sinal = sinal_celular.estimar(lat, lon, buscar_frequencias=False)
        resultado[site] = sinal
        if sinal and any(o["nivel"] != "sem" for o in sinal) and sinal_celular.frequencias_salvas(lat, lon) is None:
            faltando.append((lat, lon))
    sinal_celular.buscar_frequencias_em_segundo_plano(faltando)
    return resultado


def _historico_doenca(site, doenca_en, dias=30):
    """Serie diaria de esporos pro grafico do PDF. Fazenda real: as
    leituras dela (`data_reader.get_site_disease_history`). Fazenda
    estimada (Mapa Interpolado / lavoura de cliente): o mesmo IDW dia a
    dia da aba Graficos, a partir das fazendas reais dentro do raio
    (pedido do Alan, 01/11/2026), terminando no dia da leitura mais
    recente dessas vizinhas."""
    vf = models.get_virtual_farm(site)
    if not vf:
        return data_reader.get_site_disease_history(site, doenca_en, dias=dias)
    if vf.get("tipo") == "clima":
        return []
    vizinhas = virtual_farms.estacoes_no_raio(vf["lat"], vf["lon"], vf["raio_km"], data_reader.read_site_coordinates())
    lookup = data_reader.build_disease_concentration_lookup(data_reader.read_spore_counts())
    por_vizinha = {real: {h["data"]: h for h in lookup.get((real, doenca_en), [])} for real, _ in vizinhas}
    com_dado = [(max(m), real) for real, m in por_vizinha.items() if m]
    if not com_dado:
        return []
    ultima_iso, real_mais_recente = max(com_dado)
    limites = {k: por_vizinha[real_mais_recente][ultima_iso].get(k) for k in ("warn", "danger", "maximo")}
    ultima = datetime.fromisoformat(ultima_iso).date()
    dias_iso = [(ultima - timedelta(days=dias - 1 - i)).isoformat() for i in range(dias)]
    serie = virtual_farms.interpolar_serie_diaria(
        vizinhas, dias_iso, lambda real, dia: (por_vizinha.get(real, {}).get(dia) or {}).get("concentracao"),
    )
    return [
        {"data": dia, "concentracao": round(v, 1) if v is not None else None, **limites}
        for dia, v in zip(dias_iso, serie)
    ]


def _build_site_pdf(
    site, diseases, weather, produtos, cultura, safra, leitura_bioscout=None,
    mostrar_secao_doencas=True, anotacao=None, riscos_climaticos=None,
):
    """Gera o mesmo PDF do botao "Recomendacao (PDF)" -- usado tanto pelo
    download manual (`recommendation_pdf`) quanto pelo envio automatico
    junto do WhatsApp (`_send_site_whatsapp`). `safra=None` (envio
    agendado, sem uma safra especifica) usa a uniao das duas safras pros
    dados de plantio/aplicacao, e um rotulo generico no lugar do nome da
    safra. `leitura_bioscout` (dd/mm/aa, ja formatada) e' a data da
    ultima leitura de doenca do dispositivo BioScout -- None quando nao
    houver nenhuma (ponto "so clima" da aba Alertas Clima, por exemplo).
    `mostrar_secao_doencas=False` omite a secao "Doencas em Atencao/
    Perigo" do PDF -- ver `export_pdf.build_recommendation_pdf`.
    `anotacao` (texto livre, ver `models.get_site_climate_note`) aparece
    logo apos o clima. `riscos_climaticos` (ver
    `app._riscos_climaticos_relevantes`) e' o aviso de risco climatico
    sem leitura confirmada, independente de `mostrar_secao_doencas`."""
    vf = models.get_virtual_farm(site)
    if not (vf and vf.get("tipo") == "clima"):
        riscos_climaticos = _com_doencas_manuais(site, safra, weather, riscos_climaticos)
    hoje = weather_forecast._agora_cuiaba().date()
    rosa = _rosa_ventos(site, [(hoje - timedelta(days=i)).isoformat() for i in range(30)])
    if weather and rosa:
        weather = {**weather, "vento_30d": rosa}
    for d in diseases:
        d["historico"] = _historico_doenca(site, d["doenca_en"], dias=30)
    plantio_linhas, aplicacoes_linhas = _farm_plantio_aplicacoes_estoque(site, safra)
    nome_fazenda = _nome_exibicao(site)
    safra_label = SAFRA_LABELS[safra] if safra else "todas as safras"
    rodape_data = f"Extraído em {weather_forecast._agora_cuiaba().strftime('%d/%m/%Y %H:%M')}"
    buffer = export_pdf.build_recommendation_pdf(
        nome_fazenda, safra_label, diseases, weather=weather, produtos=produtos,
        cultura=cultura, plantio_linhas=plantio_linhas, aplicacoes_linhas=aplicacoes_linhas,
        rodape_data=rodape_data, leitura_bioscout=leitura_bioscout,
        mostrar_secao_doencas=mostrar_secao_doencas, anotacao=anotacao,
        riscos_climaticos=riscos_climaticos,
    )
    filename = f"Recomendacao_{nome_fazenda}_{datetime.now().strftime('%Y%m%d')}.pdf".replace(" ", "_")
    return nome_fazenda, filename, buffer.getvalue()


def _send_site_whatsapp(site, safra=None, enviar_texto=True, enviar_pdf=True, destinos_override=None):
    """Monta o relatorio de uma fazenda (Dados de Clima, Fungos em alta
    quantidade, Recomendacoes das Instituicoes de Pesquisa, Produtos
    Fazenda e Anotacoes -- ver `_format_whatsapp_message`) e manda pra
    TODOS os numeros cadastrados que devem recebe-la (uma fazenda pode
    ter varios -- `_site_whatsapp_destinations`), pelo WhatsApp do
    administrador (whatsapp-bridge). `enviar_texto`/`enviar_pdf`
    controlam quais dos dois formatos mandar -- essa e' a UNICA rotina de
    envio de WhatsApp do sistema: o botao manual "Enviar por WhatsApp"
    chama com os dois True (manda ambos na hora); o envio agendado
    semanal (`_run_scheduled_whatsapp_sends`) decide cada flag
    separadamente, com base em qual agenda (texto/PDF, cada uma com seus
    proprios dias da semana -- `models.get_all_whatsapp_days`/
    `get_all_whatsapp_days_pdf`) bate com o dia de hoje pra essa fazenda.
    Manda mesmo quando a fazenda nao tem nenhuma doenca em Atencao/Perigo
    no momento -- nesse caso a mensagem e' so o aviso de que esta tudo
    tranquilo (ver `_format_whatsapp_message`). Retorna (ok_texto,
    ok_pdf, mensagem-resumo) -- ok_texto/ok_pdf sao True quando aquele
    formato NEM foi pedido (nada a falhar) OU pelo menos 1 numero
    recebeu ele. Separados (em vez de um "ok" so') porque
    `_run_scheduled_whatsapp_sends` precisa marcar texto/PDF como
    "enviado hoje" de forma independente -- juntar os dois faria uma
    falha isolada no PDF (com o texto ok) fazer o agendador reenviar o
    texto de novo no proximo minuto (duplicado), ou o contrario esconder
    uma falha real de PDF atras de um texto que deu certo.
    `safra` filtra pela cultura daquela safra (quando chamado a partir de
    uma das telas de Recomendacoes) e define de qual safra vem os
    "Produtos Fazenda"; sem `safra` (envio agendado), usa a uniao das
    culturas e dos produtos das duas safras. Quando a estacao dessa
    fazenda esta sem leitura nova ha mais de `DADOS_BLOQUEIO_DIAS` (dado
    de doenca velho demais pra virar recomendacao), NAO bloqueia mais o
    envio -- em vez disso manda um relatorio SO' de clima, exatamente
    igual ao dos pontos "so clima" da aba Alertas Clima (`diseases=[]`,
    mesmo caminho curto de `_format_whatsapp_message`/`_build_site_pdf`),
    pedido explicito do usuario (antes nao mandava nada nesse caso).
    Quando os dois formatos sao mandados juntos (texto E pdf True nessa
    chamada) e o texto cabe na legenda do WhatsApp, manda os dois
    COMBINADOS num unico envio (PDF com o texto como legenda) em vez de
    2 mensagens separadas pro mesmo numero -- ver LEGENDA_MAX_CHARS mais
    abaixo. Reduz o risco de o WhatsApp reter a entrega por padrao de
    envio automatizado (2+ mensagens em sequencia pro mesmo numero).
    `destinos_override` manda so' pra essa lista especifica de
    (telefone, rotulo) em vez de todo mundo cadastrado pra essa fazenda
    (`_site_whatsapp_destinations`) -- usado pelo envio manual (um
    destinatario de cada vez, ver `_enviar_whatsapp_manual_escalonado`)
    e pelo agendado."""
    if not enviar_texto and not enviar_pdf:
        return False, False, "Nada a enviar (nem texto nem PDF agendado pra hoje)."
    translations = _load_translations()
    raw_cards = _resolve_site_cards(site, translations)
    dias_sem_leitura = _dias_sem_leitura(raw_cards)
    leitura_bioscout = models.fmt_data_br(max((c["data"] for c in raw_cards), default=None))
    culturas_by_site = models.get_all_farm_culturas()
    cultura = _cultura_label(site, safra, culturas_by_site)
    vf = models.get_virtual_farm(site)
    is_virtual = vf is not None
    is_clima_point = is_virtual and vf.get("tipo") == "clima"
    bloqueado = _nivel_dados_site(site, raw_cards) == "bloqueado"
    if (bloqueado or is_clima_point) and enviar_pdf:
        # Fazenda sem leitura recente / ponto so' clima: sem PDF (pedido do
        # Alan). PDF "ok" pro agendador nao tentar de novo a cada minuto.
        enviar_pdf = False
        if not enviar_texto:
            return False, True, "Sem PDF para fazenda sem leitura recente ou ponto so' clima."
    # Sem secao de doenca no relatorio (texto/PDF) quando nao ha'
    # monitoramento de doenca de verdade acontecendo -- ponto "so clima"
    # (nunca monitora) ou fazenda real com estacao sem leitura ha muito
    # tempo (dado velho demais pra confiar) -- senao o relatorio parece
    # dizer "tudo tranquilo" quando na verdade a fazenda nao foi checada.
    mostrar_secao_doencas = not is_clima_point and not bloqueado
    coords = _weather_coords_all()
    weather = _get_weather_for_site(site, coords)
    riscos_climaticos = []
    if bloqueado:
        diseases = []
        # Sem leitura confirmada pra montar "Doencas em Atencao/Perigo",
        # mas o risco CLIMATICO (so' clima x germinacao, nunca depende
        # do sensor) continua valido -- avisa mesmo sem confirmar a
        # doenca, pedido explicito do usuario.
        riscos_climaticos = _riscos_climaticos_relevantes(raw_cards, weather, site, safra)
    else:
        cards_by_site = _filter_cards_by_cultura(
            {site: raw_cards}, culturas_by_site, models.get_doenca_culturas(), safra=safra
        )
        cards = cards_by_site.get(site, [])
        notes = models.get_all_recommendation_notes()
        diseases = _build_site_diseases(site, cards, notes, cultura=cultura)

    for d in diseases:
        d["risco"] = _calc_risco_germinacao(d, weather)
        d["previsao_risco"] = _calc_previsao_risco_germinacao(d, weather)
    if not bloqueado and not is_clima_point:
        riscos_climaticos = _riscos_climaticos_sem_alerta(cards, weather, diseases, site, safra)
    produtos = _farm_produtos_estoque(site, safra)

    destinos = destinos_override if destinos_override is not None else _site_whatsapp_destinations(site)
    if not destinos:
        # NAO loga no historico (`models.log_whatsapp_envio`) -- fazenda
        # sem ninguem cadastrado pra receber nao e' uma FALHA de envio
        # (nada foi tentado), e' uma lacuna de cadastro. Logar isso
        # misturava com falhas de verdade no relatorio "Envios por
        # WhatsApp" (aba Exportar), inflando a contagem de falhas com
        # fazendas que nunca deveriam ter sido contadas ali. O motivo
        # ainda aparece pra quem clicou manualmente (flash da rota),
        # so' nao fica persistido no log.
        motivo = f"Nenhum usuario marcado pra receber relatorio de '{site}' (ou nenhum tem telefone cadastrado)."
        return False, False, motivo

    text = None
    if enviar_texto:
        safra_label = SAFRA_LABELS[safra] if safra else "todas as safras"
        plantio_linhas, aplicacoes_linhas = _farm_plantio_aplicacoes_estoque(site, safra)
        text = _format_whatsapp_message(
            site, diseases, weather=weather, produtos=produtos, is_virtual=is_virtual, cultura=cultura,
            safra_label=safra_label, plantio_linhas=plantio_linhas, aplicacoes_linhas=aplicacoes_linhas,
            leitura_bioscout=leitura_bioscout, mostrar_secao_doencas=mostrar_secao_doencas,
            riscos_climaticos=riscos_climaticos,
            vento_dia=_rosa_ventos(site, [weather_forecast._agora_cuiaba().date().isoformat()]) if is_clima_point else None,
            ponto_clima=is_clima_point,
        )

    # PDF (mesmo conteudo do texto, mais o grafico de concentracao) e'
    # gerado uma vez so' e mandado como documento pra cada destinatario --
    # uma falha ao gerar o PDF NAO impede o envio do texto (so' fica
    # registrada no log).
    modelo_oficial = whatsapp.usa_modelo_oficial()
    pdf_nome_fazenda = pdf_filename = pdf_bytes = None
    if enviar_pdf or modelo_oficial:
        try:
            pdf_nome_fazenda, pdf_filename, pdf_bytes = _build_site_pdf(
                site, diseases, weather, produtos, cultura, safra, leitura_bioscout=leitura_bioscout,
                mostrar_secao_doencas=mostrar_secao_doencas,
                riscos_climaticos=riscos_climaticos,
            )
        except Exception as exc:
            models.log_whatsapp_envio(site, None, None, False, f"Falha ao gerar PDF pra WhatsApp: {exc}")

    # Quando texto E PDF caem no mesmo envio, manda os dois JUNTOS (PDF
    # com o texto inteiro como legenda) em vez de 2 mensagens separadas
    # pro mesmo numero -- 2 mensagens automatizadas em sequencia pro
    # mesmo destinatario e' exatamente o padrao que o WhatsApp penaliza
    # retendo a entrega (ver comentario de MIN_DELAY_MS em
    # whatsapp-bridge/index.js; aconteceu de novo em 2026-09-11 mesmo com
    # o espacamento de 15-25s ja' em vigor). So' combina quando o texto
    # cabe no limite de legenda do WhatsApp (~1024 caracteres) -- um
    # relatorio maior que isso continua mandando os dois separados, pra
    # nunca cortar/perder conteudo.
    LEGENDA_MAX_CHARS = 1024
    combinar = bool(text) and bool(pdf_bytes) and len(text) <= LEGENDA_MAX_CHARS
    if modelo_oficial:
        variaveis_base = _variaveis_modelo_relatorio(site, diseases, mostrar_secao_doencas, riscos_climaticos)

    sucesso, falha = [], []
    # Rastreados SEPARADOS de "sucesso" (que exige os dois formatos
    # pedidos nessa chamada terem funcionado) -- um numero pode falhar so
    # no PDF com o texto ok, e isso ainda precisa contar como "texto
    # enviado" pro agendador nao mandar o texto de novo (ver retorno).
    algum_texto_ok = False
    algum_pdf_ok = False
    for phone, rotulo in destinos:
        erros = []
        ok_texto = True
        ok_pdf = True
        if modelo_oficial:
            # API oficial: um envio so' pelo modelo aprovado (resumo nas
            # variaveis + PDF completo no cabecalho) -- cobre texto e PDF.
            primeiro_nome = (rotulo or "").split()[0] if rotulo else "produtor"
            ok_pdf, message_pdf = whatsapp.send_relatorio_modelo(
                phone, pdf_bytes, pdf_filename, [primeiro_nome] + variaveis_base,
            )
            ok_texto = ok_pdf
            models.log_whatsapp_envio(site, rotulo, phone, ok_pdf, f"Modelo oficial (resumo+PDF): {message_pdf}")
            if not ok_pdf:
                erros.append(message_pdf)
        elif combinar:
            ok_pdf, message_pdf = whatsapp.send_whatsapp_document(phone, pdf_bytes, pdf_filename, caption=text)
            ok_texto = ok_pdf  # combinado -- o mesmo envio carrega os dois
            models.log_whatsapp_envio(site, rotulo, phone, ok_pdf, f"Texto+PDF combinado num so envio: {message_pdf}")
            if not ok_pdf:
                erros.append(f"PDF: {message_pdf}")
        else:
            if text is not None:
                ok_texto, message = whatsapp.send_whatsapp(phone, text)
                models.log_whatsapp_envio(site, rotulo, phone, ok_texto, message)
                if not ok_texto:
                    erros.append(message)
            if pdf_bytes:
                ok_pdf, message_pdf = whatsapp.send_whatsapp_document(
                    phone, pdf_bytes, pdf_filename,
                    caption=f"📄 Relatório em PDF - {pdf_nome_fazenda} (mesmo conteudo, com grafico de concentracao)",
                )
                models.log_whatsapp_envio(site, rotulo, phone, ok_pdf, f"PDF: {message_pdf}")
                if not ok_pdf:
                    erros.append(f"PDF: {message_pdf}")
        if enviar_texto and ok_texto:
            algum_texto_ok = True
        if enviar_pdf and ok_pdf:
            algum_pdf_ok = True
        (sucesso if ok_texto and ok_pdf else falha).append(rotulo if (ok_texto and ok_pdf) else f"{rotulo} ({'; '.join(erros)})")
    resumo = f"{len(sucesso)}/{len(destinos)} numero(s)"
    if falha:
        resumo += f" -- falha em: {', '.join(falha)}"
    ok_texto_geral = (not enviar_texto) or algum_texto_ok
    ok_pdf_geral = (not enviar_pdf) or algum_pdf_ok
    return ok_texto_geral, ok_pdf_geral, resumo


def _variaveis_modelo_relatorio(site, diseases, mostrar_secao_doencas, riscos_climaticos=()):
    """Variaveis {{2}}..{{4}} do modelo do relatorio na API oficial
    (`whatsapp_cloud_api.TEMPLATE_RELATORIO`; {{1}} e' o nome de quem
    recebe): fazenda, data e TODAS as doencas que pedem atencao -- as em
    alerta de esporos (nivel + risco de infeccao de cada uma) e as com
    risco de infeccao medio/alto pelo clima (`riscos_climaticos`, maior
    risco entre hoje e os proximos dias). Pedido do usuario (15/10/2026).
    O relatorio completo (risco dia a dia) vai no PDF do cabecalho."""
    ordem = {"vermelho": 3, "amarelo": 2, "verde": 1}
    partes = []
    for d in (diseases if mostrar_secao_doencas else []):
        nivel = "Perigo" if d["status"] == "Perigo" else "Atenção"
        risco = _risco_label(d.get("risco"))
        partes.append(f"{d['doenca']} ({nivel}" + (f", risco de infecção {risco})" if risco else ")"))
    for d in riscos_climaticos or []:
        riscos = [d.get("risco")] + [p.get("risco") for p in d.get("previsao_risco") or []]
        maior = max((r for r in riscos if r), key=lambda r: ordem.get(r, 0), default=None)
        if maior in ("amarelo", "vermelho"):
            partes.append(f"{d['doenca']} (risco de infecção {_risco_label(maior)})")
    if partes:
        doencas = "; ".join(partes)
    elif mostrar_secao_doencas:
        doencas = "Nenhuma doença em alerta"
    else:
        doencas = "Sem monitoramento de doenças (só clima)"
    return [_nome_exibicao(site), _hoje_cuiaba().strftime("%d/%m/%Y"), doencas]


_SCHEDULER_STATE_KEY = "whatsapp_scheduler_state"


def _load_scheduler_enviados(today):
    """Conjunto de "site::telefone::texto"/"site::telefone::pdf" que ja'
    receberam o envio agendado HOJE -- persistido no banco
    (`models.get_setting`/`app_settings`), nao numa variavel em memoria.
    Uma variavel em memoria zera sozinha se o processo reiniciar
    (deploy, crash, worker reciclado) no MEIO do dia, fazendo o
    agendador reenviar de novo pra quem ja' tinha recebido -- por isso o
    estado precisa sobreviver a reinicio. Chave por TELEFONE (nao pelo
    indice/posicao do destinatario, mesmo esse indice sendo usado pra
    calcular o horario escalonado de cada um -- ver
    `_horario_efetivo_destinatario`): a posicao na lista alfabetica
    (`_site_whatsapp_destinations`) pode mudar no MEIO do dia se algum
    destinatario for adicionado/removido daquela fazenda, e uma chave
    baseada em posicao ficaria associada a pessoa errada depois disso
    (reenvio duplicado pra quem ja' recebeu, ou fazenda nunca marcada
    como enviada pra quem esta' esperando). Cada destinatario da mesma
    fazenda tem seu proprio horario efetivo (escalonado) e texto/PDF
    continuam podendo ter horarios BASE diferentes tambem -- uma fazenda
    pode ja' ter mandado o texto pro 1o destinatario e ainda estar
    esperando a vez do 2o, ou esperando o PDF de todos, no MESMO dia.
    Reseta sozinho quando o dia muda (registro salvo de um dia anterior
    e' ignorado)."""
    bruto = models.get_setting(_SCHEDULER_STATE_KEY)
    if bruto:
        try:
            estado = json.loads(bruto)
        except ValueError:
            estado = {}
        if estado.get("date") == today.isoformat():
            return set(estado.get("enviados", []))
    return set()


def _salvar_scheduler_enviados(today, enviados):
    models.set_setting(_SCHEDULER_STATE_KEY, json.dumps({"date": today.isoformat(), "enviados": sorted(enviados)}))


def _horario_efetivo_destinatario(hora_site, tipo, idx):
    """(hora, minuto) em que o destinatario de indice `idx` (0 = primeiro
    de `_site_whatsapp_destinations`, ordem alfabetica) dessa fazenda
    deve receber o envio agendado -- o horario configurado pra' essa
    fazenda/tipo ("texto" ou "pdf") mais `idx * RECIPIENT_STAGGER_HORAS`.
    Mandar pra' varios destinatarios NOVOS da MESMA fazenda em sequencia
    rapida e' rajada (ver RECIPIENT_STAGGER_HORAS) mesmo respeitando o
    espacamento entre mensagens individuais do bridge -- por isso cada
    destinatario ganha seu proprio horario, nao so' os das fazendas
    diferentes. Clampado em 23h59 (nunca ultrapassa o dia) -- uma
    fazenda com base tarde + muitos destinatarios fica com os ultimos
    todos as 23h59 em vez de nunca disparar (hora >= 24 nunca bateria
    com `agora_hm`, travando pra sempre)."""
    base_minutos = max(hora_site[tipo] * 60 + hora_site[f"{tipo}_min"], ENVIO_AUTOMATICO_MINIMO_MIN)
    total_minutos = min(base_minutos + idx * RECIPIENT_STAGGER_HORAS * 60, 23 * 60 + 59)
    return total_minutos // 60, total_minutos % 60


def _horarios_efetivos_por_telefone(candidatos):
    """Recebe a lista de candidatos de hoje (cada um com `telefone` e
    `desejado_min`, minutos desde meia-noite) e devolve a MESMA lista
    com `efetivo_min` preenchido -- agrupa por telefone, ordena pelo
    horario desejado de cada um, e caminha em sequencia garantindo pelo
    menos `RECIPIENT_STAGGER_HORAS` de intervalo entre 2 itens
    consecutivos do MESMO numero, sejam eles de fazendas diferentes ou
    da mesma fazenda. So' EMPURRA pra frente (nunca antecipa) -- itens
    ja' bem espacados (fazendas com horarios bem diferentes configurados
    a mao) mantem o horario configurado deles. Constatado em 2026-09-17:
    um numero que recebe relatorio de VARIAS fazendas (ex.: 9 fazendas,
    cada uma com seu proprio horario auto-sugerido so' 3 minutos
    diferente da vizinha) tomava uma rajada de varias mensagens em
    poucos minutos -- rajada o suficiente pro WhatsApp reter a entrega,
    mesmo sem nenhuma fazenda tendo mais de 1 destinatario (esse outro
    caso, dentro da MESMA fazenda, ja' e' tratado por
    `_horario_efetivo_destinatario`, que so' calcula o horario
    "desejado" de entrada aqui)."""
    por_telefone = {}
    for c in candidatos:
        por_telefone.setdefault(c["telefone"], []).append(c)
    for telefone, itens in por_telefone.items():
        itens.sort(key=lambda c: c["desejado_min"])
        efetivo_anterior = None
        for item in itens:
            efetivo = item["desejado_min"]
            if efetivo_anterior is not None:
                efetivo = max(efetivo, efetivo_anterior + RECIPIENT_STAGGER_HORAS * 60)
            efetivo = min(efetivo, 23 * 60 + 59)
            efetivo_anterior = efetivo
            item["efetivo_min"] = efetivo
    return candidatos


def _run_scheduled_whatsapp_sends():
    """Roda a cada minuto (`_whatsapp_scheduler_loop`) -- texto e PDF tem
    cada um sua propria agenda de dias da semana E seu proprio horario
    BASE (hora E minuto, aba Fazendas, `models.get_all_whatsapp_schedule_horarios`
    -- individual por fazenda, pedido explicito do usuario pra poder
    espalhar os envios ao longo do dia em vez de todo mundo mandar no
    mesmo horario e parecer um robo). Uma fazenda pode, por exemplo, so
    mandar o PDF as segundas 15h30 e o texto as sextas 7h05.

    Cada item sai no horario configurado (nunca antes de 5h30, ver
    `ENVIO_AUTOMATICO_MINIMO_MIN`) -- desde 16/10/2026 sem o
    escalonamento de 1h por destinatario/numero
    (`RECIPIENT_STAGGER_HORAS = 0`); o que espaca as mensagens e' o
    portao de `whatsapp._um_envio_por_vez` (um envio por vez, 15s depois
    do anterior). Item que nao saiu ate' `JANELA_ENVIO_AGENDADO_MIN`
    depois do horario fica pro proximo dia programado.

    Texto e PDF de uma MESMA fazenda/destinatario que calhem do MESMO
    horario desejado (comum -- `_seed_horario_sugerido` sempre configura
    os dois iguais) viram UM candidato so' com os 2 flags, pra'
    `_send_site_whatsapp` poder combina-los num unico envio quando
    couber (`LEGENDA_MAX_CHARS`) -- menos mensagens pro mesmo numero,
    tambem ajuda contra retencao. Quando os horarios configurados
    diferem (fazenda customizada a mao), viram 2 candidatos
    independentes.

    So' dispara um item quando o dia da semana bate E ja passou (>=) do
    horario EFETIVO dele (depois das 2 camadas acima), E esse (site,
    telefone, tipo) ainda nao esta' no conjunto "ja' enviado hoje"
    (`_load_scheduler_enviados`) -- chave por TELEFONE, nunca pelo
    indice/posicao na lista (que muda se a lista de destinatarios
    daquela fazenda mudar no meio do dia). Usa ">=" (nao "==") de
    proposito: so' e' marcado como feito quando REALMENTE deu certo --
    uma falha precisa continuar tentando nos minutos seguintes, senao o
    destinatario simplesmente nunca recebe o relatorio naquele dia.
    Marca e PERSISTE logo apos cada envio (nao so' no final do lote),
    entao um crash/reinicio no meio da lista retoma so' do que falta,
    nunca reenvia o que ja' foi. Dentro de cada rodada, pontos "so'
    clima" (aba Alertas Clima) sempre vao DEPOIS de toda fazenda normal
    pendente -- pedido explicito do usuario, pra' priorizar o relatorio
    de recomendacao (doenca) na fila do whatsapp-bridge (ver
    whatsapp.py) antes de qualquer clima de referencia."""
    # `_agora_cuiaba_dt()`, NUNCA `datetime.now()` puro -- o container
    # roda em UTC (sem `TZ` no Dockerfile), entao `datetime.now()` fica
    # ~4h fora do horario configurado (Cuiaba) usado pra decidir se ja'
    # e' hora de mandar.
    agora_dt = _agora_cuiaba_dt()
    today = agora_dt.date()
    weekday = today.weekday()
    agora_hm = (agora_dt.hour, agora_dt.minute)
    schedule_texto = models.get_all_whatsapp_days()
    schedule_pdf = models.get_all_whatsapp_days_pdf()
    horarios = models.get_all_whatsapp_schedule_horarios()
    hora_default = models.get_whatsapp_send_hour()
    ja_enviados = _load_scheduler_enviados(today)
    virtual_clima_names = {vf["site_name"] for vf in models.get_all_virtual_farms() if vf.get("tipo") == "clima"}

    candidatos = []
    for site in set(schedule_texto) | set(schedule_pdf):
        hora_site = horarios.get(site) or {"texto": hora_default, "texto_min": 0, "pdf": hora_default, "pdf_min": 0}
        destinos = _site_whatsapp_destinations(site)
        for idx, (telefone, rotulo) in enumerate(destinos):
            devido_texto = (
                weekday in schedule_texto.get(site, set()) and f"{site}::{telefone}::texto" not in ja_enviados
            )
            # Ponto "so' clima" nao tem PDF (pedido do Alan).
            devido_pdf = (
                site not in virtual_clima_names
                and weekday in schedule_pdf.get(site, set()) and f"{site}::{telefone}::pdf" not in ja_enviados
            )
            if not devido_texto and not devido_pdf:
                continue
            desejado_texto = _horario_efetivo_destinatario(hora_site, "texto", idx) if devido_texto else None
            desejado_pdf = _horario_efetivo_destinatario(hora_site, "pdf", idx) if devido_pdf else None
            base = {"site": site, "telefone": telefone, "rotulo": rotulo}
            if devido_texto and devido_pdf and desejado_texto == desejado_pdf:
                candidatos.append({
                    **base, "enviar_texto": True, "enviar_pdf": True,
                    "desejado_min": desejado_texto[0] * 60 + desejado_texto[1],
                })
            else:
                if devido_texto:
                    candidatos.append({
                        **base, "enviar_texto": True, "enviar_pdf": False,
                        "desejado_min": desejado_texto[0] * 60 + desejado_texto[1],
                    })
                if devido_pdf:
                    candidatos.append({
                        **base, "enviar_texto": False, "enviar_pdf": True,
                        "desejado_min": desejado_pdf[0] * 60 + desejado_pdf[1],
                    })
    if not candidatos:
        return
    _horarios_efetivos_por_telefone(candidatos)
    # So' no horario programado: o que nao saiu ate' JANELA_ENVIO_AGENDADO_MIN
    # depois dele (servidor/WhatsApp fora do ar) fica pro proximo dia
    # programado, em vez de sair horas depois (pedido do usuario, 16/10/2026).
    agora_min = agora_hm[0] * 60 + agora_hm[1]
    pendentes = [c for c in candidatos if c["efetivo_min"] <= agora_min <= c["efetivo_min"] + JANELA_ENVIO_AGENDADO_MIN]
    if not pendentes:
        return
    # Estavel -- so' empurra os pontos "so' clima" pro final, sem mexer
    # na ordem relativa entre eles nem entre as demais fazendas.
    pendentes.sort(key=lambda item: item["site"] in virtual_clima_names)
    # Aquece o cache de clima de todas as fazendas pendentes em paralelo
    # antes de mandar (dedup -- varios destinatarios da mesma fazenda so'
    # precisam de 1 busca) -- sem isso, `_send_site_whatsapp` buscava o
    # clima de cada fazenda um de cada vez (rede, ~1-1.5s cada), somando
    # varios segundos so' de espera sequencial toda vez que o agendador roda.
    sites_pendentes = sorted({item["site"] for item in pendentes})
    _prefetch_weather(sites_pendentes, _weather_coords_all())
    for item in pendentes:
        site, telefone, rotulo = item["site"], item["telefone"], item["rotulo"]
        enviar_texto, enviar_pdf = item["enviar_texto"], item["enviar_pdf"]
        ok_texto, ok_pdf, _ = _send_site_whatsapp(
            site, enviar_texto=enviar_texto, enviar_pdf=enviar_pdf, destinos_override=[(telefone, rotulo)]
        )
        # So' marca "enviado hoje" o que realmente deu certo -- o que
        # falhar e' tentado de novo no proximo minuto, ate' funcionar ou
        # passar JANELA_ENVIO_AGENDADO_MIN do horario programado.
        if enviar_texto and ok_texto:
            ja_enviados.add(f"{site}::{telefone}::texto")
        if enviar_pdf and ok_pdf:
            ja_enviados.add(f"{site}::{telefone}::pdf")
        _salvar_scheduler_enviados(today, ja_enviados)


_NDVI_SCHEDULER_STATE_KEY = "ndvi_scheduler_state"


def _load_ndvi_scheduler_tentados(today):
    """Mesmo padrao de `_load_scheduler_enviados` (fazendas ja' TENTADAS
    hoje pelo agendamento de NDVI, sucesso ou nao -- ver
    `_run_scheduled_ndvi_sends`), so' que numa chave de settings
    separada, pra' nao interferir no dedupe do envio de relatorio."""
    bruto = models.get_setting(_NDVI_SCHEDULER_STATE_KEY)
    if bruto:
        try:
            estado = json.loads(bruto)
        except ValueError:
            estado = {}
        if estado.get("date") == today.isoformat():
            return set(estado.get("tentados", []))
    return set()


def _salvar_ndvi_scheduler_tentados(today, tentados):
    models.set_setting(_NDVI_SCHEDULER_STATE_KEY, json.dumps({"date": today.isoformat(), "tentados": sorted(tentados)}))


def _run_scheduled_ndvi_sends():
    """Roda a cada minuto (`_whatsapp_scheduler_loop`) -- pra cada
    fazenda com agendamento de NDVI ativo (`models.get_all_ndvi_agendamentos`,
    aba NDVI) cuja `proxima_execucao` ja' chegou E a hora atual bate com
    o horario individual dessa fazenda (`agendamento["hora"]` -- pedido
    explicito do usuario, pra poder espalhar os envios ao longo do dia
    em vez de todas as fazendas mandarem no mesmo horario), busca a
    melhor cena disponivel (nuvens <= `NDVI_AGENDAMENTO_COBERTURA_MAXIMA`%,
    mais estrito que o preview manual) e, se for uma cena NOVA (data
    ainda nao salva no historico dessa fazenda --
    `models.ndvi_historico_tem_data`, evita reenviar a MESMA foto quando
    nenhuma cena nova apareceu desde o ultimo ciclo, comum dado o
    revisit de ~5 dias do Sentinel-2), salva na galeria/fazenda (mesma
    sequencia de `confirmar_ndvi_preview`) e manda por WhatsApp pros
    telefones marcados como ativos (`models.get_ndvi_ativos`). Sempre
    avanca `proxima_execucao` no final (sucesso, erro ou "nada novo") --
    nunca fica preso tentando todo dia, retoma o ciclo normal na proxima
    data agendada. Tentativa (nao envio) e' marcada uma vez por dia
    (`_load_ndvi_scheduler_tentados`), igual ao agendador de relatorio,
    pra' sobreviver a reinicio no meio da hora sem reprocessar quem ja'
    foi tentado hoje -- so' marca DEPOIS que a hora certa chegar (senao
    ficaria preso "tentado" o dia inteiro antes mesmo do horario
    configurado)."""
    hoje = datetime.now(timezone.utc).date()
    hora_atual = datetime.now().hour
    tentados = _load_ndvi_scheduler_tentados(hoje)
    for site, agendamento in models.get_all_ndvi_agendamentos().items():
        if site in tentados or agendamento["proxima_execucao"] > hoje or agendamento["hora"] != hora_atual:
            continue
        try:
            cars_kml = models.get_farm_ndvi_cars_kml(site)
            if cars_kml:
                lista_de_aneis_por_car = [ndvi_service.parse_kml_poligono(kml) for kml in cars_kml]
                resultado, erro = ndvi_service.buscar_ndvi(
                    lista_de_aneis_por_car, data_alvo=hoje, cobertura_maxima=NDVI_AGENDAMENTO_COBERTURA_MAXIMA
                )
                if resultado and not models.ndvi_historico_tem_data(site, resultado["data"].isoformat()):
                    chuva, vento = _chuva_vento_ultimos_30_dias(site, resultado["data"])
                    imagem = ndvi_service.desenhar_informacoes(
                        resultado["imagem"], resultado["data"], chuva_acumulada_mm=chuva, vento_predominante=vento
                    )
                    thumbnail = ndvi_service.gerar_thumbnail(imagem)
                    models.add_farm_ndvi_historico(
                        site, resultado["data"].isoformat(), imagem, resultado["cobertura_nuvens"], thumbnail
                    )
                    models.save_farm_ndvi_image(site, imagem)
                    nome_exibicao = _nome_exibicao(site)
                    data_br = resultado["data"].strftime("%d/%m/%Y")
                    legenda = f"🛰️ NDVI - {nome_exibicao} - {data_br} (envio automatico)"
                    ativos = models.get_ndvi_ativos(site)
                    for phone, rotulo in _site_whatsapp_destinations(site):
                        if phone not in ativos:
                            continue
                        ok, mensagem = whatsapp.send_whatsapp_image(phone, imagem, caption=legenda)
                        models.log_whatsapp_envio(site, rotulo, phone, ok, f"NDVI agendado {data_br}: {mensagem}")
        except Exception:
            pass  # nunca deixa o loop do agendador morrer -- tenta de novo no proximo ciclo
        finally:
            models.avancar_ndvi_agendamento(site, hoje)
            tentados.add(site)
            _salvar_ndvi_scheduler_tentados(hoje, tentados)


def _whatsapp_scheduler_loop():
    """Roda a cada minuto, o dia inteiro -- o horario de cada envio
    (relatorio texto/PDF e NDVI agendado) agora e' individual por
    fazenda (`models.get_all_whatsapp_schedule_horarios`,
    `models.get_all_ndvi_agendamentos`), entao a checagem de "e' a hora
    certa?" mora DENTRO de cada uma dessas duas funcoes, nao mais aqui
    (antes havia um unico horario global pra tudo -- `models.get_whatsapp_send_hour()`
    continua existindo so' como default pra quem nunca customizou o
    horario de uma fazenda especifica). Roda numa thread de background
    PROPRIA (`threading.Thread`, sem nenhum request HTTP por tras) --
    `with app.app_context()` e' obrigatorio aqui: `_nome_exibicao` (usado
    em praticamente todo envio, texto e PDF) guarda cache em `flask.g`,
    que so' existe dentro de um app context. Sem isso, TODO envio
    agendado (a partir do momento em que `_nome_exibicao` passou a usar
    `g`) lancava `RuntimeError: Working outside of application context`
    logo no comeco de `_send_site_whatsapp` -- exececao sempre engolida
    pelo try/except abaixo (pra' nunca matar o loop), entao nenhum
    relatorio agendado saia' e NADA ficava registrado no log de falhas,
    ate' esse fix."""
    while True:
        with app.app_context():
            # Clima antes dos envios: no tick em que vira a janela de
            # leitura, o relatorio ja' sai com o dado novo.
            try:
                _atualizar_open_meteo_diario()
            except Exception:
                pass
            if not AMBIENTE_QAP:  # QAP (banco copiado da producao) nunca dispara relatorio agendado
                try:
                    _run_scheduled_whatsapp_sends()
                except Exception:
                    pass  # nunca deixa o loop do agendador morrer
                try:
                    _run_scheduled_ndvi_sends()
                except Exception:
                    pass
            try:
                agrofit_dados.atualizar_se_necessario()
            except Exception:
                pass
            try:
                sinal_celular.atualizar_se_necessario()
            except Exception:
                pass
            try:
                models.fazer_backup_diario()
            except Exception:
                pass
        time.sleep(60)


def _atualizar_open_meteo_diario():
    """Leitura cadenciada (04:45, 11:45 e 17:45 de Cuiaba, ver
    `weather_forecast.janela_de_leitura`) da previsao (Open-Meteo, MET
    Norway de backup) e das estacoes oficiais (INMET, DMC, SMN) pra toda
    fazenda -- a estacao de referencia (`_weather_coords_all`), as 3
    estacoes da opcao "interpolacao" e a coordenada propria. Se o servidor
    subir no meio de uma janela ainda nao lida, roda na hora. Depois,
    limpa os caches em memoria pra' Painel/Graficos refletirem a leitura
    nova."""
    if not weather_forecast.rodada_pendente():
        return
    coords_proprias = _coords_all()
    coordenadas = list(coords_proprias.values())
    for latlon in _weather_coords_all().values():
        if latlon[0] != INTERPOLACAO_SENTINEL:
            coordenadas.append(latlon)
            continue
        for par in latlon[1:]:
            country_code, codigo = par.split(":", 1)
            provider = countries.get_country(country_code)["station_provider"]
            estacao = next((e for e in provider.get_estacoes() if e["codigo"] == codigo), None)
            if estacao:
                coordenadas.append((estacao["lat"], estacao["lon"]))
    estacoes = {e for e in (_estacao_oficial_do_site(site) for site in coords_proprias) if e}
    weather_forecast.atualizar_todos(coordenadas, estacoes)
    _weather_cache.clear()
    _historico_horario_cache.clear()


login_manager = LoginManager(app)
login_manager.login_view = "login"
# Cookie de "Manter conectado" (ver /login) -- so' o navegador precisa
# ler/mandar de volta, nunca JS da propria pagina, entao HttpOnly (o
# Flask-Login nao liga por padrao) fecha uma via facil de roubo via
# XSS sem custo nenhum de funcionalidade.
app.config["REMEMBER_COOKIE_HTTPONLY"] = True


class User(UserMixin):
    def __init__(self, row):
        self.id = str(row["id"])
        self.username = row["username"]
        self.is_admin = bool(row["is_admin"])
        self.is_master = bool(row["is_master"])
        self.is_cliente = bool(row["is_cliente"])


@login_manager.user_loader
def load_user(user_id):
    row = models.get_user_by_id(user_id)
    return User(row) if row else None


def admin_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            abort(403)
        return view(*args, **kwargs)
    return wrapped


# Niveis de acesso: Usuario (so' as fazendas liberadas), Administrador
# e Master (configuracoes gerais: "Editar
# cadastro", Exportar, Conexao WhatsApp, Fazendas ocultas...). So' o
# ALAN_MAURO_USERNAME designa quem e' Master, e ele e' sempre Master.
ALAN_MAURO_USERNAME = models.MASTER_PRINCIPAL
NIVEIS_ACESSO = {"usuario": "Usuário", "admin": "Administrador", "master": "Master"}


def _nivel_acesso(row):
    if row["is_master"]:
        return "master"
    return "admin" if row["is_admin"] else "usuario"


def master_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_master:
            abort(403)
        return view(*args, **kwargs)
    return wrapped


# Abas do menu Configuracoes que o Master libera por nivel na aba Acessos
# (pedido do Alan, 19/10/2026) -- (chave, rotulo, endpoint da aba). As
# demais (Conexao WhatsApp, Exportar, Fazendas ocultas, Acessos) sao
# sempre so' Master.
ABAS_CONFIG = [
    ("usuarios", "Usuários", "admin_users"),
    ("doencas", "Doenças", "admin_doencas"),
    ("fungicidas", "Fungicidas", "admin_fungicidas"),
    ("mapa_interpolado", "Mapas de Estudo", "mapa_interpolado"),
    ("solo", "Mapas de Solo", "solo"),
    ("ndvi", "Mapas de NDVI", "ndvi"),
    ("envios", "Programação de Envios", "whatsapp_gerenciamento"),
]
_ACESSOS_KEY = "acessos_por_nivel"
ACESSOS_PADRAO = {"admin": ["usuarios", "mapa_interpolado"], "usuario": []}
_RANK_NIVEL = {"usuario": 0, "admin": 1, "master": 2}


def _acessos_por_nivel():
    acessos = getattr(g, "_acessos_por_nivel", None)
    if acessos is None:
        try:
            salvo = json.loads(models.get_setting(_ACESSOS_KEY) or "null") or {}
        except ValueError:
            salvo = {}
        acessos = g._acessos_por_nivel = {
            nivel: set(salvo.get(nivel, padrao)) for nivel, padrao in ACESSOS_PADRAO.items()
        }
    return acessos


def pode_acessar_aba(chave):
    if not current_user.is_authenticated or getattr(current_user, "is_cliente", False):
        return False
    if current_user.is_master:
        return True
    return chave in _acessos_por_nivel()["admin" if current_user.is_admin else "usuario"]


def aba_required(chave):
    def decorator(view):
        @functools.wraps(view)
        def wrapped(*args, **kwargs):
            if not pode_acessar_aba(chave):
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return decorator


def _rank_atual():
    if current_user.is_master:
        return _RANK_NIVEL["master"]
    return _RANK_NIVEL["admin" if current_user.is_admin else "usuario"]


@app.context_processor
def _inject_abas_config():
    if not current_user.is_authenticated:
        return {"abas_config_permitidas": []}
    return {
        "pode_aba": pode_acessar_aba,
        "abas_config_permitidas": [c for c, _r, _e in ABAS_CONFIG if pode_acessar_aba(c)],
    }


@app.before_request
def _ensure_sites_synced():
    if request.endpoint in ("static", "service_worker"):
        return
    # Mantem a tabela "sites" alinhada com o CSV mais recente (novas fazendas
    # aparecem automaticamente na tela de permissoes) -- fazenda nova (site
    # que ainda nao existia na tabela) ja entra com a agenda padrao de
    # WhatsApp (Texto Seg/Qua/Sex, PDF Sex -- ver
    # `models.seed_default_whatsapp_schedule`).
    try:
        novos = models.sync_sites(read_sites())
        for site_name in novos:
            models.seed_default_whatsapp_schedule(site_name)
    except FileNotFoundError:
        pass
    _auto_detectar_paises_novos()
    _auto_detectar_estacoes_novas()
    _auto_detectar_solos_novos()


def _auto_detectar_estacoes_novas():
    """Fazenda (real ou virtual/estimada, qualquer tipo) com coordenada
    mas ainda sem estacao de referencia escolhida (nem por escolha manual
    do cliente, nem por deteccao anterior) tem a estacao oficial mais
    proxima escolhida e GRAVADA sozinha como padrao (`weather_station_overrides`)
    -- fica a criterio do cliente trocar depois (aba Fazendas/Alertas
    Clima) pra outra estacao ou pra "coordenada da propria fazenda"
    (reservado pra quem tem estacao meteorologica propria, ver
    `_weather_coords_all`). NUNCA sobrescreve uma escolha ja feita
    (manual ou automatica anterior) -- so' age em quem ainda nao tem
    nenhuma linha na tabela. Pais sem estacao oficial integrada ainda
    (a maioria da America do Sul alem de Brasil/Chile, ver
    `countries.estacoes_mais_proximas_global`) continua sem override
    nenhum -- nao ha' nada pra' escolher ainda, cai na coordenada da
    propria fazenda por falta de opcao melhor."""
    try:
        coords = _coords_all()
    except FileNotFoundError:
        return
    overrides = models.get_all_weather_station_overrides()
    site_countries = models.get_all_site_countries()
    for site, (lat, lon) in coords.items():
        if site in overrides:
            continue
        pais_fazenda = site_countries.get(site, countries.DEFAULT_COUNTRY)
        cache_pais = getattr(countries.get_country(pais_fazenda)["station_provider"], "_cache", None)
        if cache_pais is not None and not cache_pais["estacoes"]:
            # Catalogo do proprio pais ainda carregando (em segundo plano,
            # ver estacoes_cache_util) -- espera, senao gravaria de vez uma
            # estacao do pais vizinho que carregou primeiro.
            countries.get_country(pais_fazenda)["station_provider"].get_estacoes()
            continue
        candidatas = countries.estacoes_mais_proximas_global(lat, lon, pais_fazenda, n=1)
        if not candidatas:
            continue
        estacao = candidatas[0]
        models.set_weather_station_override(site, estacao["codigo"], estacao["country_code"])


def _auto_detectar_solos_novos():
    """Fazenda com coordenada mas ainda sem tipo de solo resolvido tem o
    tipo de solo detectado sozinho e GRAVADO (`models.set_site_solo`) --
    mesmo padrao de `_auto_detectar_paises_novos`, so' que aqui NAO ha'
    escolha manual nenhuma pra sobrepor depois (solo e' um fato fisico
    do local, nao uma preferencia do cliente). Tenta primeiro o Mapa de
    Solos do Brasil (`soil_service.tipo_solo_embrapa`, so' cobre o
    Brasil); se nao achar nada (fora do Brasil, ou area de agua), cai
    pro fallback global (`soilgrids_service.tipo_solo_soilgrids`).
    Grava o resultado mesmo quando os dois devolvem None ("sem dado
    disponivel"), pra marcar como ja' tentado e nunca mais refazer esse
    calculo (que envolve testar contra ~2852 poligonos) a cada request."""
    try:
        coords = _coords_all()
    except FileNotFoundError:
        return
    site_solos = models.get_all_site_solos()
    for site, (lat, lon) in coords.items():
        if site in site_solos:
            continue
        resultado = soil_service.tipo_solo_embrapa(lat, lon) or soilgrids_service.tipo_solo_soilgrids(lat, lon)
        models.set_site_solo(site, resultado)


def _auto_detectar_paises_novos():
    """Fazenda com coordenada mas ainda sem pais marcado (nem por escolha
    manual na aba Fazendas, nem por deteccao anterior) tem o pais
    detectado sozinho pela coordenada (contorno estatico do Chile, etc --
    ver `countries.detectar_pais_por_coordenada`), e a escolha e' GRAVADA
    mesmo quando da' Brasil (o padrao) -- assim essa fazenda fica marcada
    como "ja testada" e nunca mais entra nesse calculo de novo, so' se
    alguem trocar manualmente depois. So' faz o teste geometrico (o unico
    passo que custa alguma coisa, ~300KB de GeoJSON) pra quem realmente
    ainda nao foi testado -- em regime permanente (a maioria das fazendas
    ja' testada) isso e' so' duas consultas rapidas ao banco, sem abrir
    nenhum arquivo."""
    try:
        coords = _coords_all()
    except FileNotFoundError:
        return
    site_countries = models.get_all_site_countries()
    for site, (lat, lon) in coords.items():
        if site in site_countries:
            continue
        models.set_site_country(site, countries.detectar_pais_por_coordenada(lat, lon))


@app.route("/sw.js")
def service_worker():
    """Serve o service worker na RAIZ do site (nao em /static/sw.js) de
    proposito -- o escopo padrao de um service worker e' a pasta de onde
    ele foi servido, e a pagina de login (que ele precisa poder
    interceptar) vive na raiz."""
    return send_from_directory(app.static_folder, "sw.js", mimetype="application/javascript")


@app.route("/")
def index():
    if current_user.is_authenticated:
        return redirect(url_for("cliente_home" if current_user.is_cliente else "mapa"))
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        # Marcado por padrao (checkbox comeca "checked" no HTML) -- so
        # fica de fora do POST se a pessoa desmarcar antes de entrar,
        # pedido explicito do usuario pra nao precisar digitar login e
        # senha toda vez. Cookie separado do Flask-Login (assinado com
        # o mesmo secret_key persistido em disco -- ver
        # _load_or_create_secret_key -- entao sobrevive a redeploy),
        # dura 365 dias por padrao (REMEMBER_COOKIE_DURATION).
        lembrar = bool(request.form.get("remember"))
        row = models.get_user_by_username(username)
        if row and check_password_hash(row["password_hash"], password):
            login_user(User(row), remember=lembrar)
            return redirect(url_for("cliente_home" if row["is_cliente"] else "mapa"))
        if row and row["temp_password_hash"] and _senha_temporaria_valida(row) \
                and check_password_hash(row["temp_password_hash"], password):
            # Senha de "esqueci a senha" -- uso unico, apaga na hora (nao
            # da' pra logar de novo com ela) e forca definir uma senha
            # definitiva antes de liberar o resto do app (ver
            # _forcar_definir_senha, o before_request logo abaixo).
            models.clear_user_temp_password(row["id"])
            login_user(User(row), remember=lembrar)
            session["deve_definir_senha"] = True
            return redirect(url_for("definir_nova_senha"))
        flash("Usuario ou senha invalidos.", "error")
    return render_template("login.html")


def _senha_temporaria_valida(row):
    if not row["temp_password_expires_at"]:
        return False
    return datetime.now() < datetime.fromisoformat(row["temp_password_expires_at"])


def _gerar_senha_temporaria(tamanho=8):
    # Sem 0/O, 1/l/I -- caracteres que se confundem facil lendo no
    # celular (a senha e' recebida por WhatsApp, digitada de cabeca).
    alfabeto = "ABCDEFGHJKMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789"
    return "".join(secrets.choice(alfabeto) for _ in range(tamanho))


@app.route("/esqueci-senha", methods=["GET", "POST"])
def esqueci_senha():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        chave = username.lower()
        agora = datetime.now()
        ultima = _esqueci_senha_ultima_solicitacao.get(chave)
        em_cooldown = ultima and (agora - ultima).total_seconds() < ESQUECI_SENHA_COOLDOWN_SEGUNDOS
        if not em_cooldown:
            row = models.get_user_by_username(username)
            if row and row["telefone"]:
                _esqueci_senha_ultima_solicitacao[chave] = agora
                senha_temporaria = _gerar_senha_temporaria()
                models.set_user_temp_password(row["id"], senha_temporaria, SENHA_TEMPORARIA_VALIDADE_MINUTOS)
                texto = (
                    f"OneAgro Monitor: recebemos uma solicitacao para redefinir sua senha.\n\n"
                    f"Sua senha temporaria e': *{senha_temporaria}*\n\n"
                    f"Ela vale por {SENHA_TEMPORARIA_VALIDADE_MINUTOS} minutos e so' funciona uma vez -- "
                    f"assim que voce entrar com ela, o sistema vai pedir pra criar uma senha nova.\n\n"
                    f"Se voce nao pediu isso, pode ignorar esta mensagem -- sua senha continua a mesma."
                )
                ok, msg = whatsapp.send_whatsapp(row["telefone"], texto)
                if not ok:
                    # So' fica registrado pro admin conferir depois -- nunca
                    # aparece pra quem pediu (evitaria vazar se o username
                    # existe/tem telefone por diferenca de resposta).
                    print(f"[esqueci-senha] falha ao enviar WhatsApp pra {username}: {msg}")
        # Mensagem SEMPRE igual, exista o usuario ou nao, tenha telefone ou
        # nao, funcione o envio ou nao -- de proposito, pra nao dar pra
        # descobrir se um username existe so' pela resposta.
        flash(
            f"Se esse usuario existir e tiver WhatsApp cadastrado, enviamos uma senha "
            f"temporaria por la (valida por {SENHA_TEMPORARIA_VALIDADE_MINUTOS} minutos).",
            "success",
        )
        return redirect(url_for("login"))
    return render_template("esqueci_senha.html")


@app.before_request
def _forcar_definir_senha():
    # Quem logou com a senha temporaria de "esqueci a senha" precisa
    # definir uma senha definitiva antes de usar o resto do app -- estado
    # guardado na SESSION (nao no banco) de proposito: se o usuario fechar
    # o navegador antes de trocar, na proxima vez ele so' pede "esqueci a
    # senha" de novo e recomeca limpo, sem "flag" nenhuma grudada no banco.
    if current_user.is_authenticated and session.get("deve_definir_senha") \
            and request.endpoint not in ("definir_nova_senha", "logout", "static"):
        return redirect(url_for("definir_nova_senha"))


@app.route("/definir-nova-senha", methods=["GET", "POST"])
@login_required
def definir_nova_senha():
    if not session.get("deve_definir_senha"):
        return redirect(url_for("mapa"))
    if request.method == "POST":
        nova = request.form.get("senha_nova", "")
        confirmar = request.form.get("senha_confirmar", "")
        if not nova or len(nova) < 6:
            flash("A nova senha precisa ter pelo menos 6 caracteres.", "error")
        elif nova != confirmar:
            flash("A confirmacao nao bate com a nova senha.", "error")
        else:
            models.set_user_password(int(current_user.id), nova)
            session.pop("deve_definir_senha", None)
            flash("Senha definida com sucesso.", "success")
            return redirect(url_for("mapa"))
    return render_template("definir_nova_senha.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


@app.route("/dashboard/atualizar", methods=["POST"])
@login_required
def dashboard_atualizar():
    """Botao 'Forcar atualizacao' do Painel -- pede uma busca nova sem
    esperar os dados ficarem velhos (os 15 min do `_maybe_auto_refresh`).
    Funciona tanto local (script PowerShell) quanto no site hospedado
    (BIOSCOUT_USERNAME/BIOSCOUT_PASSWORD, busca em Python -- ver
    `bioscout_fetch.py`); sem nenhum dos dois configurados, avisa em vez
    de fingir que funcionou."""
    if not _fetch_configured():
        flash(
            "Nenhuma busca de dados configurada nesse ambiente (nem "
            "BIOSCOUT_USERNAME/BIOSCOUT_PASSWORD, nem o script local) -- avise "
            "um administrador.",
            "error",
        )
    elif _start_fetch_if_not_running():
        flash(
            "Busca de dados nova comecou em segundo plano. Atualize a pagina "
            "daqui a pouco para ver os dados mais recentes.",
            "success",
        )
    else:
        flash("Ja tem uma busca de dados em andamento -- aguarde ela terminar.", "success")
    return redirect(request.referrer or url_for("dashboard"))


@app.route("/dashboard")
@login_required
def dashboard():
    _maybe_auto_refresh()  # silencioso -- nao avisa mais no topo da pagina, so acontece
    if current_user.is_admin:
        permitted = _sites_ativos_para_admin()  # admin ve todas as fazendas ativas
    else:
        permitted = set(models.get_user_permitted_site_names(int(current_user.id)))
        if not permitted:
            return render_template("dashboard.html", cards_by_site={}, no_access=True)
    cards_by_site = _cards_by_site_all(permitted, _load_translations())
    # Painel mostra TODAS as caixas de doenca sempre -- o filtro de cultura
    # so se aplica nas abas Recomendacoes (Safra / 2 Safra), nao aqui.
    # Umidade/chuva vem SEMPRE da Open-Meteo (mesma fonte usada em
    # Recomendacoes/Alertas Clima/WhatsApp) -- nao mais do clima que o
    # BioScout mandava por device (modelo, nao sensor), que so' ficava tao fresco quanto a
    # ultima sincronizacao daquele aparelho (podendo ficar velho bem na
    # hora que a fazenda mais precisa de clima confiavel, ver
    # DADOS_BLOQUEIO_DIAS) -- pedido explicito do usuario ("sempre
    # atualizar a informacao de clima"). Mostrado uma vez so, ao lado do
    # nome da fazenda, em vez de repetir em cada caixa de doenca.
    weather_by_site = {}
    dados_status_by_site = {}
    nomes_virtuais = {vf["site_name"]: vf["nome"] for vf in models.get_all_virtual_farms()}
    coords = _weather_coords_all()
    mais_recente_by_site = {site: max(cards, key=lambda c: c["data"]) for site, cards in cards_by_site.items()}
    _prefetch_weather(list(cards_by_site.keys()), coords)
    for site, cards in cards_by_site.items():
        mais_recente = mais_recente_by_site[site]
        weather = _get_weather_for_site(site, coords)
        weather_by_site[site] = {
            "umidade": weather.get("umidade_atual") if weather else None,
            "chuva": weather.get("chuva_atual_mm") if weather else None,
            "chuva_24h": weather.get("chuva_24h_mm") if weather else None,
            "data": mais_recente["data"],
        }
        dias_sem_leitura = _dias_sem_leitura(cards)
        dados_status_by_site[site] = {
            "dias_sem_leitura": dias_sem_leitura,
            "nivel_dados": _nivel_dados_site(site, cards),
            "virtual": site in nomes_virtuais,
            "nome_exibicao": _nome_exibicao(site),
        }

    # Mesma ordem de todas as telas (`_ordem_fazenda`): pais (Brasil
    # primeiro), nome, tempo sem sinal.
    sites_ordenados = sorted(cards_by_site, key=lambda s: (_ordem_fazenda(s), dados_status_by_site[s]["dias_sem_leitura"] or 0))
    cards_by_site = {s: cards_by_site[s] for s in sites_ordenados}

    return render_template(
        "dashboard.html", cards_by_site=cards_by_site, no_access=False,
        weather_by_site=weather_by_site, dados_status_by_site=dados_status_by_site,
        fetch_last_error=_fetch_state["last_error"] if current_user.is_admin else None,
    )


def _uf_por_site():
    """site_name -> UF/regiao (sigla do estado, ou nome da regiao pra
    fazenda de outro pais), pela estacao oficial mais proxima da
    coordenada da fazenda -- INMET pro Brasil, DMC pro Chile (ver
    `countries.py`; cada fazenda usa o pais marcado na aba Fazendas,
    `models.get_all_site_countries`, default 'BR'). Usado pro filtro de
    estado/regiao na aba Graficos. Inclui fazenda real
    (`data_reader.read_site_coordinates`, direto do CSV, sem cache -- uma
    fazenda cadastrada agora ja aparece no proximo carregamento da
    pagina) e fazenda virtual/estimada (`models.get_all_virtual_farms`,
    ver `_graficos_dados` pra como a serie dela e' interpolada das
    fazendas reais na vizinhanca). NAO precisa de cache proprio aqui -- o
    catalogo de estacoes de cada provedor (a parte cara disso, um fetch
    de rede) ja tem seu proprio cache de 24h; com ele pronto, calcular a
    UF/regiao de cada fazenda e' so' um haversine contra uma lista ja em
    memoria (barato mesmo pra' dezenas de fazendas). Fazenda sem
    coordenada, ou sem estacao proxima resolvida (catalogo fora do ar, ou
    sem credencial configurada pro provedor daquele pais), fica com UF
    None -- nunca quebra a pagina por causa disso, so' nao aparece nos
    filtros de estado. Ponto "so clima" (tipo='clima', aba Alertas Clima)
    fica de fora -- nao tem doenca nenhuma pra mostrar em grafico, pedido
    explicito do usuario."""
    coords = _coords_all()
    site_countries = models.get_all_site_countries()
    virtual_clima_names = {vf["site_name"] for vf in models.get_all_virtual_farms() if vf.get("tipo") == "clima"}
    por_site = {}
    for site, latlon in coords.items():
        if site in virtual_clima_names:
            continue
        provider = countries.get_country(site_countries.get(site, countries.DEFAULT_COUNTRY))["station_provider"]
        estacao = provider.estacao_mais_proxima(*latlon)
        por_site[site] = estacao["uf"] if estacao else None
    return por_site


def _hoje_cuiaba():
    return (datetime.now(timezone.utc) - timedelta(hours=4)).date()


def _agora_cuiaba_dt():
    """Como `_hoje_cuiaba()`, so' que devolve o datetime completo (nao so'
    a data) -- usado onde precisa somar/comparar horario, nao so' dia.
    Mesma formula de `models._agora_cuiaba()` (que devolve string): tem
    que ser SEMPRE essa, nunca `datetime.now()` puro, porque o container
    roda em UTC (sem `TZ` no Dockerfile) e qualquer horario gravado com
    `datetime.now()` fica ~4h fora ao ser comparado com algo gravado via
    `_agora_cuiaba()`."""
    return datetime.now(timezone.utc) - timedelta(hours=4)


def _sites_permitidos_usuario():
    """Todas as fazendas ATIVAS se admin (`_sites_ativos_para_admin`);
    senao o conjunto de fazendas liberadas pra esse usuario (mesmo
    `get_user_permitted_site_names` ja usado no Painel/Mapa/Manejo) --
    usado pra aba Graficos nunca mostrar dado de fazenda que o usuario
    nao tem permissao de ver, nem fazenda desativada."""
    if current_user.is_admin:
        return _sites_ativos_para_admin()
    return set(models.get_user_permitted_site_names(int(current_user.id)))


def _parse_iso_date(value):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def _parse_data_anotacao(texto):
    """O campo "Data/Anotacao" da aba Anotacoes de Safra (TS/Sulco/Folha)
    e "Data plantio" (Dados de Plantio) sao texto livre, nao um
    `<input type=date>` -- tenta 'DD/MM/AAAA', 'DD/MM/AA' e 'DD/MM'
    nessa ordem. Com ano, devolve a data ISO exata (`data_iso`); so' dia/
    mes (sem ano), devolve `dia_mes` ("DD/MM") pra' quem usa casar contra
    QUALQUER ano do periodo mostrado no grafico (ver `graficos.html`,
    onde pode ter ambiguidade se o periodo passar de 1 ano -- mostra a
    marca em todo ano que bater). None quando o texto nao bate com
    nenhum formato (anotacao solta tipo "meados de marco" e' so'
    ignorada, sem quebrar o grafico)."""
    texto = (texto or "").strip()
    if not texto:
        return None
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return {"data_iso": datetime.strptime(texto, fmt).date().isoformat(), "dia_mes": None}
        except ValueError:
            continue
    try:
        dt = datetime.strptime(texto, "%d/%m")
        return {"data_iso": None, "dia_mes": f"{dt.day:02d}/{dt.month:02d}"}
    except ValueError:
        return None


def _farm_anotacoes(site, produtos_by_site, plantio_by_site):
    """Lista de {"data_iso"|"dia_mes", "tipo"} (`tipo` = 'ts'/'sulco'/
    'folha'/'plantio') pra marcar no grafico de Esporos e de Risco de
    Infeccao (aba Graficos, `montarGraficoEsporos`/`montarGraficoRisco`
    em graficos.html) -- reune TS/Sulco/Folha (aba Anotacoes de Safra) e
    Plantio (Dados de Plantio) de TODAS as safras dessa fazenda (o
    grafico pode olhar qualquer periodo, nao so' uma safra). Deduplicado
    por (data, tipo) -- uma fazenda com quimico E biologico no mesmo dia
    do mesmo momento vira UMA marca so', nao duas sobrepostas."""
    vistos = set()
    resultado = []
    for (safra, momento, tipo), linhas in produtos_by_site.get(site, {}).items():
        for linha in linhas:
            parsed = _parse_data_anotacao(linha.get("data_anotacao"))
            if not parsed:
                continue
            chave = (parsed["data_iso"] or parsed["dia_mes"], momento)
            if chave in vistos:
                continue
            vistos.add(chave)
            resultado.append({**parsed, "tipo": momento})
    for safra, linhas in plantio_by_site.get(site, {}).items():
        for linha in linhas:
            parsed = _parse_data_anotacao(linha.get("data_plantio"))
            if not parsed:
                continue
            chave = (parsed["data_iso"] or parsed["dia_mes"], "plantio")
            if chave in vistos:
                continue
            vistos.add(chave)
            resultado.append({**parsed, "tipo": "plantio"})
    return resultado


@app.route("/graficos")
@login_required
def graficos():
    fim = _hoje_cuiaba()
    inicio = fim - timedelta(days=14)
    permitidos = _sites_permitidos_usuario()
    uf_por_site = _uf_por_site()
    if permitidos is not None:
        uf_por_site = {s: uf for s, uf in uf_por_site.items() if s in permitidos}
    site_countries = models.get_all_site_countries()
    estacoes_disponiveis = sorted(uf_por_site.keys(), key=_ordem_fazenda)
    # Pais (nao mais Estado -- generaliza melhor com fazenda fora do
    # Brasil, onde "Estado" nao faz sentido/nao existe): mesmo pais
    # registrado da fazenda (aba Fazendas) que ja decide qual provedor de
    # estacao `_uf_por_site` usa pra achar a UF/regiao, entao filtrar por
    # ele continua 100% consistente com o filtro de Estacao.
    paises_presentes = sorted(
        {site_countries.get(s, countries.DEFAULT_COUNTRY) for s in uf_por_site},
        key=lambda c: countries.get_country(c)["nome"],
    )
    # Filtro de Cultura -- so' as culturas escolhidas em alguma safra nas
    # abas Recomendacoes (seletor "Cultura atual" de cada fazenda), das
    # fazendas que o usuario enxerga.
    culturas_em_uso = sorted({
        v["cultura"] for (site, _), v in models.get_all_farm_culturas().items()
        if v["cultura"] and v["cultura"] != CULTURA_TODOS and site in uf_por_site
    }, key=str.lower)
    return render_template(
        "graficos.html", inicio=inicio.isoformat(), fim=fim.isoformat(),
        culturas_disponiveis=culturas_em_uso,
        estacoes_disponiveis=[
            {
                "nome": s, "nome_exibicao": _nome_exibicao(s), "uf": uf_por_site.get(s),
                "pais": site_countries.get(s, countries.DEFAULT_COUNTRY),
            }
            for s in estacoes_disponiveis
        ],
        paises_disponiveis=[{"code": c, "nome": countries.get_country(c)["nome"]} for c in paises_presentes],
    )


@app.route("/graficos/dados")
@login_required
def graficos_dados():
    """JSON consumido pelo JS da aba Graficos -- um grafico por doenca
    (lista dinamica, vem do que ja apareceu no BioScout, ver
    `data_reader.read_unique_display_names`), cada um com uma serie de
    esporos (preta) e uma de risco de germinacao 0-100% (azul) por
    estacao visivel. A curva de esporos e' capada no `maximo` cadastrado
    da doenca -- quando o valor real passa disso, o ponto fica marcado
    em `excedido` (seta vermelha no grafico) em vez de deixar a linha
    disparar acima do teto."""
    fim = _parse_iso_date(request.args.get("fim")) or _hoje_cuiaba()
    inicio = _parse_iso_date(request.args.get("inicio")) or (fim - timedelta(days=14))
    if inicio > fim:
        inicio, fim = fim, inicio

    permitidos = _sites_permitidos_usuario()
    uf_por_site = _uf_por_site()
    if permitidos is not None:
        uf_por_site = {s: uf for s, uf in uf_por_site.items() if s in permitidos}
    todos_sites = sorted(uf_por_site.keys(), key=_ordem_fazenda)
    site_countries = models.get_all_site_countries()

    estacoes_sel = [s for s in request.args.getlist("estacao") if s]
    paises_sel = [p for p in request.args.getlist("pais") if p]
    if estacoes_sel:
        sites = [s for s in todos_sites if s in estacoes_sel]
    elif paises_sel:
        sites = [s for s in todos_sites if site_countries.get(s, countries.DEFAULT_COUNTRY) in paises_sel]
    else:
        # Sem filtro escolhido -- mostra tudo. Antes tinha um default
        # fixo pro estado "MT" (Mato Grosso), mas com fazendas em mais de
        # um pais nao ha' mais um estado/regiao obviamente "principal" pra
        # supor sozinho.
        sites = todos_sites
    # So restringe as doencas mostradas ao que a(s) estacao(oes)
    # selecionada(s) realmente tem cadastrado quando o filtro bateu com
    # alguma fazenda de verdade -- ver `filtro_estacao_aplicado` mais
    # abaixo (usa `spore_lookup`/vizinhas, calculado depois de `sites`).
    cultura_sel = (request.args.get("cultura") or "").strip()
    if cultura_sel:
        # Mesma regra das abas Recomendacoes: fazenda com essa cultura em
        # alguma safra (Safra/2a/3a); doencas pela matriz Doencas x
        # Culturas (`_filter_cards_by_cultura`) -- doenca sem marcacao
        # nenhuma na matriz nunca e' escondida.
        culturas_by_site = models.get_all_farm_culturas()
        sites = [
            s for s in sites
            if any(culturas_by_site.get((s, safra), {}).get("cultura") == cultura_sel for safra, _ in models.SAFRAS)
        ]
        if not sites:
            return jsonify({
                "inicio": inicio.isoformat(), "fim": fim.isoformat(), "dias": [],
                "doencas": [], "rosa_ventos": {}, "num_dias_previsao": 0, "anotacoes": {},
            })
    filtro_estacao_aplicado = bool(estacoes_sel) and bool(sites)
    if not sites:
        # Filtro nao bateu com nenhuma fazenda (ex.: catalogo de estacoes
        # fora do ar na primeira chamada, ou filtro de pais sem
        # fazenda cadastrada) -- mostra tudo em vez de pagina vazia.
        sites = todos_sites

    dias = []
    d = inicio
    while d <= fim:
        dias.append(d.isoformat())
        d += timedelta(days=1)

    # Risco de infeccao previsto pros proximos 5 dias -- so' quando o
    # filtro esta olhando "ate' agora" (senao nao faz sentido colar
    # previsao do tempo real numa janela historica antiga). Reusa a
    # mesma chamada de Open-Meteo que a aba Manejo/PDF ja faz por
    # fazenda (com cache, `_get_weather_for_site`) -- forecast_days=6 ja'
    # vem na resposta, so' nao era guardado antes (ver
    # `weather_forecast.get_weather_forecast`).
    hoje = _hoje_cuiaba()
    dias_previsao = []
    if fim >= hoje - timedelta(days=2):
        dias_previsao = [d for d in (
            (hoje + timedelta(days=i)).isoformat() for i in range(1, 6)
        ) if d not in dias]
    dias_previsao_set = set(dias_previsao)
    dias = dias + dias_previsao

    # `_weather_coords_all()` (nao `_coords_all()`) -- a previsao horaria
    # daqui precisa respeitar a estacao escolhida em
    # `weather_station_overrides` igual toda outra tela (Painel,
    # Recomendacoes, WhatsApp, PDF), senao o `_weather_cache` compartilhado
    # fica com o clima calculado na coordenada ERRADA (a propria fazenda,
    # nao a estacao) -- bug real ja visto em producao (fazenda com estacao
    # escolhida na aba Fazendas nao refletia isso no relatorio, porque
    # essa rota "envenenava" o cache primeiro com a coordenada errada).
    coords_all = _weather_coords_all()
    previsao_horaria_por_site = {}
    if dias_previsao:
        _prefetch_weather(sites, coords_all)
    for site in sites:
        if not dias_previsao:
            break
        clima = _get_weather_for_site(site, coords_all)
        previsao_horaria_por_site[site] = (clima or {}).get("previsao_horaria_por_dia", {})

    translations = models.get_all_disease_translations()
    spore_lookup = data_reader.build_disease_concentration_lookup(data_reader.read_spore_counts())
    # Clima so' de fonte externa (pedido do usuario): do BioScout, so'
    # esporos -- `_completar_clima_horario` mais abaixo preenche tudo.
    hourly_lookup = {}
    device_by_site = data_reader.read_site_device_ids()

    def _horas_do_dia(site, dia_iso):
        if dia_iso in dias_previsao_set:
            return previsao_horaria_por_site.get(site, {}).get(dia_iso)
        return hourly_lookup.get((device_by_site.get(site), dia_iso))

    # Fazenda virtual/estimada nao tem leitura de esporo nem estacao
    # propria -- os dias HISTORICOS de esporos/risco/vento dela vem do
    # mesmo IDW (Inverse Distance Weighting) usado no Mapa Interpolado,
    # dia a dia, a partir das fazendas reais dentro do raio dela (sempre
    # as mesmas, independente do filtro Estado/Estacao da pagina --
    # `estacoes_no_raio` usa `coords_reais_todas`, nao `sites`). Os dias
    # de PREVISAO continuam vindo da previsao de verdade na coordenada
    # do proprio ponto (`_horas_do_dia` acima, ja funciona pra fazenda
    # virtual desde que ela tenha coordenada -- ver `_get_weather_for_site`).
    coords_reais_todas = data_reader.read_site_coordinates()
    vizinhas_por_ponto_virtual = {
        vf["site_name"]: virtual_farms.estacoes_no_raio(vf["lat"], vf["lon"], vf["raio_km"], coords_reais_todas)
        for vf in models.get_all_virtual_farms()
        if vf["site_name"] in sites
    }
    sites_reais_clima = set(sites) | {real for vizinhas in vizinhas_por_ponto_virtual.values() for real, _ in vizinhas}
    hourly_lookup = _completar_clima_horario(
        hourly_lookup, device_by_site, sites_reais_clima, [d for d in dias if d not in dias_previsao_set]
    )

    # Mesma ordem (nome cientifico) ja usada na aba Doencas -- os graficos
    # aparecem na mesma hierarquia, em vez da ordem crua de
    # `read_unique_display_names` (aparicao no CSV/BioScout). Pedido
    # explicito do usuario.
    doencas_en = sorted(
        data_reader.read_unique_display_names(),
        key=lambda en: _chave_alfabetica(translations.get(en, {}).get("nome_cientifico") or ""),
    )
    if cultura_sel:
        doenca_culturas = models.get_doenca_culturas()
        doencas_en = [en for en in doencas_en if not doenca_culturas.get(en) or cultura_sel in doenca_culturas[en]]
    if filtro_estacao_aplicado:
        # So' traz doenca realmente cadastrada na(s) estacao(oes)
        # selecionada(s) -- antes mostrava TODA doenca ja vista em
        # qualquer fazenda, inclusive as de cultura completamente
        # diferente da fazenda escolhida (ex.: doenca de uva aparecendo,
        # em branco, pro grafico de uma fazenda de soja). Pedido explicito
        # do usuario. Fazenda virtual/estimada usa a doenca das vizinhas
        # reais que alimentam sua interpolacao (`vizinhas_por_ponto_virtual`),
        # ja' que ela nunca tem leitura propria.
        doencas_por_site = {}
        for s, doenca in spore_lookup:
            doencas_por_site.setdefault(s, set()).add(doenca)
        doencas_das_estacoes = set()
        for site in sites:
            fontes = [real for real, _ in vizinhas_por_ponto_virtual[site]] if site in vizinhas_por_ponto_virtual else [site]
            for fonte in fontes:
                doencas_das_estacoes |= doencas_por_site.get(fonte, set())
        doencas_en = [en for en in doencas_en if en in doencas_das_estacoes]
    elif paises_sel:
        # Mesma hierarquia "Doenca x Pais" da aba Doencas (mirror de
        # "Doenca x Cultura"): doenca sem nenhum pais marcado nunca e'
        # escondida pelo filtro -- so' filtra quem ja foi classificado,
        # pra nao sumir um alerta novo/nao classificado por engano. A
        # matriz guarda o pais por NOME (texto livre, editavel -- ver
        # `models.set_paises_doenca_slots`), enquanto esse filtro vem por
        # CODIGO ISO (`countries.py`) -- converte pra nome antes de
        # cruzar; so' funciona pro pais cujo nome na matriz ainda bate
        # com o nome oficial (se alguem renomear o slot, esse pais para
        # de casar com o filtro ate' renomear de volta -- risco aceito,
        # o campo virou texto livre de proposito).
        doenca_paises = models.get_doenca_paises()
        nomes_sel = {countries.get_country(c)["nome"] for c in paises_sel}
        doencas_en = [
            en for en in doencas_en
            if not doenca_paises.get(en) or doenca_paises[en] & nomes_sel
        ]

    doencas_payload = []
    for doenca_en in doencas_en:
        info = translations.get(doenca_en, {})
        series = []
        risco_por_site, vento_por_site = {}, {}
        for site in sites:
            if site in vizinhas_por_ponto_virtual:
                vizinhas = vizinhas_por_ponto_virtual[site]

                historico_por_vizinha = {
                    real_site: {h["data"]: h for h in spore_lookup.get((real_site, doenca_en), [])}
                    for real_site, _ in vizinhas
                }
                esporos_interp = virtual_farms.interpolar_serie_diaria(
                    vizinhas, dias,
                    lambda real_site, dia_iso: (historico_por_vizinha.get(real_site, {}).get(dia_iso) or {}).get("concentracao"),
                )
                limites_modelo = {}
                for real_site, _ in vizinhas:
                    lista = spore_lookup.get((real_site, doenca_en), [])
                    if lista:
                        limites_modelo = {k: lista[-1].get(k) for k in ("warn", "danger", "maximo")}
                        break
                maximo_modelo = limites_modelo.get("maximo")

                esporos, excedido = [], []
                for v in esporos_interp:
                    if v is None:
                        esporos.append(None)
                        excedido.append(False)
                    elif maximo_modelo and v > maximo_modelo:
                        esporos.append(round(maximo_modelo, 1))
                        excedido.append(True)
                    else:
                        esporos.append(round(v, 1))
                        excedido.append(False)

                if not all(v is None for v in esporos):
                    series.append({
                        "estacao": site, "estacao_nome": _nome_exibicao(site), "esporos": esporos, "esporos_excedido": excedido,
                        "limite_warn": limites_modelo.get("warn"), "limite_danger": limites_modelo.get("danger"),
                        "limite_maximo": limites_modelo.get("maximo"),
                    })

                risco_idw = virtual_farms.interpolar_serie_diaria(
                    vizinhas, dias,
                    lambda real_site, dia_iso: calc_risco_diario_pct(
                        info, hourly_lookup.get((device_by_site.get(real_site), dia_iso))
                    ),
                )
                risco_do_ponto, vento_do_ponto = [], []
                for idx, dia_iso in enumerate(dias):
                    if dia_iso in dias_previsao_set:
                        risco_do_ponto.append(calc_risco_diario_pct(info, _horas_do_dia(site, dia_iso)))
                        vento_do_ponto.append(data_reader.vento_predominante_do_dia(_horas_do_dia(site, dia_iso)))
                        continue
                    risco_do_ponto.append(round(risco_idw[idx]) if risco_idw[idx] is not None else None)
                    # Direcao do vento nao da pra' interpolar por IDW
                    # numerico (e' categorica) -- media circular simples
                    # das vizinhas com dado naquele dia, mesmo espirito
                    # (sem peso por distancia) ja usado logo abaixo pra
                    # media entre fazendas selecionadas.
                    direcoes = [
                        d for d in (
                            data_reader.vento_predominante_do_dia(hourly_lookup.get((device_by_site.get(rs), dia_iso)))
                            for rs, _ in vizinhas
                        ) if d
                    ]
                    vento_do_ponto.append(data_reader.media_circular_direcoes(direcoes))
                risco_por_site[site] = risco_do_ponto
                vento_por_site[site] = vento_do_ponto
                continue

            historico_lista = spore_lookup.get((site, doenca_en), [])
            historico = {h["data"]: h for h in historico_lista}
            ultimo = historico_lista[-1] if historico_lista else {}
            esporos, excedido = [], []
            for dia_iso in dias:
                ponto = historico.get(dia_iso)
                if ponto is None or ponto["concentracao"] is None:
                    esporos.append(None)
                    excedido.append(False)
                    continue
                maximo, conc = ponto.get("maximo"), ponto["concentracao"]
                if maximo and conc > maximo:
                    esporos.append(round(maximo, 1))
                    excedido.append(True)
                else:
                    esporos.append(round(conc, 1))
                    excedido.append(False)

            if not all(v is None for v in esporos):
                series.append({
                    "estacao": site, "estacao_nome": _nome_exibicao(site), "esporos": esporos, "esporos_excedido": excedido,
                    "limite_warn": ultimo.get("warn"), "limite_danger": ultimo.get("danger"), "limite_maximo": ultimo.get("maximo"),
                })

            # Risco/vento calculados por TODAS as estacoes do filtro
            # (mesmo as sem leitura de esporo) -- viram uma unica serie
            # media logo abaixo, nunca uma por fazenda.
            risco_por_site[site] = [calc_risco_diario_pct(info, _horas_do_dia(site, d)) for d in dias]
            vento_por_site[site] = [
                data_reader.vento_predominante_do_dia(_horas_do_dia(site, d)) for d in dias
            ]

        # Media entre as fazendas selecionadas -- com 1 so' fazenda, e' a
        # media de 1 valor (ela mesma), entao o comportamento nao muda
        # nesse caso.
        risco_medio, vento_medio = [], []
        for idx in range(len(dias)):
            valores = [risco_por_site[s][idx] for s in sites if risco_por_site[s][idx] is not None]
            risco_medio.append(round(sum(valores) / len(valores)) if valores else None)
            direcoes = [vento_por_site[s][idx] for s in sites if vento_por_site[s][idx]]
            vento_medio.append(data_reader.media_circular_direcoes(direcoes))

        if series or any(v is not None for v in risco_medio):
            doencas_payload.append({
                "doenca_en": doenca_en,
                "doenca_pt": data_reader.get_doenca(doenca_en, translations),
                "series": series,
                "risco_pct": risco_medio,
                "vento_predominante": vento_medio,
            })
    # NAO reordena por doenca_pt aqui -- doencas_payload ja' sai na ordem
    # certa (hierarquia da aba Doencas, nome cientifico) porque foi
    # construido iterando `doencas_en` (ja' ordenado la em cima); um sort
    # por doenca_pt aqui reordenava tudo de novo, alfabetico por nome em
    # portugues, desfazendo a hierarquia -- bug que fazia os graficos
    # nao respeitarem a ordem da aba Doencas mesmo com `doencas_en`
    # ordenado certo.

    devices = [device_by_site.get(s) for s in sites if device_by_site.get(s)]
    vento_contagem = data_reader.contar_direcoes_vento(hourly_lookup, devices, dias)
    total_vento = sum(vento_contagem.values())
    rosa_ventos = {
        d: (round(c / total_vento * 100, 1) if total_vento else 0)
        for d, c in vento_contagem.items()
    }

    produtos_by_site = models.get_all_farm_produtos()
    plantio_by_site = models.get_all_farm_plantio()
    anotacoes = {}
    for site in sites:
        marcas = _farm_anotacoes(site, produtos_by_site, plantio_by_site)
        if marcas:
            anotacoes[site] = marcas

    return jsonify({
        "inicio": inicio.isoformat(), "fim": fim.isoformat(), "dias": dias,
        "doencas": doencas_payload, "rosa_ventos": rosa_ventos,
        "num_dias_previsao": len(dias_previsao), "anotacoes": anotacoes,
    })


def _country_map_context():
    """Variaveis compartilhadas por /mapa e /mapa-interpolado pra desenhar
    fronteira/grade de nuvens dos paises alem do Brasil -- ver
    templates/mapa.html e mapa_interpolado.html (mesmo mecanismo
    duplicado nos dois, como o resto da logica de fronteira ja era)."""
    country_boundary_urls = {}
    for code, info in countries.COUNTRIES.items():
        if info.get("boundary_mode") != "static_geoboundaries":
            continue
        country_boundary_urls[code] = {
            nivel: url_for("static", filename=caminho)
            for nivel, caminho in info["boundary_files"].items()
        }
    cloud_bboxes = {code: info["cloud_grid_bbox"] for code, info in countries.COUNTRIES.items()}
    return {
        "country_by_site": models.get_all_site_countries(),
        "country_boundary_urls": country_boundary_urls,
        "cloud_bboxes": cloud_bboxes,
        # Todo pais do registro (nao so os ativos) -- usado pra restringir
        # a busca de localidade (Nominatim) do Mapa Interpolado aos paises
        # que o app atende, sem travar num so' -- precisa incluir um pais
        # sem fazenda nenhuma ainda, ja que a busca serve justamente pra
        # ajudar a criar o primeiro ponto la'.
        "country_codes_nominatim": ",".join(code.lower() for code in countries.COUNTRIES),
    }


@app.route("/mapa")
@login_required
def mapa():
    _maybe_auto_refresh()  # silencioso -- nao avisa mais no topo da pagina, so acontece
    if current_user.is_admin:
        permitted = _sites_ativos_para_admin()  # admin ve todas as fazendas ativas
    else:
        permitted = set(models.get_user_permitted_site_names(int(current_user.id)))
        if not permitted:
            return render_template("mapa.html", sites_data=[], no_access=True)
    cards_by_site = _cards_by_site_all(permitted, _load_translations())
    coords = _coords_all()
    virtual_names = models.virtual_farm_site_names()
    site_countries = models.get_all_site_countries()
    active_countries = countries.active_country_codes(site_countries)
    sites_data = []

    def _plotar_site(site, lat, lon, cards, tipo=""):
        # Mesmo limiar que ja esconde as caixas de doenca no Painel (ver
        # _nivel_dados_defasados/DADOS_BLOQUEIO_DIAS) -- fazenda sem leitura
        # nova ha mais de 15 dias entra como "inativa" no mapa: pino menor
        # e tooltip so' com o nome (ver mapa.html), pra nao passar uma falsa
        # impressao de leitura recente.
        inativo = _nivel_dados_site(site, cards) == "bloqueado"
        sites_data.append({
            "site": site,
            "nome_exibicao": _nome_exibicao(site),
            "lat": lat,
            "lon": lon,
            "virtual": site in virtual_names,
            "tipo": tipo,
            "cards": sorted(cards, key=lambda c: c["doenca"]),
            "ultima_leitura": models.fmt_data_br(max(c["data"] for c in cards)) if cards else "-",
            "inativo": inativo,
        })

    for site, cards in cards_by_site.items():
        if site not in coords or not cards:
            continue  # fazenda sem coordenada, ou fazenda virtual sem estacao real no raio -- nao da pra plotar
        lat, lon = coords[site]
        _plotar_site(site, lat, lon, cards)

    # Pontos "so' clima" (ver mapa_interpolado/create_virtual_farm) sempre
    # aparecem no mapa, mesmo sem nenhuma fazenda real por perto -- nao
    # existe doenca nenhuma pra estimar aqui, entao o filtro "sem card =
    # nao plota" (acima) nao se aplica a eles.
    for vf in models.get_all_virtual_farms():
        if vf.get("tipo") != "clima":
            continue
        if permitted is not None and vf["site_name"] not in permitted:
            continue
        _plotar_site(vf["site_name"], vf["lat"], vf["lon"], [], tipo="clima")

    sites_data.sort(key=lambda s: _ordem_fazenda(s["site"]))
    # Fronteira desenhada mesmo sem fazenda no pais, desde que o pais ja'
    # tenha integracao de estacao oficial de verdade funcionando (Chile
    # via DMC) -- pedido explicito ("aplicar somente se tiver estacao no
    # pais"). Pinos de estacao sairam do Mapa (pedido do usuario, 16/10/2026).
    active_country_codes_mapa = sorted(active_countries | countries.paises_com_estacoes())
    return render_template(
        "mapa.html", sites_data=sites_data, no_access=False,
        abrir_no_estado=not current_user.is_admin and len(permitted) == 1,
        active_country_codes_mapa=active_country_codes_mapa,
        **_country_map_context(),
    )


@app.route("/mapa-interpolado")
@aba_required("mapa_interpolado")
def mapa_interpolado():
    """Versao "duplicada" do Mapa pra admin criar pontos estimados (sem
    estacao fisica) -- a concentracao de cada doenca nesses pontos e'
    interpolada (IDW) a partir das fazendas reais dentro do raio
    escolhido (ver `virtual_farms.py`). So admin ve essa tela (criar um
    ponto estimado e' uma configuracao, tipo as outras em
    "Configuracoes")."""
    cards_reais = get_dashboard_data(None, _load_translations())
    coords_reais = data_reader.read_site_coordinates()
    # Desativada so' some do que e' EXIBIDO (sites_data) -- cards_reais/
    # coords_reais continuam cheios, ja que sao a base de interpolacao
    # (IDW) dos pontos virtuais logo abaixo (nao pode encolher so' porque
    # uma fazenda vizinha foi desativada).
    desativadas = models.get_deactivated_site_names()

    sites_data = []
    for site, cards in cards_reais.items():
        if site not in coords_reais or site in desativadas:
            continue
        lat, lon = coords_reais[site]
        sites_data.append({
            "site": site, "nome_exibicao": _nome_exibicao(site), "lat": lat, "lon": lon, "virtual": False,
            "cards": sorted(cards, key=lambda c: c["doenca"]),
            "ultima_leitura": models.fmt_data_br(max(c["data"] for c in cards)),
            # Mesmo padrao de pino do Mapa normal (ver mapa.html/app.mapa):
            # sem leitura nova ha mais de 15 dias = pino menor e tooltip so
            # com o nome, ver _nivel_dados_defasados/DADOS_BLOQUEIO_DIAS.
            "inativo": _nivel_dados_site(site, cards) == "bloqueado",
        })

    site_countries = models.get_all_site_countries()
    pontos_virtuais = []
    for vf in models.get_all_virtual_farms():
        tipo = vf.get("tipo") or "doenca"
        if tipo == "clima":
            # Ponto so' pra dar clima de referencia ao cliente -- nao
            # tenta interpolar doenca nenhuma (nem faz sentido: nao ha'
            # concentracao de esporo pra estimar aqui, so' temperatura/
            # umidade/previsao, que ja vem de `_weather_coords_all` na
            # coordenada do ponto ou da estacao escolhida abaixo, exatamente
            # como pra uma fazenda real -- ver `_get_weather_for_site`).
            cards, estacoes_usadas = [], []
        else:
            cards, estacoes_usadas = virtual_farms.interpolar_cards(
                vf["lat"], vf["lon"], vf["raio_km"], cards_reais, coords_reais
            )
        _pais_code_vf = site_countries.get(vf["site_name"], countries.DEFAULT_COUNTRY)
        pontos_virtuais.append({
            **vf,
            "tipo": tipo,
            "criado_em": models.fmt_data_br(vf["criado_em"]),
            "cards": cards,
            "estacoes_usadas": estacoes_usadas,
            "country_code": _pais_code_vf,
            "pais_nome": countries.get_country(_pais_code_vf)["nome"],
            "ativo": vf["site_name"] not in desativadas,
        })
        if (cards or tipo == "clima") and vf["site_name"] not in desativadas:
            sites_data.append({
                "site": vf["site_name"], "nome_exibicao": vf["nome"], "lat": vf["lat"], "lon": vf["lon"], "virtual": True,
                "tipo": tipo,
                "cards": cards,
                "ultima_leitura": models.fmt_data_br(max(c["data"] for c in cards)) if cards else "-",
                "raio_km": vf["raio_km"],
                "estacoes_usadas": estacoes_usadas,
                "inativo": _nivel_dados_site(vf["site_name"], cards) == "bloqueado",
            })

    sites_data.sort(key=lambda s: _ordem_fazenda(s["site"]))
    pontos_virtuais.sort(key=lambda p: _ordem_fazenda(p["site_name"]))
    # Sinal de celular no balao de cada fazenda (pedido do Alan, 01/11/2026);
    # fazenda fora do Brasil fica sem.
    linha_sinal = get_template_attribute("_sinal_celular.html", "linha")
    sinal_por_site = _sinal_das_fazendas([s["site"] for s in sites_data])
    for s in sites_data:
        if sinal_por_site.get(s["site"]):
            s["sinal_html"] = str(linha_sinal(sinal_por_site[s["site"]]))

    # Todo pais ativo (com fazenda/ponto marcado) MAIS todo pais que ja'
    # tem integracao de estacao oficial (`paises_com_estacoes`) -- a tela
    # serve pra criar o PRIMEIRO ponto de um pais novo, entao a fronteira
    # do Chile precisa aparecer mesmo sem fazenda la'. NAO inclui todo pais
    # do registro (baixaria ~14MB de contorno por visita a toa). Pinos de
    # estacao sairam daqui (pedido do usuario, 16/10/2026).
    active_interpolado = countries.active_country_codes(site_countries) | countries.paises_com_estacoes()
    info_cobertura = sinal_celular.info()

    return render_template(
        "mapa_interpolado.html", sites_data=sites_data, pontos_virtuais=pontos_virtuais,
        countries=countries.COUNTRIES, active_country_codes_interpolado=sorted(active_interpolado),
        # Sinal de celular (ex-aba Sinal telefonia): camada por operadora
        info_cobertura=info_cobertura,
        versao_cobertura=int(os.path.getmtime(sinal_celular.COBERTURA_PATH)) if info_cobertura.get("baixado_em") else 0,
        operadoras_sinal=[{"nome": o, "cor": sinal_celular.cor(o)} for o in sinal_celular.operadoras()],
        **_country_map_context(),
    )


@app.route("/solo")
@aba_required("solo")
def solo():
    """Tipo de solo de cada fazenda (real ou virtual/estimada) + mapa com
    as zonas de solo do Brasil coloridas por cima -- mesmo esqueleto do
    Mapa Interpolado (fronteiras/tiles reaproveitados via
    `_country_map_context()`). O tipo de solo em si e' resolvido sozinho
    pela coordenada (`_auto_detectar_solos_novos`, hook em
    `_ensure_sites_synced`) -- essa rota so' le' o que ja' foi resolvido
    e cacheado (`models.get_all_site_solos`), nunca recalcula na hora."""
    coords = _coords_all()
    virtual_names = models.virtual_farm_site_names()
    site_solos = models.get_all_site_solos()

    sites_data = []
    for site, (lat, lon) in coords.items():
        sites_data.append({
            "site": site,
            "nome_exibicao": _nome_exibicao(site),
            "lat": lat, "lon": lon,
            "virtual": site in virtual_names,
            "solo": site_solos.get(site),
        })
    sites_data.sort(key=lambda s: _ordem_fazenda(s["site"]))

    return render_template(
        "solo.html", sites_data=sites_data,
        soil_geojson_url=url_for("static", filename="soils/br_solos.geojson"),
        cores_por_ordem=soil_service.CORES_POR_ORDEM, cor_default=soil_service.COR_DEFAULT,
    )


@app.route("/mapa-interpolado/detectar-pais")
@aba_required("mapa_interpolado")
def detectar_pais_ponto_virtual():
    """Pais de uma coordenada (contorno oficial completo de TODO pais
    cadastrado, nao so' os ativos -- ver `countries.detectar_pais_por_coordenada`)
    pra preencher sozinho o seletor de Pais ao clicar/arrastar o pino no
    Mapa Interpolado. Fica no backend (em vez de testar no navegador contra
    o GeoJSON do contorno) justamente pra poder detectar QUALQUER um dos
    12 paises registrados sem precisar baixar o contorno de todos eles no
    navegador so' pra cobrir esse caso (ver o cuidado ja tomado com
    `paises_com_estacoes`/leveza)."""
    try:
        lat = float(request.args.get("lat"))
        lon = float(request.args.get("lon"))
    except (TypeError, ValueError):
        abort(400)
    return jsonify({"country_code": countries.detectar_pais_por_coordenada(lat, lon)})


@app.route("/mapa-interpolado/nuvens-grade", methods=["POST"])
@login_required
def nuvens_grade():
    """Devolve a previsao de nuvens (6 dias) pra uma grade de coordenadas
    -- usado pela camada de nuvens do Mapa e do Mapa Interpolado, que cobre
    o Brasil inteiro (mesma logica de "cobrir o pais todo" ja usada pras
    divisas de municipio: o frontend recorta a grade pelo contorno oficial
    do Brasil, aqui so' busca o clima dos pontos que sobraram). Um unico
    pedido em lote pra Open-Meteo (aceita varias coordenadas separadas por
    virgula numa chamada so'), em vez de uma chamada por ponto da grade.
    So' login (nao precisa ser admin) -- o Mapa normal, usado por qualquer
    usuario, tambem tem essa camada."""
    corpo = request.get_json(silent=True) or {}
    lats = corpo.get("lats") or []
    lons = corpo.get("lons") or []
    if not lats or len(lats) != len(lons):
        abort(400)
    if len(lats) > 600:
        abort(400)
    return jsonify(weather_forecast.get_cloud_forecast_grid(lats, lons))


@app.route("/mapa-interpolado/chuva-grade", methods=["POST"])
@login_required
def chuva_grade():
    """Mesma mecanica de `nuvens_grade` (grade em lote, uma unica chamada
    Open-Meteo), so' que chuva acumulada (mm) em vez de nuvens -- usada
    pelo heatmap de chuva do Mapa Interpolado (ver
    weather_forecast.get_rain_forecast_grid)."""
    corpo = request.get_json(silent=True) or {}
    lats = corpo.get("lats") or []
    lons = corpo.get("lons") or []
    if not lats or len(lats) != len(lons):
        abort(400)
    if len(lats) > 3000:
        abort(400)
    if corpo.get("sem_esperar"):
        resultado = weather_forecast.get_rain_forecast_grid(lats, lons, esperar=False)
        if resultado is None:
            return jsonify({"pendente": True}), 202
        return jsonify(resultado)
    return jsonify(weather_forecast.get_rain_forecast_grid(lats, lons))


@app.route("/sinal-telefonia")
@login_required
def sinal_telefonia():
    """A aba Sinal telefonia virou camada do Mapas de Estudo (pedido do
    Alan, 01/11/2026) -- link antigo cai la'."""
    return redirect(url_for("mapa_interpolado"))


@app.route("/sinal-telefonia/tile/<operadora>/<int:z>/<int:x>/<int:y>.png")
@aba_required("mapa_interpolado")
def sinal_telefonia_tile(operadora, z, x, y):
    if operadora not in sinal_celular.PRINCIPAIS or not (0 <= z <= 18 and 0 <= x < 2 ** z and 0 <= y < 2 ** z):
        abort(404)
    resp = app.response_class(sinal_celular.tile(operadora, z, x, y), mimetype="image/png")
    resp.headers["Cache-Control"] = "private, max-age=86400"  # URL muda (?v=) quando a base e' baixada de novo
    return resp


@app.route("/sinal-telefonia/atualizar", methods=["POST"])
@master_required
def atualizar_cobertura_anatel():
    """Baixa de novo os mapas de cobertura da Anatel (o agendador ja' faz
    sozinho uma vez por mes)."""
    sinal_celular.atualizar_em_segundo_plano(forcar=True)
    flash("Baixando os mapas de cobertura da Anatel -- leva alguns minutos. Recarregue a página para acompanhar.", "success")
    return redirect(url_for("mapa_interpolado"))


@app.route("/sinal-celular")
@aba_required("mapa_interpolado")
def sinal_celular_ponto():
    try:
        lat, lon = float(request.args.get("lat")), float(request.args.get("lon"))
    except (TypeError, ValueError):
        abort(400)
    if not _no_brasil(lat, lon):
        return jsonify({"operadoras": None, "html": "", "pendente": False, "fora_do_brasil": True})
    operadoras = sinal_celular.estimar(lat, lon)
    quadro = get_template_attribute("_sinal_celular.html", "quadro")
    # pendente: frequencias sendo consultadas na Anatel -- a tela pergunta de novo
    return jsonify({"operadoras": operadoras, "html": str(quadro(operadoras)),
                    "pendente": sinal_celular.frequencias_pendentes(lat, lon)})


@app.route("/mapa-interpolado/adicionar", methods=["POST"])
@aba_required("mapa_interpolado")
def adicionar_ponto_virtual():
    nome = request.form.get("nome", "").strip()
    try:
        lat = float(request.form.get("lat", ""))
        lon = float(request.form.get("lon", ""))
        raio_km = float(request.form.get("raio_km", ""))
    except ValueError:
        flash("Preencha nome, coordenadas e raio (numeros validos) pra adicionar um ponto estimado.", "error")
        return redirect(url_for("mapa_interpolado"))
    if not nome or raio_km <= 0:
        flash("Nome obrigatorio e o raio precisa ser maior que zero.", "error")
        return redirect(url_for("mapa_interpolado"))
    country_code = request.form.get("country_code", "BR")
    if country_code not in countries.COUNTRIES:
        country_code = countries.DEFAULT_COUNTRY
    tipo = request.form.get("tipo", "doenca")
    if tipo not in ("doenca", "clima"):
        tipo = "doenca"
    try:
        models.create_virtual_farm(
            nome, lat, lon, raio_km, criado_por=current_user.username, country_code=country_code, tipo=tipo
        )
    except sqlite3.IntegrityError:
        flash(f"Ja existe um ponto estimado chamado '{nome}' -- escolha outro nome.", "error")
        return redirect(url_for("mapa_interpolado"))
    flash(f"Ponto estimado '{nome}' criado -- defina quem pode ve-lo na aba Usuarios.", "success")
    return redirect(url_for("mapa_interpolado"))


@app.route("/mapa-interpolado/editar", methods=["POST"])
@aba_required("mapa_interpolado")
def editar_ponto_virtual():
    site_name = request.form.get("site_name", "")
    if not models.get_virtual_farm(site_name):
        abort(404)
    nome = request.form.get("nome", "").strip()
    try:
        lat = float(request.form.get("lat", ""))
        lon = float(request.form.get("lon", ""))
        raio_km = float(request.form.get("raio_km", ""))
    except ValueError:
        flash("Preencha nome, coordenadas e raio (numeros validos) pra editar o ponto estimado.", "error")
        return redirect(url_for("mapa_interpolado"))
    if not nome or raio_km <= 0:
        flash("Nome obrigatorio e o raio precisa ser maior que zero.", "error")
        return redirect(url_for("mapa_interpolado"))
    country_code = request.form.get("country_code", "BR")
    if country_code not in countries.COUNTRIES:
        country_code = countries.DEFAULT_COUNTRY
    tipo = request.form.get("tipo", "doenca")
    if tipo not in ("doenca", "clima"):
        tipo = "doenca"
    try:
        models.update_virtual_farm(site_name, nome, lat, lon, raio_km, country_code=country_code, tipo=tipo)
    except sqlite3.IntegrityError:
        flash(f"Ja existe um ponto estimado chamado '{nome}' -- escolha outro nome.", "error")
        return redirect(url_for("mapa_interpolado"))
    # Mesmo motivo do pop em `save_weather_station_override` -- sem isso,
    # `_weather_cache` continuava servindo o clima da coordenada ANTIGA
    # por ate' `WEATHER_CACHE_TTL_SECONDS` (30min) depois de mover o ponto.
    _weather_cache.pop(site_name, None)
    flash(f"Ponto estimado '{nome}' atualizado.", "success")
    return redirect(url_for("mapa_interpolado"))


@app.route("/mapa-interpolado/remover", methods=["POST"])
@aba_required("mapa_interpolado")
def remover_ponto_virtual():
    site_name = request.form.get("site_name", "")
    if not models.get_virtual_farm(site_name):
        abort(404)
    models.delete_virtual_farm(site_name)
    flash("Ponto estimado removido.", "success")
    return redirect(url_for("mapa_interpolado"))


def _get_clima_virtual_farm_or_404(site_name):
    """Fazenda virtual do tipo 'clima' com esse site_name, ou aborta com
    404 -- usado pelas rotas de `/alertas-clima` pra garantir que ninguem
    manda WhatsApp/gera PDF de um ponto que nao e' 'clima' (ou nem existe)
    por essas rotas especificas."""
    vf = models.get_virtual_farm(site_name)
    if not vf or vf.get("tipo") != "clima":
        abort(404)
    return vf


@app.route("/alertas-clima")
@master_required
def alertas_clima():
    """Antiga aba Alertas Clima -- os pontos "so clima" agora ficam no fim
    da aba Recomendacoes (pedido do Alan, 18/10/2026)."""
    return redirect(url_for("recommendations_default") + "#pontos-clima")


def _pontos_clima():
    """Pontos "so clima" (ver `mapa_interpolado`/`create_virtual_farm`,
    tipo='clima') no fim da aba Recomendacoes -- clima atual, estacao
    oficial de referencia e envio manual. A agenda fica na aba Envios.
    So' usuario Master ve (ver `recommendations`)."""
    site_countries = models.get_all_site_countries()
    weather_overrides = models.get_all_weather_station_overrides()
    whatsapp_destinos_by_site = models.get_all_sites_whatsapp_recipients()
    coords = _weather_coords_all()
    desativadas = models.get_deactivated_site_names()

    pontos = []
    for vf in models.get_all_virtual_farms():
        if vf.get("tipo") != "clima":
            continue
        site = vf["site_name"]
        if site in desativadas:
            continue
        weather = _get_weather_for_site(site, coords)
        vento_dia = _rosa_ventos(site, [weather_forecast._agora_cuiaba().date().isoformat()])
        escolha = weather_overrides.get(site)
        _pais_code_clima = site_countries.get(site, countries.DEFAULT_COUNTRY)
        pontos.append({
            **vf,
            "country_code": _pais_code_clima,
            "pais_nome": countries.get_country(_pais_code_clima)["nome"],
            "estacoes_proximas": countries.estacoes_mais_proximas_global(vf["lat"], vf["lon"], _pais_code_clima, n=2),
            "estacao_selecionada": escolha["codigo"] if escolha else "",
            "estacao_pais": escolha["country_code"] if escolha else "",
            "whatsapp_destinos": len(whatsapp_destinos_by_site.get(site, [])),
            "weather": weather,
            "vento_dia": vento_dia,
            "whatsapp_text": _format_whatsapp_message(
                site, [], weather=weather, is_virtual=True, mostrar_secao_doencas=False,
                vento_dia=vento_dia, ponto_clima=True,
            ),
        })
    pontos.sort(key=lambda p: _ordem_fazenda(p["site_name"]))
    return pontos


@app.route("/alertas-clima/whatsapp/<path:site_name>", methods=["POST"])
@master_required
def alertas_clima_whatsapp(site_name):
    _get_clima_virtual_farm_or_404(site_name)
    if (volta := _whatsapp_desconectado(url_for("recommendations_default") + "#pontos-clima")):
        return volta
    enviar_texto = bool(request.form.get("enviar_texto"))
    ok_texto, _, message = _enviar_whatsapp_manual_escalonado(site_name, None, enviar_texto, False)
    ok = ok_texto if enviar_texto else True
    nome_exibicao = _nome_exibicao(site_name)
    if ok:
        flash(f"WhatsApp de '{nome_exibicao}' enviado para {message}.", "success")
    else:
        flash(f"Falha ao enviar WhatsApp de '{nome_exibicao}': {message}", "error")
    return redirect(url_for("recommendations_default") + "#pontos-clima")


@app.route("/alertas-clima/nota/save", methods=["POST"])
@master_required
def save_site_climate_note():
    site_name = request.form.get("site_name")
    _get_clima_virtual_farm_or_404(site_name)
    models.set_site_climate_note(site_name, request.form.get("nota", ""))
    return _save_response(f"Anotacao de '{_nome_exibicao(site_name)}' salva.", "recommendations_default")


SAFRA_LABELS = dict(models.SAFRAS)


@app.route("/recommendations")
@login_required
def recommendations_default():
    """Aba unica "Recomendacoes" do menu (antes uma por safra): abre na
    ultima safra vista nessa sessao."""
    safra = session.get("safra_recomendacoes")
    return redirect(url_for("recommendations", safra=safra if safra in SAFRA_LABELS else "safra1"))


@app.route("/recommendations/<safra>")
@login_required
def recommendations(safra):
    if safra not in SAFRA_LABELS:
        abort(404)
    session["safra_recomendacoes"] = safra
    _maybe_auto_refresh()  # silencioso -- nao avisa mais no topo da pagina, so acontece
    if current_user.is_admin:
        permitted = _sites_ativos_para_admin()  # admin ve todas as fazendas ativas
    else:
        permitted = set(models.get_user_permitted_site_names(int(current_user.id)))
        if not permitted:
            return render_template(
                "recommendations.html", sites_data=[], no_access=True,
                safra=safra, safra_label=SAFRA_LABELS[safra], safras=models.SAFRAS,
            )
    culturas_by_site = models.get_all_farm_culturas()
    raw_cards_by_site = _cards_by_site_all(permitted, _load_translations())
    cards_by_site = _filter_cards_by_cultura(
        raw_cards_by_site, culturas_by_site, models.get_doenca_culturas(), safra=safra
    )
    notes = models.get_all_recommendation_notes()
    coords = _weather_coords_all()
    _prefetch_weather(cards_by_site.keys(), coords)
    produtos_by_site = models.get_all_farm_produtos()
    plantio_by_site = models.get_all_farm_plantio()
    aplicacoes_by_site = models.get_all_farm_aplicacoes()
    virtual_names = models.virtual_farm_site_names()
    whatsapp_destinos_by_site = models.get_all_sites_whatsapp_recipients()
    sites_data = []
    for site, cards in cards_by_site.items():
        cultura_info = culturas_by_site.get((site, safra), {})
        diseases = _build_site_diseases(site, cards, notes, cultura=cultura_info.get("cultura") or "")
        weather = _get_weather_for_site(site, coords)
        for d in diseases:
            d["risco"] = _calc_risco_germinacao(d, weather)
            d["previsao_risco"] = _calc_previsao_risco_germinacao(d, weather)
        thumbnails = sorted(cards, key=lambda c: c["doenca"])
        existentes = produtos_by_site.get(site, {})
        # So' leitura -- a secao Folha e' editada na aba Manejos.
        produtos_folha = {
            tipo: [l for l in existentes.get((safra, "folha", tipo), []) if l.get("nome") or l.get("ingrediente_ativo")]
            for tipo in models.TIPOS_PRODUTO
        }
        dias_desde_atualizacao = None
        if cultura_info.get("updated_at"):
            try:
                dt = datetime.strptime(cultura_info["updated_at"], "%Y-%m-%d %H:%M:%S")
                dias_desde_atualizacao = (datetime.now() - dt).days
            except ValueError:
                pass
        # Data da leitura mais recente entre TODAS as doencas da fazenda
        # (nao so as que estao em Atencao/Perigo) -- senao some do
        # cabecalho quando a fazenda nao tem nenhum alerta no momento,
        # mesmo com leitura fresca.
        leitura_data = max((c["data"] for c in cards if c.get("data")), default=None)
        dias_sem_leitura = _dias_sem_leitura(raw_cards_by_site.get(site, []))
        nivel_dados = _nivel_dados_site(site, raw_cards_by_site.get(site, []))
        is_virtual = site in virtual_names
        # Estacao sem leitura ha muito tempo -- mesma regra de
        # `_send_site_whatsapp`: em vez de nao mostrar/enviar nada, o
        # preview (e o que realmente sera enviado) vira um relatorio SO'
        # de clima (diseases=[]), igual ao dos pontos da aba Alertas
        # Clima -- os cards de doenca na TELA continuam mostrando a
        # ultima leitura conhecida normalmente, so o WhatsApp que muda.
        # O risco CLIMATICO (so' clima x germinacao, nao depende do
        # sensor) ainda entra, mesmo bloqueado -- ver
        # `_riscos_climaticos_relevantes`.
        riscos_sem_alerta = _riscos_climaticos_sem_alerta(cards, weather, diseases, site, safra)
        riscos_climaticos = _riscos_climaticos_relevantes(cards, weather, site, safra) if nivel_dados == "bloqueado" else riscos_sem_alerta
        whatsapp_text = _format_whatsapp_message(
            site, [] if nivel_dados == "bloqueado" else diseases, weather=weather,
            produtos=_farm_produtos_estoque(site, safra),
            is_virtual=is_virtual, cultura=cultura_info.get("cultura") or "",
            safra_label=SAFRA_LABELS[safra],
            plantio_linhas=plantio_by_site.get(site, {}).get(safra, []),
            aplicacoes_linhas=aplicacoes_by_site.get(site, {}).get(safra, []),
            leitura_bioscout=models.fmt_data_br(leitura_data),
            mostrar_secao_doencas=nivel_dados != "bloqueado",
            riscos_climaticos=riscos_climaticos,
        )
        plantio_linhas = [l for l in plantio_by_site.get(site, {}).get(safra, []) if any(l.values())]
        aplicacoes_linhas = [l for l in aplicacoes_by_site.get(site, {}).get(safra, []) if any(l.values())]
        # Doencas cadastradas a mao: sempre no quadro de risco pelo clima
        # (ver `_com_doencas_manuais`); sem germinacao, aviso pra preencher.
        riscos_tela = _com_doencas_manuais(site, safra, weather, riscos_sem_alerta)
        com_germinacao = {d["doenca_en"] for d in riscos_tela}
        manuais_sem_germinacao = [
            c["doenca"] for c in _doencas_manuais_do_site(site, safra) if c["doenca_en"] not in com_germinacao
        ]
        sites_data.append({
            "site": site, "nome_exibicao": _nome_exibicao(site), "diseases": diseases, "thumbnails": thumbnails,
            "riscos_sem_alerta": riscos_tela,
            "manuais_sem_germinacao": manuais_sem_germinacao,
            "virtual": is_virtual,
            "weather": weather,
            "leitura_data": leitura_data,
            "produtos_folha": produtos_folha,
            "cultura": cultura_info.get("cultura") or "",
            "dias_desde_atualizacao": dias_desde_atualizacao,
            "dias_sem_leitura": dias_sem_leitura,
            "nivel_dados": nivel_dados,
            "whatsapp_destinos": len(whatsapp_destinos_by_site.get(site, [])),
            "whatsapp_lista": [
                {"telefone": r["telefone"], "nome": r["username"]} for r in whatsapp_destinos_by_site.get(site, [])
            ],
            "whatsapp_text": whatsapp_text,
            "plantio_linhas": plantio_linhas,
            "aplicacoes_linhas": aplicacoes_linhas,
            "vento_dia": _rosa_ventos(site, [weather_forecast._agora_cuiaba().date().isoformat()]),
        })
    sites_data.sort(key=lambda s: _ordem_fazenda(s["site"]))
    any_whatsapp_configured = any(s["whatsapp_destinos"] > 0 for s in sites_data)
    return render_template(
        "recommendations.html", sites_data=sites_data, no_access=False,
        any_whatsapp_configured=any_whatsapp_configured,
        pontos_clima=_pontos_clima() if current_user.is_master else [],
        safra=safra, safra_label=SAFRA_LABELS[safra], safras=models.SAFRAS,
    )


def _safra_or_default(form):
    safra = form.get("safra", "safra1")
    return safra if safra in SAFRA_LABELS else "safra1"


def _save_response(message, endpoint=None, ok=True, **redirect_kwargs):
    """Resposta padrao de toda rota de salvar: se veio do salvamento
    automatico (header X-Autosave, ver base.html), devolve JSON pro
    JS mostrar o status sem recarregar a pagina; senao (JS desabilitado,
    ou chamada direta) cai no flash + redirect classico de sempre."""
    if request.headers.get("X-Autosave") == "1":
        return {"ok": ok, "message": message}
    flash(message, "success" if ok else "error")
    return redirect(url_for(endpoint, **redirect_kwargs)) if endpoint else redirect(request.referrer or url_for("mapa"))


@app.route("/recommendations/whatsapp/<path:site_name>", methods=["POST"])
@login_required
def send_site_whatsapp(site_name):
    if not current_user.is_admin:
        allowed = set(models.get_user_permitted_site_names(int(current_user.id)))
        if site_name not in allowed:
            abort(403)
    if (volta := _whatsapp_desconectado(url_for("recommendations_default"))):
        return volta
    safra = _safra_or_default(request.form)
    # checkboxes "enviar_texto"/"enviar_pdf" na frente dos botoes de PDF/
    # Copiar (recommendations.html) -- deixam a pessoa escolher, nesse
    # envio manual especifico, so' texto, so' PDF ou os dois (padrao,
    # quando os dois vem marcados).
    enviar_texto = bool(request.form.get("enviar_texto"))
    enviar_pdf = bool(request.form.get("enviar_pdf"))
    destinos = None
    if request.form.get("escolher_destinos"):
        # Escolha de quem recebe (pedido do usuario, 16/10/2026): so' os
        # cadastrados marcados -- nunca um telefone qualquer vindo do
        # formulario por esse campo -- mais, opcionalmente, um numero
        # digitado na hora (nao fica salvo em lugar nenhum).
        cadastrados = dict(_site_whatsapp_destinations(site_name))
        destinos = [(tel, cadastrados[tel]) for tel in request.form.getlist("destino") if tel in cadastrados]
        numero = request.form.get("numero_telefone", "").strip()
        if numero:
            telefone = _telefone_from_form(request.form)
            if not _is_valid_phone(telefone):
                flash(f"Numero '{numero}' invalido -- use DDD + numero (ex.: 65 99999-9999), com o codigo do pais ao lado.", "error")
                return redirect(url_for("recommendations", safra=safra))
            if telefone not in dict(destinos):
                destinos.append((telefone, f"numero digitado ({telefone})"))
        if not destinos:
            flash("Escolha pelo menos um destinatario ou digite um numero.", "error")
            return redirect(url_for("recommendations", safra=safra))
    ok_texto, ok_pdf, message = _enviar_whatsapp_manual_escalonado(site_name, safra, enviar_texto, enviar_pdf, destinos=destinos)
    ok = (ok_texto if enviar_texto else True) and (ok_pdf if enviar_pdf else True)
    nome_exibicao = _nome_exibicao(site_name)
    if ok:
        flash(f"WhatsApp de '{nome_exibicao}' enviado para {message}.", "success")
    else:
        flash(f"Falha ao enviar WhatsApp de '{nome_exibicao}': {message}", "error")
    return redirect(url_for("recommendations", safra=safra))



@app.route("/recommendations/pdf/<path:site_name>")
@login_required
def recommendation_pdf(site_name):
    """PDF com o mesmo conteudo do relatorio de WhatsApp (clima, cultura,
    doencas em Atencao/Perigo, produtos ja disponiveis) mais as datas de
    plantio e de pulverizacao daquela safra (aba Fazendas) -- pra
    encaminhar por email/impressao em vez de copiar texto. Estacao sem
    leitura ha muito tempo -- mesma regra de `_send_site_whatsapp`: o PDF
    vira SO' de clima (diseases=[]), igual ao dos pontos da aba Alertas
    Clima, em vez de mostrar doenca com dado velho demais pra confiar."""
    if not current_user.is_admin:
        allowed = set(models.get_user_permitted_site_names(int(current_user.id)))
        if site_name not in allowed:
            abort(403)
    safra = _safra_or_default(request.args)
    translations = _load_translations()
    raw_cards = _resolve_site_cards(site_name, translations)
    culturas_by_site = models.get_all_farm_culturas()
    cultura = _cultura_label(site_name, safra, culturas_by_site)
    bloqueado = _nivel_dados_site(site_name, raw_cards) == "bloqueado"
    coords = _weather_coords_all()
    weather = _get_weather_for_site(site_name, coords)
    riscos_climaticos = []
    if bloqueado:
        diseases = []
        riscos_climaticos = _riscos_climaticos_relevantes(raw_cards, weather, site_name, safra)
    else:
        cards_by_site = _filter_cards_by_cultura(
            {site_name: raw_cards}, culturas_by_site, models.get_doenca_culturas(), safra=safra
        )
        notes = models.get_all_recommendation_notes()
        diseases = _build_site_diseases(site_name, cards_by_site.get(site_name, []), notes, cultura=cultura)
    for d in diseases:
        d["risco"] = _calc_risco_germinacao(d, weather)
        d["previsao_risco"] = _calc_previsao_risco_germinacao(d, weather)
    if not bloqueado:
        riscos_climaticos = _riscos_climaticos_sem_alerta(cards_by_site.get(site_name, []), weather, diseases, site_name, safra)
    produtos = _farm_produtos_estoque(site_name, safra)
    leitura_bioscout = models.fmt_data_br(max((c["data"] for c in raw_cards), default=None))
    _, filename, pdf_bytes = _build_site_pdf(
        site_name, diseases, weather, produtos, cultura, safra, leitura_bioscout=leitura_bioscout,
        mostrar_secao_doencas=not bloqueado, riscos_climaticos=riscos_climaticos,
    )
    return send_file(io.BytesIO(pdf_bytes), mimetype="application/pdf", as_attachment=True, download_name=filename)


@app.route("/recommendations/whatsapp-days/save", methods=["POST"])
@login_required
def save_whatsapp_days():
    if current_user.is_admin:
        allowed_sites = None
    else:
        allowed_sites = set(models.get_user_permitted_site_names(int(current_user.id)))

    site_name = request.form.get("site_name")
    if allowed_sites is not None and site_name not in allowed_sites:
        abort(403)
    days = {int(v) for v in request.form.getlist("weekday")}
    models.set_whatsapp_days(site_name, days)
    hora = request.form.get("hora")
    minuto = request.form.get("minuto")
    minuto_val = int(minuto) if minuto and minuto.isdigit() and 0 <= int(minuto) <= 59 else 0
    if hora and hora.isdigit() and 0 <= int(hora) <= 23:
        models.set_whatsapp_schedule_hora(site_name, "texto", int(hora), minuto_val)
    return _save_response(f"Agenda de WhatsApp (texto) de '{_nome_exibicao(site_name)}' salva.", "whatsapp_gerenciamento")


@app.route("/recommendations/whatsapp-days-pdf/save", methods=["POST"])
@login_required
def save_whatsapp_days_pdf():
    if current_user.is_admin:
        allowed_sites = None
    else:
        allowed_sites = set(models.get_user_permitted_site_names(int(current_user.id)))

    site_name = request.form.get("site_name")
    if allowed_sites is not None and site_name not in allowed_sites:
        abort(403)
    days = {int(v) for v in request.form.getlist("weekday")}
    models.set_whatsapp_days_pdf(site_name, days)
    hora = request.form.get("hora")
    minuto = request.form.get("minuto")
    minuto_val = int(minuto) if minuto and minuto.isdigit() and 0 <= int(minuto) <= 59 else 0
    if hora and hora.isdigit() and 0 <= int(hora) <= 23:
        models.set_whatsapp_schedule_hora(site_name, "pdf", int(hora), minuto_val)
    return _save_response(f"Agenda de WhatsApp (PDF) de '{_nome_exibicao(site_name)}' salva.", "whatsapp_gerenciamento")


@app.route("/fazendas")
@login_required
def fazendas():
    virtual_names = models.virtual_farm_site_names()
    # Fazenda desativada some daqui tambem -- reativar so' na tela
    # Fazendas ocultas (pedido do Alan, 18/10/2026).
    desativadas = models.get_deactivated_site_names()
    # Ponto "so clima" (aba Alertas Clima) nao aparece aqui -- nao tem
    # doenca/produtos/plantio/aplicacoes pra anotar (o titulo da pagina e'
    # literalmente "Anotacoes de Safra"), e ja tem tela propria dedicada
    # pra configurar tudo que ele precisa (estacao de referencia, agenda
    # de WhatsApp). Pedido explicito do usuario.
    virtual_clima_names = {vf["site_name"] for vf in models.get_all_virtual_farms() if vf.get("tipo") == "clima"}
    # Catalogo de produtos/variedades pra autocompletar (Produtos,
    # Aplicacoes, Plantio -- cresce sozinho a cada save, ver
    # models._catalogar_produto/_catalogar_variedade) -- buscado uma
    # unica vez, compartilhado entre todas as fazendas da pagina (pedido
    # explicito do usuario: catalogo unico, nao por fazenda).
    catalogo_produtos = models.get_all_catalogo_produtos()
    catalogo_variedades = models.get_all_catalogo_variedades()
    if current_user.is_admin:
        sites = sorted((set(read_sites()) | virtual_names) - virtual_clima_names - desativadas, key=_ordem_fazenda)
    else:
        sites = sorted(set(models.get_user_permitted_site_names(int(current_user.id))) - virtual_clima_names, key=_ordem_fazenda)
        if not sites:
            return render_template(
                "fazendas.html", sites_data=[], no_access=True,
                catalogo_produtos=catalogo_produtos, catalogo_variedades=catalogo_variedades,
            )

    produtos_by_site = models.get_all_farm_produtos()
    plantio_by_site = models.get_all_farm_plantio()
    aplicacoes_by_site = models.get_all_farm_aplicacoes()
    espacamento_by_site = models.get_all_farm_espacamento_plantio()
    coords = _coords_all()
    overrides = models.get_all_weather_station_overrides()
    site_countries = models.get_all_site_countries()
    grades = [
        ("ts", "TS (Tratamento de Sementes)"),
        ("sulco", "Sulco (aplicacao no sulco de plantio)"),
        ("folha", "Folha (aplicacao foliar)"),
    ]
    tipos = [("quimico", "Fungicidas quimicos"), ("biologico", "Biologicos")]
    culturas_by_site = models.get_all_farm_culturas()
    doenca_culturas = models.get_doenca_culturas()
    translations = _load_translations()
    notes = models.get_all_recommendation_notes()
    notas_padrao = _fungicida_notas_cached()

    # Estacao mais proxima de TODAS as fazendas, calculada em LOTE (uma
    # unica busca de catalogo por pais, nao um pool de threads por
    # fazenda -- bug de performance real, ver
    # `countries.estacoes_mais_proximas_global_lote`).
    sites_com_coords = [s for s in sites if coords.get(s)]
    pontos = [(*coords[s], site_countries.get(s, countries.DEFAULT_COUNTRY)) for s in sites_com_coords]
    resultados_lote = countries.estacoes_mais_proximas_global_lote(pontos, n=2) if pontos else []
    estacoes_proximas_por_site = dict(zip(sites_com_coords, resultados_lote))

    sites_data = []
    sinal_por_site = _sinal_das_fazendas(sites)
    for site in sites:
        existentes = produtos_by_site.get(site, {})
        plantio_existente = plantio_by_site.get(site, {})
        aplicacoes_existente = aplicacoes_by_site.get(site, {})
        safras_data = []
        for safra, safra_label in models.SAFRAS:
            secoes = []
            for momento, momento_label in grades:
                grupos = []
                for tipo, tipo_label in tipos:
                    linhas = list(existentes.get((safra, momento, tipo), []))
                    linhas_min = max(1, len(linhas) + 1)
                    while len(linhas) < linhas_min:
                        linhas.append({"data_anotacao": "", "nome": "", "ingrediente_ativo": ""})
                    grupos.append({"tipo": tipo, "titulo": tipo_label, "linhas": linhas})
                secoes.append({"momento": momento, "titulo": momento_label, "grupos": grupos})

            plantio_linhas = list(plantio_existente.get(safra, []))
            # So' de exibicao -- um <input type="date"> fica em branco pra
            # qualquer valor que nao seja ISO, entao uma data antiga salva
            # em outro formato (ex. "10/09/2025") precisa ser normalizada
            # aqui pra' aparecer certa no seletor. O banco NAO e' reescrito
            # -- dado novo ja' sai ISO direto do proprio input nativo.
            for linha in plantio_linhas:
                linha["data_plantio"] = models.parse_data_flexivel(linha["data_plantio"]) or ""
            plantio_min = max(1, len(plantio_linhas) + 1)
            while len(plantio_linhas) < plantio_min:
                plantio_linhas.append({"data_plantio": "", "talhao": "", "variedade": "", "ciclo_dias": ""})

            aplicacoes_linhas = list(aplicacoes_existente.get(safra, []))
            for linha in aplicacoes_linhas:
                linha["data_aplicacao"] = models.parse_data_flexivel(linha["data_aplicacao"]) or ""
            aplicacoes_min = max(1, len(aplicacoes_linhas) + 1)
            while len(aplicacoes_linhas) < aplicacoes_min:
                aplicacoes_linhas.append({"data_aplicacao": "", "talhao": "", "fungicidas_quimicos": "", "fungicidas_biologicos": ""})

            safras_data.append({
                "safra": safra, "titulo": safra_label, "secoes": secoes,
                "cultura": culturas_by_site.get((site, safra), {}).get("cultura") or "",
                "plantio": plantio_linhas, "aplicacoes": aplicacoes_linhas,
                "espacamento": espacamento_by_site.get((site, safra)) or "",
            })
        country_code = site_countries.get(site, countries.DEFAULT_COUNTRY)
        # Ja calculado em lote acima (mistura todo provedor cadastrado,
        # nao so' o do pais da propria fazenda -- deixa escolher a
        # estacao de referencia realmente mais perto, mesmo que seja de
        # outro pais, fazenda perto de fronteira por exemplo).
        estacoes_proximas = estacoes_proximas_por_site.get(site, [])
        escolha = overrides.get(site)
        sites_data.append({
            "site": site, "nome_exibicao": _nome_exibicao(site), "safras": safras_data,
            "anotacoes": _anotacoes_da_fazenda(
                site, {s["cultura"] for s in safras_data}, translations, doenca_culturas, notes, notas_padrao,
            ),
            "virtual": site in virtual_names,
            "estacoes_proximas": estacoes_proximas,
            "estacao_selecionada": escolha["codigo"] if escolha else "",
            "estacao_pais": escolha["country_code"] if escolha else "",
            "country_code": country_code,
            "ativo": site not in desativadas,
            "sinal_celular": sinal_por_site.get(site),
        })

    return render_template(
        "fazendas.html", sites_data=sites_data, no_access=False,
        culturas_ativas=models.get_culturas_ativas(),
        countries=countries.COUNTRIES,
        catalogo_produtos=catalogo_produtos, catalogo_variedades=catalogo_variedades,
    )


def _get_ndvi_preview(site_name):
    """Devolve a pre-visualizacao pendente desse site (dict) ou None -- se
    tiver expirado (`NDVI_PREVIEW_TTL_SECONDS`), remove do cache e devolve
    None tambem, como se nunca tivesse existido."""
    entrada = _ndvi_preview_cache.get(site_name)
    if not entrada:
        return None
    timestamp, dados = entrada
    if time.time() - timestamp > NDVI_PREVIEW_TTL_SECONDS:
        _ndvi_preview_cache.pop(site_name, None)
        return None
    return dados


@app.route("/ndvi")
@aba_required("ndvi")
def ndvi():
    virtual_names = models.virtual_farm_site_names()
    if current_user.is_admin:
        sites = sorted((set(read_sites()) | virtual_names) - models.get_deactivated_site_names(), key=_ordem_fazenda)
    else:
        sites = sorted(models.get_user_permitted_site_names(int(current_user.id)), key=_ordem_fazenda)
        if not sites:
            return render_template("ndvi.html", sites_data=[], no_access=True, credenciais_ok=True)

    cars_by_site = models.get_all_farm_ndvi_cars()
    coords = _coords_all()
    sites_data = [
        {
            "site": site,
            "nome_exibicao": _nome_exibicao(site),
            "cars": cars_by_site.get(site, []),
            "coords": coords.get(site),
            "historico": models.get_farm_ndvi_historico(site),
            "preview": _get_ndvi_preview(site),
            # Quem recebe o NDVI e' marcado na aba Envios (`toggle_ndvi_ativo`).
            "ndvi_destinatarios": [
                {"telefone": telefone, "rotulo": rotulo}
                for telefone, rotulo in _site_whatsapp_destinations(site)
                if telefone in ativos
            ],
        }
        for site in sites
        for ativos in [models.get_ndvi_ativos(site)]
    ]
    return render_template(
        "ndvi.html", sites_data=sites_data, no_access=False,
        credenciais_ok=ndvi_service.credenciais_configuradas(),
        hoje=datetime.now(timezone.utc).date().isoformat(),
    )


def _checar_acesso_site(site_name):
    if not current_user.is_admin:
        allowed = set(models.get_user_permitted_site_names(int(current_user.id)))
        if site_name not in allowed:
            abort(403)


@app.route("/ndvi/agendamento/save", methods=["POST"])
@aba_required("envios")
def save_ndvi_agendamento():
    """Ativa/desativa o envio automatico de NDVI dessa fazenda (data de
    inicio + frequencia semanal/quinzenal/mensal -- ver
    `app._run_scheduled_ndvi_sends`). Frequencia vazia (opcao
    "Desativado" no seletor) apaga o agendamento."""
    site_name = request.form.get("site_name")
    _checar_acesso_site(site_name)
    frequencia = request.form.get("frequencia", "").strip()
    if frequencia:
        data_texto = request.form.get("data_inicio", "").strip()
        try:
            data_inicio = datetime.strptime(data_texto, "%Y-%m-%d").date()
        except ValueError:
            return _save_response("Data de inicio invalida.", "whatsapp_gerenciamento", ok=False)
        hora_texto = request.form.get("hora", "")
        hora = int(hora_texto) if hora_texto.isdigit() and 0 <= int(hora_texto) <= 23 else None
        models.set_ndvi_agendamento(site_name, data_inicio, frequencia, hora=hora)
        mensagem = f"Envio automatico de NDVI de '{_nome_exibicao(site_name)}' ativado ({frequencia}, a partir de {data_inicio.strftime('%d/%m/%Y')})."
    else:
        models.set_ndvi_agendamento(site_name, None, "")
        mensagem = f"Envio automatico de NDVI de '{_nome_exibicao(site_name)}' desativado."
    return _save_response(mensagem, "whatsapp_gerenciamento")


@app.route("/whatsapp")
@aba_required("envios")
def whatsapp_gerenciamento():
    """Pagina central de envios por WhatsApp -- agenda de texto, PDF e
    NDVI, mais quem recebe cada fazenda, tudo num lugar so' (antes
    espalhado entre Manejos, NDVI e Usuarios -- pedido explicito do
    usuario depois de um bug real ficar dificil de enxergar por causa
    dessa dispersao: 9 fazendas mandando quase juntas pro mesmo numero,
    cada uma configurada numa tela diferente, sem visao de conjunto).
    Mesma regra de acesso de `fazendas()`: admin ve todas as fazendas,
    usuario comum so' as permitidas. Ordem da lista: primeiro toda
    fazenda de "Monitorar Doencas" (real ou virtual, ordem alfabetica
    pelo nome de exibicao), DEPOIS toda fazenda "So Clima" (virtual,
    tipo='clima', tambem alfabetica) -- pedido explicito do usuario,
    cada grupo com seu proprio selo visivel no cabecalho do card."""
    virtual_names = models.virtual_farm_site_names()
    virtual_clima_names = {vf["site_name"] for vf in models.get_all_virtual_farms() if vf.get("tipo") == "clima"}
    if current_user.is_admin:
        sites_set = (set(read_sites()) | virtual_names) - models.get_deactivated_site_names()
    else:
        sites_set = set(models.get_user_permitted_site_names(int(current_user.id)))
        if not sites_set:
            return render_template("whatsapp_gerenciamento.html", sites_data=[], no_access=True)

    sites_doenca = sorted(sites_set - virtual_clima_names, key=_ordem_fazenda)
    sites_clima = sorted(sites_set & virtual_clima_names, key=_ordem_fazenda)
    sites = sites_doenca + sites_clima

    gerenciamento = models.get_all_whatsapp_gerenciamento()
    cars_by_site = models.get_all_farm_ndvi_cars()
    hora_default = models.get_whatsapp_send_hour()
    todos_usuarios = models.get_all_users() if current_user.is_admin else []

    sites_data = []
    for site in sites:
        dados = gerenciamento.get(site) or {
            "dias_texto": set(), "dias_pdf": set(),
            "horarios": {"texto": hora_default, "texto_min": 0, "pdf": hora_default, "pdf_min": 0},
            "ndvi": None, "destinatarios": [], "user_ids_recebendo": set(),
        }
        sites_data.append({
            "site": site,
            "nome_exibicao": _nome_exibicao(site),
            "so_clima": site in virtual_clima_names,
            "dias_texto": dados["dias_texto"],
            "dias_pdf": dados["dias_pdf"],
            "horarios": dados["horarios"],
            "ndvi": dados["ndvi"],
            "tem_car": bool(cars_by_site.get(site)),
            "destinatarios": dados["destinatarios"],
            "user_ids_recebendo": dados["user_ids_recebendo"],
            "ndvi_ativos": models.get_ndvi_ativos(site) if cars_by_site.get(site) else set(),
        })

    return render_template(
        "whatsapp_gerenciamento.html", sites_data=sites_data, no_access=False,
        weekday_labels=list(enumerate(WEEKDAY_LABELS)),
        hoje=datetime.now(timezone.utc).date().isoformat(),
        todos_usuarios=todos_usuarios,
    )


@app.route("/whatsapp/destinatarios/save", methods=["POST"])
@aba_required("envios")
def save_whatsapp_site_recipients():
    """So' admin edita quem recebe (mesmo nivel de acesso de
    `admin_report_permissions`/`admin_user_subordinados` hoje) -- so'
    mexe em USUARIOS (`models.set_site_whatsapp_user_recipients`);
    subordinado continua sendo editado na tela do dono dele (ver
    docstring de `set_site_whatsapp_user_recipients`)."""
    site_name = request.form.get("site_name")
    user_ids = [int(v) for v in request.form.getlist("user_ids") if v.isdigit()]
    models.set_site_whatsapp_user_recipients(site_name, user_ids)
    return _save_response(f"Destinatarios de '{_nome_exibicao(site_name)}' salvos.", "whatsapp_gerenciamento")


@app.route("/ndvi/destinatario/toggle", methods=["POST"])
@aba_required("envios")
def toggle_ndvi_ativo():
    """Marca/desmarca um destinatario como "ativo" pra NDVI dessa
    fazenda -- persistido (`models.set_ndvi_ativo`), usado tanto pra
    pre-marcar a caixa no envio manual quanto pra decidir quem recebe o
    envio automatico agendado. Comeca desmarcado por padrao (pedido
    explicito do usuario) -- so' fica ativo depois de marcado aqui."""
    site_name = request.form.get("site_name")
    _checar_acesso_site(site_name)
    telefone = request.form.get("telefone", "")
    ativo = request.form.get("ativo") == "1"
    if telefone not in {p for p, _ in _site_whatsapp_destinations(site_name)}:
        return {"ok": False, "error": "Destinatario invalido pra essa fazenda."}, 400
    models.set_ndvi_ativo(site_name, telefone, ativo)
    return {"ok": True}


@app.route("/ndvi/car/save", methods=["POST"])
@aba_required("ndvi")
def save_ndvi_car():
    site_name = request.form.get("site_name")
    _checar_acesso_site(site_name)
    arquivo = request.files.get("kml_file")
    nome = request.form.get("nome", "").strip()
    try:
        if arquivo and arquivo.filename:
            conteudo = arquivo.read()
            if arquivo.filename.lower().endswith(".zip"):
                aneis = ndvi_service.parse_shapefile_zip(conteudo)
            else:
                aneis = ndvi_service.parse_kml_poligono(conteudo.decode("utf-8", errors="replace"))
            nome = nome or arquivo.filename
        else:
            aneis = ndvi_service.parse_kml_poligono(request.form.get("kml_texto", ""))
    except ValueError as exc:
        return _save_response(f"Nao foi possivel salvar o CAR de '{_nome_exibicao(site_name)}': {exc}", "ndvi", ok=False)
    nome = nome or f"CAR {len(models.get_farm_ndvi_cars(site_name)) + 1}"
    # Sempre grava KML de verdade (mesmo quando a origem foi um shapefile),
    # pra `pre_visualizar_ndvi` ter so' um formato pra reler do banco.
    # NAO substitui os CAR ja anexados -- cada chamada acrescenta mais um
    # (uma fazenda pode ter varios CAR, ex. propriedades vizinhas
    # arrendadas juntas; o NDVI gerado combina o poligono de todos).
    models.add_farm_ndvi_car(site_name, nome, ndvi_service.aneis_para_kml(aneis))
    return _save_response(f"CAR '{nome}' anexado a '{_nome_exibicao(site_name)}' -- agora e' so' clicar em \"Pre-visualizar\".", "ndvi")


@app.route("/ndvi/car/delete", methods=["POST"])
@aba_required("ndvi")
def delete_ndvi_car():
    site_name = request.form.get("site_name")
    _checar_acesso_site(site_name)
    car_id = request.form.get("car_id", type=int)
    if car_id is None:
        abort(400)
    models.delete_farm_ndvi_car(car_id, site_name)
    return _save_response(f"CAR removido de '{_nome_exibicao(site_name)}'.", "ndvi")


# Nome por extenso (pt-BR) de cada uma das 8 direcoes bucketadas em
# `data_reader._DIRECOES_VENTO` -- so' pro quadro desenhado na imagem
# NDVI (`ndvi_service.desenhar_informacoes`), que mostra o nome completo
# em vez da abreviacao (pedido explicito do usuario).
_NOME_DIRECAO_VENTO = {
    "N": "Norte", "NE": "Nordeste", "E": "Leste", "SE": "Sudeste",
    "S": "Sul", "SW": "Sudoeste", "W": "Oeste", "NW": "Noroeste",
}


def _contagem_vento(site_name, dias):
    """Quantas leituras horarias de `dias` ("YYYY-MM-DD") vieram de cada
    uma das 8 direcoes ({N: n, NE: n, ...}) -- mesma conta e mesma fonte
    da rosa dos ventos da aba Graficos. Fazenda sem device (virtual): a
    leitura Open-Meteo guardada da coordenada dela. None sem dado."""
    device = data_reader.read_site_device_ids().get(site_name)
    if device:
        # Mesma fonte da rosa da aba Graficos: estacao oficial / estacao
        # de referencia da fazenda (clima nunca vem do BioScout).
        lookup = _completar_clima_horario({}, {site_name: device}, [site_name], dias)
        contagem = data_reader.contar_direcoes_vento(lookup, [device], dias)
        return contagem if any(contagem.values()) else None
    latlon = _coords_all().get(site_name)
    if latlon:
        por_dia = weather_forecast.get_historico_por_dia(latlon[0], latlon[1], min(dias), max(dias))
        contagem = data_reader.contar_direcoes_vento({("om", d): h for d, h in por_dia.items()}, ["om"], dias)
        if any(contagem.values()):
            return contagem
    return None


def _rosa_ventos(site_name, dias):
    """{"codigo", "nome", "percentuais"} da direcao predominante + % de
    cada direcao (rosa dos ventos da tela/PDF de Recomendacoes)."""
    contagem = _contagem_vento(site_name, dias)
    if not contagem:
        return None
    total = sum(contagem.values())
    codigo = max(contagem, key=contagem.get)
    return {
        "codigo": codigo, "nome": _NOME_DIRECAO_VENTO[codigo],
        "percentuais": {d: round(n / total * 100, 1) for d, n in contagem.items()},
    }


def _chuva_vento_ultimos_30_dias(site_name, data_final):
    """Chuva acumulada (mm) e direcao de vento predominante (nome por
    extenso, ver `_NOME_DIRECAO_VENTO`) nos 30 dias ATE `data_final`
    (inclusive) -- pras linhas desenhadas na imagem NDVI (ver
    `ndvi_service.desenhar_informacoes`).

    Preferencia 1: fazenda com device BioScout -- clima de fonte externa
    da fazenda (estacao oficial / estacao de referencia, mesma fonte do
    Relatorio Diario/aba Graficos, ver `_completar_clima_horario`).

    Preferencia 2 (fazenda virtual/estimada, ou fazenda real sem leitura
    na janela -- sem device proprio pra' consultar): media das 3
    estacoes oficiais mais proximas da coordenada da fazenda
    (`countries.estacoes_mais_proximas_global`, mesmo catalogo usado
    pelo seletor de "estacao de referencia" da aba Fazendas), buscando o
    HISTORICO real (nao previsao -- endpoint diferente, sem o limite de
    "so' 1 dia pra tras" de `get_weather_forecast`) de cada uma via
    `weather_forecast.get_historico_por_dia`. Chuva de cada estacao e'
    somada nos 30 dias, depois tirada a media aritmetica entre as 3;
    vento de todas as estacoes/dias entra junto em
    `data_reader.contar_direcoes_vento` (mesma agregacao ja usada pra
    combinar mais de uma estacao no grafico de vento da aba Graficos).

    Devolve (None, None) se nenhuma das duas fontes tiver dado nenhum
    (sem device E sem estacao oficial perto o suficiente, ou rede fora
    do ar), pra' `desenhar_informacoes` simplesmente omitir essas linhas
    em vez de mostrar dado inventado."""
    dias_janela = [(data_final - timedelta(days=i)).isoformat() for i in range(30)]

    device = data_reader.read_site_device_ids().get(site_name)
    if device:
        hourly_lookup = _completar_clima_horario({}, {site_name: device}, [site_name], dias_janela)
        horas = [h for dia in dias_janela for h in hourly_lookup.get((device, dia), [])]
        if horas:
            chuva_acumulada = round(sum(h.get("chuva") or 0 for h in horas), 1)
            contagem = data_reader.contar_direcoes_vento(hourly_lookup, [device], dias_janela)
            codigo_vento = max(contagem, key=contagem.get) if any(contagem.values()) else None
            return chuva_acumulada, _NOME_DIRECAO_VENTO.get(codigo_vento, codigo_vento)

    latlon = _coords_all().get(site_name)
    if not latlon:
        return None, None
    site_country = models.get_site_country(site_name)
    estacoes = countries.estacoes_mais_proximas_global(*latlon, site_country, n=3)
    if not estacoes:
        return None, None

    desde = (data_final - timedelta(days=29)).isoformat()
    ate = data_final.isoformat()
    hourly_lookup_estacoes = {}
    chuvas_por_estacao = []
    chaves_estacoes = []
    for estacao in estacoes:
        por_dia = weather_forecast.get_historico_por_dia(estacao["lat"], estacao["lon"], desde, ate)
        if not por_dia:
            continue
        chave = estacao["codigo"]
        chaves_estacoes.append(chave)
        todas_horas = [h for horas_dia in por_dia.values() for h in horas_dia]
        chuvas_por_estacao.append(sum(h.get("chuva") or 0 for h in todas_horas if h.get("chuva") is not None))
        for dia, horas_dia in por_dia.items():
            hourly_lookup_estacoes[(chave, dia)] = horas_dia
    if not chuvas_por_estacao:
        return None, None

    chuva_acumulada = round(sum(chuvas_por_estacao) / len(chuvas_por_estacao), 1)
    contagem = data_reader.contar_direcoes_vento(hourly_lookup_estacoes, chaves_estacoes, dias_janela)
    codigo_vento = max(contagem, key=contagem.get) if any(contagem.values()) else None
    vento_predominante = _NOME_DIRECAO_VENTO.get(codigo_vento, codigo_vento)
    return chuva_acumulada, vento_predominante


@app.route("/ndvi/pre_visualizar", methods=["POST"])
@aba_required("ndvi")
def pre_visualizar_ndvi():
    site_name = request.form.get("site_name")
    _checar_acesso_site(site_name)
    cars_kml = models.get_farm_ndvi_cars_kml(site_name)
    if not cars_kml:
        return _save_response(f"'{_nome_exibicao(site_name)}' ainda nao tem nenhum CAR cadastrado.", "ndvi", ok=False)

    data_texto = request.form.get("data_alvo", "").strip()
    data_alvo = None
    if data_texto:
        try:
            data_alvo = datetime.strptime(data_texto, "%Y-%m-%d").date()
        except ValueError:
            return _save_response(f"Data invalida: '{data_texto}'.", "ndvi", ok=False)
        if data_alvo > datetime.now(timezone.utc).date():
            return _save_response("Nao e' possivel gerar NDVI de uma data no futuro.", "ndvi", ok=False)

    try:
        # Um item por CAR anexado (NAO junta os aneis de todos os CAR numa
        # lista so' antes de mandar pra `buscar_ndvi` -- ela precisa saber
        # onde um CAR termina e outro comeca, pra corrigir/filtrar ruido
        # de digitalizacao de cada CAR separadamente antes de combinar
        # tudo, ver `ndvi_service.geometria_combinada`).
        lista_de_aneis_por_car = [ndvi_service.parse_kml_poligono(kml) for kml in cars_kml]
    except ValueError as exc:
        return _save_response(f"Contorno de '{_nome_exibicao(site_name)}' invalido: {exc}", "ndvi", ok=False)
    resultado, erro = ndvi_service.buscar_ndvi(lista_de_aneis_por_car, data_alvo=data_alvo)
    if erro:
        return _save_response(f"Nao foi possivel gerar o NDVI de '{_nome_exibicao(site_name)}': {erro}", "ndvi", ok=False)
    chuva_acumulada, vento_predominante = _chuva_vento_ultimos_30_dias(site_name, resultado["data"])
    resultado["imagem"] = ndvi_service.desenhar_informacoes(
        resultado["imagem"], resultado["data"],
        chuva_acumulada_mm=chuva_acumulada, vento_predominante=vento_predominante,
    )
    _ndvi_preview_cache[site_name] = (time.time(), resultado)
    mensagem = f"Pre-visualizacao de '{_nome_exibicao(site_name)}' pronta (cena de {resultado['data'].strftime('%d/%m/%Y')}"
    if resultado["cobertura_nuvens"] is not None:
        mensagem += f", {resultado['cobertura_nuvens']:.0f}% de nuvens"
    mensagem += ") -- confira antes de salvar na galeria."
    return _save_response(mensagem, "ndvi")


@app.route("/ndvi/pre_visualizar/imagem/<path:site_name>")
@aba_required("ndvi")
def imagem_ndvi_preview(site_name):
    _checar_acesso_site(site_name)
    preview = _get_ndvi_preview(site_name)
    if not preview:
        abort(404)
    return send_file(io.BytesIO(preview["imagem"]), mimetype="image/png")


@app.route("/ndvi/pre_visualizar/confirmar", methods=["POST"])
@aba_required("ndvi")
def confirmar_ndvi_preview():
    site_name = request.form.get("site_name")
    _checar_acesso_site(site_name)
    preview = _get_ndvi_preview(site_name)
    if not preview:
        return _save_response("Essa pre-visualizacao expirou -- gere de novo.", "ndvi", ok=False)
    thumbnail = ndvi_service.gerar_thumbnail(preview["imagem"])
    models.add_farm_ndvi_historico(
        site_name, preview["data"].isoformat(), preview["imagem"], preview["cobertura_nuvens"], thumbnail
    )
    models.save_farm_ndvi_image(site_name, preview["imagem"])
    _ndvi_preview_cache.pop(site_name, None)
    return _save_response(f"NDVI de '{_nome_exibicao(site_name)}' salvo na galeria.", "ndvi")


@app.route("/ndvi/pre_visualizar/descartar", methods=["POST"])
@aba_required("ndvi")
def descartar_ndvi_preview():
    site_name = request.form.get("site_name")
    _checar_acesso_site(site_name)
    _ndvi_preview_cache.pop(site_name, None)
    return _save_response(f"Pre-visualizacao de '{_nome_exibicao(site_name)}' descartada.", "ndvi")


@app.route("/ndvi/imagem/<path:site_name>")
@aba_required("ndvi")
def imagem_ndvi(site_name):
    _checar_acesso_site(site_name)
    imagem = models.get_farm_ndvi_image(site_name)
    if not imagem:
        abort(404)
    return send_file(io.BytesIO(imagem), mimetype="image/png")


@app.route("/ndvi/imagem_historico/<int:historico_id>/<path:site_name>")
@aba_required("ndvi")
def imagem_ndvi_historico(historico_id, site_name):
    """Imagem de um item do historico -- unico por historico_id e nunca
    muda depois de salva, entao pode ficar em cache local por muito tempo
    (evita rebaixar tudo de novo so' de reabrir a galeria)."""
    _checar_acesso_site(site_name)
    imagem = models.get_farm_ndvi_historico_imagem(historico_id, site_name)
    if not imagem:
        abort(404)
    resp = send_file(io.BytesIO(imagem), mimetype="image/png")
    resp.headers["Cache-Control"] = "private, max-age=31536000, immutable"
    return resp


@app.route("/ndvi/imagem_historico_thumb/<int:historico_id>/<path:site_name>")
@aba_required("ndvi")
def imagem_ndvi_historico_thumb(historico_id, site_name):
    """Miniatura (ver `ndvi_service.gerar_thumbnail`) usada na galeria --
    bem mais leve que a imagem completa (que so' e' baixada quando o
    usuario abre `ver_ndvi_historico` pra dar zoom)."""
    _checar_acesso_site(site_name)
    thumbnail, mimetype = models.get_farm_ndvi_historico_thumbnail(historico_id, site_name)
    if not thumbnail:
        abort(404)
    resp = send_file(io.BytesIO(thumbnail), mimetype=mimetype)
    resp.headers["Cache-Control"] = "private, max-age=31536000, immutable"
    return resp


@app.route("/ndvi/ver/<int:historico_id>/<path:site_name>")
@aba_required("ndvi")
def ver_ndvi_historico(historico_id, site_name):
    _checar_acesso_site(site_name)
    item = models.get_farm_ndvi_historico_item(historico_id, site_name)
    if not item:
        abort(404)
    return render_template(
        "ndvi_visualizar.html", site_name=site_name, nome_exibicao=_nome_exibicao(site_name), item=item
    )


@app.route("/ndvi/historico/delete", methods=["POST"])
@aba_required("ndvi")
def delete_ndvi_historico():
    site_name = request.form.get("site_name")
    _checar_acesso_site(site_name)
    historico_id = request.form.get("historico_id", type=int)
    if historico_id is None:
        abort(400)
    models.delete_farm_ndvi_historico_item(historico_id, site_name)
    return _save_response(f"Imagem removida da galeria de '{_nome_exibicao(site_name)}'.", "ndvi")


@app.route("/ndvi/historico/enviar-whatsapp", methods=["POST"])
@aba_required("ndvi")
def enviar_ndvi_historico_whatsapp():
    """Manda, por WhatsApp, uma ou mais imagens marcadas na galeria da
    aba NDVI -- cada imagem selecionada vira uma mensagem separada (o
    WhatsApp/Baileys nao tem um jeito nativo de agrupar varias fotos
    numa unica mensagem), mandada em sequencia pros destinatarios
    marcados no formulario. A lista de destinatarios pra ESCOLHER vem do
    mesmo cadastro usado por `_send_site_whatsapp` (aba Usuarios >
    "Receber relatorios") -- o formulario so' deixa marcar/desmarcar
    quem ja' esta' nessa lista, nunca um numero avulso (o telefone
    marcado e' conferido contra `_site_whatsapp_destinations` de novo
    aqui, nao so' confiado no que veio do form). "Enviar agrupadas" aqui
    significa "escolher varias de uma vez e mandar tudo numa acao so'",
    nao uma unica mensagem com varias fotos dentro."""
    if (volta := _whatsapp_desconectado(url_for("ndvi"))):
        return volta
    site_name = request.form.get("site_name")
    _checar_acesso_site(site_name)
    historico_ids = [int(v) for v in request.form.getlist("historico_ids") if v.isdigit()]
    if not historico_ids:
        return _save_response("Selecione pelo menos uma imagem da galeria pra enviar.", "ndvi", ok=False)

    todos_destinos = _site_whatsapp_destinations(site_name)
    if not todos_destinos:
        return _save_response(
            f"Nenhum numero cadastrado pra receber relatorio de '{_nome_exibicao(site_name)}' (aba Usuarios).",
            "ndvi", ok=False,
        )
    telefones_marcados = set(request.form.getlist("destino_telefones"))
    destinos = [(phone, rotulo) for phone, rotulo in todos_destinos if phone in telefones_marcados]
    if not destinos:
        return _save_response("Selecione pelo menos um destinatario pra enviar.", "ndvi", ok=False)

    nome_exibicao = _nome_exibicao(site_name)
    imagens_enviadas, falhas = 0, []
    for historico_id in historico_ids:
        item = models.get_farm_ndvi_historico_item(historico_id, site_name)
        imagem = models.get_farm_ndvi_historico_imagem(historico_id, site_name)
        if not item or not imagem:
            continue
        data_br = models.fmt_data_br(item["data_alvo"])
        legenda = f"🛰️ NDVI - {nome_exibicao} - {data_br}"
        if item.get("cobertura_nuvens") is not None:
            legenda += f" ({item['cobertura_nuvens']:.0f}% de nuvens)"
        ok_alguma = False
        for phone, rotulo in destinos:
            ok, mensagem = whatsapp.send_whatsapp_image(phone, imagem, caption=legenda)
            models.log_whatsapp_envio(site_name, rotulo, phone, ok, f"NDVI {data_br}: {mensagem}")
            if ok:
                ok_alguma = True
            else:
                falhas.append(f"{rotulo} ({data_br})")
        if ok_alguma:
            imagens_enviadas += 1

    if imagens_enviadas == 0:
        return _save_response(
            f"Nao foi possivel enviar nenhuma imagem de '{nome_exibicao}' -- {'; '.join(falhas) or 'erro desconhecido'}.",
            "ndvi", ok=False,
        )
    resumo = f"{imagens_enviadas} imagem(ns) de '{nome_exibicao}' enviada(s) por WhatsApp pra {len(destinos)} numero(s)."
    if falhas:
        resumo += f" Falhas: {'; '.join(falhas)}."
    return _save_response(resumo, "ndvi", ok=not falhas)


@app.route("/ndvi/debug/<path:site_name>")
@aba_required("ndvi")
def debug_ndvi(site_name):
    """Diagnostico temporario -- a Copernicus rejeita poligono
    auto-intersectante sem dizer quais vertices sao o problema, entao essa
    rota mostra o numero de aneis/pontos e se cada um se auto-intersecta,
    pra achar a causa real sem precisar adivinhar."""
    if not current_user.is_admin:
        abort(403)
    cars_kml = models.get_farm_ndvi_cars_kml(site_name)
    if not cars_kml:
        return {"erro": "sem CAR cadastrado"}
    lista_de_aneis_por_car = [ndvi_service.parse_kml_poligono(kml) for kml in cars_kml]
    aneis = [anel for grupo in lista_de_aneis_por_car for anel in grupo]
    resposta = {
        "site": site_name,
        "n_aneis": len(aneis),
        "aneis": [
            {"indice": i, "pontos": len(anel), "auto_intersecta": ndvi_service.anel_auto_intersecta(anel)}
            for i, anel in enumerate(aneis)
        ],
    }
    try:
        geometria = ndvi_service.geometria_combinada(lista_de_aneis_por_car)
        resposta["apos_correcao"] = ndvi_service.resumo_geometria(geometria)
    except ValueError as exc:
        resposta["apos_correcao_erro"] = str(exc)
    return resposta


@app.route("/recommendations/cultura/save", methods=["POST"])
@login_required
def save_farm_cultura():
    site_name = request.form.get("site_name")
    if not current_user.is_admin:
        allowed = set(models.get_user_permitted_site_names(int(current_user.id)))
        if site_name not in allowed:
            abort(403)
    safra = _safra_or_default(request.form)
    cultura = request.form.get("cultura", "")
    if cultura and cultura != CULTURA_TODOS and cultura not in models.get_culturas_ativas():
        abort(400)
    models.set_farm_cultura(site_name, safra, cultura)
    nome_exibicao = _nome_exibicao(site_name)
    if cultura == CULTURA_TODOS:
        message = f"'{nome_exibicao}' na {SAFRA_LABELS[safra]} passa a mostrar todas as doencas cadastradas, sem filtro de cultura."
    elif cultura:
        message = (
            f"Cultura de '{nome_exibicao}' na {SAFRA_LABELS[safra]} definida como {cultura} -- "
            "essa aba passa a mostrar so doencas dessa cultura."
        )
    else:
        message = f"'{nome_exibicao}' na {SAFRA_LABELS[safra]} sem cultura definida -- nenhuma doenca aparece ate escolher uma cultura ou 'Todos'."
    return _save_response(message, "recommendations", safra=safra)


@app.route("/fazendas/estacao-clima/save", methods=["POST"])
@master_required
def save_weather_station_override():
    site_name = request.form.get("site_name")
    if not current_user.is_admin:
        allowed = set(models.get_user_permitted_site_names(int(current_user.id)))
        if site_name not in allowed:
            abort(403)
    # O valor vem como "PAIS:codigo" (ver fazendas.html) -- o pais aqui e'
    # o do PROVEDOR da estacao escolhida (pode ser diferente do pais da
    # propria fazenda, ver `countries.estacoes_mais_proximas_global`), nao
    # o da fazenda. "" (radio "coordenada da propria fazenda") continua
    # limpando a escolha manual, como antes.
    if "estacao_codigo" not in request.form:
        # Nenhuma opcao marcada (ex.: estacao salva fora da lista do
        # momento) -- nao apaga a escolha ja' feita.
        return _save_response("Nenhuma estacao escolhida -- nada foi alterado.", "fazendas")
    raw = request.form.get("estacao_codigo", "")
    if ":" in raw:
        country_code, estacao_codigo = raw.split(":", 1)
    else:
        country_code, estacao_codigo = countries.DEFAULT_COUNTRY, raw
    models.set_weather_station_override(site_name, estacao_codigo, country_code)
    # Sem isso, `_weather_cache` continuava com o clima da coordenada
    # ANTIGA por ate' `WEATHER_CACHE_TTL_SECONDS` (30min) -- a troca so'
    # valia na proxima vez que o cache expirasse sozinho.
    _weather_cache.pop(site_name, None)
    nome_exibicao = _nome_exibicao(site_name)
    if estacao_codigo:
        message = f"Previsao de '{nome_exibicao}' agora usa a estacao {estacao_codigo} como referencia."
    else:
        message = f"Previsao de '{nome_exibicao}' volta a usar a coordenada da propria fazenda."
    return _save_response(message, "fazendas")


@app.route("/fazendas/pais/save", methods=["POST"])
@master_required
def save_site_country():
    site_name = request.form.get("site_name")
    if not current_user.is_admin:
        allowed = set(models.get_user_permitted_site_names(int(current_user.id)))
        if site_name not in allowed:
            abort(403)
    country_code = request.form.get("country_code", "")
    if country_code not in countries.COUNTRIES:
        return _save_response(f"Pais invalido: '{country_code}'.", "fazendas", ok=False)
    models.set_site_country(site_name, country_code)
    nome_pais = countries.get_country(country_code)["nome"]
    return _save_response(f"'{_nome_exibicao(site_name)}' marcada como {nome_pais}.", "fazendas")


@app.route("/fazendas/ativo/save", methods=["POST"])
@admin_required
def save_site_ativo():
    """Ativa/desativa uma fazenda real ou ponto virtual (qualquer tipo,
    inclusive 'so clima') -- desativada some de TODAS as outras telas
    (Painel, Mapa, Graficos, Recomendacoes, NDVI, Envios, Alertas Clima,
    Exportar, telas de permissao). O toggle fica em Manejos (fazenda
    real e ponto virtual tipo 'doenca') e em Mapa Interpolado > Pontos
    Criados (todo ponto virtual, ja que 'so clima' nao aparece em
    Manejos) -- de onde vier o POST, e' pra la' que volta (`request.referrer`).
    Existe porque fazenda REAL vem do CSV sincronizado
    (`_ensure_sites_synced`/`models.sync_sites`, que so' ADICIONA linha
    nova e nunca remove) -- um DELETE de verdade nao resolveria, ela
    voltaria sozinha na proxima sincronizacao."""
    site_name = request.form.get("site_name")
    ativo = request.form.get("ativo") == "1"
    models.set_site_ativo(site_name, ativo)
    if ativo:
        msg = f"'{_nome_exibicao(site_name)}' reativada -- volta a aparecer em todas as telas."
    else:
        msg = f"'{_nome_exibicao(site_name)}' oculta -- deixa de aparecer no site (Mapa, Graficos, Recomendacoes, NDVI, Envios). Pra reativar: Configuracoes > Fazendas ocultas."
    return _save_response(msg)


@app.route("/fazendas/nome/save", methods=["POST"])
@master_required
def save_site_display_name():
    """Nome de exibicao de qualquer fazenda -- so' muda como ela aparece
    no site inteiro (Painel, Mapa, Manejo, WhatsApp, PDF, Graficos, ver
    `_nome_exibicao`). Fazenda REAL (vinda do BioScout): so' um apelido
    (`site_display_names`), o site_name (chave de casamento com o CSV
    do BioScout) nunca muda. Fazenda virtual/estimada: esse EH o nome de
    verdade (`virtual_farms.nome`) -- salvar aqui e' equivalente a editar
    em Mapa Interpolado > Pontos Criados (mesmo `update_virtual_farm`,
    preservando coordenada/raio/pais/tipo atuais), entao o site_name
    dela muda junto (segue o mesmo padrao de sempre -- ver
    `models._virtual_farm_site_name`); a pagina recarrega sozinha depois
    (`data-autosave-reload`) e mostra a fazenda com o site_name novo."""
    site_name = request.form.get("site_name")
    if not current_user.is_admin:
        allowed = set(models.get_user_permitted_site_names(int(current_user.id)))
        if site_name not in allowed:
            abort(403)
    nome_exibicao = request.form.get("nome_exibicao", "")
    vf = models.get_virtual_farm(site_name)
    if vf:
        nome = nome_exibicao.strip()
        if not nome:
            return _save_response("Nome nao pode ficar vazio pra fazenda virtual/estimada.", "fazendas", ok=False)
        country_code = models.get_all_site_countries().get(site_name, countries.DEFAULT_COUNTRY)
        try:
            models.update_virtual_farm(
                site_name, nome, vf["lat"], vf["lon"], vf["raio_km"],
                country_code=country_code, tipo=vf.get("tipo") or "doenca",
            )
        except sqlite3.IntegrityError:
            return _save_response(f"Ja existe um ponto estimado chamado '{nome}' -- escolha outro nome.", "fazendas", ok=False)
        # Mesmo motivo do pop em `editar_ponto_virtual` -- sem isso,
        # `_weather_cache` continuava servindo o clima da coordenada
        # antiga pelo site_name ANTIGO (que muda junto do nome).
        _weather_cache.pop(site_name, None)
        return _save_response(f"Nome de '{nome}' salvo.", "fazendas")
    nome_padrao = site_name.split(" - ", 1)[1] if " - " in site_name else site_name
    if nome_exibicao.strip() == nome_padrao:
        nome_exibicao = ""  # igual ao nome padrao -- nao guarda escolha manual a toa
    models.set_site_display_name(site_name, nome_exibicao)
    novo_nome = _nome_exibicao(site_name)
    return _save_response(f"Nome de exibicao atualizado para '{novo_nome}'.", "fazendas")


@app.route("/fazendas/save", methods=["POST"])
@login_required
def save_farm_produtos():
    site_name = request.form.get("site_name")
    if not current_user.is_admin:
        allowed = set(models.get_user_permitted_site_names(int(current_user.id)))
        if site_name not in allowed:
            abort(403)
    safra = _safra_or_default(request.form)
    for momento in models.MOMENTOS:
        for tipo in models.TIPOS_PRODUTO:
            if f"data_{momento}_{tipo}" not in request.form:
                continue  # autosave manda so' a secao editada -- nao apagar as outras
            datas = request.form.getlist(f"data_{momento}_{tipo}")
            nomes = request.form.getlist(f"nome_{momento}_{tipo}")
            ativos = request.form.getlist(f"ia_{momento}_{tipo}")
            linhas = list(zip(datas, nomes, ativos))
            models.set_farm_produtos(site_name, safra, momento, tipo, linhas)
    return _save_response(f"Produtos de '{_nome_exibicao(site_name)}' ({SAFRA_LABELS[safra]}) salvos.", "fazendas")


@app.route("/fazendas/plantio/save", methods=["POST"])
@login_required
def save_farm_plantio():
    site_name = request.form.get("site_name")
    if not current_user.is_admin:
        allowed = set(models.get_user_permitted_site_names(int(current_user.id)))
        if site_name not in allowed:
            abort(403)
    safra = _safra_or_default(request.form)
    datas = request.form.getlist("data_plantio")
    talhoes = request.form.getlist("talhao")
    variedades = request.form.getlist("variedade")
    ciclos = request.form.getlist("ciclo_dias")
    linhas = list(zip(datas, talhoes, variedades, ciclos))
    models.set_farm_plantio(site_name, safra, linhas)
    return _save_response(f"Dados de plantio de '{_nome_exibicao(site_name)}' ({SAFRA_LABELS[safra]}) salvos.", "fazendas")


@app.route("/fazendas/espacamento/save", methods=["POST"])
@login_required
def save_farm_espacamento():
    site_name = request.form.get("site_name")
    if not current_user.is_admin:
        allowed = set(models.get_user_permitted_site_names(int(current_user.id)))
        if site_name not in allowed:
            abort(403)
    safra = _safra_or_default(request.form)
    espacamento = request.form.get("espacamento", "")
    models.set_farm_espacamento_plantio(site_name, safra, espacamento)
    return _save_response(f"Espacamento de plantio de '{_nome_exibicao(site_name)}' ({SAFRA_LABELS[safra]}) salvo.", "fazendas")


@app.route("/fazendas/aplicacoes/save", methods=["POST"])
@login_required
def save_farm_aplicacoes():
    site_name = request.form.get("site_name")
    if not current_user.is_admin:
        allowed = set(models.get_user_permitted_site_names(int(current_user.id)))
        if site_name not in allowed:
            abort(403)
    safra = _safra_or_default(request.form)
    datas = request.form.getlist("data_aplicacao")
    talhoes = request.form.getlist("talhao")
    quimicos = request.form.getlist("fungicidas_quimicos")
    biologicos = request.form.getlist("fungicidas_biologicos")
    linhas = list(zip(datas, talhoes, quimicos, biologicos))
    models.set_farm_aplicacoes(site_name, safra, linhas)
    return _save_response(f"Dados de aplicacoes de '{_nome_exibicao(site_name)}' ({SAFRA_LABELS[safra]}) salvos.", "fazendas")


@app.route("/recommendations/produtos-folha/save", methods=["POST"])
@login_required
def save_produtos_folha():
    """Caixa "Folha (aplicacao foliar)" da aba Manejo -- escreve direto
    em momento="folha" (a MESMA secao Folha da aba Fazendas, ver
    `save_farm_produtos`), so' que restrita a esse unico momento: nunca
    chama `models.set_farm_produtos` pra' 'ts'/'sulco' (ficariam
    apagados, ja que esse form nao tem campos desses momentos pra'
    reenviar)."""
    site_name = request.form.get("site_name")
    if not current_user.is_admin:
        allowed = set(models.get_user_permitted_site_names(int(current_user.id)))
        if site_name not in allowed:
            abort(403)
    safra = _safra_or_default(request.form)
    for tipo in models.TIPOS_PRODUTO:
        datas = request.form.getlist(f"data_{tipo}")
        nomes = request.form.getlist(f"nome_{tipo}")
        ativos = request.form.getlist(f"ia_{tipo}")
        linhas = list(zip(datas, nomes, ativos))
        models.set_farm_produtos(site_name, safra, "folha", tipo, linhas)
    return _save_response(f"Produtos (Folha) de '{_nome_exibicao(site_name)}' salvos.", "recommendations", safra=safra)


@app.route("/recommendations/save", methods=["POST"])
@login_required
def save_recommendations():
    if current_user.is_admin:
        allowed_sites = None
    else:
        allowed_sites = set(models.get_user_permitted_site_names(int(current_user.id)))

    sites = request.form.getlist("site")
    doencas = request.form.getlist("doenca")
    notas = request.form.getlist("nota")
    notas_padrao = request.form.getlist("nota_padrao")
    for i, (site, doenca, nota) in enumerate(zip(sites, doencas, notas)):
        if allowed_sites is not None and site not in allowed_sites:
            continue  # usuario nao tem permissao para essa fazenda -- ignora
        nota = nota.strip()
        if i < len(notas_padrao) and nota == notas_padrao[i].strip():
            # Igual a anotacao padrao da Biblioteca (veio pre-preenchida) --
            # nao vira anotacao propria, continua acompanhando a Biblioteca.
            nota = ""
        models.save_recommendation_note(site, doenca, nota)
    return _save_response("Anotacoes salvas.", "recommendations", safra=_safra_or_default(request.form))


def _ordem_fazenda(site):
    """Chave de ordenacao de fazenda/estacao em toda tela (pedido do Alan,
    18/10/2026): 1) pais -- Brasil sempre primeiro, depois os outros em
    ordem alfabetica; 2) nome exibido em ordem alfabetica, sem diferenca de
    acento. (3, tempo sem sinal, entra so' onde ha' esse dado -- ver
    `dashboard`/`fazendas_ocultas`.)"""
    paises = getattr(g, "_ordem_paises", None)
    if paises is None:
        paises = g._ordem_paises = models.get_all_site_countries()
    codigo = paises.get(site, countries.DEFAULT_COUNTRY)
    return (
        codigo != countries.DEFAULT_COUNTRY,
        _chave_alfabetica(countries.get_country(codigo)["nome"]),
        _chave_alfabetica(_nome_exibicao(site)),
    )


def _chave_alfabetica(texto):
    """Chave de ordenacao ignorando acento (ordem por codepoint do Python
    colocaria "Mancha", "Míldio", "Requeima" fora de ordem alfabetica de
    verdade -- letra acentuada tem codepoint bem maior que as sem acento,
    entao "Míldio" iria parar depois de "Zebra" em vez de perto de "Mancha").
    So' usada como chave de comparacao -- o texto exibido continua com
    acento normal."""
    sem_acento = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode("ascii")
    return sem_acento.lower()


def _parse_float_or_none(valor):
    valor = (valor or "").strip().replace(",", ".")
    if not valor:
        return None
    try:
        return float(valor)
    except ValueError:
        return None


@app.route("/admin/doencas", methods=["GET", "POST"])
@aba_required("doencas")
def admin_doencas():
    if request.method == "POST":
        if request.form.get("form_id") == "matriz_culturas":
            # Nome das 12 culturas agora e' editado direto no cabecalho
            # desta matriz (antiga aba separada "Nome Culturas", removida
            # -- `models.set_culturas` migra sozinho toda marcacao/
            # registro ja feito quando e' so' um renomeio, nao uma
            # cultura nova de verdade).
            nomes_slots = [request.form.get(f"cultura_nome__{i}", "").strip() for i in range(12)]
            genuinamente_novos = models.set_culturas(nomes_slots)
            for nome in genuinamente_novos:
                # Cultura nova de verdade (slot vazio antes): bloqueia ela
                # de saida em todo quimico ja cadastrado -- ninguem
                # pesquisou registro pra ela ainda (mesmo raciocinio de
                # antes, so' que agora so' dispara pra slot GENUINAMENTE
                # novo, nao pra um renomeio).
                models.bloquear_cultura_nova_em_todos_quimicos(nome)

            doenca_ens = request.form.getlist("doenca_en")
            for idx, doenca_en in enumerate(doenca_ens):
                slots_marcados = request.form.getlist(f"culturas__{idx}")
                culturas_marcadas = []
                for slot_str in slots_marcados:
                    try:
                        slot = int(slot_str)
                    except ValueError:
                        continue
                    if 0 <= slot < len(nomes_slots) and nomes_slots[slot]:
                        culturas_marcadas.append(nomes_slots[slot])
                models.set_doenca_culturas(doenca_en, culturas_marcadas)
            return _save_response("Matriz doenca x cultura atualizada.", "admin_doencas")

        if request.form.get("form_id") == "matriz_paises":
            # Nome dos 12 paises agora e' editavel direto no cabecalho
            # desta matriz -- mesmo padrao de `matriz_culturas`, pra
            # poder cadastrar um pais fora do registro `countries.py`
            # (fora da America do Sul) so' pra marcar doenca, sem
            # depender de fronteira/estacao de verdade.
            nomes_slots = [request.form.get(f"pais_nome__{i}", "").strip() for i in range(12)]
            models.set_paises_doenca_slots(nomes_slots)

            doenca_ens = request.form.getlist("doenca_en")
            for idx, doenca_en in enumerate(doenca_ens):
                slots_marcados = request.form.getlist(f"paises__{idx}")
                paises_marcados = []
                for slot_str in slots_marcados:
                    try:
                        slot = int(slot_str)
                    except ValueError:
                        continue
                    if 0 <= slot < len(nomes_slots) and nomes_slots[slot]:
                        paises_marcados.append(nomes_slots[slot])
                models.set_doenca_paises(doenca_en, paises_marcados)
            return _save_response("Matriz doenca x pais atualizada.", "admin_doencas")

        display_names = request.form.getlist("display_name_en")
        nomes_pt = request.form.getlist("nome_pt")
        nomes_cientificos = request.form.getlist("nome_cientifico")
        temp_mins = request.form.getlist("germ_temp_min")
        temp_maxs = request.form.getlist("germ_temp_max")
        ur_mins = request.form.getlist("germ_ur_min")
        molhamentos = request.form.getlist("germ_molhamento_horas")
        info_atual = models.get_all_disease_info()
        culturas_ativas = set(models.get_culturas_ativas())
        for idx, (display_name_en, nome_pt, nome_cientifico) in enumerate(
            zip(display_names, nomes_pt, nomes_cientificos)
        ):
            nome_pt = nome_pt.strip()
            if not nome_pt:
                continue
            models.save_disease_translation(display_name_en, nome_pt, nome_cientifico.strip())
            models.set_doenca_culturas(
                display_name_en, [c for c in request.form.getlist(f"culturas_card__{idx}") if c in culturas_ativas],
            )
            # germ_agua_livre_inibe (chuva atrapalha, caso do oidio) nao tem
            # mais campo no formulario -- e' propriedade fixa do fungo, nao
            # algo pra editar aqui; preserva o valor ja salvo (aplicado pelo
            # botao de pesquisa) em vez de zerar a cada autosave.
            agua_livre_inibe = info_atual.get(display_name_en, {}).get("germ_agua_livre_inibe", False)
            models.save_disease_germ_limits(
                display_name_en,
                _parse_float_or_none(temp_mins[idx]) if idx < len(temp_mins) else None,
                _parse_float_or_none(temp_maxs[idx]) if idx < len(temp_maxs) else None,
                _parse_float_or_none(ur_mins[idx]) if idx < len(ur_mins) else None,
                _parse_float_or_none(molhamentos[idx]) if idx < len(molhamentos) else None,
                agua_livre_inibe,
            )
        return _save_response("Nomes de doencas atualizados.", "admin_doencas")

    _load_translations()  # garante linhas novas + nome cientifico pre-preenchido
    info = models.get_all_disease_info()
    doenca_culturas = models.get_doenca_culturas()
    doencas = [
        {
            "en": en, "pt": data["nome_pt"], "cientifico": data["nome_cientifico"],
            "culturas": doenca_culturas.get(en, set()),
            "germinacao": _formatar_condicoes_germinacao(data),
            "temp_min": data["germ_temp_min"], "temp_max": data["germ_temp_max"],
            "ur_min": data["germ_ur_min"], "molhamento_horas": data["germ_molhamento_horas"],
            "agua_livre_inibe": data["germ_agua_livre_inibe"],
            "manual": models.is_manual_disease(en),
        }
        for en, data in sorted(info.items(), key=lambda kv: _chave_alfabetica(kv[1]["nome_cientifico"] or ""))
    ]
    culturas_ativas = models.get_culturas_ativas()
    culturas = models.get_culturas()
    # 12 nomes de pais editaveis direto no cabecalho da matriz Doencas x
    # Pais -- mesmo padrao de `culturas`/`culturas_ativas` acima. Nasce
    # com os paises de `countries.py` (ver seed em `models.init_db`),
    # mas pode virar qualquer nome dali em diante -- inclusive um pais
    # fora da America do Sul, se um dia a OneAgro vender pra outro
    # continente, sem precisar de fronteira/estacao cadastrada nele.
    paises = models.get_paises_doenca_slots()
    doenca_paises = models.get_doenca_paises()
    matriz_paises = [
        {"en": d["en"], "pt": d["pt"], "cientifico": d["cientifico"], "marcadas": doenca_paises.get(d["en"], set())}
        for d in doencas
    ]
    return render_template(
        "admin_doencas.html", doencas=doencas, culturas_ativas=culturas_ativas, culturas=culturas,
        paises=paises, matriz_paises=matriz_paises,
    )


@app.route("/admin/fungicidas", methods=["GET", "POST"])
@aba_required("fungicidas")
def admin_fungicidas():
    if request.method == "POST":
        culturas_ativas = models.get_culturas_ativas()
        for row_id in request.form.getlist("row_id"):
            key = request.form.get(f"key__{row_id}")
            if not key:
                continue
            doenca, tipo, idx_str = key.split("|", 2)
            idx = int(idx_str)
            ingrediente = request.form.get(f"ingrediente__{row_id}", "").strip()
            classe = request.form.get(f"classe__{row_id}", "")
            rec = fungicida_data.get_recomendacao(doenca)
            itens_padrao = rec[tipo + "s"]["itens"] if rec else []
            default_item = itens_padrao[idx] if idx < len(itens_padrao) else None
            is_default = (
                default_item is not None
                and ingrediente == default_item["ingrediente"] and classe == (default_item["classe"] or "")
            )
            if is_default:
                # Sem edicao real -- nao grava override, assim a linha
                # continua acompanhando fungicida_data.py se ele mudar depois.
                models.delete_fungicida_override(doenca, tipo, idx)
            elif not ingrediente:
                if default_item is not None and _fungicida_proibido(tipo, default_item["ingrediente"]):
                    # Padrao proibido (aparece vazio na tela) -- nao vira "removido",
                    # a linha continua livre pros botoes do Agrofit.
                    models.delete_fungicida_override(doenca, tipo, idx)
                elif default_item is not None:
                    # Item padrao apagado de proposito -- marca como
                    # removido (fica vazio de vez, ver `removido` em
                    # `_apply_fungicida_overrides`) em vez de simplesmente
                    # descartar o override e deixar o padrao original
                    # reaparecer sozinho no proximo carregamento.
                    models.save_fungicida_override(doenca, tipo, idx, "", "", True)
                else:
                    # Linha extra de biologico nunca preenchida -- nada a
                    # gravar.
                    models.delete_fungicida_override(doenca, tipo, idx)
            else:
                models.save_fungicida_override(doenca, tipo, idx, ingrediente, classe, False)
            if tipo == "quimico":
                marcadas = set(request.form.getlist(f"cultura_ok__{row_id}"))
                bloqueadas = [c for c in culturas_ativas if c not in marcadas]
                models.set_fungicida_registro_bloqueado(doenca, tipo, idx, bloqueadas)
        for doenca, nota in zip(request.form.getlist("nota_doenca_key"), request.form.getlist("nota_doenca")):
            models.set_fungicida_nota(doenca, nota.strip())
        translations = _load_translations()
        for doenca, cientifico in zip(request.form.getlist("cientifico_key"), request.form.getlist("cientifico")):
            info = translations.get(doenca)
            if info and cientifico.strip() != (info.get("nome_cientifico") or ""):
                models.save_disease_translation(doenca, info["nome_pt"], cientifico.strip())
        return _save_response("Recomendacoes de fungicidas atualizadas.", "admin_fungicidas")

    overrides = models.get_all_fungicida_overrides()
    registro_bloqueado = models.get_all_fungicida_registro_bloqueado()
    culturas_ativas = models.get_culturas_ativas()
    doenca_paises = models.get_doenca_paises()
    translations = _load_translations()
    notas = models.get_all_fungicida_notas()
    nome_brasil = countries.get_country("BR")["nome"]
    bandeira_por_nome = {
        _chave_alfabetica(info["nome"]): "".join(chr(0x1F1E6 + ord(letra) - ord("A")) for letra in codigo)
        for codigo, info in countries.COUNTRIES.items()
    }

    doencas_data = []
    row_counter = 0
    # Doenca com registro encontrado (fungicida_data.py ja tem alguma
    # coisa cadastrada) primeiro, ordem alfabetica dentro de cada grupo;
    # "sem dados ainda" sempre por ultimo -- pedido explicito do usuario,
    # pra facilitar achar rapido o que ainda falta pesquisar.
    # Doencas do Brasil (ou sem pais marcado) sempre antes das de outros
    # paises; dentro delas, as do BioScout antes das cadastradas a mao.
    def _fora_do_brasil(doenca_en):
        paises = doenca_paises.get(doenca_en)
        return bool(paises) and _chave_alfabetica(nome_brasil) not in map(_chave_alfabetica, paises)

    doencas_ordenadas = sorted(
        translations.items(),
        key=lambda kv: (
            _fora_do_brasil(kv[0]), models.is_manual_disease(kv[0]),
            not fungicida_data.get_recomendacao(kv[0]), _chave_alfabetica(kv[1]["nome_pt"]),
        ),
    )
    for doenca_en, info in doencas_ordenadas:
        rotulo = info["nome_pt"]
        # Mesma info da matriz Doencas x Pais (aba Doencas) -- sem nenhum
        # pais marcado la' significa "se aplica a qualquer pais" (mesmo
        # "nunca escondida por engano" da matriz), mostrado aqui vazio em
        # vez de listar todo pais registrado.
        paises_doenca = sorted(doenca_paises.get(doenca_en, set()))
        bandeiras = [(bandeira_por_nome.get(_chave_alfabetica(nome)), nome) for nome in paises_doenca]
        rec = fungicida_data.get_recomendacao(doenca_en)
        if not rec:
            doencas_data.append({
                "doenca": doenca_en, "rotulo": rotulo, "grupos": [], "sem_dados": True, "paises": paises_doenca,
                "bandeiras": bandeiras,
                "nota": notas.get(doenca_en, ""),
                "cientifico": info.get("nome_cientifico") or "",
                "pesquisa_fora_brasil": bool(paises_doenca) and _chave_alfabetica(nome_brasil) not in map(_chave_alfabetica, paises_doenca),
            })
            continue
        grupos = []
        for tipo, grupo in (("quimico", rec["quimicos"]), ("biologico", rec["biologicos"])):
            n_real = len(grupo["itens"])
            # Sempre pelo menos 10 quimicos e 4 biologicos (linhas vazias
            # pra preencher a mao ou pelo botao Agrofit).
            n = fungicida_data.n_linhas(tipo, n_real)
            ordem = models.get_fungicida_ordem(doenca_en, tipo, n)
            linhas = []
            for posicao, idx in enumerate(ordem):
                item = grupo["itens"][idx] if idx < n_real else {"ingrediente": "", "classe": ""}
                override = overrides.get((doenca_en, tipo, idx))
                culturas_bloqueadas = registro_bloqueado.get((doenca_en, tipo, idx), set())
                ingrediente = override["ingrediente"] if override else item["ingrediente"]
                if _fungicida_proibido(tipo, ingrediente):
                    ingrediente = ""  # banido/sem registro ou nao-biologico: linha vazia, pronta pra preencher
                linhas.append({
                    "row_id": row_counter,
                    "key": f"{doenca_en}|{tipo}|{idx}",
                    "doenca": doenca_en,
                    "tipo": tipo,
                    "idx": idx,
                    "ingrediente": ingrediente,
                    "classe": (override["classe"] if override else item["classe"]) or "",
                    "pode_subir": posicao > 0,
                    "pode_descer": posicao < n - 1,
                    # Linha sem produto: nenhuma cultura marcada.
                    "culturas_registradas": [c for c in culturas_ativas if c not in culturas_bloqueadas] if ingrediente else [],
                })
                row_counter += 1
            grupos.append({
                "tipo": tipo, "titulo": "Quimicos" if tipo == "quimico" else "Biologicos",
                "fonte": grupo["fonte"], "fonte_url": grupo["fonte_url"], "linhas": linhas,
            })
        doencas_data.append({
            "doenca": doenca_en, "rotulo": rotulo, "grupos": grupos, "sem_dados": False, "paises": paises_doenca,
            "bandeiras": bandeiras,
            "nota": notas.get(doenca_en, ""),
            "cientifico": info.get("nome_cientifico") or "",
            # Agrofit/MAPA e' o registro do Brasil -- doenca marcada so' pra
            # outro pais fica de fora (ver `aplicar_pesquisa_registro`).
            "pesquisa_fora_brasil": bool(paises_doenca) and _chave_alfabetica(nome_brasil) not in map(_chave_alfabetica, paises_doenca),
        })

    return render_template(
        "admin_fungicidas.html", doencas_data=doencas_data, culturas_ativas=culturas_ativas,
        agrofit=agrofit_dados.status(),
    )


@app.route("/admin/fungicidas/agrofit-status")
@aba_required("fungicidas")
def agrofit_status():
    return jsonify(agrofit_dados.status())


@app.route("/admin/fungicidas/agrofit-enviar", methods=["POST"])
@aba_required("fungicidas")
def agrofit_enviar_arquivo():
    arquivo = request.files.get("arquivo")
    if not arquivo or not arquivo.filename:
        flash("Escolha o arquivo do Agrofit (.csv ou .zip).", "warning")
        return redirect(url_for("admin_fungicidas"))
    if agrofit_dados.status()["baixando"]:
        flash("Ja' tem uma atualizacao da base em andamento -- aguarde terminar.", "warning")
        return redirect(url_for("admin_fungicidas"))
    destino = agrofit_dados.INDICE_PATH.with_suffix(".upload")
    arquivo.save(destino)
    agrofit_dados.importar_em_segundo_plano(destino)
    return redirect(url_for("admin_fungicidas"))


@app.route("/admin/fungicidas/agrofit-atualizar", methods=["POST"])
@aba_required("fungicidas")
def agrofit_atualizar_agora():
    agrofit_dados.atualizar_em_segundo_plano(forcar=True)
    return redirect(url_for("admin_fungicidas"))


def _ingredientes_da_biblioteca(doenca, tipo):
    """[(idx, ingrediente)] de todas as linhas (10 quimicos / 4 biologicos)
    da doenca na Biblioteca, ja com as edicoes do admin; ingrediente ""
    = linha vazia, None = removida de proposito."""
    rec = fungicida_data.get_recomendacao(doenca)
    padrao = rec[tipo + "s"]["itens"] if rec else []
    overrides = models.get_all_fungicida_overrides()
    linhas = []
    for idx in range(fungicida_data.n_linhas(tipo, len(padrao))):
        override = overrides.get((doenca, tipo, idx))
        if override and override["removido"]:
            linhas.append((idx, None))
        else:
            ingrediente = override["ingrediente"] if override else (padrao[idx]["ingrediente"] if idx < len(padrao) else "")
            linhas.append((idx, "" if _fungicida_proibido(tipo, ingrediente) else ingrediente))
    return linhas


def _culturas_equivalentes(achados):
    return {c: agrofit_dados.cultura_do_agrofit(c, achados["culturas"]) for c in models.get_culturas_ativas()}


def _gravar_culturas_registradas(doenca, idx, marcadas, equivalentes, bloqueios):
    """Desmarca as culturas sem registro de um quimico; cultura que o
    Agrofit nao tem fica como esta."""
    bloqueadas = {
        c for c, nomes in equivalentes.items()
        if (c not in marcadas if nomes else c in bloqueios.get((doenca, "quimico", idx), set()))
    }
    models.set_fungicida_registro_bloqueado(doenca, "quimico", idx, sorted(bloqueadas))


def _marcar_registros_novos(doenca, idxs, achados):
    """Quimicos que o botao acabou de trazer: ja' saem com as culturas
    registradas marcadas."""
    equivalentes = _culturas_equivalentes(achados)
    bloqueios = models.get_all_fungicida_registro_bloqueado()
    linhas = dict(_ingredientes_da_biblioteca(doenca, "quimico"))
    for idx in idxs:
        chave = agrofit_dados.chave_quimico(linhas.get(idx) or "")
        registradas = agrofit_dados.culturas_cobertas(chave, achados["quimico"]) if chave else set()
        marcadas = {c for c, nomes in equivalentes.items() if nomes & registradas}
        _gravar_culturas_registradas(doenca, idx, marcadas, equivalentes, bloqueios)


def _agrofit_completar_lista(doenca, tipo, achados):
    """Operacao "Quimicos"/"Biologicos" do botao Agrofit: completa as
    linhas vazias da doenca (inclusive as apagadas na mao ou limpas pelo
    botao Registros) com os produtos mais registrados pro fungo (os que
    ja estao na lista ficam). Doenca sem nada no fungicida_data.py
    (manual ou do BioScout ainda nao pesquisada) e ainda sem nenhum
    produto daquele tipo tem a lista inteira vinda do Agrofit."""
    chave = agrofit_dados.chave_biologico if tipo == "biologico" else agrofit_dados.chave_quimico
    candidatos = sorted(
        (c for c in achados[tipo] if not _fungicida_proibido(tipo, achados["nomes"][c])),
        key=lambda c: (-len(achados[tipo][c]), c),
    )
    rec = fungicida_data.get_recomendacao(doenca)
    if fungicida_data.RECOMENDACOES.get(doenca) is None and not (rec and rec[tipo + "s"]["itens"]):
        itens = [achados["nomes"][c] for c in candidatos[: fungicida_data.LINHAS_POR_TIPO[tipo]]]
        models.set_fungicidas_importados(doenca, tipo, itens)
        fungicida_data.limpar_cache_importados()
        if tipo == "quimico":
            _marcar_registros_novos(doenca, range(len(itens)), achados)
        return f"{len(itens)} {'biologico(s)' if tipo == 'biologico' else 'quimico(s)'} trazido(s) do Agrofit."
    linhas = _ingredientes_da_biblioteca(doenca, tipo)
    existentes = {chave(ing) for _, ing in linhas if ing}
    vazias = [idx for idx, ing in linhas if not ing]
    novos = [c for c in candidatos if c not in existentes]
    for idx, c in zip(vazias, novos):
        models.save_fungicida_override(doenca, tipo, idx, achados["nomes"][c], "", False)
    if tipo == "quimico":
        _marcar_registros_novos(doenca, vazias[:len(novos)], achados)
    n = min(len(vazias), len(novos))
    if not vazias:
        return "Lista ja completa -- nenhuma linha vazia para preencher."
    return f"{n} produto(s) novo(s) nas {len(vazias)} linha(s) vazia(s)."


# Tempo maximo do botao Registros lendo bulas da ADAPAR (o gunicorn
# corta a requisicao em 60s).
_ADAPAR_PRAZO_S = 40


def _agrofit_registros(doenca, achados):
    """Operacao "Registros": em cada quimico da doenca, marca as culturas
    com registro pro fungo (produto registrado, ou mistura de tanque de
    produtos registrados -- `agrofit_dados.culturas_cobertas`) e desmarca
    as sem registro. Cultura que o Agrofit nao marcou passa por uma
    segunda conferencia nas bulas da ADAPAR (`adapar`) -- ex.: soja pra
    Alternaria (pedido do Alan, 18/10/2026). Produto sem nenhuma cultura
    no Agrofit nem na ADAPAR: a linha fica vazia pra uma nova busca no
    botao Quimicos. ADAPAR fora do ar: fica so' o que o Agrofit marcou
    (produto sem nenhuma cultura fica, desmarcado). Linha vazia: tudo
    desmarcado. Cultura que o Agrofit nao tem fica como esta."""
    bloqueios = models.get_all_fungicida_registro_bloqueado()
    equivalentes = _culturas_equivalentes(achados)
    todas = sorted(equivalentes)
    conferiveis = {c for c, nomes in equivalentes.items() if nomes}
    linhas, a_conferir = [], []
    for idx, ingrediente in _ingredientes_da_biblioteca(doenca, "quimico"):
        chave = agrofit_dados.chave_quimico(ingrediente) if ingrediente else ""
        registradas = agrofit_dados.culturas_cobertas(chave, achados["quimico"]) if chave else set()
        marcadas = {c for c, nomes in equivalentes.items() if nomes & registradas}
        linhas.append((idx, ingrediente, chave, marcadas))
        if chave and conferiveis - marcadas:
            a_conferir.append(chave)

    adapar_culturas, adapar_erro = {}, None
    if a_conferir:
        marcas = agrofit_dados.marcas_por_chave()
        partes = {p for chave in a_conferir for p in chave.split(" + ")}
        # Bulas do proprio produto primeiro; depois as de cada ingrediente
        # (mistura de tanque).
        alvo_marcas = dict(sorted(
            ((k, m) for k, m in marcas.items() if set(k.split(" + ")) <= partes),
            key=lambda km: (km[0] not in a_conferir, -len(km[0].split(" + ")), km[0]),
        ))
        # Todas as culturas do Agrofit entram como referencia na bula: na
        # tabela "Soja ... Tomate - Alternaria", a Alternaria e' do tomate.
        do_site = set().union(*equivalentes.values())
        nomes_culturas = {f"outra:{c}": {agrofit_dados.normalizar(c)} for c in achados["culturas"] if c not in do_site}
        nomes_culturas.update({
            c: {agrofit_dados.normalizar(c)} | {agrofit_dados.normalizar(n) for n in nomes}
            for c, nomes in equivalentes.items() if nomes
        })
        if not marcas:
            adapar_erro = "base do Agrofit sem as marcas comerciais -- atualize a base"
        else:
            try:
                adapar_culturas = adapar.culturas_registradas(
                    alvo_marcas, achados["pragas"], nomes_culturas, time.monotonic() + _ADAPAR_PRAZO_S,
                )
            except adapar.Indisponivel as exc:
                adapar_erro = str(exc)

    com_registro, pela_adapar, limpos, sem_conferir = 0, [], [], []
    for idx, ingrediente, chave, marcadas in linhas:
        if chave and adapar_erro is None:
            novas = (agrofit_dados.culturas_cobertas(chave, adapar_culturas) & conferiveis) - marcadas
            if novas:
                pela_adapar.append(f"{ingrediente} ({', '.join(sorted(novas))})")
                marcadas = marcadas | novas
            elif not marcadas:
                models.save_fungicida_override(doenca, "quimico", idx, "", "", True)
                limpos.append(ingrediente)
        elif chave and not marcadas:
            sem_conferir.append(ingrediente)
        if chave and marcadas:
            com_registro += 1
        if not chave or not marcadas:
            models.set_fungicida_registro_bloqueado(doenca, "quimico", idx, todas)
        else:
            _gravar_culturas_registradas(doenca, idx, marcadas, equivalentes, bloqueios)
    msg = f"{com_registro} quimico(s) com registro (culturas marcadas)."
    if pela_adapar:
        msg += " Culturas confirmadas na bula da ADAPAR: " + "; ".join(pela_adapar) + "."
    elif a_conferir and adapar_erro is None:
        msg += " Nenhuma cultura a mais nas bulas da ADAPAR."
    if limpos:
        msg += (" Sem registro no Agrofit nem na ADAPAR (linha limpa -- use o botao Quimicos): "
                + "; ".join(limpos) + ".")
    if adapar_erro:
        msg += f" Nao deu pra conferir na ADAPAR ({adapar_erro})."
        if sem_conferir:
            msg += " Culturas desmarcadas: " + "; ".join(sem_conferir) + "."
    return msg


_OPERACOES_AGROFIT = ("quimicos", "biologicos", "registros")


@app.route("/admin/fungicidas/aplicar-pesquisa-registro", methods=["POST"])
@aba_required("fungicidas")
def aplicar_pesquisa_registro():
    """Botoes "Agrofit" de UMA doenca da Biblioteca de fungicidas, cada um
    com uma operacao separada (`operacao`): "quimicos" e "biologicos"
    completam a lista daquele tipo, "registros" marca/desmarca as culturas
    dos quimicos. Busca na base aberta do Agrofit/MAPA (`agrofit_dados`)
    pelo nome cientifico do fungo -- mesmo padrao pra doenca do BioScout ou
    manual. Agrofit/MAPA e' o registro do BRASIL -- doenca marcada na
    matriz Doencas x Pais SO' pra outro pais fica de fora (sem nenhum pais
    marcado = vale pra qualquer pais, entra)."""
    doenca = request.form.get("doenca", "")
    operacao = request.form.get("operacao", "")
    nome_cientifico = (request.form.get("nome_cientifico") or "").strip()
    info = _load_translations().get(doenca)
    if not info or operacao not in _OPERACOES_AGROFIT:
        return jsonify({"ok": False, "erro": "Pedido invalido."}), 400
    if nome_cientifico != (info.get("nome_cientifico") or ""):
        models.save_disease_translation(doenca, info["nome_pt"], nome_cientifico)
    paises_da_doenca = models.get_doenca_paises().get(doenca)
    if paises_da_doenca and _chave_alfabetica(countries.get_country("BR")["nome"]) not in map(_chave_alfabetica, paises_da_doenca):
        return jsonify({"ok": False, "erro": "Doenca marcada so' pra outro pais -- Agrofit/MAPA e' o registro do Brasil."}), 400
    if not agrofit_dados.status()["baixado_em"]:
        agrofit_dados.atualizar_em_segundo_plano()
        return jsonify({
            "ok": False,
            "erro": "A base do Agrofit/MAPA ainda esta sendo baixada -- tente de novo em alguns minutos.",
        }), 409
    achados = agrofit_dados.registros(nome_cientifico)
    if not achados["pragas"]:
        return jsonify({
            "ok": False,
            "erro": f"Nenhum produto registrado no Agrofit para '{nome_cientifico or 'vazio'}'. "
                    "Confira o nome cientifico (genero e especie do fungo).",
        }), 404
    if operacao == "registros":
        if fungicida_data.get_recomendacao(doenca) is None:
            return jsonify({"ok": False, "erro": "Essa doenca ainda nao tem quimicos -- use antes o botao Quimicos."}), 400
        resumo = _agrofit_registros(doenca, achados)
    else:
        resumo = _agrofit_completar_lista(doenca, operacao[:-1], achados)
    pragas = ", ".join(p.capitalize() for p in achados["pragas"][:6]) + ("..." if len(achados["pragas"]) > 6 else "")
    return jsonify({"ok": True, "mensagem": f"Agrofit ({pragas}): {resumo}"})


# Condicoes de germinacao de esporos por NOME CIENTIFICO do fungo
# (pedido do usuario, 16/10/2026 -- antes era por nome em ingles do
# BioScout). Chave = "genero especie" ou so' "genero" (minusculo, sem
# acento); `_pesquisa_germinacao_por_cientifico` tenta especie e depois
# genero. Valores de literatura de fitopatologia (EMBRAPA, APS, Crop
# Protection Network, UC IPM, PNW Pest Management Handbooks, Cornell/
# extensao) -- os mesmos ja usados antes por nome em ingles, mais alguns
# fungos comuns em soja/milho/algodao pra' doenca cadastrada a mao.
# Sempre editaveis no quadro da doenca.
_G = lambda tmin, tmax, ur, molh, inibe=False: {  # noqa: E731
    "temp_min": tmin, "temp_max": tmax, "ur_min": ur, "molhamento_horas": molh, "agua_livre_inibe": inibe,
}
_PESQUISA_GERMINACAO = {
    "colletotrichum": _G(20, 28, 95, 6),           # antracnose
    "septoria": _G(15, 30, 80, 6),                 # septoriose
    "phakopsora": _G(15, 28, 95, 6),               # ferrugem asiatica da soja
    "phakopsora pachyrhizi": _G(15, 28, 95, 6),
    "puccinia": _G(16, 23, 90, 6),                 # ferrugens (P. sorghi -- ferrugem comum do milho)
    "puccinia sorghi": _G(16, 23, 90, 6),
    "puccinia polysora": _G(22, 30, 95, 6),        # ferrugem polissora do milho (mais quente)
    "fusarium": _G(25, 30, 85, 8),
    "corynespora": _G(25, 30, 90, 12),             # mancha-alvo
    "corynespora cassiicola": _G(25, 30, 90, 12),
    "alternaria": _G(20, 30, 90, 8),               # A. solani: molhamento minimo 4h, severidade crescente ate' 12h
    "moniliophthora": _G(25, 30, 90, 18),
    # Oidio: agua livre ATRAPALHA a germinacao dos conidios.
    "erysiphe": _G(20, 25, 80, None, True),
    "podosphaera": _G(20, 25, 80, None, True),
    "erysiphe necator": _G(20, 25, 80, None, True),
    "microsphaera": _G(20, 25, 80, None, True),
    "botrytis": _G(15, 28, 90, 15),                # mofo cinzento: 15h de molhamento a >=15 C
    "botrytis cinerea": _G(15, 28, 90, 15),
    # Botryosphaeriaceae (cancro/podridao de cacho, entra por ferimento de poda).
    "botryosphaeria": _G(20, 30, 90, 24),
    "neofusicoccum": _G(20, 30, 90, 24),
    "diplodia": _G(20, 30, 90, 24),
    "lasiodiplodia": _G(20, 30, 90, 24),
    "eutypa": _G(10, 30, 90, 12),
    "eutypa lata": _G(10, 30, 90, 12),
    "plasmopara viticola": _G(10, 25, 90, 24),    # mildio da videira, regra 10-10-24
    "plasmopara": _G(10, 25, 90, 24),
    "helminthosporium solani": _G(15, 25, 90, 24),  # sarna prateada (armazenamento)
    "phytophthora infestans": _G(10, 25, 90, 11),  # requeima, criterio "Smith Period"
    "cercospora": _G(22, 30, 90, 12),              # crestamento/olho-de-ra (soja), cercosporiose (milho)
    "cercospora zeae": _G(22, 30, 95, 12),
    "sclerotinia": _G(15, 25, 90, 16),             # mofo-branco: molhamento prolongado
    "sclerotinia sclerotiorum": _G(15, 25, 90, 16),
    "exserohilum": _G(18, 27, 90, 6),              # helmintosporiose do milho
    "exserohilum turcicum": _G(18, 27, 90, 6),
    "bipolaris": _G(20, 32, 90, 6),
    "bipolaris maydis": _G(20, 32, 90, 6),
    "ramularia": _G(18, 30, 90, 12),               # mancha de ramularia (algodao)
    "ramularia areola": _G(18, 30, 90, 12),
    "ramulariopsis": _G(18, 30, 90, 12),           # ramularia do algodao (nome atual)
    "myrothecium": _G(20, 32, 90, 12),             # mancha de mirotecio (algodao, soja)
    "myrothecium roridum": _G(20, 32, 90, 12),
    "diaporthe": _G(20, 30, 90, 12),               # cancro da haste / seca da vagem (soja)
    "phomopsis": _G(20, 30, 90, 12),
    "pseudocercospora griseola": _G(16, 28, 90, 7),  # mancha angular do feijao
    "uromyces": _G(17, 27, 95, 8),                 # ferrugem do feijao
    "uromyces appendiculatus": _G(17, 27, 95, 8),
    "stenocarpella": _G(25, 30, 90, 12),           # podridao/diplodia do milho
    "phaeosphaeria": _G(14, 25, 90, 12),           # mancha branca do milho
    "physopella": _G(22, 30, 95, 6),               # ferrugem tropical do milho
    "physopella zeae": _G(22, 30, 95, 6),
    "kabatiella": _G(10, 25, 90, 12),              # olho-de-ra do milho
    "peronospora": _G(15, 25, 95, 6),              # mildio da soja
    "peronospora manshurica": _G(15, 25, 95, 6),
    "pyricularia": _G(20, 30, 90, 10),             # brusone
    "magnaporthe": _G(20, 30, 90, 10),
    "phyllosticta citricarpa": _G(15, 30, 90, 15),  # pinta preta dos citros
    "guignardia": _G(15, 30, 90, 15),
    "elsinoe": _G(15, 25, 90, 6),                  # verrugose
    "hemileia": _G(15, 28, 95, 6),                 # ferrugem do cafe
    "hemileia vastatrix": _G(15, 28, 95, 6),
}
_PALAVRAS_IGNORADAS_CIENTIFICO = {"spp", "sp", "and", "e", "var", "f", "complex", "complexo"}


def _culturas_sugeridas_agrofit(nome_cientifico):
    """Culturas do site em que o Agrofit tem fungicida registrado pro
    fungo -- sugestao de onde a doenca ataca."""
    try:
        do_fungo = agrofit_dados.culturas_do_fungo(nome_cientifico)
    except Exception:
        return []
    return [c for c in models.get_culturas_ativas() if agrofit_dados.cultura_do_agrofit(c, do_fungo)]


def _pesquisa_germinacao_por_cientifico(nome_cientifico):
    """(chave usada, dados) da pesquisa -- "genero especie" se houver,
    senao so' o genero; (None, None) se o nome nao tem nenhum dos dois na
    tabela (ex.: "Two Celled Spore", que o BioScout manda pra septoria)."""
    texto = unicodedata.normalize("NFKD", nome_cientifico or "").encode("ascii", "ignore").decode().lower()
    palavras = [p for p in re.findall(r"[a-z]+", texto) if p not in _PALAVRAS_IGNORADAS_CIENTIFICO]
    for i in range(len(palavras) - 1):
        chave = f"{palavras[i]} {palavras[i + 1]}"
        if chave in _PESQUISA_GERMINACAO:
            return chave, _PESQUISA_GERMINACAO[chave]
    for palavra in palavras:
        if palavra in _PESQUISA_GERMINACAO:
            return palavra, _PESQUISA_GERMINACAO[palavra]
    return None, None


@app.route("/admin/doencas/buscar-germinacao", methods=["POST"])
@aba_required("doencas")
def buscar_germinacao_doenca():
    """Botao "Buscar pelo nome cientifico" de UM quadro da aba Doencas --
    so' grava a germinacao daquela doenca (pedido do usuario: uma busca
    nao pode mexer no dado de outra). Usa o nome cientifico digitado no
    quadro (salva ele junto, caso o salvamento automatico ainda nao tenha
    rodado)."""
    display_name_en = request.form.get("display_name_en") or ""
    nome_cientifico = (request.form.get("nome_cientifico") or "").strip()
    info = models.get_all_disease_info().get(display_name_en)
    if info is None:
        abort(404)
    if nome_cientifico != (info.get("nome_cientifico") or ""):
        models.save_disease_translation(display_name_en, info["nome_pt"], nome_cientifico)
    # Doenca ainda sem cultura marcada: marca onde o Agrofit tem fungicida
    # registrado pro fungo.
    culturas = []
    if not models.get_doenca_culturas().get(display_name_en):
        culturas = _culturas_sugeridas_agrofit(nome_cientifico)
        if culturas:
            models.set_doenca_culturas(display_name_en, culturas)
    aviso_culturas = f" Culturas marcadas pelo Agrofit: {', '.join(culturas)}." if culturas else ""
    chave, dados = _pesquisa_germinacao_por_cientifico(nome_cientifico)
    if not dados:
        return jsonify({
            "ok": False, "culturas": culturas,
            "mensagem": f"Sem dados de germinacao para '{nome_cientifico or '(vazio)'}' -- preencha os campos a mao "
                        "(confira genero e especie do fungo)." + aviso_culturas,
        })
    models.save_disease_germ_limits(
        display_name_en, dados["temp_min"], dados["temp_max"], dados["ur_min"],
        dados["molhamento_horas"], dados["agua_livre_inibe"],
    )
    salvo = models.get_all_disease_info()[display_name_en]
    return jsonify({
        "ok": True,
        "mensagem": f"Condicoes de germinacao preenchidas pela pesquisa de '{chave}'." + aviso_culturas,
        "culturas": culturas,
        "temp_min": dados["temp_min"], "temp_max": dados["temp_max"], "ur_min": dados["ur_min"],
        "molhamento_horas": dados["molhamento_horas"], "germinacao": _formatar_condicoes_germinacao(salvo),
    })


@app.route("/admin/doencas/manual", methods=["POST"])
@aba_required("doencas")
def adicionar_doenca_manual():
    """Doenca que o BioScout nao identifica, cadastrada a mao so' pra'
    entrar no risco de infeccao pelo clima dos relatorios (pedido do
    usuario, 16/10/2026)."""
    nome = (request.form.get("nome_pt") or "").strip()
    nome_cientifico = (request.form.get("nome_cientifico") or "").strip()
    if not nome:
        return _save_response("Informe o nome da doenca.", "admin_doencas")
    chave = models.add_manual_disease(nome)
    if not nome_cientifico:
        return _save_response(
            f"Doenca '{nome}' adicionada -- preencha abaixo o nome cientifico, a germinacao e as culturas dela.",
            "admin_doencas", abrir=chave,
        )
    models.save_disease_translation(chave, nome, nome_cientifico)
    partes = []
    _, germ = _pesquisa_germinacao_por_cientifico(nome_cientifico)
    if germ:
        models.save_disease_germ_limits(
            chave, germ["temp_min"], germ["temp_max"], germ["ur_min"], germ["molhamento_horas"], germ["agua_livre_inibe"],
        )
        partes.append("germinacao preenchida pela pesquisa")
    culturas = _culturas_sugeridas_agrofit(nome_cientifico)
    if culturas:
        models.set_doenca_culturas(chave, culturas)
        partes.append("culturas marcadas pelo Agrofit (" + ", ".join(culturas) + ")")
    resumo = "; ".join(partes) if partes else "sem dados de pesquisa nem registro no Agrofit pra esse fungo -- preencha a mao"
    return _save_response(f"Doenca '{nome}' adicionada: {resumo}. Confira abaixo.", "admin_doencas", abrir=chave)


@app.route("/admin/doencas/excluir", methods=["POST"])
@aba_required("doencas")
def excluir_doenca():
    """Exclusao manual de uma doenca duplicada (mesmo fungo, nome de
    exibicao diferente -- o merge automatico so' junta grafias
    identicas, ver `models.merge_duplicate_disease_translations`),
    pedido explicito do usuario. Se o BioScout continuar reportando essa
    display_name_en, ela reaparece em branco no proximo sync."""
    display_name_en = request.form.get("display_name_en")
    if not display_name_en:
        abort(400)
    models.delete_disease_translation(display_name_en)
    return _save_response(f"Doenca '{display_name_en}' excluida.", "admin_doencas")


@app.route("/admin/fungicidas/mover", methods=["POST"])
@aba_required("fungicidas")
def mover_fungicida_item():
    doenca = request.form.get("doenca")
    tipo = request.form.get("tipo")
    direction = request.form.get("direction")
    idx = request.form.get("idx")
    rec = fungicida_data.get_recomendacao(doenca) if doenca else None
    if rec and tipo in models.TIPOS_PRODUTO and direction in ("up", "down") and idx is not None:
        n_real = len(rec[tipo + "s"]["itens"])
        n = fungicida_data.n_linhas(tipo, n_real)
        models.move_fungicida_item(doenca, tipo, int(idx), direction, n)
    return redirect(url_for("admin_fungicidas"))


@app.route("/admin/users")
@aba_required("usuarios")
def admin_users():
    users = models.get_all_users()
    report_counts = {u["id"]: len(models.get_user_report_site_ids(u["id"])) for u in users}
    permission_counts = {u["id"]: len(models.get_user_permitted_site_ids(u["id"])) for u in users}
    site_name_by_id = {s["id"]: s["site_name"] for s in models.get_all_sites()}
    subordinados_by_owner = {}
    for u in users:
        subs = models.get_owner_subordinados(u["id"])
        for sub in subs:
            sub["fazendas"] = "; ".join(sorted(
                _nome_exibicao(site_name_by_id[sid]) for sid in sub["site_ids"] if sid in site_name_by_id
            ))
        subordinados_by_owner[u["id"]] = subs
    subordinado_counts = {uid: len(subs) for uid, subs in subordinados_by_owner.items() if subs}
    return render_template(
        "admin_users.html", users=users, default_password=models.DEFAULT_PASSWORD,
        niveis=NIVEIS_ACESSO, nivel_acesso=_nivel_acesso,
        report_counts=report_counts, permission_counts=permission_counts,
        subordinado_counts=subordinado_counts, subordinados_by_owner=subordinados_by_owner,
    )


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _is_valid_email(email):
    return bool(_EMAIL_RE.match(email))


def _is_valid_phone(phone):
    """So digitos, com codigo do pais (ex.: 5511999999999) -- formato
    exigido pelo WhatsApp/Baileys pra montar o JID do destinatario. Aceita
    10 a 15 digitos pra nao travar numeros de outros paises."""
    return phone.isdigit() and 10 <= len(phone) <= 15


def _telefone_from_form(form):
    """Monta o telefone (so digitos) a partir das duas caixas do
    formulario -- codigo do pais (padrao Brasil, 55) + numero -- pra
    evitar o erro comum de esquecer o codigo do pais na frente."""
    codigo = re.sub(r"\D", "", form.get("codigo_pais", ""))
    numero = re.sub(r"\D", "", form.get("numero_telefone", ""))
    return codigo + numero


# DDIs dos paises atendidos, pra `_split_phone` reconhecer o prefixo
# certo ao reabrir um telefone ja salvo -- ampliar conforme novos paises
# da America do Sul entrarem (ver `countries.py`).
_DDI_CONHECIDOS = ["55", "56"]  # Brasil, Chile


def _split_phone(telefone):
    """Separa um telefone ja salvo (so digitos) em (codigo_pais, numero)
    pra preencher as duas caixas ao reabrir o formulario -- tenta cada
    DDI conhecido (mais longo primeiro), Brasil (55) como ultimo recurso
    se nenhum bater. Numero de um pais nao listado ainda funciona, so
    aparece inteiro na caixa do numero pra conferir/ajustar manualmente."""
    for ddi in sorted(_DDI_CONHECIDOS, key=len, reverse=True):
        if telefone.startswith(ddi) and len(telefone) > 10:
            return ddi, telefone[len(ddi):]
    return "55", telefone


@app.route("/admin/users/create", methods=["POST"])
@aba_required("usuarios")
def admin_create_user():
    username = request.form.get("username", "").strip()
    email = request.form.get("email", "").strip()
    telefone = _telefone_from_form(request.form)
    nivel = request.form.get("nivel", "usuario")
    if nivel == "master" and current_user.username != ALAN_MAURO_USERNAME:
        nivel = "admin"
    if _RANK_NIVEL.get(nivel, 0) > _rank_atual():
        nivel = "usuario"
    if not username:
        flash("Usuario e obrigatorio.", "error")
    elif not _is_valid_email(email):
        flash("Informe um email valido.", "error")
    elif not _is_valid_phone(telefone):
        flash("Informe um numero de telefone valido, com codigo do pais (ex.: 5511999999999).", "error")
    elif models.get_user_by_username(username):
        flash("Ja existe um usuario com esse nome.", "error")
    else:
        models.create_user(
            username, models.DEFAULT_PASSWORD, nivel != "usuario", email=email, telefone=telefone,
            is_master=nivel == "master",
        )
        flash(
            f"Usuario '{username}' criado com a senha padrao '{models.DEFAULT_PASSWORD}' -- "
            "avise a pessoa pra trocar assim que entrar (botao 'Trocar senha' no menu). "
            "Nao esqueca de marcar quais fazendas ela recebe relatorio na coluna "
            "'Receber relatorios' -- o numero cadastrado ja fica pronto pra receber.",
            "success",
        )
    return redirect(url_for("admin_users"))


@app.route("/admin/users/<int:user_id>/editar", methods=["GET", "POST"])
@master_required
def admin_edit_user(user_id):
    user_row = models.get_user_by_id(user_id)
    if not user_row:
        abort(404)
    if request.method == "POST":
        action = request.form.get("action")
        email = request.form.get("email", "").strip()
        telefone = _telefone_from_form(request.form)
        nivel = request.form.get("nivel", _nivel_acesso(user_row))
        if current_user.username != ALAN_MAURO_USERNAME and (nivel == "master") != bool(user_row["is_master"]):
            nivel = _nivel_acesso(user_row)  # so' o Alan da' ou tira o Master
        if user_row["username"] == ALAN_MAURO_USERNAME:
            nivel = "master"
        error = None
        if str(user_id) == current_user.id and nivel != _nivel_acesso(user_row):
            error = "Voce nao pode mudar o seu proprio nivel de acesso."
        elif not _is_valid_email(email):
            error = "Informe um email valido."
        elif not _is_valid_phone(telefone):
            error = "Informe um numero de telefone valido, com codigo do pais (ex.: 5511999999999)."
        if error:
            if request.headers.get("X-Autosave") == "1":
                return {"ok": False, "message": error}
            flash(error, "error")
            codigo_pais, numero_telefone = _split_phone(telefone)
            return render_template(
                "admin_edit_user.html", user=user_row, email=email,
                codigo_pais=codigo_pais, numero_telefone=numero_telefone, nivel=nivel,
                niveis=NIVEIS_ACESSO,
            )
        models.set_user_contato(user_id, email, telefone, is_admin=nivel != "usuario", is_master=nivel == "master")
        if action == "test" and not whatsapp.conectado():
            flash(f"Cadastro de '{user_row['username']}' atualizado. " + _AVISO_WHATSAPP_DESCONECTADO, "error")
            return redirect(url_for("admin_users"))
        elif action == "test":
            ok, message = whatsapp.send_whatsapp(
                telefone, "OneAgro: mensagem de teste. Se voce recebeu isso, o numero esta certo!",
            )
            flash(
                f"Cadastro de '{user_row['username']}' atualizado. "
                + ("Mensagem de teste enviada!" if ok else f"Falha no teste: {message}"),
                "success" if ok else "error",
            )
            return redirect(url_for("admin_users"))
        return _save_response(f"Cadastro de '{user_row['username']}' atualizado.", "admin_users")
    codigo_pais, numero_telefone = _split_phone(user_row["telefone"] or "")
    return render_template(
        "admin_edit_user.html", user=user_row, email=user_row["email"] or "",
        codigo_pais=codigo_pais, numero_telefone=numero_telefone, nivel=_nivel_acesso(user_row),
        niveis=NIVEIS_ACESSO,
    )


@app.route("/admin/users/<int:user_id>/reset-password", methods=["POST"])
@aba_required("usuarios")
def admin_reset_password(user_id):
    user_row = models.get_user_by_id(user_id)
    if not user_row:
        abort(404)
    if user_row["is_master"] and current_user.username != ALAN_MAURO_USERNAME:
        flash("So' o Alan Mauro pode redefinir a senha de um usuario Master.", "error")
        return redirect(url_for("admin_users"))
    if _RANK_NIVEL[_nivel_acesso(user_row)] > _rank_atual():
        flash("Voce nao pode redefinir a senha de um usuario com nivel acima do seu.", "error")
        return redirect(url_for("admin_users"))
    models.set_user_password(user_id, models.DEFAULT_PASSWORD)
    flash(f"Senha de '{user_row['username']}' redefinida para '{models.DEFAULT_PASSWORD}'.", "success")
    return redirect(url_for("admin_users"))


@app.route("/trocar-senha", methods=["GET", "POST"])
@login_required
def change_password():
    if request.method == "POST":
        atual = request.form.get("senha_atual", "")
        nova = request.form.get("senha_nova", "")
        confirmar = request.form.get("senha_confirmar", "")
        user_row = models.get_user_by_id(int(current_user.id))
        if not check_password_hash(user_row["password_hash"], atual):
            flash("Senha atual incorreta.", "error")
        elif not nova or len(nova) < 6:
            flash("A nova senha precisa ter pelo menos 6 caracteres.", "error")
        elif nova != confirmar:
            flash("A confirmacao nao bate com a nova senha.", "error")
        else:
            models.set_user_password(int(current_user.id), nova)
            flash("Senha alterada com sucesso.", "success")
            return redirect(url_for("mapa"))
    return render_template("change_password.html")


@app.route("/admin/users/<int:user_id>/whatsapp-pausado", methods=["POST"])
@aba_required("usuarios")
def admin_user_whatsapp_pausado(user_id):
    """Pausa/retoma o recebimento de WhatsApp desse usuario em TODAS as
    fazendas de uma vez (ver `models.set_user_whatsapp_pausado`) --
    checkbox na aba Usuarios, sem precisar desmarcar fazenda por fazenda
    em Localidades. Fetch direto (nao um form autosave da tabela
    inteira), resposta JSON pura."""
    pausado = request.form.get("pausado") == "1"
    models.set_user_whatsapp_pausado(user_id, pausado)
    return {"ok": True}


@app.route("/admin/subordinados/<int:subordinado_id>/whatsapp-pausado", methods=["POST"])
@aba_required("usuarios")
def admin_subordinado_whatsapp_pausado(subordinado_id):
    """Mesmo padrao de `admin_user_whatsapp_pausado`, pra' subordinado --
    checkbox direto na linha da aba Usuarios (sem precisar abrir a tela
    dedicada de Subordinados so' pra isso)."""
    pausado = request.form.get("pausado") == "1"
    models.set_subordinado_whatsapp_pausado(subordinado_id, pausado)
    return {"ok": True}


@app.route("/admin/users/<int:user_id>/delete", methods=["POST"])
@aba_required("usuarios")
def admin_delete_user(user_id):
    user_row = models.get_user_by_id(user_id)
    if str(user_id) == current_user.id:
        flash("Voce nao pode excluir o proprio usuario logado.", "error")
    elif user_row and user_row["is_master"] and current_user.username != ALAN_MAURO_USERNAME:
        flash("So' o Alan Mauro pode excluir um usuario Master.", "error")
    elif user_row and _RANK_NIVEL[_nivel_acesso(user_row)] > _rank_atual():
        flash("Voce nao pode excluir um usuario com nivel acima do seu.", "error")
    else:
        models.delete_user(user_id)
        flash("Usuario excluido.", "success")
    return redirect(url_for("admin_users"))


EXCEL_EXPORT_DIR = os.environ.get("EXCEL_EXPORT_DIR", r"C:\Users\AlanMauro\OneDrive\OneAgro\Relatorios Site")


def _save_export_copy_local(conteudo, filename):
    """Salva uma copia do Excel exportado direto numa pasta do
    computador (dentro do OneDrive, que sincroniza sozinho) -- so tenta
    isso rodando local no Windows (onde `EXCEL_EXPORT_DIR` de fato
    existe); no site hospedado (Railway/Linux) esse caminho nao existe
    de verdade, entao nem tenta -- o download pelo navegador continua
    funcionando do mesmo jeito nos dois casos."""
    if not Path(POWERSHELL_EXE).exists():
        return None
    try:
        export_dir = Path(EXCEL_EXPORT_DIR)
        export_dir.mkdir(parents=True, exist_ok=True)
        destino = export_dir / filename
        destino.write_bytes(conteudo)
        return destino
    except OSError:
        return None


@app.route("/admin/acessos", methods=["GET", "POST"])
@master_required
def admin_acessos():
    """Aba Acessos (so' Master): quais abas do menu Configuracoes cada
    nivel (Administrador / Usuario) ve -- ver `ABAS_CONFIG`."""
    if request.method == "POST":
        validas = {c for c, _r, _e in ABAS_CONFIG}
        novo = {
            nivel: sorted(c for c in request.form.getlist(f"abas_{nivel}") if c in validas)
            for nivel in ACESSOS_PADRAO
        }
        models.set_setting(_ACESSOS_KEY, json.dumps(novo))
        g._acessos_por_nivel = None
        return _save_response("Acessos atualizados.", "admin_acessos")
    return render_template(
        "admin_acessos.html", abas=ABAS_CONFIG, acessos=_acessos_por_nivel(),
        niveis=[("admin", NIVEIS_ACESSO["admin"]), ("usuario", NIVEIS_ACESSO["usuario"])],
    )


@app.route("/admin/fazendas-ocultas")
@master_required
def fazendas_ocultas():
    """Fazendas que nao aparecem normalmente: desativadas (somem do site
    inteiro, ver `save_site_ativo`) e sem leitura ha mais de
    DADOS_BLOQUEIO_DIAS (doencas escondidas, relatorio so' de clima). Cada
    uma com botao Reativar -- so' pro Alan."""
    translations = _load_translations()
    desativadas = models.get_deactivated_site_names()
    virtual_clima = {vf["site_name"] for vf in models.get_all_virtual_farms() if vf.get("tipo") == "clima"}
    liberados = _bloqueios_liberados()
    sem_leitura = []
    for site in sorted((set(read_sites()) | models.virtual_farm_site_names()) - desativadas - virtual_clima):
        cards = _resolve_site_cards(site, translations)
        dias = _dias_sem_leitura(cards)
        if _nivel_dados_defasados(dias) != "bloqueado":
            continue
        ultima = max(c["data"] for c in cards)
        sem_leitura.append({
            "site": site, "nome": _nome_exibicao(site), "dias": dias,
            "ultima_leitura": models.fmt_data_br(ultima),
            "reativada": bool(liberados.get(site)) and ultima <= liberados[site],
        })
    desativadas_lista = sorted(({"site": s, "nome": _nome_exibicao(s)} for s in desativadas), key=lambda d: _ordem_fazenda(d["site"]))
    sem_leitura.sort(key=lambda d: (_ordem_fazenda(d["site"]), d["dias"]))
    return render_template("fazendas_ocultas.html", desativadas=desativadas_lista, sem_leitura=sem_leitura,
                           dias_bloqueio=DADOS_BLOQUEIO_DIAS)


@app.route("/admin/fazendas-ocultas/reativar", methods=["POST"])
@master_required
def reativar_fazenda_oculta():
    site = request.form.get("site_name", "")
    motivo = request.form.get("motivo")
    if motivo == "desativada":
        models.set_site_ativo(site, True)
        flash(f"'{_nome_exibicao(site)}' reativada -- volta a aparecer em todas as telas.", "success")
    elif motivo in ("sem_leitura", "bloquear"):
        liberados = dict(_bloqueios_liberados())
        if motivo == "sem_leitura":
            cards = _resolve_site_cards(site, _load_translations())
            if cards:
                liberados[site] = max(c["data"] for c in cards)
            flash(f"'{_nome_exibicao(site)}' reativada -- doencas e recomendacoes voltam a aparecer "
                  "(ate' chegar leitura nova do BioScout).", "success")
        else:
            liberados.pop(site, None)
            flash(f"'{_nome_exibicao(site)}' bloqueada de novo (so' clima, sem doencas).", "success")
        models.set_setting(_BLOQUEIO_LIBERADO_KEY, json.dumps(liberados))
    return redirect(url_for("fazendas_ocultas"))


@app.route("/admin/exportar")
@master_required
def admin_exportar():
    """Pagina "Exportar" -- download do Excel completo (ver
    `admin_exportar_download`) e o historico de envios de WhatsApp dos
    ultimos 7 dias (filtros de fazenda/so-falhas). Quem recebe fica na aba
    Envios e o registro de fungicidas por cultura na Biblioteca de
    fungicidas (e no Excel) -- tirados daqui pra nao repetir. So'
    usuario Master tem acesso."""
    site_name = request.args.get("site") or None
    apenas_falhas = request.args.get("falhas") == "1"
    logs = models.get_whatsapp_envio_log(site_name=site_name, apenas_falhas=apenas_falhas, dias=7)
    for l in logs:
        l["site_nome"] = _nome_exibicao(l["site_name"])
    sites = sorted((set(read_sites()) | models.virtual_farm_site_names()) - models.get_deactivated_site_names(), key=_ordem_fazenda)
    nomes_exibicao = {s: _nome_exibicao(s) for s in sites}
    return render_template(
        "admin_exportar.html",
        logs=logs, sites=sites, nomes_exibicao=nomes_exibicao,
        site_selecionado=site_name or "", apenas_falhas=apenas_falhas,
    )


@app.route("/admin/estacoes-diagnostico")
@master_required
def admin_estacoes_diagnostico():
    """Amostras reais das APIs de DADOS das estacoes oficiais (INMET, DMC,
    SMN) -- so' leitura, credenciais ocultas. Existe pra' fechar o formato
    de resposta do Chile/Argentina antes de integrar (o ambiente de
    desenvolvimento nao alcanca esses servidores)."""
    estacoes_em_uso = {}
    for escolha in models.get_all_weather_station_overrides().values():
        if escolha["codigo"] and escolha["codigo"] != INTERPOLACAO_SENTINEL:
            estacoes_em_uso.setdefault(escolha["country_code"], escolha["codigo"])
    hoje = _hoje_cuiaba()
    verificacao = []
    for site in sorted(_coords_all(), key=_ordem_fazenda):
        escolha = models.get_all_weather_station_overrides().get(site)
        if not escolha or escolha["country_code"] not in ("BR", "CL", "AR"):
            continue
        estacao = _estacao_oficial_do_site(site)
        linha = {"fazenda": _nome_exibicao(site), "pais": escolha["country_code"], "escolhida": escolha["codigo"],
                 "usada": estacao[1] if estacao else "-", "horas": "-", "ultima": "-", "previsao": "-"}
        if estacao:
            fonte = weather_forecast.fonte_estacao(estacao[0], estacao[1])
            if fonte:
                linha["usada"] = f"{estacao[1]} ({fonte})"
        if estacao:
            por_dia = weather_forecast.horas_estacao_por_dia(*estacao, (hoje - timedelta(days=3)).isoformat(), hoje.isoformat())
            ultimas = weather_forecast.ultimas_24h_estacao(*estacao)
            linha["horas"] = sum(len(h) for h in por_dia.values())
            linha["ultima"] = f"{ultimas[-1]['hora']} -- {ultimas[-1]['temp']} C, {ultimas[-1]['umidade']}%" if ultimas else "-"
            linha["previsao"] = len(weather_forecast.previsao_estacao(estacao[0], estacao[1])) if estacao[0] == "AR" else "Open-Meteo"
        verificacao.append(linha)
    return render_template(
        "admin_estacoes_diagnostico.html", amostras=estacoes_dados.diagnostico(estacoes_em_uso),
        verificacao=verificacao, erro_clima=weather_forecast.ultimo_erro,
    )


@app.route("/admin/exportar/download")
@master_required
def admin_exportar_download():
    """Relatorio Excel com o maximo de informacao possivel do site --
    usuarios/subordinados, cadastro de fazendas (pais/estacao/nome de
    exibicao), Fazendas/Manejo das 3 safras, doencas (traducao/
    germinacao/culturas/paises), culturas, leituras atuais, WhatsApp
    (historico/destinatarios/agenda), fungicidas (biblioteca completa) e
    relatorio diario de clima (ver `export_excel.build_workbook`) --
    botao "Baixar Excel completo" na pagina `admin_exportar`."""
    buffer = export_excel.build_workbook(completar_clima=_completar_clima_horario)
    filename = f"OneAgro_Export_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
    conteudo = buffer.getvalue()
    destino = _save_export_copy_local(conteudo, filename)
    if destino:
        flash(f"Copia tambem salva em {destino}.", "success")
    return send_file(
        buffer, as_attachment=True, download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


def _restaurar_backup_enviado(arquivo):
    """Confere e carrega o .db.gz/.db enviado no banco do QAP. Devolve a
    mensagem de erro, ou None se restaurou."""
    conteudo = arquivo.read() if arquivo else b""
    if conteudo[:2] == b"\x1f\x8b":
        try:
            conteudo = gzip.decompress(conteudo)
        except OSError:
            conteudo = b""
    if not conteudo.startswith(b"SQLite format 3\x00"):
        return "Arquivo invalido -- use o .db.gz do botao \"Baixar backup do banco\" (Exportar) da producao."
    with tempfile.TemporaryDirectory() as pasta:
        caminho = Path(pasta) / "restaurar.db"
        caminho.write_bytes(conteudo)
        conn = sqlite3.connect(caminho)
        try:
            integro = conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            tem_usuarios = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'users'"
            ).fetchone() is not None
        except sqlite3.DatabaseError:
            integro = tem_usuarios = False
        finally:
            conn.close()
        if not (integro and tem_usuarios):
            return "O arquivo nao e' um backup valido do OneAgro (banco corrompido ou sem usuarios)."
        models.restaurar_banco(caminho)
    return None


@app.route("/admin/restaurar-backup", methods=["GET", "POST"])
@master_required
def admin_restaurar_backup():
    """So' no QAP: carrega o arquivo do "Baixar backup do banco" da
    producao (.db.gz ou .db) no banco do ambiente de teste. No site de
    verdade a rota nem existe (404)."""
    if not AMBIENTE_QAP:
        abort(404)
    if request.method == "POST":
        erro = _restaurar_backup_enviado(request.files.get("arquivo"))
        if erro:
            flash(erro, "error")
            return redirect(url_for("admin_restaurar_backup"))
        logout_user()  # o id do usuario logado pode ser outro no banco restaurado
        flash("Backup restaurado no QAP. Entre de novo com o seu usuario e senha da producao.", "success")
        return redirect(url_for("login"))
    return render_template("admin_restaurar_backup.html")


def _qap_sem_master():
    return AMBIENTE_QAP and not models.existe_master()


@app.context_processor
def _inject_qap_sem_master():
    return {"qap_sem_master": _qap_sem_master() if request.endpoint == "login" else False}


@app.route("/qap/primeiro-acesso", methods=["GET", "POST"])
def qap_primeiro_acesso():
    """QAP recem-criado (banco vazio, ninguem consegue entrar): carrega o
    backup da producao sem login. So' existe no QAP e so' enquanto o banco
    nao tiver nenhum Master -- depois da 1a restauracao, some."""
    if not _qap_sem_master():
        abort(404)
    if request.method == "POST":
        erro = _restaurar_backup_enviado(request.files.get("arquivo"))
        if erro:
            flash(erro, "error")
            return redirect(url_for("qap_primeiro_acesso"))
        flash("Backup restaurado no QAP. Entre com o seu usuario e senha da producao.", "success")
        return redirect(url_for("login"))
    return render_template("admin_restaurar_backup.html", primeiro_acesso=True)


@app.route("/admin/exportar/backup")
@master_required
def admin_exportar_backup():
    """Copia do banco inteiro (.db compactado em .gz) pra guardar fora do
    servidor -- o backup diario automatico fica no mesmo volume do banco."""
    with tempfile.TemporaryDirectory() as pasta:
        copia = Path(pasta) / "banco.db"
        models.copiar_banco(copia)
        conteudo = gzip.compress(copia.read_bytes())
    filename = f"backup_oneagro_{datetime.now().strftime('%Y%m%d_%H%M')}.db.gz"
    return send_file(io.BytesIO(conteudo), as_attachment=True, download_name=filename, mimetype="application/gzip")


@app.route("/admin/whatsapp")
@master_required
def admin_whatsapp():
    """Tela de pareamento do WhatsApp corporativo (QR code/codigo de
    pareamento, teste de envio, trocar numero) -- so' Master
    tem acesso, ja que controla o remetente usado por TODAS as fazendas."""
    return render_template("admin_whatsapp.html", status=whatsapp.get_status())


@app.route("/admin/whatsapp/status")
@master_required
def admin_whatsapp_status():
    """JSON puro pra tela atualizar sozinha (QR code/status de conexao)
    sem precisar recarregar a pagina inteira."""
    return whatsapp.get_status()


@app.route("/admin/whatsapp/pair-code", methods=["POST"])
@master_required
def admin_whatsapp_pair_code():
    phone = request.form.get("phone", "")
    ok, resultado = whatsapp.request_pairing_code(phone)
    return {"ok": ok, "code": resultado if ok else None, "error": None if ok else resultado}


@app.route("/admin/whatsapp/test", methods=["POST"])
@master_required
def admin_whatsapp_test():
    phone = _telefone_from_form(request.form)
    texto = (
        "Teste do OneAgro Monitor -- se voce recebeu essa mensagem, o envio "
        "de relatorios por WhatsApp esta funcionando corretamente."
    )
    ok, mensagem = whatsapp.send_whatsapp(phone, texto)
    return _save_response(
        f"Mensagem de teste enviada para {phone}." if ok else f"Falha ao enviar teste: {mensagem}",
        "admin_whatsapp", ok=ok,
    )


@app.route("/admin/whatsapp/reset", methods=["POST"])
@master_required
def admin_whatsapp_reset():
    """Desconecta o numero atual pra poder conectar outro (ex.: trocar o
    WhatsApp corporativo) -- limpa a sessao salva no whatsapp-bridge e
    gera um QR code novo."""
    ok, mensagem = whatsapp.reset_session()
    return _save_response(
        "Numero desconectado -- escaneie o novo QR code pra conectar outro." if ok else f"Falha ao desconectar: {mensagem}",
        "admin_whatsapp", ok=ok,
    )


@app.route("/admin/users/<int:user_id>/permissions", methods=["GET", "POST"])
@aba_required("usuarios")
def admin_user_permissions(user_id):
    user_row = models.get_user_by_id(user_id)
    if not user_row:
        abort(404)
    if request.method == "POST":
        selected_ids = {int(v) for v in request.form.getlist("site_ids")}
        models.set_user_permissions(user_id, selected_ids)
        return _save_response(f"Permissoes de '{user_row['username']}' atualizadas.", "admin_users")
    all_sites = sorted(models.get_all_sites(), key=lambda s: _ordem_fazenda(s["site_name"]))
    permitted_ids = models.get_user_permitted_site_ids(user_id)
    return render_template(
        "admin_permissions.html", user=user_row, sites=all_sites, permitted_ids=permitted_ids
    )


@app.route("/admin/users/<int:user_id>/relatorios", methods=["GET", "POST"])
@aba_required("usuarios")
def admin_user_reports(user_id):
    user_row = models.get_user_by_id(user_id)
    if not user_row:
        abort(404)
    if request.method == "POST":
        selected_ids = {int(v) for v in request.form.getlist("site_ids")}
        models.set_user_report_permissions(user_id, selected_ids)
        return _save_response(
            f"Fazendas que '{user_row['username']}' recebe relatorio de WhatsApp foram atualizadas.", "admin_users"
        )
    all_sites = sorted(models.get_all_sites(), key=lambda s: _ordem_fazenda(s["site_name"]))
    report_ids = models.get_user_report_site_ids(user_id)
    return render_template(
        "admin_report_permissions.html", user=user_row, sites=all_sites, report_ids=report_ids
    )


@app.route("/admin/users/<int:user_id>/subordinados", methods=["GET", "POST"])
@aba_required("usuarios")
def admin_user_subordinados(user_id):
    """Subordinados (aba Usuarios) -- contatos leves (nome + telefone, sem
    login) ligados a um usuario "dono", cada um recebendo relatorio de
    WhatsApp de um subconjunto das fazendas que o proprio dono ja recebe
    (`user_report_permissions`) -- pensado pra dividir uma equipe de
    campo do mesmo proprietario entre fazendas diferentes."""
    user_row = models.get_user_by_id(user_id)
    if not user_row:
        abort(404)

    if request.method == "POST":
        form_id = request.form.get("form_id")
        if form_id == "add":
            nome = request.form.get("nome", "").strip()
            numero = re.sub(r"\D", "", request.form.get("numero_telefone", ""))
            codigo = re.sub(r"\D", "", request.form.get("codigo_pais", "")) or "55"
            if not nome or not numero:
                flash("Nome e telefone sao obrigatorios pra adicionar um subordinado.", "error")
            else:
                models.create_subordinado(user_id, nome, codigo + numero)
                flash(f"Subordinado '{nome}' adicionado.", "success")
            return redirect(url_for("admin_user_subordinados", user_id=user_id))

        if form_id == "delete":
            sub_id = int(request.form.get("subordinado_id"))
            models.delete_subordinado(sub_id)
            return _save_response("Subordinado excluido.", "admin_user_subordinados", user_id=user_id)

        # form_id == "save" (padrao) -- salva nome/telefone/fazendas de
        # todos os subordinados listados na tela de uma vez (autosave).
        # DDI e numero (DDD + numero) sao lidos do formulario, por linha.
        ids = [int(v) for v in request.form.getlist("subordinado_id")]
        nomes = request.form.getlist("nome")
        for idx, sub_id in enumerate(ids):
            nome = nomes[idx].strip() if idx < len(nomes) else ""
            numero = re.sub(r"\D", "", request.form.get(f"numero_telefone__{sub_id}", ""))
            codigo = re.sub(r"\D", "", request.form.get(f"codigo_pais__{sub_id}", "")) or "55"
            if not nome or not numero:
                continue
            models.update_subordinado(sub_id, nome, codigo + numero)
            site_ids = {int(v) for v in request.form.getlist(f"site_ids__{sub_id}")}
            models.set_subordinado_report_sites(sub_id, site_ids)
            models.set_subordinado_whatsapp_pausado(sub_id, request.form.get(f"whatsapp_pausado__{sub_id}") == "1")
        return _save_response("Subordinados atualizados.", "admin_user_subordinados", user_id=user_id)

    owner_report_ids = models.get_user_report_site_ids(user_id)
    all_sites = sorted(models.get_all_sites(), key=lambda s: _ordem_fazenda(s["site_name"]))
    owner_sites = [s for s in all_sites if s["id"] in owner_report_ids]
    subordinados = models.get_owner_subordinados(user_id)
    for sub in subordinados:
        sub["codigo_pais"], sub["numero_telefone"] = _split_phone(sub["telefone"])
    return render_template(
        "admin_subordinados.html", user=user_row, owner_sites=owner_sites, subordinados=subordinados,
    )


# --- App de celular: clientes pagantes (planos/assinatura) ------------------
# Cliente (`users.is_cliente=1`) se cadastra sozinho em /cadastro, paga a
# assinatura no Asaas (ver asaas.py) e so' enxerga a area /app: marca suas
# lavouras no mapa e ve, por ponto, o que o plano dele libera -- esporos
# ESTIMADOS por interpolacao das fazendas reais no raio do plano (nunca o
# nome nem o dado bruto de uma fazenda real), clima, risco de infeccao e
# recomendacao de fungicida. Planos e precos sao do Master (/admin/planos).
# O app Android/iOS (pasta mobile/, Capacitor) abre este mesmo site com
# "OneAgroApp" no user-agent: dentro dele nao aparece preco nem link de
# pagamento (regra das lojas -- assinatura vendida fora da loja).

_ENDPOINTS_LIVRES_CLIENTE = {
    "static", "service_worker", "index", "login", "logout", "esqueci_senha",
    "definir_nova_senha", "planos_publico", "cadastro", "solicitar_acesso", "verificar_usuario_cadastro", "asaas_webhook",
}
_CPF_CNPJ_RE = re.compile(r"\D")


def _no_app_loja():
    return "OneAgroApp" in (request.user_agent.string or "")


@app.context_processor
def _inject_no_app_loja():
    return {
        "no_app_loja": _no_app_loja() if request else False,
        "gerado_em": _agora_cuiaba_dt().strftime("%d/%m %H:%M"),
    }


# Abas do site que o plano do cliente pode liberar (recursos "site_*" em
# models.RECURSOS_PLANO, pedido do Alan, 31/10/2026). Nelas o cliente ve so'
# as fazendas liberadas pra ele, igual a um Usuario comum.
ABAS_SITE_CLIENTE = {
    "mapa": "site_mapa",
    "dashboard": "site_alertas",
    "graficos": "site_graficos", "graficos_dados": "site_graficos",
    "fazendas": "site_manejos", "save_farm_produtos": "site_manejos", "save_farm_plantio": "site_manejos",
    "save_farm_espacamento": "site_manejos", "save_farm_aplicacoes": "site_manejos",
    "save_farm_cultura": "site_manejos", "save_recommendations": "site_manejos", "save_produtos_folha": "site_manejos",
    "recommendations": "site_recomendacoes", "recommendations_default": "site_recomendacoes",
    "recommendation_pdf": "site_recomendacoes",
}
ABAS_SITE_MENU = [  # (recurso, rotulo, endpoint do link)
    ("site_mapa", "Mapa", "mapa"), ("site_alertas", "Alertas", "dashboard"), ("site_graficos", "Graficos", "graficos"),
    ("site_manejos", "Manejos", "fazendas"), ("site_recomendacoes", "Recomendações", "recommendations_default"),
]


def _abas_site_do_cliente():
    """Recursos "site_*" do plano do cliente logado (vazio sem plano em dia)."""
    abas = getattr(g, "_abas_site_cliente", None)
    if abas is None:
        _assinatura, plano, liberada = _cliente_contexto()
        abas = g._abas_site_cliente = {r for r in (plano or {}).get("recursos", []) if r.startswith("site_")} if liberada else set()
    return abas


@app.before_request
def _restringir_cliente():
    if not current_user.is_authenticated or not current_user.is_cliente:
        return
    endpoint = request.endpoint or ""
    if endpoint in ABAS_SITE_CLIENTE and ABAS_SITE_CLIENTE[endpoint] in _abas_site_do_cliente():
        return
    if endpoint in ("change_password", "salvar_preferencia"):
        return
    if not endpoint.startswith("cliente_") and endpoint not in _ENDPOINTS_LIVRES_CLIENTE:
        return redirect(url_for("cliente_home"))


@app.context_processor
def _inject_abas_site_cliente():
    if current_user.is_authenticated and getattr(current_user, "is_cliente", False):
        abas = _abas_site_do_cliente()
        return {"menu_site_cliente": [(rot, ep) for rec, rot, ep in ABAS_SITE_MENU if rec in abas]}
    return {"menu_site_cliente": None}


def _cpf_cnpj_valido(numero):
    """Digitos verificadores de CPF (11) ou CNPJ (14) -- o Asaas recusa
    documento invalido, melhor avisar ja' no formulario."""
    if len(numero) not in (11, 14) or len(set(numero)) == 1:
        return False
    digitos = [int(c) for c in numero]
    if len(numero) == 11:
        for n in (9, 10):
            soma = sum(d * p for d, p in zip(digitos[:n], range(n + 1, 1, -1)))
            if (soma * 10 % 11) % 10 != digitos[n]:
                return False
        return True
    for n, pesos in ((12, [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]), (13, [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])):
        resto = sum(d * p for d, p in zip(digitos[:n], pesos)) % 11
        if (0 if resto < 2 else 11 - resto) != digitos[n]:
            return False
    return True


def _cliente_contexto():
    """(assinatura, plano, liberada) do cliente logado."""
    assinatura = models.get_assinatura(int(current_user.id))
    plano = models.get_plano(assinatura["plano_id"]) if assinatura and assinatura["plano_id"] else None
    return assinatura, plano, models.assinatura_liberada(assinatura)


def cliente_required(view):
    """Rota da area /app que exige assinatura em dia -- sem ela, vai pra
    tela da assinatura."""
    @functools.wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if not current_user.is_cliente:
            abort(403)
        assinatura, plano, liberada = _cliente_contexto()
        if not liberada or not plano:
            return redirect(url_for("cliente_assinatura"))
        g.cliente_plano = plano
        return view(*args, **kwargs)
    return wrapped


def _iniciar_cobranca(user_id, plano):
    """Cria cliente + assinatura no Asaas (1o vencimento hoje) e devolve
    o link da 1a fatura (ou None). Levanta asaas.AsaasErro."""
    user = models.get_user_by_id(user_id)
    assinatura = models.get_assinatura(user_id)
    referencia = f"cliente:{user_id}"
    customer_id = assinatura.get("asaas_customer_id")
    if not customer_id:
        customer_id = asaas.criar_cliente(
            user["username"], assinatura["cpf_cnpj"], user["email"], user["telefone"], referencia,
        )
        models.atualizar_assinatura(user_id, asaas_customer_id=customer_id)
    subscription_id = asaas.criar_assinatura(
        customer_id, plano["valor"], plano["ciclo"], f"OneAgro Monitor - plano {plano['nome']}",
        _hoje_cuiaba().isoformat(), referencia,
    )
    models.atualizar_assinatura(user_id, asaas_subscription_id=subscription_id, plano_id=plano["id"], status="pendente")
    link = None
    for pagamento in asaas.pagamentos_da_assinatura(subscription_id):
        models.registrar_pagamento(
            user_id, pagamento["id"], pagamento.get("value"), pagamento.get("status"),
            pagamento.get("dueDate"), pagamento.get("invoiceUrl"),
        )
        link = link or pagamento.get("invoiceUrl")
    return link


@app.route("/planos")
def planos_publico():
    return render_template("cliente_planos.html", planos=models.get_planos(apenas_ativos=True), recursos=models.RECURSOS_PLANO)


@app.route("/cadastro", methods=["GET", "POST"])
def cadastro():
    # Link antigo do cadastro com pagamento direto cai no formulario de
    # cadastro (`solicitar_acesso`).
    plano = request.args.get("plano")
    return redirect(url_for("solicitar_acesso", modalidade="plano") if plano else url_for("solicitar_acesso"))


UFS_BR = ["AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS", "MG", "PA", "PB", "PR",
          "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC", "SP", "SE", "TO"]
MODALIDADES_CADASTRO = {"plano": "Assinar plano", "fazenda": "Ter acesso à fazenda cadastrada"}
PERFIS_CADASTRO = {"produtor": "Produtor", "tecnico": "Técnico do setor"}


def _sugerir_usuario(nome_completo):
    """'Nome Ultimo-sobrenome' (pedido do Alan); se ja' existir, acrescenta
    2, 3... ate' achar um livre."""
    partes = [p.capitalize() for p in nome_completo.split()]
    if not partes:
        return ""
    base = f"{partes[0]} {partes[-1]}" if len(partes) > 1 else partes[0]
    sugestao, n = base, 2
    while models.username_em_uso(sugestao):
        sugestao, n = f"{base} {n}", n + 1
    return sugestao


@app.route("/solicitar-acesso/usuario")
def verificar_usuario_cadastro():
    usuario = request.args.get("usuario", "").strip()
    return jsonify({
        "disponivel": len(usuario) >= 3 and not models.username_em_uso(usuario),
        "sugestao": _sugerir_usuario(request.args.get("nome") or usuario),
    })


@app.route("/solicitar-acesso", methods=["GET", "POST"])
def solicitar_acesso():
    """Cadastro publico (pedido do Alan, 31/10/2026): a conta nasce na hora
    e a pessoa ja' entra. "Assinar plano" vira cliente do app (escolhe o
    plano e informa CPF/CNPJ na tela da assinatura); "Ter acesso a fazenda
    cadastrada" vira Usuario sem nenhuma fazenda ate' o Master liberar. O
    Master ve cada cadastro em Configuracoes > Novos cadastros."""
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    culturas = models.get_culturas_ativas()
    form = request.form
    if request.method == "POST":
        modalidade = form.get("modalidade", "")
        perfis = [p for p in form.getlist("perfil") if p in PERFIS_CADASTRO]
        nome_completo = " ".join(form.get("nome_completo", "").split())
        username = " ".join(form.get("username", "").split())
        email = form.get("email", "").strip()
        telefone = _telefone_from_form(form)
        uf = form.get("uf", "")
        culturas_sel = [c for c in form.getlist("culturas") if c in culturas]
        outra = form.get("cultura_outra", "").strip()
        area_txt = form.get("area_ha", "").strip().replace(".", "").replace(",", ".")
        try:
            area_ha = float(area_txt)
        except ValueError:
            area_ha = 0
        senha = form.get("senha", "")
        erro = None
        if modalidade not in MODALIDADES_CADASTRO:
            erro = "Escolha o que você quer: assinar um plano ou ter acesso à fazenda cadastrada."
        elif not perfis:
            erro = "Marque se você é produtor e/ou técnico do setor."
        elif len(nome_completo.split()) < 2:
            erro = "Informe o seu nome completo (nome e sobrenome)."
        elif len(username) < 3:
            erro = "O usuário precisa ter pelo menos 3 letras."
        elif models.username_em_uso(username):
            erro = f"O usuário \"{username}\" já existe. Sugestão: {_sugerir_usuario(nome_completo)}."
        elif not _is_valid_phone(telefone):
            erro = "Celular inválido -- informe DDD + número."
        elif email and not _is_valid_email(email):
            erro = "E-mail inválido."
        elif (ja_cadastrado := models.conta_com_contato(telefone, email)):
            # Mesma pessoa tentando um 2o cadastro (pedido do Alan, 01/11/2026)
            erro = (f"Este {'celular' if ja_cadastrado == 'telefone' else 'e-mail'} já está cadastrado na OneAgro. "
                    "Entre com a sua conta na tela de login (se não lembrar o usuário ou a senha, fale com a OneAgro).")
        elif not form.get("empresa", "").strip():
            erro = "Informe a fazenda ou empresa."
        elif not form.get("cidade", "").strip() or uf not in UFS_BR:
            erro = "Informe a cidade e o estado (UF)."
        elif not culturas_sel and not outra:
            erro = "Marque pelo menos uma cultura."
        elif area_ha <= 0:
            erro = "Informe a área plantada/atendida em hectares (só o número)."
        elif len(senha) < 6:
            erro = "A senha precisa ter pelo menos 6 caracteres."
        elif senha != form.get("senha_confirmar", ""):
            erro = "A confirmação não bate com a senha."
        elif not form.get("aceite"):
            erro = "É preciso aceitar os termos de uso para continuar."
        if erro:
            flash(erro, "error")
        else:
            try:
                if modalidade == "plano":
                    user_id = models.criar_cliente(username, senha, email, telefone, "", None)
                else:
                    models.create_user(username, senha, email=email, telefone=telefone)
                    user_id = models.get_user_by_username(username)["id"]
            except sqlite3.IntegrityError:
                flash(f"O usuário \"{username}\" acabou de ser usado. Sugestão: {_sugerir_usuario(nome_completo)}.", "error")
                return render_template("solicitar_acesso.html", culturas=culturas, ufs=UFS_BR, modalidades=MODALIDADES_CADASTRO,
                                       perfis=PERFIS_CADASTRO, form=form)
            registro = {
                "username": username, "nome_completo": nome_completo, "perfil": ",".join(perfis), "email": email,
                "telefone": telefone, "tipo": "cliente" if modalidade == "plano" else "usuario",
                "empresa": form.get("empresa", "").strip(), "cidade": form.get("cidade", "").strip(), "uf": uf,
                "culturas": ", ".join(culturas_sel + ([outra] if outra else [])), "area_ha": area_ha,
                "mensagem": form.get("mensagem", "").strip(), "user_id": user_id,
            }
            models.registrar_cadastro(registro)
            _avisar_master_cadastro(registro)
            login_user(User(models.get_user_by_id(user_id)), remember=True)
            if modalidade == "plano":
                flash("Conta criada! Agora escolha o seu plano para liberar o acesso.", "success")
                return redirect(url_for("cliente_assinatura"))
            flash("Conta criada! A OneAgro vai liberar as fazendas para você -- assim que isso acontecer, elas aparecem aqui.", "success")
            return redirect(url_for("index"))
    return render_template("solicitar_acesso.html", culturas=culturas, ufs=UFS_BR, modalidades=MODALIDADES_CADASTRO,
                           perfis=PERFIS_CADASTRO, form=form)


def _whatsapp_em_segundo_plano(telefone, texto):
    """O portao de envio (`whatsapp._um_envio_por_vez`) pode segurar a
    mensagem por varios segundos -- a tela responde na hora."""
    threading.Thread(target=whatsapp.send_whatsapp, args=(telefone, texto), daemon=True).start()


def _avisar_master_cadastro(reg):
    perfis = " e ".join(PERFIS_CADASTRO[p] for p in reg["perfil"].split(",") if p in PERFIS_CADASTRO)
    linhas = [
        "🔔 *Novo cadastro - OneAgro*",
        f"{reg['nome_completo']} (usuário: {reg['username']})",
        f"Quer: {'Assinar plano' if reg['tipo'] == 'cliente' else 'Ter acesso à fazenda cadastrada'}",
        f"Perfil: {perfis}",
        f"Celular: {models.fmt_telefone_br(reg['telefone'])}",
        f"{reg['empresa']} -- {reg['cidade']}/{reg['uf']}",
        "",
        f"Detalhes: {url_for('admin_solicitacoes', _external=True)}",
    ]
    for telefone in models.get_master_telefones():
        _whatsapp_em_segundo_plano(telefone, "\n".join(linhas))


def _cobrar_ou_avisar(user_id, plano):
    if not asaas.configurado():
        flash("Conta criada! O pagamento online ainda não está disponível -- a OneAgro vai entrar em contato para liberar seu acesso.", "success")
        return redirect(url_for("cliente_assinatura"))
    try:
        link = _iniciar_cobranca(user_id, plano)
    except asaas.AsaasErro as e:
        print(f"[assinatura] falha ao criar cobranca do cliente {user_id}: {e}")
        flash(f"Não foi possível gerar a cobrança agora ({e}). Tente de novo em alguns minutos.", "error")
        return redirect(url_for("cliente_assinatura"))
    if link:
        return redirect(link)
    flash("Assinatura criada. A fatura chega no seu e-mail.", "success")
    return redirect(url_for("cliente_assinatura"))


@app.route("/app/assinatura")
@login_required
def cliente_assinatura():
    if not current_user.is_cliente:
        abort(403)
    assinatura, plano, liberada = _cliente_contexto()
    return render_template(
        "cliente_assinatura.html", assinatura=assinatura, plano=plano, liberada=liberada,
        acesso_ate=models.acesso_ate(assinatura), pagamentos=models.get_pagamentos(int(current_user.id)),
        # Todos os planos aparecem; os fora de venda (ou ainda sem preco, como
        # os pagos recem-criados) ficam visiveis como "Em breve", sem escolha.
        planos=models.get_planos(), pode_assinar=_pode_assinar(assinatura),
        recursos=models.RECURSOS_PLANO, sem_prazo=models.SEM_PRAZO,
    )


def _pode_assinar(assinatura):
    return not assinatura or not assinatura.get("asaas_subscription_id") or assinatura.get("status") == "cancelada"


@app.route("/app/assinatura/assinar", methods=["POST"])
@login_required
def cliente_assinar():
    if not current_user.is_cliente or _no_app_loja():
        abort(403)
    assinatura = models.get_assinatura(int(current_user.id))
    if not _pode_assinar(assinatura):
        flash("Você já tem uma assinatura em andamento.", "error")
        return redirect(url_for("cliente_assinatura"))
    plano = next((p for p in models.get_planos(apenas_ativos=True) if str(p["id"]) == request.form.get("plano_id")), None)
    if not plano:
        flash("Escolha um plano.", "error")
        return redirect(url_for("cliente_assinatura"))
    if plano["valor"] <= 0:
        # Plano gratis: libera na hora, sem cobranca nem CPF/CNPJ.
        models.atualizar_assinatura(int(current_user.id), plano_id=plano["id"], status="ativa", cortesia_ate=models.SEM_PRAZO)
        flash(f"Plano {plano['nome']} ativado!", "success")
        return redirect(url_for("cliente_home"))
    cpf_cnpj = _CPF_CNPJ_RE.sub("", request.form.get("cpf_cnpj", "")) or (assinatura or {}).get("cpf_cnpj") or ""
    if not _cpf_cnpj_valido(cpf_cnpj):
        flash("Informe um CPF ou CNPJ válido para a cobrança.", "error")
        return redirect(url_for("cliente_assinatura"))
    if (assinatura or {}).get("cortesia_ate") == models.SEM_PRAZO:
        # Saindo do gratis pra um pago: o acesso passa a depender do pagamento.
        models.atualizar_assinatura(int(current_user.id), cortesia_ate=None, status="pendente")
    models.atualizar_assinatura(int(current_user.id), plano_id=plano["id"], cpf_cnpj=cpf_cnpj)
    return _cobrar_ou_avisar(int(current_user.id), plano)


@app.route("/app/assinatura/cancelar", methods=["POST"])
@login_required
def cliente_cancelar_assinatura():
    if not current_user.is_cliente:
        abort(403)
    user_id = int(current_user.id)
    assinatura = models.get_assinatura(user_id)
    if assinatura and assinatura.get("asaas_subscription_id") and assinatura["status"] != "cancelada":
        try:
            asaas.cancelar_assinatura(assinatura["asaas_subscription_id"])
        except asaas.AsaasErro as e:
            flash(f"Não foi possível cancelar agora ({e}). Tente de novo em alguns minutos.", "error")
            return redirect(url_for("cliente_assinatura"))
    models.atualizar_assinatura(user_id, status="cancelada")
    flash("Assinatura cancelada. Seu acesso continua até o fim do período já pago.", "success")
    return redirect(url_for("cliente_assinatura"))


@app.route("/app/conta", methods=["GET", "POST"])
@login_required
def cliente_conta():
    if not current_user.is_cliente:
        abort(403)
    user_id = int(current_user.id)
    if request.method == "POST":
        row = models.get_user_by_id(user_id)
        atual = request.form.get("senha_atual", "")
        nova = request.form.get("senha_nova", "")
        if not check_password_hash(row["password_hash"], atual):
            flash("Senha atual incorreta.", "error")
        elif len(nova) < 6:
            flash("A nova senha precisa ter pelo menos 6 caracteres.", "error")
        elif nova != request.form.get("senha_confirmar", ""):
            flash("A confirmação não bate com a nova senha.", "error")
        else:
            models.set_user_password(user_id, nova)
            flash("Senha alterada.", "success")
        return redirect(url_for("cliente_conta"))
    return render_template("cliente_conta.html", user=models.get_user_by_id(user_id))


@app.route("/app/conta/excluir", methods=["POST"])
@login_required
def cliente_excluir_conta():
    """Exclusao da propria conta (exigencia da App Store/Google Play pra
    app com cadastro): cancela a cobranca e apaga usuario, assinatura e
    pontos."""
    if not current_user.is_cliente:
        abort(403)
    user_id = int(current_user.id)
    row = models.get_user_by_id(user_id)
    if not check_password_hash(row["password_hash"], request.form.get("senha", "")):
        flash("Senha incorreta -- a conta não foi excluída.", "error")
        return redirect(url_for("cliente_conta"))
    erro = _excluir_cliente(user_id)
    if erro:
        flash(f"Não foi possível cancelar a cobrança ({erro}). Tente de novo em alguns minutos.", "error")
        return redirect(url_for("cliente_conta"))
    logout_user()
    flash("Sua conta foi excluída.", "success")
    return redirect(url_for("login"))


def _excluir_cliente(user_id):
    assinatura = models.get_assinatura(user_id)
    if assinatura and assinatura.get("asaas_subscription_id") and assinatura["status"] != "cancelada":
        try:
            asaas.cancelar_assinatura(assinatura["asaas_subscription_id"])
        except asaas.AsaasErro as e:
            return str(e)
    models.delete_user(user_id)
    return None


@app.route("/app")
@cliente_required
def cliente_home():
    pontos = models.get_cliente_pontos(int(current_user.id))
    return render_template(
        "cliente_home.html", pontos=pontos, plano=g.cliente_plano,
        resumos={p["id"]: _resumo_ponto_cliente(p, g.cliente_plano) for p in pontos},
        urls_offline=[url_for("cliente_ponto", ponto_id=p["id"]) for p in pontos] + [url_for("cliente_regiao")],
    )


@app.route("/app/pontos/novo", methods=["GET", "POST"])
@cliente_required
def cliente_ponto_novo():
    user_id = int(current_user.id)
    plano = g.cliente_plano
    if len(models.get_cliente_pontos(user_id)) >= plano["max_pontos"]:
        flash(f"Seu plano permite até {plano['max_pontos']} lavoura(s).", "error")
        return redirect(url_for("cliente_home"))
    if request.method == "POST":
        nome = request.form.get("nome", "").strip()[:60]
        try:
            lat, lon = float(request.form.get("lat")), float(request.form.get("lon"))
        except (TypeError, ValueError):
            lat = lon = None
        if not nome:
            flash("Dê um nome para a lavoura.", "error")
        elif lat is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
            flash("Marque o local da lavoura no mapa.", "error")
        else:
            cultura = request.form.get("cultura", "")
            ponto_id = models.criar_cliente_ponto(user_id, nome, lat, lon, cultura if cultura in _culturas_cliente() else "")
            if "fazenda_estimada" in plano["recursos"]:
                _criar_fazenda_estimada_da_lavoura(user_id, ponto_id, nome, lat, lon, plano)
            return redirect(url_for("cliente_ponto", ponto_id=ponto_id))
    return render_template("cliente_ponto_novo.html", culturas=_culturas_cliente())


def _criar_fazenda_estimada_da_lavoura(user_id, ponto_id, nome, lat, lon, plano):
    """Mesmo processo do Mapa Interpolado (`adicionar_ponto_virtual`): fazenda
    estimada no local da lavoura, com o raio do plano, liberada so' pro
    cliente dono dela. Nome com o usuario pra nao colidir com outro cliente."""
    base = f"{nome} ({current_user.username})"
    for tentativa in range(1, 20):
        nome_fazenda = base if tentativa == 1 else f"{base} {tentativa}"
        try:
            site_name = models.create_virtual_farm(
                nome_fazenda, lat, lon, plano["raio_km"], criado_por=current_user.username,
                country_code=countries.detectar_pais_por_coordenada(lat, lon), tipo="doenca",
            )
        except sqlite3.IntegrityError:
            continue
        models.liberar_fazenda_para_usuario(user_id, site_name)
        models.ligar_ponto_a_fazenda(ponto_id, site_name)
        return site_name
    return None


def _culturas_cliente():
    return models.get_culturas_ativas()


@app.route("/app/pontos/<int:ponto_id>")
@cliente_required
def cliente_ponto(ponto_id):
    ponto = models.get_cliente_ponto(int(current_user.id), ponto_id)
    if not ponto:
        abort(404)
    return render_template(
        "cliente_ponto.html", ponto=ponto, plano=g.cliente_plano, culturas=_culturas_cliente(),
        dados=_dados_ponto_cliente(ponto, g.cliente_plano),
    )


@app.route("/app/pontos/<int:ponto_id>/editar", methods=["POST"])
@cliente_required
def cliente_ponto_editar(ponto_id):
    user_id = int(current_user.id)
    if not models.get_cliente_ponto(user_id, ponto_id):
        abort(404)
    nome = request.form.get("nome", "").strip()[:60]
    cultura = request.form.get("cultura", "")
    if nome:
        models.atualizar_cliente_ponto(user_id, ponto_id, nome, cultura if cultura in _culturas_cliente() else "")
    return redirect(url_for("cliente_ponto", ponto_id=ponto_id))


@app.route("/app/pontos/<int:ponto_id>/excluir", methods=["POST"])
@cliente_required
def cliente_ponto_excluir(ponto_id):
    models.excluir_cliente_ponto(int(current_user.id), ponto_id)
    return redirect(url_for("cliente_home"))


# Posicao das estacoes no mapa da regiao do cliente arredondada pra 0,1
# grau (~11 km): mostra onde esta' o risco sem apontar a fazenda.
REGIAO_PRECISAO_GRAUS = 1
REGIAO_ROTULO_MAX_KM = 150  # cidade INMET mais longe que isso nao serve de rotulo


def _rotulo_regiao(lat, lon):
    estacao = inmet_stations.estacao_mais_proxima(lat, lon)
    if estacao and estacao["distancia_km"] <= REGIAO_ROTULO_MAX_KM:
        return f"Região de {estacao['cidade']}/{estacao['uf']}"
    return f"Região {lat:.1f}, {lon:.1f}"


def _regioes_cliente(pontos, raio_km):
    """Estacoes reais ativas (com leitura recente) dentro do raio do plano
    de alguma lavoura do cliente, agrupadas por celula de 0,1 grau -- cada
    regiao leva so' o rotulo da cidade, a posicao arredondada e, por
    doenca, a leitura mais alta das estacoes da celula. Nunca o nome
    (site_name) da fazenda."""
    if not pontos:
        return []
    cards_reais = get_dashboard_data(None, _load_translations())
    ocultas = models.get_deactivated_site_names()
    corte = (_hoje_cuiaba() - timedelta(days=DADOS_BLOQUEIO_DIAS)).isoformat()
    celulas = {}
    for site, (lat, lon) in data_reader.read_site_coordinates().items():
        if site in ocultas:
            continue
        distancia = min(virtual_farms._haversine_km(p["lat"], p["lon"], lat, lon) for p in pontos)
        if distancia > raio_km:
            continue
        cards = [c for c in cards_reais.get(site, []) if c.get("data") and c["data"] >= corte and c.get("concentracao") is not None]
        if not cards:
            continue
        chave = (round(lat, REGIAO_PRECISAO_GRAUS), round(lon, REGIAO_PRECISAO_GRAUS))
        celula = celulas.setdefault(chave, {"distancia_km": distancia, "doencas": {}})
        celula["distancia_km"] = min(celula["distancia_km"], distancia)
        for c in cards:
            atual = celula["doencas"].get(c["doenca_en"])
            if atual is None or c["concentracao"] > atual["concentracao"]:
                celula["doencas"][c["doenca_en"]] = {
                    "doenca": c["doenca"], "concentracao": c["concentracao"], "status": c["status"], "data": c["data"],
                }
    ordem_status = {"Perigo": 0, "Atencao": 1}
    regioes = []
    for (lat, lon), celula in celulas.items():
        doencas = sorted(celula["doencas"].values(), key=lambda d: (ordem_status.get(d["status"], 2), d["doenca"]))
        regioes.append({
            "lat": lat, "lon": lon, "rotulo": _rotulo_regiao(lat, lon),
            "distancia_km": round(celula["distancia_km"]), "doencas": doencas,
            "pior": doencas[0]["status"] if doencas else None,
        })
    regioes.sort(key=lambda r: r["distancia_km"])
    return regioes


@app.route("/app/regiao")
@cliente_required
def cliente_regiao():
    plano = g.cliente_plano
    pontos = models.get_cliente_pontos(int(current_user.id))
    liberado = "regiao" in plano["recursos"]
    return render_template(
        "cliente_regiao.html", plano=plano, pontos=pontos, liberado=liberado,
        regioes=_regioes_cliente(pontos, plano["raio_km"]) if liberado else [],
    )


def _cards_ponto_cliente(ponto, plano):
    """Esporos estimados no ponto (IDW das fazendas reais ativas dentro do
    raio do plano), ja' filtrados pela cultura da lavoura e sem leitura
    velha demais. Devolve (cards, n_estacoes)."""
    chave = f"cliente-ponto:{ponto['id']}"
    cards_reais = get_dashboard_data(None, _load_translations())
    ocultas = models.get_deactivated_site_names()
    coords_reais = {s: c for s, c in data_reader.read_site_coordinates().items() if s not in ocultas}
    cards, vizinhas = virtual_farms.interpolar_cards(ponto["lat"], ponto["lon"], plano["raio_km"], cards_reais, coords_reais)
    corte = (_hoje_cuiaba() - timedelta(days=DADOS_BLOQUEIO_DIAS)).isoformat()
    cards = [c for c in cards if c.get("data") and c["data"] >= corte]
    cultura = ponto["cultura"] or CULTURA_TODOS
    cards = _filter_cards_by_cultura(
        {chave: cards}, {(chave, "app"): {"cultura": cultura}}, models.get_doenca_culturas(), safra="app",
    )[chave]
    return cards, len(vizinhas)


def _weather_ponto_cliente(ponto):
    chave = f"cliente-ponto:{ponto['id']}"
    return _get_weather_for_site(chave, {chave: (ponto["lat"], ponto["lon"])})


def _riscos_ponto_cliente(ponto, weather):
    """Risco de infeccao pelo clima de TODA doenca com germinacao
    cadastrada que vale pra cultura e pais da lavoura -- nao depende de
    haver estacao BioScout perto (e' 100% clima)."""
    info = _disease_info_cached()
    doenca_culturas = models.get_doenca_culturas()
    doenca_paises = models.get_doenca_paises()
    pais = countries.get_country(countries.detectar_pais_por_coordenada(ponto["lat"], ponto["lon"]))["nome"]
    cultura = ponto["cultura"]
    cards = [
        {"doenca_en": en, "doenca": d["nome_pt"]} for en, d in info.items()
        if (not cultura or not doenca_culturas.get(en) or cultura in doenca_culturas[en])
        and (not doenca_paises.get(en) or pais in doenca_paises[en])
    ]
    riscos = _build_riscos_climaticos_sem_leitura(cards)
    for d in riscos:
        d["risco"] = _calc_risco_germinacao(d, weather)
        d["previsao_risco"] = _calc_previsao_risco_germinacao(d, weather)
    ordem = {"vermelho": 0, "amarelo": 1, "verde": 2}
    riscos = [d for d in riscos if d["risco"]]
    riscos.sort(key=lambda d: (ordem.get(d["risco"], 3), d["doenca"]))
    return riscos


def _dados_ponto_cliente(ponto, plano):
    recursos = set(plano["recursos"])
    dados = {"recursos": recursos}
    weather = _weather_ponto_cliente(ponto) if recursos & {"clima", "risco"} else None
    if "clima" in recursos:
        dados["weather"] = weather
    if "risco" in recursos:
        dados["riscos"] = _riscos_ponto_cliente(ponto, weather)
    if recursos & {"esporos", "fungicidas"}:
        cards, n_estacoes = _cards_ponto_cliente(ponto, plano)
        dados["n_estacoes"] = n_estacoes
        if "esporos" in recursos:
            dados["cards"] = cards
        if "fungicidas" in recursos:
            dados["diseases"] = _build_site_diseases(
                f"cliente-ponto:{ponto['id']}", cards, {}, cultura=ponto["cultura"] or None,
            )
    return dados


def _resumo_ponto_cliente(ponto, plano):
    """Linha curta da lista de lavouras (sem buscar clima -- a lista abre
    rapido mesmo com varias lavouras)."""
    if not set(plano["recursos"]) & {"esporos", "fungicidas"}:
        return None
    cards, _n = _cards_ponto_cliente(ponto, plano)
    return {
        "perigo": sum(1 for c in cards if c["status"] == "Perigo"),
        "atencao": sum(1 for c in cards if c["status"] == "Atencao"),
        "total": len(cards),
    }


@app.route("/webhooks/asaas", methods=["POST"])
def asaas_webhook():
    """Eventos do Asaas (pagamento criado/confirmado/vencido/estornado,
    assinatura excluida). Sempre responde 200 pra evento valido -- o Asaas
    pausa a fila inteira de webhooks se o endpoint devolver erro."""
    token = asaas.token_webhook()
    if not token or not secrets.compare_digest(request.headers.get("asaas-access-token", ""), token):
        abort(401)
    evento = request.get_json(silent=True) or {}
    tipo = evento.get("event", "")
    pagamento = evento.get("payment")
    if pagamento:
        assinatura = models.get_assinatura_por_subscription(pagamento.get("subscription") or "")
        if not assinatura:
            ref = pagamento.get("externalReference") or ""
            if ref.startswith("cliente:") and ref[8:].isdigit():
                assinatura = models.get_assinatura(int(ref[8:]))
        if assinatura:
            status = "DELETED" if tipo == "PAYMENT_DELETED" else pagamento.get("status")
            models.registrar_pagamento(
                assinatura["user_id"], pagamento["id"], pagamento.get("value"), status,
                pagamento.get("dueDate"), pagamento.get("invoiceUrl"),
            )
    elif tipo in ("SUBSCRIPTION_DELETED", "SUBSCRIPTION_INACTIVATED"):
        assinatura = models.get_assinatura_por_subscription((evento.get("subscription") or {}).get("id") or "")
        if assinatura:
            models.atualizar_assinatura(assinatura["user_id"], status="cancelada")
    return jsonify({"ok": True})


@app.route("/admin/solicitacoes")
@master_required
def admin_solicitacoes():
    """Gerenciar Clientes (ex-"Clientes e cadastros"; pedido do Alan, 01/11/2026 -- junta as antigas
    "Novos cadastros" e a lista "Clientes do app" de Planos e clientes):
    uma pessoa por cartao, com os dados do cadastro (/solicitar-acesso) e,
    se for cliente do app, plano/situacao/pago ate'/cortesia/lavouras.
    Cliente sem registro de cadastro (criado antes do cadastro direto)
    entra so' com o que a conta tem. Abrir a tela marca os cadastros como
    vistos (zera o contador do menu)."""
    cadastros = models.get_cadastros()
    models.marcar_cadastros_vistos()
    clientes = {c["id"]: c for c in models.get_all_clientes()}
    pessoas = []
    for c in cadastros:
        cliente = clientes.pop(c["user_id"], None)
        pessoas.append({**c, "cliente": cliente, "data": c["criado_em"]})
    for cliente in clientes.values():  # clientes de antes do cadastro direto
        pessoas.append({
            "user_id": cliente["id"], "username": cliente["username"], "nome_completo": "", "email": cliente["email"],
            "telefone": cliente["telefone"], "tipo": "cliente", "perfil": "", "visto": 1, "conta_existe": 1,
            "cliente": cliente, "data": cliente["criado_em"] or "",
        })
    pessoas.sort(key=lambda p: p["data"] or "", reverse=True)
    # Sinal de celular em cada local com coordenada da pessoa: lavouras
    # (cliente do app) e fazendas ja' liberadas pra ela -- so' no Brasil.
    coords = _coords_all()
    paises = models.get_all_site_countries()
    for p in pessoas:
        locais = [(l["nome"], l["lat"], l["lon"]) for l in models.get_cliente_pontos(p["user_id"]) if _no_brasil(l["lat"], l["lon"])]
        locais += [(_nome_exibicao(s), *coords[s]) for s in models.get_user_permitted_site_names(p["user_id"])
                   if s in coords and paises.get(s, countries.DEFAULT_COUNTRY) == countries.DEFAULT_COUNTRY]
        p["sinal_locais"] = [(nome, sinal_celular.estimar(lat, lon, buscar_frequencias=False)) for nome, lat, lon in locais]
        sinal_celular.buscar_frequencias_em_segundo_plano(
            [(lat, lon) for _n, lat, lon in locais if sinal_celular.frequencias_salvas(lat, lon) is None])
    return render_template("admin_solicitacoes.html", pessoas=pessoas, perfis=PERFIS_CADASTRO,
                           planos=models.get_planos(), hoje=_hoje_cuiaba().isoformat())


# Itens do menu Configuracoes (botao ⋮): (chave, rotulo, endpoint, quem ve
# -- chave de ABAS_CONFIG ou "master"). A ORDEM e' escolhida so' pelo Alan
# ("↕ Ordenar abas" no proprio menu; pedido do Alan, 01/11/2026), guardada
# no setting `ordem_menu_config` e vale pro menu de todo mundo (cada um so'
# ve os itens que pode abrir). Aba nova do menu entra aqui tambem.
MENU_CONFIG = [
    ("usuarios", "Usuários", "admin_users", "usuarios"),
    ("doencas", "Doenças", "admin_doencas", "doencas"),
    ("fungicidas", "Fungicidas", "admin_fungicidas", "fungicidas"),
    ("mapa_interpolado", "Mapas de Estudo", "mapa_interpolado", "mapa_interpolado"),
    ("solo", "Mapas de Solo", "solo", "solo"),
    ("ndvi", "Mapas de NDVI", "ndvi", "ndvi"),
    ("envios", "Programação de Envios", "whatsapp_gerenciamento", "envios"),
    ("acessos", "Gerenciar Acessos", "admin_acessos", "master"),
    ("cadastros", "Gerenciar Clientes", "admin_solicitacoes", "master"),
    ("planos", "Planos e Valores", "admin_planos", "master"),
    ("whatsapp", "Conexão WhatsApp", "admin_whatsapp", "master"),
    ("exportar", "Exportar", "admin_exportar", "master"),
    ("ocultas", "Fazendas ocultas", "fazendas_ocultas", "master"),
]
_ORDEM_MENU_KEY = "ordem_menu_config"


def _ordem_menu_config():
    try:
        salvo = json.loads(models.get_setting(_ORDEM_MENU_KEY) or "[]")
    except ValueError:
        salvo = []
    padrao = [m[0] for m in MENU_CONFIG]
    salvo = [c for c in salvo if c in padrao]
    return salvo + [c for c in padrao if c not in salvo]  # item novo vai pro fim


def _menu_config_do_usuario():
    por_chave = {m[0]: m for m in MENU_CONFIG}
    itens = []
    for chave in _ordem_menu_config():
        _c, rotulo, endpoint, quem = por_chave[chave]
        if current_user.is_master if quem == "master" else pode_acessar_aba(quem):
            itens.append({"chave": chave, "rotulo": rotulo, "endpoint": endpoint})
    return itens


@app.context_processor
def _inject_menu_config():
    if not current_user.is_authenticated or current_user.is_cliente:
        return {"menu_config": [], "pode_ordenar_menu": False}
    return {"menu_config": _menu_config_do_usuario(),
            "pode_ordenar_menu": current_user.username == ALAN_MAURO_USERNAME}


@app.route("/admin/menu-config/ordem", methods=["POST"])
@login_required
def salvar_ordem_menu_config():
    if current_user.username != ALAN_MAURO_USERNAME:
        abort(403)
    ordem = (request.get_json(silent=True) or {}).get("ordem") or []
    validas = {m[0] for m in MENU_CONFIG}
    if not isinstance(ordem, list) or not all(isinstance(c, str) and c in validas for c in ordem):
        abort(400)
    models.set_setting(_ORDEM_MENU_KEY, json.dumps(ordem))
    return jsonify({"ok": True, "ordem": _ordem_menu_config()})


@app.context_processor
def _inject_whatsapp_conectado():
    """`whatsapp_conectado()` nos templates: so' consulta o servico quando a
    tela usa (botoes de envio)."""
    return {"whatsapp_conectado": whatsapp.conectado}


_AVISO_WHATSAPP_DESCONECTADO = ("WhatsApp desconectado -- nada foi enviado. Conecte de novo em "
                                "Configurações > Conexão WhatsApp.")


def _whatsapp_desconectado(destino):
    """Envio pedido com o WhatsApp desconectado (botao desativado na tela,
    mas a pagina pode estar aberta de antes): avisa e volta, sem enviar."""
    if whatsapp.conectado():
        return None
    flash(_AVISO_WHATSAPP_DESCONECTADO, "error")
    return redirect(request.referrer or destino)


@app.context_processor
def _inject_cadastros_novos():
    if current_user.is_authenticated and getattr(current_user, "is_master", False):
        return {"solicitacoes_pendentes": models.contar_cadastros_nao_vistos()}
    return {"solicitacoes_pendentes": 0}


@app.route("/admin/planos")
@master_required
def admin_planos():
    return render_template(
        "admin_planos.html", planos=models.get_planos(), recursos=models.RECURSOS_PLANO,
        ciclos=models.CICLOS_PLANO,
        asaas_configurado=asaas.configurado(), asaas_webhook_ok=bool(asaas.token_webhook()),
        asaas_producao=os.environ.get("ASAAS_AMBIENTE", "").lower() == "producao",
        hoje=_hoje_cuiaba().isoformat(),
    )


@app.route("/admin/planos/salvar", methods=["POST"])
@master_required
def admin_planos_salvar():
    """Salva TODOS os planos da tela de uma vez (um botao so'), mais o
    "Novo plano" se o nome dele foi preenchido. Confere tudo antes: com
    algum erro, nada e' gravado."""
    form = request.form
    chaves = [k for k in form.getlist("plano_ids") if k.isdigit()]
    if form.get("nome__novo", "").strip():
        chaves.append("novo")
    planos = []
    for k in chaves:
        nome = form.get(f"nome__{k}", "").strip()
        try:
            valor = float(form.get(f"valor__{k}", "").replace(",", ".") or "x")
            max_pontos = int(form.get(f"max_pontos__{k}", "1"))
            raio_km = float(form.get(f"raio_km__{k}", "50").replace(",", "."))
            ordem = int(form.get(f"ordem__{k}", "0") or 0)
        except ValueError:
            flash(f"Plano \"{nome or 'novo'}\": valor, lavouras, raio e ordem precisam ser números.", "error")
            return redirect(url_for("admin_planos"))
        ciclo = form.get(f"ciclo__{k}")
        if not nome or valor < 0 or max_pontos < 1 or raio_km <= 0 or ciclo not in models.CICLOS_PLANO:
            flash(f"Plano \"{nome or 'novo'}\": preencha nome, valor (0 = plano grátis), ciclo, lavouras e raio.", "error")
            return redirect(url_for("admin_planos"))
        planos.append((None if k == "novo" else int(k), nome, form.get(f"descricao__{k}", "").strip(), valor, ciclo,
                       max_pontos, raio_km, form.getlist(f"recursos__{k}"), bool(form.get(f"ativo__{k}")), ordem))
    for dados in planos:
        models.salvar_plano(*dados)
    flash("Planos salvos.", "success")
    return redirect(url_for("admin_planos"))


@app.route("/admin/planos/<int:plano_id>/excluir", methods=["POST"])
@master_required
def admin_planos_excluir(plano_id):
    if models.excluir_plano(plano_id):
        flash("Plano excluído.", "success")
    else:
        flash("Esse plano tem assinantes -- desative-o em vez de excluir.", "error")
    return redirect(url_for("admin_planos"))


@app.route("/admin/clientes/<int:user_id>/cortesia", methods=["POST"])
@master_required
def admin_cliente_cortesia(user_id):
    """Libera (ou tira, data vazia) acesso sem cobranca ate' uma data --
    teste, parceiro, acerto fora do Asaas."""
    row = models.get_user_by_id(user_id)
    if not row or not row["is_cliente"]:
        abort(404)
    data = _parse_iso_date(request.form.get("cortesia_ate", ""))
    plano_id = request.form.get("plano_id", "")
    campos = {"cortesia_ate": data.isoformat() if data else None}
    if plano_id.isdigit() and models.get_plano(int(plano_id)):
        campos["plano_id"] = int(plano_id)
    models.atualizar_assinatura(user_id, **campos)
    flash(f"Acesso de {row['username']} atualizado.", "success")
    return redirect(url_for("admin_solicitacoes"))


@app.route("/admin/clientes/<int:user_id>/excluir", methods=["POST"])
@master_required
def admin_cliente_excluir(user_id):
    row = models.get_user_by_id(user_id)
    if not row or not row["is_cliente"]:
        abort(404)
    erro = _excluir_cliente(user_id)
    if erro:
        flash(f"Não foi possível cancelar a cobrança no Asaas ({erro}) -- nada foi excluído.", "error")
    else:
        flash(f"Cliente {row['username']} excluído.", "success")
    return redirect(url_for("admin_solicitacoes"))


def _bootstrap_once():
    """Cria as tabelas do banco e inicia o agendador de WhatsApp em
    background. Roda tanto no modo dev (`python app.py`) quanto sob um
    servidor WSGI de producao (gunicorn) -- nesse ultimo caso o bloco
    `__main__` abaixo nunca executa, entao o import do modulo e' o unico
    lugar pra inicializar. IMPORTANTE: gunicorn precisa rodar com 1
    worker so (`-w 1`, ver Procfile) -- com mais de um processo, cada um
    teria seu proprio agendador e mandaria cada relatorio agendado em
    duplicidade."""
    models.init_db()
    _bootstrap_admin_from_env()
    _bootstrap_master_qap()
    threading.Thread(target=_whatsapp_scheduler_loop, daemon=True).start()


def _bootstrap_master_qap():
    """QAP sem nenhum Master (banco recem-criado ou vazio): cria o
    "Alan Mauro" com a senha padrao (`models.DEFAULT_PASSWORD`), pra ele
    conseguir entrar e restaurar o backup da producao (pedido do Alan,
    31/10/2026). Nunca roda fora do QAP nem mexe em senha de quem ja existe."""
    if not AMBIENTE_QAP or models.existe_master() or models.get_user_by_username(models.MASTER_PRINCIPAL):
        return
    models.create_user(models.MASTER_PRINCIPAL, models.DEFAULT_PASSWORD, is_master=True)


def _bootstrap_admin_from_env():
    """Cria o primeiro usuario administrador a partir de
    `ADMIN_BOOTSTRAP_USERNAME`/`ADMIN_BOOTSTRAP_PASSWORD` (variaveis de
    ambiente), se as duas estiverem definidas e esse usuario ainda nao
    existir -- alternativa ao `setup_admin.py` (que pede senha
    interativamente) pra hospedagem onde nao da pra abrir um terminal
    dentro do container. So roda uma vez (usuario ja existente e'
    ignorado); nao apaga nem sobrescreve senha de ninguem."""
    username = os.environ.get("ADMIN_BOOTSTRAP_USERNAME")
    password = os.environ.get("ADMIN_BOOTSTRAP_PASSWORD")
    if not username or not password:
        return
    if models.get_user_by_username(username):
        return
    models.create_user(username, password, is_admin=True)


if __name__ == "__main__":
    models.init_db()
    _bootstrap_admin_from_env()
    _bootstrap_master_qap()
    # so inicia o agendador uma vez (o reloader do Flask sobe o processo 2x em modo debug)
    if os.environ.get("WERKZEUG_RUN_MAIN") == "true" or not app.debug:
        threading.Thread(target=_whatsapp_scheduler_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=5000, debug=True)
else:
    _bootstrap_once()
