# The frontend: built to static files, then served by nginx.
#
# Two stages. The first has Node and builds the bundle; the second is nginx and
# the bundle, and nothing else. The finished image is about 25 MB rather than
# the ~400 MB it would be if Node came along for the ride.
#
#   docker build -f docker/web.Dockerfile -t edgentrag/web .

FROM node:20-alpine AS build
WORKDIR /app

# Dependencies first, so an edit to the source does not reinstall them.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY frontend/ ./
# VITE_API_BASE=/api is baked in here, at build time -- the built JavaScript
# runs in the visitor's browser, so a relative path is the only one that works
# behind any address. It also means the browser never makes a cross-origin
# request, so CORS never comes into it.
RUN npm run build


FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY docker/nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80
HEALTHCHECK --interval=15s --timeout=3s --start-period=5s --retries=3 \
    CMD wget -qO- http://127.0.0.1/ >/dev/null || exit 1
