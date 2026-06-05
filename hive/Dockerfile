FROM curlimages/curl:8.11.1 AS jdbc

ARG POSTGRES_JDBC_VERSION=42.7.7

RUN curl -fsSL "https://jdbc.postgresql.org/download/postgresql-${POSTGRES_JDBC_VERSION}.jar" -o /tmp/postgresql.jar

FROM apache/hive:4.0.0

COPY --from=jdbc /tmp/postgresql.jar /opt/hive/lib/postgresql.jar
