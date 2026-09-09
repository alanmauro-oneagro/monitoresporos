"""Clima atual e previsao de chuva via Open-Meteo (https://open-meteo.com/) --
gratuito, sem necessidade de conta ou API key, usando a latitude/longitude de
cada estacao (ja vem no spore_counts.csv, uma por fazenda)."""
import json
import urllib.request
import urllib.parse
from datetime import datetime

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"


def get_weather_forecast(lat, lon):
    """Retorna dict com clima atual, previsao dos proximos 5 dias e o
    historico horario das ultimas 24h (temperatura/umidade/chuva -- usado
    pra calibrar o risco de germinacao com base no que realmente aconteceu,
    nao so' a leitura do instante -- ver `app._calc_risco_germinacao`), ou
    None se a busca falhar (sem internet, coordenada invalida, etc.).
    `past_days=1` e' um parametro real da Open-Meteo que devolve o horario
    observado do dia anterior junto com a previsao, sem custo/chave extra."""
    params = {
        "latitude": lat,
        "longitude": lon,
        "current": "temperature_2m,relative_humidity_2m,precipitation",
        "hourly": "temperature_2m,relative_humidity_2m,precipitation",
        "daily": "precipitation_sum,temperature_2m_max,temperature_2m_min,cloud_cover_mean",
        "past_days": 1,
        "forecast_days": 6,
        "timezone": "auto",
    }
    url = f"{OPEN_METEO_URL}?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            data = json.load(resp)
    except Exception:
        return None

    current = data.get("current", {})
    daily = data.get("daily", {})
    dias = daily.get("time", [])
    chuva = daily.get("precipitation_sum", [])
    tmax = daily.get("temperature_2m_max", [])
    tmin = daily.get("temperature_2m_min", [])
    nuvens = daily.get("cloud_cover_mean", [])

    previsao = []
    for i in range(1, len(dias)):  # indice 0 e' hoje, ja coberto por "atual"
        previsao.append({
            "data": dias[i],
            "chuva_mm": chuva[i] if i < len(chuva) else None,
            "temp_max": tmax[i] if i < len(tmax) else None,
            "temp_min": tmin[i] if i < len(tmin) else None,
            "nuvens_pct": nuvens[i] if i < len(nuvens) else None,
        })

    hourly = data.get("hourly", {})
    horas = hourly.get("time", [])
    horas_temp = hourly.get("temperature_2m", [])
    horas_umidade = hourly.get("relative_humidity_2m", [])
    horas_chuva = hourly.get("precipitation", [])
    agora = datetime.fromisoformat(current["time"]) if current.get("time") else datetime.now()
    passadas = []
    futuras_por_dia = {}
    for i in range(len(horas)):
        dt = datetime.fromisoformat(horas[i])
        ponto = {
            "hora": horas[i],
            "temp": horas_temp[i] if i < len(horas_temp) else None,
            "umidade": horas_umidade[i] if i < len(horas_umidade) else None,
            "chuva": horas_chuva[i] if i < len(horas_chuva) else None,
        }
        if dt <= agora:
            passadas.append(ponto)
        else:
            # Ja vem da mesma chamada (forecast_days=6), sem custo/chave
            # extra -- so' nao era guardado antes. Usado pro risco de
            # germinacao previsto dos proximos dias (mesma regra do
            # historico, ver `app.calc_risco_diario_pct` / `data_reader`),
            # em vez de so' descartar essa parte da resposta.
            futuras_por_dia.setdefault(dt.date().isoformat(), []).append(ponto)

    ultimas_24h = passadas[-24:]
    # Soma da chuva de cada uma das ultimas 24h (cada ponto horario ja e' o
    # total precipitado NAQUELA hora, ver doc da Open-Meteo) -- diferente
    # de `chuva_atual_mm` (so' a hora corrente/instantanea), isso da' o
    # acumulado real do dia todo, mais util pra avaliar risco de doenca.
    chuva_24h_mm = round(sum(p["chuva"] for p in ultimas_24h if p.get("chuva") is not None), 1) if ultimas_24h else None

    return {
        "temperatura_atual": current.get("temperature_2m"),
        "umidade_atual": current.get("relative_humidity_2m"),
        "chuva_atual_mm": current.get("precipitation"),
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


def get_cloud_forecast_grid(lats, lons):
    """Versao em lote, so' com cobertura de nuvens -- usada pela camada de
    nuvens do Mapa Interpolado, que cobre uma grade de pontos espalhada
    pelo Brasil inteiro. Um UNICO pedido pra Open-Meteo com todas as
    coordenadas juntas (a API aceita listas separadas por virgula e devolve
    uma lista de resultados, na mesma ordem), em vez de uma chamada por
    ponto da grade -- testado com 200 pontos numa chamada so', ~1.7s."""
    params = {
        "latitude": ",".join(str(v) for v in lats),
        "longitude": ",".join(str(v) for v in lons),
        "daily": "cloud_cover_mean",
        "forecast_days": 6,
        "timezone": "auto",
    }
    url = f"{OPEN_METEO_URL}?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(url, timeout=45) as resp:
            resultados = json.load(resp)
    except Exception:
        return []
    if not isinstance(resultados, list):
        resultados = [resultados]
    saida = []
    for r in resultados:
        daily = r.get("daily", {})
        saida.append({
            "lat": r.get("latitude"),
            "lon": r.get("longitude"),
            "nuvens_pct_por_dia": daily.get("cloud_cover_mean", [])[:6],
        })
    return saida


def get_historico_por_dia(lat, lon, data_inicio, data_fim):
    """Chuva (mm) e direcao de vento (graus) hora a hora, de verdade (nao
    previsao), entre `data_inicio` e `data_fim` (inclusive, "YYYY-MM-DD"),
    agrupado por dia local -- MESMO FORMATO do retorno de
    `data_reader.build_hourly_weather_lookup` (uma lista de
    {"chuva", "vento"} por dia), so' que pra uma coordenada qualquer, nao
    um device do BioScout, pra poder reusar `data_reader.contar_direcoes_vento`
    sem duplicar logica de bucket/media circular. Usado quando a fazenda
    nao tem device proprio (fazenda virtual/estimada, ver
    `app._chuva_vento_ultimos_30_dias`) -- endpoint DIFERENTE do usado por
    `get_weather_forecast` (esse aqui e' o arquivo historico da
    Open-Meteo, sem o limite de "so' 1 dia pra tras" da previsao).
    Retorna {} se a busca falhar (sem internet, coordenada invalida,
    etc.) -- o chamador trata como "sem dado dessa estacao", nao erro."""
    params = {
        "latitude": lat, "longitude": lon,
        "start_date": data_inicio, "end_date": data_fim,
        "hourly": "precipitation,wind_direction_10m",
        "timezone": "auto",
    }
    url = f"{ARCHIVE_URL}?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(url, timeout=20) as resp:
            data = json.load(resp)
    except Exception:
        return {}
    hourly = data.get("hourly", {})
    horas = hourly.get("time", [])
    chuva = hourly.get("precipitation", [])
    vento = hourly.get("wind_direction_10m", [])
    por_dia = {}
    for i, hora_iso in enumerate(horas):
        dia = hora_iso[:10]  # "YYYY-MM-DDTHH:MM" -> "YYYY-MM-DD"
        por_dia.setdefault(dia, []).append({
            "chuva": chuva[i] if i < len(chuva) else None,
            "vento": vento[i] if i < len(vento) else None,
        })
    return por_dia
