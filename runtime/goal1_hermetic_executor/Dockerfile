FROM golang:1.25.1-bookworm@sha256:299c4191248a84de928626d34afe734505ed7307ed67263b66ce2bc645b51523 AS wrapper-builder
WORKDIR /src
COPY go.mod main.go main_test.go ./
RUN go test ./... && CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -trimpath -ldflags="-s -w -buildid=" -o /out/executor .

FROM ghcr.io/ggml-org/llama.cpp:server-b10666@sha256:5c8d0ec9d1d9c1f302e2c30071f8eb89456808306ececc381480fb9e90d7e8c1 AS model-fetch
USER root
RUN mkdir -p /opt/supernova && \
    curl --fail --location --retry 3 \
      "https://huggingface.co/mradermacher/Kimina-Prover-Preview-Distill-1.5B-GGUF/resolve/5c087d3181668115364bc9c2d76186d9bcf81640/Kimina-Prover-Preview-Distill-1.5B.Q4_K_M.gguf?download=true" \
      --output /opt/supernova/model.gguf && \
    echo "f2d0fdabe97814b7d3fe1fec4a6dd32418c081b6953882ec31d3352f0058b65d  /opt/supernova/model.gguf" | sha256sum --check --strict

FROM ghcr.io/ggml-org/llama.cpp:server-b10666@sha256:5c8d0ec9d1d9c1f302e2c30071f8eb89456808306ececc381480fb9e90d7e8c1
USER root
COPY --from=wrapper-builder /out/executor /opt/supernova/executor
COPY --from=model-fetch /opt/supernova/model.gguf /opt/supernova/model.gguf
COPY BUILD_LOCK.json /opt/supernova/BUILD_LOCK.json
RUN test -x /app/llama-server && \
    /app/llama-server --version >/dev/null && \
    executor_sha="$(sha256sum /opt/supernova/executor | cut -d' ' -f1)" && \
    llama_server_sha="$(sha256sum /app/llama-server | cut -d' ' -f1)" && \
    model_sha="$(sha256sum /opt/supernova/model.gguf | cut -d' ' -f1)" && \
    lock_sha="$(sha256sum /opt/supernova/BUILD_LOCK.json | cut -d' ' -f1)" && \
    printf '{"build_lock_sha256":"%s","executor_sha256":"%s","llama_server_sha256":"%s","model_sha256":"%s","schema":"supernova.hermetic-executor-components.v3"}\n' \
      "$lock_sha" "$executor_sha" "$llama_server_sha" "$model_sha" > /opt/supernova/components.json && \
    chmod 0555 /opt/supernova/executor && \
    chmod 0444 /opt/supernova/BUILD_LOCK.json /opt/supernova/model.gguf /opt/supernova/components.json
LABEL org.opencontainers.image.source="https://github.com/Kitahl/Supernova-V2" \
      org.opencontainers.image.description="Pinned CPU-only hermetic executor for Supernova Goal 1"
USER 65532:65532
WORKDIR /opt/supernova
ENTRYPOINT []
CMD ["/opt/supernova/executor", "--stdio"]
