"""Clima atual e previsao de chuva via Open-Meteo (https://open-meteo.com/) --
gratuito, sem necessidade de conta ou API key, usando a latitude/longitude de
cada estacao (ja vem no spore_counts.csv, uma por fazenda)."""
import hashlib
import json
import os
import threading
import time
import urllib.request
import urllib.parse
import estacoes_dados
from datetime import datetime, timedelta, timezone
from pathlib import Path

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

# Ultima falha de chamada a Open-Meteo (ou None depois de um sucesso) --
# mostrada no aviso do Painel so' pro admin (ver `app.dashboard`). Sem
# isso toda falha virava "sem clima" em silencio.
ultimo_erro = {"erro": None, "quando": None}


def _abrir_json(url, timeout):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            data = json.load(resp)
    except Exception as exc:
        ultimo_erro["erro"] = str(exc)[:200]
        ultimo_erro["quando"] = datetime.now().strftime("%d/%m %H:%M")
        raise
    ultimo_erro["erro"] = None
    ultimo_erro["quando"] = None
    return data


# ---------------------------------------------------------------------------
# Leituras CADENCIADAS (pedido do usuario, 15/10/2026 -- deixar o site mais
# leve): clima e previsao sao buscados so' as 04:45, 11:45 e 17:45 de Cuiaba
# (horarios trocados a pedido do usuario em 16/10/2026)
# (`HORARIOS_LEITURA`). Cada coordenada/estacao e' buscada no maximo 1x por
# "janela de leitura" (`janela_de_leitura`), numa chamada so' que ja traz
# historico horario dos ultimos 92 dias + previsao de 7 dias. Tudo fica
# gravado em disco (`STORE_PATH`, no mesmo volume persistente dos CSVs, entao
# sobrevive a deploy) e todas as telas leem dali -- clima "atual" sai da hora
# corrente da serie horaria ja guardada, sem chamada nova. A rodada roda no
# agendador (`app._whatsapp_scheduler_loop` -> `atualizar_todos`); coordenada
# que ainda nao foi buscada na janela (fazenda nova, estacao trocada) e'
# buscada sob demanda, uma vez, e entra no mesmo esquema.
# ---------------------------------------------------------------------------
DATA_DIR = Path(os.environ.get("BIOSCOUT_DATA_DIR", str(Path(__file__).parent.parent / "data")))
STORE_PATH = DATA_DIR / "open_meteo_diario.json"
HORARIOS_LEITURA = ((4, 45), (11, 45), (17, 45))  # (hora, minuto) de Cuiaba
DIAS_HISTORICO_GUARDADOS = 400
_HOURLY_CAMPOS = "temperature_2m,relative_humidity_2m,precipitation,wind_direction_10m"
_DAILY_CAMPOS = "precipitation_sum,temperature_2m_max,temperature_2m_min,cloud_cover_mean"

_lock = threading.RLock()
_store = None


def _agora_cuiaba():
    return datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=4)


def janela_de_leitura(agora=None):
    """Identificador da leitura vigente ("YYYY-MM-DD HHh") -- o ultimo
    horario de `HORARIOS_LEITURA` que ja passou (antes das 04:45 ainda vale
    a das 17:45 do dia anterior). Muda 3x por dia; a cada mudanca o
    agendador busca tudo de novo."""
    agora = agora or _agora_cuiaba()
    passados = [(h, m) for h, m in HORARIOS_LEITURA if (h, m) <= (agora.hour, agora.minute)]
    if passados:
        return f"{agora.date().isoformat()} {passados[-1][0]:02d}h{passados[-1][1]:02d}"
    h, m = HORARIOS_LEITURA[-1]
    return f"{(agora - timedelta(days=1)).date().isoformat()} {h:02d}h{m:02d}"


def _carregar():
    global _store
    if _store is None:
        try:
            with open(STORE_PATH, encoding="utf-8") as f:
                _store = json.load(f)
        except (OSError, ValueError):
            _store = {}
        _store.setdefault("pontos", {})
        _store.setdefault("grades", {})
        _store.setdefault("estacoes", {})
        _store.setdefault("previsoes", {})
        _store.setdefault("ultima_rodada", None)
    return _store


def _salvar():
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STORE_PATH.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(_store, f, separators=(",", ":"))
    os.replace(tmp, STORE_PATH)


def _chave(lat, lon):
    return f"{round(float(lat), 4)},{round(float(lon), 4)}"


