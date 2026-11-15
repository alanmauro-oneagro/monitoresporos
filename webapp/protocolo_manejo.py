"""Guia de manejo da OneAgro -- identificacao precoce (pesquisa Embrapa/Fundacao Rio Verde, 15/11/2026).

Cada item: cultura, nome da doenca, outros nomes com que ela pode estar cadastrada no site, sintomas
iniciais (um por linha), objetivo da verificacao e fonte.
Gravado 1x em cada doenca do site de mesmo nome (`app._semear_monitoramento`).
"""

PROTOCOLO = [('Soja',
  'Antracnose',
  [],
  'Manchas negras ao longo das nervuras das folhas, nos pecíolos e na haste.\n'
  'Vagens em R3-R4 escurecidas e retorcidas; em granação, estrias encharcadas (anasarca) que viram manchas negras.\n'
  'Pontos negros (acérvulos) sobre o tecido infectado — confirmam a doença com lupa.',
  'Encontrar a doença no início da formação de vagens, fase em que ela derruba vagens e reduz o número de grãos; '
  'mais comum no Cerrado chuvoso e quente.',
  'Embrapa Soja (2023)'),
 ('Soja',
  'Cercosporiose',
  ['Crestamento Foliar de Cercospora', 'Mancha Púrpura'],
  'Pontuações castanho-avermelhadas de bordas difusas nas folhas do terço SUPERIOR (expostas ao sol), com aspecto de '
  'bronzeamento.\n'
  'Nas vagens, pontuações vermelhas; nas hastes, manchas vermelhas superficiais.\n'
  'Na semente, mancha púrpura no tegumento (sinal tardio).',
  'Separar o início do crestamento do bronzeamento por sol/nutrição e agir antes que as manchas se juntem e causem '
  'desfolha precoce no enchimento de grãos.',
  'Embrapa Soja (2023); Fundação Rio Verde'),
 ('Soja',
  'Complexo Diaporthe/Phomopsis',
  ['Diaporthe', 'Phomopsis', 'Diaporthe/Phomopsis', 'Cancro da Haste'],
  '15-20 dias após a infecção: pequenos pontos negros na haste, que se alongam em manchas negras a '
  'castanho-avermelhadas, de um lado da haste.\n'
  'Fim de ciclo (seca da haste e da vagem): pontuações pretas (picnídios) em linha na haste e nos pecíolos e '
  'espalhadas nas vagens.\n'
  'Folhas amareladas com necrose entre as nervuras ("folha carijó") indicam infecção avançada.',
  'Identificar lesões na haste antes do acamamento e da quebra de plantas, e avaliar o risco às sementes quando há '
  'chuva na maturação.',
  'Embrapa Soja (2023)'),
 ('Soja',
  'Ferrugem da Soja',
  ['Ferrugem Asiática'],
  'Minúsculos pontos (até 1 mm) mais escuros que o tecido sadio, verde-acinzentados, na face INFERIOR dos folíolos '
  'do terço inferior e médio.\n'
  'Contra a luz, cada ponto tem uma pequena saliência (urédia); com lupa de 20x, poro aberto liberando esporos '
  'claros (pó).\n'
  'Pode surgir em qualquer estádio; antes do fechamento das entrelinhas é raro, depois cresce rápido.',
  'Achar a primeira urédia antes de a doença sair do baixeiro: a partir daí a desfolha acelera e cada dia de atraso '
  'na aplicação pesa na produtividade.',
  'Embrapa Soja (Doc. 256, 2023)'),
 ('Soja',
  'Fusarium',
  ['Fusariose', 'Podridão Vermelha da Raiz'],
  'Mancha avermelhada na raiz principal, 1 a 2 cm abaixo do solo, que circunda a raiz e escurece.\n'
  'Tecido interno da haste castanho-claro acima do solo.\n'
  'Na parte aérea, a partir da floração: amarelecimento e necrose entre nervuras ("folha carijó"), em reboleiras.',
  'Arrancar plantas suspeitas e confirmar na raiz, delimitando as reboleiras — sinal que orienta tratamento de '
  'semente e manejo de solo nas safras seguintes.',
  'Embrapa Soja (2023)'),
 ('Soja',
  'Mancha-alvo',
  ['Mancha Alvo'],
  'Pontuações pardas com halo amarelado nas folhas do terço inferior.\n'
  'Evoluem para manchas circulares castanhas com ponto escuro no centro (aspecto de alvo), até 2 cm.\n'
  'Em cultivar suscetível: manchas pardo-avermelhadas também em haste e vagem.',
  'Detectar as pontuações com halo no baixeiro e medir a severidade antes da subida para o terço médio — junto com a '
  'cercosporiose, foi a doença mais severa da soja em MT nas últimas safras (Fundação Rio Verde).',
  'Embrapa Soja (2023); Fundação Rio Verde'),
 ('Soja',
  'Mancha de Myrothecium',
  ['Myrothecium', 'Mancha de Mirotécio', 'Mirotécio'],
  'Pequenas lesões circulares com halo amarelo e centro branco-acinzentado, nas folhas mais velhas.\n'
  'Crescem formando anéis concêntricos marrom-escuros.\n'
  'Sobre a lesão, pontos negros (esporodóquios) contornados por micélio branco — o sinal que diferencia de outras '
  'manchas.',
  'Diferenciar de mancha-alvo e outras manchas foliares pelo sinal do fungo e acompanhar a severidade (ocorre também '
  'no algodão).',
  'Embrapa Soja (Doc. 256 e ID27033); descrição em outras culturas'),
 ('Soja',
  'Mofo Branco',
  ['Esclerotinia', 'Sclerotinia'],
  'Manchas aquosas (encharcadas) na haste e nas axilas, geralmente a partir de pétalas caídas, no interior do '
  'dossel.\n'
  'Em poucos dias: micélio branco e denso (algodão) sobre a lesão; folhas murchas no ápice.\n'
  'Escleródios negros e rígidos dentro e fora da haste (fase já avançada).',
  'Achar os primeiros focos na floração plena (fase mais vulnerável), com dossel fechado e clima ameno e úmido, e '
  'marcar as áreas para manejo e prevenção da próxima safra.',
  'Embrapa Soja (2023); Fundação Rio Verde'),
 ('Soja',
  'Oídio',
  [],
  'Fina cobertura branca e pulverulenta (como talco) sobre folhas, hastes e vagens.\n'
  'Mais comum a partir do início da floração, em clima ameno (18-24 °C) e ar seco.\n'
  'Com o tempo o branco passa a castanho-acinzentado.',
  'Estimar a área foliar coberta no início da floração, antes da seca e queda prematura das folhas.',
  'Embrapa Soja (2023)'),
 ('Soja',
  'Podridão/Anomalia da Soja (Moniliophthora)',
  ['Moniliophthora', 'Anomalia da Soja', 'Podridão da Soja'],
  'Sintomas costumam aparecer entre R5.1 e R5.5, com grãos ainda em formação.\n'
  'Plantas com haste verde/retenção foliar, necrose interna da haste e vagens/grãos malformados, sem padrão definido '
  'no dossel; em plantas jovens já houve relato de lesões no hipocótilo e fendilhamento da haste.\n'
  'A causa ainda está em pesquisa (Embrapa Soja, Cerrados e Agrossilvipastoril); o quadro é parecido com o do cancro '
  'da haste.',
  'Mapear plantas e reboleiras com sintomas, registrar estádio e local e cruzar com a concentração de esporos medida '
  'pela estação OneAgro — informação que ainda falta à pesquisa.',
  'Embrapa (rede de pesquisa em MT/RO); dados de campo'),
 ('Soja',
  'Rhizoctonia',
  ['Rizoctoniose', 'Mela'],
  'Plântulas (até 30-35 dias): estrangulamento da haste no nível do solo, murcha e tombamento, com falhas no '
  'estande.\n'
  'Mela (parte aérea, clima quente e úmido): áreas que secam rápido, castanhas, com folhas penduradas e teia de '
  'micélio com microescleródios.\n'
  'Ocorre em reboleiras.',
  'Medir falhas de estande e localizar reboleiras logo após a emergência; na fase vegetativa, achar a mela no '
  'início, antes de se espalhar por contato entre plantas.',
  'Embrapa Soja (2023)'),
 ('Soja',
  'Septoriose',
  ['Mancha Parda'],
  'Cerca de 2 semanas após a emergência: pequenas manchas angulares castanho-avermelhadas nas folhas unifolioladas.\n'
  'Pontuações pardas (< 1 mm) que formam manchas de centro angular castanho com halo amarelo (até 4 mm), nas duas '
  'faces.\n'
  'Progride das folhas de baixo para cima.',
  'Ver a doença ainda nas folhas unifolioladas/primeiros trifólios e acompanhar a subida no dossel, que leva a '
  'desfolha e maturação antecipada.',
  'Embrapa Soja (2023)'),
 ('Milho',
  'Ferrugem do Milho',
  ['Ferrugem Polissora', 'Ferrugem Comum', 'Ferrugem Branca'],
  'Polissora (Puccinia polysora): Pústulas pequenas, circulares a ovais, marrom-claras, principalmente na face '
  'SUPERIOR das folhas.\n'
  'Comum (Puccinia sorghi): Pústulas pequenas nas duas faces da folha, começando nas folhas inferiores. Cobertas por '
  'película que rompe e libera pó avermelhado (ferruginoso).\n'
  'Branca (Physopella zeae): Pústulas brancas ou amareladas, pequenas (0,3-1 mm), em grupos paralelos às nervuras, '
  'na face superior.',
  'Detectar as primeiras pústulas antes do pendoamento e acompanhar a subida até a folha da espiga; a polissora já '
  'causou perdas acima de 40% em epidemias.',
  'Embrapa Milho e Sorgo'),
 ('Milho',
  'Helmintosporiose do Milho',
  ['Helmintosporiose', 'Exserohilum'],
  'Lesões alongadas e elípticas, cinza a marrons, começando nas folhas INFERIORES.\n'
  'Crescem de 2,5 a 15 cm e podem se juntar, dando aspecto de queima.\n'
  'Clima ameno (18-27 °C) com orvalho favorece; mais grave na safrinha.',
  'Achar os primeiros focos na fase vegetativa — ataque antes da floração pode causar até 50% de perda.',
  'Embrapa Milho e Sorgo'),
 ('Milho',
  'Mancha de Bipolaris',
  ['Bipolaris', 'Mancha de Bipolaris do Milho'],
  'Raça T: lesões marrons, elípticas, com margem amarelada (clorótica).\n'
  'Raça 0: lesões alongadas, limitadas pelas nervuras, de margem castanha.\n'
  'Clima quente (22-30 °C) e úmido favorece.',
  'Confirmar o tipo de lesão e medir a severidade nas folhas, separando de outras manchas.',
  'Embrapa Milho e Sorgo'),
 ('Milho',
  'Mancha de Diplodia',
  ['Diplodia', 'Podridão de Diplodia', 'Stenocarpella'],
  'Na folha: lesões grandes e alongadas, como as de turcicum, mas com um pequeno círculo visível contra a luz (ponto '
  'de infecção); até 10 cm.\n'
  'No colmo: entrenós inferiores com tecido interno marrom e pontos negros (picnídios).\n'
  'Na espiga: podridão a partir da base, com grãos ardidos.',
  'Diferenciar da helmintosporiose olhando a lesão contra a luz e verificar colmo e espiga para prever quebramento e '
  'grão ardido.',
  'Embrapa Milho e Sorgo'),
 ('Algodão',
  'Mancha de Alternaria',
  ['Alternaria', 'Alternariose'],
  'Pequenas manchas circulares castanho-avermelhadas a arroxeadas nas folhas mais velhas.\n'
  'Crescem com centro castanho-acinzentado, anéis concêntricos e halo amarelo.\n'
  'Pode haver queda de folhas em ataque forte.',
  'Ver as primeiras manchas no baixeiro e acompanhar a área foliar perdida.',
  'Embrapa Algodão'),
 ('Algodão',
  'Mancha de Ramulária',
  ['Ramulária', 'Ramularia'],
  'Primeiros sinais entre o surgimento do primeiro botão floral e o início da floração.\n'
  'Pequenas manchas angulares (1-4 mm), limitadas pelas nervuras, nas folhas mais velhas do BAIXEIRO.\n'
  'Face superior verde-clara/amarelada; face inferior com pó branco (esporulação).',
  'Achar os primeiros focos no baixeiro e acompanhar a subida na planta: todas as variedades atuais são suscetíveis '
  'e o controle depende de começar cedo.',
  'Embrapa Algodão'),
 ('Algodão',
  'Ramulose',
  [],
  'Manchas necróticas circulares nas folhas mais JOVENS (ponteiro).\n'
  'As lesões evoluem e rasgam em aspecto estrelado; o ponteiro para de crescer.\n'
  'Fase avançada: superbrotamento (envassouramento) e planta baixa e ramificada.',
  'Encontrar as manchas nas folhas novas antes do superbrotamento — depois dele o dano é irreversível e o fungicida '
  'só funciona no início.',
  'Embrapa Algodão'),
 ('Algodão',
  'Mancha Salmão',
  ['Mancha de Salmão'],
  'Não encontramos descrição dessa doença com esse nome nas publicações consultadas.\n'
  'Hipótese de trabalho: lesões encharcadas em folhas do baixeiro e bainhas, com micélio e estruturas de cor '
  'rosada/salmão.',
  'Coletar amostra com sintoma e enviar para laboratório de diagnóstico para confirmar o agente causal antes de '
  'definir manejo.',
  '—')]

