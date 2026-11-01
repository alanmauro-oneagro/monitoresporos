"""Gera o relatorio Excel (.xlsx) do site em 7 abas resumidas (pedido do
Alan: simples e objetivo): Fazendas (resumo/situacao de cada uma), Clima
e Esporos (ultimos 90 dias), Manejo (plantio, aplicacoes, produtos e anotacoes
num historico so'), Doencas, Fungicidas, WhatsApp (agenda e envios dos
ultimos 90 dias) e Usuarios -- exportacao restrita a
`ALAN_MAURO_USERNAME`, ver `admin_exportar` em `app.py`."""
import io
from datetime import date, datetime, time, timedelta

from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Alignment, Font, PatternFill

import countries
import data_reader
import agrofit_dados
import fungicida_data
import models


def _ordinal_para_ordenar(data_texto):
    """Numero ordinal (dias desde 01/01/0001) de uma data/datetime em
    qualquer formato usado no banco (ISO com ou sem hora, dd/mm/aaaa,
    dd/mm/aa -- ver models.parse_data_flexivel) -- 0 se vazio/nao
    reconhecido (fica como "mais antigo" possivel, nunca quebra a
    ordenacao por causa de uma anotacao livre tipo "comprado em
    agosto"). Usado pra ordenar toda aba do Excel com coluna de data
    sempre do mais novo pro mais antigo (pedido explicito do usuario)."""
    if not data_texto:
        return 0
    bruto = str(data_texto).strip()
    try:
        return datetime.fromisoformat(bruto).toordinal()
    except ValueError:
        pass
    data_iso = models.parse_data_flexivel(bruto)
    if data_iso:
        try:
            return date.fromisoformat(data_iso).toordinal()
        except ValueError:
            pass
    return 0


