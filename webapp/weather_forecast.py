"""Clima atual e previsao de chuva via Open-Meteo (https://open-meteo.com/) --
gratuito, sem necessidade de conta ou API key, usando a latitude/longitude de
cada estacao (ja vem no spore_counts.csv, uma por fazenda)."""
import json
import urllib.request
import urllib.parse
from datetime import datetime

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"


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
        "daily": "precipitation_sum,temperature_2m_max,temperature_2m_min",
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

    previsao = []
    for i in range(1, len(dias)):  # indice 0 e' hoje, ja coberto por "atual"
        previsao.append({
            "data": dias[i],
            "chuva_mm": chuva[i] if i < len(chuva) else None,
            "temp_max": tmax[i] if i < len(tmax) else None,
            "temp_min": tmin[i] if i < len(tmin) else None,
        })

    hourly = data.get("hourly", {})
    horas = hourly.get("time", [])
    horas_temp = hourly.get("temperature_2m", [])
    horas_umidade = hourly.get("relative_humidity_2m", [])
    horas_chuva = hourly.get("precipitation", [])
    agora = datetime.fromisoformat(current["time"]) if current.get("time") else datetime.now()
    passadas = [
        {
            "hora": horas[i],
            "temp": horas_temp[i] if i < len(horas_temp) else None,
            "umidade": horas_umidade[i] if i < len(horas_umidade) else None,
            "chuva": horas_chuva[i] if i < len(horas_chuva) else None,
        }
        for i in range(len(horas))
        if datetime.fromisoformat(horas[i]) <= agora
    ]

    return {
        "temperatura_atual": current.get("temperature_2m"),
        "umidade_atual": current.get("relative_humidity_2m"),
        "chuva_atual_mm": current.get("precipitation"),
        "previsao_5_dias": previsao[:5],
        "ultimas_24h": passadas[-24:],
    }
