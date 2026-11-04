// Candidate text codec: adaptive PPM context model with exact arithmetic
// coding, plus reversible line-MTF and raw fallbacks coded with zstd.
// Family: PPM / context modelling as described in Mahoney, Data Compression
// Explained (PPM, context mixing and transforms), paired with a Witten-Neal-
// Cleary style arithmetic coder and a standard LZ backend (zstd) fallback.
// Applicability: probes show LF-terminated text (74246 lines in 2 MiB, max
// line 81 B, 101 distinct bytes, no CRLF/NUL), 38% repeated whole lines and
// order-1/2 byte entropy far below order-0. A bounded order-4 PPM captures
// word and phrase contexts that fixed-window LZ matches price less cheaply,
// while scattered repeated lines are also priced cheaply once their prefix
// context has been seen. Binary or incompressible blocks use raw zstd.
// Algorithm (encode): try three bodies and emit the smallest behind a mode
// byte (0x00 raw zstd, 0x01 PPM, 0x02 line-MTF + zstd). The PPM path encodes
// each byte in the highest available context (order 4 down to 0) using
// PPM-C style escape counts (escape frequency = number of distinct symbols
// in the context) with exclusion of symbols already escaped at higher
// orders, ending in a uniform order -1 model over non-excluded bytes. All
// contexts are rebuilt adaptively per block; an optional trained order-0/1
// prior (see train) merely seeds the lowest orders. The MTF path splits on
// LF only (every other byte passes through verbatim), replaces a record
// found in a 128-entry move-to-front cache with one tag byte, and emits
// other records as tag 0 + LEB128 length + raw bytes.
// Inverse (decode): branch on the mode byte. Raw: zstd-decompress and check
// the length. MTF: zstd-decompress, then replay the identical cache rule to
// rebuild records byte-exactly. PPM: run the arithmetic decoder, mirroring
// the encoder's context attempts, escapes, exclusions and updates exactly;
// a stored CRC-32 of the plaintext is verified, so malformed streams error
// instead of silently returning wrong bytes.
// Counted costs: total archive = model + 1 mode byte + chosen body. The
// model (0 bytes when untrained, at most 65536 when trained) is counted in
// full; PPM tables are implicit in the payload (adaptive, rebuilt on both
// sides), MTF costs 1 B per hit and about 2 B overhead per literal, and the
// PPM body carries a 4 B CRC. The adaptive pick is never worse than raw
// zstd by more than the mode byte itself.
use crate::toolkit::{crc32, zstd_compress, zstd_decompress};
use crate::{Codec, CodecError};

pub struct Candidate;

const ZSTD_LEVEL: i32 = 19;
const MODE_RAW: u8 = 0x00;
const MODE_PPM: u8 = 0x01;
const MODE_MTF: u8 = 0x02;
const CACHE_ENTRIES: usize = 128;
const CACHE_MAX_LINE: usize = 512;
const MAX_TOTAL: u32 = 4096;
const MAX_ORDER: usize = 4;

// ---------------------------------------------------------------- varint

fn write_varint(mut v: usize, out: &mut Vec<u8>) {
    loop {
        let b = (v & 0x7f) as u8;
        v >>= 7;
        if v == 0 {
            out.push(b);
            break;
        }
        out.push(b | 0x80);
    }
}

fn read_varint(buf: &[u8], pos: &mut usize) -> Result<usize, CodecError> {
    let mut shift: u32 = 0;
    let mut value: usize = 0;
    loop {
        if *pos >= buf.len() {
            return Err("mtf stream truncated in varint".into());
        }
        let b = buf[*pos];
        *pos += 1;
        if shift >= 28 && ((b & 0x7f) as usize) > (usize::MAX >> shift) {
            return Err("mtf varint overflow".into());
        }
        value |= ((b & 0x7f) as usize) << shift;
        if b & 0x80 == 0 {
            break;
        }
        shift += 7;
        if shift > 35 {
            return Err("mtf varint too long".into());
        }
    }
    Ok(value)
}

// ---------------------------------------------------------------- MTF path

