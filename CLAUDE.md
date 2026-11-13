# OneAgro Monitor (BioScoutMonitor)

Contexto persistente deste projeto. Carregado automaticamente sempre que uma
sessão do Claude Code trabalha nesta pasta, independente de qual conta/usuario
Claude esta sendo usada.

Mantido por: Alan Mauro (OneAgro, alan.mauro@outlook.com), unico responsavel pelo app.

- App: Flask "OneAgro Monitor" — monitoramento de esporos/clima/doencas em
  fazendas.
- Repo: github.com/alanmauro-oneagro/monitoresporos
- Deploy: Railway, auto-deploy a partir da branch `main`.
- URL producao: https://oneagroapp.com.br (dominio proprio, 30/10/2026;
  o endereco antigo https://monitoresporos-production.up.railway.app
  continua no ar e autorizado no app). Dominio novo tem que entrar em
  `mobile/capacitor.config.json` (`allowNavigation`) e no
  `WKAppBoundDomains` do `mobile/ios/App/App/Info.plist`.
- Codigo do app fica em `webapp/` dentro deste repo.

## Regra de negocio permanente (nao mudar sem confirmar com o Alan)

**Niveis de acesso (pedido do Alan, 19/10/2026): Usuario, Administrador
e Master.** Usuario (`is_admin=0`) ve so' as fazendas liberadas; Administrador
(`is_admin=1`) ve todas as fazendas. Quais abas do menu Configuracoes
(Usuarios, Doencas, Fungicidas, Mapas de Estudo, Mapas de Solo, NDVI,
Envios) cada um desses dois niveis abre e' o Master que decide na aba
**Acessos** (`/admin/acessos`, `ABAS_CONFIG`, setting `acessos_por_nivel`;
padrao: Administrador = Usuarios + Mapas de Estudo, Usuario = nenhuma).
**Nomes no menu (pedido do Alan, 01/11/2026)**: "Mapas de NDVI" (rota `ndvi`),
"Planos e Valores" (`admin_planos`), "Gerenciar Acessos" (`admin_acessos`)
e "Programação de Envios" (`whatsapp_gerenciamento`, chave `envios`), "Mapas de Solo" (`solo`),
"Gerenciar Exportações" (`admin_exportar`, ex-"Exportar"), "Doenças/Culturas"
(`admin_doencas`, chave `doencas`, ex-"Doenças", 08/11/2026); onde
este arquivo diz NDVI/Planos/Acessos/Envios, sao essas abas.
**Mapas de Estudo = ex-"Mapa Interpolado"** (renomeado pelo Alan,
01/11/2026): rota `/mapa-interpolado`, endpoint/chave `mapa_interpolado`
e template `mapa_interpolado.html` continuam com o nome antigo; onde este
arquivo diz "Mapa Interpolado", e' essa aba. Sem previsao de chuva e sem
limites de municipio (so' pais/estados); tem as camadas de sinal de
celular (abaixo).
Rotas dessas abas usam `@aba_required("<chave>")`; aba nova do menu
Configuracoes deve entrar em `ABAS_CONFIG` E em `app.MENU_CONFIG` (lista
que monta o menu ⋮). **Ordem do menu Configuracoes** (pedido do Alan,
01/11/2026): so' "Alan Mauro" ve "↕ Ordenar abas" no fim do menu (setas
▲▼, cada clique salva via `/admin/menu-config/ordem`, setting
`ordem_menu_config`); a ordem vale pro menu de todos (cada um so' ve o
que pode abrir); item novo entra no fim. Ninguem cria, exclui ou
redefine senha de usuario com nivel acima do proprio. Master
(`users.is_master=1`, sempre com `is_admin=1`) tem acesso a tudo, inclusive
as configuracoes gerais do site -- painel/conexao do WhatsApp, Exportar,
Fazendas ocultas, "Editar cadastro", pontos so' clima, configuracoes da
fazenda em Manejos (`@master_required` / `current_user.is_master` em
`webapp/app.py` e nos templates). **So' o usuario "Alan Mauro"
(`models.MASTER_PRINCIPAL` / `ALAN_MAURO_USERNAME`) designa ou retira
Master**, e ele e' sempre Master (`init_db` garante). Master so' pode ser
excluido ou ter a senha redefinida pelo Alan. Ao criar novas rotas/
relatorios/configuracoes gerais do site, usar `@master_required`, a menos
que seja pedido explicitamente paridade para Administradores.
"Subordinados" na aba Usuarios continuam sendo os contatos leves de
WhatsApp de cada usuario (tabela `subordinados`), nao um nivel de acesso.

