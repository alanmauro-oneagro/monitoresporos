"""Gera o PDF de recomendacao de uma fazenda (aba Recomendacoes/Manejo) --
mesmo conteudo do relatorio de WhatsApp (clima, cultura, doencas em
Atencao/Perigo com recomendacao e anotacao, produtos ja disponiveis na
fazenda) mais as datas de plantio e de pulverizacao (aba Fazendas), pra
poder ser encaminhado por email ou impresso. Ver `recommendation_pdf` em
`app.py`."""
import io
import os
from xml.sax.saxutils import escape

import models
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.lib.utils import ImageReader
import math
import re

from reportlab.graphics.shapes import Circle, Drawing, Line, Path, Polygon, Rect, String, Wedge
from reportlab.platypus import (
    HRFlowable, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

AZUL_MARCA = colors.HexColor("#0a1f44")
CINZA_CLARO = colors.HexColor("#f4f6f4")

VERDE_FAIXA = colors.HexColor("#d7ecd2")
AMARELO_FAIXA = colors.HexColor("#fdf0c4")
VERMELHO_FAIXA = colors.HexColor("#f6d4d0")

# Rodape do relatorio, igual no texto do WhatsApp (pedido do Alan, 08/11/2026).
AVISO_AGRONOMO = ("Este relatório é uma ferramenta de monitoramento e não substitui "
                  "a avaliação de um agrônomo responsável.")
CONTATO_RELATORIO = "Quer ter essa ferramenta para sua lavoura, entre em contato com a OneAgro. (43)99114-7595"

_styles = getSampleStyleSheet()
_ESTILO_TITULO = ParagraphStyle(
    "TituloFazenda", parent=_styles["Heading1"], textColor=AZUL_MARCA, fontSize=14, leading=17, spaceBefore=0, spaceAfter=1,
)
# Cabecalho "RELATÓRIO DE MONITORAMENTO" (mesmo do WhatsApp; pedido do Alan, 08/11/2026).
_ESTILO_TOPO = ParagraphStyle(
    "TopoRelatorio", parent=_styles["Normal"], fontName="Helvetica-Bold", textColor=AZUL_MARCA, fontSize=12, leading=15,
)
_ESTILO_TOPO_INFO = ParagraphStyle(
    "TopoInfo", parent=_styles["Normal"], fontName="Helvetica-Bold", fontSize=9, leading=12,
)
_ESTILO_SUBTITULO = ParagraphStyle(
    "Subtitulo", parent=_styles["Normal"], textColor=colors.grey, fontSize=8.5, spaceAfter=6,
)
_ESTILO_RODAPE = ParagraphStyle(
    "Rodape", parent=_styles["Normal"], textColor=colors.grey, fontSize=8, spaceAfter=3,
)
# keepWithNext: titulo de secao nunca fica sozinho no fim da pagina,
# separado do quadro dele.
_ESTILO_SECAO = ParagraphStyle(
    "Secao", parent=_styles["Heading2"], textColor=AZUL_MARCA, fontSize=11, leading=13, spaceBefore=8, spaceAfter=3,
    keepWithNext=1,
)
_ESTILO_NORMAL = ParagraphStyle("NormalPdf", parent=_styles["Normal"], fontSize=8.5, leading=11)
_ESTILO_DOENCA = ParagraphStyle(
    "Doenca", parent=_styles["Normal"], fontSize=9.5, leading=12, spaceBefore=4, spaceAfter=1,
)
_ESTILO_GERMINACAO = ParagraphStyle(
    "Germinacao", parent=_styles["Normal"], fontSize=8.5, textColor=colors.grey, spaceAfter=2,
)
_ESTILO_RISCO_CLIMATICO = ParagraphStyle(
    "RiscoClimatico", parent=_styles["Normal"], fontSize=10.5, leading=14, spaceAfter=2,
)
_ESTILO_PREVISAO_RISCO = ParagraphStyle(
    "PrevisaoRisco", parent=_styles["Normal"], fontSize=8.5, spaceAfter=2,
)

_RISCO_LABELS = {"vermelho": "Alto", "amarelo": "Médio", "verde": "Baixo"}
_RISCO_CORES = {"vermelho": "#c0392b", "amarelo": "#b7860b", "verde": "#2e7d32"}
_COR_ACAO = {"preparar": "#b02a37", "acompanhar": "#8a6d00", "monitorar": "#146c43"}
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


_DIRECOES_ROSA = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")


def _build_rosa_ventos(percentuais, largura=5.6 * cm, altura=4.4 * cm):
    """Rosa dos ventos -- mesmo desenho da aba Graficos (bussola azul de
    fundo, um gomo verde por direcao com raio proporcional ao % de
    leituras vindas dela). `percentuais`: {"N": %, "NE": %, ...}."""
    maximo = max(percentuais.get(d) or 0 for d in _DIRECOES_ROSA)
    if maximo <= 0:
        return None
    d = Drawing(largura, altura)
    cx, cy = largura / 2, altura / 2
    raio = min(altura / 2 - 14, largura / 2 - 40)
    linha = colors.Color(10 / 255, 31 / 255, 68 / 255, alpha=0.4)
    preenchimento = colors.Color(10 / 255, 31 / 255, 68 / 255, alpha=0.35)

    def ponto(bearing, r):
        ang = math.radians(bearing)
        return cx + math.sin(ang) * r, cy + math.cos(ang) * r

    for r in (raio, raio * 0.68):
        d.add(Circle(cx, cy, r, strokeColor=linha, strokeWidth=0.8, fillColor=None))
    for i in range(8):
        x1, y1 = ponto(i * 45, raio * 0.12)
        x2, y2 = ponto(i * 45, raio * 1.1)
        d.add(Line(x1, y1, x2, y2, strokeColor=linha, strokeWidth=0.8))
    for bearing, comprimento, meia_largura in [(b, raio * 1.05, raio * 0.11) for b in (0, 90, 180, 270)] + \
            [(b, raio * 0.75, raio * 0.055) for b in (45, 135, 225, 315)]:
        ponta = ponto(bearing, comprimento)
        lado1 = ponto(bearing + 90, meia_largura)
        lado2 = ponto(bearing - 90, meia_largura)
        d.add(Polygon([ponta[0], ponta[1], lado1[0], lado1[1], cx, cy, lado2[0], lado2[1]],
                      fillColor=preenchimento, strokeColor=None))
    verde = colors.HexColor("#63b445")
    verde_claro = colors.Color(verde.red, verde.green, verde.blue, alpha=0.33)
    for i, direcao in enumerate(_DIRECOES_ROSA):
        valor = percentuais.get(direcao) or 0
        if valor <= 0:
            continue
        centro = 90 - i * 45
        d.add(Wedge(cx, cy, raio * valor / maximo, centro - 22.5, centro + 22.5,
                    fillColor=verde_claro, strokeColor=verde, strokeWidth=0.8))
    estilo = {"fontName": "Helvetica-Bold", "fontSize": 8, "fillColor": colors.HexColor("#495057")}
    d.add(String(cx, cy + raio + 4, "NORTE", textAnchor="middle", **estilo))
    d.add(String(cx, cy - raio - 10, "SUL", textAnchor="middle", **estilo))
    d.add(String(cx + raio + 5, cy - 3, "LESTE", textAnchor="start", **estilo))
    d.add(String(cx - raio - 5, cy - 3, "OESTE", textAnchor="end", **estilo))
    return d


_ESTILO_ROTULO_CLIMA = ParagraphStyle(
    "RotuloClima", parent=_styles["Normal"], fontSize=7.5, leading=9, textColor=colors.HexColor("#6c757d"),
)
_ESTILO_VALOR_CLIMA = ParagraphStyle(
    "ValorClima", parent=_styles["Normal"], fontSize=11, leading=13, textColor=AZUL_MARCA, fontName="Helvetica-Bold",
)
_ESTILO_TITULO_CLIMA = ParagraphStyle(
    "TituloClima", parent=_styles["Normal"], fontSize=8.5, leading=11, textColor=AZUL_MARCA, fontName="Helvetica-Bold",
)
_ESTILO_PREV_DATA = ParagraphStyle("PrevData", parent=_ESTILO_ROTULO_CLIMA, alignment=1)
_ESTILO_ROTULO_CLIMA_C = ParagraphStyle("RotuloClimaC", parent=_ESTILO_ROTULO_CLIMA, alignment=1)
_ESTILO_VALOR_CLIMA_C = ParagraphStyle("ValorClimaC", parent=_ESTILO_VALOR_CLIMA, alignment=1)
_ESTILO_PREV_MM = ParagraphStyle("PrevMm", parent=_ESTILO_VALOR_CLIMA, fontSize=10, leading=12, alignment=1)
_ESTILO_LEGENDA_ROSA = ParagraphStyle(
    "LegendaRosa", parent=_ESTILO_ROTULO_CLIMA, alignment=1, textColor=AZUL_MARCA,
)


def _build_spore_chart(historico, largura=17.4 * cm, altura=3.6 * cm):
    """Grafico "Esporos por M3" da doenca na fazenda -- mesmos parametros
    do grafico da aba Graficos (pedido do usuario, 16/10/2026): faixas
    verde/amarela/vermelha (warningConcentrationThreshold/
    dangerConcentrationThreshold/maximumConcentrationThreshold do BioScout,
    ver `data_reader.get_site_disease_history`), valor acima do maximo
    cortado no topo e marcado com triangulo vermelho, um ponto por
    leitura, dia sem leitura sem linha. Desenhado com formas do reportlab
    (ja' e' dependencia do projeto, sem lib de grafico externa)."""
    if len(historico) < 2:
        return None
    warn = historico[-1]["warn"] or 0
    danger = historico[-1]["danger"] or (warn * 2 or 1)
    maximo = historico[-1]["maximo"]
    # `historico` cobre uma janela CORRIDA de dias -- dia sem leitura vem
    # com concentracao=None (gap), filtra antes do max().
    concentracoes = [h["concentracao"] for h in historico if h["concentracao"] is not None]
    if not concentracoes:
        return None
    topo = maximo or max(danger * 1.5, max(concentracoes) * 1.05)

    margem_esq, margem_dir, margem_topo, margem_baixo = 0.9 * cm, 0.2 * cm, 0.55 * cm, 0.9 * cm
    plot_w = largura - margem_esq - margem_dir
    plot_h = altura - margem_topo - margem_baixo
    escala_y = plot_h / topo if topo else 0

    d = Drawing(largura, altura)

    def y(valor):
        return margem_baixo + min(valor, topo) * escala_y

    d.add(Rect(margem_esq, y(0), plot_w, y(min(warn, topo)) - y(0), fillColor=VERDE_FAIXA, strokeColor=None))
    d.add(Rect(margem_esq, y(min(warn, topo)), plot_w, y(min(danger, topo)) - y(min(warn, topo)), fillColor=AMARELO_FAIXA, strokeColor=None))
    d.add(Rect(margem_esq, y(min(danger, topo)), plot_w, y(topo) - y(min(danger, topo)), fillColor=VERMELHO_FAIXA, strokeColor=None))
    d.add(String(margem_esq, altura - 0.35 * cm, "Esporos por M³", fontSize=7, fontName="Helvetica-Bold", fillColor=colors.HexColor("#444444")))

    n = len(historico)
    escala_x = plot_w / (n - 1) if n > 1 else 0
    cor_linha = AZUL_MARCA
    vermelho = colors.HexColor("#dc3545")
    # Um trecho por sequencia continua sem gap (dia sem leitura quebra a
    # linha, igual ao `spanGaps:false` da aba Graficos).
    trecho, pontos = [], []
    for i, h in enumerate(historico):
        if h["concentracao"] is None:
            if len(trecho) >= 2:
                d.add(_linha_suave(trecho))
            trecho = []
            continue
        ponto = (margem_esq + i * escala_x, y(h["concentracao"]))
        trecho.append(ponto)
        pontos.append((ponto, h["concentracao"] > topo))
    if len(trecho) >= 2:
        d.add(_linha_suave(trecho))
    for (px, py), excedido in pontos:
        if excedido:
            tri = Path(fillColor=vermelho, strokeColor=vermelho, strokeWidth=0.5)
            tri.moveTo(px, py + 3.2)
            tri.lineTo(px - 3, py - 2)
            tri.lineTo(px + 3, py - 2)
            tri.closePath()
            d.add(tri)
        else:
            d.add(Circle(px, py, 1.1, fillColor=cor_linha, strokeColor=cor_linha))

    for valor in sorted({0, warn, danger, topo}):
        if valor <= topo:
            d.add(String(margem_esq - 4, y(valor) - 2.5, f"{valor:g}", fontSize=6.5, fillColor=colors.grey, textAnchor="end"))

    passo_label = max(1, n // 6)
    for i, h in enumerate(historico):
        if i % passo_label == 0 or i == n - 1:
            x = margem_esq + i * escala_x
            ancora = "end" if i == n - 1 else ("start" if i == 0 else "middle")
            d.add(String(x, 2, f"{h['data'][8:10]}/{h['data'][5:7]}", fontSize=6.5, fillColor=colors.grey, textAnchor=ancora))
    if any(e for _, e in pontos):
        lx = largura - margem_dir - 1.9 * cm
        ly = altura - 0.33 * cm
        tri = Path(fillColor=vermelho, strokeColor=vermelho, strokeWidth=0.5)
        tri.moveTo(lx, ly + 5)
        tri.lineTo(lx - 3, ly)
        tri.lineTo(lx + 3, ly)
        tri.closePath()
        d.add(tri)
        d.add(String(lx + 5, ly, "acima do máximo", fontSize=6.5, fillColor=vermelho))

    return d


_ESTILO_TAB_RISCO = ParagraphStyle("TabRisco", parent=_styles["Normal"], fontSize=8, leading=9.5)
_ESTILO_SUBTITULO_RISCO = ParagraphStyle("SubtituloRisco", parent=_styles["Normal"], fontSize=8.5, leading=11, textColor=colors.HexColor("#555555"))
_ESTILO_LEGENDA_RISCO = ParagraphStyle("LegendaRisco", parent=_styles["Normal"], fontSize=7.5, leading=9, textColor=colors.HexColor("#555555"))
_FUNDO_RISCO = {"vermelho": VERMELHO_FAIXA, "amarelo": AMARELO_FAIXA, "verde": VERDE_FAIXA}


def _tabela_riscos_climaticos(riscos):
    """Quadro "Risco de infeccao pelo clima" em tabela compacta (mesmo
    formato da tela de Recomendacoes, pedido do usuario 16/10/2026): uma
    linha por doenca, uma coluna por dia, celula colorida com
    Alto/Medio/Baixo. Doenca cadastrada a mao vai por ultimo, com *."""
    riscos = sorted(riscos, key=lambda d: d["doenca_en"].startswith("manual:"))
    datas = []
    for d in riscos:
        for p in d.get("previsao_risco") or []:
            if p["data_fmt"] not in datas:
                datas.append(p["data_fmt"])
    ncols = max(len(datas), 1)
    com_acao = any(d.get("acao") for d in riscos)
    dados = [[Paragraph("<b>Doença</b>", _ESTILO_TAB_RISCO)] + ([f"{dt}" for dt in datas] or ["Agora"])
             + (["Ação"] if com_acao else [])]
    estilo = [
        ("GRID", (0, 0), (-1, -1), 0.4, colors.lightgrey),
        ("BACKGROUND", (0, 0), (-1, 0), CINZA_CLARO),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("FONTNAME", (1, 0), (-1, 0), "Helvetica-Bold"),
        ("ALIGN", (1, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]
    for i, d in enumerate(riscos, start=1):
        manual = d["doenca_en"].startswith("manual:")
        nome = f"<b>{d['rotulo'].upper()}{' *' if manual else ''}</b>"
        if d.get("cientifico"):
            nome += f' <font color="#777777"><i>({d["cientifico"]})</i></font>'
        linha = [Paragraph(nome, _ESTILO_TAB_RISCO)]
        previsao = {p["data_fmt"]: p["risco"] for p in d.get("previsao_risco") or []}
        if previsao:
            for j, dt in enumerate(datas, start=1):
                risco = previsao.get(dt)
                linha.append(_RISCO_LABELS.get(risco, "-"))
                if risco in _FUNDO_RISCO:
                    estilo.append(("BACKGROUND", (j, i), (j, i), _FUNDO_RISCO[risco]))
                    estilo.append(("TEXTCOLOR", (j, i), (j, i), colors.HexColor(_RISCO_CORES[risco])))
        else:
            risco = d.get("risco")
            linha.append(f"{_RISCO_LABELS[risco]} (agora)" if risco in _RISCO_LABELS else "Sem clima disponível")
            linha += [""] * (ncols - 1)
            estilo.append(("SPAN", (1, i), (ncols, i)))
            if risco in _FUNDO_RISCO:
                estilo.append(("BACKGROUND", (1, i), (ncols, i), _FUNDO_RISCO[risco]))
        if com_acao:
            acao = d.get("acao")
            linha.append(acao["rotulo"] if acao else "")
            if acao:
                estilo.append(("TEXTCOLOR", (ncols + 1, i), (ncols + 1, i), colors.HexColor(_COR_ACAO.get(acao["chave"], "#495057"))))
                estilo.append(("FONTNAME", (ncols + 1, i), (ncols + 1, i), "Helvetica-Bold"))
        dados.append(linha)
    largura_dia = 1.6 * cm
    largura_acao = 3.3 * cm if com_acao else 0
    tabela = Table(dados, colWidths=[17.4 * cm - largura_dia * ncols - largura_acao] + [largura_dia] * ncols
                   + ([largura_acao] if com_acao else []), repeatRows=1)
    tabela.setStyle(TableStyle(estilo))
    if not any(d["doenca_en"].startswith("manual:") for d in riscos):
        return [tabela]
    return [tabela, Spacer(1, 2), Paragraph("* Doenças não monitoradas pela estação", _ESTILO_LEGENDA_RISCO)]


def _fmt_ingrediente(item, classe_label):
    if item.get("classe"):
        return f"{item['ingrediente']} ({classe_label.get(item['classe'], item['classe'])})"
    return item["ingrediente"]


def _caixa(conteudo, fundo=None):
    """Envolve `conteudo` (lista de flowables) numa caixa de largura
    total -- com fundo cinza claro (`fundo=CINZA_CLARO`) ou so com borda,
    sem preenchimento (`fundo=None`, transparente)."""
    # splitInRow: caixa maior que o espaco da pagina continua na
    # proxima (antes, texto maior que uma pagina derrubava o PDF inteiro).
    caixa = Table([[conteudo]], colWidths=[17.4 * cm], splitInRow=1)
    estilo = [
        ("BOX", (0, 0), (-1, -1), 0.5, colors.lightgrey),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]
    if fundo:
        estilo.append(("BACKGROUND", (0, 0), (-1, -1), fundo))
    caixa.setStyle(TableStyle(estilo))
    return caixa


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


_LOGO_ONEAGRO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "oneagro-logo.png")
_LOGO_ALTURA = 0.6 * cm


def _rodape_pagina(extraido_em):
    """Logo da OneAgro no canto inferior direito de toda pagina, com a
    data de extracao do relatorio logo abaixo."""
    def desenhar(canvas, doc):
        largura, altura = ImageReader(_LOGO_ONEAGRO).getSize()
        w = _LOGO_ALTURA * largura / altura
        direita = A4[0] - doc.rightMargin
        canvas.drawImage(_LOGO_ONEAGRO, direita - w, 0.95 * cm, width=w, height=_LOGO_ALTURA, mask="auto")
        if extraido_em:
            canvas.setFont("Helvetica", 7)
            canvas.setFillColor(colors.grey)
            canvas.drawRightString(direita, 0.6 * cm, extraido_em)
    return desenhar


# Relatorio da fazenda no modelo "Monitoramento e Manejo" (pedido do Alan, 16/11/2026): resumo da semana, clima com
# probabilidade de chuva e janela pra pulverizar, rosa dos ventos com a sequencia de aplicacao, doencas por prioridade
# (1. Entrar com aplicacao, 2. Acompanhar, 3. Monitorar = quadro de favorabilidade) e o plano de acompanhamento e
# manejo (o que olhar na visita -- sintomas/objetivo do Guia de manejo --, produtos de folha por aplicacao, criterio).
_LARGURA = 17.4 * cm
_LINHA_TAB = colors.HexColor("#d5dbd5")
_FUNDO_ACAO = {"preparar": "#fbe3e3", "acompanhar": "#fdf3cf", "monitorar": "#dff3e6"}
_FUNDO_NIVEL = {"Alto": "#fbe3e3", "Médio": "#fdf3cf", "Baixo": "#dff3e6"}
_COR_NIVEL = {"Alto": "#c0392b", "Médio": "#b7860b", "Baixo": "#2e7d32"}
_PONTOS_VENTO = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")
_NOME_VENTO = {"N": "Norte", "NE": "Nordeste", "E": "Leste", "SE": "Sudeste", "S": "Sul", "SW": "Sudoeste",
               "W": "Oeste", "NW": "Noroeste"}
_JANELA_BOA_MM, _JANELA_BOA_PROB, _JANELA_DIFICIL_MM = 5, 80, 10


def _est(nome, **kw):
    base = dict(parent=_styles["Normal"], fontSize=8.5, leading=11, textColor=colors.HexColor("#1f2933"))
    base.update(kw)
    return ParagraphStyle(nome, **base)


_E_TXT = _est("RelTxt", fontSize=9, leading=12)
_E_PEQ = _est("RelPeq", fontSize=8, leading=10.5)
_E_NOTA = _est("RelNota", fontSize=7.5, leading=9.5, textColor=colors.HexColor("#5f6b76"))
_E_CENTRO = _est("RelCentro", fontSize=8, leading=10, alignment=1)
_E_SECAO = _est("RelSecao", fontName="Helvetica-Bold", fontSize=10.5, leading=13, textColor=AZUL_MARCA,
                spaceBefore=7, spaceAfter=3)
_E_TOPO_MARCA = _est("RelMarca", fontName="Helvetica-Bold", fontSize=8.5, textColor=colors.HexColor("#8fd19e"))
_E_TOPO_TIT = _est("RelTit", fontName="Helvetica-Bold", fontSize=14, leading=17, textColor=colors.white)
_E_TOPO_SUB = _est("RelSub", fontSize=8.5, leading=11, textColor=colors.HexColor("#cfe0ff"))


def _faixa_topo(titulo, subtitulo):
    t = Table([[Paragraph("ONEAGRO", _E_TOPO_MARCA)], [Paragraph(escape(titulo), _E_TOPO_TIT)],
               [Paragraph(escape(subtitulo), _E_TOPO_SUB)]], colWidths=[_LARGURA])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), AZUL_MARCA), ("LEFTPADDING", (0, 0), (-1, -1), 10),
                           ("TOPPADDING", (0, 0), (0, 0), 7), ("BOTTOMPADDING", (0, -1), (-1, -1), 8),
                           ("TOPPADDING", (0, 1), (-1, -1), 1), ("BOTTOMPADDING", (0, 0), (-1, -2), 1)]))
    return t