fn build_mtf_stream(input: &[u8]) -> Vec<u8> {
    let mut trans: Vec<u8> = Vec::with_capacity(input.len() + 1024);
    let mut n: u32 = 0;
    if !input.is_empty() {
        let mut ends_with_lf = false;
        for &b in input {
            if b == b'\n' {
                n += 1;
                ends_with_lf = true;
            } else {
                ends_with_lf = false;
            }
        }
        if !ends_with_lf {
            n += 1;
        }
    }
    trans.extend_from_slice(&n.to_le_bytes());
    if n == 0 {
        return trans;
    }
    let mut cache: Vec<Vec<u8>> = Vec::new();
    let mut rec_start: usize = 0;
    let mut idx: usize = 0;
    while idx <= input.len() {
        let at_end = idx == input.len();
        if at_end && rec_start >= input.len() {
            break;
        }
        if at_end || input[idx] == b'\n' {
            let rec_end = if at_end { idx } else { idx + 1 };
            let rec: &[u8] = &input[rec_start..rec_end];
            let mut hit: Option<usize> = None;
            for (k, c) in cache.iter().enumerate() {
                if c.as_slice() == rec {
                    hit = Some(k);
                    break;
                }
            }
            if let Some(h) = hit {
                trans.push((h + 1) as u8);
                let entry = cache.remove(h);
                cache.insert(0, entry);
            } else {
                trans.push(0x00);
                write_varint(rec.len(), &mut trans);
                trans.extend_from_slice(rec);
                if rec.len() <= CACHE_MAX_LINE {
                    cache.insert(0, rec.to_vec());
                    if cache.len() > CACHE_ENTRIES {
                        cache.pop();
                    }
                }
            }
            rec_start = rec_end;
            if at_end {
                break;
            }
        }
        idx += 1;
    }
    trans
}

fn invert_mtf_stream(trans: &[u8], original_len: usize) -> Result<Vec<u8>, CodecError> {
    if trans.len() < 4 {
        return Err("mtf stream too short".into());
    }
    let n = u32::from_le_bytes([trans[0], trans[1], trans[2], trans[3]]) as usize;
    if n > 3_000_000 {
        return Err("mtf record count implausible".into());
    }
    if n == 0 {
        if original_len != 0 {
            return Err("mtf length mismatch".into());
        }
        return Ok(Vec::new());
    }
    let mut cache: Vec<Vec<u8>> = Vec::new();
    let mut out: Vec<u8> = Vec::with_capacity(original_len.min(1 << 20));
    let mut pos: usize = 4;
    for _ in 0..n {
        if pos >= trans.len() {
            return Err("mtf stream truncated at tag".into());
        }
        let tag = trans[pos];
        pos += 1;
        if tag == 0x00 {
            let len = read_varint(trans, &mut pos)?;
            if len > 2 * 1024 * 1024 + 16 {
                return Err("mtf literal too long".into());
            }
            if pos + len > trans.len() {
                return Err("mtf stream truncated at literal".into());
            }
            let rec = trans[pos..pos + len].to_vec();
            pos += len;
            if out.len() + rec.len() > original_len {
                return Err("mtf output exceeds original length".into());
            }
            out.extend_from_slice(&rec);
            if rec.len() <= CACHE_MAX_LINE {
                cache.insert(0, rec);
                if cache.len() > CACHE_ENTRIES {
                    cache.pop();
                }
            }
        } else if (1..=CACHE_ENTRIES as u8).contains(&tag) {
            let h = (tag - 1) as usize;
            if h >= cache.len() {
                return Err("mtf index out of range".into());
            }
            let entry = cache.remove(h);
            if out.len() + entry.len() > original_len {
                return Err("mtf output exceeds original length".into());
            }
            out.extend_from_slice(&entry);
            cache.insert(0, entry);
        } else {
            return Err("mtf unknown tag".into());
        }
    }
    if out.len() != original_len {
        return Err("mtf length mismatch".into());
    }
    Ok(out)
}

// ---------------------------------------------------------------- contexts

#[derive(Clone)]
struct Ctx {
    syms: Vec<u8>,
    counts: Vec<u16>,
    total: u32,
}

