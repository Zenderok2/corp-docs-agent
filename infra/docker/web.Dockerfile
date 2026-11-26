FROM node:24.16.0-bookworm-slim AS build
WORKDIR /opt/ui
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-fund --no-audit
COPY frontend/index.html frontend/tsconfig.json frontend/vite.config.ts ./
COPY frontend/src ./src
RUN npm run build

FROM caddy:2.11.2-alpine
COPY --from=build /opt/ui/dist /usr/share/caddy
COPY infra/docker/Caddyfile /etc/caddy/Caddyfile
