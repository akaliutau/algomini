// Line-dictionary + sparse PPM text codec.
//
// Mechanism (refinement over parent sparse-PPM seed): text blocks here carry
// many exact repeated lines (dev probe: 28248/74246 lines repeat, 45998
// distinct, max 81 B, LF-delimited) that order-6 PPM cannot link across
// kilobytes of distance. New mode 2 therefore (a) splits input on `\n`
// keeping delimiters so CRLF/LF/ lone fragments round-trip, (b) keeps a
// per-block table of the most-saving repeated lines (saving counts table
// bytes + per-occurrence id bytes), serializes it as varint count +
// varint-len entries, (c) recodes the line sequence as 0x00 literal
// (varint len + raw bytes, any bytes incl. NUL/binary) or 0x01 reference
// (varint id), then (d) compresses that token buffer with the same PPM
// arithmetic coder or zstd-22 and keeps the smaller. Tables/ids/lengths are
// all inside the counted payload; nothing is trained across blocks.
// Applicability: line-oriented text with repeated records/verses gains;
// binary, single-huge-line, or low-repetition inputs fail the saving test
// and fall back to mode 0/1. Decoder rebuilds lines by concatenation, so
// whitespace, numeric spellings, quoting and endings are preserved exactly.
// Framing: mode byte 0 = zstd22, 1 = direct PPM bits + hash, 2 = dict
// transform (sub byte 0 = zstd(T), 1 = PPM(T)) + u32 T-len + hash32(input).
// Encoder compares complete tagged lengths and keeps the shortest.
use crate::{Codec, CodecError};
use std::collections::HashMap;
use std::hash::{BuildHasherDefault, Hasher};

pub const MAX_ORDER: usize = 6;
pub const INCREMENT: u32 = 2;
pub const RESCALE: u32 = 16384;
pub const USE_SEE: bool = true;

const MAX_CONTEXTS: usize = 1 << 20;
const MAX_ENTRIES: usize = 1 << 22;
const HALF: u32 = 0x8000_0000;
const QUARTER: u32 = 0x4000_0000;
const THREE_QUARTER: u32 = 0xC000_0000;
const LIMIT_INPUT: usize = 2 * 1024 * 1024;
const LIMIT_OUTPUT: usize = 4 * 1024 * 1024;
const SEE_N: usize = 24576;

pub struct Candidate;

#[derive(Default)]
struct IntHasher {
    h: u64,
}
impl Hasher for IntHasher {
    fn write(&mut self, b: &[u8]) {
        for &x in b {
            self.h = self.h.wrapping_mul(0x100000001B3).wrapping_add(x as u64);
        }
    }
    fn write_u64(&mut self, k: u64) {
        let mut x = k.wrapping_mul(0x9E3779B97F4A7C15);
        x ^= x >> 29;
        x = x.wrapping_mul(0xBF58476D1CE4E5B9);
        x ^= x >> 32;
        self.h = x;
    }
    fn finish(&self) -> u64 {
        self.h
    }
}
type CtxMap = HashMap<u64, u32, BuildHasherDefault<IntHasher>>;

struct Sym {
    sym: u8,
    count: u32,
}
struct Ctx {
    syms: Vec<Sym>,
    sum: u32,
}

fn order_mask(order: usize) -> u64 {
    if order >= 8 {
        u64::MAX
    } else if order == 0 {
        0
    } else {
        (1u64 << (8 * order as u32)) - 1
    }
}
fn ctx_key(history: u64, order: usize) -> u64 {
    (1u64 << (8 * order as u32)) | (history & order_mask(order))
}
fn hash32(data: &[u8]) -> u32 {
    let mut h: u32 = 2166136261;
    for &b in data {
        h ^= b as u32;
        h = h.wrapping_mul(16777619);
    }
    h
}

