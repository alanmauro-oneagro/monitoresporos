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

# --timeout 360: o envio manual de WhatsApp (botao "Enviar por
# WhatsApp") manda o PRIMEIRO destinatario dentro da propria requisicao
# HTTP (os demais, quando a fazenda tem mais de um, sao enfileirados e
# despachados depois em segundo plano -- ver
# `_enviar_whatsapp_manual_escalonado` em app.py). Mesmo so' pra esse
# primeiro, se texto e PDF nao couberem combinados num unico envio
# (`LEGENDA_MAX_CHARS`), a requisicao faz as DUAS chamadas em sequencia
# -- ate' 135s (texto) + 180s (PDF) = 315s no pior caso (ver os timeouts
# em whatsapp.py, calibrados pro espacamento de 25-45s do bridge, ver
# MIN_DELAY_MS em whatsapp-bridge/index.js). Um timeout curto aqui mata
# o worker NO MEIO do envio (sem nenhuma falha registrada) -- 360s da'
# folga sobre esse pior caso.
CMD gunicorn --workers 1 --threads 4 --timeout 360 --bind 0.0.0.0:$PORT app:app
