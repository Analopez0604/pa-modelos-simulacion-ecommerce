# Imagen de la tienda. La API y el worker usan la misma.
# La arma docker-compose.yml con: docker compose up --build -d

FROM python:3.11-slim
WORKDIR /srv

# Primero las librerías, así Docker no las reinstala si solo cambia el código.
COPY sistema_real/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY sistema_real/app ./app
EXPOSE 8000

# Prende la API. Con más de 100 peticiones a la vez responde 503 (el tope del nodo).
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1 --limit-concurrency ${LIMITE_CONCURRENCIA:-100}"]