struct See {
    hit: [u32; SEE_N],
    esc: [u32; SEE_N],
    used: [bool; SEE_N],
}
impl See {
    fn new() -> Self {
        Self {
            hit: [0; SEE_N],
            esc: [0; SEE_N],
            used: [false; SEE_N],
        }
    }
    fn counts(&mut self, feat: usize, mean: u32) -> (u32, u32) {
        if !self.used[feat] {
            self.used[feat] = true;
            let mut h = core::cmp::min(mean, 32);
            if h == 0 {
                h = 1;
            }
            self.hit[feat] = h;
            self.esc[feat] = 1;
        }
        (self.hit[feat], self.esc[feat])
    }
    fn update(&mut self, feat: usize, is_hit: bool) {
        if is_hit {
            self.hit[feat] += 1;
        } else {
            self.esc[feat] += 1;
        }
        if self.hit[feat] + self.esc[feat] >= 1024 {
            self.hit[feat] = core::cmp::max(1, self.hit[feat] / 2);
            self.esc[feat] = core::cmp::max(1, self.esc[feat] / 2);
        }
    }
}

fn see_feature(order: usize, types: u32, mean: u32, has_excl: u32) -> usize {
    let t = core::cmp::min(types.saturating_sub(1), 15) as usize;
    let m = if mean < 16 { mean } else { 16 + core::cmp::min(7, (mean / 16).ilog2()) } as usize;
    (((order * 16 + t) * 24 + m) * 2) + (has_excl as usize)
}

struct BitWriter {
    buf: Vec<u8>,
    cur: u8,
    n: u8,
}
impl BitWriter {
    fn new(cap: usize) -> Self {
        Self {
            buf: Vec::with_capacity(cap),
            cur: 0,
            n: 0,
        }
    }
    fn put(&mut self, bit: u32) {
        self.cur = (self.cur << 1) | (bit as u8 & 1);
        self.n += 1;
        if self.n == 8 {
            self.buf.push(self.cur);
            self.cur = 0;
            self.n = 0;
        }
    }
    fn finish(mut self) -> Vec<u8> {
        if self.n > 0 {
            self.cur <<= 8 - self.n;
            self.buf.push(self.cur);
        }
        self.buf
    }
}

struct ArithEncoder {
    low: u32,
    high: u32,
    pending: u32,
    out: BitWriter,
}
impl ArithEncoder {
    fn new(cap: usize) -> Self {
        Self {
            low: 0,
            high: u32::MAX,
            pending: 0,
            out: BitWriter::new(cap),
        }
    }
    fn emit(&mut self, bit: u32) {
        self.out.put(bit);
        while self.pending > 0 {
            self.out.put(bit ^ 1);
            self.pending -= 1;
        }
    }
    fn encode(&mut self, total: u32, cum_lo: u32, cum_hi: u32) {
        let range = (self.high as u64 - self.low as u64) + 1;
        let t = total as u64;
        self.high = (self.low as u64 + range * cum_hi as u64 / t - 1) as u32;
        self.low = (self.low as u64 + range * cum_lo as u64 / t) as u32;
        loop {
            if self.high < HALF {
                self.emit(0);
            } else if self.low >= HALF {
                self.emit(1);
                self.low -= HALF;
                self.high -= HALF;
            } else if self.low >= QUARTER && self.high < THREE_QUARTER {
                self.pending += 1;
                self.low -= QUARTER;
                self.high -= QUARTER;
            } else {
                break;
            }
            self.low <<= 1;
            self.high = (self.high << 1) | 1;
        }
    }
    fn finish(mut self) -> Vec<u8> {
        self.pending += 1;
        if self.low < QUARTER {
            self.emit(0);
        } else {
            self.emit(1);
        }
        self.out.finish()
    }
}

struct BitReader<'a> {
    data: &'a [u8],
    pos: usize,
}
impl<'a> BitReader<'a> {
    fn new(data: &'a [u8]) -> Self {
        Self { data, pos: 0 }
    }
    fn get(&mut self) -> u32 {
        let b = self.pos;
        self.pos += 1;
        let i = b >> 3;
        if i < self.data.len() {
            ((self.data[i] >> (7 - (b & 7))) & 1) as u32
        } else {
            0
        }
    }
}