def _faixa_prioridade(titulo, subtitulo, cor):
    t = Table([[Paragraph(f'<font color="white"><b>{titulo}</b> <font size="7.5">{subtitulo}</font></font>',
                          _est("RelFaixa", fontName="Helvetica-Bold", fontSize=10, leading=12))]], colWidths=[_LARGURA])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(cor)), ("LEFTPADDING", (0, 0), (-1, -1), 8),
                           ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
    return t


def _quadro(conteudo, largura=_LARGURA, fundo=CINZA_CLARO, borda=_LINHA_TAB, pad=6):
    t = Table([[conteudo]], colWidths=[largura], splitInRow=1)
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), fundo), ("BOX", (0, 0), (-1, -1), 0.6, borda),
                           ("LEFTPADDING", (0, 0), (-1, -1), pad), ("RIGHTPADDING", (0, 0), (-1, -1), pad),
                           ("TOPPADDING", (0, 0), (-1, -1), pad), ("BOTTOMPADDING", (0, 0), (-1, -1), pad)]))
    return t


def _selo_acao(acao, largura=3.6 * cm):
    cor = _COR_ACAO.get(acao["chave"], "#495057")
    t = Table([[Paragraph(f'<font color="{cor}"><b>{escape(acao["rotulo"])}</b></font>',
                          _est("RelSelo", fontSize=8, leading=10, alignment=1))]], colWidths=[largura])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(_FUNDO_ACAO.get(acao["chave"], "#eeeeee"))),
                           ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor(cor)),
                           ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
    return t


