# Stage 1: Build ngx_http_vts_module as a dynamic module
FROM alpine:3.21 AS builder

ARG NGINX_VERSION=1.29.0

RUN apk add --no-cache \
    build-base \
    openssl-dev \
    pcre2-dev \
    zlib-dev \
    linux-headers \
    git

RUN wget -q "https://nginx.org/download/nginx-${NGINX_VERSION}.tar.gz" && \
    tar -xzf "nginx-${NGINX_VERSION}.tar.gz"

RUN git clone --depth=1 https://github.com/vozlt/nginx-module-vts.git

RUN cd "nginx-${NGINX_VERSION}" && \
    ./configure \
        --with-compat \
        --add-dynamic-module=../nginx-module-vts \
        --with-cc-opt='-Os -fstack-clash-protection -Wformat -Werror=format-security -fno-plt -g' \
        --with-ld-opt='-Wl,--as-needed,-O1,--sort-common' && \
    make modules

# Stage 2: Final nginx image with VTS module
FROM nginx:1.29.0-alpine

COPY --from=builder /nginx-1.29.0/objs/ngx_http_vhost_traffic_status_module.so /usr/lib/nginx/modules/

RUN sed -i '1s/^/load_module modules\/ngx_http_vhost_traffic_status_module.so;\n/' /etc/nginx/nginx.conf
