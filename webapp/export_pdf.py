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
import math

from reportlab.graphics.shapes import Circle, Drawing, Line, Path, Polygon, Rect, String, Wedge
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
# keepWithNext: titulo de secao nunca fica sozinho no fim da pagina,
# separado do quadro dele.
_ESTILO_SECAO = ParagraphStyle(
    "Secao", parent=_styles["Heading2"], textColor=AZUL_MARCA, fontSize=12, spaceBefore=12, spaceAfter=4,
    keepWithNext=1,
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
    "PrevisaoRisco", parent=_styles["Normal"], fontSize=8.5, spaceAfter=2,
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
_ESTILO_PREV_MM = ParagraphStyle("PrevMm", parent=_ESTILO_VALOR_CLIMA, fontSize=10, leading=12, alignment=1)
_ESTILO_LEGENDA_ROSA = ParagraphStyle(
    "LegendaRosa", parent=_ESTILO_ROTULO_CLIMA, alignment=1, textColor=AZUL_MARCA,
)


def _cabecalho_clima(weather, leitura_bioscout):
    """Bloco do topo do PDF: "Clima agora" em 4 indicadores, previsao de
    chuva dos 5 dias numa tabelinha (data em cima, mm embaixo -- sem
    temp. min/max, pedido do usuario) e, a direita, a rosa dos ventos dos
    ultimos 30 dias (quando houver)."""
    largura_total = A4[0] - 3.6 * cm - 12  # margens + padding do frame (6pt de cada lado)
    vento = weather.get("vento_30d")
    rosa = _build_rosa_ventos(vento["percentuais"]) if vento else None
    largura_esq = largura_total - (rosa.width + 0.4 * cm if rosa else 0)
    sem_borda = [
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]
    esquerda = []

    indicadores = []
    for rotulo, chave, unidade in (
        ("Temperatura", "temperatura_atual", " °C"), ("Umidade", "umidade_atual", "%"),
        ("Chuva agora", "chuva_atual_mm", " mm"), ("Chuva 24h", "chuva_24h_mm", " mm"),
    ):
        if weather.get(chave) is not None:
            indicadores.append([
                Paragraph(rotulo, _ESTILO_ROTULO_CLIMA), Paragraph(f"{weather[chave]}{unidade}", _ESTILO_VALOR_CLIMA),
            ])
    if indicadores:
        esquerda.append(Paragraph("Clima agora", _ESTILO_TITULO_CLIMA))
        esquerda.append(Spacer(1, 2))
        largura_ind = largura_esq / len(indicadores)
        tabela = Table([indicadores], colWidths=[largura_ind] * len(indicadores), hAlign="LEFT")
        tabela.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), CINZA_CLARO),
            ("LINEAFTER", (0, 0), (-2, -1), 0.6, colors.white),
            ("LEFTPADDING", (0, 0), (-1, -1), 7), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("ROUNDEDCORNERS", [4, 4, 4, 4]),
        ]))
        esquerda.append(tabela)

    previsao = weather.get("previsao_5_dias") or []
    if previsao:
        esquerda.append(Spacer(1, 7))
        esquerda.append(Paragraph("Previsão de chuva", _ESTILO_TITULO_CLIMA))
        esquerda.append(Spacer(1, 2))
        datas = [Paragraph(f"{d['data'][8:10]}/{d['data'][5:7]}", _ESTILO_PREV_DATA) for d in previsao]
        mms = [Paragraph(f"{d['chuva_mm']} mm", _ESTILO_PREV_MM) for d in previsao]
        tabela = Table([datas, mms], colWidths=[largura_esq / len(previsao)] * len(previsao), hAlign="LEFT")
        tabela.setStyle(TableStyle([
            ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#dee2e6")),
            ("LINEAFTER", (0, 0), (-2, -1), 0.6, colors.HexColor("#dee2e6")),
            ("TOPPADDING", (0, 0), (-1, 0), 4), ("BOTTOMPADDING", (0, 0), (-1, 0), 0),
            ("TOPPADDING", (0, 1), (-1, 1), 1), ("BOTTOMPADDING", (0, 1), (-1, 1), 5),
            ("ROUNDEDCORNERS", [4, 4, 4, 4]),
        ]))
        esquerda.append(tabela)

    if leitura_bioscout:
        esquerda.append(Spacer(1, 5))
        esquerda.append(Paragraph(f"Última leitura BioScout: {leitura_bioscout}", _ESTILO_ROTULO_CLIMA))

    if not rosa:
        tabela = Table([[esquerda]], colWidths=[largura_esq], hAlign="LEFT")
        tabela.setStyle(TableStyle(sem_borda))
        return tabela
    direita = [
        Paragraph(f"<b>Vento predominante</b> (30 dias)<br/>de {vento['nome']}", _ESTILO_LEGENDA_ROSA),
        rosa,
    ]
    tabela = Table([[esquerda, direita]], colWidths=[largura_esq + 0.4 * cm, rosa.width], hAlign="LEFT")
    tabela.setStyle(TableStyle(sem_borda + [
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (1, 0), (1, 0), "CENTER"),
    ]))
    return tabela