def _data_hora_excel(criado_em):
    """(date, time) DE VERDADE (nao texto) a partir de um `criado_em`
    ("YYYY-MM-DD HH:MM:SS", formato fixo gravado por
    `models._agora_cuiaba()` -- ver `log_whatsapp_envio`) -- mesmo
    raciocinio de `_data_excel` (Excel reconhece como data/hora de
    verdade, ordena/filtra certo pela propria planilha em vez de texto).
    Usado na aba WhatsApp (Historico de Envios), que tinha um unico
    campo "Data/Hora" combinado -- separado em 2 colunas pra' poder
    ordenar/filtrar so' por data ou so' por hora. Devolve
    (valor_original, None) se nao reconhecer o formato, nunca quebra a
    exportacao por causa disso."""
    if not criado_em:
        return None, None
    try:
        dt = datetime.strptime(str(criado_em).strip(), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return criado_em, None
    return dt.date(), dt.time()


def _data_excel(data_texto):
    """Objeto `date` DE VERDADE (nao texto formatado) -- Excel reconhece
    como data de verdade (ordena/filtra certo pela propria planilha, e
    nao mostra aquele aviso de "numero guardado como texto"). A
    aparencia dd/mm/aaaa fica por conta do number_format aplicado na
    celula (ver `date_columns` em `_write_sheet`/`_try_sheet`), nao do
    valor em si -- ANTES essa funcao devolvia uma STRING ja formatada
    (dd/mm/aaaa), que o Excel tratava como texto puro: a ordem que a
    gente escrevia (mais novo primeiro) ficava certa, mas se a pessoa
    tentasse ordenar/filtrar pela propria coluna no Excel, ordenava
    alfabeticamente (errado). So' usado na coluna "Data" do Relatorio
    Diario -- devolve o texto original se nao reconhecer a data (nunca
    quebra a exportacao por causa disso)."""
    if not data_texto:
        return data_texto
    data_iso = models.parse_data_flexivel(str(data_texto).strip())
    if not data_iso:
        return data_texto
    try:
        return date.fromisoformat(data_iso)
    except ValueError:
        return data_texto


UR_LIMIARES = (80, 85, 90, 95)

MOMENTO_LABELS = {"ts": "TS", "sulco": "Sulco", "folha": "Folha"}
TIPO_LABELS = {"quimico": "Químico", "biologico": "Biológico"}
SAFRA_LABELS = dict(models.SAFRAS)
WEEKDAY_LABELS = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sab", "Dom"]  # 0=Segunda ... 6=Domingo
_CABECALHO_FUNDO = PatternFill("solid", fgColor="0A3D14")
# Mesmos limites de `app.DADOS_AVISO_DIAS`/`DADOS_BLOQUEIO_DIAS` (duplicados
# pelo mesmo motivo do `_MOLHAMENTO_PADRAO_HORAS` abaixo -- import circular).
_DADOS_AVISO_DIAS = 7
_DADOS_BLOQUEIO_DIAS = 15
_HISTORICO_WHATSAPP_DIAS = 90
_HISTORICO_CLIMA_DIAS = 90


def _safe_row(row):
    """Remove caractere de controle invalido em XML (0x00-0x08, 0x0B-0x0C,
    0x0E-0x1F) de qualquer valor de texto na linha -- sem isso, um unico
    caractere assim (ex.: copiado de uma mensagem de erro do
    whatsapp-bridge, ou de um campo de anotacao livre) faz
    `ws.append` levantar `IllegalCharacterError` e derruba a exportacao
    INTEIRA (mesmo quem so' queria uma aba sem nada a ver com o campo
    problematico -- ja aconteceu, ver conversa)."""
    return [ILLEGAL_CHARACTERS_RE.sub("", v) if isinstance(v, str) else v for v in row]


def _write_sheet(wb, title, headers, rows, date_columns=()):
    """`date_columns` (indices, 0-based) marca quais colunas recebem um
    objeto `date` DE VERDADE (ver `_data_excel`) em vez de texto --
    aplica o number_format dd/mm/aaaa na celula, pra' Excel mostrar no
    formato brasileiro E reconhecer como data de verdade (da' pra'
    ordenar/filtrar certo pela propria planilha, sem o aviso de "numero
    guardado como texto" que uma STRING ja formatada causava antes)."""
    ws = wb.create_sheet(title=title)
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = _CABECALHO_FUNDO
        cell.alignment = Alignment(vertical="center", wrap_text=True)
    for row in rows:
        ws.append(_safe_row(row))
    ws.freeze_panes = "A2"
    if rows:
        ws.auto_filter.ref = ws.dimensions
    if date_columns:
        for row_cells in ws.iter_rows(min_row=2):
            for idx in date_columns:
                if idx < len(row_cells) and isinstance(row_cells[idx].value, (date, datetime)):
                    row_cells[idx].number_format = "DD/MM/YYYY"
    for col in ws.columns:
        length = max((len(str(c.value)) for c in col if c.value is not None), default=8)
        ws.column_dimensions[col[0].column_letter].width = min(max(length + 2, 10), 50)


def _try_sheet(wb, title, headers, rows_fn, date_columns=()):
    """Roda `rows_fn()` protegido -- se QUALQUER excecao acontecer (dado
    inesperado vindo de producao, campo ausente etc.) na montagem de
    UMA aba, escreve essa aba mesmo assim com uma linha explicando o
    erro em vez de derrubar a exportacao INTEIRA por causa dela (sem
    isso, um bug numa unica aba fazia a rota inteira devolver erro,
    nenhum arquivo pra baixar -- ou pior, a aba simplesmente sumia sem
    nenhuma pista do motivo). O erro fica visivel na propria planilha,
    facil de reportar de volta."""
    try:
        rows = rows_fn()
    except Exception as exc:
        rows = [[f"Erro ao gerar esta aba: {exc}"] + [""] * (len(headers) - 1)]
    _write_sheet(wb, title, headers, rows, date_columns=date_columns)


def _usuarios_rows():
    """Uma aba so' com usuario (login de verdade) e subordinado (contato
    leve -- nome + telefone, sem login, ver `models.get_owner_subordinados`)
    mesclados -- cada subordinado aparece logo abaixo do seu "dono", com
    "Tipo"/"Dono" marcando a diferenca, em vez de duas abas separadas
    (pedido explicito do usuario)."""
    site_names_by_id = {s["id"]: s["site_name"] for s in models.get_all_sites()}
    rows = []
    # Admin primeiro (entre eles, ordem alfabetica), depois os demais --
    # mesma ordem de `models.get_all_users` (pedido explicito do usuario).
    for u in models.get_all_users():
        if u["is_admin"]:
            fazendas = "Todas (admin)"
        else:
            fazendas = ", ".join(models.get_user_permitted_site_names(u["id"])) or "-"
        report_ids = models.get_user_report_site_ids(u["id"])
        relatorios = ", ".join(sorted(site_names_by_id[sid] for sid in report_ids if sid in site_names_by_id)) or "-"
        rows.append([
            u["username"], "Usuario", "-", u["email"] or "", models.fmt_telefone_br(u["telefone"]) or "",
            "Sim" if u["is_admin"] else "Nao", fazendas, relatorios,
        ])
        subs = sorted(models.get_owner_subordinados(u["id"]), key=lambda s: s["nome"].lower())
        for s in subs:
            fazendas_sub = ", ".join(sorted(site_names_by_id[sid] for sid in s["site_ids"] if sid in site_names_by_id)) or "-"
            rows.append([
                s["nome"], "Subordinado", u["username"], "-", models.fmt_telefone_br(s["telefone"]) or "",
                "-", "-", fazendas_sub,
            ])
    return rows


_MOLHAMENTO_PADRAO_HORAS = 6  # mesmo padrao/valor de `app._MOLHAMENTO_PADRAO_HORAS` -- duplicado aqui de proposito (nao importado de app.py, que importaria export_excel.py de volta e criaria import circular), mesmo padrao ja usado no projeto (ex.: haversine duplicada entre inmet_stations.py/virtual_farms.py) pra' uma conta pequena e pura como essa.


def _calc_risco_diario_pct(disease, horas_do_dia):
    """Mesma conta de `app.calc_risco_diario_pct` (risco de infeccao
    0-100%, horas do dia com temp E umidade/chuva favoraveis ao mesmo
    tempo pra germinacao, dividido pelo limiar de molhamento da doenca)
    -- duplicada aqui pelo mesmo motivo do `_MOLHAMENTO_PADRAO_HORAS`
    acima. Retorna None quando falta clima ou limite cadastrado."""
    temp_min, temp_max, ur_min = disease.get("germ_temp_min"), disease.get("germ_temp_max"), disease.get("germ_ur_min")
    if temp_min is None or temp_max is None or not horas_do_dia:
        return None
    agua_livre_inibe = disease.get("germ_agua_livre_inibe")
    favoraveis = 0
    for h in horas_do_dia:
        temp, umidade, chuva = h.get("temp"), h.get("umidade"), h.get("chuva") or 0
        if temp is None:
            continue
        temp_ok = temp_min <= temp <= temp_max
        umidade_ok = False
        if ur_min is not None and umidade is not None:
            if agua_livre_inibe:
                umidade_ok = umidade >= ur_min and chuva <= 0.2
            else:
                umidade_ok = umidade >= ur_min or chuva > 0.2
        if temp_ok and umidade_ok:
            favoraveis += 1
    limiar = disease.get("germ_molhamento_horas") or _MOLHAMENTO_PADRAO_HORAS
    return round(min(100, favoraveis / limiar * 100))


def _atividades_por_site_dia():
    """(site_name, data ISO) -> lista de (rotulo_atividade, detalhe) --
    Plantio/Aplicacoes/Produtos correlacionados por fazenda+data, pra'
    entrar na aba "Relatorio Diario" assim que uma data de verdade for
    preenchida numa dessas 3 telas (pedido explicito do usuario: comecar
    um historico de atividades de safra ao lado do dado de estacao, pra'
    no futuro dar pra' cruzar os dois). So' entra quem tem uma data que
    o `models.parse_data_flexivel` reconhece (ISO ou dd/mm/aaaa/aa,
    string inteira) -- em Produtos isso deixa de fora a maioria das
    linhas de proposito, ja' que `data_anotacao` tambem aceita anotacao
    livre (nao vira campo de data obrigatorio, so' correlaciona quando
    o que foi digitado realmente parece uma data)."""
    atividades = {}

    def _add(site, data_raw, rotulo, detalhe):
        data_iso = models.parse_data_flexivel(data_raw)
        if data_iso:
            atividades.setdefault((site, data_iso), []).append((rotulo, detalhe))

    for site, por_safra in models.get_all_farm_plantio().items():
        for safra, linhas in por_safra.items():
            for l in linhas:
                detalhe = (
                    f"{SAFRA_LABELS.get(safra, safra)} - Talhao {l['talhao'] or '-'} - "
                    f"{l['variedade'] or '-'} - Ciclo {l['ciclo_dias'] or '-'} dias"
                )
                _add(site, l["data_plantio"], "Plantio", detalhe)

    for site, por_safra in models.get_all_farm_aplicacoes().items():
        for safra, linhas in por_safra.items():
            for l in linhas:
                detalhe = (
                    f"{SAFRA_LABELS.get(safra, safra)} - Talhao {l['talhao'] or '-'} - "
                    f"Quimicos: {l['fungicidas_quimicos'] or '-'} - Biologicos: {l['fungicidas_biologicos'] or '-'}"
                )
                _add(site, l["data_aplicacao"], "Aplicacao", detalhe)

    for site, buckets in models.get_all_farm_produtos().items():
        for (safra, momento, tipo), linhas in buckets.items():
            if momento not in models.MOMENTOS:
                continue  # so' defesa, mesmo criterio de _fazendas_produtos_rows
            for l in linhas:
                rotulo = f"Produto ({MOMENTO_LABELS.get(momento, momento)})"
                detalhe = f"{SAFRA_LABELS.get(safra, safra)} - {TIPO_LABELS.get(tipo, tipo)}: {l['nome'] or '-'} ({l['ingrediente_ativo'] or '-'})"
                _add(site, l["data_anotacao"], rotulo, detalhe)

    for lista in atividades.values():
        lista.sort(key=lambda t: t[0])
    return atividades


def _relatorio_diario_rows(completar_clima=None, desde=None):
    """Uma linha por (estacao/fazenda, dia, doenca OU atividade de
    safra) -- clima do dia (temp, UR, vento, igual sempre) MAIS a
    concentracao de esporos/risco de infeccao (pra cada doenca com
    leitura naquele dia) E as atividades de Plantio/Aplicacoes/Produtos
    datadas naquele dia (ver `_atividades_por_site_dia`) -- pedido
    explicito do usuario de ter tudo isso correlacionado na mesma aba,
    formando um historico (fazenda+data) que uma futura combinacao
    "atividades de safra + dados das estacoes" possa aproveitar.
    Fazenda/dia sem NENHUMA doenca/atividade vira 1 linha so' com essas
    colunas em branco (nao perde o clima); fazenda/dia com N doencas E M
    atividades vira N+M linhas (clima repetido em cada uma, NUNCA um
    produto cartesiano N*M -- doenca e atividade sao fatos
    independentes do mesmo dia, nao uma ligada a outra). Fazenda/dia com
    atividade mas SEM NENHUMA leitura de clima (fazenda nova/virtual sem
    estacao mapeada ainda, ou doenca de um device que nunca foi "o
    primeiro" daquele site em `read_site_device_ids` -- esse 2o caso ja'
    era um bug antes desta funcao ganhar atividades: a doenca
    simplesmente sumia da aba, sem nenhum fallback) tambem ganha
    linha(s), so' com o clima em branco."""
    translations = models.get_all_disease_translations()
    spore_lookup = data_reader.build_disease_concentration_lookup(data_reader.read_spore_counts())
    device_by_site = data_reader.read_site_device_ids()
    device_to_site = {device: site for site, device in device_by_site.items()}
    hourly_lookup = {}
    if completar_clima:
        # Clima so' de fonte externa (`app._completar_clima_horario`) --
        # do BioScout so' esporos. Cobre do 1o dia com leitura de esporo
        # ate' hoje, por fazenda com device.
        dias_com_esporo = sorted({p["data"] for pontos in spore_lookup.values() for p in pontos})
        if dias_com_esporo:
            inicio = max(date.fromisoformat(dias_com_esporo[0]), desde or date.min)
            dias = [(inicio + timedelta(days=i)).isoformat() for i in range((date.today() - inicio).days + 1)]
            hourly_lookup = completar_clima({}, device_by_site, set(device_by_site), dias)
    report = data_reader.relatorio_diario_de_horas(hourly_lookup, UR_LIMIARES)

    # (site, dia) -> lista de (nome_pt, concentracao, risco_pct, warn,
    # danger, maximo), ordenada por nome de doenca -- montada uma vez so',
    # fora do loop principal. warn/danger/maximo sao os limites de
    # concentracao daquele MODELO de doenca (mesmos 3 usados pra colorir
    # o grafico de esporos da aba Graficos e decidir o status Atencao/
    # Perigo do dashboard) -- vem junto de cada leitura porque a BioScout
    # manda o limiar vigente em cada amostra (pode mudar se o modelo for
    # recalibrado), nao e' um valor fixo por doenca.
    doencas_por_site_dia = {}
    for (site, doenca_en), pontos in spore_lookup.items():
        info = translations.get(doenca_en, {})
        nome_pt = info.get("nome_pt") or doenca_en
        device = device_by_site.get(site)
        for p in pontos:
            horas = hourly_lookup.get((device, p["data"]))
            risco_pct = _calc_risco_diario_pct(info, horas) if horas else None
            conc = p["concentracao"]
            doencas_por_site_dia.setdefault((site, p["data"]), []).append((
                nome_pt, round(conc, 1) if conc is not None else None, risco_pct,
                p.get("warn"), p.get("danger"), p.get("maximo"),
            ))
    for lista in doencas_por_site_dia.values():
        lista.sort(key=lambda t: t[0].lower())

    # Atividades de safra -- protegido em try/except PROPRIO (nao o
    # _try_sheet la' de fora, que e' por aba inteira): um bug nessas 3
    # fontes novas nao pode derrubar as linhas de clima/doenca que ja
    # funcionavam antes desta funcao ganhar atividades.
    try:
        atividades_por_site_dia = _atividades_por_site_dia()
    except Exception:
        atividades_por_site_dia = {}

    entries = []  # (sort_key, row)
    consumidos = set()

    def _sort_key(fazenda, data_iso):
        # Data mais NOVA primeiro dentro de cada fazenda (pedido
        # explicito do usuario -- mesma regra aplicada em toda aba do
        # Excel com coluna de data, ver _ordinal_para_ordenar).
        return (fazenda.lower(), -_ordinal_para_ordenar(data_iso))

    for r in report:
        site = device_to_site.get(r["estacao"])
        fazenda = site or r["estacao"]
        data_iso = r["data"]
        data_br = _data_excel(data_iso)
        base = [
            data_br, fazenda, r["estacao"], r["temp_min"], r["temp_max"],
        ] + [r["ur_counts"][limiar] for limiar in UR_LIMIARES] + [r["vento_predominante"] or "-"]
        doencas = doencas_por_site_dia.get((site, data_iso), []) if site else []
        atividades = atividades_por_site_dia.get((site, data_iso), []) if site else []
        if site:
            consumidos.add((site, data_iso))
        if not doencas and not atividades:
            entries.append((_sort_key(fazenda, data_iso), base + [None, None, None, None, None, None, None, None]))
            continue
        for nome_pt, conc, risco_pct, warn, danger, maximo in doencas:
            entries.append((_sort_key(fazenda, data_iso), base + [nome_pt, conc, risco_pct, warn, danger, maximo, None, None]))
        for rotulo, detalhe in atividades:
            entries.append((_sort_key(fazenda, data_iso), base + [None, None, None, None, None, None, rotulo, detalhe]))

    # Fazenda/dia com doenca OU atividade que nenhuma linha de clima
    # acima cobriu (site sem device mapeado, ou device que nunca virou
    # "o primeiro" daquele site) -- clima em branco (None, nunca 0 --
    # 0 sugeriria "UR nunca passou do limite", que nao e' o caso, e'
    # so' dado ausente), Estacao/Vento como "-".
    faltantes = (set(doencas_por_site_dia) | set(atividades_por_site_dia)) - consumidos
    for site, data_iso in faltantes:
        fazenda = site
        data_br = _data_excel(data_iso)
        base = [data_br, fazenda, "-", None, None, None, None, None, None, "-"]
        for nome_pt, conc, risco_pct, warn, danger, maximo in doencas_por_site_dia.get((site, data_iso), []):
            entries.append((_sort_key(fazenda, data_iso), base + [nome_pt, conc, risco_pct, warn, danger, maximo, None, None]))
        for rotulo, detalhe in atividades_por_site_dia.get((site, data_iso), []):
            entries.append((_sort_key(fazenda, data_iso), base + [None, None, None, None, None, None, rotulo, detalhe]))

    entries.sort(key=lambda e: e[0])
    return [row for _, row in entries]


def _nomes_exibicao():
    """site_name -> nome de exibicao (mesma cadeia de `app._nome_exibicao`:
    nome do ponto virtual, nome escolhido na aba Manejos, ou o site_name
    sem o prefixo "OneAgro - ")."""
    display = models.get_all_site_display_names()
    virtuais = {vf["site_name"]: vf["nome"] for vf in models.get_all_virtual_farms()}

    def nome(site):
        if site in virtuais:
            return virtuais[site]
        return display.get(site) or (site.split(" - ", 1)[1] if " - " in site else site)
    return nome


def _fazendas_rows():
    """Aba "Fazendas": uma linha por fazenda ativa com cadastro + situacao
    (culturas das 3 safras, ultima leitura, dias sem leitura, quantos
    recebem por WhatsApp)."""
    nome = _nomes_exibicao()
    site_countries = models.get_all_site_countries()
    overrides = models.get_all_weather_station_overrides()
    virtual_by_name = {vf["site_name"]: vf for vf in models.get_all_virtual_farms()}
    coords = dict(data_reader.read_site_coordinates())
    for vf in virtual_by_name.values():
        coords[vf["site_name"]] = (vf["lat"], vf["lon"])
    culturas = models.get_all_farm_culturas()
    destinos = models.get_all_sites_whatsapp_recipients()
    ultima_por_site = {}
    for (site, _), pontos in data_reader.build_disease_concentration_lookup(data_reader.read_spore_counts()).items():
        if pontos:
            ultima_por_site[site] = max(ultima_por_site.get(site, ""), pontos[-1]["data"])
    hoje = date.today()
    rows = []
    for site in (set(data_reader.read_sites()) | set(virtual_by_name)) - models.get_deactivated_site_names():
        vf = virtual_by_name.get(site)
        so_clima = bool(vf) and vf.get("tipo") == "clima"
        tipo = "Só clima" if so_clima else ("Estimada (virtual)" if vf else "Real")
        ultima = ultima_por_site.get(site)
        dias = (hoje - date.fromisoformat(ultima)).days if ultima else None
        if so_clima:
            situacao = "Só clima"
        elif dias is None:
            situacao = "Sem leitura"
        elif dias > _DADOS_BLOQUEIO_DIAS:
            situacao = f"Sem leitura há +{_DADOS_BLOQUEIO_DIAS} dias"
        elif dias > _DADOS_AVISO_DIAS:
            situacao = "Atenção (leitura antiga)"
        else:
            situacao = "OK"
        escolha = overrides.get(site)
        if escolha and escolha["codigo"] == "__interp3__":
            estacao = "Média das 3 estações"
        elif escolha and escolha["codigo"]:
            estacao = escolha["codigo"]
        else:
            estacao = "Coordenada própria"
        lat, lon = coords.get(site, (None, None))
        rows.append([
            nome(site), tipo, countries.get_country(site_countries.get(site, countries.DEFAULT_COUNTRY))["nome"],
            situacao,
            *[(culturas.get((site, safra)) or {}).get("cultura") or "-" for safra, _ in models.SAFRAS],
            _data_excel(ultima) if ultima else None, dias,
            len(destinos.get(site, [])), estacao, lat, lon,
        ])
    rows.sort(key=lambda r: str(r[0]).lower())
    return rows


def _clima_esporos_rows(completar_clima=None):
    """Aba "Clima e Esporos": o relatorio diario (ver `_relatorio_diario_rows`)
    dos ultimos 90 dias, enxuto -- sem codigo de estacao, sem os 3 limites
    (viram o "Nivel") e sem as atividades de safra (estao na aba Manejo)."""
    nome = _nomes_exibicao()
    rows, so_atividade = [], {}
    desde = date.today() - timedelta(days=_HISTORICO_CLIMA_DIAS)
    for r in _relatorio_diario_rows(completar_clima, desde):
        data_, fazenda, _estacao, tmin, tmax, *resto = r
        if isinstance(data_, date) and data_ < desde:
            continue
        ur = resto[:len(UR_LIMIARES)]
        vento, doenca, conc, risco, warn, danger, _maximo, atividade, _detalhe = resto[len(UR_LIMIARES):]
        base = [data_, nome(fazenda), tmin, tmax, *ur, vento]
        if atividade and not doenca:
            so_atividade.setdefault((str(data_), fazenda), base)
            continue
        if conc is None:
            nivel = None
        elif danger is not None and conc >= danger:
            nivel = "Perigo"
        elif warn is not None and conc >= warn:
            nivel = "Atenção"
        else:
            nivel = "Baixo"
        rows.append(base + [doenca, conc, nivel, risco])
    ja = {(str(r[0]), r[1]) for r in rows}
    for (data_txt, fazenda), base in so_atividade.items():
        if (data_txt, base[1]) not in ja:
            rows.append(base + [None, None, None, None])
    rows.sort(key=lambda r: (str(r[1]).lower(), -_ordinal_para_ordenar(r[0].isoformat() if isinstance(r[0], date) else r[0])))
    return rows


def _manejo_rows():
    """Aba "Manejo": tudo que e' anotado na aba Manejos num historico so'
    -- cultura, plantio, aplicacoes, produtos (TS/Sulco/Folha) e as
    anotacoes por doenca."""
    nome = _nomes_exibicao()
    culturas = models.get_all_farm_culturas()

    def cultura(site, safra):
        return (culturas.get((site, safra)) or {}).get("cultura") or "-"

    rows = []
    for site, por_safra in models.get_all_farm_plantio().items():
        for safra, linhas in por_safra.items():
            for l in linhas:
                if any((l["data_plantio"], l["talhao"], l["variedade"], l["ciclo_dias"])):
                    rows.append([nome(site), SAFRA_LABELS.get(safra, safra), cultura(site, safra), "Plantio",
                                 _data_excel(l["data_plantio"]), l["talhao"], l["variedade"],
                                 f"Ciclo {l['ciclo_dias']} dias" if l["ciclo_dias"] else ""])
    for site, por_safra in models.get_all_farm_aplicacoes().items():
        for safra, linhas in por_safra.items():
            for l in linhas:
                if any((l["data_aplicacao"], l["talhao"], l["fungicidas_quimicos"], l["fungicidas_biologicos"])):
                    produtos = " / ".join(p for p in (l["fungicidas_quimicos"], l["fungicidas_biologicos"]) if p)
                    rows.append([nome(site), SAFRA_LABELS.get(safra, safra), cultura(site, safra), "Aplicação",
                                 _data_excel(l["data_aplicacao"]), l["talhao"], produtos, ""])
    for site, buckets in models.get_all_farm_produtos().items():
        for (safra, momento, tipo), linhas in buckets.items():
            if momento not in models.MOMENTOS:
                continue
            for l in linhas:
                if any((l["data_anotacao"], l["nome"], l["ingrediente_ativo"])):
                    rows.append([nome(site), SAFRA_LABELS.get(safra, safra), cultura(site, safra),
                                 f"Produto {MOMENTO_LABELS.get(momento, momento)} - {TIPO_LABELS.get(tipo, tipo)}",
                                 _data_excel(l["data_anotacao"]), "", l["nome"], l["ingrediente_ativo"]])
    for (site, doenca), nota in models.get_all_recommendation_notes().items():
        if nota:
            rows.append([nome(site), "-", "-", f"Anotação - {doenca}", None, "", "", nota])

    def _ordem(r):
        d = r[4]
        return (str(r[0]).lower(), r[1], -_ordinal_para_ordenar(d.isoformat() if isinstance(d, date) else d), r[3])
    rows.sort(key=_ordem)
    return rows


def _doencas_rows():
    """Aba "Doencas": nome, cientifico, onde vale (culturas/paises) e as
    condicoes de germinacao num texto so'."""
    info = models.get_all_disease_info()
    doenca_culturas = models.get_doenca_culturas()
    doenca_paises = models.get_doenca_paises()
    rows = []
    for doenca_en, d in info.items():
        partes = []
        if d["germ_temp_min"] is not None and d["germ_temp_max"] is not None:
            partes.append(f"{d['germ_temp_min']:g}–{d['germ_temp_max']:g} °C")
        if d["germ_ur_min"] is not None:
            partes.append(f"UR ≥ {d['germ_ur_min']:g}%")
        if d["germ_agua_livre_inibe"]:
            partes.append("água livre atrapalha")
        elif d["germ_molhamento_horas"] is not None:
            partes.append(f"{d['germ_molhamento_horas']:g} h de molhamento")
        rows.append([
            d["nome_pt"], d["nome_cientifico"] or "",
            "Manual" if models.is_manual_disease(doenca_en) else "BioScout",
            ", ".join(sorted(doenca_culturas.get(doenca_en, []))) or "Todas",
            ", ".join(sorted(doenca_paises.get(doenca_en, []))) or "Todos",
            " · ".join(partes) or "-",
        ])
    rows.sort(key=lambda r: (r[0] or "").lower())
    return rows


def _fungicidas_rows():
    """Aba "Fungicidas": a Biblioteca como esta' na tela (ordem dos botoes
    ▲▼, sem produto banido/sem registro nem "biologico" que nao e'
    microrganismo), com as culturas registradas num texto so'."""
    culturas_ativas = models.get_culturas_ativas()
    overrides = models.get_all_fungicida_overrides()
    bloqueios = models.get_all_fungicida_registro_bloqueado()
    rows = []
    for doenca_en, info in sorted(models.get_all_disease_translations().items(), key=lambda kv: kv[1]["nome_pt"]):
        rec = fungicida_data.get_recomendacao(doenca_en)
        if not rec:
            continue
        for tipo in ("quimico", "biologico"):
            itens = rec[tipo + "s"]["itens"]
            n = fungicida_data.n_linhas(tipo, len(itens))
            for idx in models.get_fungicida_ordem(doenca_en, tipo, n):
                override = overrides.get((doenca_en, tipo, idx))
                if override and override["removido"]:
                    continue
                ingrediente = override["ingrediente"] if override else (itens[idx]["ingrediente"] if idx < len(itens) else "")
                if not ingrediente:
                    continue
                if tipo == "quimico" and agrofit_dados.produto_sem_registro(ingrediente):
                    continue
                if tipo == "biologico" and not agrofit_dados.eh_biologico(ingrediente):
                    continue
                if tipo == "quimico":
                    bloq = bloqueios.get((doenca_en, tipo, idx), set())
                    registro = ", ".join(c for c in culturas_ativas if c not in bloq) or "Nenhuma"
                else:
                    registro = "-"
                rows.append([info["nome_pt"], TIPO_LABELS[tipo], ingrediente, registro])
    return rows


def _whatsapp_agenda_rows():
    """Aba "WhatsApp" (1o bloco): por fazenda, quem recebe e quando."""
    nome = _nomes_exibicao()
    destinos = models.get_all_sites_whatsapp_recipients()
    dias_texto = models.get_all_whatsapp_days()
    dias_pdf = models.get_all_whatsapp_days_pdf()
    clima = {vf["site_name"] for vf in models.get_all_virtual_farms() if vf.get("tipo") == "clima"}
    desativadas = models.get_deactivated_site_names()
    rows = []
    for site in sorted((set(dias_texto) | set(dias_pdf) | set(destinos)) - desativadas, key=lambda s: nome(s).lower()):
        h = models.get_whatsapp_schedule_horarios(site)

        def quando(dias, hora, minuto):
            return f"{', '.join(WEEKDAY_LABELS[d] for d in sorted(dias))} às {hora:02d}:{minuto:02d}" if dias else "-"
        rows.append([
            nome(site),
            ", ".join(f"{r['username']} ({models.fmt_telefone_br(r['telefone'])})" for r in destinos.get(site, [])) or "-",
            quando(dias_texto.get(site, []), h["texto"], h["texto_min"]),
            "-" if site in clima else quando(dias_pdf.get(site, []), h["pdf"], h["pdf_min"]),
        ])
    return rows


def _whatsapp_envios_rows():
    """Aba "WhatsApp" (2o bloco): envios dos ultimos 90 dias."""
    nome = _nomes_exibicao()
    corte = date.today() - timedelta(days=_HISTORICO_WHATSAPP_DIAS)
    rows = []
    for log in models.get_whatsapp_envio_log(limit=1_000_000):
        data_obj, hora_obj = _data_hora_excel(log["criado_em"])
        if isinstance(data_obj, date) and data_obj < corte:
            continue
        rows.append([
            data_obj, hora_obj, nome(log["site_name"]), log["destinatario"] or "",
            "Enviado" if log["ok"] else "Falha", "" if log["ok"] else (log["mensagem"] or ""),
        ])
    return rows


def _add_whatsapp_sheet(wb):
    agenda_cab = ["Fazenda", "Quem recebe", "Texto", "PDF"]
    envios_cab = ["Data", "Hora", "Fazenda", "Destinatário", "Status", "Motivo da falha"]
    try:
        agenda = _whatsapp_agenda_rows()
    except Exception as exc:
        agenda = [[f"Erro: {exc}", "", "", ""]]
    try:
        envios = _whatsapp_envios_rows()
    except Exception as exc:
        envios = [[f"Erro: {exc}", "", "", "", "", ""]]
    ws = wb.create_sheet(title="WhatsApp")
    ws.append(["Agenda e destinatários"])
    ws.cell(row=1, column=1).font = Font(bold=True, size=12)
    ws.append(agenda_cab)
    for cell in ws[ws.max_row]:
        cell.font, cell.fill = Font(bold=True, color="FFFFFF"), _CABECALHO_FUNDO
    for r in agenda:
        ws.append(_safe_row(r))
    ws.append([])
    ws.append([f"Envios dos últimos {_HISTORICO_WHATSAPP_DIAS} dias"])
    ws.cell(row=ws.max_row, column=1).font = Font(bold=True, size=12)
    ws.append(envios_cab)
    for cell in ws[ws.max_row]:
        cell.font, cell.fill = Font(bold=True, color="FFFFFF"), _CABECALHO_FUNDO
    inicio = ws.max_row + 1
    for r in envios:
        ws.append(_safe_row(r))
    for row_cells in ws.iter_rows(min_row=inicio, max_row=ws.max_row, min_col=1, max_col=2):
        if isinstance(row_cells[0].value, date):
            row_cells[0].number_format = "DD/MM/YYYY"
        if isinstance(row_cells[1].value, time):
            row_cells[1].number_format = "HH:MM"
    for col in ws.columns:
        length = max((len(str(c.value)) for c in col if c.value is not None), default=8)
        ws.column_dimensions[col[0].column_letter].width = min(max(length + 2, 10), 50)


def _sim_nao(valor):
    return "Sim" if valor else "Não"


def _cortesia_excel(valor):
    return "Sem prazo" if valor == models.SEM_PRAZO else _data_excel(valor)


def _clientes_rows():
    """Abas "Clientes" e "Planos" (pedido do Alan, 01/11/2026): um cliente do
    app por linha, com os dados do cadastro (/solicitar-acesso) e a
    assinatura -- mesma informacao da tela Gerenciar Clientes."""
    planos = {p["id"]: p for p in models.get_planos()}
    cadastros = {c["user_id"]: c for c in models.get_cadastros()}
    hoje = models._hoje_cuiaba()
    rows = []
    for c in sorted(models.get_all_clientes(), key=lambda c: (c["criado_em"] or ""), reverse=True):
        cad = cadastros.get(c["id"], {})
        plano = planos.get(c["plano_id"])
        perfil = ", ".join({"produtor": "Produtor", "tecnico": "Técnico do setor"}.get(p, p)
                           for p in (cad.get("perfil") or "").split(",") if p)
        cidade = (cad.get("cidade") or "") + (f"/{cad['uf']}" if cad.get("uf") else "")
        fim = models.acesso_ate(c)
        rows.append([
            cad.get("nome_completo") or c["username"], c["username"], models.fmt_telefone_br(c["telefone"]) or "",
            c["email"] or "", perfil, cad.get("empresa") or "", cidade, cad.get("culturas") or "",
            cad.get("area_ha") or "", _data_excel((cad.get("criado_em") or c["criado_em"] or "")[:10]),
            plano["nome"] if plano else "Sem plano escolhido",
            plano["valor"] if plano else "",
            models.CICLOS_PLANO.get(plano["ciclo"], (plano["ciclo"],))[0] if plano else "",
            {"pendente": "Aguardando pagamento", "ativa": "Ativa"}.get(c["status"], (c["status"] or "").capitalize()),
            _sim_nao(fim and fim >= hoje), _data_excel(c["pago_ate"]), _cortesia_excel(c["cortesia_ate"]),
            c["n_pontos"],
        ])
    return rows


def _planos_rows():
    rotulos = dict(models.RECURSOS_PLANO)
    clientes = models.get_all_clientes()
    hoje = models._hoje_cuiaba()
    rows = []
    for p in models.get_planos():
        do_plano = [c for c in clientes if c["plano_id"] == p["id"]]
        liberados = sum(1 for c in do_plano if (models.acesso_ate(c) or "") >= hoje)
        rows.append([
            p["nome"], p["valor"], models.CICLOS_PLANO.get(p["ciclo"], (p["ciclo"],))[0], _sim_nao(p["ativo"]),
            p["max_pontos"], p["raio_km"], ", ".join(rotulos.get(r, r) for r in p["recursos"]),
            len(do_plano), liberados,
        ])
    return rows


def build_workbook(completar_clima=None):
    wb = Workbook()
    wb.remove(wb.active)
    _try_sheet(
        wb, "Fazendas",
        ["Fazenda", "Tipo", "País", "Situação", "Cultura Safra", "Cultura 2ª Safra", "Cultura 3ª Safra",
         "Última leitura", "Dias sem leitura", "Recebem WhatsApp", "Estação de clima", "Latitude", "Longitude"],
        _fazendas_rows, date_columns=(7,),
    )
    _try_sheet(
        wb, "Clima e Esporos",
        ["Data", "Fazenda", "Temp mín (°C)", "Temp máx (°C)"]
        + [f"Horas UR ≥ {limiar}%" for limiar in UR_LIMIARES]
        + ["Vento predominante", "Doença", "Esporos/m³", "Nível", "Risco de infecção (%)"],
        lambda: _clima_esporos_rows(completar_clima), date_columns=(0,),
    )
    _try_sheet(
        wb, "Manejo",
        ["Fazenda", "Safra", "Cultura", "Registro", "Data", "Talhão", "Produto / Variedade", "Detalhe"],
        _manejo_rows, date_columns=(4,),
    )
    _try_sheet(wb, "Doenças", ["Doença", "Nome científico", "Origem", "Culturas", "Países", "Condições de germinação"], _doencas_rows)
    _try_sheet(wb, "Fungicidas", ["Doença", "Tipo", "Ingrediente ativo", "Registrado para"], _fungicidas_rows)
    try:
        _add_whatsapp_sheet(wb)
    except Exception as exc:
        _write_sheet(wb, "WhatsApp (erro)", ["Erro"], [[str(exc)]])
    _try_sheet(
        wb, "Usuários",
        ["Usuário", "Tipo", "Dono (se subordinado)", "Email", "Telefone", "Admin", "Fazendas liberadas", "Recebe relatório"],
        _usuarios_rows,
    )
    _try_sheet(
        wb, "Clientes",
        ["Cliente", "Usuário", "Celular", "E-mail", "Perfil", "Fazenda/empresa", "Cidade", "Culturas", "Área (ha)",
         "Cadastrado em", "Plano", "Valor do plano (R$)", "Ciclo", "Situação", "Acesso liberado", "Pago até",
         "Cortesia até", "Lavouras"],
        _clientes_rows, date_columns=(9, 15, 16),
    )
    _try_sheet(
        wb, "Planos",
        ["Plano", "Valor (R$)", "Ciclo", "À venda", "Lavouras (máx.)", "Raio (km)", "Abas liberadas",
         "Clientes no plano", "Clientes com acesso liberado"],
        _planos_rows,
    )
    for aba, coluna in (("Clientes", "L"), ("Planos", "B")):  # valores em R$
        for celula in wb[aba][coluna][1:]:
            if isinstance(celula.value, (int, float)):
                celula.number_format = '"R$" #,##0.00'
    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer
