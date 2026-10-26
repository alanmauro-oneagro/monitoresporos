# App OneAgro Monitor (Android e iOS)

Casca nativa feita com [Capacitor](https://capacitorjs.com): o app abre a área do cliente do
site de produção (`/app`), então toda melhoria publicada no Railway já aparece no app, sem nova
versão na loja. Só é preciso publicar de novo quando mudar ícone, nome, permissões ou a própria casca.

- `capacitor.config.json`: endereço do site, id do app (`br.com.oneagro.monitor`) e o
  user-agent `OneAgroApp`. É por ele que o site esconde preços e links de pagamento dentro do
  app, porque as lojas não permitem vender assinatura por fora delas. A assinatura é feita no site
  (`/planos`), com Pix, boleto ou cartão pelo Asaas.
- **Offline**: o service worker do site (`webapp/static/sw.js`) salva no celular as telas do
  cliente (lavouras, detalhes e região) sempre que abrem com internet; sem sinal, o app mostra a
  última versão com o aviso "Sem internet". No iOS isso depende de `WKAppBoundDomains` no
  `Info.plist`: se o site mudar de domínio, atualize ali e em `capacitor.config.json`.
- `www/offline.html`: tela de "sem conexão" para o primeiro acesso sem internet (nada salvo ainda).
- `assets/`: ícone e splash de origem. Para regenerar:
  `npx @capacitor/assets generate --iconBackgroundColor '#ffffff' --splashBackgroundColor '#f4f6f4'`.

## Gerar e publicar

```bash
cd mobile
npm install
npx cap sync
```

**Android** (Android Studio, conta Google Play Console de US$ 25, pagamento único):
`npx cap open android` → Build → Generate Signed App Bundle (.aab) → enviar no Play Console.
A cada versão nova, aumente `versionCode`/`versionName` em `android/app/build.gradle`.

**iOS** (precisa de um Mac com Xcode e da conta Apple Developer, US$ 99 por ano):
`npx cap open ios` → em Signing & Capabilities, escolha o Team → Product → Archive →
Distribute App → App Store Connect.

## O que as lojas cobram na revisão

- Exclusão de conta dentro do app: existe em Conta → Excluir minha conta.
- Política de privacidade: é preciso informar uma URL no cadastro do app nas duas lojas.
- Conta de teste para o revisor: crie um cliente e libere o acesso pela aba
  Configurações → Planos e clientes → "Cortesia até".
- A Apple pode recusar app que "só abre um site" (diretriz 4.2). A área `/app` foi feita em
  formato de app (abas embaixo, mapa, localização), mas se houver recusa, o próximo passo é
  acrescentar recursos nativos, como notificações push de alerta de doença.
