"""Busca dados recentes do BioScout (sites + contagem de esporos + clima)
em Python puro -- versao do `Fetch-BioScoutData.ps1 -SkipExtras` (pula
spraylogs/relatorios de site, que so o Build-Report/Refresh-Dashboard
locais usam) que roda em qualquer lugar, sem precisar de Windows/PowerShell.
Usado pelo site hospedado (Railway/Linux), onde o script original nao pode
rodar -- ver `_run_fetch_in_background` em `app.py`.

Fazenda com historico completo desde `FULL_HISTORY_SINCE` ja salvo busca
so' uma janela deslizante dos ultimos `LOOKBACK_DAYS` dias -- rapido, uma
chamada por site em vez de meses de historico, suficiente pra manter o
Painel/Graficos em dia. Fazenda sem esse historico completo (nunca
sincronizada, OU so' recebeu uma sincronizacao PARCIAL alguma vez -- ver
`_earliest_reading_by_site`/`fetch_recent`) busca o HISTORICO COMPLETO
desde `FULL_HISTORY_SINCE`, senao ela ficaria com buraco permanente nos
graficos pra qualquer data anterior a essa sincronizacao parcial (bug
real, ja visto com fazenda do Chile -- ver conversa de 2026-09-06). O
merge por chave (`_merge_csv`) so
atualiza ou adiciona linha, nunca remove -- se uma estacao nao aparecer na
janela (sem leitura nova), a ultima leitura que ja tinha no CSV fica
exatamente como estava, nunca "some".

So usa `urllib` (biblioteca padrao), sem dependencia nova."""
import csv
import json
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

API_BASE = "https://rest.bioscout.com.au"
# Pedido explicito do usuario (06/09/2026): garantir pelo menos 30 dias
# de janela mesmo pra fazenda ja' conhecida -- 16 dias era curto demais
# pra dar margem de sobra em caso de atraso na sincronizacao (o site
# hospedado so' busca quando alguem visita o Painel/Manejo com dado
# velho, ver `_maybe_auto_refresh` em app.py; ninguem visitando por mais
# de 16 dias corridos criava risco de buraco permanente de novo).
LOOKBACK_DAYS = 30
# Mesmo `-SinceDate` padrao do Fetch-BioScoutData.ps1 -- usado so' pra
# fazenda sem historico completo ainda (ver `_earliest_reading_by_site`/
# `fetch_recent`), pra backfill de historico completo dela.
FULL_HISTORY_SINCE = datetime(2025, 10, 1)


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