struct ArithDecoder<'a> {
    low: u32,
    high: u32,
    code: u32,
    rd: BitReader<'a>,
}
impl<'a> ArithDecoder<'a> {
    fn new(data: &'a [u8]) -> Self {
        let mut rd = BitReader::new(data);
        let mut code: u32 = 0;
        for _ in 0..32 {
            code = (code << 1) | rd.get();
        }
        Self {
            low: 0,
            high: u32::MAX,
            code,
            rd,
        }
    }
    fn scaled(&self, total: u32) -> Result<u32, CodecError> {
        if total == 0 {
            return Err("ppm: empty distribution".to_string());
        }
        let range = (self.high as u64 - self.low as u64) + 1;
        let v = (self.code as u64 - self.low as u64 + 1) * total as u64 - 1;
        Ok((v / range) as u32)
    }
    fn update(&mut self, total: u32, cum_lo: u32, cum_hi: u32) -> Result<(), CodecError> {
        if total == 0 || cum_lo >= cum_hi || cum_hi > total {
            return Err("ppm: bad range".to_string());
        }
        let range = (self.high as u64 - self.low as u64) + 1;
        let t = total as u64;
        self.high = (self.low as u64 + range * cum_hi as u64 / t - 1) as u32;
        self.low = (self.low as u64 + range * cum_lo as u64 / t) as u32;
        loop {
            if self.high < HALF {
            } else if self.low >= HALF {
                self.low -= HALF;
                self.high -= HALF;
                self.code -= HALF;
            } else if self.low >= QUARTER && self.high < THREE_QUARTER {
                self.low -= QUARTER;
                self.high -= QUARTER;
                self.code -= QUARTER;
            } else {
                break;
            }
            self.low <<= 1;
            self.high = (self.high << 1) | 1;
            self.code = (self.code << 1) | self.rd.get();
        }
        Ok(())
    }
}

struct Ppm {
    arena: Vec<Ctx>,
    map: CtxMap,
    zero: [u32; 256],
    zero_total: u32,
    entries: usize,
    history: u64,
}
impl Ppm {
    fn new() -> Self {
        Self {
            arena: Vec::new(),
            map: CtxMap::default(),
            zero: [1u32; 256],
            zero_total: 256,
            entries: 0,
            history: 0,
        }
    }
    fn lookup_or_create(&mut self, key: u64) -> Option<u32> {
        if let Some(&i) = self.map.get(&key) {
            return Some(i);
        }
        if self.arena.len() >= MAX_CONTEXTS {
            return None;
        }
        let idx = self.arena.len() as u32;
        self.arena.push(Ctx {
            syms: Vec::new(),
            sum: 0,
        });
        self.map.insert(key, idx);
        Some(idx)
    }
    fn rescale_ctx(c: &mut Ctx) {
        let mut s: u32 = 0;
        for e in c.syms.iter_mut() {
            e.count = core::cmp::max(1, e.count / 2);
            s += e.count;
        }
        c.sum = s;
    }
    fn update_ctx(&mut self, idx: u32, value: u8) {
        let c = &mut self.arena[idx as usize];
        let mut at = None;
        for (i, e) in c.syms.iter().enumerate() {
            if e.sym == value {
                at = Some(i);
                break;
            }
        }
        match at {
            Some(i) => {
                c.syms[i].count += INCREMENT;
                c.sum += INCREMENT;
                let mut j = i;
                while j > 0 && c.syms[j].count > c.syms[j - 1].count {
                    c.syms.swap(j, j - 1);
                    j -= 1;
                }
            }
            None => {
                if self.entries >= MAX_ENTRIES {
                    return;
                }
                c.syms.push(Sym {
                    sym: value,
                    count: INCREMENT,
                });
                c.sum += INCREMENT;
                self.entries += 1;
                let mut j = c.syms.len() - 1;
                while j > 0 && c.syms[j].count > c.syms[j - 1].count {
                    c.syms.swap(j, j - 1);
                    j -= 1;
                }
            }
        }
        let c = &mut self.arena[idx as usize];
        if c.sum + c.syms.len() as u32 >= RESCALE {
            Self::rescale_ctx(c);
        }
    }
    fn update_zero(&mut self, value: u8) {
        self.zero[value as usize] += INCREMENT;
        self.zero_total += INCREMENT;
        if self.zero_total >= RESCALE {
            let mut s: u32 = 0;
            for z in self.zero.iter_mut() {
                *z = core::cmp::max(1, *z / 2);
                s += *z;
            }
            self.zero_total = s;
        }
    }
}

