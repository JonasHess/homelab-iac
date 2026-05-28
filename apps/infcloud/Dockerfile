# InfCloud is unmaintained upstream — pin to a known Unrud commit and rebuild
# only when you intentionally want to roll forward. See apps/infcloud/README.md.
#
# Sources:
#   - InfCloud assets: github.com/Unrud/RadicaleInfCloud (radicale_infcloud/web/)
#   - Pinned commit:   53d3a95af5b58cfa3242cef645f8d40c731a7d95 (master, 2022-04-18)

FROM alpine:3.20 AS fetch
ARG INFCLOUD_REPO=https://github.com/Unrud/RadicaleInfCloud.git
ARG INFCLOUD_COMMIT=53d3a95af5b58cfa3242cef645f8d40c731a7d95
RUN apk add --no-cache git ca-certificates \
 && git clone "${INFCLOUD_REPO}" /src \
 && git -C /src checkout "${INFCLOUD_COMMIT}" \
 && rm -rf /src/.git

FROM nginx:1.27-alpine
COPY --from=fetch /src/radicale_infcloud/web/ /usr/share/nginx/html/
COPY nginx.conf /etc/nginx/conf.d/default.conf

# nginx:alpine ships a default index in /etc/nginx/conf.d/default.conf;
# our COPY overwrites it. The InfCloud assets are now served from /.
EXPOSE 8080
