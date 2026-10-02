// Servico que usa o WhatsApp de verdade do administrador (via Baileys,
// protocolo multi-device do WhatsApp) pra mandar os relatorios do
// OneAgro Monitor. O endpoint /send NAO tem autenticacao -- a seguranca
// depende de isolamento de rede (so o app Flask consegue chamar aqui).
//
// Primeira vez: "npm install", "npm start" e pareie pela tela
// Configuracoes > WhatsApp do app (QR code ou codigo de pareamento).
// A sessao fica salva em ./auth_info/ (no Railway, precisa de volume
// persistente montado nesse diretorio).
//
// AJUSTES DESTA VERSAO:
// 1. Ritmo de envio configuravel por variaveis de ambiente no Railway
//    (WHATSAPP_MIN_DELAY_MS / WHATSAPP_JITTER_MS).
// 2. /send espera a reconexao (ate WHATSAPP_WAIT_CONNECT_MS) em vez de
//    devolver erro na hora -- antes, mensagem enviada durante uma queda
//    de conexao ficava presa em "aguardando envio" pra sempre.
// 3. Envio com ate 3 tentativas (backoff 10s/20s).
// 4. Reconexao com pausa entre tentativas.
//
// IMPORTANTE sobre "Aguardando mensagem..." no celular do destinatario:
// isso e' retencao de entrega feita pelo PROPRIO WhatsApp quando desconfia
// de comportamento de bot num "aparelho conectado". O espacamento 25-45s
// reduz o risco, nao elimina. Mitiga ainda mais: destinatario com
// historico de conversa com o numero remetente e numero salvo nos
// contatos. Solucao definitiva: API oficial (WhatsApp Cloud API).

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
// So usa 0.0.0.0 quando explicitamente pedido (ambiente hospedado).
const HOST = process.env.HOST || "127.0.0.1";
const AUTH_DIR = process.env.WHATSAPP_AUTH_DIR || "./auth_info";

// Espacamento minimo entre envios (+ variacao aleatoria). Configuravel
// no Railway (WHATSAPP_MIN_DELAY_MS e WHATSAPP_JITTER_MS).
const MIN_DELAY_MS = parseInt(process.env.WHATSAPP_MIN_DELAY_MS || "25000", 10);
const JITTER_MS = parseInt(process.env.WHATSAPP_JITTER_MS || "20000", 10);
// Pausa antes de cada tentativa de reconexao.
const RECONNECT_DELAY_MS = parseInt(process.env.WHATSAPP_RECONNECT_DELAY_MS || "5000", 10);
// Quanto tempo o /send espera pela reconexao antes de devolver erro.
const WAIT_CONNECT_MS = parseInt(process.env.WHATSAPP_WAIT_CONNECT_MS || "60000", 10);
// Tentativas de envio por mensagem.
const SEND_RETRIES = parseInt(process.env.WHATSAPP_SEND_RETRIES || "3", 10);

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
            console.log("Novo QR code gerado -- abra /qr pra escanear (ou use /pair-code).");
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
                console.log("Tentando reconectar em " + RECONNECT_DELAY_MS + "ms...");
                setTimeout(() => {
                    startSock().catch((e) => console.error("Falha ao reconectar:", e.message));
                }, RECONNECT_DELAY_MS);
            }
        }
    });
}

startSock().catch((err) => {
    console.error("Falha ao iniciar o Baileys:", err);
});

// Espera a reconexao acontecer. Resolve true se conectou no prazo.
function esperarConexao(tempoMaxMs) {
    const inicio = Date.now();
    return new Promise((resolve) => {
        const t = setInterval(() => {
            if (connected || Date.now() - inicio > tempoMaxMs) {
                clearInterval(t);
                resolve(connected);
            }
        }, 500);
    });
}

