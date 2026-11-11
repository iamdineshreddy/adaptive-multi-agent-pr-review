# syntax=docker/dockerfile:1
FROM node:20-alpine AS build

WORKDIR /app

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

# Vite inlines VITE_* at build time. The dashboard calls the API through the
# nginx /api proxy, so the demo bearer token has to be present in the bundle.
ARG VITE_API_TOKEN=""
ENV VITE_API_TOKEN=${VITE_API_TOKEN}

COPY frontend/ ./
RUN npm run build

# --- runtime: static host + /api reverse proxy to the api service ----------
FROM nginx:alpine

COPY --from=build /app/dist /usr/share/nginx/html
COPY infra/docker/frontend/nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80