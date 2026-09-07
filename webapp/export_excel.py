"""Gera o relatorio Excel (.xlsx) com o MAXIMO de informacao possivel do
site: cadastro (usuarios/subordinados), Fazendas (cadastro/produtos/
plantio/aplicacoes), Manejo das 3 safras (cultura, estoque rapido,
anotacoes), Doencas (traducao/germinacao/culturas/paises), Culturas,
WhatsApp (historico/destinatarios/agenda), Fungicidas (biblioteca
completa) e Relatorio Diario (clima + concentracao de esporos e risco
de infeccao, dia a dia) -- exportacao restrita a `ALAN_MAURO_USERNAME`,
ver `admin_exportar` em `app.py`."""
import io

from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Font

import countries
import data_reader
import fungicida_data
import models

UR_LIMIARES = (80, 85, 90, 95)

MOMENTO_LABELS = {"ts": "TS", "sulco": "Sulco", "folha": "Folha"}
TIPO_LABELS = {"quimico": "Quimico", "biologico": "Biologico"}
SAFRA_LABELS = dict(models.SAFRAS)
WEEKDAY_LABELS = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sab", "Dom"]  # 0=Segunda ... 6=Domingo


def _safe_row(row):
    """Remove caractere de controle invalido em XML (0x00-0x08, 0x0B-0x0C,
    0x0E-0x1F) de qualquer valor de texto na linha -- sem isso, um unico
    caractere assim (ex.: copiado de uma mensagem de erro do
    whatsapp-bridge, ou de um campo de anotacao livre) faz
    `ws.append` levantar `IllegalCharacterError` e derruba a exportacao
    INTEIRA (mesmo quem so' queria uma aba sem nada a ver com o campo
    problematico -- ja aconteceu, ver conversa)."""
    return [ILLEGAL_CHARACTERS_RE.sub("", v) if isinstance(v, str) else v for v in row]


def _write_sheet(wb, title, headers, rows):
    ws = wb.create_sheet(title=title)
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for row in rows:
        ws.append(_safe_row(row))
    for col in ws.columns:
        length = max((len(str(c.value)) for c in col if c.value is not None), default=8)
        ws.column_dimensions[col[0].column_letter].width = min(max(length + 2, 10), 50)


def _try_sheet(wb, title, headers, rows_fn):
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
    _write_sheet(wb, title, headers, rows)


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


def _fazendas_cadastro_rows():
    """Uma linha por fazenda (real ou virtual/estimada) com tudo que a
    aba Fazendas/Mapa Interpolado deixa configurar: nome de exibicao,
    tipo, pais, coordenada, estacao de referencia escolhida e anotacao
    de clima (quando houver)."""
    site_countries = models.get_all_site_countries()
    display_names = models.get_all_site_display_names()
    overrides = models.get_all_weather_station_overrides()
    virtual_by_name = {vf["site_name"]: vf for vf in models.get_all_virtual_farms()}
    notes = models.get_all_site_climate_notes()
    coords = dict(data_reader.read_site_coordinates())
    for vf in virtual_by_name.values():
        coords[vf["site_name"]] = (vf["lat"], vf["lon"])
    todos = sorted(set(data_reader.read_sites()) | set(virtual_by_name))

    rows = []
    for site in todos:
        vf = virtual_by_name.get(site)
        if vf:
            tipo = "Virtual - Clima" if vf.get("tipo") == "clima" else "Virtual - Doenca"
            nome_exibicao = vf["nome"]
            raio_km = vf.get("raio_km")
            criado_em = models.fmt_data_br(vf.get("criado_em")) or ""
            criado_por = vf.get("criado_por") or ""
        else:
            tipo = "Real"
            nome_exibicao = display_names.get(site) or ""
            raio_km = ""
            criado_em = ""
            criado_por = ""
        pais_code = site_countries.get(site, countries.DEFAULT_COUNTRY)
        pais_nome = countries.get_country(pais_code)["nome"]
        lat, lon = coords.get(site, (None, None))
        escolha = overrides.get(site)
        if escolha and escolha["codigo"]:
            estacao = f'{escolha["codigo"]} ({countries.get_country(escolha["country_code"])["nome"]})'
        else:
            estacao = "Coordenada propria"
        rows.append([
            site, nome_exibicao, tipo, pais_nome, lat, lon, estacao,
            raio_km, criado_em, criado_por, notes.get(site, ""),
        ])
    return rows