fn excluded_set() -> [u64; 4] {
    [0u64; 4]
}
fn excl_add(e: &mut [u64; 4], s: u8) {
    e[(s >> 6) as usize] |= 1u64 << (s & 63);
}
fn excl_has(e: &[u64; 4], s: u8) -> bool {
    (e[(s >> 6) as usize] >> (s & 63)) & 1 == 1
}
fn excl_empty(e: &[u64; 4]) -> bool {
    e[0] == 0 && e[1] == 0 && e[2] == 0 && e[3] == 0
}

fn ppm_compress(input: &[u8]) -> Vec<u8> {
    let mut ppm = Ppm::new();
    ppm.map.reserve(core::cmp::min(input.len() / 3 + 256, MAX_CONTEXTS));
    ppm.arena.reserve(core::cmp::min(input.len() / 3 + 256, MAX_CONTEXTS));
    let mut see = See::new();
    let mut enc = ArithEncoder::new(input.len() / 2 + 64);
    let mut visited: [u32; 8] = [0; 8];
    for (pos, &value) in input.iter().enumerate() {
        let top = core::cmp::min(pos, MAX_ORDER);
        let mut nv = 0usize;
        let mut excl = excluded_set();
        let mut done = false;
        for order in (1..=top).rev() {
            let key = ctx_key(ppm.history, order);
            let Some(idx) = ppm.lookup_or_create(key) else { continue; };
            visited[nv] = idx;
            nv += 1;
            let c = &ppm.arena[idx as usize];
            if c.syms.is_empty() {
                continue;
            }
            let mut total: u32 = 0;
            let mut nvis: u32 = 0;
            let mut found = false;
            for e in c.syms.iter() {
                if excl_has(&excl, e.sym) {
                    continue;
                }
                total += e.count;
                nvis += 1;
                if e.sym == value {
                    found = true;
                }
            }
            if nvis == 0 {
                continue;
            }
            let mean = core::cmp::max(1, total / nvis);
            let has_excl = if excl_empty(&excl) { 0 } else { 1 };
            let feat = see_feature(order, nvis, mean, has_excl) * 4 + match ppm.history as u8 { b' ' | b'\n' | b'\r' | b'\t' => 0, b'a'..=b'z' | b'A'..=b'Z' => 1, b'0'..=b'9' => 2, _ => 3 };
            let (hit, esc) = see.counts(feat, mean);
            let den_see = hit + esc;
            if found {
                enc.encode(den_see, 0, hit);
                see.update(feat, true);
                if nvis == 1 {
                    done = true;
                    break;
                }
                let mut cum: u32 = 0;
                for e in c.syms.iter() {
                    if excl_has(&excl, e.sym) {
                        continue;
                    }
                    if e.sym == value {
                        enc.encode(total, cum, cum + e.count);
                        break;
                    }
                    cum += e.count;
                }
                done = true;
                break;
            } else {
                enc.encode(den_see, hit, den_see);
                see.update(feat, false);
                for e in c.syms.iter() {
                    excl_add(&mut excl, e.sym);
                }
            }
        }
        let mut zero_used = false;
        if !done {
            let mut total: u32 = 0;
            for b in 0..256u16 {
                if !excl_has(&excl, b as u8) {
                    total += ppm.zero[b as usize];
                }
            }
            let mut cum: u32 = 0;
            for b in 0..256u16 {
                if excl_has(&excl, b as u8) {
                    continue;
                }
                let zc = ppm.zero[b as usize];
                if b as u8 == value {
                    enc.encode(total, cum, cum + zc);
                    break;
                }
                cum += zc;
            }
            zero_used = true;
        }
        if zero_used {
            ppm.update_zero(value);
        }
        for v in 0..nv {
            ppm.update_ctx(visited[v], value);
        }
        ppm.history = ((ppm.history << 8) | value as u64) & order_mask(MAX_ORDER);
    }
    enc.finish()
}