def _fmt(valor, casas=1):
    return f"{valor:.{casas}f}".replace(".", ",") if isinstance(valor, (int, float)) else "-"


def _ponto_vento(graus):
    return _PONTOS_VENTO[int(((graus % 360) + 22.5) // 45) % 8] if isinstance(graus, (int, float)) else None


def _janela(dia):
    mm, prob = dia.get("chuva_mm"), dia.get("chuva_prob")
    if not isinstance(mm, (int, float)):
        return None
    if mm < _JANELA_BOA_MM and (not isinstance(prob, (int, float)) or prob < _JANELA_BOA_PROB):
        return ("Boa", "#146c43", "#dff3e6")
    if mm <= _JANELA_DIFICIL_MM:
        return ("Possível", "#8a6d00", "#fdf3cf")
    return ("Difícil", "#b02a37", "#fbe3e3")


def _clima_da_fazenda(weather):
    previsao = weather.get("previsao_5_dias") or []
    acumulado = sum(d["chuva_mm"] for d in previsao if isinstance(d.get("chuva_mm"), (int, float)))
    hoje = f'{_fmt(weather.get("chuva_hoje_mm"))} mm'
    rot_hoje = "Chuva hoje (prev.)"
    if isinstance(weather.get("chuva_hoje_prob"), (int, float)):
        rot_hoje += f' · {weather["chuva_hoje_prob"]:.0f}%'
    itens = [(f'{_fmt(weather.get("temperatura_atual"))} °C', "Temperatura agora"),
             (f'{_fmt(weather.get("umidade_atual"), 0)}%', "Umidade agora"), (hoje, rot_hoje),
             (f'{_fmt(weather.get("chuva_24h_mm"))} mm', "Chuva últimas 24 h")]
    if previsao:
        itens.append((f"{_fmt(acumulado)} mm", f"Acumulado {len(previsao)} dias"))
    valores = [Paragraph(f"<b>{v}</b>", _est("RelMet", fontName="Helvetica-Bold", fontSize=11.5, leading=14,
                                                alignment=1, textColor=AZUL_MARCA)) for v, _ in itens]
    rotulos = [Paragraph(r, _est("RelMetR", fontSize=7, leading=9, alignment=1, textColor=colors.HexColor("#5f6b76")))
               for _, r in itens]
    metricas = Table([valores, rotulos], colWidths=[_LARGURA / len(itens)] * len(itens))
    metricas.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), CINZA_CLARO),
                                  ("LINEAFTER", (0, 0), (-2, -1), 0.6, colors.white),
                                  ("TOPPADDING", (0, 0), (-1, 0), 6), ("BOTTOMPADDING", (0, -1), (-1, -1), 6)]))
    saida = [metricas]
    if not previsao:
        return saida
    linhas = [[Paragraph("<b>Dia</b>", _E_PEQ)] + [Paragraph(f"<b>{d['data'][8:10]}/{d['data'][5:7]}</b>", _E_CENTRO)
                                                   for d in previsao],
              [Paragraph("Chuva prevista", _E_PEQ)] + [Paragraph(
                  f'<font color="#1c5fa8"><b>{_fmt(d.get("chuva_mm"))} mm</b></font>', _E_CENTRO) for d in previsao]]
    estilo = [("GRID", (0, 0), (-1, -1), 0.4, _LINHA_TAB), ("BACKGROUND", (0, 0), (-1, 0), CINZA_CLARO),
              ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]
    if any(isinstance(d.get("chuva_prob"), (int, float)) for d in previsao):
        linhas.append([Paragraph("Probabilidade de chuva", _E_PEQ)] + [Paragraph(
            f"<b>{d['chuva_prob']:.0f}%</b>" if isinstance(d.get("chuva_prob"), (int, float)) else "-", _E_CENTRO)
            for d in previsao])
        estilo.append(("BACKGROUND", (1, len(linhas) - 1), (-1, len(linhas) - 1), colors.HexColor("#eef4fb")))
    if any(_ponto_vento(d.get("vento_dir")) for d in previsao):
        linhas.append([Paragraph("Vento (de onde vem)", _E_PEQ)] + [Paragraph(
            (_NOME_VENTO[_ponto_vento(d["vento_dir"])]
             + (f' · até {_fmt(d.get("vento_kmh"), 0)} km/h' if isinstance(d.get("vento_kmh"), (int, float)) else ""))
            if _ponto_vento(d.get("vento_dir")) else "-", _est("RelVento", fontSize=7, leading=9, alignment=1))
            for d in previsao])
    janelas = [_janela(d) for d in previsao]
    if any(janelas):
        linhas.append([Paragraph("Janela p/ pulverizar", _E_PEQ)] + [Paragraph(
            f'<font color="{j[1]}"><b>{j[0]}</b></font>' if j else "-", _E_CENTRO) for j in janelas])
        for i, j in enumerate(janelas, start=1):
            if j:
                estilo.append(("BACKGROUND", (i, len(linhas) - 1), (i, len(linhas) - 1), colors.HexColor(j[2])))
    w0 = _LARGURA * 0.22
    tabela = Table(linhas, colWidths=[w0] + [(_LARGURA - w0) / len(previsao)] * len(previsao))
    tabela.setStyle(TableStyle(estilo))
    saida += [Spacer(1, 4), tabela]
    if any(janelas):
        saida.append(Paragraph(
            f"Janela: Boa = menos de {_JANELA_BOA_MM} mm e probabilidade abaixo de {_JANELA_BOA_PROB}%; Possível = até "
            f"{_JANELA_DIFICIL_MM} mm; Difícil = mais de {_JANELA_DIFICIL_MM} mm. Chuva logo após a aplicação lava o "
            "produto — confira na bula o tempo sem chuva necessário.", _E_NOTA))
    return saida


