"""Gera o PDF de recomendacao de uma fazenda (aba Recomendacoes/Manejo) --
mesmo conteudo do relatorio de WhatsApp (clima, cultura, doencas em
Atencao/Perigo com recomendacao e anotacao, produtos ja disponiveis na
fazenda) mais as datas de plantio e de pulverizacao (aba Fazendas), pra
poder ser encaminhado por email ou impresso. Ver `recommendation_pdf` em
`app.py`."""
import io

import models
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.graphics.shapes import Drawing, Path, Rect, String
from reportlab.platypus import (
    KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

AZUL_MARCA = colors.HexColor("#0a1f44")
CINZA_CLARO = colors.HexColor("#f4f6f4")

VERDE_FAIXA = colors.HexColor("#d7ecd2")
AMARELO_FAIXA = colors.HexColor("#fdf0c4")
VERMELHO_FAIXA = colors.HexColor("#f6d4d0")

_styles = getSampleStyleSheet()
_ESTILO_TITULO = ParagraphStyle(
    "TituloFazenda", parent=_styles["Heading1"], textColor=AZUL_MARCA, fontSize=16, spaceAfter=2,
)
_ESTILO_SUBTITULO = ParagraphStyle(
    "Subtitulo", parent=_styles["Normal"], textColor=colors.grey, fontSize=9, spaceAfter=10,
)
_ESTILO_RODAPE = ParagraphStyle(
    "Rodape", parent=_styles["Normal"], textColor=colors.grey, fontSize=8, spaceAfter=3,
)
_ESTILO_SECAO = ParagraphStyle(
    "Secao", parent=_styles["Heading2"], textColor=AZUL_MARCA, fontSize=12, spaceBefore=12, spaceAfter=4,
)
_ESTILO_NORMAL = ParagraphStyle("NormalPdf", parent=_styles["Normal"], fontSize=9.5, leading=13)
_ESTILO_DOENCA = ParagraphStyle(
    "Doenca", parent=_styles["Normal"], fontSize=10.5, leading=14, spaceBefore=6, spaceAfter=2,
)
_ESTILO_GERMINACAO = ParagraphStyle(
    "Germinacao", parent=_styles["Normal"], fontSize=8.5, textColor=colors.grey, spaceAfter=2,
)
_ESTILO_RISCO_CLIMATICO = ParagraphStyle(
    "RiscoClimatico", parent=_styles["Normal"], fontSize=10.5, leading=14, spaceAfter=2,
)
_ESTILO_PREVISAO_RISCO = ParagraphStyle(
    "PrevisaoRisco", parent=_styles["Normal"], fontSize=8.5, textColor=colors.grey, spaceAfter=2,
)

_RISCO_LABELS = {"vermelho": "Alto", "amarelo": "Médio", "verde": "Baixo"}
_RISCO_CORES = {"vermelho": "#c0392b", "amarelo": "#b7860b", "verde": "#2e7d32"}
_STATUS_LABELS = {"Perigo": "Alta concentração de esporos", "Atencao": "Moderada concentração de esporos"}


def _linha_suave(pontos):
    """Path com curvas suaves (Catmull-Rom convertido pra Bezier cubica)
    passando exatamente pelos pontos [(x0,y0), (x1,y1), ...] -- em vez de
    uma polilinha com angulos retos em cada ponto."""
    p = Path(strokeColor=AZUL_MARCA, strokeWidth=1.0, fillColor=None)
    n = len(pontos)
    p.moveTo(*pontos[0])
    for i in range(n - 1):
        p0 = pontos[i - 1] if i > 0 else pontos[i]
        p1 = pontos[i]
        p2 = pontos[i + 1]
        p3 = pontos[i + 2] if i + 2 < n else pontos[i + 1]
        c1 = (p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6)
        c2 = (p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6)
        p.curveTo(c1[0], c1[1], c2[0], c2[1], p2[0], p2[1])
    return p


def _build_spore_chart(historico, largura=17.4 * cm, altura=4.5 * cm):
    """Graficozinho de concentracao de esporos ao longo do tempo, com as
    mesmas faixas verde/amarelo/vermelho do dashboard do BioScout (fixas
    por doenca -- warningConcentrationThreshold/dangerConcentrationThreshold/
    maximumConcentrationThreshold, ver `data_reader.get_site_disease_history`).
    Desenhado na mao com formas do reportlab (sem lib de grafico externa,
    ja que reportlab ja e' dependencia do projeto) -- so' uma linha (a
    fazenda deste relatorio), nao comparando com outras fazendas."""
    if len(historico) < 2:
        return None
    warn = historico[-1]["warn"] or 0
    danger = historico[-1]["danger"] or (warn * 2 or 1)
    maximo = historico[-1]["maximo"] or (danger * 1.5)
    topo = max(maximo, max(h["concentracao"] for h in historico) * 1.05)

    margem_esq, margem_dir, margem_topo, margem_baixo = 0.7 * cm, 0.2 * cm, 0.3 * cm, 0.9 * cm
    plot_w = largura - margem_esq - margem_dir
    plot_h = altura - margem_topo - margem_baixo
    escala_y = plot_h / topo if topo else 0

    d = Drawing(largura, altura)

    def y(valor):
        return margem_baixo + min(valor, topo) * escala_y

    d.add(Rect(margem_esq, y(0), plot_w, y(warn) - y(0), fillColor=VERDE_FAIXA, strokeColor=None))
    d.add(Rect(margem_esq, y(warn), plot_w, y(danger) - y(warn), fillColor=AMARELO_FAIXA, strokeColor=None))
    d.add(Rect(margem_esq, y(danger), plot_w, y(topo) - y(danger), fillColor=VERMELHO_FAIXA, strokeColor=None))

    n = len(historico)
    escala_x = plot_w / (n - 1) if n > 1 else 0
    pontos = [(margem_esq + i * escala_x, y(h["concentracao"])) for i, h in enumerate(historico)]
    d.add(_linha_suave(pontos))

    for valor in sorted({0, warn, danger, topo}):
        d.add(String(margem_esq - 4, y(valor) - 2.5, f"{valor:g}", fontSize=6.5, fillColor=colors.grey, textAnchor="end"))

    passo_label = max(1, n // 6)
    for i, h in enumerate(historico):
        if i % passo_label == 0 or i == n - 1:
            x = margem_esq + i * escala_x
            d.add(String(x, 2, f"{h['data'][8:10]}/{h['data'][5:7]}", fontSize=6.5, fillColor=colors.grey, textAnchor="middle"))

    return d


def _fmt_ingrediente(item, classe_label):
    if item.get("classe"):
        return f"{item['ingrediente']} ({classe_label.get(item['classe'], item['classe'])})"
    return item["ingrediente"]


def _caixa(conteudo, fundo=None):
    """Envolve `conteudo` (lista de flowables) numa caixa de largura
    total -- com fundo cinza claro (`fundo=CINZA_CLARO`) ou so com borda,
    sem preenchimento (`fundo=None`, transparente)."""
    caixa = Table([[conteudo]], colWidths=[17.4 * cm])
    estilo = [
        ("BOX", (0, 0), (-1, -1), 0.5, colors.lightgrey),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]
    if fundo:
        estilo.append(("BACKGROUND", (0, 0), (-1, -1), fundo))
    caixa.setStyle(TableStyle(estilo))
    return caixa


def _caixa_recomendacao_instituicoes(biologicos_itens, quimicos_itens, classe_label):
    """Caixa (sem preenchimento -- so a borda) com os ingredientes
    recomendados pelas instituicoes de pesquisa (biblioteca da aba
    Fungicidas), pra deixar claro que essa parte NAO e' uma anotacao da
    fazenda (essa sim, com fundo cinza -- ver `_caixa`)."""
    linhas = [Paragraph("Recomendações das Instituições de Pesquisa", ParagraphStyle(
        "RecInst", parent=_ESTILO_NORMAL, fontName="Helvetica-Bold", spaceAfter=3,
    ))]
    if biologicos_itens:
        ativos = " // ".join(_fmt_ingrediente(p, classe_label) for p in biologicos_itens)
        linhas.append(Paragraph(f"<b>Biologicos:</b> {ativos}", _ESTILO_NORMAL))
    if quimicos_itens:
        ativos = " // ".join(_fmt_ingrediente(p, classe_label) for p in quimicos_itens)
        linhas.append(Paragraph(f"<b>Quimicos:</b> {ativos}", _ESTILO_NORMAL))
    return _caixa(linhas, fundo=None)


def _tabela_padrao(headers, rows, col_widths):
    data = [headers] + rows
    tbl = Table(data, colWidths=col_widths, repeatRows=1)
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), AZUL_MARCA),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.lightgrey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, CINZA_CLARO]),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return tbl


