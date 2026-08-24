"""Biblioteca de referencia de fungicidas por doenca -- ingredientes ativos
para controle quimico e biologico, um por doenca monitorada pelo BioScout.

IMPORTANTE sobre a chave: cada doenca e' identificada aqui pelo nome ORIGINAL
em ingles que o BioScout usa (displayName), o mesmo que aparece como chave em
data_reader.DOENCA_MAP -- NAO pelo nome em portugues (esse e' editavel pelo
admin na aba Doencas e pode mudar a qualquer momento; se essa biblioteca
fosse indexada por ele, uma simples renomeacao quebraria a recomendacao
silenciosamente -- ja aconteceu: "Soybean Rust" foi renomeado de "Ferrugem da
Soja" para "Ferrugem Asiatica" e "Septoria" de "Septoriose" para "DFC").
O nome exibido na tela sempre vem da traducao atual (aba Doencas), nunca
daqui.

Fontes variam por doenca (ver campo "fonte"/"fonte_url" de cada grupo):
- Soybean Rust, Target Spot, Powdery Mildew e Septoria vem da ferramenta
  'Classificacao de eficacia de fungicidas quimicos e biologicos: modulo
  soja' (Embrapa Soja / Laboratorio de Epidemiologia UFV), que classifica
  cada ingrediente numa classe de eficacia (E/MB/B/R/F com base em ensaios
  de campo). Por isso esses 4 tem o campo "classe" preenchido.
- General Alternaria, General Rust, Moniliophthora spp. BETA, Anthracnose e
  Dry rot NAO tem uma ferramenta de classificacao de eficacia dedicada --
  os ingredientes ativos foram levantados via pesquisa na internet
  (Agrolink/Agrofit-MAPA, Embrapa, CEPLAC, artigos academicos) em 2026-08-23
  e refletem o que e' registrado/recomendado para a doenca, sem uma nota de
  eficacia comparavel entre si. Por isso o campo "classe" fica None nesses
  casos -- o template so mostra o ingrediente e a fonte.

Isto e' uma referencia estatica. Reconsulte as fontes periodicamente e
atualize este arquivo -- principalmente as doencas sem classe de eficacia,
onde a pesquisa foi mais generica (registro do ingrediente contra o
patogeno/genero, nao necessariamente ensaio de campo na cultura exata).

Este conteudo NAO substitui a avaliacao de um agronomo responsavel pela
lavoura -- doses, epoca de aplicacao, rotacao de modos de acao e
regularidade no MAPA devem ser sempre confirmadas antes do uso.
"""

DATA_CONSULTA = "23/08/2026"

_EMBRAPA_UFV_FONTE = "Embrapa Soja / Lab. de Epidemiologia UFV -- ferramenta de eficacia de fungicidas (modulo soja)"
_EMBRAPA_UFV_URL = "https://fitossanidadetropical.shinyapps.io/fungicidas/"

