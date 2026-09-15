# Contexto do projeto OneAgro Monitor — resumo para retomar em outra conta Claude

Cole esta mensagem inteira como a primeira mensagem numa conversa nova, dentro do
diretorio de trabalho `C:\Users\AlanMauro\OneDrive\OneAgro\BioScoutMonitor\webapp`,
para o Claude retomar com o contexto essencial.

## Quem sou / projeto

Sou Alan Mauro (alan.mauro@biotrop.com.br), mantenho sozinho o app Flask
"OneAgro Monitor" (monitoramento de esporos/clima/doencas em fazendas).

- Repo: github.com/alanmauro-oneagro/monitoresporos
- Deploy: Railway, auto-deploy a partir da branch `main`
- URL producao: https://monitoresporos-production.up.railway.app
- Diretorio de trabalho: `C:\Users\AlanMauro\OneDrive\OneAgro\BioScoutMonitor\webapp`

## Regra de negocio permanente (importante)

**Somente o usuario "Alan Mauro" (esta conta) deve poder ajustar
configuracoes gerais do site.** Varias rotas/relatorios admin sao
propositalmente restritos a `current_user.username == 'Alan Mauro'`
especificamente, NAO apenas `current_user.is_admin` — ex.: painel admin do
WhatsApp, relatorio de envios do WhatsApp, relatorio de fungicidas (no
dropdown de configuracoes do `base.html` e nas rotas correspondentes em
`app.py`). Isso e deliberado: outros usuarios marcados como admin NAO devem
ver essas telas. Ao criar novas rotas/relatorios/configuracoes gerais do
site, seguir esse mesmo padrao de gate por username, a menos que eu peca
explicitamente paridade para outros admins.

## Arquitetura / conceitos tecnicos principais

- Flask + Jinja2 + Bootstrap + Leaflet 1.9.4, SQLite (`models.get_db()`,
  modo WAL), openpyxl para exportacao Excel, `data_reader.py` para dados de
  clima/esporos via CSV, `countries.py` para provedores de estacao por pais.
- **Padrao de cache por requisicao via `flask.g`**: uma funcao wrapper
  checa `getattr(g, "_cache_key", None) is None` antes de chamar a funcao
  cara `models.get_all_*()`, guardando o resultado em `g` (reseta sozinho a
  cada requisicao). Usado para eliminar N+1 queries quando o mesmo "buscar
  tudo" e necessario uma vez por item num loop (ex.: loop de fazendas em
  `/recommendations`). Ja aplicado a: `_nome_exibicao`, ordem de
  fungicidas, overrides/bloqueios de fungicidas, info de doenca, produtos
  de fazenda.
- **Scheduler do WhatsApp**: `_whatsapp_scheduler_loop()` roda numa
  `threading.Thread`, chamando `_run_scheduled_whatsapp_sends()` e
  `_run_scheduled_ndvi_sends()` a cada 60s. Bug critico ja corrigido: o
  loop nao tinha contexto de app Flask, entao qualquer codigo tocando
  `flask.g` dentro de um envio agendado (como `_nome_exibicao`) lancava
  `RuntimeError`, engolido silenciosamente pelo try/except do proprio loop
  — ou seja, praticamente TODOS os envios agendados falhavam silenciosamente
  desde que o cache por `g` foi introduzido. Corrigido envolvendo o corpo
  em `with app.app_context():`.
- Cada fazenda pode ter horario (hora+minuto) configuravel para envio de
  texto/PDF via WhatsApp; horario e sugerido automaticamente para fazendas
  novas (comecando as 5:00, intervalo de 3 minutos entre fazendas) e so'
  quando ha' pelo menos um dia de envio marcado. Pontos "so' clima" (sem
  fazenda associada) sao processados sempre DEPOIS de todas as
  recomendacoes de fazenda terminarem de enviar, em cada tick do scheduler.
- **whatsapp-bridge** (`webapp/whatsapp-bridge/index.js`): fila sequencial
  unica (`sendQueue`), `MIN_DELAY_MS=15000`/`JITTER_MS=10000` (15-25s entre
  mensagens, anti-deteccao de bot) — a resposta HTTP so' volta DEPOIS do
  envio ser processado na fila, entao os timeouts do lado Python
  (`webapp/whatsapp.py`) precisam ser maiores que a pior profundidade de
  fila possivel × delay por mensagem (hoje: 75s texto, 100s
  imagem/documento).
- **Autosave generico** (`templates/base.html`): listeners delegados de
  `input`/`change` no `document`, debounce por formulario (`WeakMap`),
  `data-autosave` dispara um `fetch` POST com header `X-Autosave: 1`;
  `data-autosave-reload` tambem recarrega a pagina apos salvar com sucesso.
  Para campos de TEXTO em formularios `data-autosave-reload`, o salvamento
  so' dispara no `focusout` (perder o foco), nao mais so' por debounce —
  evita salvar/recarregar a pagina no meio da digitacao. Campos de escolha
  (select/checkbox/radio) continuam no debounce normal (1200ms).
