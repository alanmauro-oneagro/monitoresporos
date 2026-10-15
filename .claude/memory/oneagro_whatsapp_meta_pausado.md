# Migracao WhatsApp -> API oficial da Meta (PAUSADA em 2026-10-15)

Codigo ja pronto (`webapp/whatsapp_cloud_api.py`, `WHATSAPP_PROVIDER=cloud_api`,
modelo `relatorio_fazenda` com PDF no cabecalho + 4 variaveis). Falta so' a parte
na Meta. Estado quando pausou:

- Conta WhatsApp "OneAgro" (a conta "WhatsApp Monitor Doencas" tem so' o numero
  de teste +1 555). O modelo e o Usuario do Sistema tem que ser da conta OneAgro.
- Numero cadastrado como +55 43 9114-7595 -> "Invalid Phone Number". Provavel
  causa: faltou o nono digito. Apagar e recadastrar como +55 43 99114-7595
  (o ID do numero muda; o antigo 1378263582038256 nao serve).
- Pendentes: verificar o numero (SMS/ligacao), forma de pagamento, criar e aprovar
  o modelo `relatorio_fazenda`, token permanente via Usuario do Sistema
  (permissoes whatsapp_business_messaging + whatsapp_business_management),
  verificacao da empresa (opcional, sobe limite de 250 conversas/dia).
- Depois: no Railway `WHATSAPP_PROVIDER=cloud_api`, `WHATSAPP_CLOUD_ACCESS_TOKEN`,
  `WHATSAPP_CLOUD_PHONE_NUMBER_ID`; testar envio; desligar o whatsapp-bridge.
