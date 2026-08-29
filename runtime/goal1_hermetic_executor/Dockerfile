FROM golang:1.25.1-bookworm@sha256:299c4191248a84de928626d34afe734505ed7307ed67263b66ce2bc645b51523 AS wrapper-builder
WORKDIR /src
COPY go.mod main.go main_test.go ./
RUN go test ./... && CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -trimpath -ldflags="-s -w -buildid=" -o /out/executor .

FROM ghcr.io/ggml-org/llama.cpp:full@sha256:fdaff59437fa09d8de49d667d8ce6a21fe49ab669129906618d65da5bbdcdb6c AS model-fetch
USER root
RUN mkdir -p /opt/supernova && \
    curl --fail --location --retry 3 \
      "https://huggingface.co/Qwen/Qwen2.5-Coder-0.5B-Instruct-GGUF/resolve/bf1da6ca8f02b444067db175f02a14e74f49c5c0/qwen2.5-coder-0.5b-instruct-q4_k_m.gguf?download=true" \
      --output /opt/supernova/model.gguf && \
    echo "1d9614638d18024d0fbb36575a15f1302a3adf044df10345688ec4f6e1c4ff32  /opt/supernova/model.gguf" | sha256sum --check --strict

FROM ghcr.io/ggml-org/llama.cpp:full@sha256:fdaff59437fa09d8de49d667d8ce6a21fe49ab669129906618d65da5bbdcdb6c
USER root
COPY --from=wrapper-builder /out/executor /opt/supernova/executor
COPY --from=model-fetch /opt/supernova/model.gguf /opt/supernova/model.gguf
COPY BUILD_LOCK.json /opt/supernova/BUILD_LOCK.json
RUN executor_sha="$(sha256sum /opt/supernova/executor | cut -d' ' -f1)" && \
    llama_cli_sha="$(sha256sum /app/llama-cli | cut -d' ' -f1)" && \
    model_sha="$(sha256sum /opt/supernova/model.gguf | cut -d' ' -f1)" && \
    lock_sha="$(sha256sum /opt/supernova/BUILD_LOCK.json | cut -d' ' -f1)" && \
    printf '{"build_lock_sha256":"%s","executor_sha256":"%s","llama_cli_sha256":"%s","model_sha256":"%s","schema":"supernova.hermetic-executor-components.v1"}\n' \
      "$lock_sha" "$executor_sha" "$llama_cli_sha" "$model_sha" > /opt/supernova/components.json && \
    chmod 0555 /opt/supernova/executor && \
    chmod 0444 /opt/supernova/BUILD_LOCK.json /opt/supernova/model.gguf /opt/supernova/components.json
LABEL org.opencontainers.image.source="https://github.com/Kitahl/Supernova-V2" \
      org.opencontainers.image.description="Pinned CPU-only hermetic executor for Supernova Goal 1"
USER 65532:65532
WORKDIR /opt/supernova
ENTRYPOINT []
CMD ["/opt/supernova/executor", "--stdio"]
