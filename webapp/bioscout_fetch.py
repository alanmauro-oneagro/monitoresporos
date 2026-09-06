"""Busca dados recentes do BioScout (sites + contagem de esporos + clima)
em Python puro -- versao do `Fetch-BioScoutData.ps1 -SkipExtras` (pula
spraylogs/relatorios de site, que so o Build-Report/Refresh-Dashboard
locais usam) que roda em qualquer lugar, sem precisar de Windows/PowerShell.
Usado pelo site hospedado (Railway/Linux), onde o script original nao pode
rodar -- ver `_run_fetch_in_background` em `app.py`.

Fazenda ja conhecida (pelo menos uma leitura salva antes) busca so' uma
janela deslizante dos ultimos `LOOKBACK_DAYS` dias -- rapido, uma chamada
por site em vez de meses de historico, suficiente pra manter o Painel/
Graficos em dia. Fazenda NUNCA sincronizada antes busca o HISTORICO
COMPLETO desde `FULL_HISTORY_SINCE` (ver `_known_site_ids`/`fetch_recent`),
senao ela ficaria com buraco permanente nos graficos pra qualquer data
anterior a' primeira sincronizacao (bug real, ja visto com fazenda do
Chile -- ver conversa de 2026-09-06). O merge por chave (`_merge_csv`) so
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
# fazenda NUNCA sincronizada antes (ver `_known_site_ids`/`fetch_recent`),
# pra backfill de historico completo dela.
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


def _known_site_ids(spore_counts_path):
    """siteId (string) de qualquer fazenda que ja tenha pelo menos uma
    linha salva em spore_counts.csv -- usado por `fetch_recent` pra saber
    quem so' precisa da janela recente (`lookback_days`) e quem precisa
    de historico completo (fazenda nunca vista ainda, ver comentario
    la')."""
    known = set()
    if not spore_counts_path.exists():
        return known
    with open(spore_counts_path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            site_id = row.get("siteId")
            if site_id:
                known.add(str(site_id))
    return known


def fetch_recent(data_dir, username, password, lookback_days=LOOKBACK_DAYS, log=print):
    """Busca sites + spore_counts + weather. Fazenda que a gente ja
    sincronizou antes so' busca a janela recente (`lookback_days`) --
    rapido, e o merge por chave preserva qualquer leitura mais antiga ja
    salva, mesmo que a estacao nao apareca nessa janela. Fazenda NUNCA
    vista antes (nova no BioScout, ou so' passou a aparecer agora -- ver
    correcao de 2026-09 sobre o filtro por nome que escondia site fora do
    padrao "OneAgro") busca o HISTORICO COMPLETO desde `FULL_HISTORY_SINCE`
    em vez da janela recente -- senao ela so' ganharia dado a partir de
    agora, e todo o historico anterior que o BioScout tem de verdade (so'
    nunca foi buscado) ficaria faltando PRA SEMPRE nos graficos, mesmo com
    o merge rodando toda vez (`lookback_days` sozinho nunca olha pra tras
    disso)."""
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    spore_counts_path = data_dir / "spore_counts.csv"

    # Quem a gente ja tinha pelo menos uma leitura salva ANTES dessa
    # busca -- capturado logo no inicio (antes de qualquer escrita), pra
    # decidir daqui a pouco quem e' "fazenda nova" (nunca vista) e
    # precisa de historico completo.
    known_site_ids = _known_site_ids(spore_counts_path)

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
    new_site_ids = [sid for sid in site_ids if sid not in known_site_ids]
    if new_site_ids:
        log(f"Fazenda(s) nunca sincronizada(s) antes -- buscando historico completo desde {FULL_HISTORY_SINCE.date()}: {new_site_ids}")

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