fn ppm_decompress(bits: &[u8], original_len: usize) -> Result<Vec<u8>, CodecError> {
    if original_len > LIMIT_INPUT + 1024 * 1024 {
        return Err("ppm: bad length".to_string());
    }
    let mut out = Vec::with_capacity(original_len);
    if original_len == 0 {
        return Ok(out);
    }
    if bits.len() > LIMIT_OUTPUT + 16 {
        return Err("ppm: payload too large".to_string());
    }
    let mut ppm = Ppm::new();
    ppm.map.reserve(core::cmp::min(original_len / 3 + 256, MAX_CONTEXTS));
    ppm.arena.reserve(core::cmp::min(original_len / 3 + 256, MAX_CONTEXTS));
    let mut see = See::new();
    let mut dec = ArithDecoder::new(bits);
    let mut visited: [u32; 8] = [0; 8];
    for pos in 0..original_len {
        let top = core::cmp::min(pos, MAX_ORDER);
        let mut nv = 0usize;
        let mut excl = excluded_set();
        let mut value: Option<u8> = None;
        for order in (1..=top).rev() {
            let key = ctx_key(ppm.history, order);
            let Some(idx) = ppm.lookup_or_create(key) else { continue; };
            visited[nv] = idx;
            nv += 1;
            let (total, nvis) = {
                let c = &ppm.arena[idx as usize];
                if c.syms.is_empty() {
                    (0, 0)
                } else {
                    let mut t: u32 = 0;
                    let mut n: u32 = 0;
                    for e in c.syms.iter() {
                        if excl_has(&excl, e.sym) {
                            continue;
                        }
                        t += e.count;
                        n += 1;
                    }
                    (t, n)
                }
            };
            if nvis == 0 {
                continue;
            }
            let mean = core::cmp::max(1, total / nvis);
            let has_excl = if excl_empty(&excl) { 0 } else { 1 };
            let feat = see_feature(order, nvis, mean, has_excl) * 4 + match ppm.history as u8 { b' ' | b'\n' | b'\r' | b'\t' => 0, b'a'..=b'z' | b'A'..=b'Z' => 1, b'0'..=b'9' => 2, _ => 3 };
            let (hit, esc) = see.counts(feat, mean);
            let den_see = hit + esc;
            let s = dec.scaled(den_see)?;
            if s < hit {
                dec.update(den_see, 0, hit)?;
                see.update(feat, true);
                if nvis == 1 {
                    let mut pick: u8 = 0;
                    {
                        let c = &ppm.arena[idx as usize];
                        for e in c.syms.iter() {
                            if excl_has(&excl, e.sym) {
                                continue;
                            }
                            pick = e.sym;
                            break;
                        }
                    }
                    value = Some(pick);
                    break;
                }
                let s2 = dec.scaled(total)?;
                let mut cum: u32 = 0;
                let mut pick: u8 = 0;
                let mut plo: u32 = 0;
                let mut phi: u32 = 0;
                {
                    let c = &ppm.arena[idx as usize];
                    for e in c.syms.iter() {
                        if excl_has(&excl, e.sym) {
                            continue;
                        }
                        if s2 < cum + e.count {
                            pick = e.sym;
                            plo = cum;
                            phi = cum + e.count;
                            break;
                        }
                        cum += e.count;
                    }
                }
                dec.update(total, plo, phi)?;
                value = Some(pick);
                break;
            } else {
                dec.update(den_see, hit, den_see)?;
                see.update(feat, false);
                let c = &ppm.arena[idx as usize];
                for e in c.syms.iter() {
                    excl_add(&mut excl, e.sym);
                }
            }
        }
        let mut zero_used = false;
        let val = match value {
            Some(b) => b,
            None => {
                let mut total: u32 = 0;
                for b in 0..256u16 {
                    if !excl_has(&excl, b as u8) {
                        total += ppm.zero[b as usize];
                    }
                }
                if total == 0 {
                    return Err("ppm: empty order0".to_string());
                }
                let s = dec.scaled(total)?;
                let mut cum: u32 = 0;
                let mut pick: Option<u8> = None;
                let mut plo: u32 = 0;
                let mut phi: u32 = 0;
                for b in 0..256u16 {
                    if excl_has(&excl, b as u8) {
                        continue;
                    }
                    let zc = ppm.zero[b as usize];
                    if s < cum + zc {
                        pick = Some(b as u8);
                        plo = cum;
                        phi = cum + zc;
                        break;
                    }
                    cum += zc;
                }
                match pick {
                    Some(b) => {
                        dec.update(total, plo, phi)?;
                        zero_used = true;
                        b
                    }
                    None => return Err("ppm: order0 miss".to_string()),
                }
            }
        };
        if zero_used {
            ppm.update_zero(val);
        }
        for v in 0..nv {
            ppm.update_ctx(visited[v], val);
        }
        ppm.history = ((ppm.history << 8) | val as u64) & order_mask(MAX_ORDER);
        out.push(val);
    }
    Ok(out)
}

