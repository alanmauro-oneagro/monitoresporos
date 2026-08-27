"""Clima atual e previsao de chuva via Open-Meteo (https://open-meteo.com/) --
gratuito, sem necessidade de conta ou API key, usando a latitude/longitude de
cada estacao (ja vem no spore_counts.csv, uma por fazenda)."""
import json
import urllib.request
import urllib.parse

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"


def get_weather_forecast(lat, lon):
    """Retorna dict com clima atual e previsao dos proximos 5 dias, ou None
    se a busca falhar (sem internet, coordenada invalida, etc.)."""
    params = {
        "latitude": lat,
        "longitude": lon,
        "current": "temperature_2m,relative_humidity_2m,precipitation",
        "daily": "precipitation_sum,temperature_2m_max,temperature_2m_min",
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

    return {
        "temperatura_atual": current.get("temperature_2m"),
        "umidade_atual": current.get("relative_humidity_2m"),
        "chuva_atual_mm": current.get("precipitation"),
        "previsao_5_dias": previsao[:5],
    }
