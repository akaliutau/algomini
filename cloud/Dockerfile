FROM rust:1.92.0-slim-bookworm@sha256:f1f73538ebe623fd3673a35aff3df358ae1084c64c55646516e5b17b321b6c9b AS rust
FROM python:3.12.16-slim-bookworm@sha256:e697c81897302a4c4333bade000d98038fbb21eb064f8ee1fd42d02ebcd9d337
COPY --from=rust /usr/local/cargo /usr/local/cargo
COPY --from=rust /usr/local/rustup /usr/local/rustup
ENV PATH="/usr/local/cargo/bin:${PATH}" CARGO_HOME=/usr/local/cargo RUSTUP_HOME=/usr/local/rustup
RUN apt-get update && apt-get install --yes --no-install-recommends \
    build-essential ca-certificates curl git pkg-config gzip xz-utils zstd file xxd binutils util-linux time libseccomp2 libzstd1 \
    && rm -rf /var/lib/apt/lists/* \
    && ln -s /usr/local/rustup /root/.rustup && ln -s /usr/local/cargo /root/.cargo \
    && if [ ! -e /usr/bin/python3 ]; then ln -s /usr/local/bin/python3 /usr/bin/python3; fi
WORKDIR /opt/algomini
COPY . .
RUN python -m pip install --no-cache-dir '.[analytics,coder,test]' \
    && cargo fetch --locked --manifest-path crates/algomini-cli/Cargo.toml \
    && python -c "from pathlib import Path; from algomini.common import load_config; from algomini.environment import bootstrap_muse; root,cfg=load_config('run.toml'); bootstrap_muse(root,cfg)" \
    && dpkg-query -W > cloud/image-packages.lock \
    && python -m pip freeze > cloud/image-python.lock
CMD ["python", "-m", "algomini.cloud", "smoke", "--workspace", "/work"]
