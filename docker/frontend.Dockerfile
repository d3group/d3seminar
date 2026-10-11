FROM nginx:1.28-alpine
COPY docker/nginx.conf /etc/nginx/nginx.conf
COPY site/ /usr/share/nginx/html/
RUN chmod -R a+rX /usr/share/nginx/html
USER nginx
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
    CMD wget -q -O /dev/null http://127.0.0.1:8080/ || exit 1
ENTRYPOINT ["nginx"]
CMD ["-g", "daemon off;"]
