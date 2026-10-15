"""Chuva MEDIDA pelos pluviometros do CEMADEN (Brasil) -- substitui a chuva
estimada da Open-Meteo nas fazendas brasileiras com pluviometro perto
(pedido do usuario, 15/10/2026: o INMET so' libera dado automatico por
Acordo de Cooperacao Tecnica).

Usa os servicos PUBLICOS do Mapa Interativo do CEMADEN, sem cadastro:
  - `ESTACOES_URL`: todas as estacoes com lat/lon e o acumulado das
    ultimas 24h (JSONP `estacoes([...])` apesar do Content-Type JSON);
  - `HORARIO_URL`: serie hora a hora das ultimas 48h de uma estacao
    (`horarios` = rotulos, `acumulados` = [dia][hora], so' um dia tem
    valor em cada hora; o ultimo indice e' a hora corrente).
Formato conferido no cliente aberto github.com/helvecioneto/amaview-dados
(scripts/cemaden.mjs) -- o ambiente de desenvolvimento nao alcanca o
CEMADEN, por isso a tela de diagnostico mostra a resposta real."""
import json
import re
import urllib.request
from datetime import datetime, timedelta, timezone

import inmet_stations

ESTACOES_URL = "https://resources.cemaden.gov.br/dados/311_24.json"
HORARIO_URL = "https://mapservices.cemaden.gov.br/MapaInterativoWS/resources/horario/{id}/47"
RAIO_KM = 20  # chuva e' muito local: pluviometro mais longe que isso nao representa a fazenda
_USER_AGENT = "OneAgroMonitor/1.0 (+https://monitoresporos-production.up.railway.app)"


def _baixar(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _milimetros(valor):
    """mm ou None -- campo vazio NAO e' chuva zero, e negativo e' marcador
    de sensor sem dado."""
    if valor is None:
        return None
    texto = str(valor).strip().replace(",", ".")
    if not texto:
        return None
    try:
        v = float(texto)
    except ValueError:
        return None
    return v if v >= 0 else None


def ler_estacoes(texto):
    cru = re.sub(r"^\s*[A-Za-z_$][\w$]*\s*\(", "", texto)
    cru = re.sub(r"\)\s*;?\s*$", "", cru)
    grupos = json.loads(cru)
    grupo = grupos[0] if isinstance(grupos, list) else grupos
    if not isinstance(grupo, dict) or "estacao" not in grupo:
        raise ValueError("resposta do CEMADEN sem o campo 'estacao'")
    estacoes = []
    for e in grupo["estacao"]:
        try:
            lat, lon, id_ = float(e["latitude"]), float(e["longitude"]), int(e["idestacao"])
        except (KeyError, TypeError, ValueError):
            continue
        estacoes.append({
            "id": id_,
            "codigo": str(e.get("codestacao") or ""),
            "nome": str(e.get("nomeestacao") or "").strip(),
            "cidade": str(e.get("cidade") or "").strip(),
            "uf": str(e.get("uf") or ""),
            "lat": lat,
            "lon": lon,
            "acumulado_24h": _milimetros(e.get("acumulado")),
        })
    return estacoes


def buscar_estacoes():
    return ler_estacoes(_baixar(ESTACOES_URL, timeout=60))


def mais_proxima(estacoes, lat, lon, max_km=RAIO_KM):
    melhor = None
    for e in estacoes:
        d = inmet_stations._haversine_km(lat, lon, e["lat"], e["lon"])
        if d <= max_km and (melhor is None or d < melhor["distancia_km"]):
            melhor = dict(e, distancia_km=round(d, 1))
    return melhor


def ler_horario(texto, ancora_utc):
    """{"YYYY-MM-DDTHH:00" UTC: mm} -- `ancora_utc` e' o inicio da hora
    corrente (ultimo indice da serie, ainda incompleta -- fica de fora).
    Cada hora vai rotulada pelo FIM dela, mesma convencao da Open-Meteo
    (chuva das 13h = o que caiu entre 12h e 13h). Hora sem medicao fica
    de fora (nao vira chuva zero)."""
    j = json.loads(texto)
    n = len(j.get("horarios") or [])
    serie = [None] * n
    for dia in j.get("acumulados") or []:
        if not isinstance(dia, list):
            continue
        for k in range(min(n, len(dia))):
            v = _milimetros(dia[k])
            if v is not None:
                serie[k] = v
    return {
        (ancora_utc - timedelta(hours=n - 2 - k)).strftime("%Y-%m-%dT%H:00"): mm
        for k, mm in enumerate(serie[:-1]) if mm is not None
    }


def chuva_horaria(id_estacao):
    """Chuva medida hora a hora (ultimas ~48h) na estacao, em UTC."""
    ancora = datetime.now(timezone.utc).replace(tzinfo=None, minute=0, second=0, microsecond=0)
    return ler_horario(_baixar(HORARIO_URL.format(id=id_estacao)), ancora)
