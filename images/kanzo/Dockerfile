ARG RUST_BUILDER
FROM ${RUST_BUILDER} AS build

RUN apt-get update \
  && apt-get install -y --no-install-recommends musl-tools ca-certificates \
  && rm -rf /var/lib/apt/lists/* \
  && rustup target add x86_64-unknown-linux-musl

WORKDIR /src
COPY . .
RUN cargo build --locked --release --target x86_64-unknown-linux-musl

FROM scratch
COPY --from=build /src/target/x86_64-unknown-linux-musl/release/kanzo /kanzo
COPY --from=build /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-certificates.crt
ENTRYPOINT ["/kanzo"]
