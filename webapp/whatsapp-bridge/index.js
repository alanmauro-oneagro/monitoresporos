// Servico que usa o WhatsApp de verdade do administrador (via Baileys,
// protocolo multi-device do WhatsApp) pra mandar os relatorios do
// OneAgro Monitor -- substitui o CallMeBot. O endpoint /send NAO tem
// nenhuma autenticacao -- a seguranca depende inteiramente de isolamento
// de rede (so o app Flask consegue chamar aqui). Local, isso significa
// so escutar em 127.0.0.1; hospedado (Railway/Render), significa manter
// esse servico SEM dominio publico -- so acessivel pela rede privada
// entre servicos do mesmo projeto (por isso o bind muda pra 0.0.0.0
// quando HOST vem definido, ver .env.example).
//
// Primeira vez: rode "npm install" aqui dentro, depois "npm start" e
// pareie de um dos dois jeitos, pela tela Configuracoes > WhatsApp do
// app: (1) escaneando o QR code (Aparelhos conectados > Conectar um
// aparelho), ou (2) gerando um codigo de pareamento (Aparelhos
// conectados > Conectar com numero de telefone > digitar codigo) --
// mais confiavel quando a camera/QR da problema. A sessao fica salva em
// ./auth_info/, entao nao precisa parear de novo depois de reiniciar --
// so se voce desconectar o aparelho pelo proprio celular (hospedado,
// esse diretorio precisa estar num disco persistente, senao perde o
// pareamento a cada deploy).
const fs = require("fs");
const path = require("path");
const express = require("express");
const pino = require("pino");
const QRCode = require("qrcode");
const {
    default: makeWASocket,
    useMultiFileAuthState,
    fetchLatestBaileysVersion,
    DisconnectReason,
} = require("@whiskeysockets/baileys");

const PORT = process.env.PORT || 3001;
// So usa 0.0.0.0 quando explicitamente pedido (ambiente hospedado) --
// local continua isolado em 127.0.0.1 por padrao, sem precisar mudar nada.
const HOST = process.env.HOST || "127.0.0.1";
const AUTH_DIR = process.env.WHATSAPP_AUTH_DIR || "./auth_info";
// Espaco minimo entre envios (+ variacao aleatoria por cima) -- 2s fixo
// era rapido e regular demais pra ser um envio manual de verdade; o
// WhatsApp trata rajadas de mensagens vindas de um "aparelho conectado"
// (que e' o que o Baileys e', tecnicamente) nesse ritmo como
// comportamento de bot e passa a segurar a ENTREGA pro destinatario --
// a mensagem fica "enviada" pro remetente, mas o destinatario so' ve'
// "Aguardando mensagem. Essa acao pode levar alguns instantes." sem
// nunca abrir (o texto some quando o WhatsApp decide liberar, minutos
// ou horas depois -- ou nunca). Mandar pelo proprio celular nao sofre
// disso porque nao passa por esse mecanismo de "linked device". Isso e'
// o que estava acontecendo no envio automatico dos relatorios (varias
// fazendas seguidas, mensagens parecidas, ritmo constante de 2s) mas
// nao no envio manual pelo app do WhatsApp do administrador.
const MIN_DELAY_MS = 8000;
const JITTER_MS = 5000;

let sock = null;
let lastQrDataUrl = null;
let connected = false;
let sendQueue = Promise.resolve();
let pairingCodeState = null; // { code, phone } ou null

async function startSock() {
    const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR);
    const { version, isLatest } = await fetchLatestBaileysVersion();
    console.log("Usando versao do WhatsApp Web " + version.join(".") + (isLatest ? " (atual)" : " (desatualizada!)"));
    sock = makeWASocket({
        auth: state,
        version,
        browser: ["OneAgro Monitor", "Chrome", "120.0.0"],
        logger: pino({ level: "silent" }),
    });

    sock.ev.on("creds.update", saveCreds);

    sock.ev.on("connection.update", async (update) => {
        const { connection, lastDisconnect, qr } = update;
        if (qr) {
            lastQrDataUrl = await QRCode.toDataURL(qr);
            console.log("Novo QR code gerado -- abra http://localhost:" + PORT + "/qr pra escanear.");
        }
        if (connection === "open") {
            connected = true;
            lastQrDataUrl = null;
            pairingCodeState = null;
            console.log("WhatsApp conectado.");
        }
        if (connection === "close") {
            connected = false;
            const err = lastDisconnect && lastDisconnect.error;
            const statusCode = err && err.output ? err.output.statusCode : null;
            const loggedOut = statusCode === DisconnectReason.loggedOut;
            console.log("Conexao fechada -- statusCode=" + statusCode + " motivo=" + (err ? err.message : "desconhecido"));
            if (loggedOut) {
                console.log("Desconectado pelo celular -- apague a pasta auth_info e escaneie de novo.");
            } else {
                console.log("Tentando reconectar...");
                startSock();
            }
        }
    });
}

startSock().catch((err) => {
    console.error("Falha ao iniciar o Baileys:", err);
});

function normalizePhone(phone) {
    const digits = String(phone).replace(/\D/g, "");
    return digits + "@s.whatsapp.net";
}

const app = express();
app.use(express.json());

app.get("/status", (req, res) => {
    res.json({
        connected,
        qr: connected ? null : lastQrDataUrl,
        pairingCode: connected ? null : (pairingCodeState ? pairingCodeState.code : null),
    });
});

