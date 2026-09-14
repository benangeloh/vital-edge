# syntax=docker/dockerfile:1
FROM node:22-alpine AS builder

RUN corepack enable && corepack prepare pnpm@10.30.2 --activate

WORKDIR /build
COPY package.json pnpm-workspace.yaml pnpm-lock.yaml ./
COPY shared/contracts/ts shared/contracts/ts
COPY central/web central/web

RUN pnpm install --frozen-lockfile
RUN pnpm --filter @fleetview/central-web build

FROM nginx:1.27-alpine AS runtime

COPY --from=builder /build/central/web/dist /usr/share/nginx/html
COPY central/web/nginx/fleetview.conf /etc/nginx/conf.d/default.conf
COPY central/web/nginx/fleetview-proxy-headers.conf /etc/nginx/fleetview-proxy-headers.conf
RUN mkdir -p /var/www/acme-challenge

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=5 \
    CMD ["wget", "--quiet", "--tries=1", "--spider", "--no-check-certificate", "https://127.0.0.1/api/health"]

EXPOSE 80
