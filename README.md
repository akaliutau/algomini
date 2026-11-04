# 🗜️ Algomini - Give your data its own compressor.

<p>
  <img alt="Status: proof of concept" src="https://img.shields.io/badge/status-proof%20of%20concept-orange">
  <img alt="Research: Muse Spark 1.3" src="https://img.shields.io/badge/research-Muse%20Spark%201.3-blue">
  <img alt="Runtime: Rust" src="https://img.shields.io/badge/runtime-Rust-orange">
  <img alt="Cloud: GitHub Actions" src="https://img.shields.io/badge/cloud-GitHub%20Actions-2088FF">
  <img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-green">
</p>


<p align="center">
  <img src="docs/assets/algomini_logo.png" width="480" alt="Algomini — a mechanical clamp and neural network on an engineering blueprint" />
</p>

### [Build a compression plugin](https://github.com/akaliutau/algomini/issues/new?template=codec.yml) · [See the results](#experiments) · [Cloud setup](docs/cloud.md)

Demo on YT: https://youtu.be/W4ovaZ9cb3E

Algomini is an automated discovery of novel compression algorithms, turining them into growing collection of plugins for the Rust-based universal compressor.

Designing a new algorithm now is as easy as this: just invent a new plugin name and provide an archive of training samples. That's all.
The dedicated Data Lab will investigate patterns, retrieve relevant research skills, ask **Meta Muse Spark 1.3** to write and refine Rust candidates, and benchmark against current sota baselines (`gzip`, `xz` and `zstd`).

Algomini bot will generate a PR with plugin code. After merge, the release workflow rebuilds the qualified implementation and publishes a console client that you can install with Cargo. 

After that it's just a  compression tools that you can run locally; no Python, model credentials or network connection is needed anymore.

---

## Why

General-purpose compressors are strong. However, a specific format and content of files that comprises enormous organization's datalikes can still offer useful structure: repeated JSON keys, exact numeric spellings, column layouts or predictable language contexts. Finding an optimum requires a reversible representation, working code and careful accounting.
This is a very open-ended problem, the optimal solution for each case is not known, and general-purpose compressors do not guarantee that optimum.

**Algomini automates that research loop and keeps all notes in GitHub.** It combines candidate coding, numerical research and statistics experiments with data samples, analysing  feedback.

In addition, Algomini becomes better on each iteration, formulating and collecting research heauristics and skill.

Embedded Data Lab is extensible as well - you can literally install any analytical tools that can run in CLI mode.

## Demo

