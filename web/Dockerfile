# Multi-stage build for the Metal ERP SPA.
#
# The `build` stage is what deploy-metalerp.sh targets:
#   docker build --target build .   then   docker cp <cid>:/app/dist ...
# Caddy serves that dist/ at metal.fleekfinance.in. The final `runtime`
# stage is a convenience for a standalone `docker run` (static file
# server) and is not used by the fleek-stack deploy.

FROM node:20-alpine AS build
WORKDIR /app

COPY package.json package-lock.json* ./
RUN npm ci

COPY . .
RUN npm run build
# -> /app/dist

FROM nginx:alpine AS runtime
COPY --from=build /app/dist /usr/share/nginx/html
# SPA history-mode fallback
RUN printf 'server {\n\
  listen 80;\n\
  root /usr/share/nginx/html;\n\
  location / { try_files $uri /index.html; }\n\
}\n' > /etc/nginx/conf.d/default.conf
EXPOSE 80