# Chave = nome original em ingles do BioScout (displayName / data_reader.DOENCA_MAP)
RECOMENDACOES = {
    "Soybean Rust": {
        "quimicos": {
            "fonte": _EMBRAPA_UFV_FONTE,
            "fonte_url": _EMBRAPA_UFV_URL,
            "itens": [
                {"ingrediente": "protioconazol + fluxapiroxade + mancozebe", "classe": "MB"},
                {"ingrediente": "ciproconazol + picoxistrobina + oxicloreto de cobre", "classe": "MB"},
                {"ingrediente": "protioconazol + mancozebe", "classe": "MB"},
                {"ingrediente": "protioconazol + fluxapiroxade", "classe": "MB"},
                {"ingrediente": "tebuconazol + picoxistrobina + mancozebe", "classe": "MB"},
                {"ingrediente": "protioconazol + clorotalonil", "classe": "MB"},
                {"ingrediente": "protioconazol + picoxistrobina + mancozebe", "classe": "MB"},
                {"ingrediente": "tebuconazol + picoxistrobina", "classe": "MB"},
                {"ingrediente": "protioconazol + picoxistrobina", "classe": "MB"},
                {"ingrediente": "protioconazol + azoxistrobina + mancozebe", "classe": "MB"},
            ],
        },
        "biologicos": {
            "fonte": _EMBRAPA_UFV_FONTE,
            "fonte_url": _EMBRAPA_UFV_URL,
            "itens": [
                {"ingrediente": "Bacillus velezensis BV02", "classe": "R"},
            ],
        },
    },
    "Target Spot": {
        "quimicos": {
            "fonte": _EMBRAPA_UFV_FONTE,
            "fonte_url": _EMBRAPA_UFV_URL,
            "itens": [
                {"ingrediente": "protioconazol + fluxapiroxade + mancozebe", "classe": "MB"},
                {"ingrediente": "protioconazol + mancozebe", "classe": "MB"},
                {"ingrediente": "protioconazol + fluxapiroxade", "classe": "MB"},
                {"ingrediente": "protioconazol + picoxistrobina + mancozebe", "classe": "MB"},
                {"ingrediente": "protioconazol + azoxistrobina + mancozebe", "classe": "MB"},
                {"ingrediente": "protioconazol + picoxistrobina + mancozebe", "classe": "MB"},
                {"ingrediente": "clorotalonil", "classe": "MB"},
                {"ingrediente": "protioconazol + clorotalonil", "classe": "B"},
                {"ingrediente": "oxicloreto de cobre", "classe": "B"},
                {"ingrediente": "protioconazol + impirfluxam", "classe": "B"},
            ],
        },
        "biologicos": {
            "fonte": _EMBRAPA_UFV_FONTE,
            "fonte_url": _EMBRAPA_UFV_URL,
            "itens": [
                {"ingrediente": "Bacillus velezensis BV02", "classe": "R"},
                {"ingrediente": "Bacillus subtilis + B. velezensis + B. pumilus", "classe": "R"},
                {"ingrediente": "Bacillus velezensis + B. subtilis", "classe": "R"},
                {"ingrediente": "Cerevisane", "classe": "R"},
            ],
        },
    },
    "Powdery Mildew": {
        "quimicos": {
            "fonte": _EMBRAPA_UFV_FONTE,
            "fonte_url": _EMBRAPA_UFV_URL,
            "itens": [
                {"ingrediente": "protioconazol + fluxapiroxade", "classe": "E"},
                {"ingrediente": "protioconazol + azoxistrobina + mancozebe", "classe": "E"},
                {"ingrediente": "protioconazol + trifloxistrobina + bixafem", "classe": "E"},
                {"ingrediente": "enxofre", "classe": "E"},
                {"ingrediente": "piraclostrobina + fluxapiroxade", "classe": "E"},
                {"ingrediente": "tebuconazol + carbendazim", "classe": "E"},
                {"ingrediente": "tiofanato-metilico + fluazinam", "classe": "MB"},
                {"ingrediente": "fluxapiroxade + oxicloreto de cobre", "classe": "MB"},
                {"ingrediente": "tetraconazol", "classe": "MB"},
            ],
        },
        "biologicos": {
            "fonte": _EMBRAPA_UFV_FONTE,
            "fonte_url": _EMBRAPA_UFV_URL,
            "itens": [],
        },
    },
    "Septoria": {
        "quimicos": {
            "fonte": _EMBRAPA_UFV_FONTE + " -- parte do complexo de doencas de fim de ciclo (DFC)",
            "fonte_url": _EMBRAPA_UFV_URL,
            "itens": [
                {"ingrediente": "mefentrifluconazol + piraclostrobina + fluxapiroxade", "classe": "MB"},
                {"ingrediente": "protioconazol + picoxistrobina + mancozebe", "classe": "MB"},
                {"ingrediente": "protioconazol + azoxistrobina + mancozebe", "classe": "MB"},
                {"ingrediente": "tebuconazol + trifloxistrobina + oxicloreto de cobre", "classe": "MB"},
                {"ingrediente": "difenoconazol + trifloxistrobina + clorotalonil", "classe": "MB"},
                {"ingrediente": "ciproconazol + azoxistrobina + clorotalonil", "classe": "MB"},
                {"ingrediente": "clorotalonil", "classe": "MB"},
                {"ingrediente": "metominostrobina + impirfluxam + clorotalonil", "classe": "MB"},
                {"ingrediente": "tebuconazol + azoxistrobina + mancozebe", "classe": "MB"},
                {"ingrediente": "tebuconazol + metominostrobina", "classe": "B"},
            ],
        },
        "biologicos": {
            "fonte": _EMBRAPA_UFV_FONTE + " -- parte do complexo de doencas de fim de ciclo (DFC)",
            "fonte_url": _EMBRAPA_UFV_URL,
            "itens": [
                {"ingrediente": "Bacillus subtilis + B. velezensis + B. pumilus", "classe": "R"},
                {"ingrediente": "Bacillus velezensis + B. subtilis", "classe": "R"},
            ],
        },
    },
    "General Alternaria": {
        "quimicos": {
            "fonte": (
                "Agrolink -- registros Agrofit/MAPA para Alternaria alternata. Doenca secundaria "
                "de baixo impacto na soja -- sem boletins de eficacia dedicados da Embrapa/Fundacao MT/"
                "ESALQ/UFV/UFLA/Fundacao MS/Fundacao Chapadao/IAC; rotulos de Nativo, Sphere Max, "
                "Priori Xtra, Cantus, Miravis Duo/Pro, Fox Xpro, Aproach Prima, Unizeb Gold e Elatus "
                "Trio conferidos diretamente e nenhum lista soja+Alternaria -- esses 5 provavelmente "
                "sao o conjunto real e completo registrado para essa combinacao doenca/cultura no Brasil."
            ),
            "fonte_url": "https://www.agrolink.com.br/problemas/mancha-de-alternaria_1652.html",
            "itens": [
                {"ingrediente": "azoxistrobina", "classe": None},
                {"ingrediente": "azoxistrobina + difenoconazol", "classe": None},
                {"ingrediente": "tebuconazol", "classe": None},
                {"ingrediente": "clorotalonil", "classe": None},
                {"ingrediente": "clorotalonil + difenoconazol", "classe": None},
            ],
        },
        "biologicos": {
            "fonte": "Agrolink -- registros Agrofit/MAPA para Alternaria alternata (mesma ressalva acima)",
            "fonte_url": "https://www.agrolink.com.br/problemas/mancha-de-alternaria_1652.html",
            "itens": [
                {"ingrediente": "Bacillus subtilis + B. velezensis + B. pumilus", "classe": None},
            ],
        },
    },
    "General Rust": {
        "quimicos": {
            "fonte": "Agrolink / Embrapa / Fundacao MS (via Mais Soja) -- ferrugem do milho (Puccinia sorghi / P. polysora)",
            "fonte_url": "https://www.agrolink.com.br/problemas/ferrugem_1739.html",
            "itens": [
                {"ingrediente": "piraclostrobina + epoxiconazol", "classe": None},
                {"ingrediente": "trifloxistrobina + protioconazol", "classe": None},
                {"ingrediente": "picoxistrobina + ciproconazol", "classe": None},
                {"ingrediente": "tebuconazol", "classe": None},
                {"ingrediente": "mancozebe + tebuconazol + picoxistrobina", "classe": None},
                {"ingrediente": "azoxistrobina + ciproconazol", "classe": None},
                {"ingrediente": "piraclostrobina + fluxapiroxade", "classe": None},
                {"ingrediente": "azoxistrobina + benzovindiflupir", "classe": None},
                {"ingrediente": "benzovindiflupir + picoxistrobina", "classe": None},
                {"ingrediente": "bixafeno + protioconazol + trifloxistrobina", "classe": None},
            ],
        },
        "biologicos": {
            "fonte": "Agrolink (Koppert, Syngenta, Biotrop) -- registros com ferrugem-polissora no alvo, milho",
            "fonte_url": "https://www.agrolink.com.br/problemas/ferrugem-polisora_1722.html",
            "itens": [
                {"ingrediente": "Bacillus pumilus (isolado CNPSo3203)", "classe": None},
                {"ingrediente": "Bacillus subtilis + Bacillus velezensis + Bacillus pumilus (isolados CNPSo)", "classe": None},
                {"ingrediente": "Bacillus subtilis + Bacillus velezensis + Bacillus pumilus (isolados CCTB)", "classe": None},
            ],
        },
    },
    "Moniliophthora spp. BETA": {
        "quimicos": {
            "fonte": "Agrolink/CEPLAC -- vassoura-de-bruxa do cacaueiro (Moniliophthora perniciosa)",
            "fonte_url": "https://www.agrolink.com.br/problemas/vassoura-de-bruxa_3046.html",
            "itens": [
                {"ingrediente": "piraclostrobina + epoxiconazol", "classe": None},
                {"ingrediente": "oxicloreto de cobre", "classe": None},
                {"ingrediente": "hidroxido de cobre", "classe": None},
                {"ingrediente": "oxido cuproso", "classe": None},
                {"ingrediente": "tebuconazol", "classe": None},
                {"ingrediente": "acibenzolar-S-metilico", "classe": None},
                {"ingrediente": "azoxistrobina + tebuconazol", "classe": None},
            ],
        },
        "biologicos": {
            "fonte": "CEPLAC -- Tricovab (Trichoderma stromaticum), 1o fungicida microbiologico brasileiro para essa praga",
            "fonte_url": "https://www.agrolink.com.br/agrolinkfito/produto/tricovab_3333.html",
            "itens": [
                {"ingrediente": "Trichoderma stromaticum", "classe": None},
            ],
        },
    },
    "Anthracnose": {
        "quimicos": {
            "fonte": "Agrolink / Agrofit-MAPA / Pioneer / Unisc -- antracnose da soja (Colletotrichum truncatum)",
            "fonte_url": "https://www.agrolink.com.br/problemas/antracnose_1764.html",
            "itens": [
                {"ingrediente": "piraclostrobina + tiofanato-metilico", "classe": None},
                {"ingrediente": "trifloxistrobina + ciproconazol", "classe": None},
                {"ingrediente": "carbendazim", "classe": None},
                {"ingrediente": "fludioxonil + metalaxil-M", "classe": None},
                {"ingrediente": "tiofanato-metilico + fluazinam", "classe": None},
                {"ingrediente": "azoxistrobina + benzovindiflupir", "classe": None},
                {"ingrediente": "fluxapiroxade + piraclostrobina + epoxiconazol", "classe": None},
                {"ingrediente": "trifloxistrobina + protioconazol", "classe": None},
                {"ingrediente": "carboxina + tiram (tratamento de sementes)", "classe": None},
                {"ingrediente": "tiabendazol + tiram (tratamento de sementes)", "classe": None},
            ],
        },
        "biologicos": {
            "fonte": "Brazilian Journal of Animal and Environmental Research / Mais Soja / Adapar -- isolados nativos e produtos registrados para soja",
            "fonte_url": "https://ojs.brazilianjournals.com.br/ojs/index.php/BJAER/article/view/70035",
            "itens": [
                {"ingrediente": "Trichoderma spp. (isolados nativos)", "classe": None},
                {"ingrediente": "Bacillus subtilis", "classe": None},
                {"ingrediente": "Trichoderma harzianum", "classe": None},
                {"ingrediente": "Trichoderma asperellum", "classe": None},
                {"ingrediente": "Bacillus amyloliquefaciens", "classe": None},
            ],
        },
    },
    "Dry rot": {
        "quimicos": {
            "fonte": "Agrolink / Embrapa / Corteva / Revista Cultivar -- fusariose e tratamento de sementes da soja (podridao de graos)",
            "fonte_url": "https://www.agrolink.com.br/problemas/fusariose_1998.html",
            "itens": [
                {"ingrediente": "piraclostrobina + tiofanato-metilico", "classe": None},
                {"ingrediente": "ipconazol (tratamento de sementes)", "classe": None},
                {"ingrediente": "fludioxonil + metalaxil-M (tratamento de sementes)", "classe": None},
                {"ingrediente": "difenoconazol + fludioxonil (tratamento de sementes)", "classe": None},
                {"ingrediente": "metalaxil-M + tiabendazol + fludioxonil + tiametoxam (tratamento de sementes)", "classe": None},
                {"ingrediente": "captana", "classe": None},
                {"ingrediente": "tiofanato-metilico + fluazinam", "classe": None},
                {"ingrediente": "trifloxistrobina", "classe": None},
                {"ingrediente": "carboxina + tiram", "classe": None},
                {"ingrediente": "picoxistrobina + ipconazol + oxatiapiprolina (tratamento de sementes)", "classe": None},
            ],
        },
        "biologicos": {
            "fonte": "SciELO Portugal / Embrapa / Revista Cultivar / Adapar -- biocontrole de Fusarium spp. em soja",
            "fonte_url": "https://scielo.pt/scielo.php?script=sci_arttext&pid=S0871-018X2013000300010",
            "itens": [
                {"ingrediente": "Bacillus velezensis + Paenibacillus ottowi", "classe": None},
                {"ingrediente": "Trichoderma asperellum", "classe": None},
                {"ingrediente": "Bacillus subtilis", "classe": None},
                {"ingrediente": "Trichoderma asperelloides (isolado ESALQ 1306)", "classe": None},
                {"ingrediente": "Bacillus subtilis (ESALQ-EpD2-5) + Bacillus velezensis (ESALQ-RZ1MS9)", "classe": None},
            ],
        },
    },
}

CLASSE_LABEL = {
    "E": "Excelente",
    "MB": "Muito boa",
    "B": "Boa",
    "R": "Razoavel",
    "F": "Fraca",
}


def get_recomendacao(doenca_en):
    """Retorna {quimicos, biologicos} para a doenca (chave = nome original em
    ingles do BioScout), ou None se ainda nao tem nada cadastrado na
    biblioteca (precisa ser pesquisado)."""
    return RECOMENDACOES.get(doenca_en)