impl Ctx {
    fn new() -> Self {
        Ctx { syms: Vec::new(), counts: Vec::new(), total: 0 }
    }
    fn add(&mut self, sym: u8) {
        match self.syms.iter().position(|&s| s == sym) {
            Some(i) => self.counts[i] = self.counts[i].saturating_add(1),
            None => {
                self.syms.push(sym);
                self.counts.push(1);
            }
        }
        self.total += 1;
        if self.total > MAX_TOTAL {
            let mut t: u32 = 0;
            for c in self.counts.iter_mut() {
                *c = ((*c as u32 + 1) >> 1) as u16;
                t += *c as u32;
            }
            self.total = t;
        }
    }
    // Sum of counts and number of distinct symbols not excluded.
    fn filtered_stats(&self, excluded: &[bool; 256]) -> (u32, u32) {
        let mut total: u32 = 0;
        let mut distinct: u32 = 0;
        for (i, &s) in self.syms.iter().enumerate() {
            if !excluded[s as usize] {
                total += self.counts[i] as u32;
                distinct += 1;
            }
        }
        (total, distinct)
    }
}

struct Store {
    keys: Vec<u32>,
    vals: Vec<u32>,
    ctxs: Vec<Ctx>,
    mask: usize,
    shift: u32,
}

impl Store {
    fn new(bits: u32) -> Self {
        let size = 1usize << bits;
        Store {
            keys: vec![0; size],
            vals: vec![u32::MAX; size],
            ctxs: Vec::new(),
            mask: size - 1,
            shift: 32 - bits,
        }
    }
    fn slot(&self, key: u32) -> usize {
        (key.wrapping_mul(2_654_435_761) >> self.shift) as usize
    }
    fn lookup(&self, key: u32) -> Option<usize> {
        let mut i = self.slot(key);
        loop {
            if self.vals[i] == u32::MAX {
                return None;
            }
            if self.keys[i] == key {
                return Some(self.vals[i] as usize);
            }
            i = (i + 1) & self.mask;
        }
    }
    fn get_or_create(&mut self, key: u32) -> Option<usize> {
        let mut i = self.slot(key);
        loop {
            if self.vals[i] == u32::MAX {
                if self.ctxs.len() * 4 >= (self.mask + 1) * 3 {
                    return None;
                }
                let idx = self.ctxs.len();
                self.keys[i] = key;
                self.vals[i] = idx as u32;
                self.ctxs.push(Ctx::new());
                return Some(idx);
            }
            if self.keys[i] == key {
                return Some(self.vals[i] as usize);
            }
            i = (i + 1) & self.mask;
        }
    }
}

fn context_key(hist: &[u8], order: usize) -> u32 {
    let start = hist.len() - order;
    let mut key: u32 = 0;
    for &b in &hist[start..] {
        key = (key << 8) | b as u32;
    }
    key
}

// ---------------------------------------------------------------- model

struct Ppm {
    o0: Ctx,
    o1: Vec<Ctx>,
    o2: Store,
    o3: Store,
    o4: Store,
}

fn read_u16le(buf: &[u8], pos: &mut usize) -> Option<usize> {
    if *pos + 2 > buf.len() {
        return None;
    }
    let v = u16::from_le_bytes([buf[*pos], buf[*pos + 1]]) as usize;
    *pos += 2;
    Some(v)
}

fn parse_ctx(buf: &[u8], pos: &mut usize) -> Option<Ctx> {
    let nd = read_u16le(buf, pos)?;
    if nd > 256 {
        return None;
    }
    let mut ctx = Ctx::new();
    for _ in 0..nd {
        if *pos + 2 > buf.len() {
            return None;
        }
        let sym = buf[*pos];
        let cnt = buf[*pos + 1];
        *pos += 2;
        if cnt == 0 || ctx.syms.contains(&sym) {
            return None;
        }
        ctx.syms.push(sym);
        ctx.counts.push(cnt as u16);
        ctx.total += cnt as u32;
    }
    Some(ctx)
}

