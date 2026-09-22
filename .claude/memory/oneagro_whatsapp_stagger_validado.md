---
name: oneagro-whatsapp-stagger-validado
description: "No OneAgro Monitor, mandar WhatsApp pra varios destinatarios da MESMA fazenda (ou pro mesmo numero em VARIAS fazendas) precisa ser espalhado por HORAS (nao so segundos) -- confirmado em teste real que isso resolve a retencao do WhatsApp."
metadata:
  type: feedback
---

Mandar relatorio de WhatsApp pra varios destinatarios da mesma fazenda
(dono + subordinados) em sequencia -- mesmo respeitando o espacamento
de 25-45s entre mensagens individuais do whatsapp-bridge (Baileys) --
ainda e' rajada suficiente pro WhatsApp reter a entrega ("Aguardando
mensagem" que nunca abre), principalmente em numeros de subordinados
(contatos com pouco historico de conversa com o numero remetente).

**Fix validado em producao (2026-09-16/17)**: escalonar cada
destinatario da MESMA fazenda por `RECIPIENT_STAGGER_HORAS = 1` (1h de
intervalo), nao so' segundos -- ver `app.py`:
`_horario_efetivo_destinatario` (envio agendado diario) e
`_enviar_whatsapp_manual_escalonado`/`_run_manual_whatsapp_fila`/tabela
`whatsapp_manual_fila` (envio manual, 1o destinatario sai na hora, o
resto entra numa fila despachada 1/hora). Deduplicacao ("ja enviado
hoje") tem que ser por TELEFONE (identidade estavel), nunca por
indice/posicao na lista -- indice desloca se a lista de destinatarios
da fazenda mudar no mesmo dia, causando reenvio duplicado ou envio
pulado.

**Por que:** o usuario testou em producao depois do deploy e confirmou
que os subordinados passaram a receber normalmente (antes so' o
primeiro da fila recebia, os demais ficavam presos em "Aguardando
mensagem"). Aumentar so' o espacamento ENTRE mensagens (que ja' existia,
15-25s -> 25-45s) nao resolveu sozinho -- o problema era especificamente
rajada de VARIOS destinatarios novos em sequencia rapida, nao o
espacamento entre mensagens individuais.

**Segunda causa encontrada (2026-09-17), mesmo sintoma:** um numero que
recebe relatorio de VARIAS FAZENDAS diferentes (nao so' varios
destinatarios da mesma fazenda) tambem toma rajada -- cada fazenda tem
seu proprio horario auto-sugerido, so' 3 minutos diferente da vizinha,
entao um numero ligado a 9 fazendas recebia 9 mensagens em 24 minutos.
Fix: `_horarios_efetivos_por_telefone` (app.py) agrupa os envios do dia
por TELEFONE (cruzando todas as fazendas dele) e garante
`RECIPIENT_STAGGER_HORAS` de intervalo entre 2 mensagens consecutivas
pro mesmo numero, nao importa de qual fazenda vem cada uma. So' empurra
pra frente (nunca antecipa), entao fazendas com horarios ja' bem
diferentes continuam do jeito que estao. Confirmado em teste real com
2 numeros que recebem de 9 fazendas cada.

**Como aplicar:** se um problema parecido aparecer de novo (mensagem
nao chegando, "Aguardando mensagem"), NAO tentar resolver so' aumentando
o espacamento em segundos do bridge (`MIN_DELAY_MS`/`JITTER_MS` em
whatsapp-bridge/index.js) -- ja' se mostrou insuficiente sozinho.
Verificar: (1) se `RECIPIENT_STAGGER_HORAS` ainda esta' em vigor (1h);
(2) se o numero recebe de MULTIPLAS fazendas -- conferir com
`models.get_all_sites_whatsapp_recipients()` quantas fazendas cada
telefone recebe, e se os horarios delas estao proximos; (3) se a causa
nao e' outra (ex.: numero de subordinado realmente novo/nunca conversou
com o remetente -- nesse caso vale sugerir o usuario "esquentar" o
contato manualmente antes).

Desde 2026-09-16, a agenda de envio (texto/PDF/NDVI) e quem recebe cada
fazenda foram consolidados numa unica aba "Envios" (antes espalhados em
Manejos/NDVI/Usuarios) -- ver [[oneagro-sole-site-admin]] pra regra de
acesso admin-only de outras telas relacionadas (Conexao WhatsApp).

**Terceira causa encontrada (2026-09-22), mesmo sintoma:** mesmo com as
duas causas acima corrigidas, alguns numeros que JA recebiam normalmente
(nao numeros novos) continuavam presos em "Aguardando mensagem" --
confirmado com print do usuario mostrando 2 mensagens so' 34min uma da
outra pro mesmo numero. Causa raiz: `_run_scheduled_whatsapp_sends()`
(envio agendado por tick) e `_enviar_whatsapp_manual_escalonado()` (botao
"Enviar por WhatsApp") sao DOIS mecanismos independentes -- cada um so'
respeitava `RECIPIENT_STAGGER_HORAS` DENTRO do proprio lote (fazendas do
tick atual, ou destinatarios do clique atual), sem saber o que o OUTRO
mecanismo tinha acabado de mandar pro mesmo telefone minutos antes (ex.:
fazenda A manda automatico as 09:27, fazenda B e' mandada manualmente as
10:01 -- cada sistema, isolado, se achava "correto"). Fix:
`models.get_horarios_ocupados_por_telefone_hoje()` (junta o mais recente
entre `whatsapp_envio_log.criado_em` e `whatsapp_manual_fila.disparar_em`
de hoje, por telefone) agora e' consultado pelos DOIS mecanismos antes de
decidir o horario de qualquer envio novo -- inclusive o PRIMEIRO
destinatario do envio manual, que antes sempre saia na hora sem checar
nada. De brinde, corrigido `_run_scheduled_whatsapp_sends()` usando
`datetime.now()` puro (UTC, container sem `TZ` no Dockerfile) em vez de
`_agora_cuiaba_dt()` pra decidir "ja' e' hora" -- inconsistente com o
resto do agendador e com o log usado no fix acima.

**Como aplicar (atualizado):** se "Aguardando mensagem" voltar a
acontecer com numero que JA recebia normalmente, primeiro checar se ha'
MAIS de uma fazenda enviando (agendado E/OU manual) pro mesmo numero em
janela curta -- `models.get_horarios_ocupados_por_telefone_hoje()` e' o
lugar certo pra' depurar isso (deveria sempre refletir o ultimo envio
real, de qualquer origem, antes de qualquer novo calculo).