def _croqui_aplicacao(codigo, tamanho=4.4 * cm):
    """Fazenda vista de cima: faixas na ordem de aplicacao (1 = lado de onde vem o vento)."""
    d = Drawing(tamanho, tamanho)
    m = 10
    lado = tamanho - 2 * m
    d.add(Rect(m, m, lado, lado, fillColor=colors.HexColor("#eef6ee"), strokeColor=colors.HexColor("#146c43"),
               strokeWidth=0.8))
    ang = math.radians(90 - _PONTOS_VENTO.index(codigo) * 45)
    ux, uy = math.cos(ang), math.sin(ang)
    cx = cy = tamanho / 2
    for k, cor in enumerate(("#b02a37", "#d9822b", "#e0b000", "#7fb069")):
        off = (1.2 - k) * lado / 4.6
        px, py = cx + ux * off, cy + uy * off
        d.add(Circle(px, py, 7, fillColor=colors.HexColor(cor), strokeColor=colors.white))
        d.add(String(px, py - 3, str(k + 1), fontName="Helvetica-Bold", fontSize=8, textAnchor="middle",
                     fillColor=colors.white))
    ini = (cx + ux * (lado / 2 + 9), cy + uy * (lado / 2 + 9))
    fim = (cx + ux * (lado / 2 - 4), cy + uy * (lado / 2 - 4))
    azul = colors.HexColor("#1c5fa8")
    d.add(Line(ini[0], ini[1], fim[0], fim[1], strokeColor=azul, strokeWidth=2))
    px, py = -uy, ux
    d.add(Polygon([fim[0] - ux * 6, fim[1] - uy * 6, fim[0] + px * 4, fim[1] + py * 4, fim[0] - px * 4,
                   fim[1] - py * 4], fillColor=azul, strokeColor=None))
    d.add(String(tamanho / 2, tamanho - 7, "N", fontName="Helvetica-Bold", fontSize=7, textAnchor="middle",
                 fillColor=AZUL_MARCA))
    return d