app.post("/pair-code", async (req, res) => {
    const { phone } = req.body || {};
    const digits = String(phone || "").replace(/\D/g, "");
    if (!digits) {
        return res.status(400).json({ ok: false, error: "phone e obrigatorio (com codigo do pais)" });
    }
    if (!sock) {
        return res.status(503).json({ ok: false, error: "servico ainda iniciando, tente de novo em alguns segundos" });
    }
    if (connected) {
        return res.status(400).json({ ok: false, error: "WhatsApp ja esta conectado" });
    }
    try {
        const code = await sock.requestPairingCode(digits);
        pairingCodeState = { code, phone: digits };
        console.log("Codigo de pareamento gerado pra " + digits + ": " + code);
        res.json({ ok: true, code });
    } catch (err) {
        console.log("Falha ao gerar codigo de pareamento:", err);
        res.status(500).json({ ok: false, error: String(err) });
    }
});

app.get("/qr", (req, res) => {
    if (connected) {
        res.send("<p>WhatsApp ja conectado -- nao precisa de QR code.</p>");
    } else if (lastQrDataUrl) {
        res.send('<img src="' + lastQrDataUrl + '" alt="QR code" />');
    } else {
        res.send("<p>QR code ainda nao gerado -- aguarde alguns segundos e recarregue.</p>");
    }
});

app.post("/check-number", async (req, res) => {
    // Debug/validacao: confirma se um numero tem WhatsApp de verdade antes
    // de mandar (Baileys aceita mandar pra qualquer JID sem erro, mesmo
    // que o numero nao exista ou o formato esteja errado -- a mensagem so'
    // "some", sem aviso nenhum).
    const { phone } = req.body || {};
    const digits = String(phone || "").replace(/\D/g, "");
    if (!digits) {
        return res.status(400).json({ ok: false, error: "phone e obrigatorio" });
    }
    if (!connected || !sock) {
        return res.status(503).json({ ok: false, error: "WhatsApp nao conectado" });
    }
    try {
        const resultado = await sock.onWhatsApp(digits);
        res.json({ ok: true, resultado });
    } catch (err) {
        res.status(500).json({ ok: false, error: String(err) });
    }
});

app.post("/send", (req, res) => {
    const { phone, message, documentBase64, fileName, caption } = req.body || {};
    if (!phone || (!message && !documentBase64)) {
        return res.status(400).json({ ok: false, error: "phone e (message ou documentBase64) sao obrigatorios" });
    }
    if (!connected || !sock) {
        return res.status(503).json({ ok: false, error: "WhatsApp nao conectado -- escaneie o QR code em /qr" });
    }
    // fila sequencial simples com um espacamento minimo (+ variacao) entre
    // mensagens -- ver comentario de MIN_DELAY_MS acima.
    sendQueue = sendQueue
        .then(() => new Promise((resolve) => setTimeout(resolve, MIN_DELAY_MS + Math.random() * JITTER_MS)))
        .then(async () => {
            // O numero "oficial" (com o 9o digito, padrao brasileiro atual)
            // nem sempre bate com o JID de verdade que o WhatsApp usa por
            // baixo dos panos -- alguns DDDs ainda respondem so' ao formato
            // de 8 digitos. sendMessage NAO da erro pra um JID que nao
            // existe, a mensagem so' "some" sem aviso -- por isso confirma
            // com onWhatsApp() primeiro e usa o JID que ele devolver.
            const digits = String(phone).replace(/\D/g, "");
            const [info] = await sock.onWhatsApp(digits).catch(() => []);
            const jid = (info && info.exists) ? info.jid : normalizePhone(digits);
            // "Digitando..." antes de mandar de verdade -- outro sinal de
            // envio humano (ver comentario de MIN_DELAY_MS). So' cosmetico:
            // se falhar (numero sem presenca disponivel, etc.) nao impede
            // o envio, so' pula direto pra mensagem.
            try {
                await sock.presenceSubscribe(jid);
                await sock.sendPresenceUpdate("composing", jid);
                await new Promise((resolve) => setTimeout(resolve, 1200 + Math.random() * 1200));
                await sock.sendPresenceUpdate("paused", jid);
            } catch (err) { /* presenca e' so' cosmetica -- nunca bloqueia o envio */ }
            if (documentBase64) {
                // Relatorio em PDF (mesmo conteudo do texto, mais o grafico
                // de concentracao) mandado como documento anexado -- ver
                // `_send_site_whatsapp` em app.py, que manda o texto e
                // depois o PDF pro mesmo numero.
                return sock.sendMessage(jid, {
                    document: Buffer.from(documentBase64, "base64"),
                    mimetype: "application/pdf",
                    fileName: fileName || "relatorio.pdf",
                    caption: caption || undefined,
                });
            }
            return sock.sendMessage(jid, { text: message });
        })
        .then(() => res.json({ ok: true }))
        .catch((err) => res.status(500).json({ ok: false, error: String(err) }));
});

app.post("/reset", async (req, res) => {
    // Desconecta o numero atual e limpa a sessao salva, pra poder conectar
    // um numero diferente (ex.: trocar o WhatsApp corporativo) sem precisar
    // mexer no servidor na mao -- usado pelo botao "Trocar numero" na tela
    // Configuracoes > WhatsApp do app.
    try {
        if (sock) {
            try { await sock.logout(); } catch (err) { console.log("Logout falhou (ignorando):", err && err.message); }
            try { sock.end(undefined); } catch (err) { /* ja pode estar fechado */ }
        }
        sock = null;
        connected = false;
        lastQrDataUrl = null;
        pairingCodeState = null;
        if (fs.existsSync(AUTH_DIR)) {
            fs.readdirSync(AUTH_DIR).forEach((nome) => {
                try { fs.unlinkSync(path.join(AUTH_DIR, nome)); } catch (err) { /* ignora */ }
            });
        }
        await startSock();
        res.json({ ok: true });
    } catch (err) {
        res.status(500).json({ ok: false, error: String(err) });
    }
});

app.listen(PORT, HOST, () => {
    console.log("Bridge do WhatsApp rodando em http://" + HOST + ":" + PORT);
});
