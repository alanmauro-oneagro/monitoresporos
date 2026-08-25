"""BioScout Web -- painel movel com login e permissoes por fazenda.

Le os mesmos CSVs que o Fetch-BioScoutData.ps1 mantem -- o botao "Atualizar
dados" do painel dispara esse script em segundo plano (busca na API do
BioScout, que e' lenta -- varios minutos por causa da latencia da propria
API do BioScout, chamada site a site). O clique NAO espera o fim da busca
(evita matar o processo no meio e corromper os CSVs, algo que ja aconteceu
antes nesse projeto); a pagina volta na hora avisando que a busca comecou,
e os dados novos aparecem quando a pessoa atualizar a pagina de novo.
"""
import functools
import os
import re
import sqlite3
import subprocess
import threading
import time
from pathlib import Path

from flask import Flask, render_template, request, redirect, url_for, flash, abort, send_file
from flask_login import (
    LoginManager, UserMixin, login_user, logout_user, login_required,
    current_user,
)
from werkzeug.security import check_password_hash

from datetime import datetime

import models
import fungicida_data
import data_reader
import bioscout_fetch
import export_excel
import whatsapp
import weather_forecast
import inmet_stations
import virtual_farms
from data_reader import read_sites, get_dashboard_data

WEEKDAY_LABELS = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sab", "Dom"]  # 0=Segunda ... 6=Domingo (Python date.weekday())
WHATSAPP_SEND_HOUR = 7  # hora do dia (0-23) em que o envio automatico roda
WEATHER_CACHE_TTL_SECONDS = 30 * 60  # nao busca de novo na Open-Meteo antes disso, por fazenda
DADOS_AVISO_DIAS = 7  # ate isso = verde (ok); acima = aviso (amarelo)
DADOS_BLOQUEIO_DIAS = 15  # acima disso (16+ dias) = vermelho, bloqueia envio/copia da recomendacao


def _dias_sem_leitura(cards):
    """Dias desde a leitura mais recente entre TODOS os cartoes da fazenda
    (nao so os que estao em Atencao/Perigo) -- usado pra saber se a
    estacao fisica parou de mandar dado novo pra API do BioScout,
    independente do status de cada doenca no momento."""
    if not cards:
        return None
    ultima = max(datetime.strptime(c["data"], "%Y-%m-%d").date() for c in cards)
    return (datetime.now().date() - ultima).days


def _nivel_dados_defasados(dias_sem_leitura):
    """None (ok), 'atencao' (mostra aviso) ou 'bloqueado' (recomendacao
    bloqueada) de acordo com `dias_sem_leitura`."""
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
    cards_reais_todos = get_dashboard_data(None, translations)
    coords_reais = data_reader.read_site_coordinates()
    cards, _ = virtual_farms.interpolar_cards(vf["lat"], vf["lon"], vf["raio_km"], cards_reais_todos, coords_reais)
    return cards


_weather_cache = {}  # site_name -> (timestamp, dados)


def _get_weather_for_site(site, coords):
    """Cache simples em memoria por fazenda -- evita bater na Open-Meteo a
    cada carregamento da pagina de Recomendacoes. Alem do clima em si,
    marca de onde veio (`fonte`) e a cidade da estacao oficial do INMET
    mais proxima (`cidade`/`uf`, so como referencia geografica -- o valor
    numerico continua sendo o da Open-Meteo, ver `inmet_stations.py`)."""
    latlon = coords.get(site)
    if not latlon:
        return None
    cached = _weather_cache.get(site)
    now = time.time()
    if cached and now - cached[0] < WEATHER_CACHE_TTL_SECONDS:
        return cached[1]
    data = weather_forecast.get_weather_forecast(*latlon)
    if data:
        estacao = inmet_stations.estacao_mais_proxima(*latlon)
        data["cidade"] = estacao["cidade"] if estacao else None
        data["uf"] = estacao["uf"] if estacao else None
        data["fonte"] = "Open-Meteo"
        _weather_cache[site] = (now, data)
    return data

def _weather_coords_all():
    """Coordenada usada pra buscar a previsao (Open-Meteo) de cada site --
    normalmente a mesma da fazenda (`_coords_all`), mas usa a estacao
    INMET escolhida manualmente na aba Fazendas quando houver
    (`weather_station_overrides`), pra deixar a pessoa optar por uma
    referencia mais representativa do clima da regiao dela."""
    coords = _coords_all()
    overrides = models.get_all_weather_station_overrides()
    if not overrides:
        return coords
    estacoes_by_codigo = {e["codigo"]: e for e in inmet_stations.get_estacoes()}
    resultado = dict(coords)
    for site, codigo in overrides.items():
        estacao = estacoes_by_codigo.get(codigo)
        if estacao and site in resultado:
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


@app.after_request
def _no_cache(response):
    """Sem isso, o botao 'voltar' do navegador depois de 'Sair' mostra a
    pagina anterior direto do cache local, sem pedir login de novo."""
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response


@app.context_processor
def _inject_nav_safras():
    """`models.SAFRAS` disponivel em toda pagina que estende base.html --
    o menu (link "Manejo {safra}" por safra) precisa disso sem cada rota
    ter que passar explicitamente."""
    return {"nav_safras": models.SAFRAS}