// ---- line-dictionary transform (mode 2) ----
fn var_put(v: u32, out: &mut Vec<u8>) {
    let mut x = v;
    loop {
        let b = (x & 0x7f) as u8;
        x >>= 7;
        if x == 0 {
            out.push(b | 0x80);
            break;
        } else {
            out.push(b);
        }
    }
}
fn var_get(buf: &[u8], pos: &mut usize) -> Result<u32, CodecError> {
    let mut x: u32 = 0;
    let mut s = 0u32;
    loop {
        if *pos >= buf.len() {
            return Err("dict: truncated varint".to_string());
        }
        let b = buf[*pos];
        *pos += 1;
        if b & 0x80 != 0 {
            x |= ((b & 0x7f) as u32) << s;
            break;
        } else {
            x |= ((b & 0x7f) as u32) << s;
            s += 7;
            if s > 28 {
                return Err("dict: varint overflow".to_string());
            }
        }
    }
    Ok(x)
}

fn build_token_buf(input: &[u8]) -> Option<Vec<u8>> {
    if input.len() < 2048 {
        return None;
    }
    let mut nl = 0usize;
    for &b in input.iter() {
        if b == b'\n' {
            nl += 1;
        }
    }
    if nl < 64 {
        return None;
    }
    let mut lines: Vec<&[u8]> = Vec::with_capacity(nl + 1);
    let mut st = 0usize;
    for (i, &b) in input.iter().enumerate() {
        if b == b'\n' {
            lines.push(&input[st..i + 1]);
            st = i + 1;
        }
    }
    if st < input.len() {
        lines.push(&input[st..]);
    } else if st == input.len() && input.is_empty() {
        return None;
    }
    if lines.len() < 64 {
        return None;
    }
    let mut freq: HashMap<Vec<u8>, u32> = HashMap::new();
    for ln in lines.iter() {
        if ln.len() < 6 || ln.len() > 4096 {
            continue;
        }
        let e = freq.entry((*ln).to_vec()).or_insert(0);
        *e += 1;
        if freq.len() > 60000 {
            break;
        }
    }
    let mut cand: Vec<(u64, Vec<u8>, u32)> = Vec::new();
    for (k, c) in freq.into_iter() {
        if c < 3 {
            continue;
        }
        let ln = k.len() as u64;
        // net saving vs literal stream: (c-1)*ln minus table + id bytes
        let save = (c as u64 - 1) * ln;
        let cost = ln + 4 + (c as u64) * 3;
        if save > cost + 64 {
            cand.push((save - cost, k, c));
        }
    }
    if cand.is_empty() {
        return None;
    }
    cand.sort_by(|a, b| b.0.cmp(&a.0));
    let mut total_dict = 0usize;
    let mut take = 0usize;
    for (s, k, _c) in cand.iter() {
        let _ = s;
        if take >= 2048 {
            break;
        }
        if total_dict + k.len() + 4 > 131072 {
            break;
        }
        total_dict += k.len() + 4;
        take += 1;
    }
    if take == 0 {
        return None;
    }
    cand.truncate(take);
    let mut id_of: HashMap<Vec<u8>, u32> = HashMap::with_capacity(cand.len() * 2);
    let mut dict: Vec<Vec<u8>> = Vec::with_capacity(cand.len());
    for (i, (_, k, _)) in cand.into_iter().enumerate() {
        id_of.insert(k.clone(), i as u32);
        dict.push(k);
    }
    let mut t: Vec<u8> = Vec::with_capacity(input.len());
    var_put(dict.len() as u32, &mut t);
    for d in dict.iter() {
        var_put(d.len() as u32, &mut t);
        t.extend_from_slice(d);
    }
    for ln in lines.iter() {
        if let Some(&id) = id_of.get(*ln) {
            t.push(1u8);
            var_put(id, &mut t);
        } else {
            t.push(0u8);
            var_put(ln.len() as u32, &mut t);
            t.extend_from_slice(ln);
        }
    }
    // Require token buffer to be plausibly useful (avoids pathological blowup).
    if t.len() > input.len() + 262144 {
        return None;
    }
    Some(t)
}

