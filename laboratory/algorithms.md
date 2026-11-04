# Compression research brief

Use published high-ratio methods as starting points, then measure their suitability
for this workload. This is a shortlist of mechanisms, not a claim that one is the
current benchmark leader. The coding worker has no web tools; these reviewed
references accompany the prompt.

- Text, XML and arbitrary bytes: investigate bounded PPM predictors with escape
  estimation and exclusion; PAQ-style context mixing with byte, word, match and
  run contexts; or DMC. Pair a useful predictor with exact arithmetic/range coding.
  Compare BWT/MTF/RLE when the observed structure supports a reversible transform.
- JSON/JSONL: compare exact lexical channels, repeated-record grouping and key
  templates. Preserve duplicate keys, ordering, whitespace and numeric spellings.
  Count exceptions and all dictionaries. Never use normalized JSON as the inverse.
- CSV and telemetry: compare columns, deltas, delta-of-delta, XOR, bitplanes and
  run lengths. Gorilla's timestamp/float ideas require lexical residuals when
  applied to text: reconstruct the original spelling, not only numeric values.
- Entropy coding: compare range coding and rANS where symbol statistics justify
  them. Changing the coder alone cannot compensate for an uninformative model.
- Avoid a search consisting only of dictionary size or zstd level changes. Use
  initial trials for different model/transform families, then perform controlled
  refinements of the best measured mechanism. Keep a reversible fallback.

All designs must fit the existing source, model, memory and execution limits.
Use bounded tables rather than corpus-sized state, account for independent 2 MiB
chunks, and justify model/header costs on complete archives. Cite a family or
paper in ordinary source comments; claim improvements only from measurements.

Primary references:

- [Matt Mahoney, Data Compression Explained: PPM, DMC, context mixing and transforms](https://mattmahoney.net/dc/dce.html)
- [Jarek Duda, Asymmetric numeral systems](https://arxiv.org/abs/1311.2540)
- [Pelkonen et al., Gorilla: A Fast, Scalable, In-Memory Time Series Database](https://www.vldb.org/pvldb/vol8/p1816-teller.pdf)
- [Zstandard reference implementation](https://github.com/facebook/zstd)
