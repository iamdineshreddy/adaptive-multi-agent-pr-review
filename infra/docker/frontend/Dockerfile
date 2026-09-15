# syntax=docker/dockerfile:1
FROM node:20-alpine AS build

WORKDIR /app

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build

# --- runtime: static host + /api reverse proxy to the api service ----------
FROM nginx:alpine

COPY --from=build /app/dist /usr/share/nginx/html
COPY infra/docker/frontend/nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80