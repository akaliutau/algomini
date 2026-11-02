# Appliances telemetry provenance

Source: **Candanedo, L. (2017). Appliances Energy Prediction [Dataset]. UCI Machine Learning Repository.** DOI: [10.24432/C5VC8G](https://doi.org/10.24432/C5VC8G).

The [UCI dataset page](https://archive.ics.uci.edu/dataset/374/appliances+energy+prediction) licenses the data under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). The measurements cover a single house and associated weather observations. This experiment studies later time periods from that source; it does not establish generalization to unseen houses or other telemetry systems.

`run.toml` pins the official archive URL and SHA-256. Preparation groups original CSV records by date, assigns chronological train/validation/test periods, and discards one intervening day group at each split boundary. Each bounded chunk repeats the original header and preserves original record bytes, including number spellings, quotes, and line endings. These chunks are derived extracts rather than an unmodified redistribution of the complete source file.

`manifest.json` records the source provenance, preparation hash, development-group membership, file checksums, and a commitment to the private sealed manifest. Test files and their detailed manifest live under ignored `data/sealed/`; exploratory loaders accept training and validation paths only. Candidate workers must receive only development mounts when containment is added in Stage 2. The ignored raw-source cache also contains test records and must never be mounted into those workers.

Recreate with `python -m algomini prepare`. Cached inputs and prepared development files are verified before reuse. The public manifest and report contain no test-data profiles or compression measurements.
