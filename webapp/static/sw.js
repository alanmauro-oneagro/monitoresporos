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
            Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
        )
    );
    self.clients.claim();
});

self.addEventListener("fetch", (event) => {
    const req = event.request;
    if (req.method !== "GET") return; // login de verdade (POST) precisa de rede, sem fallback possivel

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
