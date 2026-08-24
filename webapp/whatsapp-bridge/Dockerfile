# Build explicito via Docker -- mesmo motivo do Dockerfile do app Flask:
# evita depender do provider Node do Railpack, que teve um bug
# equivalente no provider Python.
FROM node:20-slim

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci --omit=dev

COPY . .

CMD ["node", "index.js"]