- **Renomear fazenda virtual**: `models.update_virtual_farm(...)` muda o
  `nome`, que muda o proprio `site_name` (cascata em varias tabelas). Isso
  e' acessivel tanto por Mapa Interpolado quanto (desde recentemente) pelo
  campo "Nome de exibicao" na aba Fazendas.
- **Datas no Excel**: sempre escrever objetos `date`/`datetime` reais nas
  celulas (nunca string pre-formatada), usando
  `cell.number_format = "DD/MM/YYYY"` para exibicao — senao o Excel trata
  como TEXTO e o proprio sort/filtro do Excel quebra, mesmo que a ordem das
  linhas escrita esteja certa.
- `_agora_cuiaba()` em `models.py`: hora local de Cuiaba (UTC-4, sem
  horario de verao) = `(datetime.now(timezone.utc) - timedelta(hours=4))`.
  Usado em todos os timestamps `criado_em`. **Nunca usar
  `datetime('now', ...)` do proprio SQLite para comparar com essas
  colunas** (retorna UTC, 4h de diferenca) — qualquer logica de corte por
  data deve calcular em Python com essa mesma formula.

## Como eu gosto de trabalhar (licoes ja aprendidas, nao repetir)

- **Sempre testar de ponta a ponta antes de considerar concluido**: testes
  unitarios Python + testes HTTP via `test_client` + verificacao ao vivo no
  navegador quando fizer sentido (mudanca visual/UX). So' reportar como
  pronto depois de rodar tudo isso.
- **Nunca usar fazenda real para teste no navegador** — usar sempre uma
  fazenda descartavel, com nome unico e obviamente de teste (ex. "Zzz
  Teste ..."), e limpar ao final. Ja aconteceu de a fazenda real "Faz
  Mourao" ser acidentalmente renomeada/alterada durante teste ao vivo por
  selecao posicional (`querySelectorAll(...)[0]` pegando o primeiro
  elemento em vez do de teste) — sempre selecionar o elemento certo
  explicitamente, nunca por posicao/primeiro-match.
- Ao corrigir um bug de UX (ex. autosave disparando cedo demais), preferir
  um fix ESCOPADO (so' onde o problema existe) em vez de mudar o
  comportamento geral para todo mundo.
- Antes de qualquer operacao destrutiva do git (reset, checkout, clean),
  rodar `git status` primeiro.
- Sempre criar commits novos (nunca `--amend`) e nunca pular hooks.
- Nao adicionar funcionalidades/abstracao alem do pedido; nao adicionar
  comentarios obvios no codigo; nao criar documentos de planejamento a
  menos que eu peca.

## Trabalho recente concluido (para referencia, ja no ar em producao)

Unificacao de "Produtos Fazenda" com a secao Folha; raio de "so' clima" so'
no Mapa Interpolado (removido do Mapa normal); arrastar pino ao editar
ponto no Mapa Interpolado; layout mobile do Mapa Interpolado corrigido;
checkbox "Manter conectado" no login; notas explicativas gerais movidas
para rodape das paginas (mantendo notas especificas de acao/tabela no
lugar); ordenacao de datas no Excel corrigida (mais novo primeiro, datas
reais no Excel, nao texto); relatorio diario do Excel com coluna "Data"
primeiro e formato dd/mm/aaaa; horario de envio WhatsApp com granularidade
de hora+minuto por fazenda; sugestao automatica de horario para fazendas
novas/existentes; processamento de "so' clima" sempre por ultimo no
scheduler; relatorio de envios do WhatsApp limitado aos ultimos 7 dias
(Excel completo continua com historico inteiro); campo "Nome de
exibicao" disponivel tambem para fazendas virtuais na aba Fazendas; fix do
autosave disparando no meio da digitacao (commit `7589151`).

## Ha um plano salvo, ainda nao implementado

Existe um plano detalhado (modo Plan) cobrindo varias ideias futuras ainda
NAO implementadas, entre elas:
1. Catalogo de produtos/variedades com autocompletar (aprende sozinho o
   que ja foi digitado, preenche produto<->ingrediente ativo
   automaticamente) nas abas Produtos/Aplicacoes/Plantio.
2. Correlacionar Plantio/Aplicacoes/Produtos na aba "Relatorio Diario" do
   Excel (historico de atividades de safra ao lado do dado de estacao).
3. Trocar a tecnica de desenho do mapa de chuva: sair do `Leaflet.heat`
   (que SOMA opacidade de pontos vizinhos, causando leitura errada
   dependendo do zoom) para um raster proprio interpolado (bilinear) via
   canvas + `L.imageOverlay`.
4. Mapa de chuva (heatmap `Leaflet.heat`) no Mapa Interpolado no lugar da
   camada de nuvens.
5. Fluxo "Esqueci a senha" via WhatsApp: senha temporaria de uso unico
   (colunas separadas `temp_password_hash`/`temp_password_expires_at`,
   nunca sobrescreve a senha real ate' ser de fato usada), forca trocar
   senha no proximo login.

Se eu disser para continuar algum desses, o plano completo com todos os
detalhes de implementacao ja foi escrito — vale a pena eu (re)pedir para o
Claude reconstituir os detalhes olhando o codigo atual antes de comecar,
ja que o plano pode ter ficado desatualizado.