def _fazendas_produtos_rows():
    rows = []
    for site_name, buckets in models.get_all_farm_produtos().items():
        for (safra, momento, tipo), linhas in buckets.items():
            if momento not in models.MOMENTOS:
                continue  # momento "geral" (estoque rapido) vai na aba de Manejo
            for linha in linhas:
                rows.append([
                    site_name, SAFRA_LABELS.get(safra, safra), MOMENTO_LABELS.get(momento, momento),
                    TIPO_LABELS.get(tipo, tipo), linha["data_anotacao"], linha["nome"], linha["ingrediente_ativo"],
                ])
    rows.sort(key=lambda r: (r[0].lower(), r[1], r[2], r[3]))
    return rows


def _fazendas_plantio_rows():
    rows = []
    for site_name, por_safra in models.get_all_farm_plantio().items():
        for safra, linhas in por_safra.items():
            for linha in linhas:
                rows.append([
                    site_name, SAFRA_LABELS.get(safra, safra),
                    linha["data_plantio"], linha["talhao"], linha["variedade"], linha["ciclo_dias"],
                ])
    rows.sort(key=lambda r: (r[0].lower(), r[1]))
    return rows


def _fazendas_aplicacoes_rows():
    rows = []
    for site_name, por_safra in models.get_all_farm_aplicacoes().items():
        for safra, linhas in por_safra.items():
            for linha in linhas:
                rows.append([
                    site_name, SAFRA_LABELS.get(safra, safra), linha["data_aplicacao"], linha["talhao"],
                    linha["fungicidas_quimicos"], linha["fungicidas_biologicos"],
                ])
    rows.sort(key=lambda r: (r[0].lower(), r[1]))
    return rows


def _manejo_cultura_rows():
    rows = []
    for (site_name, safra), info in models.get_all_farm_culturas().items():
        rows.append([site_name, SAFRA_LABELS.get(safra, safra), info["cultura"] or "", models.fmt_data_br(info["updated_at"]) or ""])
    rows.sort(key=lambda r: (r[0].lower(), r[1]))
    return rows


def _manejo_estoque_rows():
    rows = []
    for site_name, buckets in models.get_all_farm_produtos().items():
        for (safra, momento, tipo), linhas in buckets.items():
            if momento != models.MOMENTO_ESTOQUE_RAPIDO:
                continue
            for linha in linhas:
                rows.append([
                    site_name, SAFRA_LABELS.get(safra, safra), TIPO_LABELS.get(tipo, tipo),
                    linha["data_anotacao"], linha["nome"],
                ])
    rows.sort(key=lambda r: (r[0].lower(), r[1], r[2]))
    return rows


def _manejo_anotacoes_rows():
    rows = [[site_name, doenca, nota or ""] for (site_name, doenca), nota in models.get_all_recommendation_notes().items() if nota]
    rows.sort(key=lambda r: (r[0].lower(), r[1]))
    return rows


