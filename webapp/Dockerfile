# Build explicito via Docker -- evita o provider Python do Railpack
# (Railway), que esta com um bug interno no passo de instalar o Python
# via mise ("secret ... not found", falha em poucos ms, sem nada a ver
# com o nosso codigo).
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD gunicorn --workers 1 --threads 4 --timeout 60 --bind 0.0.0.0:$PORT app:app