fn restore_token_buf(t: &[u8]) -> Result<Vec<u8>, CodecError> {
    let mut pos = 0usize;
    let n = var_get(t, &mut pos)? as usize;
    if n > 8192 {
        return Err("dict: too many entries".to_string());
    }
    let mut dict: Vec<Vec<u8>> = Vec::with_capacity(n);
    for _ in 0..n {
        let ln = var_get(t, &mut pos)? as usize;
        if ln == 0 || ln > LIMIT_INPUT + 16 {
            return Err("dict: bad entry len".to_string());
        }
        if pos + ln > t.len() {
            return Err("dict: truncated entry".to_string());
        }
        dict.push(t[pos..pos + ln].to_vec());
        pos += ln;
    }
    let mut out: Vec<u8> = Vec::new();
    while pos < t.len() {
        let kind = t[pos];
        pos += 1;
        if kind == 1 {
            let id = var_get(t, &mut pos)? as usize;
            if id >= dict.len() {
                return Err("dict: bad id".to_string());
            }
            let d = &dict[id];
            if out.len() + d.len() > LIMIT_INPUT {
                return Err("dict: output too large".to_string());
            }
            out.extend_from_slice(d);
        } else if kind == 0 {
            let ln = var_get(t, &mut pos)? as usize;
            if ln > LIMIT_INPUT + 16 {
                return Err("dict: bad lit len".to_string());
            }
            if pos + ln > t.len() {
                return Err("dict: truncated lit".to_string());
            }
            if out.len() + ln > LIMIT_INPUT {
                return Err("dict: output too large".to_string());
            }
            out.extend_from_slice(&t[pos..pos + ln]);
            pos += ln;
        } else {
            return Err("dict: bad kind".to_string());
        }
    }
    Ok(out)
}