fn parse_model(model: &[u8]) -> Option<(Ctx, Vec<Ctx>)> {
    if model.is_empty() || model[0] != 1 {
        return None;
    }
    let mut pos: usize = 1;
    let o0 = parse_ctx(model, &mut pos)?;
    let mut o1: Vec<Ctx> = Vec::with_capacity(256);
    for _ in 0..256 {
        o1.push(parse_ctx(model, &mut pos)?);
    }
    if pos != model.len() {
        return None;
    }
    Some((o0, o1))
}

impl Ppm {
    fn new(model: &[u8]) -> Self {
        let mut ppm = Ppm {
            o0: Ctx::new(),
            o1: (0..256).map(|_| Ctx::new()).collect(),
            o2: Store::new(16),
            o3: Store::new(17),
            o4: Store::new(18),
        };
        if let Some((o0, o1)) = parse_model(model) {
            ppm.o0 = o0;
            ppm.o1 = o1;
        }
        ppm
    }
    fn ctx_ref(&self, order: usize, hist: &[u8]) -> Option<&Ctx> {
        match order {
            1 => self.o1.get((hist[hist.len() - 1]) as usize),
            2 => self.o2.lookup(context_key(hist, 2)).map(|i| &self.o2.ctxs[i]),
            3 => self.o3.lookup(context_key(hist, 3)).map(|i| &self.o3.ctxs[i]),
            4 => self.o4.lookup(context_key(hist, 4)).map(|i| &self.o4.ctxs[i]),
            _ => None,
        }
    }
    fn update(&mut self, hist: &[u8], sym: u8) {
        self.o0.add(sym);
        let max_k = MAX_ORDER.min(hist.len());
        for k in 1..=max_k {
            match k {
                1 => {
                    let key = hist[hist.len() - 1] as usize;
                    self.o1[key].add(sym);
                }
                2 => {
                    let key = context_key(hist, 2);
                    if let Some(i) = self.o2.get_or_create(key) {
                        self.o2.ctxs[i].add(sym);
                    }
                }
                3 => {
                    let key = context_key(hist, 3);
                    if let Some(i) = self.o3.get_or_create(key) {
                        self.o3.ctxs[i].add(sym);
                    }
                }
                _ => {
                    let key = context_key(hist, 4);
                    if let Some(i) = self.o4.get_or_create(key) {
                        self.o4.ctxs[i].add(sym);
                    }
                }
            }
        }
    }
}

// ---------------------------------------------------------------- arithmetic

struct ArithEnc {
    low: u32,
    high: u32,
    pending: u64,
    out: Vec<u8>,
    cur: u8,
    nbits: u32,
}

impl ArithEnc {
    fn new() -> Self {
        ArithEnc { low: 0, high: 0xFFFF_FFFF, pending: 0, out: Vec::new(), cur: 0, nbits: 0 }
    }
    fn emit_bit(&mut self, bit: u32) {
        self.cur = (self.cur << 1) | (bit as u8);
        self.nbits += 1;
        if self.nbits == 8 {
            self.out.push(self.cur);
            self.cur = 0;
            self.nbits = 0;
        }
    }
    fn emit_with_pending(&mut self, bit: u32) {
        self.emit_bit(bit);
        let inv = bit ^ 1;
        while self.pending > 0 {
            self.emit_bit(inv);
            self.pending -= 1;
        }
    }
    fn encode(&mut self, cum: u32, freq: u32, total: u32) {
        debug_assert!(freq > 0 && cum + freq <= total);
        let range = self.high as u64 - self.low as u64 + 1;
        self.high = self.low + ((range * (cum + freq) as u64 / total as u64) - 1) as u32;
        self.low = self.low + (range * cum as u64 / total as u64) as u32;
        loop {
            if self.high < 0x8000_0000 {
                self.emit_with_pending(0);
            } else if self.low >= 0x8000_0000 {
                self.emit_with_pending(1);
                self.low -= 0x8000_0000;
                self.high -= 0x8000_0000;
            } else if self.low >= 0x4000_0000 && self.high < 0xC000_0000 {
                self.pending += 1;
                self.low -= 0x4000_0000;
                self.high -= 0x4000_0000;
            } else {
                break;
            }
            self.low <<= 1;
            self.high = (self.high << 1) | 1;
        }
    }
    fn finish(mut self) -> Vec<u8> {
        self.pending += 1;
        if self.low < 0x4000_0000 {
            self.emit_with_pending(0);
        } else {
            self.emit_with_pending(1);
        }
        if self.nbits > 0 {
            self.cur <<= 8 - self.nbits;
            self.out.push(self.cur);
        }
        self.out
    }
}

