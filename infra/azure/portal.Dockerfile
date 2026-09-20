# syntax=docker/dockerfile:1
# Build from the repository root; the adjacent Dockerfile-specific ignore file
# includes Portal sources and Demo metadata, but excludes Demo runtimes/secrets.
FROM node:22-alpine AS build
WORKDIR /workspace
RUN npm install --global pnpm@11.19.0
COPY . .
RUN pnpm install --frozen-lockfile
RUN pnpm metadata && pnpm --filter @playground/portal build

FROM nginxinc/nginx-unprivileged:stable-alpine AS runtime
COPY infra/azure/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /workspace/apps/portal/dist/ /usr/share/nginx/html/
USER 101:101
EXPOSE 8080
CMD ["nginx", "-g", "daemon off;"]
