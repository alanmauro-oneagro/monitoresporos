# OneAgro Monitor (BioScoutMonitor)

Contexto persistente deste projeto. Carregado automaticamente sempre que uma
sessão do Claude Code trabalha nesta pasta, independente de qual conta/usuario
Claude esta sendo usada.

Mantido por: Alan Mauro (OneAgro, alan.mauro@outlook.com), unico responsavel pelo app.

- App: Flask "OneAgro Monitor" — monitoramento de esporos/clima/doencas em
  fazendas.
- Repo: github.com/alanmauro-oneagro/monitoresporos
- Deploy: Railway, auto-deploy a partir da branch `main`.
- URL producao: https://monitoresporos-production.up.railway.app
- Codigo do app fica em `webapp/` dentro deste repo.

## Regra de negocio permanente (nao mudar sem confirmar com o Alan)

**Niveis de acesso (pedido do Alan, 19/10/2026): Usuario, Administrador
e Master.** Usuario (`is_admin=0`) ve so' as fazendas liberadas; Administrador
(`is_admin=1`) ve todas as fazendas. Quais abas do menu Configuracoes
(Usuarios, Doencas, Fungicidas, Mapa Interpolado, Mapa de Solos, NDVI,
Envios) cada um desses dois niveis abre e' o Master que decide na aba
**Acessos** (`/admin/acessos`, `ABAS_CONFIG`, setting `acessos_por_nivel`;
padrao: Administrador = Usuarios + Mapa Interpolado, Usuario = nenhuma).
Rotas dessas abas usam `@aba_required("<chave>")`; aba nova do menu
Configuracoes deve entrar em `ABAS_CONFIG`. Ninguem cria, exclui ou
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

**Cliente do app (pedido do Alan, 25/10/2026)**: 4o tipo de conta,
`users.is_cliente=1` (nunca admin), criado sozinho em `/cadastro` e
pago no Asaas (`webapp/asaas.py`, env `ASAAS_API_KEY`/`ASAAS_AMBIENTE`/
`ASAAS_WEBHOOK_TOKEN`, webhook `/webhooks/asaas`). So' enxerga `/app/*`
(`_restringir_cliente`); marca lavouras no mapa (`cliente_pontos`) e ve
esporos ESTIMADOS por IDW das fazendas reais (nunca nome/leitura de
fazenda real), clima, risco e fungicidas conforme o plano. Planos,
precos e cortesia: Configuracoes -> Planos e clientes (`/admin/planos`,
Master). Clientes nao aparecem na aba Usuarios. App Android/iOS em
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

## Plano salvo, ainda nao implementado

Nenhum no momento (atualizado 2026-09-21) — o plano anterior (catalogo de
autocompletar, correlacionar atividades no Relatorio Diario, raster de
chuva interpolado, mapa de chuva no Mapa Interpolado, "esqueci a senha")
foi TODO implementado e ja esta em producao. Resumo completo do trabalho
recente em `.claude\RESUMO_PARA_NOVA_CONTA.md`.

## Notas / historico arquivado

Arquivos de memoria antigos (ligados a uma conta/sessao anterior) ficavam em
`C:\Users\AlanMauro\.claude\projects\...\memory\` — esse local foi
descontinuado. A partir de agora, este `CLAUDE.md` (raiz do repo) e a pasta
`.claude\memory\` DENTRO deste projeto sao a fonte de verdade. Novas
memorias/decisoes relevantes de longo prazo devem ser gravadas aqui ou em
`.claude\memory\`, nao mais no caminho antigo.