struct ArithDec<'a> {
    low: u32,
    high: u32,
    value: u32,
    data: &'a [u8],
    byte_pos: usize,
    bit_pos: u32,
}

impl<'a> ArithDec<'a> {
    fn new(data: &'a [u8]) -> Self {
        let mut dec = ArithDec { low: 0, high: 0xFFFF_FFFF, value: 0, data, byte_pos: 0, bit_pos: 0 };
        for _ in 0..32 {
            let b = dec.next_bit();
            dec.value = (dec.value << 1) | b;
        }
        dec
    }
    fn next_bit(&mut self) -> u32 {
        if self.byte_pos >= self.data.len() {
            return 0;
        }
        let bit = ((self.data[self.byte_pos] >> (7 - self.bit_pos)) & 1) as u32;
        self.bit_pos += 1;
        if self.bit_pos == 8 {
            self.bit_pos = 0;
            self.byte_pos += 1;
        }
        bit
    }
    fn get_cum(&self, total: u32) -> u32 {
        if self.value < self.low {
            return 0;
        }
        if self.value > self.high {
            return total - 1;
        }
        let range = self.high as u64 - self.low as u64 + 1;
        (((self.value as u64 - self.low as u64 + 1) * total as u64 - 1) / range) as u32
    }
    fn confirm(&mut self, cum: u32, freq: u32, total: u32) {
        let range = self.high as u64 - self.low as u64 + 1;
        self.high = self.low + ((range * (cum + freq) as u64 / total as u64) - 1) as u32;
        self.low = self.low + (range * cum as u64 / total as u64) as u32;
        loop {
            if self.high < 0x8000_0000 {
                // no offset adjustment
            } else if self.low >= 0x8000_0000 {
                self.low -= 0x8000_0000;
                self.high -= 0x8000_0000;
                self.value -= 0x8000_0000;
            } else if self.low >= 0x4000_0000 && self.high < 0xC000_0000 {
                self.low -= 0x4000_0000;
                self.high -= 0x4000_0000;
                self.value -= 0x4000_0000;
            } else {
                break;
            }
            self.low <<= 1;
            self.high = (self.high << 1) | 1;
            let b = self.next_bit();
            self.value = (self.value << 1) | b;
        }
    }
}

// ---------------------------------------------------------------- PPM core

fn enc_ctx(enc: &mut ArithEnc, ctx: &Ctx, sym: u8, excluded: &mut [bool; 256]) -> bool {
    let (total_f, distinct) = ctx.filtered_stats(excluded);
    if distinct == 0 {
        return false;
    }
    let denom = total_f + distinct;
    let mut cum: u32 = 0;
    let mut found: Option<(u32, u32)> = None;
    for (i, &s) in ctx.syms.iter().enumerate() {
        if excluded[s as usize] {
            continue;
        }
        if s == sym {
            found = Some((cum, ctx.counts[i] as u32));
        }
        cum += ctx.counts[i] as u32;
    }
    match found {
        Some((c, f)) => {
            enc.encode(c, f, denom);
            true
        }
        None => {
            enc.encode(total_f, distinct, denom);
            for &s in ctx.syms.iter() {
                excluded[s as usize] = true;
            }
            false
        }
    }
}

enum DecStep {
    Skipped,
    Escaped,
    Symbol(u8),
}

fn dec_ctx(dec: &mut ArithDec, ctx: &Ctx, excluded: &mut [bool; 256]) -> DecStep {
    let (total_f, distinct) = ctx.filtered_stats(excluded);
    if distinct == 0 {
        return DecStep::Skipped;
    }
    let denom = total_f + distinct;
    let target = dec.get_cum(denom);
    let mut cum: u32 = 0;
    for (i, &s) in ctx.syms.iter().enumerate() {
        if excluded[s as usize] {
            continue;
        }
        let f = ctx.counts[i] as u32;
        if target < cum + f {
            dec.confirm(cum, f, denom);
            return DecStep::Symbol(s);
        }
        cum += f;
    }
    dec.confirm(total_f, distinct, denom);
    for &s in ctx.syms.iter() {
        excluded[s as usize] = true;
    }
    DecStep::Escaped
}