1. Open **[Develop a compression plugin](https://github.com/akaliutau/algomini/issues/new?template=codec.yml)**.
2. Fill in two fields:

   | Field | Example |
   | --- | --- |
   | Plugin name | `text-demo` |
   | Dataset archive URL (this one was used to film a video) | [books2.zip](https://github.com/akaliutau/algomini-datasets/raw/refs/heads/main/gutenberg.org/books2.zip) |

3. Watch the linked Actions run prepare the dataset, investigate patterns and benchmark candidates.
4. Read the issue's result table, algorithm summary and source links.
5. Review and merge a PR, if benchmark shows the adventage and you are happy with algorithm (Muse describes it in source code docstring).
6. Copy the Cargo installation command posted to the issue, then use your plugin:

   ```bash
   algomini compress --plugin text-demo input.txt output.alc
   algomini decompress output.alc restored.txt
   cmp input.txt restored.txt
   ```

With two dataset files, the larger one becomes development data and the other becomes final data. Larger collections split as 70:30 by file count. The manifest is generated automatically; public HTTP/HTTPS downloads support redirects and query parameters. Note: currently only public URLs are supported, subject to fix this limitation in future variants.

**Research sees the first 2 MiB of development data. Qualification measures the complete files.** A maintainer's request starts automatically; other requests need maintainer authorization. A run that cannot beat the baseline produces a report. Reusing a reviewed winner produces a qualification PR; a newly generated winner includes its Rust source.

[Dataset preparation, permissions and retries →](docs/cloud.md#dataset-and-issue)

## Experiments

Earlier reviewed Rust codecs beat the strongest configured baseline on these supplied samples:

| Workload | Input bytes | Algomini archive | Best baseline archive | Smaller archive |
| --- | ---: | ---: | ---: | ---: |
| Shakespeare text | 5,422,721 | **1,434,371** | 1,656,213 · xz | **13.39%** |
| Dumas text | 2,704,417 | **644,368** | 764,445 · xz | **15.71%** |
| JSON development fixture | 1,026,294 | **8,269** | 8,801 · xz | **6.04%** |

These are complete archive sizes, including framing and shared models, with exact reconstruction. The text files use three and two independent chunks respectively. Executable size is reported separately.

The measurements use existing reviewed codecs on known local regression samples. The JSON fixtures contain repeated records.

[Measurement method, artifacts and reproduction →](docs/benchmarks.md)

## Stack

| Layer | Technology |
| --- | --- |
| Candidate coding | Meta Muse Spark 1.3 through the Muse CLI |
| Laboratory and orchestration | Python 3.12+, deterministic probes and native process measurements |
| Research memory | Markdown skills, SQLite FTS5/BM25, Model2Vec embeddings (pinned potion-base-2M with 64 dimensions) and a Git-versioned index |
| Compression runtime | Rust, bundled zstd and reviewed specialized codecs |
| Cloud execution | GitHub Actions, Docker and GHCR images pinned by digest |
| Review and delivery | GitHub App, pull requests, release artifacts and Cargo source installation |
| Native containment | Linux Landlock and seccomp |

## How it works

```mermaid
flowchart TB
    A["GitHub issue: plugin name + ZIP"]

    subgraph R["01 · Research loop"]
        direction LR
        C["Muse Spark<br/>Design + refine Rust"]

        subgraph LAB["Data Lab + evaluation"]
            direction TB
            B["Prepare data<br/>Probes + skills"]
            D["Compile + benchmark"]
        end

        C -->|"Investigate patterns"| B
        B -.->|"Statistical insights"| C
        C -->|"Rust candidate"| D
        D -.->|"Measured feedback"| C
    end

    subgraph Q["02 · Qualification"]
        direction LR
        F["Freeze source,<br/>model + client"]
        G["Full-file<br/>final benchmark"]
        H{"Correct +<br/>smaller?"}

        F --> G --> H
    end

    subgraph S["03 · Release"]
        direction LR
        I["Review PR"]
        J["Human merge"]
        K["Reproducible<br/>Rust release"]
        L["Cargo install<br/>Offline CLI"]

        I --> J --> K --> L
    end

    X["Issue report"]

    A --> R
    R -->|"Development passes"| Q
    Q -->|"Final passes"| S
    R -.->|"No gain"| X
    Q -.->|"Final fails"| X

    classDef input fill:#EDF4FC,stroke:#4776A8,color:#263243
    classDef research fill:#EDF4FC,stroke:#4776A8,color:#263243
    classDef qualify fill:#FFF4E3,stroke:#C79545,color:#263243
    classDef release fill:#E6F3F1,stroke:#278C83,color:#263243
    classDef decision fill:#FFFFFF,stroke:#64748B,color:#263243
    classDef fail fill:#F3F4F6,stroke:#9CA3AF,color:#374151

    class A input
    class B,C,D research
    class H decision
    class F,G qualify
    class I,J,K,L release
    class X fail

    style R fill:#F8FBFF,stroke:#A8BFDC,stroke-width:1px
    style LAB fill:#FFFFFF,stroke:#C7D7E9,stroke-width:1px
    style Q fill:#FFFCF5,stroke:#E4C992,stroke-width:1px
    style S fill:#F3FAF7,stroke:#9AC6B6,stroke-width:1px
```

The laboratory uses deterministic byte, entropy, repetition and structure probes to guide experiments. Muse receives relevant skills, parent source, measured archive costs and failure feedback. Candidate generation and repair cycles are bounded by configuration and spending reservations.

A candidate must pass validation and full-development checks before the final claim. Source, model and client are frozen first; final results cannot trigger further tuning. The release rebuild must reproduce the qualified executable and development archive.

[Research and qualification →](docs/cloud.md#research-and-results) · [Release and installation →](docs/cloud.md#merge-release-and-install)

## Research memory

Research skills live in Git as **`.agents/skills/NAME/SKILL.md`**, with metadata describing their applicable families and tools. A portable keyword/semantic index helps the laboratory find relevant procedures and earlier development evidence. Retrieval is bounded to four documents and 6,000 excerpt characters.

Campaigns retain development successes and failures for later searches. Reviewed skills and codecs become shared knowledge after merge. The coding model's weights stay fixed; improvement comes from better evidence, tools, retrieval and controlled experiments.

Extend the system by adding a focused skill, a registered laboratory probe or a Rust codec implementing the existing interface.

[Define a skill →](docs/laboratory.md#define-or-improve-a-skill) · [Add a tool →](docs/laboratory.md#add-a-laboratory-tool) · [Extend an algorithm →](docs/laboratory.md#add-or-improve-an-algorithm)

## Design principles

| Principle | Implementation |
| --- | --- |
| **Exact bytes** | Preserve whitespace, quoting, numeric spellings and binary content; verify reconstruction. |
| **Count complete archives** | Include payloads, frames, metadata and shared models. Track the executable separately. |
| **Measure strong baselines** | Compare against gzip, xz and zstd, including development-trained dictionary controls. |
| **Keep final data outside research** | Separate artifacts, a frozen candidate and one durable final claim. |
| **Bound cost and execution** | Spending reservations, finite coding cycles and contained workers with time/memory limits. |
| **Make results reviewable** | Commit source/model identities, reports and receipts; distinguish generated winners from reused codecs. |
| **Ship the qualified snapshot** | Human merge, pinned build image, reproducible rebuild and verified release checksums. |

[Protocol and operational details →](docs/cloud.md) · [Reproducibility records →](repro/README.md)

## Quick start

Build the client locally using Linux, Rust 1.92+ and a C compiler. The development catalog starts with no plugins. GitHub CLI users can configure Git authentication with `gh auth login` and `gh auth setup-git`.

```bash
git clone https://github.com/akaliutau/algomini.git
cd algomini
cargo install --path crates/algomini-cli --locked --force

algomini --version
algomini plugins
```

`plugins` reports that none are installed. Open a compression request to generate the first codec with the current tool and retained research skills. After a qualifying PR merges, its release adds the plugin name from the issue. Previous codecs remain regression fixtures and old releases remain installable. See [clean plugin development](docs/cloud.md#clean-plugin-development).

For a qualified cloud release, copy the command from its issue. It has this form, with `N` replaced by the issue number:

```bash
CARGO_NET_GIT_FETCH_WITH_CLI=true cargo install \
  --git https://github.com/akaliutau/algomini.git \
  --tag codec-issue-N algomini-cli --locked --force
```

Then compress with the plugin name you requested, for example `text-demo`:

```bash
algomini compress --plugin text-demo input.txt output.alc
algomini decompress output.alc restored.txt
cmp input.txt restored.txt
```

To run research in GitHub, configure the GitHub App credentials and Meta API key once. Installed compressors need none of those credentials.

[Cloud credentials and setup →](docs/cloud.md#credentials) · [Local laboratory and tests →](docs/development.md) · [Rust client →](crates/algomini-cli/README.md)


## PoC scope

| Boundary | Current behavior |
| --- | --- |
| Research input | First **2 MiB** of development data |
| Complete files | Up to **64 MiB** per file/partition |
| Larger-file encoding | Independent **2 MiB** chunks; whole files buffered in memory |
| Dataset ZIP | Up to **128 MiB** downloaded and expanded, with 2–64 data files |
| Selection | Archive size first; speed secondary, with mandatory resource limits |
| Release threshold | Positive archive saving; **10%** is the target |
| Plugin delivery | Compiled into the Rust client; updates require installing a newer build |
| Release platform | Linux x86_64 |

Compression efficiency depends on the workload and chunk boundaries. Final measurements remain independent of the coding loop; known fixtures remain regression evidence. Existing release tags retain their original clients and limits—large-file support starts with client 0.3.1.

## Explore further

| Question | Guide |
| --- | --- |
| How do issues become experiments, PRs and releases? | [GitHub workflow](docs/cloud.md) |
| What do the benchmark numbers include? | [Benchmarks and evidence](docs/benchmarks.md) |
| How are skills stored, searched and improved? | [Research, skills and extension](docs/laboratory.md) |
| How do I reproduce results and run checks? | [Local development](docs/development.md) |
| Which versions and hashes are recorded? | [Reproducibility](repro/README.md) |
| Where are the earlier experiments? | [Historical Git revisions](docs/history.md) |

## Vision

Make specialized compression an accessible engineering workflow: supply data, investigate a mechanism, measure it honestly and ship the implementation that earns its place.

The same research loop can keep improving as its collection of skills, tools and measured examples grows. Every proposed compressor has a concrete test: reconstruct the original bytes and justify the archive it produces.

## License

[MIT](LICENSE). Dataset provenance and embedded notices are described with the [supplied samples](datasets/sample/README.md).

---

*Algomini: make every byte count.*

Built exclusively for:

Meta Global AI Developer Hackathon