# Padroes editaveis na aba Manejo e Fungicidas (settings guia_objetivo_geral / guia_fontes).
OBJETIVO_GERAL = [
    "Confirmar no campo o que a estação OneAgro indica (esporos no ar + clima favorável) e encontrar a infecção no INÍCIO, quando ainda há poucas lesões e o controle é mais eficiente.",
    "Saber em que parte da planta e da lavoura a doença está (terço, folha, reboleira), para decidir onde e quando aplicar.",
    "Separar doença de sintomas parecidos (nutrição, sol, fitotoxidez, outra mancha) antes de recomendar produto.",
    "Registrar estádio, incidência, severidade, local (GPS) e fotos, para comparar visitas e acompanhar a evolução.",
]
FONTES = [
    'Embrapa Soja. Manual de identificação de doenças de soja, 6. ed. (Documentos 256), Londrina, 2023.',
    'Embrapa Milho e Sorgo. Sistema de produção do milho — Doenças foliares; Manejo de doenças na lavoura (Agro DBO, 2012).',
    'Embrapa Algodão. Comunicados e circulares técnicas sobre mancha de ramulária e ramulose.',
    'Fundação Rio Verde (Lucas do Rio Verde/MT). Ensaios em rede de fungicidas e resultados de severidade de mancha-alvo e cercosporiose em MT.',
    'Embrapa Soja, Cerrados e Agrossilvipastoril. Pesquisa em rede sobre a anomalia/podridão da soja em MT e RO.',
]