def build_recommendation_pdf(
    nome_fazenda, safra_label, diseases, weather=None, produtos=None,
    cultura=None, plantio_linhas=None, aplicacoes_linhas=None, rodape_data="",
):
    """`diseases`/`weather`/`produtos`/`cultura` tem o mesmo formato usado
    em `_format_whatsapp_message` (ver app.py); `plantio_linhas` e
    `aplicacoes_linhas` vem de `models.get_all_farm_plantio`/
    `get_all_farm_aplicacoes` (lista de dicts), ja filtrados pra
    fazenda+safra. Retorna um `io.BytesIO` com o PDF pronto."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=1.8 * cm, rightMargin=1.8 * cm, topMargin=1.5 * cm, bottomMargin=1.5 * cm,
        title=f"Recomendacao - {nome_fazenda}",
    )
    story = []

    story.append(Paragraph(f"{nome_fazenda} - OneAgro", _ESTILO_TITULO))
    subtitulo = f"Manejo {safra_label} - {cultura.upper()}" if cultura else f"Manejo {safra_label}"
    story.append(Paragraph(subtitulo, _ESTILO_SUBTITULO))

    if weather:
        partes = []
        if weather.get("temperatura_atual") is not None:
            partes.append(f"Temp.: {weather['temperatura_atual']}°C")
        if weather.get("umidade_atual") is not None:
            partes.append(f"Umidade: {weather['umidade_atual']}%")
        if weather.get("chuva_atual_mm") is not None:
            partes.append(f"Chuva agora: {weather['chuva_atual_mm']} mm")
        if partes:
            story.append(Paragraph("<b>Clima agora:</b> " + " &nbsp;·&nbsp; ".join(partes), _ESTILO_NORMAL))
        if weather.get("previsao_5_dias"):
            prev = " &nbsp;|&nbsp; ".join(
                f"<b>{d['data'][8:10]}/{d['data'][5:7]}</b>: {d['chuva_mm']}mm ({d['temp_min']}-{d['temp_max']}°C)"
                for d in weather["previsao_5_dias"]
            )
            story.append(Paragraph(f"<b>Previsao:</b> {prev}", _ESTILO_NORMAL))
        story.append(Spacer(1, 4))

    story.append(Paragraph("Doencas em Atencao / Perigo", _ESTILO_SECAO))
    fontes_pesquisadas = []
    if not diseases:
        story.append(Paragraph("Nenhuma doenca em Atencao ou Perigo nessa fazenda no momento.", _ESTILO_NORMAL))
    else:
        for d in diseases:
            cabecalho = []
            cor_hex = "#ff6b6b" if d["status"] == "Perigo" else "#e6ac00"
            cabecalho.append(Paragraph(
                f'<font color="{cor_hex}">●</font> <b>{d["rotulo"].upper()}</b> — '
                f'{_STATUS_LABELS.get(d["status"], d["status"])} — Contagem: {d["concentracao"]} esporos/m³',
                _ESTILO_DOENCA,
            ))
            risco_label = _RISCO_LABELS.get(d.get("risco"))
            if risco_label:
                cor_risco = _RISCO_CORES.get(d.get("risco"), "#666666")
                cabecalho.append(Paragraph(
                    f'<font color="{cor_risco}">●</font> Risco climático: <b>{risco_label}</b>',
                    _ESTILO_RISCO_CLIMATICO,
                ))
                previsao_risco = d.get("previsao_risco")
                if previsao_risco:
                    dias_txt = " &nbsp;·&nbsp; ".join(
                        f'<font color="{_RISCO_CORES.get(p["risco"], "#666666")}">●</font> '
                        f'{p["rotulo_dia"]} {_RISCO_LABELS.get(p["risco"], "")}'
                        for p in previsao_risco
                    )
                    cabecalho.append(Paragraph(f"Previsão: {dias_txt}", _ESTILO_PREVISAO_RISCO))
            elif d.get("cientifico"):
                cabecalho.append(Paragraph(d["cientifico"], _ESTILO_GERMINACAO))
            grafico = _build_spore_chart(d.get("historico") or [])
            if grafico:
                cabecalho.append(Spacer(1, 2))
                cabecalho.append(grafico)
            story.append(KeepTogether(cabecalho))
            biologicos_itens = d["biologicos"]["itens"][:3] if d.get("biologicos") else None
            quimicos_itens = d["quimicos"]["itens"][:3] if d.get("quimicos") else None
            if biologicos_itens or quimicos_itens:
                story.append(Spacer(1, 3))
                story.append(_caixa_recomendacao_instituicoes(biologicos_itens, quimicos_itens, d["classe_label"]))
                story.append(Spacer(1, 3))
                for grupo in (d.get("biologicos"), d.get("quimicos")):
                    if grupo and grupo.get("fonte"):
                        fontes_pesquisadas.append(grupo["fonte"].split(" -- ")[0])
            story.append(Spacer(1, 3))
            story.append(_caixa(
                [Paragraph(f"Sugestão: {d.get('nota') or '-'}", _ESTILO_NORMAL)],
                fundo=CINZA_CLARO,
            ))

    produtos = produtos or {}
    story.append(Paragraph("Produtos ja disponiveis na fazenda", _ESTILO_SECAO))
    tem_produtos = any(produtos.get(tipo) for tipo in ("quimico", "biologico"))
    if not tem_produtos:
        conteudo = [Paragraph("Nenhum produto cadastrado para esta fazenda.", _ESTILO_NORMAL)]
    else:
        conteudo = []
        for tipo, titulo in (("biologico", "Biologicos"), ("quimico", "Quimicos")):
            itens = produtos.get(tipo) or []
            partes = [
                f"{p['nome']} ({p['data_anotacao']})" if p.get("data_anotacao") else p["nome"]
                for p in itens if p.get("nome")
            ]
            conteudo.append(Paragraph(f"<b>{titulo}:</b> {', '.join(partes) if partes else '-'}", _ESTILO_NORMAL))
    story.append(_caixa(conteudo, fundo=CINZA_CLARO))

    plantio_linhas = [l for l in (plantio_linhas or []) if any(l.values())]
    story.append(Paragraph("Datas de Plantio", _ESTILO_SECAO))
    if not plantio_linhas:
        story.append(_caixa([Paragraph("Nenhum plantio cadastrado para esta safra.", _ESTILO_NORMAL)], fundo=CINZA_CLARO))
    else:
        rows = [
            [models.fmt_data_br(l["data_plantio"]) or "-", l["talhao"] or "-", l["variedade"] or "-", l["ciclo_dias"] or "-"]
            for l in plantio_linhas
        ]
        story.append(_tabela_padrao(
            ["Data", "Talhao", "Variedade", "Ciclo (dias)"], rows,
            [3.2 * cm, 4.5 * cm, 6 * cm, 3.2 * cm],
        ))

    aplicacoes_linhas = [l for l in (aplicacoes_linhas or []) if any(l.values())]
    story.append(Paragraph("Datas de Pulverizacao", _ESTILO_SECAO))
    if not aplicacoes_linhas:
        story.append(_caixa([Paragraph("Nenhuma pulverizacao cadastrada para esta safra.", _ESTILO_NORMAL)], fundo=CINZA_CLARO))
    else:
        rows = [
            [models.fmt_data_br(l["data_aplicacao"]) or "-", l["talhao"] or "-", l["fungicidas_quimicos"] or "-", l["fungicidas_biologicos"] or "-"]
            for l in aplicacoes_linhas
        ]
        story.append(_tabela_padrao(
            ["Data", "Talhao", "Fungicidas quimicos", "Fungicidas biologicos"], rows,
            [3.2 * cm, 3.2 * cm, 5.2 * cm, 5.3 * cm],
        ))

    story.append(Spacer(1, 10))
    rodape = rodape_data
    if weather and weather.get("fonte"):
        rodape = f"Fonte do clima: {weather['fonte']}" + (f" · {rodape_data}" if rodape_data else "")
    if rodape:
        story.append(Paragraph(rodape, _ESTILO_RODAPE))
    if fontes_pesquisadas:
        fontes_unicas = sorted(set(fontes_pesquisadas))
        story.append(Paragraph(
            f"Instituicoes de pesquisa consultadas para as recomendacoes acima: {' · '.join(fontes_unicas)}.",
            _ESTILO_RODAPE,
        ))
    story.append(Paragraph(
        "Isso nao substitui a avaliacao de um agronomo responsavel.",
        _ESTILO_RODAPE,
    ))
    story.append(Paragraph(
        "Powered by BioScout",
        _ESTILO_RODAPE,
    ))

    doc.build(story)
    buffer.seek(0)
    return buffer
