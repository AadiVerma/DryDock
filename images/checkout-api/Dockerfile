FROM python:3.12-alpine
ARG APP_VERSION=1.4.2
ENV PYTHONUNBUFFERED=1 APP_VERSION=${APP_VERSION}
# An unprivileged user for task definitions that should not run as root.
RUN adduser -D -H -u 10001 app
# Create /app first: a folder created implicitly by COPY --chmod inherits that mode
# (no execute bit), which locks non-root users out of it.
WORKDIR /app
COPY --chmod=644 server.py /app/server.py
COPY --chmod=755 healthcheck /healthcheck
EXPOSE 8080
CMD ["python", "/app/server.py"]