def _doencas_rows():
    """Mesmo dado da aba Doencas: traducao, nome cientifico, condicoes de
    germinacao (base da luz de risco) e as marcacoes das matrizes
    Doencas x Culturas / Doencas x Pais."""
    info = models.get_all_disease_info()
    doenca_culturas = models.get_doenca_culturas()
    doenca_paises = models.get_doenca_paises()
    rows = []
    for doenca_en, d in info.items():
        culturas = ", ".join(sorted(doenca_culturas.get(doenca_en, []))) or "-"
        paises = ", ".join(sorted(doenca_paises.get(doenca_en, []))) or "-"
        rows.append([
            d["nome_pt"], doenca_en, d["nome_cientifico"] or "", culturas, paises,
            d["germ_temp_min"], d["germ_temp_max"], d["germ_ur_min"], d["germ_molhamento_horas"],
            "Sim" if d["germ_agua_livre_inibe"] else "Nao",
        ])
    rows.sort(key=lambda r: (r[0] or "").lower())
    return rows


def _culturas_rows():
    return [[i + 1, nome] for i, nome in enumerate(models.get_culturas()) if nome]


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


def _relatorio_diario_rows():
    """Uma linha por (estacao/fazenda, dia, doenca) -- clima do dia (temp,
    UR, vento, igual sempre) MAIS a concentracao de esporos (leitura real
    do BioScout, mesma fonte da aba Graficos) e o risco de infeccao
    calculado daquele dia (so' clima x germinacao, 0-100%, mesma conta do
    grafico de Risco de Infeccao da aba Graficos) pra' cada doenca com
    leitura naquele dia -- pedido explicito do usuario de ter doenca e
    clima juntos na mesma aba, nao numa aba separada. Fazenda/dia sem
    NENHUMA leitura de doenca ainda vira 1 linha so' com essas 3 colunas
    em branco (nao perde o clima so' por falta de doenca cadastrada);
    fazenda/dia com N doencas com leitura vira N linhas (clima repetido),
    ordenadas por nome de doenca dentro do mesmo dia."""
    report = data_reader.build_daily_weather_report(data_reader.read_weather(), UR_LIMIARES)
    translations = models.get_all_disease_translations()
    spore_lookup = data_reader.build_disease_concentration_lookup(data_reader.read_spore_counts())
    hourly_lookup = data_reader.build_hourly_weather_lookup(data_reader.read_weather())
    device_by_site = data_reader.read_site_device_ids()
    device_to_site = {device: site for site, device in device_by_site.items()}

    # (site, dia) -> lista de (nome_pt, concentracao, risco_pct), ordenada
    # por nome de doenca -- montada uma vez so', fora do loop principal.
    doencas_por_site_dia = {}
    for (site, doenca_en), pontos in spore_lookup.items():
        info = translations.get(doenca_en, {})
        nome_pt = info.get("nome_pt") or doenca_en
        device = device_by_site.get(site)
        for p in pontos:
            horas = hourly_lookup.get((device, p["data"]))
            risco_pct = _calc_risco_diario_pct(info, horas) if horas else None
            conc = p["concentracao"]
            doencas_por_site_dia.setdefault((site, p["data"]), []).append(
                (nome_pt, round(conc, 1) if conc is not None else None, risco_pct)
            )
    for lista in doencas_por_site_dia.values():
        lista.sort(key=lambda t: t[0].lower())

    rows = []
    for r in report:
        base = [
            models.fmt_data_br(r["data"]) or r["data"], r["estacao"],
            r["temp_min"], r["temp_max"],
        ] + [r["ur_counts"][limiar] for limiar in UR_LIMIARES] + [r["vento_predominante"] or "-"]
        site = device_to_site.get(r["estacao"])
        doencas = doencas_por_site_dia.get((site, r["data"]), []) if site else []
        if not doencas:
            rows.append(base + [None, None, None])
            continue
        for nome_pt, conc, risco_pct in doencas:
            rows.append(base + [nome_pt, conc, risco_pct])
    return rows


