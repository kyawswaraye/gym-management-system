FROM node:24.20.0-slim AS deps

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM node:24.20.0-slim AS build

WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# Public browser origin list only. Server secrets are runtime environment.
ARG NEXT_PUBLIC_OBJECT_ORIGINS=
ENV NEXT_PUBLIC_OBJECT_ORIGINS=$NEXT_PUBLIC_OBJECT_ORIGINS
RUN npm run build

FROM node:24.20.0-slim AS runtime

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

WORKDIR /app
RUN apt-get update \
    && apt-get upgrade -y \
    && rm -rf /var/lib/apt/lists/* \
        /usr/local/lib/node_modules/npm \
        /usr/local/bin/npm \
        /usr/local/bin/npx \
    && useradd --uid 10001 --home-dir /tmp --no-create-home --shell /usr/sbin/nologin web
COPY --from=build --chown=web:web /app/.next/standalone ./
COPY --from=build --chown=web:web /app/.next/static ./.next/static
COPY --from=build --chown=web:web /app/public ./public
ENV HOME=/tmp
USER web

EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["node", "-e", "fetch('http://127.0.0.1:3000/login').then(function (response) { process.exit(Number(response.status !== 200)); }).catch(function () { process.exit(1); })"]

CMD ["node", "server.js"]
