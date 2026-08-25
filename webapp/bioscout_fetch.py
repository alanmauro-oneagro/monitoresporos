"""Busca incremental de dados do BioScout (sites + contagem de esporos +
clima) em Python puro -- equivalente do `Fetch-BioScoutData.ps1 -SkipExtras`
(pula spraylogs/relatorios de site, que so o Build-Report/Refresh-Dashboard
locais usam) que roda em qualquer lugar, sem precisar de Windows/PowerShell.
Usado pelo site hospedado (Railway/Linux), onde o script original nao pode
rodar -- ver `_run_fetch_in_background` em `app.py`.

So usa `urllib` (biblioteca padrao), sem dependencia nova."""
import csv
import json
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

API_BASE = "https://rest.bioscout.com.au"
SINCE_DATE = "2025-10-01"


def _http_json(url, headers=None, method="GET", body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _find_jwt(obj):
    """Mesma busca "achar um JWT em qualquer lugar da resposta" do
    Find-JwtToken do PS -- a API muda o formato da resposta de login de
    vez em quando, e isso sobrevive a essas mudancas."""
    if obj is None:
        return None
    if isinstance(obj, str):
        return obj if len(obj.split(".")) == 3 else None
    if isinstance(obj, list):
        for item in obj:
            found = _find_jwt(item)
            if found:
                return found
        return None
    if isinstance(obj, dict):
        for value in obj.values():
            found = _find_jwt(value)
            if found:
                return found
    return None


def _get_auth_token(username, password):
    resp = _http_json(f"{API_BASE}/api/Auth/login", method="POST", body={"UserName": username, "Password": password})
    token = _find_jwt(resp)
    if not token:
        raise RuntimeError("Nao foi possivel extrair o token de autenticacao da resposta de login.")
    return token


def _row_key(row, key_props):
    vals = []
    for k in key_props:
        v = row.get(k)
        if v is None or v == "":
            return json.dumps(row, sort_keys=True, default=str)
        vals.append(str(v))
    return "|".join(vals)


def _merge_csv(new_rows, path, key_props):
    """Mesma logica do Merge-Csv do PS: le o CSV existente (se tiver),
    monta um mapa por `key_props`, e sobrescreve/adiciona com as linhas
    novas -- nunca perde historico ja salvo."""
    if not new_rows:
        return
    existing = []
    if path.exists():
        with open(path, encoding="utf-8-sig", newline="") as f:
            existing = list(csv.DictReader(f))

    fieldnames = []
    for row in existing + new_rows:
        for k in row:
            if k not in fieldnames:
                fieldnames.append(k)

    merged = {}
    for row in existing:
        merged[_row_key(row, key_props)] = row
    for row in new_rows:
        merged[_row_key(row, key_props)] = {k: ("" if v is None else v) for k, v in row.items()}

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in merged.values():
            writer.writerow({k: row.get(k, "") for k in fieldnames})


def _add_months(dt, n):
    month = dt.month - 1 + n
    year = dt.year + month // 12
    month = month % 12 + 1
    return dt.replace(year=year, month=month)


def fetch_incremental(data_dir, username, password, log=print):
    """Busca sites + spore_counts + weather, incremental (so refaz o mes
    atual + meses novos desde o ultimo `lastCompletedMonth`). Grava em
    `data_dir` (sites.csv, spore_counts.csv, weather.csv, sync_state.json)."""
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    state_path = data_dir / "sync_state.json"

    log("Autenticando...")
    token = _get_auth_token(username, password)
    headers = {"Authorization": f"Bearer {token}"}
    log("OK.")

    all_sites = _http_json(f"{API_BASE}/api/Site/get?SiteRole=2&SiteRole=3&SiteRole=5&SiteRole=6", headers=headers)
    sites = [s for s in all_sites if str(s.get("siteName") or "").startswith("OneAgro")]
    with open(data_dir / "sites.csv", "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["siteId", "siteName"])
        writer.writeheader()
        for s in sites:
            writer.writerow({"siteId": s.get("siteId"), "siteName": s.get("siteName")})
    site_ids = [s.get("siteId") for s in sites]
    log(f"Sites OneAgro/Brasil: {len(site_ids)} (de {len(all_sites)} totais na conta)")

    state = {"lastCompletedMonth": None}
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))

    start = datetime.strptime(SINCE_DATE, "%Y-%m-%d")
    end = datetime.now()
    months = []
    cursor = start.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    while cursor <= end:
        months.append(cursor)
        cursor = _add_months(cursor, 1)

    start_idx = 0
    if state.get("lastCompletedMonth"):
        last_completed = datetime.strptime(state["lastCompletedMonth"], "%Y-%m-%d")
        for i, month in enumerate(months):
            if month > last_completed:
                start_idx = i
                break
            start_idx = i + 1
    months_to_fetch = months[start_idx:]
    log(f"Meses a buscar: {len(months_to_fetch)}")

    for month_start in months_to_fetch:
        month_end = min(_add_months(month_start, 1), end)
        from_iso = month_start.strftime("%Y-%m-%dT%H:%M:%SZ")
        to_iso = month_end.strftime("%Y-%m-%dT%H:%M:%SZ")
        log(f"Mes {month_start.strftime('%Y-%m')} ({from_iso} -> {to_iso})")

        try:
            qs = "&".join(f"SiteIds={sid}" for sid in site_ids)
            url = f"{API_BASE}/api/service-subscriptions/counts?From={from_iso}&To={to_iso}&{qs}"
            counts = _http_json(url, headers=headers)
            _merge_csv(counts, data_dir / "spore_counts.csv", ["tapeScanId", "particulateId"])
            log(f"  contagem de esporos: {len(counts)} registros")
        except Exception as exc:
            log(f"  erro contagem de esporos: {exc}")

        month_weather = []
        for site_id in site_ids:
            try:
                w = _http_json(
                    f"{API_BASE}/api/Weather/readings/sites?SiteId={site_id}&StartDate={from_iso}&EndDate={to_iso}",
                    headers=headers,
                )
                if w:
                    month_weather.extend(w)
            except Exception as exc:
                log(f"  erro clima site {site_id}: {exc}")
        _merge_csv(month_weather, data_dir / "weather.csv", ["deviceId", "dateMeasured"])
        log(f"  clima: {len(month_weather)} registros")

        if month_end < end:
            state["lastCompletedMonth"] = month_start.strftime("%Y-%m-%d")
            state_path.write_text(json.dumps(state), encoding="utf-8")

    log(f"Concluido. Dados em {data_dir}")
