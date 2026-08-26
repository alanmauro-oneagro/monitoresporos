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
from reportlab.platypus import (
    Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

AZUL_MARCA = colors.HexColor("#0a1f44")
CINZA_CLARO = colors.HexColor("#f4f6f4")

_styles = getSampleStyleSheet()
_ESTILO_TITULO = ParagraphStyle(
    "TituloFazenda", parent=_styles["Heading1"], textColor=AZUL_MARCA, fontSize=16, spaceAfter=2,
)
_ESTILO_SUBTITULO = ParagraphStyle(
    "Subtitulo", parent=_styles["Normal"], textColor=colors.grey, fontSize=9, spaceAfter=10,
)
_ESTILO_SECAO = ParagraphStyle(
    "Secao", parent=_styles["Heading2"], textColor=AZUL_MARCA, fontSize=12, spaceBefore=12, spaceAfter=4,
)
_ESTILO_NORMAL = ParagraphStyle("NormalPdf", parent=_styles["Normal"], fontSize=9.5, leading=13)
_ESTILO_DOENCA = ParagraphStyle(
    "Doenca", parent=_styles["Normal"], fontSize=10.5, leading=14, spaceBefore=6, spaceAfter=2,
)


def _fmt_ingrediente(item, classe_label):
    if item.get("classe"):
        return f"{item['ingrediente']} ({classe_label.get(item['classe'], item['classe'])})"
    return item["ingrediente"]


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
    story.append(Paragraph(f"Manejo {safra_label} · powered by BioScout", _ESTILO_SUBTITULO))

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
        if weather.get("previsao_3_dias"):
            prev = " &nbsp;|&nbsp; ".join(
                f"<b>{d['data'][8:10]}/{d['data'][5:7]}</b>: {d['chuva_mm']}mm ({d['temp_min']}-{d['temp_max']}°C)"
                for d in weather["previsao_3_dias"]
            )
            story.append(Paragraph(f"<b>Previsao:</b> {prev}", _ESTILO_NORMAL))
        story.append(Spacer(1, 4))

    if cultura:
        story.append(Paragraph(f"<b>Cultura:</b> {cultura.upper()}", _ESTILO_NORMAL))

    if diseases:
        n_perigo = sum(1 for d in diseases if d["status"] == "Perigo")
        n_atencao = sum(1 for d in diseases if d["status"] == "Atencao")
        partes_resumo = []
        if n_perigo:
            partes_resumo.append(f"{n_perigo} em PERIGO")
        if n_atencao:
            partes_resumo.append(f"{n_atencao} em ATENCAO")
        story.append(Paragraph(f"<b>Resumo:</b> {', '.join(partes_resumo)}", _ESTILO_NORMAL))

    story.append(Paragraph("Doencas em Atencao / Perigo", _ESTILO_SECAO))
    if not diseases:
        story.append(Paragraph("Nenhuma doenca em Atencao ou Perigo nessa fazenda no momento.", _ESTILO_NORMAL))
    else:
        for d in diseases:
            cor_hex = "#ff6b6b" if d["status"] == "Perigo" else "#e6ac00"
            story.append(Paragraph(
                f'<font color="{cor_hex}">●</font> <b>{d["status"].upper()} — '
                f'{d["rotulo"].upper()}</b> — Contagem: {d["concentracao"]} esporos/m³',
                _ESTILO_DOENCA,
            ))
            biologicos_itens = d["biologicos"]["itens"][:3] if d.get("biologicos") else None
            quimicos_itens = d["quimicos"]["itens"][:3] if d.get("quimicos") else None
            if biologicos_itens:
                ativos = " // ".join(_fmt_ingrediente(p, d["classe_label"]) for p in biologicos_itens)
                story.append(Paragraph(f"Biologicos: {ativos}", _ESTILO_NORMAL))
            if quimicos_itens:
                ativos = " // ".join(_fmt_ingrediente(p, d["classe_label"]) for p in quimicos_itens)
                story.append(Paragraph(f"Quimicos: {ativos}", _ESTILO_NORMAL))
            story.append(Paragraph(f"Obs.: {d.get('nota') or '-'}", _ESTILO_NORMAL))

    produtos = produtos or {}
    story.append(Paragraph("Produtos ja disponiveis na fazenda", _ESTILO_SECAO))
    tem_produtos = any(produtos.get(tipo) for tipo in ("quimico", "biologico"))
    if not tem_produtos:
        story.append(Paragraph("Nenhum produto cadastrado para esta fazenda.", _ESTILO_NORMAL))
    else:
        for tipo, titulo in (("biologico", "Biologicos"), ("quimico", "Quimicos")):
            itens = produtos.get(tipo) or []
            partes = [
                f"{p['nome']} ({p['data_anotacao']})" if p.get("data_anotacao") else p["nome"]
                for p in itens if p.get("nome")
            ]
            story.append(Paragraph(f"<b>{titulo}:</b> {', '.join(partes) if partes else '-'}", _ESTILO_NORMAL))

    plantio_linhas = [l for l in (plantio_linhas or []) if any(l.values())]
    story.append(Paragraph("Datas de Plantio", _ESTILO_SECAO))
    if not plantio_linhas:
        story.append(Paragraph("Nenhum plantio cadastrado para esta safra.", _ESTILO_NORMAL))
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
        story.append(Paragraph("Nenhuma pulverizacao cadastrada para esta safra.", _ESTILO_NORMAL))
    else:
        rows = [
            [models.fmt_data_br(l["data_aplicacao"]) or "-", l["talhao"] or "-", l["fungicidas_quimicos"] or "-", l["fungicidas_biologicos"] or "-"]
            for l in aplicacoes_linhas
        ]
        story.append(_tabela_padrao(
            ["Data", "Talhao", "Fungicidas quimicos", "Fungicidas biologicos"], rows,
            [3.2 * cm, 3.2 * cm, 5.2 * cm, 5.3 * cm],
        ))

    story.append(Spacer(1, 14))
    rodape = rodape_data
    if weather and weather.get("fonte"):
        rodape = f"Fonte do clima: {weather['fonte']}" + (f" · {rodape_data}" if rodape_data else "")
    if rodape:
        story.append(Paragraph(rodape, _ESTILO_SUBTITULO))
    story.append(Paragraph(
        "Isso nao substitui a avaliacao de um agronomo responsavel.",
        ParagraphStyle("Aviso", parent=_ESTILO_SUBTITULO, fontSize=8),
    ))

    doc.build(story)
    buffer.seek(0)
    return buffer