def _vento_e_sequencia(weather):
    vento = weather.get("vento_30d")
    if not vento or not vento.get("percentuais"):
        return []
    rosa = _build_rosa_ventos(vento["percentuais"], largura=4.6 * cm, altura=4.4 * cm)
    codigo = vento["codigo"]
    oposto = _PONTOS_VENTO[(_PONTOS_VENTO.index(codigo) + 4) % 8]
    ordem = sorted(vento["percentuais"], key=lambda k: vento["percentuais"][k], reverse=True)
    segundo = ordem[1] if len(ordem) > 1 else None
    previstos = []
    for d in weather.get("previsao_5_dias") or []:
        p = _ponto_vento(d.get("vento_dir"))
        if p and _NOME_VENTO[p] not in previstos:
            previstos.append(_NOME_VENTO[p])
    texto = [Paragraph("<b>Vento dos últimos 30 dias</b>", _E_PEQ),
             Paragraph(f"Predominante de <b>{_NOME_VENTO[codigo]}</b> ({_fmt(vento['percentuais'][codigo], 0)}% das "
                       "horas)" + (f", depois {_NOME_VENTO[segundo]} ({_fmt(vento['percentuais'][segundo], 0)}%)."
                                   if segundo else "."), _E_PEQ),
             Spacer(1, 3), Paragraph("<b>Sequência de aplicação sugerida</b>", _E_PEQ),
             Paragraph(f"1. Comece pelos talhões do lado <b>{_NOME_VENTO[codigo]}</b> — é por onde o vento traz os "
                       "esporos primeiro.", _E_PEQ),
             Paragraph(f"2. Avance em faixas em direção ao lado <b>{_NOME_VENTO[oposto]}</b> (números do croqui).", _E_PEQ),
             Paragraph("3. Passe com o pulverizador cruzando o vento e aplique com vento entre 3 e 10 km/h, evitando "
                       "as horas mais quentes (deriva e evaporação).", _E_PEQ)]
    if previstos:
        texto.append(Paragraph(f"4. Nos próximos dias o vento previsto vem de <b>{', '.join(previstos)}</b>: no dia "
                               "da aplicação, refaça a ordem pelo vento do dia.", _E_PEQ))
    largura_desenhos = (rosa.width if rosa else 0) + 4.8 * cm
    linha = [[rosa, _croqui_aplicacao(codigo), texto]] if rosa else [[_croqui_aplicacao(codigo), texto]]
    larguras = ([rosa.width] if rosa else []) + [4.8 * cm, _LARGURA - largura_desenhos]
    t = Table(linha, colWidths=larguras)
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("BACKGROUND", (0, 0), (-1, -1), CINZA_CLARO)]))
    return [KeepTogether([Paragraph("ROSA DOS VENTOS E SEQUÊNCIA DE APLICAÇÃO", _E_SECAO), t, Paragraph(
        "Rosa: % das horas com vento vindo de cada direção. Croqui: 1 = primeira faixa a aplicar; a seta mostra o "
        "vento.", _E_NOTA)])]


def _linha_risco(previsao_risco, largura):
    celulas = [Paragraph("<b>Risco de infecção</b>", _E_PEQ)]
    for p in previsao_risco:
        rot = _RISCO_LABELS.get(p["risco"], "-")
        cor = _RISCO_CORES.get(p["risco"], "#666666")
        celulas.append(Paragraph(f'<font color="{cor}">●</font> {p["data_fmt"]} <font color="{cor}"><b>{rot}</b></font>',
                                 _est("RelRisco", fontSize=7.5, leading=9, alignment=1)))
    w0 = 2.7 * cm
    t = Table([celulas], colWidths=[w0] + [(largura - w0) / max(len(previsao_risco), 1)] * len(previsao_risco))
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 2),
                           ("RIGHTPADDING", (0, 0), (-1, -1), 2)]))
    return t


def _itens_lista(texto):
    return "<br/>".join(f"• {escape(x)}" for x in _frases(texto)) if (texto or "").strip() else ""


def _cartao_doenca(d, guia, fontes_pesquisadas):
    acao = d["acao"]
    cor = _COR_ACAO.get(acao["chave"], "#495057")
    interno = _LARGURA - 12
    nome = f'<font size="11"><b>{escape(d["rotulo"])}</b></font>'
    if d.get("cientifico"):
        nome += f' <font color="#6b7680">{escape(d["cientifico"])}</font>'
    previsao = d.get("previsao_risco") or []
    altos = sum(1 for p in previsao if p.get("risco") == "vermelho")
    linha_esporos = f'<b>Esporos no ar:</b> {_STATUS_LABELS.get(d.get("status"), "Baixa concentração de esporos")}'
    if previsao:
        linha_esporos += f" · <b>{altos} de {len(previsao)} dias</b> com risco alto de infecção"
    corpo = [Table([[Paragraph(nome, _E_TXT), _selo_acao(acao)]], colWidths=[interno - 3.8 * cm, 3.8 * cm],
                   style=[("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]),
             Paragraph(f'{linha_esporos}. <b>Ação:</b> {escape(acao["texto"])}', _E_PEQ)]
    if previsao:
        corpo += [Spacer(1, 2), _linha_risco(previsao, interno)]
    esquerda = []
    if guia and _itens_lista(guia.get("texto")):
        esquerda.append(Paragraph(f'<b>Sintomas iniciais a procurar</b><br/>{_itens_lista(guia["texto"])}', _E_PEQ))
    if (d.get("nota") or "").strip():
        esquerda += [Spacer(1, 3), Paragraph(
            "<b>Anotação</b><br/>" + escape(d["nota"].strip()).replace("\n", "<br/>"), _E_PEQ)]
    direita = []
    if guia and (guia.get("objetivo") or "").strip():
        direita += [Paragraph(f'<b>Objetivo da verificação</b><br/>{escape(guia["objetivo"].strip())}', _E_PEQ),
                    Spacer(1, 3)]
    referencias = []
    for chave, titulo in (("biologicos", "Biológicos"), ("quimicos", "Químicos")):
        grupo = d.get(chave)
        itens = (grupo or {}).get("itens") or []
        if itens:
            referencias.append(f"{titulo}: " + "; ".join(escape(_fmt_ingrediente(p, d.get("classe_label") or {}))
                                                        for p in itens[:4]))
            if grupo.get("fonte"):
                fontes_pesquisadas.append(grupo["fonte"].split(" -- ")[0])
    if referencias:
        direita.append(Paragraph("<b>Referências de pesquisa</b><br/>" + "<br/>".join(referencias), _E_PEQ))
    if esquerda or direita:
        if esquerda and direita:
            t = Table([[esquerda, direita]], colWidths=[interno * 0.55, interno * 0.45])
            t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (0, 0), 0),
                                   ("LINEAFTER", (0, 0), (0, 0), 0.5, _LINHA_TAB), ("LEFTPADDING", (1, 0), (1, 0), 7)]))
            corpo += [Spacer(1, 3), t]
        else:
            corpo += [Spacer(1, 3)] + (esquerda or direita)
    if d.get("status") == "Perigo":
        # Grafico das doencas com alta concentracao de esporos (pedido do Alan, 16/11/2026).
        grafico = _build_spore_chart(d.get("historico") or [], largura=interno, altura=3.2 * cm)
        if grafico:
            corpo += [Spacer(1, 3), Paragraph("<b>Esporos por M³ — últimos 30 dias</b>", _E_PEQ), grafico]
    if guia and (guia.get("fonte") or "").strip():
        corpo.append(Paragraph(f'Fonte dos sintomas: {escape(guia["fonte"].strip())}', _E_NOTA))
    return _quadro(corpo, fundo=colors.white, borda=colors.HexColor(cor))


def _resumo_semana(por_acao, n_monitorar, weather, mostrar_secao_doencas):
    linhas = []
    if mostrar_secao_doencas:
        for chave, rotulo, nomes in (("preparar", "Entrar com aplicação", por_acao["preparar"]),
                                     ("acompanhar", "Acompanhar", por_acao["acompanhar"])):
            linhas.append([_selo_acao({"chave": chave, "rotulo": _ROTULO_PRIORIDADE[chave]}),
                           Paragraph(f"<b>{rotulo}:</b> " + (escape(", ".join(nomes)) if nomes else "nenhuma doença"),
                                     _E_TXT)])
    if n_monitorar:
        linhas.append([_selo_acao({"chave": "monitorar", "rotulo": _ROTULO_PRIORIDADE["monitorar"]}),
                       Paragraph(f"<b>Monitorar:</b> {n_monitorar} doença{'s' if n_monitorar > 1 else ''} no quadro de "
                                 "favorabilidade", _E_TXT)])
    conteudo = []
    if linhas:
        t = Table(linhas, colWidths=[3.8 * cm, _LARGURA - 3.8 * cm - 12])
        t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                               ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
        conteudo.append(t)
    previsao = (weather or {}).get("previsao_5_dias") or []
    frases = []
    chuvas = [d for d in previsao if isinstance(d.get("chuva_mm"), (int, float))]
    if chuvas:
        total = sum(d["chuva_mm"] for d in chuvas)
        menor = min(chuvas, key=lambda d: (d["chuva_mm"], d.get("chuva_prob") or 0))
        probs = [d["chuva_prob"] for d in chuvas if isinstance(d.get("chuva_prob"), (int, float))]
        frase = f"Chuva prevista: {_fmt(total)} mm em {len(chuvas)} dias"
        if probs:
            frase += f" (probabilidade {min(probs):.0f}–{max(probs):.0f}%)"
        frase += (f". Dia com menos chuva: <b>{menor['data'][8:10]}/{menor['data'][5:7]}</b> "
                  f"({_fmt(menor['chuva_mm'])} mm).")
        frases.append(frase)
    vento = (weather or {}).get("vento_30d")
    if vento and vento.get("codigo") in _NOME_VENTO:
        frases.append(f"Vento predominante de <b>{_NOME_VENTO[vento['codigo']]}</b>: comece a aplicação pelo lado "
                      f"{_NOME_VENTO[vento['codigo']]} da fazenda.")
    if frases:
        conteudo += [Spacer(1, 2), Paragraph(" ".join(frases), _E_PEQ)]
    if not conteudo:
        return []
    return [Paragraph("RESUMO DA SEMANA", _E_SECAO), _quadro(conteudo)]


_ROTULO_PRIORIDADE = {"preparar": "Preparar aplicação", "acompanhar": "Acompanhar", "monitorar": "Monitorar"}


def _visita(doencas, monitorar, guias):
    linhas = [[Paragraph(f"<b>{t}</b>", _E_PEQ) for t in ("Prioridade", "Sintomas iniciais a procurar",
                                                          "Objetivo da verificação")]]
    extras = [d for d in monitorar if sum(1 for p in d.get("previsao_risco") or [] if p.get("risco") == "vermelho") >= 3
              and not d["doenca_en"].startswith("manual:")]
    for d in list(doencas) + extras[:2]:
        guia = guias.get(d["doenca_en"]) or {}
        if not (guia.get("texto") or guia.get("objetivo")):
            continue
        acao = d.get("acao") or {"chave": "monitorar", "rotulo": "Monitorar"}
        itens = _frases(guia.get("texto") or "")[:2]
        linhas.append([Paragraph(f'<b>{escape(d["rotulo"])}</b><br/><font color="{_COR_ACAO.get(acao["chave"])}" '
                                 f'size="7"><b>{escape(acao["rotulo"])}</b></font>', _E_PEQ),
                       Paragraph("<br/>".join(f"• {escape(x)}" for x in itens if x), _E_PEQ),
                       Paragraph(escape((guia.get("objetivo") or "").strip()), _E_PEQ)])
    if len(linhas) == 1:
        return []
    t = Table(linhas, colWidths=[3 * cm, (_LARGURA - 3 * cm) * 0.58, (_LARGURA - 3 * cm) * 0.42], repeatRows=1)
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, _LINHA_TAB), ("BACKGROUND", (0, 0), (-1, 0), CINZA_CLARO),
                           ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    return [KeepTogether([Paragraph("O QUE FAZER NA PRÓXIMA VISITA", _E_SECAO), t,
                          Paragraph("Sintomas e objetivos do Guia de manejo OneAgro (aba Manejo e Fungicidas).", _E_NOTA)])]


def _produtos_folha(produtos):
    grupos = models.produtos_por_data(produtos)
    if not grupos:
        return []

    def itens(lista):
        return "<br/>".join(f"<b>{escape(p['nome'])}</b>" + (
            f' <font color="#5f6b76">({escape(p["ingrediente_ativo"])})</font>' if p["ingrediente_ativo"] else "")
            for p in lista) or "-"
    linhas = [[Paragraph(f"<b>{t}</b>", _E_PEQ) for t in ("Aplicação", "Químicos", "Biológicos")]]
    for g in grupos:
        linhas.append([Paragraph(f"<b>{escape(g['data'] or 'Sem data')}</b>",
                                 _est("RelAplic", fontName="Helvetica-Bold", fontSize=9, textColor=AZUL_MARCA)),
                       Paragraph(itens(g["quimico"]), _E_PEQ), Paragraph(itens(g["biologico"]), _E_PEQ)])
    t = Table(linhas, colWidths=[_LARGURA * 0.15, _LARGURA * 0.5, _LARGURA * 0.35], repeatRows=1)
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, _LINHA_TAB), ("BACKGROUND", (0, 0), (-1, 0), CINZA_CLARO),
                           ("VALIGN", (0, 0), (-1, -1), "TOP"),
                           ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, CINZA_CLARO])]))
    return [KeepTogether([Paragraph("PRODUTOS DE FOLHA POR APLICAÇÃO", _E_SECAO), t, Paragraph(
        "Programa cadastrado em Manejos. Confira em que aplicação a lavoura está e, se a próxima coincidir com a "
        "janela de risco, avalie antecipar ou reforçar o alvo prioritário.", _E_NOTA)])]


