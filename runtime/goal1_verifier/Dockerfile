FROM docker.io/library/debian@sha256:88200866dfff7ea7f5cbcb6ec7c8a701889efe6fe859fe64d6990e4b07ea4171 AS build

ARG DEBIAN_FRONTEND=noninteractive
ARG LEAN_TOOLCHAIN=leanprover/lean4:v4.33.1
ARG LEAN4EXPORT_COMMIT=15f6055e299ad5b89345e533cc2192f4cc00f659
ARG COMPARATOR_COMMIT=71b52ec29e06d4b7d882726553b1ceb99a2499e0
ARG NANODA_COMMIT=68d5ca9db226849b41a6fff59d796ff19d0a8840

RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
       build-essential ca-certificates curl git libssl-dev pkg-config python3 \
    && rm -rf /var/lib/apt/lists/*
RUN useradd --create-home --uid 10001 verifier \
    && mkdir -p /opt/supernova/bin /opt/supernova/src /opt/supernova/template \
    && chown -R verifier:verifier /opt/supernova /home/verifier

USER verifier
ENV PATH="/home/verifier/.elan/bin:/home/verifier/.cargo/bin:/usr/local/bin:/usr/bin:/bin"
WORKDIR /opt/supernova/src

RUN curl --fail --silent --show-error https://elan.lean-lang.org/elan-init.sh \
      | sh -s -- -y --default-toolchain none \
    && elan toolchain install "${LEAN_TOOLCHAIN}" \
    && elan default "${LEAN_TOOLCHAIN}" \
    && lean --version
ARG RUST_TOOLCHAIN=1.98.0
RUN curl --proto '=https' --tlsv1.2 --fail --silent --show-error https://sh.rustup.rs \
      | sh -s -- -y --profile minimal --default-toolchain "${RUST_TOOLCHAIN}" \
    && rustc --version

RUN git clone https://github.com/leanprover/lean4export.git lean4export \
    && cd lean4export \
    && git checkout --detach "${LEAN4EXPORT_COMMIT}" \
    && printf '%s\n' "${LEAN_TOOLCHAIN}" > lean-toolchain \
    && lake build lean4export \
    && cp .lake/build/bin/lean4export /opt/supernova/bin/lean4export

RUN git clone https://github.com/leanprover/comparator.git comparator-upstream \
    && cd comparator-upstream \
    && git checkout --detach "${COMPARATOR_COMMIT}"
RUN mkdir checker \
    && cp comparator-upstream/Comparator.lean checker/Comparator.lean \
    && cp -R comparator-upstream/Comparator checker/Comparator \
    && cp comparator-upstream/LICENSE checker/COMPARATOR_LICENSE \
    && cp /opt/supernova/src/lean4export/LICENSE checker/LEAN4EXPORT_LICENSE \
    && printf '%s\n' "${LEAN_TOOLCHAIN}" > checker/lean-toolchain \
    && printf '%s\n' \
       'name = "supernova_goal1_checker"' \
       'version = "0.1.0"' \
       'defaultTargets = ["check_exports"]' \
       '' \
       '[[lean_lib]]' \
       'name = "Comparator"' \
       '' \
       '[[lean_exe]]' \
       'name = "check_exports"' \
       'root = "CheckExports"' \
       '' \
       '[[require]]' \
       'scope = "leanprover"' \
       'name = "lean4export"' \
       'rev = "15f6055e299ad5b89345e533cc2192f4cc00f659"' \
       > checker/lakefile.toml
COPY --chown=verifier:verifier runtime/goal1_verifier/CheckExports.lean /opt/supernova/src/checker/CheckExports.lean
RUN cd checker \
    && lake update \
    && lake build check_exports \
    && cp .lake/build/bin/check_exports /opt/supernova/bin/check_exports

RUN git clone https://github.com/robsimmons/nanoda_lib.git nanoda \
    && cd nanoda \
    && git checkout --detach "${NANODA_COMMIT}" \
    && cargo build --release \
    && cp target/release/nanoda_bin /opt/supernova/bin/nanoda_bin

COPY --chown=verifier:verifier runtime/lean/lakefile.toml runtime/lean/lake-manifest.json runtime/lean/lean-toolchain /opt/supernova/template/
COPY --chown=verifier:verifier runtime/goal1_verifier/ParseProduct.lean /opt/supernova/template/ParseProduct.lean
RUN cd /opt/supernova/template \
    && lake update \
    && lake exe cache get \
    && printf '%s\n' \
       '' \
       '[[lean_exe]]' \
       'name = "parse_product"' \
       'root = "ParseProduct"' \
       'supportInterpreter = true' \
       >> lakefile.toml \
    && lake build parse_product \
    && cp .lake/build/bin/parse_product /opt/supernova/bin/parse_product

COPY --chown=verifier:verifier runtime/goal1_verifier/freeze_runtime.py /opt/supernova/freeze_runtime.py
COPY --chown=verifier:verifier runtime/goal1_verifier/entrypoint.py runtime/goal1_verifier/pins.json runtime/goal1_verifier/sandbox_sentinel.txt /opt/supernova/
RUN cd /opt/supernova/template \
    && lake env python3 /opt/supernova/freeze_runtime.py \
       --template /opt/supernova/template \
       --build-sysroot /home/verifier/.elan/toolchains/leanprover--lean4---v4.33.1 \
       --runtime-sysroot /opt/lean \
       --output /opt/supernova/VERIFIER_RUNTIME_LOCK.json

RUN rm -rf /home/verifier/.cache /home/verifier/.cargo /home/verifier/.rustup \
       /opt/supernova/src

FROM docker.io/library/debian@sha256:88200866dfff7ea7f5cbcb6ec7c8a701889efe6fe859fe64d6990e4b07ea4171 AS runtime

ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install --yes --no-install-recommends python3 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 verifier \
    && mkdir -p /opt/supernova/bin /opt/supernova/template /opt/lean \
    && chown -R verifier:verifier /opt/supernova /opt/lean /home/verifier

COPY --from=build --chown=verifier:verifier \
  /home/verifier/.elan/toolchains/leanprover--lean4---v4.33.1/ /opt/lean/
COPY --from=build --chown=verifier:verifier /opt/supernova/bin/ /opt/supernova/bin/
COPY --from=build --chown=verifier:verifier /opt/supernova/entrypoint.py /opt/supernova/entrypoint.py
COPY --from=build --chown=verifier:verifier /opt/supernova/pins.json /opt/supernova/pins.json
COPY --from=build --chown=verifier:verifier /opt/supernova/sandbox_sentinel.txt /opt/supernova/sandbox_sentinel.txt
COPY --from=build --chown=verifier:verifier /opt/supernova/template/ /opt/supernova/template/
COPY --from=build --chown=verifier:verifier /opt/supernova/VERIFIER_RUNTIME_LOCK.json /opt/supernova/VERIFIER_RUNTIME_LOCK.json

# Runtime verification is deliberately Lake- and Git-free. Dependency
# materialization happens only in the networked build stage above.
RUN rm -f /opt/lean/bin/lake

USER verifier
ENV PATH="/opt/lean/bin:/usr/local/bin:/usr/bin:/bin"
WORKDIR /tmp
ENTRYPOINT ["python3", "/opt/supernova/entrypoint.py"]