def _falhou_ha_pouco(registro):
    """Ja falhou NESTA janela de leitura -- so' tenta de novo no proximo
    horario (04:45/11:45/17:45), pra' nao ficar repetindo a chamada
    (pedido do usuario: site mais leve). Enquanto isso valem o backup e a
    ultima leitura guardada."""
    return (registro or {}).get("falha_janela") == janela_de_leitura()


def _ponto(lat, lon, salvar=True):
    """Dados guardados da coordenada, buscando na Open-Meteo so' se ainda
    nao tem a leitura da janela (e nao falhou ha pouco). Em falha devolve o
    que ja tinha (leitura antiga ainda serve -- a previsao cobre 7 dias),
    ou None se nunca teve nada."""
    chave = _chave(lat, lon)
    dia = janela_de_leitura()
    with _lock:
        registro = _carregar()["pontos"].get(chave)
        if registro and (registro.get("dia") == dia or _falhou_ha_pouco(registro)):
            return registro if registro.get("horas") else None
    params = {
        "latitude": lat, "longitude": lon,
        "hourly": _HOURLY_CAMPOS, "daily": _DAILY_CAMPOS,
        "past_days": 92, "forecast_days": 7, "timezone": "auto",
    }
    url = f"{OPEN_METEO_URL}?{urllib.parse.urlencode(params)}"
    try:
        data = _abrir_json(url, timeout=30)
        hourly = data.get("hourly", {})
        colunas = [hourly.get(c) or [] for c in ("temperature_2m", "relative_humidity_2m", "precipitation", "wind_direction_10m")]
        novas = {
            t: [col[i] if i < len(col) else None for col in colunas]
            for i, t in enumerate(hourly.get("time", []))
        }
        daily, utc_offset, fonte = data.get("daily", {}), data.get("utc_offset_seconds", 0), "Open-Meteo"
    except Exception:
        try:
            utc_offset = (registro or {}).get("utc_offset")
            if utc_offset is None:
                utc_offset = _offset_estimado(lat, lon)
            novas, daily = _met_norway(lat, lon, utc_offset)
            fonte = "MET Norway"
        except Exception as exc:
            ultimo_erro["erro"] = f"{ultimo_erro.get('erro') or 'Open-Meteo falhou'}; backup MET Norway: {str(exc)[:120]}"
            ultimo_erro["quando"] = datetime.now().strftime("%d/%m %H:%M")
            with _lock:
                registro = dict(registro or {})
                registro["falha_janela"] = janela_de_leitura()
                _store["pontos"][chave] = registro
            return registro if registro.get("horas") else None

    corte = (datetime.now() - timedelta(days=DIAS_HISTORICO_GUARDADOS)).strftime("%Y-%m-%d")
    with _lock:
        horas = dict((registro or {}).get("horas") or {})
        horas.update(novas)
        horas = {t: v for t, v in horas.items() if t >= corte}
        registro = {"dia": dia, "horas": horas, "daily": daily, "utc_offset": utc_offset, "fonte": fonte}
        _store["pontos"][chave] = registro
        if salvar:
            _salvar()
    return registro


def _estacao(country_code, codigo, uf, salvar=True):
    """Leitura da janela das horas MEDIDAS pela estacao oficial (ver
    `estacoes_dados`) -- mesmo esquema de `_ponto`: no maximo 1 busca por
    janela de leitura, acumulando o historico em disco, backoff em falha.
    Primeira busca traz 90 dias; as seguintes, so' os ultimos 3 (o resto
    ja esta guardado). None se o pais nao tem integracao/credencial."""
    if not estacoes_dados.configurado(country_code):
        return None
    chave = f"{country_code}:{codigo}"
    dia = janela_de_leitura()
    with _lock:
        registro = _carregar()["estacoes"].get(chave)
        if registro and (registro.get("dia") == dia or _falhou_ha_pouco(registro)):
            return registro if registro.get("horas") else None
    hoje = _agora_cuiaba().date()
    inicio = hoje - timedelta(days=3 if (registro or {}).get("horas") else 90)
    try:
        novas, fonte = estacoes_dados.buscar_horas(country_code, codigo, uf, inicio.isoformat(), hoje.isoformat())
    except Exception as exc:
        ultimo_erro["erro"] = f"estacao {chave}: {str(exc)[:180]}"
        ultimo_erro["quando"] = datetime.now().strftime("%d/%m %H:%M")
        with _lock:
            registro = dict(registro or {})
            registro["falha_janela"] = janela_de_leitura()
            _store["estacoes"][chave] = registro
        return registro if registro.get("horas") else None
    corte = (datetime.now() - timedelta(days=DIAS_HISTORICO_GUARDADOS)).strftime("%Y-%m-%d")
    with _lock:
        horas = dict((registro or {}).get("horas") or {})
        horas.update(novas)
        horas = {t: v for t, v in horas.items() if t >= corte}
        registro = {"dia": dia, "horas": horas, "uf": uf, "fonte": fonte}
        _store["estacoes"][chave] = registro
        if salvar:
            _salvar()
    return registro


def fonte_estacao(country_code, codigo):
    """De onde veio a ultima leitura guardada da estacao ("INMET",
    "DMC", "SMN") -- None se ainda nao ha leitura."""
    with _lock:
        return (_carregar()["estacoes"].get(f"{country_code}:{codigo}") or {}).get("fonte")


def horas_estacao_por_dia(country_code, codigo, uf, data_inicio, data_fim):
    """Horas MEDIDAS pela estacao oficial entre as datas (inclusive),
    agrupadas por dia local -- mesmo formato de `get_historico_horario`.
    {} se nao houver integracao/dado."""
    registro = _estacao(country_code, codigo, uf)
    if not registro:
        return {}
    por_dia = {}
    for t in sorted(registro["horas"]):
        v = registro["horas"][t]
        if v[0] is None or not (data_inicio <= t[:10] <= data_fim):
            continue
        por_dia.setdefault(t[:10], []).append({"temp": v[0], "umidade": v[1], "chuva": v[2] or 0, "vento": v[3]})
    return por_dia


def previsao_estacao(country_code, codigo):
    """Previsao do orgao nacional pra' estacao (hoje so' o SMN da
    Argentina, ver `estacoes_dados.previsao_smn`): {"YYYY-MM-DDTHH:00":
    [temp, chuva_mm]} de 3 em 3 horas -- buscada 1x por janela de leitura,
    guardada em disco. {} se o pais nao publica previsao legivel."""
    if country_code != "AR":
        return {}
    chave = f"{country_code}:{codigo}"
    dia = janela_de_leitura()
    with _lock:
        registro = _carregar()["previsoes"].get(chave)
        if registro and (registro.get("dia") == dia or _falhou_ha_pouco(registro)):
            return registro.get("horas") or {}
    try:
        horas = estacoes_dados.previsao_smn(codigo)
    except Exception as exc:
        ultimo_erro["erro"] = f"previsao {chave}: {str(exc)[:180]}"
        ultimo_erro["quando"] = datetime.now().strftime("%d/%m %H:%M")
        with _lock:
            registro = dict(registro or {})
            registro["falha_janela"] = janela_de_leitura()
            _store["previsoes"][chave] = registro
        return registro.get("horas") or {}
    with _lock:
        _store["previsoes"][chave] = {"dia": dia, "horas": horas}
        _salvar()
    return horas


def ultimas_24h_estacao(country_code, codigo, uf):
    """Ultimas 24 horas medidas pela estacao (lista de {"hora", "temp",
    "umidade", "chuva"}, mais antiga primeiro) -- [] se nao houver."""
    registro = _estacao(country_code, codigo, uf)
    if not registro:
        return []
    ultimas = sorted(t for t, v in registro["horas"].items() if v[0] is not None)[-24:]
    return [_hora_dict(t, registro["horas"][t]) for t in ultimas]


MET_NORWAY_URL = "https://api.met.no/weatherapi/locationforecast/2.0/compact"
# A MET Norway exige identificar o aplicativo no User-Agent (termos de uso);
# dados sob CC BY 4.0 -- a fonte aparece como "MET Norway" no rotulo.
MET_NORWAY_USER_AGENT = "OneAgroMonitor/1.0 (+https://oneagroapp.com.br)"


def _offset_estimado(lat, lon):
    """Fuso (segundos) quando ainda nao ha leitura da Open-Meteo pra'
    coordenada: Chile pela mesma regra de verao/inverno usada no resto do
    site; resto da America do Sul pela longitude (UTC-3 a UTC-5)."""
    if lon < -66.5 and lat < -17:
        horas = -3 if datetime.now().month in (9, 10, 11, 12, 1, 2, 3, 4) else -4
    else:
        horas = max(-5, min(-3, round(lon / 15)))
    return horas * 3600


def _met_norway(lat, lon, utc_offset):
    """Backup da previsao quando a Open-Meteo falha (pedido do usuario,
    15/10/2026): Locationforecast 2.0 da MET Norway (gratuita, sem
    chave), mesmo formato de `_ponto` -- ({"YYYY-MM-DDTHH:00" local:
    [temp, umidade, chuva, vento]}, daily no formato da Open-Meteo).
    Hora a hora nos primeiros ~2-3 dias, depois de 6 em 6 h (cada ponto
    de 6 h vira 6 horas iguais, chuva dividida entre elas). So' previsao
    -- nao traz historico."""
    params = urllib.parse.urlencode({"lat": round(float(lat), 4), "lon": round(float(lon), 4)})
    req = urllib.request.Request(f"{MET_NORWAY_URL}?{params}", headers={"User-Agent": MET_NORWAY_USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.load(resp)
    serie = (data.get("properties") or {}).get("timeseries") or []
    if not serie:
        raise RuntimeError("resposta sem previsao")
    offset = timedelta(seconds=utc_offset)
    horas, nuvens_por_dia = {}, {}
    for i, ponto in enumerate(serie):
        dt_utc = datetime.fromisoformat(ponto["time"].replace("Z", "+00:00")).replace(tzinfo=None)
        dados = ponto.get("data") or {}
        instante = (dados.get("instant") or {}).get("details") or {}
        if "next_1_hours" in dados:
            passo, chuva = 1, ((dados["next_1_hours"].get("details") or {}).get("precipitation_amount"))
        else:
            passo = 6 if "next_6_hours" in dados else 1
            chuva = ((dados.get("next_6_hours") or {}).get("details") or {}).get("precipitation_amount")
        for h in range(passo):
            local = dt_utc + offset + timedelta(hours=h)
            horas[local.strftime("%Y-%m-%dT%H:00")] = [
                instante.get("air_temperature"), instante.get("relative_humidity"),
                round(chuva / passo, 2) if chuva is not None else None, instante.get("wind_from_direction"),
            ]
        if instante.get("cloud_area_fraction") is not None:
            nuvens_por_dia.setdefault((dt_utc + offset).date().isoformat(), []).append(instante["cloud_area_fraction"])
    por_dia = {}
    for t, v in horas.items():
        por_dia.setdefault(t[:10], []).append(v)
    dias = sorted(por_dia)
    daily = {
        "time": dias,
        "precipitation_sum": [round(sum(v[2] or 0 for v in por_dia[d]), 1) for d in dias],
        "temperature_2m_max": [max(v[0] for v in por_dia[d] if v[0] is not None) for d in dias],
        "temperature_2m_min": [min(v[0] for v in por_dia[d] if v[0] is not None) for d in dias],
        "cloud_cover_mean": [round(sum(nuvens_por_dia[d]) / len(nuvens_por_dia[d])) if nuvens_por_dia.get(d) else None for d in dias],
    }
    return horas, daily


def _hora_atual_local(registro):
    agora = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(seconds=registro.get("utc_offset", 0))
    return agora.strftime("%Y-%m-%dT%H:00")


def _hora_dict(t, v):
    return {"hora": t, "temp": v[0], "umidade": v[1], "chuva": v[2]}


def rodada_pendente():
    with _lock:
        return _carregar()["ultima_rodada"] != janela_de_leitura()


def atualizar_todos(coordenadas, estacoes=()):
    """Rodada das 04:45/11:45/17:45: busca a leitura da janela de cada
    (lat, lon) e estacao, gravando o arquivo uma vez so' no fim. O que
    falhar fica pra' proxima janela (`_falhou_ha_pouco`) -- a rodada conta
    como feita e o agendador nao repete as chamadas ate' o proximo
    horario."""
    vistas = set()
    for lat, lon in coordenadas:
        chave = _chave(lat, lon)
        if chave in vistas:
            continue
        vistas.add(chave)
        _ponto(lat, lon, salvar=False)
    for country_code, codigo, uf in estacoes:
        if not estacoes_dados.configurado(country_code) or f"{country_code}:{codigo}" in vistas:
            continue
        vistas.add(f"{country_code}:{codigo}")
        _estacao(country_code, codigo, uf, salvar=False)
        previsao_estacao(country_code, codigo)
    # Grades de chuva/nuvens dos mapas ja' pedidas -- busca agora, pra'
    # quem abrir o mapa nao esperar pela Open-Meteo.
    corte = (_agora_cuiaba().date() - timedelta(days=3)).isoformat()
    with _lock:
        # So' grade que alguem abriu nos ultimos 3 dias -- senao uma grade
        # abandonada seria rebuscada 3x por dia pra sempre.
        grades = [
            g for g in _store["grades"].values()
            if g.get("lats") and g.get("dia") != janela_de_leitura() and g.get("pedida_em", "") >= corte
        ]
    for g in grades:
        _grade_diaria(g["tipo"], g["lats"], g["lons"], g["campo_daily"], g["campo_saida"], salvar=False, pela_rodada=True)
    with _lock:
        _store["ultima_rodada"] = janela_de_leitura()
        _salvar()


def get_weather_forecast(lat, lon):
    """Retorna dict com clima atual, previsao dos proximos 5 dias e o
    historico horario das ultimas 24h (temperatura/umidade/chuva -- usado
    pra calibrar o risco de germinacao com base no que realmente aconteceu,
    nao so' a leitura do instante -- ver `app._calc_risco_germinacao`), ou
    None se nao houver dado. Sai da leitura diaria guardada (`_ponto`);
    "atual" e' a hora corrente da serie horaria."""
    registro = _ponto(lat, lon)
    if not registro:
        return None
    horas = registro["horas"]
    hora_atual = _hora_atual_local(registro)
    hoje = hora_atual[:10]
    ordenadas = sorted(horas)
    passadas = [t for t in ordenadas if t <= hora_atual and horas[t][0] is not None]
    futuras = [t for t in ordenadas if t > hora_atual]
    atual = horas[passadas[-1]] if passadas else [None, None, None, None]

    daily = registro.get("daily", {})
    dias = daily.get("time", [])
    chuva = daily.get("precipitation_sum", [])
    tmax = daily.get("temperature_2m_max", [])
    tmin = daily.get("temperature_2m_min", [])
    nuvens = daily.get("cloud_cover_mean", [])
    previsao = []
    for i in range(len(dias)):
        if dias[i] <= hoje:
            continue
        previsao.append({
            "data": dias[i],
            "chuva_mm": chuva[i] if i < len(chuva) else None,
            "temp_max": tmax[i] if i < len(tmax) else None,
            "temp_min": tmin[i] if i < len(tmin) else None,
            "nuvens_pct": nuvens[i] if i < len(nuvens) else None,
        })

    # Previsao horaria por dia -- usada pro risco de germinacao previsto
    # dos proximos dias (`app.calc_risco_diario_pct`).
    futuras_por_dia = {}
    for t in futuras:
        futuras_por_dia.setdefault(t[:10], []).append(_hora_dict(t, horas[t]))

    ultimas_24h = [_hora_dict(t, horas[t]) for t in passadas[-24:]]
    # Soma da chuva de cada uma das ultimas 24h (cada ponto horario ja e' o
    # total precipitado NAQUELA hora) -- acumulado real do dia todo.
    chuva_24h_mm = round(sum(p["chuva"] for p in ultimas_24h if p.get("chuva") is not None), 1) if ultimas_24h else None

    return {
        "temperatura_atual": atual[0],
        "umidade_atual": atual[1],
        "chuva_atual_mm": atual[2],
        "chuva_24h_mm": chuva_24h_mm,
        "previsao_5_dias": previsao[:5],
        "ultimas_24h": ultimas_24h,
        "previsao_horaria_por_dia": futuras_por_dia,
        "fonte_previsao": registro.get("fonte", "Open-Meteo"),
    }


def _media(valores):
    valores = [v for v in valores if v is not None]
    return round(sum(valores) / len(valores), 1) if valores else None


def _media_por_horario(listas_de_pontos, campos):
    """`listas_de_pontos` = uma lista de listas (uma por estacao), cada
    uma com pontos horarios {"hora":, campo1:, campo2:, ...}. Casa por
    `"hora"` (nao por indice -- uma estacao pode ter um ponto a mais/
    menos que outra, ex. uma chamada que demorou um pouco mais e pegou
    uma hora nova) e devolve uma lista so', ordenada, com cada campo
    sendo a media das estacoes que tinham aquele horario."""
    por_hora = {}
    for pontos in listas_de_pontos:
        for p in pontos:
            por_hora.setdefault(p["hora"], []).append(p)
    saida = []
    for hora in sorted(por_hora):
        pontos = por_hora[hora]
        item = {"hora": hora}
        for campo in campos:
            item[campo] = _media([p.get(campo) for p in pontos])
        saida.append(item)
    return saida


def get_weather_forecast_interpolado(coords_list):
    """Chama `get_weather_forecast` pra cada (lat, lon) de `coords_list`
    (as 3 estacoes oficiais mais proximas de uma fazenda, ver
    `app._weather_coords_all`/aba Fazendas > "Interpolacao das 3
    estacoes mais proximas") e MEDIA os campos correspondentes -- uma
    estacao que falhar (rede, coordenada invalida etc.) e' simplesmente
    ignorada, a media sai so' das que responderam; None se NENHUMA
    responder. Mesmo formato de retorno de `get_weather_forecast`, pra
    quem chama (`app._get_weather_for_site`) nao precisar saber a
    diferenca."""
    resultados = [get_weather_forecast(lat, lon) for lat, lon in coords_list]
    resultados = [r for r in resultados if r]
    if not resultados:
        return None

    previsao_5_dias = []
    n_dias = min(len(r["previsao_5_dias"]) for r in resultados)
    for i in range(n_dias):
        dias_i = [r["previsao_5_dias"][i] for r in resultados]
        previsao_5_dias.append({
            "data": dias_i[0]["data"],
            "chuva_mm": _media([d["chuva_mm"] for d in dias_i]),
            "temp_max": _media([d["temp_max"] for d in dias_i]),
            "temp_min": _media([d["temp_min"] for d in dias_i]),
            "nuvens_pct": _media([d["nuvens_pct"] for d in dias_i]),
        })

    ultimas_24h = _media_por_horario([r["ultimas_24h"] for r in resultados], ["temp", "umidade", "chuva"])

    todas_datas = set()
    for r in resultados:
        todas_datas.update(r["previsao_horaria_por_dia"].keys())
    previsao_horaria_por_dia = {
        data: _media_por_horario(
            [r["previsao_horaria_por_dia"].get(data, []) for r in resultados], ["temp", "umidade", "chuva"]
        )
        for data in todas_datas
    }

    return {
        "temperatura_atual": _media([r["temperatura_atual"] for r in resultados]),
        "umidade_atual": _media([r["umidade_atual"] for r in resultados]),
        "chuva_atual_mm": _media([r["chuva_atual_mm"] for r in resultados]),
        "chuva_24h_mm": _media([r["chuva_24h_mm"] for r in resultados]),
        "previsao_5_dias": previsao_5_dias,
        "ultimas_24h": ultimas_24h,
        "previsao_horaria_por_dia": previsao_horaria_por_dia,
    }


def _resultado_vigente(registro):
    """Grade guardada sem os dias que ja passaram -- quando a leitura e' de
    uma janela anterior (falha na atual), o 1o dia dela pode ser ontem."""
    resultado = (registro or {}).get("resultado") or []
    inicio, campo = (registro or {}).get("inicio"), (registro or {}).get("campo_saida")
    if not resultado or not inicio or not campo:
        return resultado
    atraso = (_agora_cuiaba().date() - datetime.fromisoformat(inicio).date()).days
    if atraso <= 0:
        return resultado
    return [{**p, campo: (p.get(campo) or [])[atraso:]} for p in resultado]


# Limite da Open-Meteo gratuita: 600 chamadas/minuto, e cada ponto de uma
# grade em lote conta como uma chamada -- grade maior que isso vai em lotes,
# esperando a janela de 1 minuto liberar entre um e outro.
GRADE_LOTE_PONTOS = 500
_GRADE_LIMITE_MINUTO = 550
_grade_envios = []  # [(timestamp, pontos)] dos ultimos 60s
_grade_envios_lock = threading.Lock()
_grade_busca_locks = {}


def _aguardar_cota_minuto(pontos):
    while True:
        with _grade_envios_lock:
            agora = time.monotonic()
            _grade_envios[:] = [(t, n) for t, n in _grade_envios if agora - t < 60]
            usados = sum(n for _, n in _grade_envios)
            if not _grade_envios or usados + pontos <= _GRADE_LIMITE_MINUTO:
                _grade_envios.append((agora, pontos))
                return
            espera = 60 - (agora - _grade_envios[0][0]) + 0.5
        time.sleep(max(espera, 0.5))


def _buscar_grade_em_lotes(lats, lons, campo_daily):
    resultados = []
    for i in range(0, len(lats), GRADE_LOTE_PONTOS):
        lote_lats, lote_lons = lats[i:i + GRADE_LOTE_PONTOS], lons[i:i + GRADE_LOTE_PONTOS]
        params = {
            "latitude": ",".join(str(v) for v in lote_lats),
            "longitude": ",".join(str(v) for v in lote_lons),
            "daily": campo_daily,
            "forecast_days": 6,
            "timezone": "auto",
        }
        for tentativa in range(3):
            _aguardar_cota_minuto(len(lote_lats))
            try:
                lote = _abrir_json(f"{OPEN_METEO_URL}?{urllib.parse.urlencode(params)}", timeout=45)
                break
            except Exception as exc:
                print(f"[grade] lote {i // GRADE_LOTE_PONTOS + 1} ({len(lote_lats)} pontos) falhou: {exc}", flush=True)
                if tentativa == 2:
                    raise
                time.sleep(65)
        resultados.extend(lote if isinstance(lote, list) else [lote])
    return resultados


GRADE_RETENTATIVA_SEGUNDOS = 180


def _grade_pronta(registro, dia):
    """Leitura da janela ja feita -- ou falhou ha pouco nesta janela. Sem
    nenhum resultado guardado (grade nova), a falha so' segura 3 min:
    senao o mapa ficava vazio ate' o proximo horario de leitura."""
    if not registro:
        return False
    if registro.get("dia") == dia:
        return True
    if not _falhou_ha_pouco(registro):
        return False
    return bool(registro.get("resultado")) or time.time() - registro.get("falha_em", 0) < GRADE_RETENTATIVA_SEGUNDOS


def _grade_diaria(tipo, lats, lons, campo_daily, campo_saida, salvar=True, pela_rodada=False, esperar=True):
    """Grade em lote (camadas de nuvens/chuva dos mapas) -- uma chamada
    Open-Meteo pra todos os pontos, guardada por janela de leitura: a mesma
    grade so' e' buscada 1x por janela (cada ponto da grade conta como uma
    chamada pro limite da Open-Meteo, entao isso pesava muito). A grade
    pedida fica guardada (lats/lons) e a rodada das 04:45/11:45/17:45 ja'
    busca de novo sozinha (`atualizar_todos`) -- quem abre o mapa nao
    espera pela Open-Meteo. Em falha, devolve a ultima grade boa, sem
    aviso na tela (pedido do usuario, 16/10/2026)."""
    assinatura = json.dumps([[round(float(v), 3) for v in lats], [round(float(v), 3) for v in lons]])
    chave = f"{tipo}:{hashlib.sha1(assinatura.encode()).hexdigest()}"
    dia = janela_de_leitura()
    hoje = _agora_cuiaba().date().isoformat()
    with _lock:
        registro = _carregar()["grades"].get(chave)
        if registro and not pela_rodada:
            registro["pedida_em"] = hoje
        if _grade_pronta(registro, dia):
            return _resultado_vigente(registro)
        busca_lock = _grade_busca_locks.setdefault(chave, threading.Lock())
    if not esperar:
        # Pedido de pagina nao fica preso na busca (grade grande leva
        # minutos, em lotes -- com 4 threads no gunicorn, poucos pedidos
        # presos derrubavam o site inteiro): busca em segundo plano e devolve
        # a grade anterior se houver, ou None ("ainda carregando").
        if not busca_lock.locked():
            threading.Thread(
                target=_grade_diaria, args=(tipo, lats, lons, campo_daily, campo_saida), daemon=True
            ).start()
        return _resultado_vigente(registro) if (registro or {}).get("resultado") else None
    # Uma busca por grade de cada vez: quem chega enquanto ela roda (grade
    # grande leva minutos, ver GRADE_LOTE_PONTOS) espera e usa o resultado.
    with busca_lock:
        with _lock:
            registro = _carregar()["grades"].get(chave)
            if _grade_pronta(registro, dia):
                return _resultado_vigente(registro)
        return _buscar_e_guardar_grade(
            chave, registro, tipo, lats, lons, campo_daily, campo_saida, salvar, pela_rodada, dia, hoje
        )


def _buscar_e_guardar_grade(chave, registro, tipo, lats, lons, campo_daily, campo_saida, salvar, pela_rodada, dia, hoje):
    base = {
        "tipo": tipo, "lats": list(lats), "lons": list(lons), "campo_daily": campo_daily, "campo_saida": campo_saida,
        "pedida_em": (registro or {}).get("pedida_em", hoje) if pela_rodada else hoje,
    }
    try:
        resultados = _buscar_grade_em_lotes(list(lats), list(lons), campo_daily)
    except Exception:
        with _lock:
            registro = {**(registro or {}), **base, "falha_janela": dia, "falha_em": time.time()}
            _store["grades"][chave] = registro
        return _resultado_vigente(registro)
    saida = []
    for r in resultados:
        daily = r.get("daily", {})
        saida.append({
            "lat": r.get("latitude"),
            "lon": r.get("longitude"),
            campo_saida: daily.get(campo_daily, [])[:6],
        })
    inicio = ((resultados[0].get("daily") or {}).get("time") or [None])[0] if resultados else None
    corte = (_agora_cuiaba().date() - timedelta(days=3)).isoformat()
    with _lock:
        # Grade que ninguem abre ha 3 dias sai do arquivo (e da rodada).
        grades = {k: v for k, v in _store["grades"].items() if v.get("pedida_em", "") >= corte}
        grades[chave] = {**base, "dia": dia, "resultado": saida, "inicio": inicio}
        _store["grades"] = grades
        if salvar:
            _salvar()
    return saida


def get_cloud_forecast_grid(lats, lons):
    """Cobertura de nuvens (6 dias) pra uma grade de pontos -- camada de
    nuvens do Mapa Interpolado (ver `_grade_diaria`)."""
    return _grade_diaria("nuvens", lats, lons, "cloud_cover_mean", "nuvens_pct_por_dia")


def get_rain_forecast_grid(lats, lons, esperar=True):
    """Chuva acumulada (mm, 6 dias) pra uma grade de pontos -- camada de
    chuva do Mapa/Mapa Interpolado (ver `_grade_diaria`). Com
    `esperar=False`, None enquanto a busca roda em segundo plano."""
    return _grade_diaria("chuva", lats, lons, "precipitation_sum", "chuva_mm_por_dia", esperar=esperar)


def _horas_observadas_por_dia(lat, lon, data_inicio, data_fim):
    registro = _ponto(lat, lon)
    if not registro:
        return {}
    hora_atual = _hora_atual_local(registro)
    por_dia = {}
    for t in sorted(registro["horas"]):
        v = registro["horas"][t]
        if t > hora_atual or v[0] is None or not (data_inicio <= t[:10] <= data_fim):
            continue
        por_dia.setdefault(t[:10], []).append(v)
    return por_dia


def get_historico_por_dia(lat, lon, data_inicio, data_fim):
    """Chuva (mm) e direcao de vento (graus) hora a hora, observados,
    entre `data_inicio` e `data_fim` ("YYYY-MM-DD", inclusive), agrupado
    por dia local -- formato {"chuva", "vento"} de
    `app._completar_clima_horario`, pra reusar
    `data_reader.contar_direcoes_vento` (ver `app._chuva_vento_ultimos_30_dias`).
    Sai da leitura diaria guardada (`_ponto`). {} se nao houver dado."""
    return {
        dia: [{"chuva": v[2], "vento": v[3]} for v in horas]
        for dia, horas in _horas_observadas_por_dia(lat, lon, data_inicio, data_fim).items()
    }


def get_historico_horario(lat, lon, data_inicio, data_fim):
    """Temperatura, umidade, chuva e direcao de vento hora a hora,
    observados, entre `data_inicio` e `data_fim` ("YYYY-MM-DD", inclusive),
    agrupado por dia local -- mesmo formato de
    `app._completar_clima_horario`. Usado pelo Risco de
    Infeccao quando o device BioScout nao tem clima pro dia. Sai da
    leitura diaria guardada (`_ponto`, historico acumulado de ate'
    `DIAS_HISTORICO_GUARDADOS` dias). {} se nao houver dado."""
    return {
        dia: [{"temp": v[0], "umidade": v[1], "chuva": v[2] or 0, "vento": v[3]} for v in horas]
        for dia, horas in _horas_observadas_por_dia(lat, lon, data_inicio, data_fim).items()
    }