def _build_spore_chart(historico, largura=17.4 * cm, altura=4.8 * cm):
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
    dados = [[Paragraph("<b>Doença</b>", _ESTILO_TAB_RISCO)] + ([f"{dt}" for dt in datas] or ["Agora"])]
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
        dados.append(linha)
    largura_dia = 1.6 * cm
    tabela = Table(dados, colWidths=[17.4 * cm - largura_dia * ncols] + [largura_dia] * ncols, repeatRows=1)
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
    leitura_bioscout=None, mostrar_secao_doencas=True, anotacao=None, riscos_climaticos=None,
):
    """`diseases`/`weather`/`produtos`/`cultura` tem o mesmo formato usado
    em `_format_whatsapp_message` (ver app.py); `plantio_linhas` e
    `aplicacoes_linhas` vem de `models.get_all_farm_plantio`/
    `get_all_farm_aplicacoes` (lista de dicts), ja filtrados pra
    fazenda+safra. `leitura_bioscout` (dd/mm/aa, ja formatada) e' a data
    da ultima leitura de doenca do dispositivo BioScout -- None se nao
    houver nenhuma (ponto "so clima"). `mostrar_secao_doencas=False`
    omite completamente a secao "Doencas em Atencao/Perigo" (cabecalho
    incluso, nao so' a mensagem de "nenhuma doenca") -- usado quando
    `diseases` esta vazio por nao haver monitoramento de doenca de
    verdade acontecendo (ponto "so clima" da aba Alertas Clima, ou
    fazenda real com estacao sem leitura ha muito tempo, ver
    `app._send_site_whatsapp`), pra nao sugerir que a fazenda foi
    checada e esta tudo bem quando na verdade nao foi checada.
    `anotacao` (texto livre, ver `models.get_site_climate_note`) aparece
    logo apos o clima, quando preenchida. `riscos_climaticos` (ver
    `app._riscos_climaticos_relevantes`) e' um aviso a parte, so' de
    risco climatico (sem status/concentracao confirmados) -- aparece
    mesmo com `mostrar_secao_doencas=False`, pois nao afirma deteccao
    nenhuma, so' que o clima favorece a germinacao de uma doenca ja
    monitorada nessa fazenda. Retorna
    um `io.BytesIO` com o PDF
    pronto."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=1.8 * cm, rightMargin=1.8 * cm, topMargin=1.5 * cm, bottomMargin=1.5 * cm,
        title=f"Recomendacao - {nome_fazenda}",
    )
    story = []

    story.append(Paragraph(f"{nome_fazenda} - OneAgro", _ESTILO_TITULO))
    subtitulo = f"Recomendações {safra_label} - {cultura.upper()}" if cultura else f"Recomendações {safra_label}"
    story.append(Paragraph(subtitulo, _ESTILO_SUBTITULO))

    if weather:
        story.append(_cabecalho_clima(weather, leitura_bioscout))
        story.append(Spacer(1, 6))

    if anotacao:
        # Mesma caixa cinza das outras secoes de anotacao livre (Produtos/
        # Plantio/Pulverizacao) -- destaque visual, em vez de uma linha
        # em negrito perdida junto do clima.
        story.append(Paragraph("Anotações", _ESTILO_SECAO))
        story.append(_caixa([Paragraph(anotacao, _ESTILO_NORMAL)], fundo=CINZA_CLARO))
        story.append(Spacer(1, 4))

    if mostrar_secao_doencas:
        story.append(Paragraph("Doencas em Atencao / Perigo", _ESTILO_SECAO))
    fontes_pesquisadas = []
    if not diseases:
        if mostrar_secao_doencas:
            story.append(Paragraph("Nenhuma doenca em Atencao ou Perigo nessa fazenda no momento.", _ESTILO_NORMAL))
    else:
        for d in diseases:
            cabecalho = []
            cor_hex = "#ff6b6b" if d["status"] == "Perigo" else "#e6ac00"
            cabecalho.append(Paragraph(
                f'<font color="{cor_hex}">●</font> <b>{d["rotulo"].upper()}</b> — '
                f'{_STATUS_LABELS.get(d["status"], d["status"])}',
                _ESTILO_DOENCA,
            ))
            previsao_risco = d.get("previsao_risco")
            risco_label = _RISCO_LABELS.get(d.get("risco"))
            if previsao_risco:
                dias_txt = " &nbsp;·&nbsp; ".join(
                    f'<font color="{_RISCO_CORES.get(p["risco"], "#666666")}">●</font> '
                    f'<b>{p["data_fmt"]}</b>: {_RISCO_LABELS.get(p["risco"], "")}'
                    for p in previsao_risco
                )
                cabecalho.append(Paragraph(f"Risco de infecção: {dias_txt}", _ESTILO_PREVISAO_RISCO))
            elif risco_label:
                cor_risco = _RISCO_CORES.get(d.get("risco"), "#666666")
                cabecalho.append(Paragraph(
                    f'<font color="{cor_risco}">●</font> Risco de infecção: <b>{risco_label}</b>',
                    _ESTILO_RISCO_CLIMATICO,
                ))
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

    if riscos_climaticos:
        # Bloco a parte de "Doencas em Atencao/Perigo" -- so' clima
        # comparado com a germinacao, sem status/concentracao
        # confirmados (ver docstring). Aparece mesmo com
        # mostrar_secao_doencas=False, de proposito.
        story.append(KeepTogether([
            Paragraph("Favorabilidade climática para doenças na lavoura", _ESTILO_SECAO),
            Paragraph(
                "Condições climáticas previstas para os próximos 5 dias e seu potencial para favorecer o "
                "desenvolvimento das principais doenças, independentemente da presença de esporos no ar.",
                _ESTILO_SUBTITULO_RISCO,
            ),
            Spacer(1, 4),
            *_tabela_riscos_climaticos(riscos_climaticos),
        ]))
        story.append(Spacer(1, 4))

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