_CRITERIOS = ("Confirmar estádio fenológico e cultivar.",
              "Inspecionar e registrar presença, incidência e severidade dos sintomas.",
              "Considerar o histórico da área e o programa de aplicações.",
              "Cruzar esporos medidos, risco de infecção, chuva prevista e vento (janela e sequência).",
              "Verificar registro para cultura/alvo, bula, dose, intervalo de segurança e rotação de modos de ação.")


def build_recommendation_pdf(
    nome_fazenda, safra_label, diseases, weather=None, produtos=None,
    cultura=None, plantio_linhas=None, aplicacoes_linhas=None, rodape_data="",
    leitura_bioscout=None, mostrar_secao_doencas=True, anotacao=None, riscos_climaticos=None,
    titulo_fazenda=None, data_relatorio="", guias=None,
):
    """Relatorio da fazenda (botao PDF, envio por WhatsApp e app). `diseases`/`weather`/`produtos`/`cultura` no
    formato de `app._format_whatsapp_message`; cada doenca ja' com `acao` (`app._com_nivel_acao`) e `historico`
    (grafico). `riscos_climaticos` = quadro de favorabilidade (com `acao`). `guias` = {doenca_en: {texto, objetivo,
    fonte}} do Guia de manejo (aba Manejo e Fungicidas). `mostrar_secao_doencas=False` (ponto so' clima, estacao
    sem leitura) tira as secoes de doenca com esporos. Retorna um `io.BytesIO`."""
    guias = guias or {}
    diseases = diseases or []
    weather = weather or {}
    por_acao = {"preparar": [], "acompanhar": [], "monitorar": []}
    doencas_por_acao = {"preparar": [], "acompanhar": [], "monitorar": []}
    for d in diseases:
        chave = (d.get("acao") or {}).get("chave", "monitorar")
        doencas_por_acao[chave].append(d)
        por_acao[chave].append(d["rotulo"])
    monitorar = doencas_por_acao["monitorar"] + list(riscos_climaticos or [])

    previsao = weather.get("previsao_5_dias") or []
    validade = (f" · Válido de {previsao[0]['data'][8:10]}/{previsao[0]['data'][5:7]} a "
                f"{previsao[-1]['data'][8:10]}/{previsao[-1]['data'][5:7]}") if previsao else ""
    sub = (titulo_fazenda or nome_fazenda.upper()) + (f" · {cultura.upper()}" if cultura else "") + \
        (f" · {data_relatorio}" if data_relatorio else "") + validade
    story = [_faixa_topo("RELATÓRIO DE MONITORAMENTO E MANEJO", sub), Spacer(1, 4)]
    story += _resumo_semana(por_acao, len(monitorar), weather, mostrar_secao_doencas)
    if weather:
        story += [KeepTogether([Paragraph("CLIMA NA FAZENDA", _E_SECAO), *_clima_da_fazenda(weather)])]
        story += _vento_e_sequencia(weather)

    fontes_pesquisadas = []
    if mostrar_secao_doencas:
        for chave, titulo, sub_faixa, cor in (
            ("preparar", "1. ENTRAR COM APLICAÇÃO", "alta concentração de esporos e risco alto de infecção", "#b02a37"),
            ("acompanhar", "2. ACOMPANHAR", "esporos em atenção ou risco alto de infecção", "#8a6d00"),
        ):
            cartoes = [_cartao_doenca(d, guias.get(d["doenca_en"]), fontes_pesquisadas) for d in doencas_por_acao[chave]]
            faixa = _faixa_prioridade(titulo, sub_faixa, cor)
            if cartoes:
                story += [Spacer(1, 6), KeepTogether([faixa, Spacer(1, 3), cartoes[0]])]
                for c in cartoes[1:]:
                    story += [Spacer(1, 4), c]
            else:
                story += [Spacer(1, 6), faixa, Paragraph("Nenhuma doença nesta situação.", _E_PEQ)]
    if monitorar:
        tabela = _tabela_riscos_climaticos(monitorar)
        story += [Spacer(1, 6), KeepTogether([
            _faixa_prioridade("3. MONITORAR — FAVORABILIDADE CLIMÁTICA" if mostrar_secao_doencas
                              else "FAVORABILIDADE CLIMÁTICA", "clima dos próximos dias, mesmo sem esporos detectados",
                              "#146c43"),
            Spacer(1, 3), *tabela])]
    notas = [Paragraph(escape(anotacao).replace("\n", "<br/>"), _E_PEQ)] if anotacao else []
    notas += [Paragraph(f"<b>{escape(d['rotulo'])}:</b> " + escape(d["nota"].strip()).replace("\n", "<br/>"), _E_PEQ)
              for d in doencas_por_acao["monitorar"] if (d.get("nota") or "").strip()]
    if notas:
        story += [KeepTogether([Paragraph("ANOTAÇÕES", _E_SECAO), _quadro(notas)])]

    plano = _visita(doencas_por_acao["preparar"] + doencas_por_acao["acompanhar"], monitorar, guias) \
        if mostrar_secao_doencas else []
    plano += _produtos_folha(produtos)
    plantio = [l for l in (plantio_linhas or []) if any(l.values())]
    if plantio:
        plano += [KeepTogether([Paragraph("DATAS DE PLANTIO", _E_SECAO), _tabela_padrao(
            ["Data", "Talhão", "Variedade", "Ciclo (dias)"],
            [[models.fmt_data_br(l["data_plantio"]) or "-", l["talhao"] or "-", l["variedade"] or "-",
              l["ciclo_dias"] or "-"] for l in plantio], [3.2 * cm, 4.5 * cm, 6.5 * cm, 3.2 * cm])])]
    aplicacoes = [l for l in (aplicacoes_linhas or []) if any(l.values())]
    if aplicacoes:
        plano += [KeepTogether([Paragraph("DATAS DE PULVERIZAÇÃO", _E_SECAO), _tabela_padrao(
            ["Data", "Talhão", "Fungicidas químicos", "Fungicidas biológicos"],
            [[models.fmt_data_br(l["data_aplicacao"]) or "-", l["talhao"] or "-", l["fungicidas_quimicos"] or "-",
              l["fungicidas_biologicos"] or "-"] for l in aplicacoes], [3.2 * cm, 3.2 * cm, 5.5 * cm, 5.5 * cm])])]
    if mostrar_secao_doencas and (diseases or monitorar):
        plano += [KeepTogether([Paragraph("CRITÉRIO PARA DECIDIR SOBRE A APLICAÇÃO", _E_SECAO), _quadro(
            [Paragraph(f"• {c}", _E_TXT) for c in _CRITERIOS] + [Spacer(1, 3), Paragraph(
                "<b>Decisão do responsável técnico:</b> se é necessário intervir, qual alvo priorizar e quando "
                "reavaliar. O alerta da estação apoia a decisão; não substitui a vistoria.", _E_PEQ)])])]
    if plano:
        story += [Spacer(1, 8), KeepTogether([_faixa_topo("PLANO DE ACOMPANHAMENTO E MANEJO", sub), plano[0]])]
        story += plano[1:]

    story.append(Spacer(1, 8))
    if fontes_pesquisadas:
        story.append(Paragraph("Instituições de pesquisa consultadas: " + " · ".join(sorted(set(fontes_pesquisadas)))
                               + ".", _E_NOTA))
    story += [Paragraph(AVISO_AGRONOMO, _E_NOTA), Paragraph(CONTATO_RELATORIO, _E_NOTA)]

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=1.8 * cm, rightMargin=1.8 * cm, topMargin=1.3 * cm,
                            bottomMargin=1.9 * cm, title=f"Relatório - {nome_fazenda}")
    rodape = _rodape_pagina(rodape_data)
    doc.build(story, onFirstPage=rodape, onLaterPages=rodape)
    buffer.seek(0)
    return buffer