impl Codec for Candidate {
    fn encode(input: &[u8], _model: &[u8]) -> Result<Vec<u8>, CodecError> {
        if input.len() > LIMIT_INPUT {
            return Err("input too large".to_string());
        }
        let z: Vec<u8> = crate::toolkit::zstd_compress(input, 22)?;
        if z.len() > LIMIT_OUTPUT {
            return Err("zstd payload too large".to_string());
        }
        if input.is_empty() {
            let mut value = vec![0u8];
            value.extend_from_slice(&z);
            return Ok(value);
        }
        let bits = ppm_compress(input);
        let h = hash32(input);
        let mut best: Vec<u8>;
        if bits.len().checked_add(5).is_none_or(|n| n > LIMIT_OUTPUT) {
            let mut v = Vec::with_capacity(z.len() + 1);
            v.push(0u8);
            v.extend_from_slice(&z);
            best = v;
        } else {
            let mut p = Vec::with_capacity(bits.len() + 5);
            p.push(1u8);
            p.extend_from_slice(&bits);
            p.extend_from_slice(&h.to_le_bytes());
            let mut zv = Vec::with_capacity(z.len() + 1);
            zv.push(0u8);
            zv.extend_from_slice(&z);
            if p.len() < zv.len() {
                best = p;
            } else {
                best = zv;
            }
        }
        if let Some(t) = build_token_buf(input) {
            if t.len() <= LIMIT_OUTPUT {
                let zt = crate::toolkit::zstd_compress(&t, 22).unwrap_or_default();
                let bt = ppm_compress(&t);
                let (sub, comp) = if !zt.is_empty() && zt.len() <= bt.len() {
                    (0u8, zt)
                } else {
                    (1u8, bt)
                };
                if comp.len() + 10 <= LIMIT_OUTPUT {
                    let mut p2 = Vec::with_capacity(comp.len() + 10);
                    p2.push(2u8);
                    p2.push(sub);
                    p2.extend_from_slice(&(t.len() as u32).to_le_bytes());
                    p2.extend_from_slice(&comp);
                    p2.extend_from_slice(&h.to_le_bytes());
                    if p2.len() < best.len() {
                        best = p2;
                    }
                }
            }
        }
        Ok(best)
    }

    fn decode(
        encoded: &[u8],
        _model: &[u8],
        original_len: usize,
    ) -> Result<Vec<u8>, CodecError> {
        if original_len > LIMIT_INPUT {
            return Err("bad original length".to_string());
        }
        if encoded.is_empty() {
            return Err("empty stream".to_string());
        }
        if encoded.len() > LIMIT_OUTPUT + 16 {
            return Err("stream too large".to_string());
        }
        match encoded[0] {
            0 => {
                let raw: Vec<u8> = crate::toolkit::zstd_decompress(&encoded[1..])?;
                if raw.len() != original_len {
                    return Err("length mismatch".to_string());
                }
                Ok(raw)
            }
            1 => {
                let pay = &encoded[1..];
                if pay.len() < 4 {
                    return Err("truncated ppm".to_string());
                }
                if original_len == 0 {
                    let want = hash32(&[]);
                    let got = u32::from_le_bytes([pay[0], pay[1], pay[2], pay[3]]);
                    if pay.len() != 4 || got != want {
                        return Err("bad empty ppm".to_string());
                    }
                    return Ok(Vec::new());
                }
                let (bits, tag) = pay.split_at(pay.len() - 4);
                let want = u32::from_le_bytes([tag[0], tag[1], tag[2], tag[3]]);
                let raw = ppm_decompress(bits, original_len)?;
                if hash32(&raw) != want {
                    return Err("checksum mismatch".to_string());
                }
                Ok(raw)
            }
            2 => {
                let pay = &encoded[1..];
                if pay.len() < 1 + 4 + 4 {
                    return Err("truncated dict".to_string());
                }
                let sub = pay[0];
                let tlen = u32::from_le_bytes([pay[1], pay[2], pay[3], pay[4]]) as usize;
                if tlen > LIMIT_OUTPUT {
                    return Err("dict: bad tlen".to_string());
                }
                if original_len == 0 {
                    return Err("dict: bad orig".to_string());
                }
                let (comp, tag) = pay[5..].split_at(pay[5..].len() - 4);
                let want = u32::from_le_bytes([tag[0], tag[1], tag[2], tag[3]]);
                let t: Vec<u8> = match sub {
                    0 => crate::toolkit::zstd_decompress(comp)?,
                    1 => ppm_decompress(comp, tlen)?,
                    _ => return Err("dict: bad sub".to_string()),
                };
                if t.len() != tlen {
                    return Err("dict: tlen mismatch".to_string());
                }
                let raw = restore_token_buf(&t)?;
                if raw.len() != original_len {
                    return Err("length mismatch".to_string());
                }
                if hash32(&raw) != want {
                    return Err("checksum mismatch".to_string());
                }
                Ok(raw)
            }
            _ => Err("bad tag".to_string()),
        }
    }
}
