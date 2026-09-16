# Build a partir da raiz do repositorio (Root Directory = vazio no
# Railway) -- precisa ver tanto webapp/ quanto data/, que ficam uma ao
# lado da outra (data_reader.py em webapp/ le os CSVs subindo um nivel:
# Path(__file__).parent.parent / "data"). Um Dockerfile dentro so de
# webapp/ nao conseguiria alcancar data/ (fora do contexto de build).
FROM python:3.12-slim

WORKDIR /app

COPY webapp/requirements.txt webapp/requirements.txt
RUN pip install --no-cache-dir -r webapp/requirements.txt

COPY . .

WORKDIR /app/webapp

# --timeout 300: o envio manual de WhatsApp (botao "Enviar por
# WhatsApp") roda dentro da propria requisicao HTTP, bloqueando ate
# todos os destinatarios daquela fazenda serem processados -- com o
# espacamento de 25-45s por mensagem entre eles (ver MIN_DELAY_MS em
# whatsapp-bridge/index.js), uma fazenda com 2+ destinatarios (dono +
# subordinados) pode passar de 60s. Um timeout curto aqui mata o worker
# NO MEIO do envio (quem ainda nao foi alcancado no loop simplesmente
# nao recebe nada, sem nenhuma falha registrada) -- 300s da' folga pra
# varios destinatarios/formatos na mesma fazenda sem cortar o envio.
CMD gunicorn --workers 1 --threads 4 --timeout 300 --bind 0.0.0.0:$PORT app:app