def _earliest_reading_by_site(spore_counts_path):
    """siteId (string) -> data (datetime) da leitura mais ANTIGA ja salva
    em spore_counts.csv -- usado por `fetch_recent` pra saber quem
    precisa de backfill de historico completo. Nao basta checar so' "a
    fazenda ja apareceu alguma vez" (site pode ter recebido so' uma
    sincronizacao PARCIAL antes -- ex.: uma busca de janela recente que
    rodou entre a fazenda comecar a ser sincronizada e o backfill de
    historico completo ainda nao existir/nao ter rodado -- ficando com
    so' alguns dias salvos pra sempre, mesmo com esse backfill existindo
    agora); comparar a data mais antiga contra `FULL_HISTORY_SINCE` pega
    esse caso tambem, nao so' o de fazenda 100% nunca vista."""
    earliest = {}
    if not spore_counts_path.exists():
        return earliest
    with open(spore_counts_path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            site_id = row.get("siteId")
            ts = row.get("samplingStartTime")
            if not site_id or not ts:
                continue
            try:
                dt = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ")
            except ValueError:
                continue
            site_id = str(site_id)
            if site_id not in earliest or dt < earliest[site_id]:
                earliest[site_id] = dt
    return earliest


def fetch_recent(data_dir, username, password, lookback_days=LOOKBACK_DAYS, log=print):
    """Busca sites + spore_counts + weather. Fazenda com historico
    completo desde `FULL_HISTORY_SINCE` ja salvo so' busca a janela
    recente (`lookback_days`) -- rapido, e o merge por chave preserva
    qualquer leitura mais antiga ja salva, mesmo que a estacao nao
    apareca nessa janela. Fazenda cuja leitura mais antiga salva e' mais
    recente que `FULL_HISTORY_SINCE` (nunca vista antes, OU so' recebeu
    uma sincronizacao PARCIAL alguma vez -- ver `_earliest_reading_by_site`)
    busca o HISTORICO COMPLETO desde `FULL_HISTORY_SINCE` em vez da
    janela recente -- senao ela fica faltando PRA SEMPRE nos graficos
    pra qualquer data anterior a essa sincronizacao parcial, mesmo com o
    merge rodando toda vez (`lookback_days` sozinho nunca olha pra tras
    disso). Caso real (2026-09-06): fazenda do Chile ficou marcada como
    "ja conhecida" so' por ter recebido uma janela recente antes do
    backfill completo existir, e nunca mais ganhou o historico anterior
    ate essa checagem virar "data mais antiga", nao so' "existe alguma
    linha"."""
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    spore_counts_path = data_dir / "spore_counts.csv"

    # Data mais antiga ja salva por site, ANTES dessa busca -- capturado
    # logo no inicio (antes de qualquer escrita), pra decidir daqui a
    # pouco quem precisa de backfill de historico completo.
    earliest_by_site = _earliest_reading_by_site(spore_counts_path)
    # Margem de alguns dias -- a API pode nao ter leitura EXATAMENTE no
    # primeiro dia certo (fazenda instalada uns dias depois do inicio do
    # mes, fim de semana sem leitura etc.), entao so' conta como
    # "precisa de backfill" quem esta bem mais recente que o esperado,
    # nao qualquer diferenca de 1-2 dias.
    backfill_grace = timedelta(days=5)

    log("Autenticando...")
    token = _get_auth_token(username, password)
    headers = {"Authorization": f"Bearer {token}"}
    log("OK.")

    # Sincroniza TODO site que aparecer nessa conta BioScout -- ate' 2026-09
    # so' entravam os que comecavam com "OneAgro" (convencao dos primeiros
    # clientes, todos no Brasil); com a expansao pra America do Sul,
    # fazenda nova (ex.: "Pencahue CYT", "SZ Seeds", clientes do Chile)
    # nao segue mais esse padrao de nome e ficava de fora, silenciosamente
    # (nem aparecia em log de erro -- so' nunca sincronizava). Filtrar por
    # nome nunca foi o jeito certo de saber "e' nosso" -- e' so' o
    # conteudo dessa conta mesmo, entao usa todos.
    all_sites = _http_json(f"{API_BASE}/api/Site/get?SiteRole=2&SiteRole=3&SiteRole=5&SiteRole=6", headers=headers)
    sites = all_sites
    with open(data_dir / "sites.csv", "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["siteId", "siteName"])
        writer.writeheader()
        for s in sites:
            writer.writerow({"siteId": s.get("siteId"), "siteName": s.get("siteName")})
    site_ids = [str(s.get("siteId")) for s in sites]
    log(f"Sites sincronizados: {len(site_ids)}")

    end = datetime.now()
    recent_start = end - timedelta(days=lookback_days)
    new_site_ids = [
        sid for sid in site_ids
        if sid not in earliest_by_site or earliest_by_site[sid] > FULL_HISTORY_SINCE + backfill_grace
    ]
    if new_site_ids:
        log(f"Fazenda(s) sem historico completo -- buscando desde {FULL_HISTORY_SINCE.date()}: {new_site_ids}")

    # Contagem de esporos: uma chamada com a janela recente (todo mundo
    # que a gente ja conhece) e, se houver fazenda nova, MAIS uma chamada
    # separada com o historico completo so' pra ela -- assim ela nao fica
    # faltando dado antigo pra sempre so' porque comecou a ser
    # sincronizada hoje.
    janelas = [(recent_start, end, [sid for sid in site_ids if sid not in new_site_ids])]
    if new_site_ids:
        janelas.append((FULL_HISTORY_SINCE, end, new_site_ids))
    for start, window_end, ids in janelas:
        if not ids:
            continue
        from_iso = start.strftime("%Y-%m-%dT%H:%M:%SZ")
        to_iso = window_end.strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            qs = "&".join(f"SiteIds={sid}" for sid in ids)
            url = f"{API_BASE}/api/service-subscriptions/counts?From={from_iso}&To={to_iso}&{qs}"
            counts = _http_json(url, headers=headers)
            _merge_csv(counts, spore_counts_path, ["tapeScanId", "particulateId"])
            log(f"  contagem de esporos ({from_iso[:10]} -> {to_iso[:10]}): {len(counts)} registros")
        except Exception as exc:
            log(f"  erro contagem de esporos: {exc}")

    weather_rows = []
    for site_id in site_ids:
        start = FULL_HISTORY_SINCE if site_id in new_site_ids else recent_start
        from_iso = start.strftime("%Y-%m-%dT%H:%M:%SZ")
        to_iso = end.strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            w = _http_json(
                f"{API_BASE}/api/Weather/readings/sites?SiteId={site_id}&StartDate={from_iso}&EndDate={to_iso}",
                headers=headers,
            )
            if w:
                weather_rows.extend(w)
        except Exception as exc:
            log(f"  erro clima site {site_id}: {exc}")
    _merge_csv(weather_rows, data_dir / "weather.csv", ["deviceId", "dateMeasured"])
    log(f"  clima: {len(weather_rows)} registros")

    log(f"Concluido. Dados em {data_dir}")
