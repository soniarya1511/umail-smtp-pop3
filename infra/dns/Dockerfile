FROM alpine:3.20
RUN apk add --no-cache dnsmasq
COPY dnsmasq.conf /etc/dnsmasq.conf
EXPOSE 53/udp 53/tcp
HEALTHCHECK --interval=5s --timeout=3s CMD nslookup mail.posthawk.com 127.0.0.1 >/dev/null || exit 1
ENTRYPOINT ["dnsmasq", "--keep-in-foreground", "--conf-file=/etc/dnsmasq.conf"]