def _add_whatsapp_sheet(wb):
    """Uma aba so' com os tres blocos da tela "Relatorio WhatsApp" + a
    agenda de envio: historico de envios (data/hora, fazenda,
    destinatario, status), quem esta cadastrado pra receber cada
    fazenda, e em quais dias da semana (texto/PDF, cada um com sua
    propria agenda)."""
    ws = wb.create_sheet(title="WhatsApp")

    ws.append(["Historico de Envios"])
    ws.cell(row=ws.max_row, column=1).font = Font(bold=True, size=12)
    ws.append(["Data/Hora", "Fazenda", "Destinatario", "Telefone", "Status", "Detalhe"])
    for cell in ws[ws.max_row]:
        cell.font = Font(bold=True)
    for log in models.get_whatsapp_envio_log(limit=1_000_000):
        ws.append(_safe_row([
            models.fmt_data_br(log["criado_em"]) or "",
            log["site_name"], log["destinatario"] or "", models.fmt_telefone_br(log["telefone"]) or "",
            "Enviado" if log["ok"] else "Falha", log["mensagem"] or "",
        ]))

    # Fazenda real (tabela `sites`) + virtual/estimada -- as duas podem
    # ter destinatario/agenda proprios (`_send_site_whatsapp` funciona
    # pra qualquer site_name, real ou nao).
    site_names = sorted({s["site_name"] for s in models.get_all_sites()} | models.virtual_farm_site_names())

    ws.append([])
    ws.append(["Cadastrados para Receber"])
    ws.cell(row=ws.max_row, column=1).font = Font(bold=True, size=12)
    ws.append(["Fazenda", "Destinatario", "Telefone"])
    for cell in ws[ws.max_row]:
        cell.font = Font(bold=True)
    for site_name in site_names:
        for r in models.get_site_whatsapp_recipients(site_name):
            ws.append(_safe_row([site_name, r["username"], models.fmt_telefone_br(r["telefone"])]))

    ws.append([])
    ws.append(["Agenda de Envio"])
    ws.cell(row=ws.max_row, column=1).font = Font(bold=True, size=12)
    ws.append(["Fazenda", "Dias (Texto)", "Dias (PDF)"])
    for cell in ws[ws.max_row]:
        cell.font = Font(bold=True)
    dias_texto = models.get_all_whatsapp_days()
    dias_pdf = models.get_all_whatsapp_days_pdf()
    for site_name in site_names:
        txt = ", ".join(WEEKDAY_LABELS[d] for d in sorted(dias_texto.get(site_name, []))) or "-"
        pdf = ", ".join(WEEKDAY_LABELS[d] for d in sorted(dias_pdf.get(site_name, []))) or "-"
        ws.append([site_name, txt, pdf])

    for col in ws.columns:
        length = max((len(str(c.value)) for c in col if c.value is not None), default=8)
        ws.column_dimensions[col[0].column_letter].width = min(max(length + 2, 10), 50)


def _add_fungicidas_sheet(wb, culturas_ativas):
    """Uma aba com o mesmo dado da tela "Relatorio Fungicidas": um quimico
    por linha (doenca + ingrediente) e uma coluna por cultura ativa com
    "Sim"/"Nao" conforme o checkbox "Registrado para"."""
    overrides = models.get_all_fungicida_overrides()
    registro_bloqueado = models.get_all_fungicida_registro_bloqueado()
    translations = models.get_all_disease_translations()

    rows = []
    for doenca_en, info in sorted(translations.items(), key=lambda kv: kv[1]["nome_pt"]):
        rec = fungicida_data.get_recomendacao(doenca_en)
        if not rec:
            continue
        for tipo, tipo_label in (("quimico", "Quimico"), ("biologico", "Biologico")):
            grupo = rec[tipo + "s"]
            n = len(grupo["itens"])
            ordem = models.get_fungicida_ordem(doenca_en, tipo, n)
            for idx in ordem:
                item = grupo["itens"][idx]
                override = overrides.get((doenca_en, tipo, idx))
                ingrediente = override["ingrediente"] if override else item["ingrediente"]
                classe_raw = (override["classe"] if override else item["classe"]) or ""
                classe = fungicida_data.CLASSE_LABEL.get(classe_raw, classe_raw)
                if tipo == "quimico":
                    culturas_bloqueadas = registro_bloqueado.get((doenca_en, tipo, idx), set())
                    registro = ["Nao" if c in culturas_bloqueadas else "Sim" for c in culturas_ativas]
                else:
                    # Registro por cultura so' e' rastreado pra quimico
                    # (ver "Registrado para" na aba Fungicidas) --
                    # biologico nunca tem essa marcacao.
                    registro = ["-" for _ in culturas_ativas]
                rows.append([info["nome_pt"], tipo_label, ingrediente, classe] + registro)

    _write_sheet(wb, "Fungicidas", ["Doenca", "Tipo", "Ingrediente ativo", "Classe"] + culturas_ativas, rows)


