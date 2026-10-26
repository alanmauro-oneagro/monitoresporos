// Service worker minimo -- so' garante que a tela de login continue
// aparecendo mesmo sem internet (celular em area sem sinal, por
// exemplo), guardando em cache a pagina de login e o que ela precisa pra
// desenhar (logo, fotos de fundo, icones, Bootstrap). NAO faz login
// offline de verdade (isso e' impossivel sem rede) -- so' evita a tela de
// erro do proprio navegador ("sem conexao"), mostrando o app de qualquer
// jeito. Servido em /sw.js (rota dedicada em app.py, nao /static/sw.js)
// de proposito -- um service worker so controla o que esta dentro (ou
// abaixo) da pasta de onde ele foi servido, e a pagina de login vive na
// raiz do site.
const CACHE_NAME = "oneagro-login-shell-v1";
// Area do cliente do app (/app): cada pagina aberta com internet fica
// salva aqui e volta quando o celular estiver sem sinal, com
// data-offline="1" no <body> (o cliente_base.html mostra o aviso). Apagado
// ao sair (logout) -- celular compartilhado nao ve dado de outra conta.
const CACHE_APP = "oneagro-app-dados";
// CSS/JS de CDN (Bootstrap, Leaflet) e pedacos de mapa ja vistos. Resposta
// de outro dominio e' "opaca" e o navegador conta varios MB de cota por
// item -- por isso o limite baixo e o try/catch no put.
const CACHE_ESTATICOS = "oneagro-estaticos-v1";
const MAX_ESTATICOS = 150;
const HOSTS_ESTATICOS = ["cdn.jsdelivr.net", "unpkg.com", "server.arcgisonline.com"];
const CACHES_ATUAIS = [CACHE_NAME, CACHE_APP, CACHE_ESTATICOS];
const OFFLINE_URLS = [
    "/login",
    "/static/oneagro-logo.png",
    "/static/login-bg-mobile.jpg",
    "/static/login-bg-desktop.jpg",
    "/static/icon-32.png",
    "/static/icon-16.png",
    "/static/icon-180.png",
    "/static/favicon.ico",
    "https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css",
];

self.addEventListener("install", (event) => {
    event.waitUntil(
        caches.open(CACHE_NAME).then((cache) => cache.addAll(OFFLINE_URLS))
    );
    self.skipWaiting();
});

self.addEventListener("activate", (event) => {
    event.waitUntil(
        caches.keys().then((keys) =>
            Promise.all(keys.filter((k) => !CACHES_ATUAIS.includes(k)).map((k) => caches.delete(k)))
        )
    );
    self.clients.claim();
});

self.addEventListener("fetch", (event) => {
    const req = event.request;
    if (req.method !== "GET") return; // login de verdade (POST) precisa de rede, sem fallback possivel
    const url = new URL(req.url);

    if (url.origin === self.location.origin && url.pathname === "/logout") {
        event.respondWith(caches.delete(CACHE_APP).then(() => fetch(req)));
        return;
    }

    if (url.origin === self.location.origin && (url.pathname === "/app" || url.pathname.startsWith("/app/"))) {
        event.respondWith(paginaDoApp(req));
        return;
    }

    if (HOSTS_ESTATICOS.includes(url.hostname)) {
        event.respondWith(estatico(req));
        return;
    }

    if (req.mode === "navigate") {
        // Qualquer navegacao (usuario abrindo o site, recarregando,
        // clicando num link) -- tenta a rede primeiro, pra sempre pegar a
        // versao atual quando ha' conexao; so cai pra tela de login em
        // cache se a rede realmente falhar (offline).
        event.respondWith(
            fetch(req).catch(() => caches.match("/login"))
        );
        return;
    }

    // Recursos estaticos (CSS do CDN, imagens, icones) -- cache primeiro
    // (mais rapido e funciona offline), busca na rede so' se nao tiver
    // em cache ainda.
    event.respondWith(
        caches.match(req).then((cached) => cached || fetch(req))
    );
});

async function paginaDoApp(req) {
    const url = new URL(req.url);
    const chave = url.origin + url.pathname;
    try {
        const resp = await fetch(req);
        if (resp.ok && !resp.redirected && (resp.headers.get("Content-Type") || "").includes("text/html")) {
            try {
                const cache = await caches.open(CACHE_APP);
                await cache.put(chave, resp.clone());
            } catch (e) {
                // cota cheia -- a pagina continua aparecendo, so' nao fica salva
            }
        }
        return resp;
    } catch (erro) {
        const salvo = await caches.match(chave);
        if (salvo) {
            const html = (await salvo.text()).replace('data-offline="0"', 'data-offline="1"');
            return new Response(html, { headers: { "Content-Type": "text/html; charset=utf-8" } });
        }
        if (req.mode === "navigate") {
            return (await caches.match("/login")) || Response.error();
        }
        throw erro;
    }
}

async function estatico(req) {
    const salvo = await caches.match(req);
    if (salvo) return salvo;
    const resp = await fetch(req);
    if (resp.ok || resp.type === "opaque") {
        try {
            const cache = await caches.open(CACHE_ESTATICOS);
            await cache.put(req, resp.clone());
            const chaves = await cache.keys();
            if (chaves.length > MAX_ESTATICOS) {
                await Promise.all(chaves.slice(0, chaves.length - MAX_ESTATICOS).map((k) => cache.delete(k)));
            }
        } catch (e) {
            // cota cheia -- segue sem guardar
        }
    }
    return resp;
}