fn ppm_encode(input: &[u8], model: &[u8]) -> Vec<u8> {
    let mut ppm = Ppm::new(model);
    let mut enc = ArithEnc::new();
    for pos in 0..input.len() {
        let sym = input[pos];
        let hist = &input[..pos];
        let mut excluded = [false; 256];
        let mut done = false;
        let max_k = MAX_ORDER.min(pos);
        for k in (1..=max_k).rev() {
            if let Some(ctx) = ppm.ctx_ref(k, hist) {
                if enc_ctx(&mut enc, ctx, sym, &mut excluded) {
                    done = true;
                    break;
                }
            }
        }
        if !done {
            let ctx = &ppm.o0;
            if !enc_ctx(&mut enc, ctx, sym, &mut excluded) {
                let mut rank: u32 = 0;
                for b in 0..sym {
                    if !excluded[b as usize] {
                        rank += 1;
                    }
                }
                let denom = excluded.iter().filter(|&&e| !e).count() as u32;
                enc.encode(rank, 1, denom);
            }
        }
        ppm.update(hist, sym);
    }
    enc.finish()
}

fn ppm_decode(data: &[u8], model: &[u8], original_len: usize) -> Vec<u8> {
    let mut ppm = Ppm::new(model);
    let mut dec = ArithDec::new(data);
    let mut out: Vec<u8> = Vec::with_capacity(original_len.min(1 << 20));
    for _ in 0..original_len {
        let mut excluded = [false; 256];
        let mut sym_out: Option<u8> = None;
        let max_k = MAX_ORDER.min(out.len());
        for k in (1..=max_k).rev() {
            if let Some(ctx) = ppm.ctx_ref(k, &out) {
                match dec_ctx(&mut dec, ctx, &mut excluded) {
                    DecStep::Symbol(s) => {
                        sym_out = Some(s);
                        break;
                    }
                    DecStep::Escaped | DecStep::Skipped => {}
                }
            }
        }
        let sym = match sym_out {
            Some(s) => s,
            None => {
                match dec_ctx(&mut dec, &ppm.o0, &mut excluded) {
                    DecStep::Symbol(s) => s,
                    _ => {
                        let denom = excluded.iter().filter(|&&e| !e).count() as u32;
                        if denom == 0 {
                            // Malformed stream: emit a placeholder; the CRC
                            // check in decode will reject the block.
                            0u8
                        } else {
                        let target = dec.get_cum(denom);
                        let mut rank: u32 = 0;
                        let mut chosen: u8 = 0;
                        for b in 0..=255u8 {
                            if !excluded[b as usize] {
                                if rank == target {
                                    chosen = b;
                                    break;
                                }
                                rank += 1;
                            }
                        }
                        dec.confirm(target, 1, denom);
                        chosen
                        }
                    }
                }
            }
        };
        out.push(sym);
        let hist_len = out.len() - 1;
        // Update with the history as it was before this symbol.
        ppm.update(&out[..hist_len], sym);
    }
    out
}

// ---------------------------------------------------------------- codec

fn scale_counts(counts: &[u32], out: &mut Vec<u8>) {
    // counts: 256 entries for one context; emit u16 distinct + pairs.
    let mut pairs: Vec<(u8, u32)> = Vec::new();
    let mut sum: u64 = 0;
    for (i, &c) in counts.iter().enumerate() {
        if c > 0 {
            pairs.push((i as u8, c));
            sum += c as u64;
        }
    }
    out.extend_from_slice(&(pairs.len() as u16).to_le_bytes());
    for (sym, c) in pairs {
        let scaled: u32 = if sum <= 48 {
            c
        } else {
            ((c as u64 * 48) / sum).max(1) as u32
        };
        out.push(sym);
        out.push(scaled.min(255) as u8);
    }
}