**Cadastro direto (pedido do Alan, 31/10/2026 -- substitui a aprovacao
e o codigo no WhatsApp de 30/10)**: "Ainda nao tem conta? Cadastre-se"
(login) e o "Assinar" de `/planos` levam a `/solicitar-acesso`; no login sao BOTOES abaixo de um divisor "ou" (os dois iguais, brancos com contorno e texto verdes, sem icone: "Ainda nao tem conta? Cadastre-se / Gratis por 7 dias" + "Conheca os planos"); veu branco do cartao do login 0.3 no computador e 0.39 no celular (40% mais transparente, pedido do Alan, 13/11/2026) (a faixa "🎁 Gratis por 7 dias" no topo do "Criar conta" foi retirada a pedido do Alan, 13/11/2026); `/cadastro`
so' redireciona pra la'. **Sem a pergunta "O que voce quer?"** (pedido do Alan,
08/11/2026): o cadastro e' sempre "Assinar plano" (campo escondido
`modalidade`); so' o link `/solicitar-acesso?modalidade=fazenda` cria
acesso a fazenda. **Area por cultura** (mesmo pedido): marcar a cultura
(ou escrever "Outra cultura") abre o campo da area dela (`area_cultura__<cultura>`,
`area_outra`), "Area total" soma na tela; `solicitacoes_acesso.culturas`
fica "Soja (1.200 ha), Milho (800 ha)" e `area_ha` = soma (base do "Preco
pela area"). Gerenciar Clientes mostra uma linha por cultura com a area dela
("Soja — 1.200 ha", `app._culturas_com_area`; cadastro antigo sem area
mostra so' o nome) e "Area total". **Tipo de cultura + planos no cadastro** (pedido do Alan,
08/11/2026): antes das culturas a pessoa escolhe Cereais / HF / Outros (ex-"Cana",
trocado pelo Alan em 08/11/2026; `init_db` troca "cana" por "outros" nos
cadastros e no setting `grupos_culturas`)
(`app.GRUPOS_CULTURA`, `_grupo_da_cultura`; a tela mostra so' as culturas
do grupo): Cereais = Soja, Milho, Algodao (preco pela area de SOJA; sem
Soja, soma); Cana = Cana; HF = as demais + "Outra cultura" (soma)
(`_area_para_preco`). Grava `solicitacoes_acesso.grupo_cultura` e
`area_preco_ha`. A tela "Criar conta" ja' traz os planos (cartoes com
preco; no app da loja sem preco); plano gratis escolhido ativa na hora,
pago vai pra Minha assinatura com o plano ja' marcado (`?plano=<id>`,
CPF/CNPJ continua la'). **"Criar conta" so' libera com os termos aceitos**
(botao `#botao-criar` desativado ate' marcar `aceite`). **Minha assinatura
vindo do cadastro** (pedido do Alan, 08/11/2026): com `?plano=<id>` mostra
SO' esse plano ("Ver todos os planos" volta a lista) e, se o cliente ainda
nao tem lavoura, o quadro "Onde sera' a area monitorada?" (nome da area =
fazenda do cadastro, latitude/longitude digitaveis com virgula ou ponto,
"Usar minha localizacao" e mapa Leaflet onde tocar/arrastar marca). Ao
continuar (`cliente_assinar`, campo `pedir_local`), o ponto vira a 1a
lavoura (`criar_cliente_ponto` + fazenda estimada se o plano tiver); sem
local = aviso e volta. **Preco pela area** (`planos.preco_por_area`,
caixa "Preço pela área" em Planos e Valores; ja' ligado no Premium): o
valor total = area do preco x U$/ha da faixa da tabela de apoio, em U$,
parcelas pelo ciclo (`app._plano_para_area`), calculado ao digitar no
cadastro e usado em Minha assinatura, na cobranca (`cliente_assinar`) e em
Gerenciar Clientes; faixa sem U$/ha = "Sob consulta" (nao deixa assinar).
/planos mostra "Preço pela área plantada". Excel (aba Clientes) tem a coluna "Tipo de cultura"
antes de "Culturas" (`export_excel._GRUPOS_CULTURA`). A tela de cadastro NAO explica a regra do
preco (sem dica do grupo, sem "area para o preco", sem nota no Premium --
pedido do Alan, 08/11/2026). **Tecnico do setor** marcado: os planos somem
e aparece "Entrar em contato com: Alan Mauro / Cel (43) 99114-7595 /
contato@oneagro.com.br" (`app.CONTATO_COMERCIAL`); o servidor ignora o
plano escolhido e a conta nasce sem plano (aviso com o mesmo contato). **Grupo economico** (pedido do Alan, 08/11/2026): campo
"Nome do grupo econômico" em Seus dados (opcional, ate' 40 letras,
`solicitacoes_acesso.grupo_economico`); com ele, cada fazenda estimada do
cliente nasce como "<grupo> - <fazenda>" (ex.: "Grupo Mauro - Faz Mourão",
`_criar_fazenda_estimada_da_lavoura`, vale tambem pra fazenda nova do app)
e e' esse o nome no relatorio/telas; sem ele, o antigo "<fazenda>
(<usuario>)". Aparece em Gerenciar Clientes, na Conta do app e no Excel
(ultima coluna da aba Clientes). Fazendas criadas antes nao mudam de nome. **"Voce e'" = UMA opcao** (radio; servidor guarda so' a 1a) e
**Tipo de cultura = varias** (checkbox; pedido do Alan, 08/11/2026):
`grupo_cultura` vira "cereais,outros"; a tela mostra as culturas de todos
os tipos marcados e a area do preco soma a regra de cada tipo
(`app._area_preco_grupos`; filtro `grupos_cultura` = "Cereais, Outros"). **Tipo de cada cultura** (pedido do Alan, 08/11/2026): na aba
Doenças/Culturas, quadro Culturas, um seletor Cereais/HF/Cana ao lado de
cada nome (Cereais/HF/Outros, `cultura_grupo__<slot>`, setting `grupos_culturas` = lista dos
12 slots, `models.get_grupos_culturas`) -- e' ele que decide em que tipo a
cultura aparece no cadastro e como entra no preco
(`app._grupo_da_cultura`: escolhido > regra pelo nome). **Locais de plantio + quem mais recebe**
(pedido do Alan, 08/11/2026): no cadastro, blocos "Local 1, 2..." ("+
Adicionar outro local", ate' 10): nome, culturas/areas do local
(`culturas__<i>`, `area_cultura__<i>__<cultura>`, `cultura_outra__<i>`,
`area_outra__<i>`) e ponto no mapa Leaflet/coordenadas (`local_lat__<i>`,
`local_lon__<i>`; `local_idx` lista os locais). **Cada local = uma fazenda
com Nome, Cidade e Estado proprios** (`local_nome__<i>`, `local_cidade__<i>`,
`local_uf__<i>`; pedido do Alan, 08/11/2026 -- o bloco "Fazenda ou empresa
/ Cidade / Estado" do topo saiu; a conta guarda os da 1a fazenda;
`cliente_pontos.cidade/uf`). Mapa de cada fazenda: divisas dos estados e
contorno do municipio digitado, buscados no navegador na API do IBGE
(`servicodados.ibge.gov.br`: malha UF, lista de municipios da UF que
vira sugestao da Cidade -- corrige pra grafia oficial -- e malha do
municipio, que enquadra o mapa enquanto nao ha' ponto marcado). Cada local vira area
monitorada (`cliente_pontos.culturas`/`area_ha`) + fazenda estimada
(`_criar_locais_e_extras`); o cadastro guarda o total (culturas somadas
por nome, area, area do preco = soma da regra de cada local). Form sem
`local_idx` (antigo) = um local sem mapa (`_locais_do_form`). "Quem mais
recebe o relatorio": ate' `MAX_EXTRAS_RELATORIO` (2) pessoas -- nome,
celular (`codigo_pais__extra<k>`/`numero_telefone__extra<k>`) e de quais
locais (`extra_locais__<k>`) -- viram **subordinados** do cliente
recebendo so' das fazendas daqueles locais, no mesmo formato (texto/PDF)
do plano do cliente (`_formatos_whatsapp_clientes`). Integracao: Gerenciar
Clientes (culturas de cada area + "Tambem recebem o relatorio"), app aba
Conta (ex-Culturas; culturas por area + form "Quem mais recebe", rota
`cliente_destinatarios`, substitui as pessoas; ao salvar volta pra tela
de onde o cliente veio -- campo `voltar` = Referer da Culturas, so' caminho
interno, `_caminho_interno`) e Excel aba Clientes
(colunas "Locais de plantio" e "Também recebem o relatório"). **Safras pelo cadastro** (pedido do Alan, 08/11/2026): a
fazenda de cada area do cliente ja' nasce com Safra / 2a / 3a Safra =
as 3 culturas de MAIOR area daquele local (`app._preencher_safras`;
`farm_culturas`, o mesmo que Manejos edita e Recomendacoes filtra). So'
culturas da lista do site (outra cultura digitada fica de fora); nunca
sobrescreve fazenda que ja' tem cultura em alguma safra. Fonte das areas
(`_pares_cultura_area`): culturas do local > cultura do ponto > culturas do
cadastro. Vale no cadastro com locais, no mapa da assinatura, em "+ Nova
area" do app e no backfill da subida (`_criar_fazendas_das_lavouras_antigas`,
cadastro so' quando o cliente tem 1 area). A pessoa escolhe **Assinar plano** ou **Ter acesso
a fazenda cadastrada**, marca Produtor e/ou Tecnico do setor e preenche
(tudo obrigatorio, menos e-mail e mensagem): nome completo, usuario (sugerido como
"Nome Ultimo-sobrenome" por `_sugerir_usuario`, duplicidade checada ao
digitar em `/solicitar-acesso/usuario` e de novo no envio -- mudar o nome sempre refaz a sugestao --, sem diferenciar
maiusculas -- `models.username_em_uso`), celular, fazenda/empresa,
cidade/UF, culturas, area plantada/atendida, mensagem e senha (>= 6). A
conta nasce na hora e a pessoa ja' entra: "Assinar plano" vira cliente do
app com assinatura sem plano e sem CPF (escolhe o plano e informa o
CPF/CNPJ na tela "Minha assinatura", `cliente_assinar` valida); "fazenda"
vira Usuario sem nenhuma fazenda ate' o Master liberar. Cada cadastro fica
registrado em `solicitacoes_acesso` (status 'cadastrado') pra tela
Configuracoes -> **Gerenciar Clientes** (`/admin/solicitacoes`, Master; ex-"Clientes e cadastros",
ex-"Novos cadastros" + a lista "Clientes do app" que ficava em Planos e
clientes -- juntadas pelo Alan, 01/11/2026; contador vermelho = nao
vistos, abrir a tela marca como visto): UM cartao por pessoa com data,
nome, usuario, perfil, celular (link wa.me), e-mail, fazenda, cidade,
culturas, area, mensagem, sinal e "Liberar fazendas"; se for cliente,
quadro da assinatura (plano, situacao, pago ate', lavouras, cortesia) e
"Excluir cliente". **Atualizado em 08/11/2026** (pedido do Alan): selo de
situacao ja' no cartao fechado ("<plano> · ativo/Free/bloqueado/escolher
plano"); no quadro da assinatura "Situacao" em palavras (`app._situacao_cliente`:
pago ate' X / usando o Free ate' X (aguardando pagamento) / cortesia /
bloqueado) e "Relatorio por WhatsApp: texto + PDF" pelo plano que vale
hoje; secao "Areas monitoradas" (ex-lavouras: nome, coordenadas, link
"🟢 no Mapas de Estudo", sinal de cada uma); filtros "No Free" e
"Bloqueados / sem plano". Cliente sem registro de cadastro (de antes do
cadastro direto) entra so' com os dados da conta. Filtros Todos/Novos/
Clientes/Acesso a' fazenda (no navegador). Cartoes RECOLHIDOS como em
Manejos (so' nome, selos e data; clicar abre), sem texto explicativo no
topo; sinal de celular na mesma linha discreta de Manejos, uma por local
(pedido do Alan, 01/11/2026). Todo Master com
telefone recebe aviso no WhatsApp (`_avisar_master_cadastro`, em thread). **Sem cadastro
duplicado** (pedido do Alan, 01/11/2026): celular (mesmos digitos ou os
11 ultimos) ou e-mail ja' usado por outra conta = aviso "ja' esta'
cadastrado... entre com a sua conta" e nada e' criado
(`models.conta_com_contato`). Conta excluida some de Novos cadastros
(`delete_user` apaga o registro; `_apagar_cadastros_sem_conta` limpa os
que sobraram ao abrir a tela/contar).

**Nomes dos planos (pedido do Alan, 09/11/2026)**: Free = **Essencial**,
Premium = **Avançado**, Premium + Estação = **Inteligência** (Básico igual).
`models.PLANOS_RENOMEADOS` troca no `init_db` (so' se o nome novo nao
existir; o id fica o mesmo); **planos padrao e nomes novos so' UMA vez** (setting `planos_padrao_feitos`; os padrao so' num banco sem plano nenhum -- antes o `init_db` recriava pelo nome a cada subida e plano renomeado/excluido na tela voltava; pedido do Alan, 13/11/2026); `plano_free()` acha "Essencial" (ou "Free"; senao o 1o plano de valor 0 sem preco pela area, os a venda antes).
Onde este arquivo diz Free/Premium/Premium + Estacao, sao esses planos.
**Modelo unico de fazenda em Gerenciar Clientes** (pedido do Alan,
09/11/2026 -- substitui o "💳 Plano e grupo" do Mapas de Estudo e as
configuracoes da fazenda que ficavam em Manejos, que SAIRAM sem atalho): toda
fazenda pertence a um cadastro (conta de cliente, com login) que da' plano,
validade e grupo economico. As do app ja' nascem assim (`cliente_pontos`); as
da BioScout e do Mapas de Estudo o Master liga a um cadastro -- "+ Novo
cliente" (nome, usuario, senha, celular, e-mail, grupo, plano -- padrao
Inteligência --, cortesia e as fazendas; rota `admin_cliente_novo`) ou "+ Ligar
fazenda a este cadastro" / "Ligar a este cliente" em "Fazendas sem cadastro"
(`admin_cliente_vincular`; "Desligar" = `admin_cliente_desvincular`). Fazenda
ligada = linha em `cliente_pontos` com `vinculada`=1 e `adicional_status`
'incluida' (`models.vincular_fazenda`, ja' libera e marca o cliente pra
receber o relatorio): nunca e' apagada junto com o cadastro/ponto
(`delete_user`, `excluir_cliente_ponto`), nao e' renomeada/movida pelo app
(`_renomear_fazenda_cliente`), o cliente nao exclui pelo app e nao tem pino
verde-claro (`sites_de_clientes`). Fazenda ainda sem cadastro guarda os dados em
`fazendas_dados` (municipio, UF, culturas, area; vao pro ponto ao ligar e
voltam ao desligar). Cada fazenda (cliente, ligada ou sem cadastro) tem a MESMA
linha (`linha_fazenda` em admin_solicitacoes.html: nome, selo do plano, origem
📡 BioScout/🗺️ Mapas de Estudo/📱 App, municipio, culturas, area) e o MESMO editor
"✏️ Dados da fazenda" (`_dados_fazenda.html`, nesta ordem -- pedido do Alan,
09/11/2026: Grupo economico, Nome de exibicao, Pais, Municipio/Estado + Tipo de
cultura, Estacao meteorologica, Ocultar; carregado por fetch em
`admin_fazenda_dados`; tudo salva sozinho: "Grupo econômico" (`admin_fazenda_grupo_salvar`:
com cadastro muda o grupo do cadastro; sem cadastro fica em
`fazendas_dados.grupo_economico` e ja' agrupa em Recomendações),
municipio/UF e culturas NO MESMO PADRAO DO APP (tipo Cereais/HF/Outros,
caixa por cultura com a area, "Outra cultura", Area total; JS
`atualizarCulturas` no admin_solicitacoes.html; `admin_fazenda_dados_salvar`
le com `_locais_do_form` e grava "Soja (1.200 ha), ..."), nome de exibicao,
ocultar, pais e estacao do clima -- as mesmas rotas que Manejos usava). Grupo
economico editavel no cartao do cadastro (`admin_cliente_grupo`). Tudo
`@master_required`. **Lista de Gerenciar Clientes** (pedido do Alan, 09/11/2026):
as fazendas sem cadastro sao CARTOES iguais aos dos produtores (macros
`cartao_pessoa`/`cartao_fazenda`, `data-tipo="fazenda"`, selo "Fazenda sem
cadastro", origem e plano; abrir mostra a linha SEM repetir nome/plano/origem -- `linha_fazenda(..., sem_titulo=True)`,
pedido do Alan, 10/11/2026 -- + "Dados da fazenda"; o
"Ligar ao cliente" fica DENTRO do "Dados da fazenda", no topo, so' pra fazenda
sem cadastro; select comeca em "-" (botao "Ligar" so' libera ao escolher) com
"Clientes do app" (entra no cadastro, `admin_cliente_vincular`) e "Usuários" da aba
Usuarios (pedido do Alan, 10/11/2026: ligar = libera a fazenda + recebe o relatorio,
sem cadastro de cliente -- `admin_usuario_ligar_fazenda`; linha "Ligada a:" com os
usuarios ligados, `models.usuarios_ligados_a_fazenda`, ✕ desliga =
`admin_usuario_desligar_fazenda`; sem ninguem = "-")); a linha de cada fazenda mostra tambem a estacao do clima
escolhida ("📡 SINOP/MT (A917) · 17,7 km", coordenada propria ou media das 3
-- `_preencher_estacoes`, em lote); "Exibir por: Nome do produtor | Grupo"
(`?exibir=`, lembrado em `session["gerenciar_clientes_exibir"]`; por grupo =
caixa "👥 <grupo>" com os cadastros e as fazendas soltas daquele grupo, o
resto depois; a caixa FECHADA ja' mostra "N cadastro(s) · N fazenda(s)" e um
resumo `.grupo-resumo` com cada cadastro -- nome, "N fazenda(s)" e os mesmos
selos do cartao, macro `selos_pessoa` -- e cada fazenda solta com seus selos;
abrir troca o resumo pelos cartoes -- pedido do Alan, 10/11/2026); selo "⚠ Cadastro incompleto" ao lado do perfil (Produtor) com
o que falta no `title` (`_faltando_no_cadastro`: nome completo, celular,
plano, fazenda, municipio/estado e culturas de cada fazenda; fazenda sem
cadastro NUNCA tem o selo "Cadastro incompleto" -- ja' tem o selo "Fazenda sem cadastro" (pedido
do Alan, 10/11/2026); `_faltando_na_fazenda` = municipio (estado so' no Brasil) e culturas) e filtros "Cadastro
incompleto" e "Fazendas sem cadastro". **Dados preenchidos sozinhos** (pedido do Alan, 10/11/2026;
`_completar_dados_fazendas`, ao abrir Gerenciar Clientes): so' o que esta' VAZIO, nunca troca o digitado
(`_gravar_dados_vazios`) -- estado pelo fim do nome ("Dallas - MT", so' Brasil), culturas pelas safras de
Manejos (`farm_culturas`, sem area) e municipio/estado pela coordenada (OpenStreetMap Nominatim reverse,
`_municipio_da_coordenada`, thread de fundo 1 por segundo, cada fazenda tentada no maximo a cada 6 h; fora com
TESTING) -- aparece na proxima abertura da tela. **Cartoes iguais** (mesmo pedido): nome sozinho na 1a linha
(data a' direita) e selos na 2a (`.cad-selos`), fazenda sem cadastro na ordem "Fazenda sem cadastro", plano,
origem. Ponto "so' clima" NAO entra em
Gerenciar Clientes (nem nas sem cadastro, nem no "+ Novo cliente"/Ligar --
`_fazendas_sem_cadastro`; pedido do Alan, 09/11/2026): fica so' no Mapas de
Estudo. Cada linha de fazenda tem "🙈 Ocultar" (`save_site_ativo`; e' o UNICO lugar com Ocultar -- saiu de Manejos de
novo, pedido do Alan, 12/11/2026); fazenda
OCULTA some de Gerenciar Clientes (sem cadastro e dentro dos cadastros) e
fica so' em Gerenciar Fazendas Ocultas. Tamanhos dos cartoes = os da aba Manejos (pedido do Alan, 13/11/2026; ver
"Padrao Manejos" em Tipografia). Mapas de Estudo ficou so' com criar/editar ponto
(coordenada, raio, tipo) e remover.
**Mapas de Estudo -- estudo de viabilidade, regua e grupos** (pedido do Alan, 12/11/2026): tipos
sem parenteses ("🦠 Monitorar doenças", "☁️ Somente clima", "📐 Estudo de viabilidade"). Estudo de
viabilidade = tabela PROPRIA `pontos_estudo` (`models.criar/atualizar/excluir_ponto_estudo`), so'
nesta tela -- nao vira fazenda, nao liga em nada (projeto de venda de estacao); nas rotas
adicionar/editar/remover do Mapas de Estudo o `site_name` "estudo:<id>" (`_ponto_estudo_do_form`).
Pino LILAS claro (`#e5dbff`, pedido do Alan, 12/11/2026) + circulo tracejado do raio. **Pino do Mapas de
Estudo so' marca** (pedido do Alan, 12/11/2026): balao so' com nome, tipo e sinal -- sem quadro de esporos,
sem levar pra Manejos (clique na fazenda real so' abre o balao; ponto criado abre o Editar); sem a
"BioScout mais proxima".
O "Tipo de ponto" do Editar tem as 3 opcoes: estudo vira ponto de doenca/clima (cria a fazenda
estimada) e ponto vira estudo (apaga a fazenda: permissoes, envios, anotacoes; fazenda de cliente
recusada). Cartao da Anatel logo abaixo do mapa (`#cartao-anatel`). Regua no canto do mapa (📏 distancia km,
⬠ area ha + perimetro; ✕ limpa; medindo, o clique nao marca ponto novo; cada ponto da medida e'
arrastavel pra ajustar antes de terminar -- `.vertice-medida`, pedido do Alan, 12/11/2026). Pontos Criados em grupos:
Monitoramento de doenças, Só clima, Estudo de viabilidade (macro `cartao_ponto`). Cada cartao RECOLHIDO como
em Manejos (seta ▶/▼ no nome = o antigo "Editar": abre os dados e o pino arrastavel); dentro: "Grupo
econômico" (sugestoes `grupos_economicos`; ponto de doenca/clima grava como na Gerenciar Clientes --
cadastro do cliente ou `fazendas_dados`; estudo em `pontos_estudo.grupo_economico`; o grupo vai junto
quando o tipo muda; o formulario "Adicionar Ponto" tambem tem o campo), os campos, "Criado em ... por" abaixo do Pais e o Remover (pedidos do Alan, 12/11/2026).
**Plano de cada fazenda** (`app._plano_da_fazenda`, selo `selo_plano(site)` --
template global): fazenda REAL (BioScout) = Inteligência (`models.PLANO_FAZENDA_REAL`),
com a validade do cadastro se estiver ligada; as outras = o plano que vale hoje
pro cadastro (`_acesso_do_cliente`, `models.donos_das_fazendas_clientes`); sem
cadastro = "Sem plano". A tabela `planos_fazenda` (e a cobranca `estudo:<id>` no
webhook) ficou do 1o desenho, sem tela. O selo ("Avançado · até 31/03/2027", verde; amarelo aguardando pagamento;
vermelho vencido; cinza sem plano) aparece na frente do nome em Alertas
(dashboard.html), Manejos (fazendas.html), Recomendações e Pontos Criados. A
aba **Gráficos do site** tem o filtro "Plano" (`planos_disponiveis`,
`data-plano` em cada estacao; sem estacao marcada, manda todas as do plano;
guardado nos filtros da conta). **Fazendas sempre por plano** (pedido do
Alan, 09/11/2026): `_ordem_fazenda` comeca por `_ordem_do_plano` --
Inteligência, Avançado, Básico (ou outro plano), Essencial, sem plano --
depois pais e nome; vale em toda tela que usa essa ordem. **Grupo economico em
Recomendações** (mesmo pedido): 2+ fazendas com o mesmo grupo
(`_grupo_economico_da_fazenda` = grupo do cadastro a que a fazenda pertence,
ou o digitado na fazenda sem cadastro;
sugestoes dos grupos ja' usados `models.grupos_economicos`; mesmo nome = mesma
caixa) ficam numa caixa "<grupo>"
(selo do plano + "N fazendas"; abre e mostra os cartoes das fazendas;
`_agrupar_por_grupo_economico` marca `grupo`/`grupo_inicio`/`grupo_fim` e o
template abre/fecha a caixa) com "Texto agrupado" (todas numa mensagem so':
topo e rodape 1 vez, `_juntar_relatorios` -- o mesmo do "Todas as fazendas"
do app), "PDFs juntos" (o PDF de cada fazenda, um atras do outro), "Copiar
texto do grupo" e "Enviar grupo por WhatsApp" (destinatarios = os de todas
as fazendas do grupo + numero digitado; rota `send_grupo_whatsapp`, thread
de fundo, respeita o formato do plano de cada cliente).

**Cliente do app (pedido do Alan, 25/10/2026)**: 4o tipo de conta,
`users.is_cliente=1` (nunca admin), criado pelo cadastro direto (acima,
opcao "Assinar plano") e pago no Asaas (`webapp/asaas.py`, env `ASAAS_API_KEY`/`ASAAS_AMBIENTE`/
`ASAAS_WEBHOOK_TOKEN`, webhook `/webhooks/asaas`). Enxerga `/app/*` e,
se o plano liberar (recursos "site_*" em `RECURSOS_PLANO`, logo depois de
"clima"; pedido do Alan, 31/10/2026), as abas do site Mapa/Alertas/
Graficos/Manejos/Recomendacoes (`ABAS_SITE_CLIENTE` em `_restringir_cliente`;
menu do `base.html` so' com essas abas + "📱 App"; no app, aba "🌐 Site").
**App do cliente** (pedido do Alan, 08/11/2026): a barra de baixo
(`_abas_cliente.html`: Alertas (ex-"Site"), Graficos (ex-"Regiao"), Relatorios, Assinatura, Conta -- nomes trocados pelo Alan em 08/11/2026) e' fixa
no rodape e aparece TAMBEM nas abas do Site (incluida em base.html e
cliente_base.html); a aba aberta fica numa caixa verde-clara. **Conta**
(`cliente_home`, `/app`; Culturas + Conta juntas, pedido do Alan,
08/11/2026): dados da conta, tipo(s), cada cultura com a area e a area
total vindos do cadastro, as "Fazendas monitoradas" (as antigas lavouras;
ex-"Areas monitoradas", nome trocado pelo Alan em 08/11/2026 no app e em
Gerenciar Clientes),
quem mais recebe e "Trocar senha" (`cliente_conta` so' recebe o POST;
GET volta pra `/app`). "Quem mais recebe" salva sozinho, sem botao
(texto ao sair do campo, caixas na hora; header `X-Autosave` -> JSON,
pessoa incompleta nao apaga o que ja' estava salvo). Trocar senha (unico
botao Salvar): botao 👁 mostrar senha em cada campo, senha atual conferida
ao digitar (`/app/conta/conferir-senha`), nova >= 6 e confirmacao igual;
"Salvar senha" so' libera com tudo certo (pedidos do Alan, 08/11/2026). **Editar o que foi digitado** (pedido do Alan, 08/11/2026): botao
"✏️ Editar" no quadro da conta (`/app/conta/editar`, `cliente_conta_editar`:
nome completo, grupo economico, celular, e-mail; celular/e-mail de outra
conta recusado -- `conta_com_contato(..., exceto_user_id)`; usuario nao
muda; trocar o grupo renomeia as fazendas estimadas,
`_renomear_fazenda_cliente`) e em cada fazenda (`/app/pontos/<id>/editar`,
mesma tela da "Nova fazenda" ja' preenchida: nome, cidade/UF, tipo,
culturas/areas e local; renomeia/move a fazenda estimada e refaz o total do
cadastro pela soma das fazendas, `_recalcular_cadastro`; "Excluir fazenda"
ficou nessa tela; edicao SALVA SOZINHA, sem botao --
`_autosave_cliente.html`, header `X-Autosave` -> JSON {ok, erro}). Editar area NAO gera nem ajusta cobranca.
**Topo do cliente** (`_topo_cliente.html`, pedido do Alan, 08/11/2026): o
MESMO no app (cliente_base) e nas abas do Site (base.html troca o navbar da
equipe por ele): logo + menu ☰ com as abas do Site que o plano libera e as do
app (Graficos, Relatorios, Assinatura, Conta) + Sair (`#linkSair`, limpa o
cache offline). **Aba Relatorios** (`/app/relatorios`, `cliente_relatorios`):
um cartao RECOLHIDO por fazenda ("▶ <nome>", mesmo modelo de Manejos);
abrir carrega por fetch (`/app/relatorios/<id>.txt?ver=1`) e mostra o texto do relatorio (`_texto_relatorio`, o
mesmo do WhatsApp -- `_preparar_relatorio` monta os dados pros dois) com
cabecalho no formato do Alan (08/11/2026, TUDO em negrito): ━━ /
"*RELATÓRIO DE MONITORAMENTO*" / "*ONEAGRO - dd/mm/aaaa*" / (linha) /
"*<Grupo economico> - <FAZENDA>*" / (linha) / "*Informações relevantes para
manejo da(s) cultura(s) de: SOJA / MILHO*" / ━━
(`_cabecalho_relatorio_cliente` + `_whatsapp_titulo`, montado em
`_format_whatsapp_message` -- vale TAMBEM pro WhatsApp automatico/manual de
toda fazenda, pedido do Alan, 08/11/2026; grupo economico do cliente fica
como digitado, resto em maiusculas; ponto so' clima/fazenda sem cultura =
sem a linha "Informações"; o PDF tem o mesmo cabecalho, tudo em negrito
entre duas linhas -- `export_pdf.build_recommendation_pdf(titulo_fazenda=,
data_relatorio=)`). **Rodape** (texto e PDF, pedido do Alan, 09/11/2026):
"Este relatório é uma ferramenta de monitoramento e não substitui a
avaliação de um agrônomo responsável." + "Quer ter essa ferramenta para sua
lavoura, entre em contato com a OneAgro. (43)99114-7595"
(`export_pdf.AVISO_AGRONOMO`/`CONTATO_RELATORIO`; o PDF nao tem mais "Fonte do
clima" nem "Powered by BioScout", 09/11/2026; ponto so' clima continua
sem rodape no texto). **Sem "Última leitura BioScout"** no texto (WhatsApp/app) nem no PDF de
nenhuma fazenda (pedido do Alan, 10/11/2026). **Corpo do texto** (pedido do Alan, 09/11/2026):
"🌧️ *Previsão de chuvas:*" com 1 dia por linha ("09/11: 12.2 mm (80%)",
`_linhas_previsao_chuva`; eram 2 por linha, mas com a probabilidade quebrava no WhatsApp -- pedido do Alan, 13/11/2026) e cada doenca como "- *Moniliophthora:*" /
"Quantidade de esporos: 🔴 Alta" (Perigo; Atencao = 🟡 Média,
`_QUANTIDADE_ESPOROS`) / "Risco de infecção:" / "09/11 🟢; 10/11 🟡; ..."
(so' a cor por dia) / "Recomendações das instituições de pesquisa:" /
🧪 Biológicos / ⚗️ Químicos (a linha "_Recomendações..._" embaixo do titulo
"DOENÇAS EM ATENÇÃO / PERIGO" saiu); cartao
"Todas as fazendas (uma mensagem só)" no topo quando ha' mais de uma
(`/app/relatorios/todos.txt`: 1 cabecalho + o bloco de cada fazenda) e os botoes "Enviar pelo WhatsApp" (wa.me/?text=),
"Copiar texto" e "Baixar .txt" (`/app/relatorios/<id>.txt`). Na lista "Fazendas monitoradas" o "✏️ Editar" fica
logo na frente do nome; a pagina da fazenda (`cliente_ponto`) mostra SO'
culturas/areas, area total, municipio, estado, coordenadas e o mapa
(clima/risco/esporos sairam -- pedido do Alan, 08/11/2026). **Sem "Excluir conta"** no app (pedido do Alan,
08/11/2026; rota `/app/conta/excluir` retirada) -- o Master exclui em
Gerenciar Clientes. Depois do login o
cliente entra direto no Site (`_inicio_cliente`: 1a aba do Site que o plano
libera; sem nenhuma, nas Culturas).
Nelas o cliente e' como um Usuario: so' as fazendas liberadas pra ele.
**Toda lavoura vira fazenda estimada, sem configuracao da OneAgro**
(pedido do Alan, 08/11/2026 -- antes so' com o recurso "fazenda_estimada"):
ja' entra na Programacao de Envios (agenda padrao do `create_virtual_farm`)
com o proprio cliente recebendo (`models.receber_relatorio_da_fazenda`). Lavouras de antes dessa regra: `_criar_fazendas_das_lavouras_antigas`
roda na subida do site (`_bootstrap_once`), cria a fazenda das que nao tem
e garante liberacao + destinatario das que ja' tinham (idempotente).
**Relatorio do Free enxuto** (pedido do Alan, 08/11/2026): fazenda de
cliente cujo plano que vale hoje e' o Free (`_relatorio_simples`,
`models.dono_da_fazenda_cliente`) sai sem "Clima agora", sem "Última leitura
BioScout" e sem "Produtos disponíveis na fazenda" (`_format_whatsapp_message(...,
simples=True)`; previsao de chuva e doencas ficam) -- no WhatsApp e na aba
Relatorios. **Relatorio por WhatsApp pelo plano**: recursos `whatsapp_texto` e
`whatsapp_pdf` (linhas na tabela de Planos e Valores; padrao: texto em
todos, PDF so' no Premium e Premium + Estacao -- migrado 1x, setting
`recursos_whatsapp_migrados`). `_send_site_whatsapp` manda a cada cliente
so' o formato do plano que vale agora (`_formatos_whatsapp_clientes` /
`_acesso_do_cliente`, que funciona sem requisicao); cliente sem acesso
nao recebe nada. Cada lavoura nova vira uma
fazenda ESTIMADA (mesmo processo do Mapa Interpolado, raio do plano, nome
"<lavoura> (<usuario>)", `_criar_fazenda_estimada_da_lavoura`) liberada so'
pra ele; apagar a lavoura ou o cliente apaga a fazenda estimada. O Master
tambem pode criar no Mapa Interpolado e liberar. Premium + Estacao: sem
fazenda estimada -- o Master libera a fazenda REAL da estacao BioScout do
cliente (Planos e clientes -> "Liberar fazendas"). Marca lavouras no mapa (`cliente_pontos`) e ve
esporos ESTIMADOS por IDW das fazendas reais (nunca nome/leitura de
fazenda real), clima, risco e fungicidas conforme o plano. Planos e
precos: Configuracoes -> **Planos** (`/admin/planos`, Master; so' os planos,
"Abas liberadas no app" = `RECURSOS_PLANO`); cortesia/plano de cada
cliente: Configuracoes -> Gerenciar Clientes. **Moeda do plano** (pedido do
Alan, 07/11/2026): cada plano tem `moeda` BRL (R$) ou USD (U$)
(`models.MOEDAS`, select ao lado do valor); o Asaas so' cobra em reais,
entao plano em U$ e' convertido na hora de assinar pela PTAX de venda do
Banco Central (`cotacao.dolar()`: olinda.bcb.gov.br, 6 h em memoria, ultimo
valor no setting `cotacao_dolar`; sem cotacao nenhuma = assinatura recusada
com aviso) -- `_iniciar_cobranca`. Telas mostram "U$ 20,00 (≈ R$ 110,03)"
(filtro `preco_plano`); a cobranca recorrente fica no valor em reais do dia
da assinatura. **Planos lado a lado** (pedido do Alan, 07/11/2026): Planos e Valores e'
UMA tabela comparativa (`.tabela-planos`): uma coluna por plano + "Novo
plano", 1a coluna fixa com o campo (Nome, Valor, Ciclo, Lavouras, Raio,
Ordem, A' venda, Descricao) e cada aba liberada (`RECURSOS_PLANO`) numa
linha, com uma caixa por plano; mesmos nomes de campo (`nome__<id>`,
`recursos__<id>`...) e um Salvar so'. **Linhas fora da tela** (pedido do
Alan, 07/11/2026): Lavouras, Raio e as abas fazenda_estimada/esporos/
risco/fungicidas/regiao (`app.RECURSOS_FORA_DA_TELA`) nao aparecem na
tabela, mas o plano continua com elas (campos escondidos no formulario --
salvar nao apaga; teste `PlanosLinhasForaDaTelaTest`). **Sem limite de lavouras** (pedido do
Alan, 07/11/2026: "nao teremos limites de lavoura e sim acessos ao
site"): o cliente marca quantas lavouras quiser (`cliente_ponto_novo`
nao confere `max_pontos`; "Ate' X lavouras" saiu de Minha assinatura,
/planos e da home do app; coluna "Lavouras (max.)" saiu da aba Planos do
Excel). A coluna `planos.max_pontos` ficou no banco, sem uso. **Parcelas pelo ciclo** (pedido do
Alan, 08/11/2026): o valor do plano e' o TOTAL de 12 meses ("Valor
total" em Planos e Valores) e o ciclo divide: Mensal 12x, Trimestral 4x,
Semestral 2x, Anual 1x (`plano["parcelas"]`/`plano["valor_parcela"]` em
`models._plano_row_to_dict`). O Asaas cobra UMA parcela por ciclo
(`_iniciar_cobranca`; plano em U$ converte a parcela). Telas do cliente
(Minha assinatura, /planos) mostram "12x de R$ 99,90" em destaque e
"Total R$ 1.198,80" por ultimo em letra menor (filtro `parcelas_plano`).
**Cartao de plano** (pedido do Alan, 08/11/2026; cadastro, Minha
assinatura e /planos): nome do plano sozinho na 1a linha e o preco num
quadro verde (`.preco-box` em cliente_base.html): "12x de U$ 60,00",
em U$ "≈ R$ X por parcela", "Total U$ 720,00 · ≈ R$ 3.960,94" e, com a
area plantada TOTAL (todas as culturas), o selo "R$ X por ha/ano" (total
em R$ / area; todos os planos pagos, nunca o Free). `_parcelas_plano(plano,
area_ha)` no servidor e `precoHtml` no JS do cadastro montam o MESMO HTML. No
JS, o quadro so' e' trocado se o HTML mudou (`trocar`): sair do campo de
area dispara "change" no mesmo toque que escolhe o plano e refazer o
quadro debaixo do dedo perdia o toque. Gerenciar Clientes mostra o mesmo
"R$ X por ha/ano" no quadro da assinatura (`por_ha_ano`, area total do
cadastro). Gerenciar Clientes mostra as
parcelas no quadro da assinatura ("12x de R$ 99,90 (total ...)") e no
seletor de plano da cortesia. **Tabela de apoio** (Planos e
Valores, pedido do Alan, 07/11/2026): faixas FIXAS de area
(`app.FAIXAS_PRECO`: ate' 100, 250, 500, 1.000, 2.000, 3.000, 5.000,
10.000 ha e acima de 10.000), o Master digita so' o U$/ha de cada faixa;
raio e total no limite da faixa, ≈ R$ pela cotacao do dia, campo "Simular
area do cliente". A area do cadastro do cliente busca a faixa
(`app.preco_pela_area`; valor intermediario vai pra faixa de CIMA: 300 ha
-> "ate' 500 ha"; limite exato fica na propria faixa) e o cartao do
cliente em Gerenciar Clientes mostra "Preco pela area" (U$, ≈ R$, faixa,
U$/ha, raio da area). Setting `tabela_precificacao` = lista
{ate_ha (null = acima), usd_ha}; o formato antigo (10 areas livres)
aproveita so' as areas iguais a uma faixa. Rota `admin_planos_tabela`.
So' apoio: nao muda plano nem cobranca. **Area de cada faixa editavel** (pedido do Alan, 13/11/2026): coluna Area
  vira campo "ate' [N] ha" (`ate_ha_<i>`, salva sozinho; a ultima linha "acima de N" segue a penultima);
  `app._faixas_preco()` le os limites gravados no setting (crescentes; senao `FAIXAS_PRECO` padrao) e
  vale em toda conta (`_faixa_da_area`, `preco_pela_area`, cadastro). **Sempre em ordem de area**
  (pedido do Alan, 13/11/2026): a tela reordena as linhas ao sair do campo de area (JS `ordenar`) e a rota
  `admin_planos_tabela` grava ordenado (o valor/ha acompanha a area); so' area repetida e' recusada.
  **Preco fixo** -- COLUNA da tabela, uma caixa por faixa (`fixo_<i>`; gravado em cada item do setting como
  `fixo`, `app._fixo_por_faixa()`; a caixa unica antiga `tabela_preco_fixo` so' vale de padrao pra faixa gravada
  sem a marca; pedidos do Alan, 13/11/2026; colunas na ordem Area | valor/ha | Total | ≈ Total R$ | Preco fixo | ✕):
  faixa marcada, o cliente paga a area do LIMITE da faixa (350 ha cai em "ate' 400" = 400 x valor/ha);
  a faixa "acima de" continua pela area. Vale em todo o site: `preco_pela_area` (devolve `preco_fixo`/`area_cobrada`)
  -> plano com preco pela area, cobranca, fazenda adicional, Gerenciar Clientes ("cobrado por N ha"), Minha
  assinatura ("valor da faixa: N ha"), cadastro (`PRECO_FIXO` no JS) e o Simular da tabela. **Quantidade de
  faixas editavel** (mesmo dia): "+ Adicionar faixa" (nova = ultima x 2) e "✕" em cada faixa (minimo 1, maximo
  `app.MAX_FAIXAS` = 20); o JS renumera `ate_ha_<i>`/`usd_ha_<i>` (a linha "acima" e' a ultima) e a rota usa
  quantos `ate_ha_*` vierem. A coluna "Raio (km)" SAIU da tabela (pedido do Alan, 13/11/2026); o raio continua so' no
  cartao do cliente em Gerenciar Clientes (saiu tambem do "Simular area do cliente", mesmo dia). O paragrafo explicativo
  acima da tabela e o "vale para o preco pela area..." ao lado da Moeda tambem sairam (mesmo dia). **Moeda da tabela** (pedido do Alan,
09/11/2026): seletor "Moeda da tabela" Dolar (U$) / Real (R$) na propria
tabela (setting `tabela_precificacao_moeda`, `app._moeda_tabela`; os campos
seguem `usd_ha`/`total_usd` por compatibilidade, mas o valor esta' na moeda
escolhida). Em R$: sem conversao (some a coluna "≈ Total R$"),
`preco_pela_area` devolve `moeda`/`simbolo` e `total_brl` = total, o plano com
preco pela area (`_plano_para_area`) e a fazenda adicional
(`_cobrar_fazenda_adicional`, `adicional_moeda`) saem em R$, o cadastro
(`MOEDA_TABELA` no JS) e Gerenciar Clientes mostram R$. **Pino verde-claro**
(`PIN_COR_PLANO` `#b2f2bb`, pedido do Alan, 07/11/2026): fazenda criada
pelo plano (lavoura de cliente, `models.sites_de_clientes()` =
`cliente_pontos.site_name`) no Mapa e no Mapas de Estudo (`de_plano` no
`sites_data`); `cliente_pontos` entra na cascata de rename da fazenda
virtual (`_VIRTUAL_FARM_RENAME_TABLES`). No Mapas de Estudo a fazenda do
cliente aparece SEMPRE (mesmo sem fazenda real no raio ainda: pino normal,
balao "Sem fazenda real com leitura no raio ainda") e, em Pontos Criados,
com o selo "🟢 Cliente: <usuario>" (`models.clientes_por_site`; pedido do
Alan, 08/11/2026). Planos padrao
(`models.PLANOS_PADRAO`, criados pelo `init_db` so' se faltar o nome --
pedido do Alan, 31/10/2026): **Free** (valor 0, a venda), Basico, Premium e
Premium + Estacao (nascem fora de venda e com valor 0 ate' o Master por o
preco). Valor 0 = gratis: em "Minha assinatura" (cartoes com TODOS os
planos; os fora de venda aparecem como "Em breve", sem escolha; o quadro de preco do plano gratis diz "Grátis 7 dias" -- cadastro, Minha assinatura e /planos, pedido do Alan, 13/11/2026; o quadro
de situacao/"Aguardando pagamento" so' aparece depois de escolher um
plano) o Free ativa na hora, sem CPF nem Asaas. **Free = 7 dias**
(pedido do Alan, 08/11/2026): `cortesia_ate = models.fim_free(assinatura)`
= criacao da conta + `DIAS_FREE` (7); depois disso o Free nao ativa mais
(aviso). Os Free antigos com `SEM_PRAZO` tambem passaram a 7 dias do
cadastro (`init_db`, so' assinatura de plano de valor 0 sem preco pela
area; pedido do Alan, 08/11/2026). **Plano pago vale 1 ano a partir do
pagamento** (`models.MESES_VALIDADE_PAGO` = 12; `registrar_pagamento`:
pago_ate = vencimento do pagamento confirmado + 12 meses, sem a antiga
tolerancia de 5 dias por ciclo).
**Plano pago sem pagamento aprovado usa o Free** nesses mesmos 7 dias
(`app._acesso_cliente` -> plano efetivo = `models.plano_free()`; Minha
assinatura avisa "voce usa o plano Free ate' DD/MM"); passou dos 7 dias
sem pagamento = bloqueado. Trocar do Free pra um pago pede CPF/CNPJ e
limpa a cortesia do Free (o pago so' vale com pagamento). **Upgrade de plano** (pedido do Alan, 08/11/2026): com
plano ja' escolhido, Minha assinatura mostra so' o quadro da situacao (sem
lista de planos nem "Ver minhas lavouras") e o botao "⬆ Fazer upgrade de
plano" (`?upgrade=1`), que lista SO' os planos a venda acima do atual
(`_planos_superiores`: Ordem maior em Planos e Valores; "sob consulta" fora).
No `cliente_assinar` com `upgrade=1`, a assinatura antiga no Asaas e'
cancelada e a nova toma o lugar. Sem plano acima = sem botao. **Desconto no upgrade** (pedido do Alan, 08/11/2026): o que o
cliente ja' pagou na assinatura atual e ainda esta' dentro do ano de
validade (`models.credito_para_upgrade`: pagamentos confirmados com
`assinatura_pagamentos.subscription_id` = a assinatura atual, ou sem id;
valor cheio, sem proporcional ao tempo) sai do total do plano novo: o
resto e' dividido pelas parcelas do ciclo e a nova assinatura no Asaas
para nessas parcelas (`maxPayments`; renovacao depois e' no valor cheio).
Credito >= total = troca na hora, sem cobranca. **Fazenda adicional** (pedido do Alan,
08/11/2026): "+ Adicionar fazenda monitorada" (`cliente_ponto_novo`) tem as
mesmas opcoes do cadastro (nome "ex.: Faz Mourão", cidade/UF, tipo
Cereais/HF/Outros, culturas e area de cada uma, mapa; campos sem sufixo de
`_locais_do_form`); a fazenda soma no cadastro (`models.somar_ao_cadastro`:
culturas, area, area do preco). Plano pago com assinatura no Asaas = cobranca
adicional (`_cobrar_fazenda_adicional`): preco da area total nova pela Tabela
de apoio menos o da area anterior, em U$, numa assinatura PROPRIA no Asaas
(ref `fazenda:<ponto_id>`, mesmo ciclo/parcelas do plano, U$ pela cotacao);
Free ou plano ainda sem cobranca nao cobram; faixa sem U$/ha = "sob_consulta".
`cliente_pontos.adicional_status` (NULL/"incluida" = coberta, "pendente",
"ativa", "sob_consulta", "cancelada"), `adicional_pago_ate` pelos pagamentos
com `assinatura_pagamentos.ponto_id` (nunca mexem no `pago_ate` do plano nem
no credito do upgrade). 7 dias gratis da criacao (`models.ponto_liberado`);
depois, sem pagamento, a fazenda some das telas do cliente
(`get_user_permitted_site_names` tira `sites_bloqueados_de_clientes`), a
pagina dela mostra "acesso terminou", e o relatorio dela nao sai
(`_send_site_whatsapp`). Assinatura lista "Fazendas adicionais" (situacao,
U$/ano, "Pagar fatura desta fazenda", "Gerar cobranca" se falhou na hora --
`cliente_cobrar_fazenda`); Conta e Gerenciar Clientes mostram o selo.
Excluir a fazenda cancela a cobranca dela. **Sem acesso** (prazo vencido sem
pagamento): login cai direto na Assinatura (`_inicio_cliente`), a barra so'
tem Assinatura e Conta (`cliente_bloqueado`); Conta (`cliente_home`) e "quem
recebe" abrem sem plano. **Forma de pagamento** (Minha assinatura, PIX ou
Cartao de credito; `assinaturas.forma_pagamento`, `asaas.FORMAS_PAGAMENTO`):
vai no `billingType` das assinaturas novas e troca as existentes (plano e
fazendas adicionais, `asaas.alterar_forma_pagamento`, PUT com
`updatePendingPayments`); sem escolha = UNDEFINED (escolhe na fatura). Pagamentos antigos ficam
presos a' assinatura velha (`fechar_pagamentos_sem_assinatura`), o credito
nao e' reaproveitado. A tela do upgrade mostra "Com o desconto: ..." em
cada plano. **Clientes na aba Usuarios** (pedido do Alan, 10/11/2026): aparecem depois
de todos (`models.get_all_users(com_clientes=True)`, so' em `admin_users`), Acesso
"Cliente do app", botoes "Gerenciar Clientes" (Master), Redefinir senha e Excluir
(`admin_cliente_excluir`, cancela o Asaas; sem "Editar cadastro"). **Subordinados do
cliente**: os "Receber relatorio" (ate' 2, cadastro/app) sao subordinados com
`subordinados.pelo_cliente`=1; Master/Administrador inclui MAIS na aba Usuarios >
Subordinados (sem limite, `pelo_cliente`=0, selo "pela equipe"). O app mostra e troca
SO' os dele (`cliente_destinatarios`, `_extras_do_cliente(..., so_do_cliente=True)`);
os da equipe nunca sao apagados pelo app e recebem no formato do plano do cliente. Coluna **Grupo** entre Usuario e Email
(pedido do Alan, 10/11/2026): cliente = grupo do cadastro; equipe/usuario = grupos das
fazendas que recebe. Nos subordinados (linhas da aba Usuarios e tela Subordinados) a fazenda
aparece SEM o "<grupo> - " da frente (`_nome_sem_grupo`, template global `nome_sem_grupo`). App Android/iOS em
`mobile/` (Capacitor abrindo `/app`, user-agent `OneAgroApp`): dentro do
app nao aparece preco nem link de pagamento (regra das lojas).
**Aba Graficos do app** (`/app/regiao`, endpoint `cliente_regiao`; ex-"Regiao",
cujo mapa de estacoes por celula saiu -- pedido do Alan, 08/11/2026): filtro
por cultura no topo ("Minhas culturas" = todas as do cliente) e por fazenda
(se tiver mais de uma); tabela de **favorabilidade climatica** (hoje + 5
dias, A/M/B) das doencas com germinacao, do pais da fazenda
(`_favorabilidade_ponto`). **Filtro previo pelas culturas do cliente**
(pedido do Alan, 08/11/2026): so' entram doencas marcadas (Doenças/Culturas)
pra alguma cultura que ele informou no cadastro/fazendas
(`_culturas_do_cliente`); doenca sem cultura marcada fica de fora; cliente
sem nenhuma cultura da lista ve todas. Tabela (e graficos) AGRUPADOS por
cultura, na ordem das culturas do cliente (cadastro, depois fazendas;
linha "🌱 Soja"; doenca de varias culturas fica so' na 1a --
`_agrupar_por_cultura`). Doenca sem leitura recente de esporos nas estacoes do raio
da fazenda (`_cards_ponto_cliente`) vem com "(-)" na coluna Hoje (no lugar do
A/M/B de hoje; os dias da previsao continuam) e a legenda "(-) Sem medição" (pedido do Alan, 08/11/2026).
Com o recurso de plano `graficos_app` ("Gráficos: 7 dias de histórico e 5
de previsão"; Premium e Premium + Estacao, migrado 1x pelo setting
`recursos_graficos_migrados`): um grafico por doenca (Chart.js) com
favorabilidade % e esporos estimados, 7 dias + 5 de previsao (area cinza),
lendo o JSON do site `/graficos/dados?estacao=<fazenda estimada>` --
`_restringir_cliente` libera `graficos_dados` so' pra cliente com
`graficos_app`. Sem ele: aviso "fazem parte dos planos Premium". O recurso
"regiao" ficou sem uso. **Numero de esporos so' no Premium + Estacao** (pedido do Alan,
08/11/2026): recurso de plano `esporos_numero` ("Número de esporos (sem ele:
Alto, Médio e Baixo)", linha visivel em Planos e Valores; migrado 1x pelo
setting `recursos_esporos_numero_migrados`). Cliente sem ele ve Alto/Medio/
Baixo (status Perigo/Atencao/Normal) nas caixas: Alertas (dashboard.html),
miniaturas de Recomendacoes, balao do Mapa (o numero nem vai pra pagina) e
"Esporos na regiao" do app -- filtro `valor_esporo` e `esporos_numero` nos
templates (`app._ve_numero_esporos`; equipe OneAgro sempre ve o numero). Nos
graficos do app as barras viram nivel 1-3 (Baixo/Medio/Alto pelos limites
warn/danger da serie). A aba Graficos do site, o PDF e o texto do WhatsApp
continuam com o numero. Offline: `static/sw.js`
guarda cada pagina /app aberta (cache `oneagro-app-dados`, apagado no
logout) e devolve com `data-offline="1"` sem sinal; a home pre-carrega
lavouras + regiao. iOS so' roda service worker com WKAppBoundDomains
(Info.plist + `limitsNavigationsToAppBoundDomains`) -- dominio novo do
site tem que entrar la'.

## Arquitetura / conceitos tecnicos principais

- Flask + Jinja2 + Bootstrap + Leaflet 1.9.4, SQLite (`models.get_db()`, modo
  WAL), openpyxl para exportacao Excel, `data_reader.py` para dados de
  clima/esporos via CSV, `countries.py` para provedores de estacao por pais.
- **Leituras do spore_counts.csv** (`data_reader._derivado_das_leituras`):
  nomes de doenca, nomes cientificos, device ids e ultima leitura por
  fazenda/doenca ficam em memoria ate' o CSV mudar -- antes cada troca de
  aba varria as ~14 mil linhas varias vezes (Recomendações relia uma vez
  por fazenda na rosa dos ventos). Menu tem `speculationrules` (prerender
  "moderate" dos links, menos "Sair") pra trocar de aba mais rapido.
- **Cache por requisicao em `models.py`** (`@_cache_requisicao`, 01/11/2026):
  `get_all_virtual_farms`, `get_all_weather_station_overrides`,
  `get_all_site_countries`, `get_all_disease_translations`,
  `get_deactivated_site_names`, `get_all_site_solos` sao lidos 1x por
  requisicao (copia em `g._models_cache`); TODO `commit()` limpa o cache
  (`_Conexao.commit`) -- gravar sempre com `conn.commit()`, nunca `with
  conn:`. Recomendacoes caiu de 68 pra 32 conexoes (o resto e' fixo, nao
  cresce com o numero de fazendas). `PRAGMA journal_mode = WAL` so' na 1a
  conexao do processo (`_wal_ligado`, religado apos `restaurar_banco`).
- **Cache por requisicao via `flask.g`**: uma funcao wrapper checa
  `getattr(g, "_cache_key", None) is None` antes de chamar a funcao cara
  `models.get_all_*()`, guardando o resultado em `g` (reseta sozinho a cada
  requisicao). Usar esse padrao para evitar N+1 queries quando o mesmo
  "buscar tudo" e necessario uma vez por item num loop (ex.: loop de fazendas
  em `/recommendations`). Ja aplicado a: `_nome_exibicao`, ordem de
  fungicidas, overrides/bloqueios de fungicidas, info de doenca, produtos de
  fazenda.
- **Scheduler do WhatsApp**: `_whatsapp_scheduler_loop()` roda numa
  `threading.Thread`, chamando `_run_scheduled_whatsapp_sends()` e
  `_run_scheduled_ndvi_sends()` a cada 60s, sempre dentro de
  `with app.app_context():` (sem isso, qualquer codigo tocando `flask.g`
  falha silenciosamente dentro do try/except do loop — ja foi bug em
  producao, todos os envios agendados falhavam sem erro visivel).
- Cada fazenda tem horario (hora+minuto) configuravel para envio de
  texto/PDF via WhatsApp; horario e sugerido automaticamente para fazendas
  novas (comecando as 5:00, intervalo de 3 minutos entre fazendas) e so'
  quando ha' pelo menos um dia de envio marcado. Pontos "so' clima" (sem
  fazenda associada) sao sempre processados DEPOIS de todas as recomendacoes
  de fazenda, em cada tick do scheduler.
- **whatsapp-bridge** (`webapp/whatsapp-bridge/index.js`): fila sequencial
  unica, 15-25s entre mensagens (anti-deteccao de bot) — a resposta HTTP so'
  volta apos o envio ser processado na fila, entao os timeouts do lado Python
  (`webapp/whatsapp.py`) precisam ser maiores que a pior profundidade de fila
  possivel × delay por mensagem (hoje: 75s texto, 100s imagem/documento).
- **Autosave generico** (`webapp/templates/base.html`): `data-autosave`
  dispara salvamento via fetch; `data-autosave-reload` tambem recarrega a
  pagina apos salvar. Para campos de TEXTO em formularios
  `data-autosave-reload`, o salvamento so' dispara no `focusout` (perder o
  foco), nunca so' por debounce — evita salvar/recarregar a pagina no meio da
  digitacao. Campos de escolha (select/checkbox/radio) continuam no debounce
  normal (1200ms).
- **Sem botao Salvar em todo o site** (pedido do Alan, 12/11/2026; teste
  `test_salva_sozinho_sem_botao_salvar` abre toda tela e recusa botao "Salvar"): Planos e
  Valores e Tabela de apoio com `data-autosave` (o "Novo plano" so' e' criado com nome E valor;
  criado, a rota devolve `reload: true`, que o base.html segue; `aviso` aparece depois do
  "✓ Salvo"); Editar ponto do Mapas de Estudo com `data-autosave-reload` (soltar o pino acha o
  pais e salva; virar estudo pede confirmacao antes); Cortesia em Gerenciar Clientes (ex-"OK");
  Trocar senha / Definir nova senha / senha do app gravam sozinhas ao sair do campo com tudo
  certo. **Criar tambem salva sozinho** (pedido do Alan, 13/11/2026): formularios `data-autocriar` (JS no fim do
  base.html) -- Novo usuário, Novo subordinado, + Novo cliente, Adicionar doença -- sem botao: com os `required`
  preenchidos, cria quando a pessoa sai do quadro (1,5 s sem voltar; Enter tambem envia; falta obrigatorio = aviso
  na `.autocriar-status`). Ligar fazenda (Gerenciar Clientes e "Dados da fazenda") = select `data-auto-enviar`
  (confirma e envia ao escolher). Agrofit manual e CAR do NDVI enviam ao escolher o arquivo. Continuam com botao
  so' ACOES: login/cadastro/assinatura, enviar WhatsApp, Pré-visualizar/Pôr na galeria (NDVI), Redefinir senha,
  Excluir/Remover/Ocultar/Reativar, e os que dependem de clique no mapa ("➕ Criar ponto", "➕ Adicionar fazenda").
- **Revisao de redundancias** (pedido do Alan, 12/11/2026): Alertas mostra so' "⏸ Sem leitura ha N
  dias" na fazenda bloqueada (o texto longo repetia em cada uma); NDVI com o formulario de CAR
  recolhido em "📎 Anexar CAR" (`<details>`); sairam o template sem uso `cliente_cadastro.html` e
  7 funcoes sem uso do models.py (set_ndvi_oculta, set_whatsapp_send_hour, get_whatsapp_days(_pdf),
  delete_farm_ndvi_historico_de_site, atualizar_cliente_ponto, get_plano_fazenda).
- **Caixas de esporos em Recomendações** (`.mini-card` em base.html, pedido do Alan, 13/11/2026): no
  maximo 8rem de largura; nome grande quebra em 2+ linhas em vez de esticar a caixa. Alertas (`.disease-card`)
  igual: nome/cientifico grande quebra dentro da caixa e as caixas de cada linha ficam da mesma altura.
- **NDVI e permissoes** (pedido do Alan, 13/11/2026): telas de fazendas liberadas (`admin_permissions.html`,
  `admin_report_permissions.html`) mostram o nome de exibicao (template global `nome_fazenda`), nao o `site_name`.
  Na aba NDVI: "Nuvens até" por fazenda (`NDVI_OPCOES_NUVENS`, setting `ndvi_nuvens_por_fazenda`,
  `models.get/set_ndvi_nuvens`, rota `salvar_ndvi_nuvens`, salva sozinho; vazio = padrao 5% no Pre-visualizar e 3%
  no automatico) vale no Pre-visualizar e no envio automatico; "📋 Copiar mensagem" copia o texto das imagens
  marcadas (mesma legenda do envio) pra mandar a mao; "Recebem o NDVI" fica no rodape do cartao.
  **Nuvem medida SOBRE A FAZENDA** (pedido do Alan, 13/11/2026: o `eo:cloud_cover` do catalogo e' do quadro de
  100 x 100 km -- "2% de nuvens" com a fazenda encoberta): o `EVALSCRIPT_NDVI` pinta nuvem com alfa ~0.1 (fora do
  contorno alfa 0), `ndvi_service.nuvens_na_fazenda` conta; `buscar_ndvi` tenta os dias mais perto da data
  (`MAX_CENAS_TENTADAS` = 6, quadro ate' `NUVEM_MAXIMA_DO_QUADRO` 90%) e fica com o 1o com a fazenda dentro do
  limite (erro lista o % de cada dia tentado). Pre-visualizacao: URL com `?v=` + `no-store` (o navegador mostrava a
  imagem anterior) e tocar na imagem abre grande.
  **Cartoes recolhidos como Manejos** (pedido do Alan, 13/11/2026): "▶ <nome>" (`.farm-toggle`) abre/fecha, com
  selos "📎 N CAR" e "🛰️ N" ao lado; abertos lembrados no navegador (`ndvi.abertas`), com pre-visualizacao pendente
  ja' abre. Coordenadas em destaque abaixo do nome (`.coords-fazenda`, pilula verde, link pro Google Maps). Anexar
  CAR salva sozinho (escolher o arquivo ou sair do campo do KML; sem botao).
- **Manejos -- TS/Sulco/Folha em boxes** (pedido do Alan, 12/11/2026): cada secao recolhida
  (▶, `.secao-toggle`, numero de boxes preenchidas ao lado) com boxes iguais a Aplicacoes: Data
  aplicacao, Talhao, Fungicidas quimicos, Fungicidas biologicos (campos `cx_*_<momento>`); 1 box
  vazia por padrao e mais uma aparece ao preencher. Gravado em `farm_produtos` (colunas novas
  `talhao`, `caixa`; `models.set_farm_produtos_caixas`: uma linha por tipo preenchido, mesmo
  `caixa`; o ingrediente ativo de produto que ja' existia fica). Linha antiga (sem caixa) vira uma
  box sozinha com "Produto (ingrediente)"; data/anotacao que nao e' data fica em campo de texto
  (`app._caixas_de_produtos`). Recomendações/texto/PDF continuam lendo o nome por tipo. Dados de
  Plantio (com o Espacamento dentro), Dados de Aplicacoes e Anotacoes por doenca tambem recolhidos
  (mesmo `.secao-toggle`, numero de preenchidos ao lado; pedido do Alan, 13/11/2026).
- **Renomear fazenda virtual**: `models.update_virtual_farm(...)` muda o
  `nome`, que muda o proprio `site_name` (cascata em varias tabelas).
  Acessivel tanto por Mapa Interpolado quanto pela aba Manejos (campo "Nome
  de exibicao").
- **Datas no Excel**: sempre escrever objetos `date`/`datetime` reais nas
  celulas (nunca string pre-formatada), usando
  `cell.number_format = "DD/MM/YYYY"` para exibicao — senao o Excel trata
  como TEXTO e o proprio sort/filtro do Excel quebra.
- `_agora_cuiaba()` em `models.py`: hora local de Cuiaba (UTC-4, sem horario
  de verao). Usado em todos os timestamps `criado_em`. **Nunca usar
  `datetime('now', ...)` do proprio SQLite** para comparar com essas colunas
  (retorna UTC, 4h de diferenca) — qualquer logica de corte por data deve
  calcular em Python com a mesma formula.
- **Graficos de esporos da fazenda estimada** (pedido do Alan, 01/11/2026):
  igual a fazenda real em Graficos, Recomendacoes (bloco "📈 Esporos por
  M³", que so' usa a serie da propria fazenda -- `serieDaFazenda`) e PDF
  (`app._historico_doenca`: real = leituras dela; estimada = IDW dia a dia
  das fazendas reais no raio, `virtual_farms.interpolar_serie_diaria`,
  terminando na leitura mais recente das vizinhas; ponto so' clima = sem
  grafico). Testes: `tests.test_fazenda_estimada`.
- **Fazenda REAL vem sincronizada de `data/sites.csv`** via `_ensure_sites_synced()`
  (`models.sync_sites`, roda a cada requisicao) — so' ADICIONA linha nova na
  tabela `sites`, NUNCA remove. Uma fazenda no CSV sempre reaparece sozinha,
  mesmo apagada na mao do banco.
- **Fazenda ativa/desativada** (`sites.ativo`, `models.set_site_ativo`/
  `get_deactivated_site_names`): esconde fazenda (real ou virtual) de TODAS
  as telas sem apagar — existe por causa do ponto acima (delete nao
  resolveria). OCULTAR so' em Manejos (botao) e Mapa Interpolado > Pontos
  Criados; REATIVAR so' em Fazendas ocultas (pedido do Alan, 18/10/2026 --
  Manejos nao mostra mais desativada). Preferir desativar a apagar quando
  o pedido for "sumir com uma fazenda".
- **Menos redundancia (pedido do Alan, 18/10/2026)**: menu tem UMA aba
  "Recomendações" com seletor de safra (lembra a ultima, `session`) e, no
  fim dela, so' pro Alan, os pontos "so'
  clima" (`_pontos_clima`, `_pontos_clima.html` -- substitui a aba Alertas
  Clima, `/alertas-clima` redireciona). A aba Alertas (`/dashboard`,
  dashboard.html) foi retirada e depois VOLTOU como era; o resumo "Alertas
  agora" que ficou um tempo em Recomendações foi retirado (pedidos do Alan,
  18/10/2026). Quem recebe (relatorio E NDVI) so'
  se marca na aba Envios; Usuarios mostra so' a contagem; NDVI so' mostra.
  Horario de envio so' em Envios (o padrao `get_whatsapp_send_hour` ficou
  so' interno). Exportar = Excel + historico de envios (o Excel tem tambem as abas
  "Clientes" -- cadastro + assinatura de cada cliente do app -- e "Planos"
  -- preco, ciclo, abas e quantos clientes; pedido do Alan, 01/11/2026). Solo so' na aba
  Mapa de Solos. Tudo que se EDITA da safra fica na aba Manejos (cultura
  por safra, TS/Sulco/Folha, plantio, aplicacoes e "Anotações por doença"
  -- `_anotacoes_da_fazenda`, mesmas rotas `save_farm_cultura`/
  `save_recommendations`); Recomendações so' mostra e envia. Pontos "so'
  clima" tem o mesmo bloco de clima agora/previsao/vento das fazendas.
- **Gerenciar Fazendas Ocultas** (ex-"Fazendas ocultas"; Configuracoes -> Gerenciar Fazendas Ocultas, so' "Alan Mauro",
  rota `fazendas_ocultas`): lista as desativadas e as sem leitura ha mais
  de `DADOS_BLOQUEIO_DIAS`, com botao Reativar. Reativar uma sem leitura
  grava a data da ultima leitura (setting `bloqueio_liberado`) e
  `_nivel_dados_site` passa a tratar como 'atencao' ate' chegar leitura
  nova. Usar `_nivel_dados_site(site, cards)`, nao
  `_nivel_dados_defasados` direto, onde houver fazenda. Fazenda bloqueada e
  ponto so' clima NUNCA mandam PDF (`_send_site_whatsapp`).
- **3 mecanismos diferentes de "listar fazendas"** (sem choke point unico):
  CSV cru (`read_sites()`/`get_dashboard_data(None,...)`) pro admin em
  varias telas, `models.get_user_permitted_site_names()` (DB) pro
  nao-admin, `models.get_all_sites()` (DB) nas 4 telas de permissao. Um
  filtro que precisa valer "sempre, admin ou nao" tem que tocar nos 3.
- **Aba "Envios"** (Configuracoes -> Envios, admin-only, rota
  `whatsapp_gerenciamento`/`/whatsapp`): consolida agenda de texto/PDF/NDVI
  + destinatarios por fazenda (inclusive pontos "so' clima") + quem recebe
  o NDVI (`toggle_ndvi_ativo`). Substituiu a
  agenda que antes ficava espalhada em Manejos/NDVI/Alertas Clima — ao
  mexer em agenda de envio, e' aqui que fica agora. Pontos "so' clima" NAO
  tem PDF nem anotacao (pedido do Alan): so' texto, manual ou agendado.
- **Aba Doencas** (pedido do Alan, 18/10/2026): cadastro manual ja' pede
  o nome cientifico -- germinacao vem de `_PESQUISA_GERMINACAO` e as
  culturas de `agrofit_dados.culturas_do_fungo` (culturas com fungicida
  registrado pro fungo); o botao "Buscar pelo nome cientifico" tambem
  marca as culturas se a doenca nao tiver nenhuma. As culturas de cada
  doenca ficam no proprio cartao (`culturas_card__<idx>`); a matriz
  Doencas x Culturas foi retirada -- sobrou so' o editor dos 12 nomes
  de cultura (form_id `matriz_culturas`).
- **Renomear doenca** (pedido do Alan, 10/11/2026): campo "✏️ Nome da doença (vale em
  todo o site)" no cartao de cada doenca em Doenças/Culturas (`nome_pt`, salva sozinho,
  o titulo do cartao acompanha). Tudo do site e' guardado pela chave em ingles
  (`display_name_en`), menos as anotacoes por fazenda (`recommendation_notes.doenca` =
  nome em portugues): `models.save_disease_translation` leva as anotacoes pro nome novo.
  Nome igual ao de outra doenca e' recusado (aviso no autosave, fica o nome antigo).
  **Nome editado nunca volta pra traducao** (pedido do Alan, 13/11/2026; ex.: "Grey Mould", doenca do Chile que
  deve ficar em ingles): coluna `disease_translations.nome_editado` (1 quando o nome muda no `save_disease_translation`);
  `ensure_disease_translations` nao traduz linha editada, `merge_duplicate_disease_translations` (Grey Mould x Grey mould)
  fica com o nome editado e `doencas_sem_traducao` ignora a editada (sem selo "Sem tradução"). Teste `DoencaNomeEditadoTest`.
- **Doenca nova do BioScout** (pedido do Alan, 11/11/2026): `data_reader.DOENCA_MAP` traz o nome
  em portugues (Brown Spot = Mancha Parda, Purple Seed Stain = Mancha Púrpura, White Mould = Mofo
  Branco e outros nomes comuns); `models.ensure_disease_translations` tambem troca pra portugues a
  doenca que ja' estava no banco com o nome em ingles (nunca troca nome editado nem repete nome).
  O sufixo do esporo no nome cientifico vira portugues ("Corynespora spp. - conidiósporo",
  `data_reader.cientifico_em_portugues`). Doenca sem traducao (`doencas_sem_traducao()`): selo pequeno "Sem tradução" (pilula ambar,
  `.selo-doenca`; Manual/Nova no mesmo estilo, a' direita do nome) + faixa ambar a' esquerda do cartao
  em Doenças/Culturas (pedido do Alan, 13/11/2026) (o aviso no topo do Alertas SAIU -- pedido do Alan, 12/11/2026).
  **Recomendações sem cultura na safra** continua sem mostrar doenca (decisao antiga), mas agora
  avisa "Nenhuma cultura definida para esta safra -- escolha em Manejos" em vez de "Nenhuma doença
  em Atenção ou Perigo" (que parecia "tudo bem").
- **Doenças/Culturas por pais + nome que nao volta** (pedido do Alan, 12/11/2026): os cartoes ficam
  em grupos "🌎 <pais>" pelo pais das fazendas onde o BioScout leu a doenca
  (`data_reader.sites_por_doenca` + pais da fazenda -- escolhido ou pela coordenada,
  `countries.detectar_pais_por_coordenada`); sem leitura = paises marcados; sem pais = Brasil.
  Brasil primeiro, depois os outros paises em ordem alfabetica; dentro, ordem alfabetica. Doenca
  lida e ainda sem pais marcado ganha os paises da leitura na matriz (`set_doenca_paises`, so' se
  vazio). **Nome editado voltava pro ingles**: a mesma doenca vinha com grafias diferentes
  ("Silver scurf"/"Silver Scurf") e a grafia escolhida era a 1a do arquivo (mudava a cada busca);
  `merge_duplicate_disease_translations` juntava ficando com o nome em ingles. Agora a grafia e'
  fixa (a ja' gravada no banco, `data_reader.definir_grafias_preferidas`, chamada em
  `_sincronizar_apelidos`) e a juncao mantem o nome digitado. Cada cartao tem o proprio
  "✓ Salvo" ao lado do nome (`data-autosave-group` + `.autosave-status`); o aviso de salvo de
  todo o site fica 5 s na tela.
- **Germinacao de fungos comuns** (pedido do Alan, 12/11/2026): `_PESQUISA_GERMINACAO` ganhou
  Rhizoctonia/Thanatephorus (mela/rizoctoniose), Waitea (R. zeae, mancha salmao), Sclerotium/Athelia rolfsii, Ascochyta, Phoma,
  Pyrenophora, Blumeria, Fusarium graminearum/Gibberella (giberela, 48 h), Venturia, Monilinia,
  Mycosphaerella e Pseudocercospora -- antes "Buscar pelo nome cientifico" dizia "Sem dados".
- **Fundir doencas** (pedido do Alan, 11/11/2026; Master): no cartao de cada doenca em
  Doenças/Culturas, "🔗 Fundir com..." + "Fundir" (rota `fundir_doenca`, confirmacao no navegador)
  junta ESTA doenca na escolhida (`models.fundir_doencas`): fica nome/culturas/fungicidas da
  escolhida, o que estiver vazio nela vem da outra (nome cientifico, germinacao), culturas e
  paises somam, tabelas de fungicida por linha (a da escolhida ganha), anotacoes por fazenda vao
  pra' que ficou (as duas, uma embaixo da outra). As LEITURAS do BioScout da que saiu (passadas e
  futuras) contam na que ficou: tabela `doenca_fusoes` (origem_en -> destino_en) ->
  `models.apelidos_de_doencas` -> `data_reader.definir_apelidos` (reescreve `displayName` ao ler
  o spore_counts.csv; relido a cada 30 s por `_apelidos_em_dia` e na subida); mesmo dia e fazenda
  = vale a MAIOR contagem (`data_reader._mesma_leitura_maior`, so' quando uma das leituras veio de
  apelido). Doenca manual nao pode ficar no lugar de uma do BioScout (aviso: junte a manual na do
  BioScout). Secao "🔗 Fusões feitas" com "Desfazer" (`desfazer_fusao_doenca` /
  `models.desfazer_fusao`: volta nome, nome cientifico, germinacao, culturas e paises; anotacoes
  e fungicidas que foram pra' outra ficam la').
- **Ordem em Doenças/Culturas** (pedido do Alan, 11/11/2026): doencas do Brasil (ou sem pais
  marcado) primeiro, depois as de outros paises, cada parte em ordem alfabetica do nome em
  portugues. Doenca que entrou ha' menos de `app.DIAS_DOENCA_NOVA` (3) dias (coluna
  `disease_translations.criado_em`, gravada ao entrar do BioScout ou a mao; as antigas ficam sem
  data) fica em azul escuro (`.doenca-nova`, faixa e fundo azul + selo "Nova"), depois volta ao normal.
- **Anotacao padrao por doenca** (Biblioteca de fungicidas, tabela
  `fungicida_nota_doenca`): vale nas Recomendacoes/texto/PDF de toda
  fazenda SEM anotacao propria daquela doenca (`_build_site_diseases`);
  anotacao escrita na fazenda aparece so' la'.
- **Botao Agrofit por doenca** (Biblioteca de fungicidas, rota
  `aplicar_pesquisa_registro`): usa a base ABERTA do Agrofit/MAPA
  (dados.agricultura.gov.br, recurso "Produto Formulado"), baixada pelo
  servidor UMA VEZ POR MES (`agrofit_dados.atualizar_se_necessario` no
  scheduler; indice em `agrofit_indice.json` ao lado do banco). Busca pelo
  NOME CIENTIFICO do fungo, em 3 operacoes SEPARADAS (pedido do Alan):
  "Quimicos" e "Biologicos" completam as linhas vazias (inclusive as
  apagadas na mao) com os mais registrados, ja' marcando as culturas;
  "Registros" marca/desmarca culturas dos quimicos (mistura de tanque de
  produtos registrados tambem conta -- `agrofit_dados.culturas_cobertas`).
  Toda cultura do site que o Agrofit NAO marcou passa por 2a conferencia
  nas bulas da ADAPAR (`adapar.py`: pagina de fungicidas lida 1x/mes, bula
  achada pela marca comercial do Agrofit, texto guardado em
  `adapar_bulas.json`) -- ex.: soja pra Alternaria. Todas as culturas do
  Agrofit entram como referencia na bula (a Alternaria na linha do Tomate
  nao marca a Soja da linha de cima). Produto sem cultura no Agrofit nem
  na ADAPAR: a linha e' limpa pro botao Quimicos (pedidos do Alan,
  18/10/2026). ADAPAR fora do ar = fica so' o Agrofit, nao limpa nada.
  A base aberta do servidor liga cultura x alvo de verdade (a copia do
  parquet do agrofit2 usada nos testes cruza TODAS as culturas com TODOS
  os alvos de cada produto -- nao tirar conclusao de registro dela). "Citrus" do site = "Citros" do Agrofit. Botao Quimicos so'
  traz fungicida quimico (`agrofit_dados.eh_fungicida_quimico`: classe
  "Fungicida" no Agrofit, sem oleo/neem/azadiractina/extrato); quimico que
  nao e' fungicida tambem nao conta em Registros/ADAPAR e some da
  Biblioteca (o Agrofit lista inseticida acetamiprido + bifentrina contra
  Septoria glycines). Indice sem classes/marcas = aviso "formato antigo"
  na Biblioteca (o filtro de classe nao funciona). Comparacao
  de nomes tolerante a grafia (`agrofit_dados.chave_quimico`/
  `chave_biologico`: benzovindiflupyr=benzovindiflupir, bixafem=bixafeno,
  "B. velezensis" expandido); sem produto no Agrofit
  (ex.: carbendazim, banido) ou linha vazia = culturas desmarcadas.
  REGRA (Alan): produto banido/suspenso NUNCA aparece (Biblioteca,
  Recomendacoes, texto, PDF, relatorio, Excel) -- `app._fungicida_proibido`:
  quimico em `agrofit_dados.INGREDIENTES_BANIDOS` ou sem registro vigente
  na base; linha fica vazia e o botao Quimicos pode preenche-la. Biologico
  = so' microrganismo (`agrofit_dados.eh_biologico`, por genero); oleo
  mineral/neem/azadiractina NAO sao biologicos (o Agrofit classifica neem
  como "microbiologico" -- nao confiar na classe). Toda doenca tem
  sempre 10 quimicos e 4 biologicos (`fungicida_data.LINHAS_POR_TIPO`),
  mesmo padrao pra doenca do BioScout ou manual (sem nada no
  fungicida_data.py, a lista inteira vem da tabela
  `fungicida_importado_item`, lida por `fungicida_data.get_recomendacao`).
  Ordem na Biblioteca = MESMA de Doenças/Culturas (pedido do Alan, 12/11/2026): grupos "🌎 <pais>"
  pelo pais de origem (`app._pais_de_cada_doenca`, usado nas duas telas), Brasil primeiro, ordem
  alfabetica dentro de cada pais (o antigo BioScout > manuais > sem dados saiu). A
  API AgroAPI da Embrapa foi descartada (nao gerava token). O portal do
  MAPA respondeu 403 ao link pelo NOME do dataset; o link com o id
  (`agrofit_dados._DOWNLOAD_DIRETO`, mesmo usado por
  github.com/matheusacorsi/agrofit2, que baixa todo mes dos EUA) e' o
  primeiro tentado. Ingrediente do Agrofit tem parenteses aninhados
  ("mancozebe (alquilenobis(ditiocarbamato))") -- ver `_sem_parenteses`.
  Mesmo assim existe o envio manual do arquivo na propria
  Biblioteca (rota `agrofit_enviar_arquivo`, limite de upload de 600MB so'
  nessa rota) -- o navegador filtra o CSV antes (so' linhas com
  "Fungicida" na coluna CLASSE) e manda .csv.gz (~265MB -> ~2MB no teste);
  `montar_indice` aceita .csv/.zip/.gz e tambem so' indexa fungicidas. O ambiente de nuvem do Claude nao alcanca esse portal.
- **Sinal de celular** (pedido do Alan, 31/10/2026, `webapp/sinal_celular.py`):
  mapas oficiais de cobertura prevista da Anatel (Mosaico SMP,
  `sistemas.anatel.gov.br/se/public/cmap.php`, ~70 zips
  `pred_{2G..5G}_{OPERADORA}_dbm-{N}.zip`, 2 limiares por tecnologia: o
  menor dBm = "bom", o maior = "fraco"; "5G_SA_NSA" conta como 5G). **So'
  Vivo, Claro e TIM, sem 2G** (pedido do Alan, 01/11/2026: `PRINCIPAIS`/
  `TECNOLOGIAS`; o resto nem e' baixado e `_FILTRO_SQL` ignora o que
  sobrar numa base antiga). O
  servidor baixa tudo sozinho 1x/mes (`atualizar_se_necessario` no
  agendador, em thread; ~170 MB, ~3 min, ~400 MB de memoria no pico) ou
  pelo botao "Baixar agora" do Master no Mapa Interpolado. Cada shapefile
  (um poligono com centenas de milhares de partes -- shapely inteiro e'
  lento demais) vira grade de bits de 0,0025 grau (~280 m) em blocos de 1
  grau, em `cobertura_anatel.db` ao lado do banco (fora do backup, ~42 MB).
  `estimar(lat, lon)` le 1 bit por mapa (~3 ms). Tela (`_sinal_celular.html`,
  pedido do Alan): um icone de barras por operadora, lado a lado --
  verde otimo (4G/5G forte), amarelo bom (4G/5G minimo ou 3G forte),
  vermelho fraco (so' 3G minimo), cinza com X sem sinal (Vivo/Claro/TIM
  sempre aparecem, pra comparar); detalhe por tecnologia no `title`.
  **Frequencia** (pedido do Alan, 01/11/2026): os mapas nao tem MHz -- vem
  da tabela de estacoes licenciadas da Anatel (`lic_table.php`, busca
  centro+raio de `RAIO_ANTENAS_KM`=20, 250 linhas por pagina, servico
  "010" = SMP): faixas da antena mais proxima de cada operadora/tecnologia
  (`consultar_frequencias`), consultada em SEGUNDO PLANO (uma por vez,
  `buscar_frequencias_em_segundo_plano`; falha = nova tentativa em 6h) e
  guardada por ponto na tabela `frequencias` do `cobertura_anatel.db`
  (some e e' refeita quando a base e' baixada de novo). Chip = icone +
  operadora + tecnologia que da' o nivel (empate: 4G antes do 5G) + MHz.
  Manejos mostra SO' uma linha discreta abaixo do nome da fazenda
  (`_sinal_celular.linha`, estilo `.sinal-linha` em fazendas.html: sem
  bordas, letra/icone menores, operadora sem sinal esmaecida, nenhuma com
  sinal = "Sem sinal previsto"; `app._sinal_das_fazendas`). Recomendacoes
  NAO mostra sinal (pedido do Alan, 01/11/2026). **So' Brasil** (pedido do Alan,
  01/11/2026): fazenda com outro pais cadastrado nao mostra sinal (nem no
  mapa da aba Sinal telefonia); ponto solto (clique, lavoura de cliente)
  pelo contorno dos paises (`app._no_brasil`, `/sinal-celular` devolve
  `fora_do_brasil`);
  `/sinal-celular` devolve `pendente` e as telas perguntam de novo. O
  Mapas de Estudo recebe o HTML pronto do `/sinal-celular`. Aparece em Manejos, no
  Mapa Interpolado (`/sinal-celular`) e em Novos cadastros. A aba
  **Sinal telefonia** foi criada e depois ACABOU (pedido do Alan,
  01/11/2026): virou camada do **Mapas de Estudo** (`/sinal-telefonia`
  so' redireciona; tiles `/sinal-telefonia/tile/...` e `/sinal-celular`
  usam `@aba_required("mapa_interpolado")`): caixa por operadora acima do
  mapa (lembra a escolha no navegador, `mapaEstudos.operadoras`), cada
  uma uma camada de tiles PNG (`sinal_celular.tile`, rota
  `/sinal-telefonia/tile/<operadora>/z/x/y.png`) na cor da operadora
  (`CORES_OPERADORA`), mais forte onde o sinal e' melhor (como o mapa de
  chuva); zoom <= 7 usa a media de quadrados de 10/40 celulas
  (`_visao`, montada na 1a vez, em memoria). Clicar no mapa mostra o
  quadro do ponto marcado (`#sinal-ponto`). O balao (tooltip) de cada
  fazenda/ponto do Mapas de Estudo tem a mesma linha de sinal de Manejos
  (`sinal_html`, montado na rota com `_sinal_das_fazendas`; so' Brasil). O cartao da base da Anatel
  com "Baixar agora" (Master) fica no Mapas de Estudo, antes de Pontos
  Criados. **5G so' conta onde a
  operadora tem 4G** (`_sem_5g_isolado` / `_grade`): o mapa
  5G_SA_NSA_VIVO -110 da Anatel tem um poligono grosseiro cobrindo o
  Sudeste inteiro (onde o 4G da Vivo cobre ~6%). Substituiu a
  1a versao por distancia de antena (ERBs, tabela `erbs` apagada).
- **Tipografia padrao** (pedido do Alan, 08/11/2026): `templates/_tipografia.html`,
  incluido em base.html e cliente_base.html (depois do CSS deles, antes do
  `extra_head` de cada tela): titulo de pagina (h1-h4, `h1.titulo`) 1.25rem
  negrito, subtitulo de secao (h5/h6) 1rem semi-negrito, conteudo 0.95rem
  (body, campos, botoes, tabelas), versoes `-sm` 0.85rem. Tela nova: usar
  h4/h1.titulo pro titulo e h6 pras secoes, sem tamanho proprio. No app,
  "Quem mais recebe o relatorio" virou **"Receber relatório"** com
  "Contato 1/2" (ex-"Pessoa"), tambem no cadastro; "Trocar senha" e' uma
  secao como as outras (h6 + campos pequenos). Conta: usuario/e-mail/celular em negrito e um
  pouco maiores; caixa "Culturas" com o tipo (Cereais/HF/Outros) maior no
  topo, culturas menores e "Area total · <nome do dono da conta>"
  (`nome_completo`, senao o usuario) -- o total e' de todas as fazendas,
  nao da 1a (antes mostrava "<fazenda> · <cidade>"; pedidos do Alan, 08/11/2026).
- **Padrao Manejos em todo o site** (pedido do Alan, 13/11/2026): a aba Manejos e' o modelo -- titulo da tela
  h4 (1.25rem), nome de cada cartao recolhivel 1rem negrito (`.farm-toggle`, `.cad-toggle`, `.doenca-toggle`,
  `.fung-toggle`, `.grupo-toggle`, `.relatorio-toggle`, `.editar-ponto-toggle` -- regra unica em `_tipografia.html`),
  secao dentro do cartao (`.secao-toggle`) no tamanho do conteudo, negrito; selos e textos pequenos nos tamanhos do
  `_tipografia.html` (sairam os tamanhos proprios de Gerenciar Clientes, o `fs-5`/1.15rem de Recomendações e os
  `font-size` soltos de 0.7-0.78rem em Planos, NDVI e Graficos do app). Componentes de dado (caixas de esporo,
  quadro do clima, preco, legendas e baloes de mapa) seguem com tamanho proprio.
- **Revisao geral de padrao** (pedido do Alan, 10/11/2026): titulo de toda tela (h4/h1.titulo,
  20 px) e o `<title>` = nome da aba no menu ("Usuários", "Gráficos", "Manejos", "Mapa",
  "Alertas", "Fungicidas", "Recomendações"...); textos visiveis com acento. Nenhuma tela
  muda o tamanho base da pagina (a Recomendações tinha `html { font-size: 14px }`, saiu).
  `_tipografia.html` tambem fixa a fonte (system-ui), cabecalho de tabela 0.85rem semi-negrito,
  rotulo de campo 500, selo 600 e texto `.small.text-muted`/`.form-text` 0.8rem. Conferido
  em todas as telas a 430 px e 1280 px: nenhuma passa da largura. **Nomes dos arquivos
  baixados** (`app._nome_arquivo`): `OneAgro_<Tipo>[_<Nome>]_<AAAA-MM-DD>[_<HHhMM>].<ext>` --
  PDF `OneAgro_Relatorio_<fazenda>_...pdf`, Excel `OneAgro_Exportacao_..._HHhMM.xlsx`,
  backup `OneAgro_Backup_...db.gz`, texto do app `OneAgro_Relatorio_<fazenda>_...txt` /
  `OneAgro_Relatorios_...txt` (hora de Cuiaba). **Gerenciar Exportações** em 3 cartoes:
  Planilha Excel (lista as abas), Backup do banco e Historico de envios por WhatsApp (filtros
  dentro do cartao).
- **Revisao geral de 13/11/2026** (pedido do Alan: vinculos, estrutura, fontes, acessos e hierarquias): crawler
  com os 4 niveis seguindo todo link/formulario interno (nenhum destino quebrado; 404 so' nas telas do QAP), as 10 rotas
  sem login sao as publicas de proposito (login, cadastro, /planos, webhook Asaas...), as so' `@login_required` filtram
  pelas fazendas liberadas, e a hierarquia (Admin nao mexe em Master, Master comum nao mexe no Alan, Usuario/Cliente
  barrados nas telas da equipe) testada na pratica. Corrigido: textos visiveis e mensagens de aviso (`flash`) sem
  acento, "Minha conta" -> "Conta" (igual a aba), h5 fora do padrao (Usuários e /planos) e o teste do Avançado que
  dependia da ordem dos arquivos de teste.
- **Botao "Atualizar" com progresso** (pedido do Alan, 11/11/2026): Alertas ("🔄 Atualizar",
  busca do BioScout), Fungicidas ("Atualizar agora" da base Agrofit) e Mapas de Estudo
  ("Baixar agora" da Anatel) usam a macro `_botao_atualizar.html` (`botao_atualizar(qual,
  acao, rotulo, classe)`; JS `.botao-atualizar` no fim do base.html): o clique faz POST com
  header `X-Progresso: 1` (a rota responde JSON, sem flash -- acabou o "Ja tem uma busca em
  andamento"), o botao vira "🔄 Atualizando... XX%" consultando `/atualizacao/<qual>/status`
  (`estado_atualizacao`: bioscout = `_fetch_state["pct"]` vindo do callback `progresso` de
  `bioscout_fetch.fetch_recent`; agrofit = bytes baixados / processando; anatel = mapas
  feitos/total) e a pagina recarrega ao terminar. Abrir a tela com a busca ja' rodando (ex.: a
  auto-atualizacao do Alertas) ja' mostra o progresso.
- **Estado primeiro, cidade da lista do IBGE** (pedido do Alan, 13/11/2026): no cadastro (cada Local), em
  Nova/Editar fazenda do app e em "Dados da fazenda" (Gerenciar Clientes) o Estado vem antes e a cidade vira um
  select com os municipios daquela UF (`_cidade_por_estado.html`, incluido em base.html e cliente_base.html: par
  `.par-uf-cidade` com `[data-uf-ibge]` e `[data-cidade-ibge]`; `window.cidadesIbge(raiz)` pra conteudo carregado
  depois). O campo e' input no HTML (sem JS / fora do Brasil / IBGE fora do ar continua texto livre); cidade
  gravada com outra grafia vira a oficial; gravada e fora da lista continua escolhida "(fora da lista)" -- nunca
  e' apagada pelo autosave.
- **Previsao de chuva do Mapa em todos os dias** (pedido do Alan, 13/11/2026): a grade em lote
  (`weather_forecast._grade_diaria`) pede `DIAS_GRADE + 1` (7) dias e `_resultado_vigente` corta os dias que
  ja' passaram e devolve sempre 6 -- antes, da meia-noite ate' a leitura das 04:45 (ou com a leitura falhando)
  a grade guardada era de ontem e o ultimo dia do ciclo ficava vazio. No mapa.html o ciclo automatico pula dia
  sem dado (`malhaChuva.comDados`).
  Enquanto a grade e' buscada, o Mapa mostra "Carregando previsão de chuva... XX%" (pedido do Alan, 13/11/2026):
  `weather_forecast.progresso_grade` (lotes feitos / total + tempo dentro do lote, ~1 min cada), devolvido como `pct`
  no 202 de `/chuva/grade`; o navegador pergunta a cada 5 s.
- **Previsao de chuva mais precisa** (pedido do Alan, 13/11/2026): relatorios (texto, PDF, Recomendações,
  pontos so' clima) usam a MEDIA de 3 modelos (`weather_forecast.MODELOS_CHUVA` = ECMWF IFS 0,25, GFS, ICON;
  `_chuva_media_modelos`, 2a chamada por ponto e janela, guardada em `registro["chuva_modelos"]`; falhou = modelo
  padrao) e mostram a probabilidade de chuva (`precipitation_probability_max` em `_DAILY_CAMPOS` -> `chuva_prob`;
  texto "12.2 mm (80%)" por `app._prob_chuva`, 3a linha no quadro do PDF, numero pequeno nas telas). No Mapa, alem
  da grade geral de 1 grau, uma grade FINA de 0,5 grau em volta de cada fazenda (+-1,5 grau, `GRADE_FINA_*` no
  mapa.html, no maximo 1.500 pontos), buscada depois da geral sem mensagem; onde ela tem valor, vale ela.
  **"Chuva hoje (prevista)"** no lugar de "Chuva agora" (que era so' a ultima hora; pedido do Alan, 13/11/2026):
  `chuva_hoje_mm` (media dos modelos do dia de hoje) + `chuva_hoje_prob` em `get_weather_forecast`, no quadro
  Clima agora do site/PDF ("7.4 mm (49%)") e no texto ("🌧️ 7.4 mm hoje (prev.) (49%)"). `chuva_atual_mm` segue
  existindo pro calculo de risco.
  Leitura guardada sem a chave `chuva_modelos` (formato antigo) e' buscada de novo na hora, sem esperar a
  proxima janela; falha na media dos modelos mantem a media anterior.
- **Paises na chuva do Mapa** (pedido do Alan, 13/11/2026): `countries.paises_com_estacoes()` = pais com
  provedor de estacao integrado (nao `sem_estacoes`; Chile so' com credencial DMC), SEM consultar o catalogo
  na hora -- antes a Bolivia sumia da previsao de chuva quando o servidor do SENAMHI Bolivia caia (502; o
  catalogo dela e o do Peru nao ficam no repo e se perdiam a cada deploy).
- **Telefone com DDI 55** (pedido do Alan, 08/11/2026): em todo o site
  "(66)99631-9500" (fixo "(66)3322-1100") -- `models.fmt_telefone_br`
  (filtro `telefone_br`, Excel, aviso no WhatsApp); outro DDI fica no
  formato antigo. Campos `numero_telefone*` com o `codigo_pais*` = 55 ganham
  a mesma mascara ao digitar (`_mascara_telefone.html`, incluido em
  base.html e cliente_base.html); o servidor sempre le so' os digitos.
- **WhatsApp desconectado = botoes de envio desativados** (pedido do Alan,
  01/11/2026): `whatsapp.conectado()` (status do provedor guardado
  `STATUS_CACHE_S`=30 s), `whatsapp_conectado()` nos templates. Desativa
  "Enviar por WhatsApp" + caixas PDF/Texto (Recomendacoes), "Enviar agora"
  dos pontos so' clima, "Enviar selecionadas" (NDVI) e "Enviar teste" (editar
  usuario); aviso `_aviso_whatsapp.html` no topo de Recomendacoes/NDVI
(cliente do app NAO ve o aviso -- pedido do Alan, 08/11/2026). As
  rotas tambem recusam (`_whatsapp_desconectado`, flash e volta). Envios
  (so' configuracao) e Gerenciar WhatsApp (ex-"Conexao WhatsApp") nao mudam.
- **Intervalo entre envios de WhatsApp** (decisao do Alan, 16/10/2026 --
  substitui o escalonamento de 1h validado em set/2026, ver
  `.claude\memory\oneagro_whatsapp_stagger_validado.md`): nunca dois
  envios ao mesmo tempo (manual, agendado, NDVI, fazendas diferentes); um
  novo envio so' comeca `whatsapp.INTERVALO_ENTRE_ENVIOS_S = 15` segundos
  depois do anterior terminar (portao `_um_envio_por_vez` em
  `webapp/whatsapp.py`). `RECIPIENT_STAGGER_HORAS = 0`. Envio manual roda
  em thread de fundo (a tela responde na hora). Dedup "ja enviado hoje"
  continua por TELEFONE.

## Como trabalhar neste projeto (licoes ja aprendidas — nao repetir)

- **Sempre testar de ponta a ponta antes de considerar concluido**: testes
  unitarios Python + testes HTTP via `test_client` + verificacao ao vivo no
  navegador quando fizer sentido (mudanca visual/UX). So' reportar como
  pronto depois de rodar tudo isso.
- **Nunca usar fazenda real para teste no navegador** — usar sempre uma
  fazenda descartavel, com nome unico e obviamente de teste (ex. "Zzz Teste
  ..."), e limpar ao final. Nunca selecionar elemento por posicao/primeiro-
  match (`querySelectorAll(...)[0]`) — ja causou alteracao acidental de
  fazenda real. Selecionar sempre explicitamente pelo elemento certo.
- Ao corrigir um bug de UX, preferir um fix ESCOPADO (so' onde o problema
  existe) em vez de mudar o comportamento geral para todo mundo.
- **Ao final de cada pedido: sempre fazer merge e push** (pedido explicito
  do Alan, 15/10/2026). Fluxo: branch nova a partir da `main` -> commit ->
  push -> PR -> merge na `main` (o Railway publica sozinho) -> conferir o
  deploy. Nao precisa perguntar "quer que eu faca o merge?".
- Antes de qualquer operacao destrutiva do git (reset, checkout, clean),
  rodar `git status` primeiro. Sempre criar commits novos (nunca `--amend`),
  nunca pular hooks.
- Nao adicionar funcionalidades/abstracao alem do pedido; nao adicionar
  comentarios obvios no codigo; nao criar documentos de planejamento a menos
  que seja pedido.

## Plano salvo, em andamento: migracao para escala (aprovado pelo Alan, 27/10/2026)

Meta: 1.500 acessos simultaneos, 10 mil usuarios, 5 mil fazendas. Hoje:
1 processo gunicorn com 4 threads, SQLite (52 tabelas, 278 consultas,
todas em `models.py`), CSVs/JSONs no disco do servidor, agendador e
envio manual em threads dentro do site, caches em memoria por processo,
WhatsApp pelo bridge (~2-3 mil mensagens/dia no maximo). Cada fase vai
pra producao sozinha; testar tudo primeiro no QAP (ambiente de teste).

- **Fase 0 -- preparacao e medicao** (FEITA, 27-28/10/2026):
  - Backup diario automatico (`models.fazer_backup_diario`, no loop do
    agendador, a partir das 3h de Cuiaba, guarda 7 em
    `<pasta do banco>/backups/`) + botao "Baixar backup do banco" em
    Exportar (`/admin/exportar/backup`, Master, .db.gz).
  - Testes: `python -m unittest tests.test_telas tests.test_assinaturas tests.test_solicitacoes tests.test_planos tests.test_sinal_celular tests.test_fazenda_estimada tests.test_whatsapp_desconectado tests.test_menu_config tests.test_fazenda_adicional`
    (rodar de `webapp/`). `test_telas` abre TODA tela GET sem parametro
    com Master/Administrador/Usuario/Cliente (sem erro 500, rede
    desligada) e confere as regras de acesso; tambem passa o JavaScript de
    cada tela (Master e Cliente) no `node --check` -- um "});" sobrando
    derrubou o mapa do Mapas de Estudo (11/11/2026). Rodar antes de cada PR da
    migracao; tela nova entra sozinha no teste.
  - Teste de carga: `loadtest/locustfile.py` (Locust; NUNCA contra
    producao). Linha de base local (mesma config do Railway, 8 fazendas,
    servicos externos fora, 4 CPUs): 50 pessoas = mediana 41 ms / p95
    220 ms; 100 = 130 / 800 ms; 200 = 2.200 / 4.700 ms (Recomendacoes
    6,6 s no p95). Teto ~30 pedidos/s. Repetir no QAP.
  - Ambiente de teste **QAP** criado pelo Alan (28/10/2026): Railway
    Environment "QAP", servico na branch `qap` (auto-deploy),
    https://monitoresporos-qap-bb3f.up.railway.app. O site detecta pelo
    `RAILWAY_ENVIRONMENT_NAME` (`app.AMBIENTE_QAP`): faixa amarela
    "AMBIENTE DE TESTE" no topo e a tela **Restaurar backup** (Exportar,
    `/admin/restaurar-backup`, Master; 404 fora do QAP) que carrega o
    .db.gz do "Baixar backup do banco" da producao (confere integridade,
    guarda copia do estado anterior, roda `init_db`, desloga). QAP com o
    banco sem nenhum Master (recem-criado): o login mostra "Primeiro
    acesso: carregar o backup da producao" (`/qap/primeiro-acesso`, sem
    login, mesma conferencia `_restaurar_backup_enviado`; 404 fora do QAP
    ou assim que existir um Master). E na subida do QAP sem nenhum Master
    `_bootstrap_master_qap` cria "Alan Mauro" com `models.DEFAULT_PASSWORD`
    (so' no QAP; nunca mexe em quem ja existe). O QAP tem banco proprio: usuarios da
    producao so' existem la' depois de restaurar um backup. Pra levar
    codigo novo pro QAP: atualizar a branch `qap` a partir da `main` (ou
    de uma branch da migracao). O ambiente de nuvem do Claude nao alcanca
    o QAP (nem a producao) sem liberar o dominio na rede da sessao.
  - WhatsApp no QAP: `whatsapp._um_envio_por_vez` so' deixa sair pra
    numeros em `QAP_WHATSAPP_PERMITIDOS` (variavel do QAP, separados por
    virgula; sem ela nada sai) e o agendador do QAP nunca dispara
    relatorio/NDVI agendado. Pra testar WhatsApp no QAP: colocar
    `WHATSAPP_BRIDGE_URL` (a mesma da producao) + `QAP_WHATSAPP_PERMITIDOS`.
  - Conferir com o Alan: variaveis de WhatsApp/Asaas apagadas no QAP
    (`WHATSAPP_BRIDGE_URL`, `WHATSAPP_PROVIDER`, `WHATSAPP_CLOUD_*`,
    `ASAAS_API_KEY`, `ASAAS_WEBHOOK_TOKEN`) e `BIOSCOUT_WEB_SECRET` fixo
    nos dois ambientes (sem ele a chave vem de `webapp/secret_key.bin`,
    que nao serve com varias replicas). A branch `staging` no GitHub
    sobrou sem uso (nome antigo) -- pode apagar.
- **Fase 1 -- PostgreSQL** (maior fase): Postgres no Railway + `psycopg`
  com pool; `get_db()` devolvendo conexao com linhas por nome (`?` ->
  `%s`); `CREATE TABLE` + os "ALTER ... except OperationalError" viram
  migracoes numeradas (.sql + tabela de controle); `INSERT OR IGNORE` ->
  `ON CONFLICT DO NOTHING`, `lastrowid` -> `RETURNING id`, `COLLATE
  NOCASE` -> `lower()`, `PRAGMA table_info` -> `information_schema`, BLOB
  -> BYTEA; horarios continuam via `_agora_cuiaba()`. CSVs do BioScout
  (esporos, clima horario) viram tabelas indexadas; `agrofit_indice.json`,
  `adapar_bulas.json` e `open_meteo_diario.json` vao pra uma tabela de
  cache. Script de copia SQLite -> Postgres conferindo contagem por
  tabela, ensaiado no QAP. Virada a noite (depois dos envios), troca
  de `DATABASE_URL`; SQLite guardado 7 dias pra voltar.
- **Fase 2 -- worker separado**: servico "worker" no Railway (agendador
  WhatsApp/NDVI, busca BioScout, Agrofit/ADAPAR); site so' inicia as
  threads com `RUN_SCHEDULER`; "Atualizar dados" e envio manual viram
  pedidos numa tabela `fila_tarefas`; `_um_envio_por_vez` vira advisory
  lock do Postgres (trava tambem contra 2 workers).
- **Fase 3 -- escalar o site**: gunicorn 4 workers x 4 threads, 2+
  replicas; tirar `_ensure_sites_synced` de cada requisicao (worker faz
  depois da busca); Redis pros caches de clima/previsao/NDVI.
- **Fase 4 -- telas pra volume**: paginacao + busca em Usuarios (fim das
  3 consultas por usuario), permissoes, Recomendacoes/Manejos (filtro e
  carga sob demanda por cartao), Envios; markercluster nos mapas; indices
  em fazenda/usuario/data.
- **API oficial da Meta -- parametros prontos no site** (pedido do Alan, 13/11/2026): variaveis no Railway
  `WHATSAPP_PROVIDER=cloud_api`, `WHATSAPP_CLOUD_ACCESS_TOKEN`, `WHATSAPP_CLOUD_PHONE_NUMBER_ID`,
  `WHATSAPP_CLOUD_APP_SECRET`, `WHATSAPP_CLOUD_WEBHOOK_VERIFY_TOKEN` (opcionais: `..._TEMPLATE_RELATORIO`
  [relatorio_fazenda], `..._TEMPLATE_RELATORIO_TEXTO` [relatorio_fazenda_texto], `..._TEMPLATE_NDVI` [ndvi_fazenda],
  `..._TEMPLATE_IDIOMA` [pt_BR], `..._API_VERSION` [v21.0]). 3 modelos: com PDF (cabecalho documento + {{1}} nome,
  {{2}} fazenda, {{3}} data, {{4}} doencas), sem PDF (mesmo corpo; plano so' texto e ponto so' clima --
  `send_relatorio_texto_modelo`) e NDVI (cabecalho imagem + {{1}}..{{3}}; `app._enviar_ndvi`). Webhook
  `/webhooks/whatsapp` (sem login; GET = verify token, POST = assinatura X-Hub-Signature-256 com o APP_SECRET):
  status entregue/lido/falhou vai pra `whatsapp_envio_log.status_meta` (achado pelo `[wamid]` que o envio grava no
  texto; aparece no Historico de envios) e mensagens recebidas pra tabela `whatsapp_recebidas`. Gerenciar WhatsApp
  tem o quadro "API oficial da Meta" (variaveis preenchidas ✅/⬜ sem mostrar valor, modelos, URL do webhook,
  ultimas mensagens recebidas). Testes `ApiOficialMetaTest` e o envio por plano em `test_fazenda_estimada`.
- **Umbler Talk** (pedido do Alan, 13/11/2026 -- escolhida no lugar da Meta direta): `WHATSAPP_PROVIDER=umbler`,
  `webapp/whatsapp_umbler.py` (API https://app-utalk.umbler.com/api/v1, Bearer). Variaveis: `UMBLER_TALK_TOKEN`,
  `UMBLER_TALK_ORGANIZATION_ID`, `UMBLER_TALK_FROM_PHONE` (+55..., canal que envia), opcionais
  `UMBLER_TALK_TEMPLATE_RELATORIO_ID` / `_TEXTO_ID` / `_NDVI_ID` (ids dos modelos aprovados no painel da Umbler) e
  `UMBLER_TALK_WEBHOOK_TOKEN`. Texto/PDF/imagem por POST `/messages/simplified/` (multipart, campo `File`); modelos
  por `/template-messages/simplified/` (`TemplateId` + `Params`). Sem os 2 ids do relatorio, `usa_modelo_oficial()` e'
  falso e o relatorio sai como no bridge (texto completo + PDF); NDVI sem id = imagem com legenda. Status pelo GET
  `/members/me/`. Webhook `/webhooks/umbler?token=<UMBLER_TALK_WEBHOOK_TOKEN>` (a Umbler nao assina): estado
  Sent/Received/Read/Failed vira enviado/entregue/lido/falhou no Historico (`atualizar_status_meta`, pelo
  "enviado [id]") e mensagem do contato vai pra `whatsapp_recebidas`. Gerenciar WhatsApp: quadro "Umbler Talk"
  (variaveis ✅/⬜, URL do webhook), quadro das mensagens recebidas e o da Meta. Teste `UmblerTalkTest`.
- **Fase 5 -- WhatsApp em volume**: `WHATSAPP_PROVIDER=cloud_api`
  (`whatsapp_cloud_api.py`), exige verificacao da empresa na Meta e
  modelos aprovados, custo por conversa; fila com novas tentativas;
  intervalo de 15 s so' no bridge (reserva).
- **Fase 6 -- servicos externos**: clima por quadricula (fazendas
  proximas na mesma consulta), plano pago do Open-Meteo se preciso;
  NDVI/solo sempre pela fila do worker.
- **Fase 7 -- carga final**: meta 1.500 simultaneos com p95 < 2 s nas
  telas principais; ajustar replicas/processos.

## Notas / historico arquivado

Arquivos de memoria antigos (ligados a uma conta/sessao anterior) ficavam em
`C:\Users\AlanMauro\.claude\projects\...\memory\` — esse local foi
descontinuado. A partir de agora, este `CLAUDE.md` (raiz do repo) e a pasta
`.claude\memory\` DENTRO deste projeto sao a fonte de verdade. Novas
memorias/decisoes relevantes de longo prazo devem ser gravadas aqui ou em
`.claude\memory\`, nao mais no caminho antigo.
