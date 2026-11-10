# Built from the exact upstream source commit recorded in versions.yml. Keep
# this build equivalent to the upstream Dockerfile while pinning both bases.
FROM --platform=$BUILDPLATFORM docker.io/library/golang:1.27-alpine@sha256:0fd028db9bdd4f01657bc8047c209b04d8ce19b853d776bd6f1e76a41729b936 AS builder

RUN apk add --no-cache git nodejs npm make

WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download

COPY . .
RUN cd frontend && npm ci && npm run build
RUN mkdir -p web/frontend && cp -r frontend/dist web/frontend/

ARG VERSION=dev
ARG COMMIT=unknown
ARG BUILD_DATE=unknown
ARG TARGETOS=linux
ARG TARGETARCH=amd64
RUN CGO_ENABLED=0 GOOS=${TARGETOS} GOARCH=${TARGETARCH} go build \
    -tags server \
    -ldflags "-X main.version=${VERSION} -X main.commit=${COMMIT} -X main.date=${BUILD_DATE} -X github.com/smart-mcp-proxy/mcpproxy-go/internal/httpapi.buildVersion=${VERSION} -X github.com/smart-mcp-proxy/mcpproxy-go/internal/updatecheck.buildChannel=docker -s -w" \
    -o /mcpproxy ./cmd/mcpproxy

FROM gcr.io/distroless/static-debian12:nonroot@sha256:52dcfbabb7457ea47c82f6e13af8c8a4a1d9f7b0145142b3ecab20f2b888411d
COPY --from=builder /mcpproxy /usr/local/bin/mcpproxy
EXPOSE 8080
ENTRYPOINT ["/usr/local/bin/mcpproxy", "serve", "--listen", "0.0.0.0:8080"]