impl Codec for Candidate {
    fn train(samples: &[&[u8]]) -> Result<Vec<u8>, CodecError> {
        if samples.is_empty() {
            return Ok(Vec::new());
        }
        let mut o0 = vec![0u32; 256];
        let mut bigram = vec![0u32; 65536];
        let mut total_bytes: usize = 0;
        for sample in samples {
            total_bytes += sample.len();
            let mut prev: Option<u8> = None;
            for &b in sample.iter() {
                o0[b as usize] += 1;
                if let Some(p) = prev {
                    bigram[(p as usize) * 256 + b as usize] += 1;
                }
                prev = Some(b);
            }
        }
        if total_bytes == 0 {
            return Ok(Vec::new());
        }
        let mut model: Vec<u8> = Vec::new();
        model.push(1u8);
        scale_counts(&o0, &mut model);
        for p in 0..256usize {
            let row = &bigram[p * 256..p * 256 + 256];
            scale_counts(row, &mut model);
        }
        if model.len() > 60000 {
            return Ok(Vec::new());
        }
        Ok(model)
    }

    fn encode(input: &[u8], model: &[u8]) -> Result<Vec<u8>, CodecError> {
        if input.len() < 256 {
            let body = zstd_compress(input, ZSTD_LEVEL)?;
            let mut out = Vec::with_capacity(body.len() + 1);
            out.push(MODE_RAW);
            out.extend_from_slice(&body);
            return Ok(out);
        }
        let mut best: Option<(u8, Vec<u8>)> = None;
        let mut first_err: Option<CodecError> = None;
        let consider = |mode: u8, body: Vec<u8>, best: &mut Option<(u8, Vec<u8>)>| {
            let better = match best {
                Some((_, b)) => body.len() < b.len(),
                None => true,
            };
            if better {
                *best = Some((mode, body));
            }
        };
        match zstd_compress(input, ZSTD_LEVEL) {
            Ok(body) => consider(MODE_RAW, body, &mut best),
            Err(e) => first_err = Some(e),
        }
        // PPM path: body = CRC-32 (LE) + arithmetic stream.
        {
            let arith = ppm_encode(input, model);
            let mut body = Vec::with_capacity(arith.len() + 4);
            body.extend_from_slice(&crc32(input).to_le_bytes());
            body.extend_from_slice(&arith);
            consider(MODE_PPM, body, &mut best);
        }
        {
            let trans = build_mtf_stream(input);
            if trans.len() <= 4 * 1024 * 1024 {
                match zstd_compress(&trans, ZSTD_LEVEL) {
                    Ok(body) => consider(MODE_MTF, body, &mut best),
                    Err(e) => {
                        if first_err.is_none() {
                            first_err = Some(e);
                        }
                    }
                }
            }
        }
        match best {
            Some((mode, body)) => {
                let mut out = Vec::with_capacity(body.len() + 1);
                out.push(mode);
                out.extend_from_slice(&body);
                Ok(out)
            }
            None => Err(first_err.unwrap_or_else(|| "encode failed".to_string())),
        }
    }

    fn decode(encoded: &[u8], model: &[u8], original_len: usize) -> Result<Vec<u8>, CodecError> {
        if encoded.is_empty() {
            return Err("empty frame".into());
        }
        if original_len > 16 * 1024 * 1024 {
            return Err("original length implausible".into());
        }
        let mode = encoded[0];
        let body = &encoded[1..];
        match mode {
            MODE_RAW => {
                let out = zstd_decompress(body)?;
                if out.len() != original_len {
                    return Err("length mismatch in raw path".into());
                }
                Ok(out)
            }
            MODE_PPM => {
                if body.len() < 4 {
                    return Err("ppm body too short".into());
                }
                let expect = u32::from_le_bytes([body[0], body[1], body[2], body[3]]);
                let out = ppm_decode(&body[4..], model, original_len);
                if crc32(&out) != expect {
                    return Err("ppm crc mismatch".into());
                }
                Ok(out)
            }
            MODE_MTF => {
                let trans = zstd_decompress(body)?;
                invert_mtf_stream(&trans, original_len)
            }
            _ => Err("unknown mode byte".into()),
        }
    }
}