FETCH_SCRIPT = Path(__file__).parent.parent / "Fetch-BioScoutData.ps1"
POWERSHELL_EXE = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
SPORE_CSV = data_reader.DATA_DIR / "spore_counts.csv"
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
            bioscout_fetch.fetch_recent(
                data_reader.DATA_DIR, BIOSCOUT_USERNAME, BIOSCOUT_PASSWORD, log=lambda msg: None
            )
            error = None
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
    try:
        return time.time() - SPORE_CSV.stat().st_mtime
    except FileNotFoundError:
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
    preenchido quando o BioScout mandar) e devolve o mapa atual."""
    try:
        models.ensure_disease_translations(
            data_reader.read_unique_display_names(), data_reader.DOENCA_MAP, data_reader.read_scientific_names()
        )
    except FileNotFoundError:
        pass
    return models.get_all_disease_translations()


def _filter_cards_by_cultura(cards_by_site, culturas_by_site, doenca_culturas, safra=None):
    """Se a fazenda tiver uma cultura atual definida numa safra (aba
    Fazendas/Recomendacoes), mostra so os cartoes de doencas marcadas para
    essa cultura na matriz da aba Doencas (`doenca_culturas`) -- doenca sem
    nenhuma marcacao na matriz ainda NUNCA e' escondida, pra nao sumir um
    alerta novo/desconhecido por engano.

    Com `safra` definido (Recomendacoes Safra / 2a Safra), usa so a
    cultura daquela safra. Sem `safra` (Painel, que nao e' por safra),
    usa a uniao das culturas de todas as safras da fazenda -- fazenda com
    Soja na Safra e Milho na 2a Safra mostra as doencas das duas."""
    filtered = {}
    for site, cards in cards_by_site.items():
        if safra:
            culturas_ativas = {culturas_by_site.get((site, safra), {}).get("cultura")}
        else:
            culturas_ativas = {
                culturas_by_site.get((site, s), {}).get("cultura") for s, _ in models.SAFRAS
            }
        culturas_ativas.discard(None)
        culturas_ativas.discard("")
        if not culturas_ativas:
            filtered[site] = cards
            continue
        filtered[site] = [
            c for c in cards
            if not doenca_culturas.get(c["doenca_en"]) or doenca_culturas[c["doenca_en"]] & culturas_ativas
        ]
    return filtered


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


def _apply_fungicida_overrides(doenca, tipo, itens, overrides):
    """Aplica edicoes feitas pelo admin (tela Fungicidas) sobre a lista
    padrao de ingredientes ativos daquela doenca -- itens sem edicao saem
    exatamente como em fungicida_data.py -- e depois reordena conforme os
    botoes "mover pra cima/baixo" (models.get_fungicida_ordem), para que o
    admin possa colocar os melhores tratamentos da regiao no topo (so os 3
    primeiros aparecem nas abas Recomendacoes)."""
    if not itens:
        return itens
    content = []
    for idx, item in enumerate(itens):
        override = overrides.get((doenca, tipo, idx))
        if override and override["removido"]:
            content.append(None)  # historico de remocoes antigas, se houver
        elif override:
            content.append({"ingrediente": override["ingrediente"], "classe": override["classe"]})
        else:
            content.append(item)
    order = models.get_fungicida_ordem(doenca, tipo, len(itens))
    return [content[i] for i in order if content[i] is not None]


def _build_recomendacao_grupo(doenca, tipo, grupo, overrides):
    """Monta {fonte, fonte_url, itens} de um grupo (quimico/biologico) de uma
    doenca, ja com as edicoes/remocoes do admin aplicadas."""
    if not grupo:
        return None
    itens = _apply_fungicida_overrides(doenca, tipo, grupo["itens"], overrides)
    return {"fonte": grupo["fonte"], "fonte_url": grupo["fonte_url"], "itens": itens}


def _build_site_diseases(site, cards, notes, fungicida_overrides=None):
    """A partir dos cartoes (todas as doencas) de uma fazenda, monta a lista
    das que estao em Atencao/Perigo com a recomendacao de fungicida (quando
    houver) e a anotacao manual salva -- usado pela tela de Recomendacoes,
    pelo envio manual de WhatsApp e pelo envio agendado."""
    if fungicida_overrides is None:
        fungicida_overrides = models.get_all_fungicida_overrides()
    diseases = []
    for card in cards:
        if card["status"] not in ("Perigo", "Atencao"):
            continue
        doenca_en = card["doenca_en"]
        rec = fungicida_data.get_recomendacao(doenca_en)
        quimicos = _build_recomendacao_grupo(doenca_en, "quimico", rec["quimicos"], fungicida_overrides) if rec else None
        biologicos = _build_recomendacao_grupo(doenca_en, "biologico", rec["biologicos"], fungicida_overrides) if rec else None
        diseases.append({
            "doenca": card["doenca"],
            "status": card["status"],
            "concentracao": card["concentracao"],
            "data": card["data"],
            "rotulo": card["doenca"],
            "quimicos": quimicos,
            "biologicos": biologicos,
            "classe_label": fungicida_data.CLASSE_LABEL,
            "nota": notes.get((site, card["doenca"]), "") or "",
        })
    diseases.sort(key=lambda d: (0 if d["status"] == "Perigo" else 1, d["doenca"]))
    return diseases


def _fmt_ingrediente(item, classe_label):
    if item.get("classe"):
        return f"{item['ingrediente']} ({classe_label.get(item['classe'], item['classe'])})"
    return item["ingrediente"]


_WHATSAPP_SEPARADOR = "━" * 15


def _whatsapp_titulo(site, is_virtual=False):
    """'OneAgro - Grupo PIVA' -> '*GRUPO PIVA - OneAgro , powered by
    BioScout*' -- nome da fazenda em destaque, sem repetir 'OneAgro' duas
    vezes. Fazenda virtual/estimada usa o padrao '"{nome}" - OneAgro' (ver
    `models.create_virtual_farm`) -- sem nenhuma marca extra no titulo, o
    padrao de nome diferente e' a unica diferenca visivel."""
    if is_virtual:
        nome_fazenda = site.split('"')[1] if site.count('"') >= 2 else site
    else:
        nome_fazenda = site.split(" - ", 1)[1] if " - " in site else site
    return f"*{nome_fazenda.upper()} - OneAgro , powered by BioScout*"


def _format_whatsapp_message(site, diseases, weather=None, produtos=None, is_virtual=False, cultura=None):
    """Monta o relatorio inteiro de uma fazenda numa unica mensagem de
    texto, agrupado POR DOENCA (status, contagem, recomendacao e
    observacao juntos num bloco so, em vez de secoes separadas repetindo
    o nome da doenca) -- mais facil do cliente ler e agir. `weather` e' o
    retorno de `_get_weather_for_site` (ou None); `produtos` e'
    {"quimico": [...], "biologico": [...]} com os itens que a fazenda ja
    tem comprado (aba Recomendacoes > Produtos Fazenda). Sem nenhuma
    doenca em Atencao/Perigo, a mensagem e' so o aviso de que esta tudo
    tranquilo (mesmo texto usado na tela de Recomendacoes). As caixas de
    Observacao (por doenca) e Produtos Fazenda sempre aparecem, mesmo
    vazias -- nesse caso o valor e' so "*", pra manter o mesmo formato de
    relatorio sempre (nao muda de estrutura conforme o que foi
    preenchido). `is_virtual` (fazenda virtual/estimada, ver
    `virtual_farms.py`) so muda o jeito de extrair o nome do site_name pro
    titulo (`_whatsapp_titulo`) -- a mensagem em si nao tem nenhum aviso
    de que o valor e' estimado, pro relatorio ficar igual ao de uma
    fazenda real. `cultura` (nome da cultura atual definida na aba Manejo,
    ou None/"" se ainda estiver "(vazio)") aparece em destaque (negrito,
    maiuscula), entre o clima e o Resumo. A data/hora de atualizacao (e a
    cidade da estacao de referencia, se houver) fica no rodape do
    relatorio, nao mais logo abaixo do titulo. Ordem do cabecalho: titulo
    -> Clima agora (uma linha em branco abaixo do nome da fazenda) ->
    Cultura -> Resumo (sempre antes da primeira doenca)."""
    rodape_data = f"Atualizado em {datetime.now().strftime('%d/%m/%y %H:%M')}"
    if weather and weather.get("cidade"):
        rodape_data += f" · {weather['cidade']}/{weather['uf']}"

    if not diseases:
        partes = [_whatsapp_titulo(site, is_virtual), ""]
        if cultura:
            partes.append(f"Cultura: *{cultura.upper()}*")
            partes.append("")
        partes.append("Nenhuma doenca em Atencao ou Perigo nessa fazenda no momento.")
        partes.append("")
        partes.append(rodape_data)
        return "\n".join(partes)

    n_perigo = sum(1 for d in diseases if d["status"] == "Perigo")
    n_atencao = sum(1 for d in diseases if d["status"] == "Atencao")
    partes_resumo = []
    if n_perigo:
        partes_resumo.append(f"{n_perigo} em PERIGO")
    if n_atencao:
        partes_resumo.append(f"{n_atencao} em ATENCAO")

    lines = [_whatsapp_titulo(site, is_virtual), ""]

    if weather:
        partes = []
        if weather.get("temperatura_atual") is not None:
            partes.append(f"🌡️ {weather['temperatura_atual']}°C")
        if weather.get("umidade_atual") is not None:
            partes.append(f"💧 {weather['umidade_atual']}%")
        if weather.get("chuva_atual_mm") is not None:
            partes.append(f"🌧️ {weather['chuva_atual_mm']} mm")
        if partes:
            lines.append("🌤️ *Clima agora:* " + " · ".join(partes))
        else:
            lines.append("🌤️ *Clima agora*")
        if weather.get("previsao_3_dias"):
            prev = " | ".join(
                # dd/mm, mesmo padrao de data do resto do relatorio (nao o
                # AAAA-MM-DD cru que vem do forecast).
                f"{d['data'][8:10]}/{d['data'][5:7]}: {d['chuva_mm']}mm ({d['temp_min']}-{d['temp_max']}°C)"
                for d in weather["previsao_3_dias"]
            )
            lines.append(f"Previsao: {prev}")
        lines.append("")

    if cultura:
        lines.append(f"Cultura: *{cultura.upper()}*")
        lines.append("")
    lines.append(f"📋 *Resumo:* {', '.join(partes_resumo)}")
    lines.append("")

    lines.append(_WHATSAPP_SEPARADOR)
    lines.append("")

    for d in diseases:
        emoji_status = "🔴" if d["status"] == "Perigo" else "🟡"
        lines.append(
            f"{emoji_status} {d['status'].upper()} — *{d['rotulo'].upper()}* - "
            f"Contagem: {d['concentracao']} esporos/m³"
        )
        biologicos_itens = d["biologicos"]["itens"][:3] if d["biologicos"] else None
        quimicos_itens = d["quimicos"]["itens"][:3] if d["quimicos"] else None
        if biologicos_itens:
            ativos = " // ".join(_fmt_ingrediente(p, d["classe_label"]) for p in biologicos_itens)
            lines.append(f"🧪 *Biologicos:* {ativos}")
        if quimicos_itens:
            ativos = " // ".join(_fmt_ingrediente(p, d["classe_label"]) for p in quimicos_itens)
            lines.append(f"⚗️ *Quimicos:* {ativos}")
        lines.append(f"📝 Obs.: {d.get('nota') or '*'}")
        lines.append("")

    lines.append(_WHATSAPP_SEPARADOR)
    lines.append("")
    lines.append("📦 *Produtos ja disponiveis na fazenda*")
    produtos = produtos or {}
    for tipo, titulo, emoji in (("biologico", "Biologicos", "🧪"), ("quimico", "Quimicos", "⚗️")):
        itens = produtos.get(tipo) or []
        partes = [
            f"{p['nome']} ({p['data_anotacao']})" if p.get("data_anotacao") else p["nome"]
            for p in itens if p.get("nome")
        ]
        lines.append(f"{emoji} *{titulo}:* {', '.join(partes) if partes else '*'}")
    lines.append("")

    lines.append(_WHATSAPP_SEPARADOR)
    if weather:
        lines.append(f"Fonte do clima: {weather['fonte']}")
    lines.append(rodape_data)

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


def _farm_produtos_estoque(site, safra):
    """{"quimico": [...], "biologico": [...]} com os itens que a fazenda
    ja tem comprado (aba Recomendacoes > Produtos Fazenda, momento
    "geral") -- sem `safra` (envio agendado), junta os itens das duas
    safras."""
    existentes = models.get_all_farm_produtos().get(site, {})
    safras_a_considerar = [safra] if safra else [s for s, _ in models.SAFRAS]
    produtos = {}
    for tipo in models.TIPOS_PRODUTO:
        itens = []
        for s in safras_a_considerar:
            itens.extend(existentes.get((s, models.MOMENTO_ESTOQUE_RAPIDO, tipo), []))
        produtos[tipo] = itens
    return produtos


def _send_site_whatsapp(site, safra=None):
    """Monta o relatorio de uma fazenda numa mensagem de texto so (Dados
    de Clima, Fungos em alta quantidade, Recomendacoes das Instituicoes
    de Pesquisa, Produtos Fazenda e Anotacoes -- ver
    `_format_whatsapp_message`) e manda pra TODOS os numeros cadastrados
    que devem recebe-la (uma fazenda pode ter varios --
    `_site_whatsapp_destinations`), pelo WhatsApp do administrador
    (whatsapp-bridge). Manda mesmo quando a fazenda nao tem nenhuma doenca
    em Atencao/Perigo no momento -- nesse caso a mensagem e' so o aviso de
    que esta tudo tranquilo (ver `_format_whatsapp_message`). Retorna (ok,
    mensagem-resumo); ok=True se pelo menos 1 numero recebeu a mensagem.
    `safra` filtra pela cultura daquela safra (quando chamado a partir de
    uma das telas de Recomendacoes) e define de qual safra vem os
    "Produtos Fazenda"; sem `safra` (envio agendado), usa a uniao das
    culturas e dos produtos das duas safras. Bloqueia o envio (sem mandar
    nada) se a estacao dessa fazenda estiver sem leitura nova ha mais de
    `DADOS_BLOQUEIO_DIAS` -- dado velho demais pra virar recomendacao."""
    translations = _load_translations()
    raw_cards = _resolve_site_cards(site, translations)
    dias_sem_leitura = _dias_sem_leitura(raw_cards)
    if _nivel_dados_defasados(dias_sem_leitura) == "bloqueado":
        return False, (
            f"Estacao de '{site}' sem leitura nova ha {dias_sem_leitura} dias -- "
            "envio bloqueado (dado velho demais pra confiar)."
        )
    culturas_by_site = models.get_all_farm_culturas()
    cards_by_site = _filter_cards_by_cultura(
        {site: raw_cards}, culturas_by_site, models.get_doenca_culturas(), safra=safra
    )
    cards = cards_by_site.get(site, [])
    notes = models.get_all_recommendation_notes()
    diseases = _build_site_diseases(site, cards, notes)

    coords = _weather_coords_all()
    weather = _get_weather_for_site(site, coords)
    produtos = _farm_produtos_estoque(site, safra)
    is_virtual = models.get_virtual_farm(site) is not None
    cultura = _cultura_label(site, safra, culturas_by_site)
    text = _format_whatsapp_message(
        site, diseases, weather=weather, produtos=produtos, is_virtual=is_virtual, cultura=cultura
    )

    destinos = _site_whatsapp_destinations(site)
    if not destinos:
        return False, f"Nenhum usuario marcado pra receber relatorio de '{site}' (ou nenhum tem telefone cadastrado)."

    sucesso, falha = [], []
    for phone, rotulo in destinos:
        ok, message = whatsapp.send_whatsapp(phone, text)
        (sucesso if ok else falha).append(rotulo if ok else f"{rotulo} ({message})")
    resumo = f"{len(sucesso)}/{len(destinos)} numero(s)"
    if falha:
        resumo += f" -- falha em: {', '.join(falha)}"
    return len(sucesso) > 0, resumo


_scheduler_last_run_date = None


def _run_scheduled_whatsapp_sends():
    global _scheduler_last_run_date
    today = datetime.now().date()
    schedule = models.get_all_whatsapp_days()
    weekday = today.weekday()
    for site, days in schedule.items():
        if weekday in days:
            _send_site_whatsapp(site)
    _scheduler_last_run_date = today


def _whatsapp_scheduler_loop():
    while True:
        now = datetime.now()
        if now.hour == WHATSAPP_SEND_HOUR and _scheduler_last_run_date != now.date():
            try:
                _run_scheduled_whatsapp_sends()
            except Exception:
                pass  # nunca deixa o loop do agendador morrer
        time.sleep(60)


login_manager = LoginManager(app)
login_manager.login_view = "login"


class User(UserMixin):
    def __init__(self, row):
        self.id = str(row["id"])
        self.username = row["username"]
        self.is_admin = bool(row["is_admin"])


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


ALAN_MAURO_USERNAME = "Alan Mauro"  # unico usuario com acesso a "Editar cadastro" e a exportacao Excel


def alan_mauro_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated or current_user.username != ALAN_MAURO_USERNAME:
            abort(403)
        return view(*args, **kwargs)
    return wrapped


@app.before_request
def _ensure_sites_synced():
    # Mantem a tabela "sites" alinhada com o CSV mais recente (novas fazendas
    # aparecem automaticamente na tela de permissoes).
    try:
        models.sync_sites(read_sites())
    except FileNotFoundError:
        pass


@app.route("/")
def index():
    if current_user.is_authenticated:
        return redirect(url_for("mapa"))
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        row = models.get_user_by_username(username)
        if row and check_password_hash(row["password_hash"], password):
            login_user(User(row))
            return redirect(url_for("mapa"))
        flash("Usuario ou senha invalidos.", "error")
    return render_template("login.html")


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
    return redirect(url_for("dashboard"))


@app.route("/dashboard")
@login_required
def dashboard():
    _maybe_auto_refresh()  # silencioso -- nao avisa mais no topo da pagina, so acontece
    if current_user.is_admin:
        permitted = None  # admin ve todas as fazendas
    else:
        permitted = set(models.get_user_permitted_site_names(int(current_user.id)))
        if not permitted:
            return render_template("dashboard.html", cards_by_site={}, no_access=True)
    cards_by_site = _cards_by_site_all(permitted, _load_translations())
    # Painel mostra TODAS as caixas de doenca sempre -- o filtro de cultura
    # so se aplica nas abas Recomendacoes (Safra / 2 Safra), nao aqui.
    # Umidade/chuva sao do mesmo sensor da fazenda para todas as doencas --
    # mostra uma vez so, ao lado do nome da fazenda, usando a leitura mais
    # recente entre as doencas (em vez de repetir em cada caixa).
    weather_by_site = {}
    dados_status_by_site = {}
    nomes_virtuais = {vf["site_name"]: vf["nome"] for vf in models.get_all_virtual_farms()}
    coords = _weather_coords_all()
    for site, cards in cards_by_site.items():
        mais_recente = max(cards, key=lambda c: c["data"])
        umidade, chuva = mais_recente["umidade"], mais_recente["chuva"]
        if umidade is None and chuva is None and site in nomes_virtuais:
            # Fazenda virtual/estimada nao tem sensor de umidade/chuva
            # proprio (esses campos vem do dispositivo do BioScout) --
            # usa a Open-Meteo pra coordenada dela, igual a tela de
            # Recomendacoes ja faz.
            weather = _get_weather_for_site(site, coords)
            if weather:
                umidade = weather.get("umidade_atual")
                chuva = weather.get("chuva_atual_mm")
        weather_by_site[site] = {
            "umidade": umidade,
            "chuva": chuva,
            "data": mais_recente["data"],
        }
        dias_sem_leitura = _dias_sem_leitura(cards)
        dados_status_by_site[site] = {
            "dias_sem_leitura": dias_sem_leitura,
            "nivel_dados": _nivel_dados_defasados(dias_sem_leitura),
            "virtual": site in nomes_virtuais,
            "nome_exibicao": nomes_virtuais.get(site, site),
        }

    # Ordem do Painel: por data da leitura mais recente (weather_by_site
    # ja' calculou isso por fazenda, real ou virtual), da mais nova pra
    # mais velha.
    sites_ordenados = sorted(cards_by_site, key=lambda s: weather_by_site[s]["data"], reverse=True)
    cards_by_site = {s: cards_by_site[s] for s in sites_ordenados}

    return render_template(
        "dashboard.html", cards_by_site=cards_by_site, no_access=False,
        weather_by_site=weather_by_site, dados_status_by_site=dados_status_by_site,
    )




@app.route("/mapa")
@login_required
def mapa():
    _maybe_auto_refresh()  # silencioso -- nao avisa mais no topo da pagina, so acontece
    if current_user.is_admin:
        permitted = None  # admin ve todas as fazendas
    else:
        permitted = set(models.get_user_permitted_site_names(int(current_user.id)))
        if not permitted:
            return render_template("mapa.html", sites_data=[], no_access=True)
    cards_by_site = _cards_by_site_all(permitted, _load_translations())
    coords = _coords_all()
    virtual_names = models.virtual_farm_site_names()
    overrides = models.get_all_weather_station_overrides()
    estacoes_catalogo = {e["codigo"]: e for e in inmet_stations.get_estacoes()}
    sites_data = []
    estacoes_by_codigo = {}
    for site, cards in cards_by_site.items():
        if site not in coords or not cards:
            continue  # fazenda sem coordenada, ou fazenda virtual sem estacao real no raio -- nao da pra plotar
        lat, lon = coords[site]
        codigo_escolhido = overrides.get(site)
        if codigo_escolhido and codigo_escolhido in estacoes_catalogo:
            # Estacao escolhida na aba Fazendas como referencia de previsao --
            # essa, e nao a mais proxima, e' quem realmente alimenta o clima
            # dessa fazenda.
            base = estacoes_catalogo[codigo_escolhido]
            distancia = inmet_stations._haversine_km(lat, lon, base["lat"], base["lon"])
            estacao = {**base, "distancia_km": round(distancia, 1)}
        else:
            estacao = inmet_stations.estacao_mais_proxima(lat, lon)
        sites_data.append({
            "site": site,
            "lat": lat,
            "lon": lon,
            "virtual": site in virtual_names,
            "cards": sorted(cards, key=lambda c: c["doenca"]),
            "ultima_leitura": models.fmt_data_br(max(c["data"] for c in cards)),
        })
        if estacao:
            entry = estacoes_by_codigo.setdefault(estacao["codigo"], {
                "codigo": estacao["codigo"], "cidade": estacao["cidade"], "uf": estacao["uf"],
                "lat": estacao["lat"], "lon": estacao["lon"], "fazendas": [],
            })
            entry["fazendas"].append({"site": site, "distancia_km": estacao["distancia_km"]})
    sites_data.sort(key=lambda s: s["site"])
    return render_template(
        "mapa.html", sites_data=sites_data, estacoes=list(estacoes_by_codigo.values()), no_access=False
    )


@app.route("/mapa-interpolado")
@admin_required
def mapa_interpolado():
    """Versao "duplicada" do Mapa pra admin criar pontos estimados (sem
    estacao fisica) -- a concentracao de cada doenca nesses pontos e'
    interpolada (IDW) a partir das fazendas reais dentro do raio
    escolhido (ver `virtual_farms.py`). So admin ve essa tela (criar um
    ponto estimado e' uma configuracao, tipo as outras em
    "Configuracoes")."""
    cards_reais = get_dashboard_data(None, _load_translations())
    coords_reais = data_reader.read_site_coordinates()

    sites_data = []
    for site, cards in cards_reais.items():
        if site not in coords_reais:
            continue
        lat, lon = coords_reais[site]
        sites_data.append({
            "site": site, "lat": lat, "lon": lon, "virtual": False,
            "cards": sorted(cards, key=lambda c: c["doenca"]),
            "ultima_leitura": models.fmt_data_br(max(c["data"] for c in cards)),
        })

    pontos_virtuais = []
    for vf in models.get_all_virtual_farms():
        cards, estacoes_usadas = virtual_farms.interpolar_cards(
            vf["lat"], vf["lon"], vf["raio_km"], cards_reais, coords_reais
        )
        pontos_virtuais.append({
            **vf,
            "criado_em": models.fmt_data_br(vf["criado_em"]),
            "cards": cards,
            "estacoes_usadas": estacoes_usadas,
        })
        if cards:
            sites_data.append({
                "site": vf["site_name"], "lat": vf["lat"], "lon": vf["lon"], "virtual": True,
                "cards": cards,
                "ultima_leitura": models.fmt_data_br(max(c["data"] for c in cards)),
                "raio_km": vf["raio_km"],
                "estacoes_usadas": estacoes_usadas,
            })

    sites_data.sort(key=lambda s: s["site"])
    pontos_virtuais.sort(key=lambda p: p["nome"])

    # Todas as estacoes do Brasil aqui (nao so as dos estados com fazenda/
    # ponto, como no Mapa normal) -- essa tela e' justamente pra escolher
    # onde criar um ponto novo, em qualquer lugar do pais.
    estacoes = inmet_stations.get_estacoes()

    return render_template(
        "mapa_interpolado.html", sites_data=sites_data, pontos_virtuais=pontos_virtuais, estacoes=estacoes,
    )


@app.route("/mapa-interpolado/adicionar", methods=["POST"])
@admin_required
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
    try:
        models.create_virtual_farm(nome, lat, lon, raio_km, criado_por=current_user.username)
    except sqlite3.IntegrityError:
        flash(f"Ja existe um ponto estimado chamado '{nome}' -- escolha outro nome.", "error")
        return redirect(url_for("mapa_interpolado"))
    flash(f"Ponto estimado '{nome}' criado -- defina quem pode ve-lo na aba Usuarios.", "success")
    return redirect(url_for("mapa_interpolado"))


@app.route("/mapa-interpolado/editar", methods=["POST"])
@admin_required
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
    try:
        models.update_virtual_farm(site_name, nome, lat, lon, raio_km)
    except sqlite3.IntegrityError:
        flash(f"Ja existe um ponto estimado chamado '{nome}' -- escolha outro nome.", "error")
        return redirect(url_for("mapa_interpolado"))
    flash(f"Ponto estimado '{nome}' atualizado.", "success")
    return redirect(url_for("mapa_interpolado"))


@app.route("/mapa-interpolado/remover", methods=["POST"])
@admin_required
def remover_ponto_virtual():
    site_name = request.form.get("site_name", "")
    if not models.get_virtual_farm(site_name):
        abort(404)
    models.delete_virtual_farm(site_name)
    flash("Ponto estimado removido.", "success")
    return redirect(url_for("mapa_interpolado"))


SAFRA_LABELS = dict(models.SAFRAS)


@app.route("/recommendations")
@login_required
def recommendations_default():
    return redirect(url_for("recommendations", safra="safra1"))


@app.route("/recommendations/<safra>")
@login_required
def recommendations(safra):
    if safra not in SAFRA_LABELS:
        abort(404)
    _maybe_auto_refresh()  # silencioso -- nao avisa mais no topo da pagina, so acontece
    if current_user.is_admin:
        permitted = None
    else:
        permitted = set(models.get_user_permitted_site_names(int(current_user.id)))
        if not permitted:
            return render_template(
                "recommendations.html", sites_data=[], no_access=True,
                safra=safra, safra_label=SAFRA_LABELS[safra], safras=models.SAFRAS,
            )
    culturas_by_site = models.get_all_farm_culturas()
    culturas_ativas = models.get_culturas_ativas()
    raw_cards_by_site = _cards_by_site_all(permitted, _load_translations())
    cards_by_site = _filter_cards_by_cultura(
        raw_cards_by_site, culturas_by_site, models.get_doenca_culturas(), safra=safra
    )
    notes = models.get_all_recommendation_notes()
    coords = _weather_coords_all()
    produtos_by_site = models.get_all_farm_produtos()
    virtual_names = models.virtual_farm_site_names()

    sites_data = []
    for site, cards in cards_by_site.items():
        diseases = _build_site_diseases(site, cards, notes)
        thumbnails = sorted(cards, key=lambda c: c["doenca"])
        existentes = produtos_by_site.get(site, {})
        estoque_rapido = {}
        for tipo in models.TIPOS_PRODUTO:
            linhas = list(existentes.get((safra, models.MOMENTO_ESTOQUE_RAPIDO, tipo), []))
            # Minimo de 2 linhas; sempre 1 linha em branco a mais que o
            # preenchido -- o resto e' criado sozinho no navegador (mesma
            # logica da aba Fazendas), sem limite fixo de linhas.
            linhas_min = max(2, len(linhas) + 1)
            while len(linhas) < linhas_min:
                linhas.append({"data_anotacao": "", "nome": "", "ingrediente_ativo": ""})
            estoque_rapido[tipo] = linhas
        cultura_info = culturas_by_site.get((site, safra), {})
        dias_desde_atualizacao = None
        if cultura_info.get("updated_at"):
            try:
                dt = datetime.strptime(cultura_info["updated_at"], "%Y-%m-%d %H:%M:%S")
                dias_desde_atualizacao = (datetime.now() - dt).days
            except ValueError:
                pass
        weather = _get_weather_for_site(site, coords)
        # Data da leitura mais recente entre TODAS as doencas da fazenda
        # (nao so as que estao em Atencao/Perigo) -- senao some do
        # cabecalho quando a fazenda nao tem nenhum alerta no momento,
        # mesmo com leitura fresca.
        leitura_data = max((c["data"] for c in cards if c.get("data")), default=None)
        dias_sem_leitura = _dias_sem_leitura(raw_cards_by_site.get(site, []))
        nivel_dados = _nivel_dados_defasados(dias_sem_leitura)
        is_virtual = site in virtual_names
        whatsapp_text = (
            "" if nivel_dados == "bloqueado"
            else _format_whatsapp_message(
                site, diseases, weather=weather, produtos=_farm_produtos_estoque(site, safra),
                is_virtual=is_virtual, cultura=cultura_info.get("cultura") or "",
            )
        )
        sites_data.append({
            "site": site, "diseases": diseases, "thumbnails": thumbnails,
            "virtual": is_virtual,
            "weather": weather,
            "leitura_data": leitura_data,
            "estoque_rapido": estoque_rapido,
            "cultura": cultura_info.get("cultura") or "",
            "dias_desde_atualizacao": dias_desde_atualizacao,
            "dias_sem_leitura": dias_sem_leitura,
            "nivel_dados": nivel_dados,
            "whatsapp_destinos": len(_site_whatsapp_destinations(site)),
            "whatsapp_text": whatsapp_text,
        })
    sites_data.sort(key=lambda s: s["site"])
    any_whatsapp_configured = any(s["whatsapp_destinos"] > 0 for s in sites_data)
    return render_template(
        "recommendations.html", sites_data=sites_data, no_access=False,
        any_whatsapp_configured=any_whatsapp_configured,
        safra=safra, safra_label=SAFRA_LABELS[safra], safras=models.SAFRAS, culturas_ativas=culturas_ativas,
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
    safra = _safra_or_default(request.form)
    ok, message = _send_site_whatsapp(site_name, safra=safra)
    if ok:
        flash(f"WhatsApp de '{site_name}' enviado para {message}.", "success")
    else:
        flash(f"Falha ao enviar WhatsApp de '{site_name}': {message}", "error")
    return redirect(url_for("recommendations", safra=safra))



@app.route("/recommendations/whatsapp/selecionados", methods=["POST"])
@login_required
def send_selected_whatsapp():
    safra = _safra_or_default(request.form)
    site_names = request.form.getlist("site_name")
    allowed = None if current_user.is_admin else set(models.get_user_permitted_site_names(int(current_user.id)))
    if not site_names:
        flash("Nenhuma fazenda selecionada.", "error")
        return redirect(url_for("recommendations", safra=safra))
    enviados, falhas = [], []
    for site_name in site_names:
        if allowed is not None and site_name not in allowed:
            continue
        ok, message = _send_site_whatsapp(site_name, safra=safra)
        (enviados if ok else falhas).append(f"{site_name} ({message})")
    if enviados:
        flash(f"WhatsApp enviado para: {', '.join(enviados)}.", "success")
    if falhas:
        flash(f"Falha ao enviar para: {', '.join(falhas)}.", "error")
    return redirect(url_for("recommendations", safra=safra))


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
    return _save_response(f"Agenda de WhatsApp de '{site_name}' salva.", "fazendas")


@app.route("/fazendas")
@login_required
def fazendas():
    virtual_names = models.virtual_farm_site_names()
    if current_user.is_admin:
        sites = sorted(set(read_sites()) | virtual_names)
    else:
        sites = sorted(models.get_user_permitted_site_names(int(current_user.id)))
        if not sites:
            return render_template("fazendas.html", sites_data=[], no_access=True)

    produtos_by_site = models.get_all_farm_produtos()
    plantio_by_site = models.get_all_farm_plantio()
    aplicacoes_by_site = models.get_all_farm_aplicacoes()
    all_days = models.get_all_whatsapp_days()
    coords = _coords_all()
    overrides = models.get_all_weather_station_overrides()
    grades = [("ts", "TS (Tratamento de Sementes)"), ("folha", "Folha (aplicacao foliar)")]
    tipos = [("quimico", "Fungicidas quimicos"), ("biologico", "Biologicos")]

    sites_data = []
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
            plantio_min = max(1, len(plantio_linhas) + 1)
            while len(plantio_linhas) < plantio_min:
                plantio_linhas.append({"data_plantio": "", "talhao": "", "variedade": "", "ciclo_dias": ""})

            aplicacoes_linhas = list(aplicacoes_existente.get(safra, []))
            aplicacoes_min = max(1, len(aplicacoes_linhas) + 1)
            while len(aplicacoes_linhas) < aplicacoes_min:
                aplicacoes_linhas.append({"data_aplicacao": "", "talhao": "", "fungicidas_quimicos": "", "fungicidas_biologicos": ""})

            safras_data.append({
                "safra": safra, "titulo": safra_label, "secoes": secoes,
                "plantio": plantio_linhas, "aplicacoes": aplicacoes_linhas,
            })
        latlon = coords.get(site)
        estacoes_proximas = inmet_stations.estacoes_mais_proximas(*latlon, n=2) if latlon else []
        sites_data.append({
            "site": site, "safras": safras_data, "selected_days": all_days.get(site, set()),
            "virtual": site in virtual_names,
            "estacoes_proximas": estacoes_proximas,
            "estacao_selecionada": overrides.get(site, ""),
        })

    return render_template(
        "fazendas.html", sites_data=sites_data, no_access=False,
        weekday_labels=list(enumerate(WEEKDAY_LABELS)),
    )


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
    if cultura and cultura not in models.get_culturas_ativas():
        abort(400)
    models.set_farm_cultura(site_name, safra, cultura)
    if cultura:
        message = (
            f"Cultura de '{site_name}' na {SAFRA_LABELS[safra]} definida como {cultura} -- "
            "essa aba passa a mostrar so doencas dessa cultura."
        )
    else:
        message = f"Filtro de cultura removido de '{site_name}' na {SAFRA_LABELS[safra]}."
    return _save_response(message, "recommendations", safra=safra)


@app.route("/fazendas/estacao-clima/save", methods=["POST"])
@login_required
def save_weather_station_override():
    site_name = request.form.get("site_name")
    if not current_user.is_admin:
        allowed = set(models.get_user_permitted_site_names(int(current_user.id)))
        if site_name not in allowed:
            abort(403)
    estacao_codigo = request.form.get("estacao_codigo", "")
    models.set_weather_station_override(site_name, estacao_codigo)
    if estacao_codigo:
        message = f"Previsao de '{site_name}' agora usa a estacao {estacao_codigo} como referencia."
    else:
        message = f"Previsao de '{site_name}' volta a usar a coordenada da propria fazenda."
    return _save_response(message, "fazendas")


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
            datas = request.form.getlist(f"data_{momento}_{tipo}")
            nomes = request.form.getlist(f"nome_{momento}_{tipo}")
            ativos = request.form.getlist(f"ia_{momento}_{tipo}")
            linhas = list(zip(datas, nomes, ativos))
            models.set_farm_produtos(site_name, safra, momento, tipo, linhas)
    return _save_response(f"Produtos de '{site_name}' ({SAFRA_LABELS[safra]}) salvos.", "fazendas")


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
    return _save_response(f"Dados de plantio de '{site_name}' ({SAFRA_LABELS[safra]}) salvos.", "fazendas")


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
    return _save_response(f"Dados de aplicacoes de '{site_name}' ({SAFRA_LABELS[safra]}) salvos.", "fazendas")


@app.route("/recommendations/estoque/save", methods=["POST"])
@login_required
def save_estoque_rapido():
    site_name = request.form.get("site_name")
    if not current_user.is_admin:
        allowed = set(models.get_user_permitted_site_names(int(current_user.id)))
        if site_name not in allowed:
            abort(403)
    safra = _safra_or_default(request.form)
    for tipo in models.TIPOS_PRODUTO:
        datas = request.form.getlist(f"data_{tipo}")
        nomes = request.form.getlist(f"nome_{tipo}")
        linhas = [(data, nome, "") for data, nome in zip(datas, nomes)]
        models.set_farm_produtos(site_name, safra, models.MOMENTO_ESTOQUE_RAPIDO, tipo, linhas)
    return _save_response(f"Estoque de '{site_name}' salvo.", "recommendations", safra=safra)


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
    for site, doenca, nota in zip(sites, doencas, notas):
        if allowed_sites is not None and site not in allowed_sites:
            continue  # usuario nao tem permissao para essa fazenda -- ignora
        models.save_recommendation_note(site, doenca, nota.strip())
    return _save_response("Anotacoes salvas.", "recommendations", safra=_safra_or_default(request.form))


@app.route("/admin/doencas", methods=["GET", "POST"])
@admin_required
def admin_doencas():
    if request.method == "POST":
        if request.form.get("form_id") == "matriz_culturas":
            doenca_ens = request.form.getlist("doenca_en")
            for idx, doenca_en in enumerate(doenca_ens):
                culturas = request.form.getlist(f"culturas__{idx}")
                models.set_doenca_culturas(doenca_en, culturas)
            return _save_response("Matriz doenca x cultura atualizada.", "admin_doencas")

        display_names = request.form.getlist("display_name_en")
        nomes_pt = request.form.getlist("nome_pt")
        nomes_cientificos = request.form.getlist("nome_cientifico")
        for display_name_en, nome_pt, nome_cientifico in zip(display_names, nomes_pt, nomes_cientificos):
            nome_pt = nome_pt.strip()
            if nome_pt:
                models.save_disease_translation(display_name_en, nome_pt, nome_cientifico.strip())
        return _save_response("Nomes de doencas atualizados.", "admin_doencas")

    _load_translations()  # garante linhas novas + nome cientifico pre-preenchido
    info = models.get_all_disease_info()
    doencas = [
        {"en": en, "pt": data["nome_pt"], "cientifico": data["nome_cientifico"]}
        for en, data in sorted(info.items(), key=lambda kv: kv[1]["nome_pt"])
    ]
    culturas_ativas = models.get_culturas_ativas()
    doenca_culturas = models.get_doenca_culturas()
    matriz = [
        {"en": d["en"], "pt": d["pt"], "marcadas": doenca_culturas.get(d["en"], set())}
        for d in doencas
    ]
    return render_template(
        "admin_doencas.html", doencas=doencas, culturas_ativas=culturas_ativas, matriz=matriz
    )


@app.route("/admin/culturas", methods=["GET", "POST"])
@admin_required
def admin_culturas():
    if request.method == "POST":
        nomes = [request.form.get(f"nome_{i}", "").strip() for i in range(10)]
        models.set_culturas(nomes)
        return _save_response("Nomes de culturas atualizados.", "admin_culturas")

    return render_template("admin_culturas.html", nomes=models.get_culturas())


@app.route("/admin/fungicidas", methods=["GET", "POST"])
@admin_required
def admin_fungicidas():
    if request.method == "POST":
        for row_id in request.form.getlist("row_id"):
            key = request.form.get(f"key__{row_id}")
            if not key:
                continue
            doenca, tipo, idx_str = key.split("|", 2)
            idx = int(idx_str)
            ingrediente = request.form.get(f"ingrediente__{row_id}", "").strip()
            classe = request.form.get(f"classe__{row_id}", "")
            rec = fungicida_data.get_recomendacao(doenca)
            default_item = rec[tipo + "s"]["itens"][idx] if rec else None
            is_default = (
                default_item is not None
                and ingrediente == default_item["ingrediente"] and classe == (default_item["classe"] or "")
            )
            if is_default:
                # Sem edicao real -- nao grava override, assim a linha continua
                # acompanhando fungicida_data.py se ele for atualizado depois.
                models.delete_fungicida_override(doenca, tipo, idx)
            else:
                models.save_fungicida_override(doenca, tipo, idx, ingrediente, classe, False)
        return _save_response("Recomendacoes de fungicidas atualizadas.", "admin_fungicidas")

    overrides = models.get_all_fungicida_overrides()
    translations = _load_translations()

    doencas_data = []
    row_counter = 0
    for doenca_en, rotulo in sorted(translations.items(), key=lambda kv: kv[1]):
        rec = fungicida_data.get_recomendacao(doenca_en)
        if not rec:
            doencas_data.append({"doenca": doenca_en, "rotulo": rotulo, "grupos": [], "sem_dados": True})
            continue
        grupos = []
        for tipo, grupo in (("quimico", rec["quimicos"]), ("biologico", rec["biologicos"])):
            n = len(grupo["itens"])
            ordem = models.get_fungicida_ordem(doenca_en, tipo, n)
            linhas = []
            for posicao, idx in enumerate(ordem):
                item = grupo["itens"][idx]
                override = overrides.get((doenca_en, tipo, idx))
                linhas.append({
                    "row_id": row_counter,
                    "key": f"{doenca_en}|{tipo}|{idx}",
                    "doenca": doenca_en,
                    "tipo": tipo,
                    "idx": idx,
                    "ingrediente": override["ingrediente"] if override else item["ingrediente"],
                    "classe": (override["classe"] if override else item["classe"]) or "",
                    "pode_subir": posicao > 0,
                    "pode_descer": posicao < n - 1,
                })
                row_counter += 1
            grupos.append({
                "tipo": tipo, "titulo": "Quimicos" if tipo == "quimico" else "Biologicos",
                "fonte": grupo["fonte"], "fonte_url": grupo["fonte_url"], "linhas": linhas,
            })
        doencas_data.append({"doenca": doenca_en, "rotulo": rotulo, "grupos": grupos, "sem_dados": False})

    classes = [("", "Sem classificacao")] + list(fungicida_data.CLASSE_LABEL.items())
    return render_template("admin_fungicidas.html", doencas_data=doencas_data, classes=classes)


@app.route("/admin/fungicidas/mover", methods=["POST"])
@admin_required
def mover_fungicida_item():
    doenca = request.form.get("doenca")
    tipo = request.form.get("tipo")
    direction = request.form.get("direction")
    idx = request.form.get("idx")
    rec = fungicida_data.get_recomendacao(doenca) if doenca else None
    if rec and tipo in models.TIPOS_PRODUTO and direction in ("up", "down") and idx is not None:
        n = len(rec[tipo + "s"]["itens"])
        models.move_fungicida_item(doenca, tipo, int(idx), direction, n)
    return redirect(url_for("admin_fungicidas"))


@app.route("/admin/users")
@admin_required
def admin_users():
    users = models.get_all_users()
    report_counts = {u["id"]: len(models.get_user_report_site_ids(u["id"])) for u in users}
    return render_template(
        "admin_users.html", users=users, default_password=models.DEFAULT_PASSWORD, report_counts=report_counts
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


def _split_phone(telefone):
    """Separa um telefone ja salvo (so digitos) em (codigo_pais, numero)
    pra preencher as duas caixas ao reabrir o formulario -- assume Brasil
    (55) como padrao, que e' o caso de praticamente todo cadastro atual;
    numero de outro pais ainda funciona, so aparece inteiro na caixa do
    numero pra conferir/ajustar manualmente."""
    if telefone.startswith("55") and len(telefone) > 10:
        return "55", telefone[2:]
    return "55", telefone


@app.route("/admin/users/create", methods=["POST"])
@admin_required
def admin_create_user():
    username = request.form.get("username", "").strip()
    email = request.form.get("email", "").strip()
    telefone = _telefone_from_form(request.form)
    is_admin = request.form.get("is_admin") == "on"
    if not username:
        flash("Usuario e obrigatorio.", "error")
    elif not _is_valid_email(email):
        flash("Informe um email valido.", "error")
    elif not _is_valid_phone(telefone):
        flash("Informe um numero de telefone valido, com codigo do pais (ex.: 5511999999999).", "error")
    elif models.get_user_by_username(username):
        flash("Ja existe um usuario com esse nome.", "error")
    else:
        models.create_user(username, models.DEFAULT_PASSWORD, is_admin, email=email, telefone=telefone)
        flash(
            f"Usuario '{username}' criado com a senha padrao '{models.DEFAULT_PASSWORD}' -- "
            "avise a pessoa pra trocar assim que entrar (botao 'Trocar senha' no menu). "
            "Nao esqueca de marcar quais fazendas ela recebe relatorio na coluna "
            "'Receber relatorios' -- o numero cadastrado ja fica pronto pra receber.",
            "success",
        )
    return redirect(url_for("admin_users"))


@app.route("/admin/users/<int:user_id>/editar", methods=["GET", "POST"])
@alan_mauro_required
def admin_edit_user(user_id):
    user_row = models.get_user_by_id(user_id)
    if not user_row:
        abort(404)
    if request.method == "POST":
        action = request.form.get("action")
        email = request.form.get("email", "").strip()
        telefone = _telefone_from_form(request.form)
        is_admin = request.form.get("is_admin") == "on"
        error = None
        if str(user_id) == current_user.id and not is_admin:
            error = "Voce nao pode remover seu proprio acesso de administrador."
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
                codigo_pais=codigo_pais, numero_telefone=numero_telefone, is_admin=is_admin,
            )
        models.set_user_contato(user_id, email, telefone, is_admin=is_admin)
        if action == "test":
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
        codigo_pais=codigo_pais, numero_telefone=numero_telefone, is_admin=bool(user_row["is_admin"]),
    )


@app.route("/admin/users/<int:user_id>/reset-password", methods=["POST"])
@admin_required
def admin_reset_password(user_id):
    user_row = models.get_user_by_id(user_id)
    if not user_row:
        abort(404)
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


@app.route("/meu-whatsapp", methods=["GET", "POST"])
@login_required
def meu_whatsapp():
    if request.method == "POST":
        action = request.form.get("action")
        telefone = _telefone_from_form(request.form)
        if not _is_valid_phone(telefone):
            error = "Informe um numero de telefone valido, com codigo do pais (ex.: 5511999999999)."
            if request.headers.get("X-Autosave") == "1":
                return {"ok": False, "message": error}
            flash(error, "error")
            return redirect(url_for("meu_whatsapp"))
        models.set_user_whatsapp(int(current_user.id), telefone)
        if action == "test":
            ok, message = whatsapp.send_whatsapp(
                telefone, "OneAgro: mensagem de teste. Se voce recebeu isso, seu numero esta certo!",
            )
            flash(("Mensagem de teste enviada! Confira seu WhatsApp." if ok else f"Falha no teste: {message}"),
                  "success" if ok else "error")
            return redirect(url_for("meu_whatsapp"))
        return _save_response("Seu telefone foi salvo.", "meu_whatsapp")
    user_row = models.get_user_by_id(int(current_user.id))
    codigo_pais, numero_telefone = _split_phone(user_row["telefone"] or "")
    return render_template("meu_whatsapp.html", codigo_pais=codigo_pais, numero_telefone=numero_telefone)


@app.route("/admin/users/<int:user_id>/delete", methods=["POST"])
@admin_required
def admin_delete_user(user_id):
    if str(user_id) == current_user.id:
        flash("Voce nao pode excluir o proprio usuario logado.", "error")
    else:
        models.delete_user(user_id)
        flash("Usuario excluido.", "success")
    return redirect(url_for("admin_users"))


@app.route("/admin/exportar")
@alan_mauro_required
def admin_exportar():
    """Relatorio Excel (cadastro/Fazendas/Manejo das 3 safras) -- so
    `ALAN_MAURO_USERNAME` tem acesso (aba escondida no menu pra qualquer
    outra conta -- ver base.html)."""
    buffer = export_excel.build_workbook()
    filename = f"bioscout_export_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
    return send_file(
        buffer, as_attachment=True, download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.route("/admin/users/<int:user_id>/permissions", methods=["GET", "POST"])
@admin_required
def admin_user_permissions(user_id):
    user_row = models.get_user_by_id(user_id)
    if not user_row:
        abort(404)
    if request.method == "POST":
        selected_ids = {int(v) for v in request.form.getlist("site_ids")}
        models.set_user_permissions(user_id, selected_ids)
        return _save_response(f"Permissoes de '{user_row['username']}' atualizadas.", "admin_users")
    all_sites = models.get_all_sites()
    permitted_ids = models.get_user_permitted_site_ids(user_id)
    return render_template(
        "admin_permissions.html", user=user_row, sites=all_sites, permitted_ids=permitted_ids
    )


@app.route("/admin/users/<int:user_id>/relatorios", methods=["GET", "POST"])
@admin_required
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
    all_sites = models.get_all_sites()
    report_ids = models.get_user_report_site_ids(user_id)
    return render_template(
        "admin_report_permissions.html", user=user_row, sites=all_sites, report_ids=report_ids
    )


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
    threading.Thread(target=_whatsapp_scheduler_loop, daemon=True).start()


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
    # so inicia o agendador uma vez (o reloader do Flask sobe o processo 2x em modo debug)
    if os.environ.get("WERKZEUG_RUN_MAIN") == "true" or not app.debug:
        threading.Thread(target=_whatsapp_scheduler_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=5000, debug=True)
else:
    _bootstrap_once()