// Envio com tentativas: erro momentaneo nao perde mais a mensagem.
async function enviarComRetry(jid, conteudo) {
    let ultimoErro = null;
    for (let tentativa = 1; tentativa <= SEND_RETRIES; tentativa++) {
        try {
            return await sock.sendMessage(jid, conteudo);
        } catch (err) {
            ultimoErro = err;
            console.log("Envio falhou (tentativa " + tentativa + "/" + SEND_RETRIES + "): " + (err && err.message));
            if (tentativa < SEND_RETRIES) {
                await new Promise((r) => setTimeout(r, 10000 * tentativa));
            }
        }
    }
    throw ultimoErro;
}

function normalizePhone(phone) {
    const digits = String(phone).replace(/\D/g, "");
    return digits + "@s.whatsapp.net";
}

const app = express();
// Default do express.json() e' 100kb -- pequeno demais pro PDF/imagem
// NDVI em base64.
app.use(express.json({ limit: "20mb" }));

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
    const { phone, message, documentBase64, imageBase64, fileName, caption } = req.body || {};
    if (!phone || (!message && !documentBase64 && !imageBase64)) {
        return res.status(400).json({ ok: false, error: "phone e (message, documentBase64 ou imageBase64) sao obrigatorios" });
    }
    (async () => {
        // Em vez de devolver erro na hora (o que deixava a mensagem presa
        // em "aguardando envio" durante quedas de conexao), espera a
        // reconexao por ate WAIT_CONNECT_MS.
        if (!connected) {
            console.log("/send sem conexao -- aguardando reconexao (ate " + WAIT_CONNECT_MS + "ms)...");
            const ok = await esperarConexao(WAIT_CONNECT_MS);
            if (!ok) {
                return res.status(503).json({ ok: false, error: "WhatsApp nao conectado apos espera -- escaneie o QR em /qr" });
            }
        }
        // Fila sequencial com espacamento minimo (+ variacao) entre
        // mensagens -- ritmo humano reduz a retencao de entrega.
        sendQueue = sendQueue
            .then(() => new Promise((resolve) => setTimeout(resolve, MIN_DELAY_MS + Math.random() * JITTER_MS)))
            .then(async () => {
                // A conexao pode ter caido enquanto a mensagem esperava na
                // fila -- espera de novo antes de desistir.
                if (!connected) {
                    const ok = await esperarConexao(WAIT_CONNECT_MS);
                    if (!ok) throw new Error("WhatsApp desconectado no momento do envio");
                }
                // O numero "oficial" nem sempre bate com o JID de verdade
                // que o WhatsApp usa -- confirma com onWhatsApp() primeiro.
                const digits = String(phone).replace(/\D/g, "");
                const [info] = await sock.onWhatsApp(digits).catch(() => []);
                const jid = (info && info.exists) ? info.jid : normalizePhone(digits);
                // "Digitando..." antes de mandar -- sinal de envio humano
                // (so' cosmetico: falha aqui nao impede o envio).
                try {
                    await sock.presenceSubscribe(jid);
                    await sock.sendPresenceUpdate("composing", jid);
                    await new Promise((resolve) => setTimeout(resolve, 1200 + Math.random() * 1200));
                    await sock.sendPresenceUpdate("paused", jid);
                } catch (err) { /* presenca e' so' cosmetica -- nunca bloqueia */ }
                if (documentBase64) {
                    return enviarComRetry(jid, {
                        document: Buffer.from(documentBase64, "base64"),
                        mimetype: "application/pdf",
                        fileName: fileName || "relatorio.pdf",
                        caption: caption || undefined,
                    });
                }
                if (imageBase64) {
                    return enviarComRetry(jid, {
                        image: Buffer.from(imageBase64, "base64"),
                        caption: caption || undefined,
                    });
                }
                return enviarComRetry(jid, { text: message });
            })
            .then(() => res.json({ ok: true }))
            .catch((err) => res.status(500).json({ ok: false, error: String(err) }));
    })().catch((err) => res.status(500).json({ ok: false, error: String(err) }));
});

app.post("/reset", async (req, res) => {
    // Desconecta o numero atual e limpa a sessao salva (botao "Trocar
    // numero" na tela Configuracoes > WhatsApp do app).
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
