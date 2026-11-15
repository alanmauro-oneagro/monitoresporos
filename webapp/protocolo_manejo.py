"""Protocolo de verificacao em campo da OneAgro (documento do Alan, 15/11/2026).

Cada item: nome da doenca no documento, outros nomes com que ela pode estar
cadastrada no site, o que verificar em campo e o objetivo da verificacao.
Gravado 1x em cada doenca do site de mesmo nome (`app._semear_monitoramento`).
"""

PROTOCOLO = [
    # Soja
    ("Antracnose", [],
     "Inspecionar hastes, pecíolos, folhas, vagens e sementes. Procurar lesões escuras, necroses, manchas nas vagens e "
     "estruturas do fungo. Avaliar principalmente plantas no período reprodutivo.",
     "Identificar a presença da doença, avaliar sua distribuição e verificar possíveis danos às hastes, vagens e sementes."),
    ("Cercosporiose", ["Crestamento Foliar de Cercospora", "Mancha Púrpura"],
     "Avaliar folhas do terço inferior, médio e superior. Procurar manchas, bronzeamento, necroses e crestamento foliar. "
     "Observar também alterações de coloração nas sementes.",
     "Identificar o início da doença e acompanhar sua evolução no dossel, especialmente a progressão das lesões e da desfolha."),
    ("Complexo Diaporthe/Phomopsis", ["Diaporthe", "Phomopsis", "Diaporthe/Phomopsis", "Cancro da Haste"],
     "Inspecionar hastes, nós, pecíolos e vagens. Procurar lesões alongadas, escurecimento de tecidos, secamento de hastes "
     "e deterioração de vagens e sementes.",
     "Identificar sintomas associados ao complexo e avaliar possíveis comprometimentos de hastes, vagens e qualidade das sementes."),
    ("Ferrugem da Soja", ["Ferrugem Asiática"],
     "Examinar principalmente o terço inferior e médio das plantas, observando a face inferior das folhas. Procurar pequenas "
     "lesões e urédias, com atenção à evolução da desfolha.",
     "Detectar precocemente a presença da ferrugem e avaliar sua distribuição e progressão na lavoura."),
    ("Fusarium", ["Fusariose", "Podridão Vermelha da Raiz"],
     "Avaliar plantas com amarelecimento, murcha, desenvolvimento reduzido e falhas. Retirar plantas com sintomas e examinar "
     "raízes, colo e sistema vascular, observando escurecimentos e podridões.",
     "Identificar possíveis infecções radiculares e vasculares e delimitar reboleiras com sintomas compatíveis."),
    ("Mancha-alvo", ["Mancha Alvo"],
     "Inspecionar folhas dos diferentes terços, procurando manchas circulares castanhas, com halo amarelado e aspecto de alvo. "
     "Verificar também pecíolos, hastes e evolução da desfolha.",
     "Identificar a presença da doença, medir sua severidade e acompanhar a evolução das lesões no dossel."),
    ("Mancha de Myrothecium", ["Myrothecium", "Mancha de Mirotécio", "Mirotécio"],
     "Examinar folhas, principalmente as mais velhas, procurando lesões necróticas e estruturas circulares do fungo, que podem "
     "apresentar aspecto de pequenos pontos escuros.",
     "Identificar manchas compatíveis com Myrothecium e avaliar sua distribuição e evolução."),
    ("Mofo Branco", ["Esclerotinia", "Sclerotinia"],
     "Inspecionar o interior do dossel, especialmente próximo ao fechamento das linhas. Procurar murcha, lesões aquosas na "
     "haste, micélio branco e escleródios pretos.",
     "Identificar focos iniciais, verificar a presença de estruturas de sobrevivência e avaliar o risco de disseminação."),
    ("Oídio", [],
     "Examinar as duas faces das folhas, hastes e pecíolos. Procurar crescimento branco e pulverulento sobre os tecidos, "
     "observando a intensidade e a distribuição nas plantas.",
     "Detectar a presença do patógeno e acompanhar a evolução da área foliar colonizada."),
    ("Podridão/Anomalia da Soja (Moniliophthora)", ["Moniliophthora", "Anomalia da Soja", "Podridão da Soja"],
     "Inspecionar plantas em áreas com histórico e reboleiras. Observar alterações de desenvolvimento, sintomas em hastes e "
     "estruturas reprodutivas do fungo, quando presentes. Registrar localização e estádio fenológico.",
     "Identificar plantas com sintomas compatíveis, delimitar focos e relacionar os achados de campo com a concentração de "
     "esporos monitorada pela estação."),
    ("Rhizoctonia", ["Rizoctoniose", "Mela"],
     "Avaliar plântulas e plantas jovens, procurando tombamento, estrangulamento do colo, lesões castanhas e podridão "
     "radicular. Verificar falhas de estande e reboleiras.",
     "Identificar problemas de emergência e estabelecimento da cultura e avaliar a distribuição de possíveis focos de infecção."),
    ("Septoriose", ["Mancha Parda"],
     "Examinar principalmente folhas inferiores, procurando pequenas manchas castanhas, com halo amarelado, que podem evoluir "
     "e provocar amarelecimento e desfolha.",
     "Detectar o início da doença e acompanhar a progressão das lesões das folhas inferiores para os demais terços."),
    # Milho
    ("Ferrugem do Milho", ["Ferrugem Polissora", "Ferrugem Comum", "Ferrugem Branca"],
     "Inspecionar as folhas, principalmente as intermediárias e superiores. Procurar pústulas de coloração variável conforme "
     "o tipo de ferrugem, observando quantidade, distribuição e evolução das lesões.",
     "Identificar precocemente a doença, acompanhar sua evolução no dossel e avaliar o comprometimento da área foliar."),
    ("Helmintosporiose do Milho", ["Helmintosporiose", "Exserohilum"],
     "Examinar folhas inferiores e superiores. Procurar lesões alongadas, de formato elíptico, coloração parda a acinzentada, "
     "que podem aumentar e coalescer.",
     "Identificar os primeiros focos e avaliar a velocidade de expansão das lesões e a perda de área fotossintética."),
    ("Mancha de Bipolaris", ["Bipolaris", "Mancha de Bipolaris do Milho"],
     "Inspecionar folhas, procurando lesões alongadas ou elípticas, de coloração castanha, com diferentes dimensões. Observar "
     "a distribuição das manchas na planta.",
     "Confirmar sintomas compatíveis, avaliar a severidade e acompanhar a evolução da doença nas folhas."),
    ("Mancha de Diplodia", ["Diplodia", "Podridão de Diplodia", "Stenocarpella"],
     "Inspecionar colmo, entrenós, nós e espigas. Verificar descoloração interna dos tecidos, podridão, enfraquecimento do "
     "colmo e presença de estruturas reprodutivas do fungo nos tecidos afetados.",
     "Identificar possíveis podridões de colmo e espiga, avaliar o comprometimento estrutural e antecipar riscos de quebramento "
     "e perdas na colheita."),
    # Algodao
    ("Mancha de Alternaria", ["Alternaria", "Alternariose"],
     "Examinar folhas mais velhas e intermediárias. Procurar manchas circulares ou irregulares, de coloração castanha, "
     "frequentemente com anéis concêntricos e halo amarelado.",
     "Identificar a presença da doença e acompanhar a evolução das lesões e o comprometimento da área foliar."),
    ("Mancha de Ramulária", ["Ramulária", "Ramularia"],
     "Inspecionar principalmente folhas do baixeiro e terço médio. Procurar manchas angulares, inicialmente delimitadas pelas "
     "nervuras, com esporulação de aspecto pulverulento na face inferior. Observar desfolha.",
     "Detectar os primeiros focos, avaliar a distribuição vertical da doença e acompanhar a evolução da desfolha."),
    ("Ramulose", [],
     "Examinar ponteiros, folhas novas e ramos. Procurar lesões necróticas de aspecto estrelado, encurtamento dos entrenós, "
     "superbrotamento e deformação dos ponteiros.",
     "Identificar infecções iniciais e avaliar o comprometimento do crescimento vegetativo e da arquitetura das plantas."),
    ("Mancha Salmão", ["Mancha de Salmão"],
     "Inspecionar folhas, pecíolos, hastes e estruturas reprodutivas, procurando lesões com coloração salmão ou alaranjada, "
     "quando compatíveis com a doença cadastrada. Registrar distribuição e evolução.",
     "Identificar sintomas suspeitos e avaliar a necessidade de confirmação da identidade do patógeno antes de definir o manejo."),
]

# Ficha de inspecao (secao 5 do documento): o que registrar em cada visita.
REGISTRO_INSPECAO = [
    "Estádio fenológico",
    "Incidência: percentual de plantas afetadas",
    "Severidade: percentual de área foliar lesionada",
    "Terço da planta afetado",
    "Localização GPS",
    "Fotografias",
    "Conclusão do consultor: observações técnicas e próximos passos da inspeção",
]