_ESTILO_GUIA_TITULO = ParagraphStyle("GuiaTitulo", parent=_styles["Normal"], fontName="Helvetica-Bold", fontSize=13,
                                     leading=16, alignment=1, textColor=AZUL_MARCA)
_ESTILO_GUIA_SUB = ParagraphStyle("GuiaSub", parent=_styles["Normal"], fontSize=9, leading=12, alignment=1,
                                  textColor=colors.HexColor("#555555"))
_ESTILO_GUIA_DOENCA = ParagraphStyle("GuiaDoenca", parent=_styles["Normal"], fontName="Helvetica-Bold", fontSize=10.5,
                                     leading=13, textColor=AZUL_MARCA)
_ESTILO_GUIA_ITEM = ParagraphStyle("GuiaItem", parent=_styles["Normal"], fontSize=9, leading=12.5, leftIndent=10,
                                   bulletIndent=0, spaceBefore=1.5)


def _frases(texto):
    """Quebra o texto em frases (cada uma vira um item do guia)."""
    if "\n" in (texto or "").strip():
        return [l.strip() for l in texto.splitlines() if l.strip()]
    partes = [p.strip() for p in re.split(r"(?<=[.!?])\s+", texto or "") if p.strip()]
    return partes or [texto]


_ESTILO_GUIA_SECAO = ParagraphStyle("GuiaSecao", parent=_styles["Normal"], fontName="Helvetica-Bold", fontSize=11.5,
                                    leading=14, textColor=colors.white)
_ESTILO_GUIA_ROTULO = ParagraphStyle("GuiaRotulo", parent=_styles["Normal"], fontName="Helvetica-Bold", fontSize=8.5,
                                     leading=11, textColor=colors.HexColor("#2b6a2f"), spaceBefore=3)
_ESTILO_GUIA_TEXTO = ParagraphStyle("GuiaTexto", parent=_styles["Normal"], fontSize=9, leading=12.5, leftIndent=10)
_VERDE_GUIA = colors.HexColor("#2b6a2f")


def _faixa_secao(titulo):
    faixa = Table([[Paragraph(escape(titulo.upper()), _ESTILO_GUIA_SECAO)]], colWidths=[17.4 * cm])
    faixa.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), _VERDE_GUIA),
                               ("LEFTPADDING", (0, 0), (-1, -1), 8), ("TOPPADDING", (0, 0), (-1, -1), 3),
                               ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
    return faixa


_PREPOSICAO_CULTURA = {"soja": "da", "milho": "do", "algodao": "do", "algodão": "do", "feijao": "do", "feijão": "do",
                       "trigo": "do", "cafe": "do", "café": "do", "arroz": "do", "cana": "da", "batata": "da"}


def _titulo_secao_cultura(cultura):
    if cultura == "Outras culturas":
        return "Outras culturas"
    prep = _PREPOSICAO_CULTURA.get(cultura.lower())
    return f"Doenças {prep} {cultura.lower()}" if prep else f"Doenças: {cultura}"


def _quadro_doenca(nome, cientifico, texto, objetivo, extra="", fonte=""):
    titulo = escape(nome.upper())
    if cientifico:
        titulo += f' <font color="#777777" size="9"><i>({escape(cientifico)})</i></font>'
    if extra:
        titulo += f' <font color="#777777" size="8">· {escape(extra)}</font>'
    conteudo = [Paragraph(titulo, _ESTILO_GUIA_DOENCA)]
    if (texto or "").strip():
        conteudo.append(Paragraph("Sintomas iniciais", _ESTILO_GUIA_ROTULO))
        conteudo += [Paragraph(escape(f), _ESTILO_GUIA_ITEM, bulletText="•") for f in _frases(texto)]
    if (objetivo or "").strip():
        conteudo.append(Paragraph("Objetivo da verificação", _ESTILO_GUIA_ROTULO))
        conteudo.append(Paragraph(escape(objetivo.strip()), _ESTILO_GUIA_TEXTO))
    if (fonte or "").strip():
        conteudo.append(Paragraph(f'<font color="#888888" size="7">Fonte: {escape(fonte.strip())}</font>', _ESTILO_GUIA_TEXTO))
    return _caixa(conteudo, fundo=CINZA_CLARO)


def build_guia_manejo_pdf(secoes, objetivo_geral=None, fontes=None, extraido_em=None):
    """Guia de manejo -- identificacao precoce (aba Manejo e Fungicidas, pedido do Alan, 15/11/2026): o quadro
    "Objetivo da verificação a campo" (`objetivo_geral`, uma linha por item), uma secao numerada por cultura
    (`secoes` = [(cultura, [{nome, cientifico, texto, objetivo, fonte, outras_culturas}])], um quadro por doenca
    com os sintomas iniciais -- uma linha/frase por item --, o objetivo e a fonte) e o quadro "Fontes"."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=1.8 * cm - 6, rightMargin=1.8 * cm - 6,
                            topMargin=1.5 * cm, bottomMargin=1.8 * cm, title="Guia de manejo - OneAgro")
    historia = [
        HRFlowable(width="100%", thickness=1, color=AZUL_MARCA, spaceAfter=4),
        Paragraph("GUIA DE MANEJO", _ESTILO_GUIA_TITULO),
        Paragraph("<b>ONEAGRO</b> · Identificação precoce: sintomas iniciais e objetivo da verificação a campo",
                  _ESTILO_GUIA_SUB),
        HRFlowable(width="100%", thickness=1, color=AZUL_MARCA, spaceBefore=4, spaceAfter=10),
    ]
    if objetivo_geral:
        geral = [Paragraph("OBJETIVO DA VERIFICAÇÃO A CAMPO", _ESTILO_GUIA_DOENCA), Spacer(1, 3)]
        geral += [Paragraph(escape(t), _ESTILO_GUIA_ITEM, bulletText="•") for t in objetivo_geral]
        historia += [KeepTogether([_caixa(geral, fundo=colors.HexColor("#eaf4ea"))]), Spacer(1, 10)]
    numero = 0
    for cultura, itens in secoes:
        blocos = [[_quadro_doenca(i["nome"], i.get("cientifico"), i.get("texto"), i.get("objetivo"),
                                  ("também em " + ", ".join(i["outras_culturas"])) if i.get("outras_culturas") else "",
                                  i.get("fonte", "")),
                   Spacer(1, 7)] for i in itens]
        if not blocos:
            continue
        numero += 1
        historia.append(KeepTogether([_faixa_secao(f"{numero}. {_titulo_secao_cultura(cultura)}"), Spacer(1, 6)]
                                     + blocos[0]))
        historia += [KeepTogether(b) for b in blocos[1:]]
    if not numero:
        historia.append(Paragraph("Nenhuma doença com o guia preenchido.", _ESTILO_NORMAL))
    if fontes:
        caixa = [Paragraph("FONTES", _ESTILO_GUIA_DOENCA), Spacer(1, 3)]
        caixa += [Paragraph(escape(f), _ESTILO_GUIA_ITEM, bulletText="•") for f in fontes]
        historia += [Spacer(1, 4), KeepTogether([_caixa(caixa)])]
    historia += [Spacer(1, 8), Paragraph(AVISO_AGRONOMO, _ESTILO_LEGENDA_RISCO), Paragraph(CONTATO_RELATORIO, _ESTILO_LEGENDA_RISCO)]
    rodape = _rodape_pagina(extraido_em)
    doc.build(historia, onFirstPage=rodape, onLaterPages=rodape)
    return buf.getvalue()
