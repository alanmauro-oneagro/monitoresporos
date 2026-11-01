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
e "Programação de Envios" (`whatsapp_gerenciamento`, chave `envios`), "Mapas de Solo" (`solo`); onde
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
(login) e o "Assinar" de `/planos` levam a `/solicitar-acesso`; `/cadastro`
so' redireciona pra la'. A pessoa escolhe **Assinar plano** ou **Ter acesso
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
"Excluir cliente". Cliente sem registro de cadastro (de antes do
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

**Cliente do app (pedido do Alan, 25/10/2026)**: 4o tipo de conta,
`users.is_cliente=1` (nunca admin), criado pelo cadastro direto (acima,
opcao "Assinar plano") e pago no Asaas (`webapp/asaas.py`, env `ASAAS_API_KEY`/`ASAAS_AMBIENTE`/
`ASAAS_WEBHOOK_TOKEN`, webhook `/webhooks/asaas`). Enxerga `/app/*` e,
se o plano liberar (recursos "site_*" em `RECURSOS_PLANO`, logo depois de
"clima"; pedido do Alan, 31/10/2026), as abas do site Mapa/Alertas/
Graficos/Manejos/Recomendacoes (`ABAS_SITE_CLIENTE` em `_restringir_cliente`;
menu do `base.html` so' com essas abas + "📱 App"; no app, aba "🌐 Site").
Nelas o cliente e' como um Usuario: so' as fazendas liberadas pra ele.
Basico/Premium (recurso "fazenda_estimada"): cada lavoura nova vira uma
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
cliente: Configuracoes -> Gerenciar Clientes. Planos padrao
(`models.PLANOS_PADRAO`, criados pelo `init_db` so' se faltar o nome --
pedido do Alan, 31/10/2026): **Free** (valor 0, a venda), Basico, Premium e
Premium + Estacao (nascem fora de venda e com valor 0 ate' o Master por o
preco). Valor 0 = gratis: em "Minha assinatura" (cartoes com TODOS os
planos; os fora de venda aparecem como "Em breve", sem escolha; o quadro
de situacao/"Aguardando pagamento" so' aparece depois de escolher um
plano) o Free ativa na hora, sem CPF nem Asaas (`cortesia_ate =
models.SEM_PRAZO`, tela mostra "Sem prazo"); trocar pra um pago pede
CPF/CNPJ e so' entao tira o "sem prazo" (acesso passa a depender do
pagamento). Clientes nao aparecem na aba Usuarios. App Android/iOS em
`mobile/` (Capacitor abrindo `/app`, user-agent `OneAgroApp`): dentro do
app nao aparece preco nem link de pagamento (regra das lojas).
Recurso de plano "regiao" (`/app/regiao`, pedido do Alan, 26/10/2026):
estacoes reais ate' o raio do plano das lavouras do cliente, agrupadas em
celulas de 0,1 grau (~11 km) e rotuladas so' pela cidade INMET mais
proxima ("Regiao de X/UF") -- nunca site_name. Offline: `static/sw.js`
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
  so' interno). Exportar = Excel + historico de envios. Solo so' na aba
  Mapa de Solos. Tudo que se EDITA da safra fica na aba Manejos (cultura
  por safra, TS/Sulco/Folha, plantio, aplicacoes e "Anotações por doença"
  -- `_anotacoes_da_fazenda`, mesmas rotas `save_farm_cultura`/
  `save_recommendations`); Recomendações so' mostra e envia. Pontos "so'
  clima" tem o mesmo bloco de clima agora/previsao/vento das fazendas.
- **Fazendas ocultas** (Configuracoes -> Fazendas ocultas, so' "Alan Mauro",
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
  Ordem na Biblioteca: BioScout do Brasil (ou sem pais), depois manuais,
  por ultimo as de outros paises. A
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
- **WhatsApp desconectado = botoes de envio desativados** (pedido do Alan,
  01/11/2026): `whatsapp.conectado()` (status do provedor guardado
  `STATUS_CACHE_S`=30 s), `whatsapp_conectado()` nos templates. Desativa
  "Enviar por WhatsApp" + caixas PDF/Texto (Recomendacoes), "Enviar agora"
  dos pontos so' clima, "Enviar selecionadas" (NDVI) e "Enviar teste" (editar
  usuario); aviso `_aviso_whatsapp.html` no topo de Recomendacoes/NDVI. As
  rotas tambem recusam (`_whatsapp_desconectado`, flash e volta). Envios
  (so' configuracao) e Conexao WhatsApp nao mudam.
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
  - Testes: `python -m unittest tests.test_telas tests.test_assinaturas tests.test_solicitacoes tests.test_planos tests.test_sinal_celular tests.test_fazenda_estimada tests.test_whatsapp_desconectado tests.test_menu_config`
    (rodar de `webapp/`). `test_telas` abre TODA tela GET sem parametro
    com Master/Administrador/Usuario/Cliente (sem erro 500, rede
    desligada) e confere as regras de acesso. Rodar antes de cada PR da
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
