# Bounded Wikipedia integration fixture

`manifest.json` pins the first **1,000,000 bytes** of enwik8, the published March 2006 English Wikipedia XML benchmark. The prefix includes natural language, XML/wikitext structure, entity escapes and varied record lengths. It is an arbitrary byte prefix, not a complete XML document or a new unseen-data benchmark.

Source: [Matt Mahoney's benchmark description](https://mattmahoney.net/dc/textdata.html), with original text attributed to Wikipedia contributors. The descriptor links the source rights information. Raw and encoded corpus bytes are excluded from Git.

The upstream ZIP is 36,445,475 bytes. `prepare_fixture` downloads it only when necessary, verifies the pinned archive SHA-256, then streams the 100,000,000-byte member through its published MD5/SHA-1 checksums while retaining only the requested prefix. Reading/hashing the source does not compress it. Every compression invocation in the integration run receives exactly 1,000,000 bytes, below the 2 MiB cap.

Prefix SHA-256: `369b688978f649681136198fb96db14c1616756260c55fb4b65e9bc049552cad`.

```bash
python -m algomini text-e2e
```

The committed result is replayed without original data or new codec/embedding work. To execute a new installation/integration measurement, choose a new report directory with `--output reports/text-e2e-repeat-01`. Its records remain separate from the first run and from the final telemetry result.
