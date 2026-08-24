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

CMD gunicorn --workers 1 --threads 4 --timeout 60 --bind 0.0.0.0:$PORT app:app
