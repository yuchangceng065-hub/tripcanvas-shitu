FROM python:3.12-slim
WORKDIR /app
COPY backend/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY alembic.ini /app/alembic.ini
COPY backend /app/backend
EXPOSE 8000
CMD ["python","-m","backend.start"]