def build_workbook():
    wb = Workbook()
    wb.remove(wb.active)

    _try_sheet(
        wb, "Usuarios",
        ["Usuario", "Tipo", "Dono (se subordinado)", "Email", "Telefone", "Admin", "Fazendas liberadas", "Recebe relatorio"],
        _usuarios_rows,
    )
    _try_sheet(
        wb, "Fazendas - Cadastro",
        ["Fazenda (site_name)", "Nome de exibicao", "Tipo", "Pais", "Latitude", "Longitude",
         "Estacao de referencia", "Raio (km, se virtual)", "Criado em (se virtual)", "Criado por (se virtual)",
         "Anotacao de clima"],
        _fazendas_cadastro_rows,
    )
    _try_sheet(wb, "Fazendas - Produtos", ["Fazenda", "Safra", "Momento", "Tipo", "Data/Anotacao", "Nome do produto", "Ingrediente ativo"], _fazendas_produtos_rows)
    _try_sheet(wb, "Fazendas - Plantio", ["Fazenda", "Safra", "Data plantio", "Talhao", "Variedade", "Ciclo (dias)"], _fazendas_plantio_rows)
    _try_sheet(wb, "Fazendas - Aplicacoes", ["Fazenda", "Safra", "Data aplicacao", "Talhao", "Fungicidas quimicos", "Fungicidas biologicos"], _fazendas_aplicacoes_rows)
    _try_sheet(wb, "Manejo - Cultura", ["Fazenda", "Safra", "Cultura", "Atualizado em"], _manejo_cultura_rows)
    _try_sheet(wb, "Manejo - Estoque rapido", ["Fazenda", "Safra", "Tipo", "Data/Anotacao", "Nome do produto"], _manejo_estoque_rows)
    _try_sheet(wb, "Manejo - Anotacoes", ["Fazenda", "Doenca", "Nota"], _manejo_anotacoes_rows)
    _try_sheet(
        wb, "Doencas",
        ["Nome (site)", "Nome (BioScout, EN)", "Nome cientifico", "Culturas", "Paises",
         "Germ. temp min (C)", "Germ. temp max (C)", "Germ. UR min (%)", "Germ. molhamento (h)", "Agua livre inibe"],
        _doencas_rows,
    )
    _try_sheet(wb, "Culturas", ["Slot", "Nome"], _culturas_rows)
    try:
        _add_whatsapp_sheet(wb)
    except Exception as exc:
        _write_sheet(wb, "WhatsApp (erro)", ["Erro"], [[str(exc)]])
    try:
        _add_fungicidas_sheet(wb, models.get_culturas_ativas())
    except Exception as exc:
        _write_sheet(wb, "Fungicidas (erro)", ["Erro"], [[str(exc)]])
    _try_sheet(
        wb, "Relatorio Diario",
        ["Data", "Estacao", "Temp min (C)", "Temp max (C)"]
        + [f"Horas UR>={limiar}%" for limiar in UR_LIMIARES]
        + ["Vento predominante", "Doenca", "Concentracao (esporos/m3)", "Risco de Infeccao (%)"],
        _relatorio_diario_rows,
    )

    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer
