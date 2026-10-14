"""Clima atual e previsao de chuva via Open-Meteo (https://open-meteo.com/) --
gratuito, sem necessidade de conta ou API key, usando a latitude/longitude de
cada estacao (ja vem no spore_counts.csv, uma por fazenda)."""
import hashlib
import json
import os
import threading
import urllib.request
import urllib.parse
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
# Leitura UNICA por dia (pedido do usuario, 14/10/2026 -- reduzir o uso da
# Open-Meteo): cada coordenada e' buscada no maximo 1x por "dia de leitura"
# (que vira as 01:00 de Cuiaba, `HORA_LEITURA`) numa chamada so' que ja traz
# historico horario dos ultimos 92 dias + previsao de 7 dias. Tudo fica
# gravado em disco (`STORE_PATH`, no mesmo volume persistente dos CSVs, entao
# sobrevive a deploy) e todas as telas leem dali -- clima "atual" sai da hora
# corrente da serie horaria ja guardada, sem chamada nova. A rodada das 01:00
# roda no agendador (`app._whatsapp_scheduler_loop` -> `atualizar_todos`);
# coordenada que ainda nao foi buscada no dia (fazenda nova, estacao trocada)
# e' buscada sob demanda, uma vez, e entra no mesmo esquema.
# ---------------------------------------------------------------------------
DATA_DIR = Path(os.environ.get("BIOSCOUT_DATA_DIR", str(Path(__file__).parent.parent / "data")))
STORE_PATH = DATA_DIR / "open_meteo_diario.json"
HORA_LEITURA = 1  # 01:00, horario de Cuiaba
DIAS_HISTORICO_GUARDADOS = 400
FALHA_BACKOFF = timedelta(minutes=30)
_HOURLY_CAMPOS = "temperature_2m,relative_humidity_2m,precipitation,wind_direction_10m"
_DAILY_CAMPOS = "precipitation_sum,temperature_2m_max,temperature_2m_min,cloud_cover_mean"

_lock = threading.RLock()
_store = None


def _agora_cuiaba():
    return datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=4)


def dia_de_leitura(agora=None):
    """Data (ISO) da leitura vigente -- vira as 01:00 de Cuiaba, nao a
    meia-noite (de 00:00 a 00:59 ainda vale a leitura do dia anterior)."""
    return ((agora or _agora_cuiaba()) - timedelta(hours=HORA_LEITURA)).date().isoformat()


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
    falha = (registro or {}).get("falha_em")
    return bool(falha) and _agora_cuiaba() - datetime.fromisoformat(falha) < FALHA_BACKOFF


def _ponto(lat, lon, salvar=True):
    """Dados guardados da coordenada, buscando na Open-Meteo so' se ainda
    nao tem a leitura do dia (e nao falhou ha pouco). Em falha devolve o
    que ja tinha (leitura antiga ainda serve -- a previsao cobre 7 dias),
    ou None se nunca teve nada."""
    chave = _chave(lat, lon)
    dia = dia_de_leitura()
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
    except Exception:
        with _lock:
            registro = dict(registro or {})
            registro["falha_em"] = _agora_cuiaba().isoformat(timespec="seconds")
            _store["pontos"][chave] = registro
        return registro if registro.get("horas") else None

    hourly = data.get("hourly", {})
    colunas = [hourly.get(c) or [] for c in ("temperature_2m", "relative_humidity_2m", "precipitation", "wind_direction_10m")]
    novas = {
        t: [col[i] if i < len(col) else None for col in colunas]
        for i, t in enumerate(hourly.get("time", []))
    }
    corte = (datetime.now() - timedelta(days=DIAS_HISTORICO_GUARDADOS)).strftime("%Y-%m-%d")
    with _lock:
        horas = dict((registro or {}).get("horas") or {})
        horas.update(novas)
        horas = {t: v for t, v in horas.items() if t >= corte}
        registro = {
            "dia": dia, "horas": horas, "daily": data.get("daily", {}),
            "utc_offset": data.get("utc_offset_seconds", 0),
        }
        _store["pontos"][chave] = registro
        if salvar:
            _salvar()
    return registro


def _hora_atual_local(registro):
    agora = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(seconds=registro.get("utc_offset", 0))
    return agora.strftime("%Y-%m-%dT%H:00")


def _hora_dict(t, v):
    return {"hora": t, "temp": v[0], "umidade": v[1], "chuva": v[2]}


def rodada_pendente():
    with _lock:
        return _carregar()["ultima_rodada"] != dia_de_leitura()


def atualizar_todos(coordenadas):
    """Rodada diaria (01:00): garante a leitura do dia de cada (lat, lon),
    gravando o arquivo uma vez so' no fim. So' marca a rodada como feita
    se TODAS as coordenadas ficaram com a leitura do dia -- senao o
    agendador chama de novo a cada minuto e `_ponto` retenta cada falha
    depois de `FALHA_BACKOFF` (antes uma falha na rodada so' era retentada
    na madrugada seguinte)."""
    dia = dia_de_leitura()
    vistas = set()
    completa = True
    for lat, lon in coordenadas:
        chave = _chave(lat, lon)
        if chave in vistas:
            continue
        vistas.add(chave)
        registro = _ponto(lat, lon, salvar=False)
        if not registro or registro.get("dia") != dia:
            completa = False
    with _lock:
        if completa:
            _store["ultima_rodada"] = dia
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


def _grade_diaria(tipo, lats, lons, campo_daily, campo_saida):
    """Grade em lote (camadas de nuvens/chuva dos mapas) -- uma chamada
    Open-Meteo pra todos os pontos, guardada por dia de leitura: a mesma
    grade so' e' buscada 1x por dia (cada ponto da grade conta como uma
    chamada pro limite da Open-Meteo, entao isso pesava muito)."""
    assinatura = json.dumps([[round(float(v), 3) for v in lats], [round(float(v), 3) for v in lons]])
    chave = f"{tipo}:{hashlib.sha1(assinatura.encode()).hexdigest()}"
    dia = dia_de_leitura()
    with _lock:
        registro = _carregar()["grades"].get(chave)
        if registro and (registro.get("dia") == dia or _falhou_ha_pouco(registro)):
            return registro.get("resultado") or []
    params = {
        "latitude": ",".join(str(v) for v in lats),
        "longitude": ",".join(str(v) for v in lons),
        "daily": campo_daily,
        "forecast_days": 6,
        "timezone": "auto",
    }
    url = f"{OPEN_METEO_URL}?{urllib.parse.urlencode(params)}"
    try:
        resultados = _abrir_json(url, timeout=45)
    except Exception:
        with _lock:
            registro = dict(registro or {})
            registro["falha_em"] = _agora_cuiaba().isoformat(timespec="seconds")
            _store["grades"][chave] = registro
        return registro.get("resultado") or []
    if not isinstance(resultados, list):
        resultados = [resultados]
    saida = []
    for r in resultados:
        daily = r.get("daily", {})
        saida.append({
            "lat": r.get("latitude"),
            "lon": r.get("longitude"),
            campo_saida: daily.get(campo_daily, [])[:6],
        })
    with _lock:
        grades = {k: v for k, v in _store["grades"].items() if v.get("dia") == dia}
        grades[chave] = {"dia": dia, "resultado": saida}
        _store["grades"] = grades
        _salvar()
    return saida


def get_cloud_forecast_grid(lats, lons):
    """Cobertura de nuvens (6 dias) pra uma grade de pontos -- camada de
    nuvens do Mapa Interpolado (ver `_grade_diaria`)."""
    return _grade_diaria("nuvens", lats, lons, "cloud_cover_mean", "nuvens_pct_por_dia")


def get_rain_forecast_grid(lats, lons):
    """Chuva acumulada (mm, 6 dias) pra uma grade de pontos -- camada de
    chuva do Mapa/Mapa Interpolado (ver `_grade_diaria`)."""
    return _grade_diaria("chuva", lats, lons, "precipitation_sum", "chuva_mm_por_dia")


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
    `data_reader.build_hourly_weather_lookup`, pra reusar
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
    `data_reader.build_hourly_weather_lookup`. Usado pelo Risco de
    Infeccao quando o device BioScout nao tem clima pro dia. Sai da
    leitura diaria guardada (`_ponto`, historico acumulado de ate'
    `DIAS_HISTORICO_GUARDADOS` dias). {} se nao houver dado."""
    return {
        dia: [{"temp": v[0], "umidade": v[1], "chuva": v[2] or 0, "vento": v[3]} for v in horas]
        for dia, horas in _horas_observadas_por_dia(lat, lon, data_inicio, data_fim).items()
    }
