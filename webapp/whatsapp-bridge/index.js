// Servico que usa o WhatsApp de verdade do administrador (via Baileys,
// protocolo multi-device do WhatsApp) pra mandar os relatorios do
// BioScout Web -- substitui o CallMeBot. O endpoint /send NAO tem
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
const MIN_DELAY_MS = 2000; // espaco minimo entre envios, pra nao parecer bot

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
        browser: ["BioScout Web", "Chrome", "120.0.0"],
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

app.post("/send", (req, res) => {
    const { phone, message } = req.body || {};
    if (!phone || !message) {
        return res.status(400).json({ ok: false, error: "phone e message sao obrigatorios" });
    }
    if (!connected || !sock) {
        return res.status(503).json({ ok: false, error: "WhatsApp nao conectado -- escaneie o QR code em /qr" });
    }
    // fila sequencial simples com um espacamento minimo entre mensagens
    sendQueue = sendQueue
        .then(() => new Promise((resolve) => setTimeout(resolve, MIN_DELAY_MS)))
        .then(() => sock.sendMessage(normalizePhone(phone), { text: message }))
        .then(() => res.json({ ok: true }))
        .catch((err) => res.status(500).json({ ok: false, error: String(err) }));
});

app.listen(PORT, HOST, () => {
    console.log("Bridge do WhatsApp rodando em http://" + HOST + ":" + PORT);
});
