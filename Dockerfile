FROM node:24-alpine AS miniapp-builder

WORKDIR /build/miniapp
COPY miniapp/package.json miniapp/package-lock.json ./
RUN npm ci
COPY miniapp/ ./
RUN npm run build

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app/backend
COPY backend/requirements.txt ./requirements.txt
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY backend/ ./
COPY --from=miniapp-builder /build/miniapp/dist /app/miniapp/dist

EXPOSE 8000

CMD ["python", "-m", "src"]
