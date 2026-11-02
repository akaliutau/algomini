use crate::toolkit::{zstd_compress, zstd_decompress};
use crate::{Codec, CodecError};

pub struct Candidate;

// inner tags: 0 = raw fallback, 1 = decimal-delta columns
fn wuv(v: u128, o: &mut Vec<u8>) {
    let mut x = v;
    loop {
        let b = (x & 0x7f) as u8;
        x >>= 7;
        if x == 0 {
            o.push(b);
            return;
        }
        o.push(b | 0x80);
    }
}
fn ruv(d: &[u8], p: &mut usize) -> Option<u128> {
    let mut s = 0u32;
    let mut r = 0u128;
    loop {
        if *p >= d.len() || s > 126 {
            return None;
        }
        let b = d[*p];
        *p += 1;
        r |= ((b & 0x7f) as u128) << s;
        s += 7;
        if b & 0x80 == 0 {
            return Some(r);
        }
    }
}
fn zig(v: i128) -> u128 {
    ((v << 1) ^ (v >> 127)) as u128
}
fn unzig(u: u128) -> Option<i128> {
    let v = ((u >> 1) as i128) ^ -((u & 1) as i128);
    Some(v)
}
fn pow10(e: u32) -> Option<i128> {
    let mut r = 1i128;
    for _ in 0..e {
        r = r.checked_mul(10)?;
    }
    Some(r)
}
// unwrap padding/quotes; returns (lead,quoted,core,t trail) or None
fn unwrap(t: &[u8]) -> Option<(u8, bool, &[u8], u8)> {
    let mut l = 0usize;
    while l < t.len() && (t[l] == b' ' || t[l] == b'\t') {
        l += 1;
    }
    let mut r = 0usize;
    while r + l < t.len() && (t[t.len() - 1 - r] == b' ' || t[t.len() - 1 - r] == b'\t') {
        r += 1;
    }
    if l + r > t.len() || l > 255 || r > 255 {
        return None;
    }
    let m = &t[l..t.len() - r];
    if m.len() >= 2 && m[0] == b'"' && m[m.len() - 1] == b'"' {
        let inner = &m[1..m.len() - 1];
        // validate doubled quotes only
        let mut i = 0;
        while i < inner.len() {
            if inner[i] == b'"' {
                if i + 1 < inner.len() && inner[i + 1] == b'"' {
                    i += 2;
                } else {
                    return None;
                }
            } else {
                i += 1;
            }
        }
        Some((l as u8, true, inner, r as u8))
    } else {
        if m.contains(&b'"') {
            return None;
        }
        Some((l as u8, false, m, r as u8))
    }
}
// parse decimal core -> (mantissa, scale)
fn parse_dec(core: &[u8]) -> Option<(i128, i32)> {
    if core.is_empty() || core.len() > 128 {
        return None;
    }
    let mut i = 0;
    if core[0] == b'+' || core[0] == b'-' {
        i = 1;
    }
    let ds = i;
    let mut int_d: &[u8] = &[];
    let mut frac_d: &[u8] = &[];
    let mut j = i;
    while j < core.len() && core[j].is_ascii_digit() {
        j += 1;
    }
    int_d = &core[i..j];
    if j < core.len() && core[j] == b'.' {
        let k = j + 1;
        j = k;
        while j < core.len() && core[j].is_ascii_digit() {
            j += 1;
        }
        frac_d = &core[k..j];
    }
    if int_d.is_empty() && frac_d.is_empty() {
        return None;
    }
    // reject empty int with no digits etc; allow ".5"
    let mut exp: i32 = 0;
    let mut has_exp = false;
    if j < core.len() && (core[j] == b'e' || core[j] == b'E') {
        has_exp = true;
        j += 1;
        if j >= core.len() {
            return None;
        }
        let mut neg = false;
        if core[j] == b'+' || core[j] == b'-' {
            neg = core[j] == b'-';
            j += 1;
        }
        let s = j;
        while j < core.len() && core[j].is_ascii_digit() {
            j += 1;
        }
        if s == j || j != core.len() || j - s > 5 {
            return None;
        }
        let mut e: i32 = 0;
        for &b in &core[s..j] {
            e = e.checked_mul(10)?.checked_add((b - b'0') as i32)?;
        }
        exp = if neg { -e } else { e };
        if exp.abs() > 10000 {
            return None;
        }
    } else if j != core.len() {
        return None;
    }
    let _ = (ds, has_exp);
    let neg = core[0] == b'-';
    let mut m: i128 = 0;
    for &b in int_d.iter().chain(frac_d.iter()) {
        m = m.checked_mul(10)?.checked_add((b - b'0') as i128)?;
    }
    if neg {
        m = m.checked_neg()?;
    }
    let scale = (frac_d.len() as i32).checked_sub(exp)?;
    if scale < -10000 || scale > 10000 {
        return None;
    }
    // normalize negative scale into mantissa
    if scale < 0 {
        let p = pow10((-scale) as u32)?;
        m = m.checked_mul(p)?;
        return Some((m, 0));
    }
    if scale > 76 {
        return None;
    }
    Some((m, scale))
}
fn to_common(m: i128, s: i32, cs: i32) -> Option<i128> {
    if s == cs {
        return Some(m);
    }
    if s > cs {
        let p = pow10((s - cs) as u32)?;
        if m % p != 0 {
            return None;
        }
        Some(m / p)
    } else {
        let p = pow10((cs - s) as u32)?;
        Some(m.checked_mul(p)?)
    }
}
fn fmt_can(m: i128, scale: i32, lead: u8, q: bool, trail: u8, out: &mut Vec<u8>) -> Option<()> {
    if scale < 0 || scale > 76 {
        return None;
    }
    for _ in 0..lead {
        out.push(b' ');
    }
    if q {
        out.push(b'"');
    }
    let mut dig = if m < 0 {
        out.push(b'-');
        m.checked_neg()?.to_string().into_bytes()
    } else {
        m.to_string().into_bytes()
    };
    if scale == 0 {
        out.extend_from_slice(&dig);
    } else {
        while dig.len() <= scale as usize {
            dig.insert(0, b'0');
        }
        let n = dig.len() - scale as usize;
        out.extend_from_slice(&dig[..n]);
        out.push(b'.');
        out.extend_from_slice(&dig[n..]);
    }
    if q {
        out.push(b'"');
    }
    for _ in 0..trail {
        out.push(b' ');
    }
    Some(())
}
fn split_fields(line: &[u8]) -> Option<Vec<(usize, usize)>> {
    let mut f = Vec::new();
    let mut st = 0usize;
    let mut i = 0usize;
    let mut q = false;
    while i < line.len() {
        let b = line[i];
        if q {
            if b == b'"' {
                if i + 1 < line.len() && line[i + 1] == b'"' {
                    i += 2;
                } else {
                    q = false;
                    i += 1;
                }
            } else {
                i += 1;
            }
        } else if b == b'"' {
            q = true;
            i += 1;
        } else if b == b',' {
            f.push((st, i));
            st = i + 1;
            i += 1;
        } else {
            i += 1;
        }
    }
    if q {
        return None;
    }
    f.push((st, line.len()));
    Some(f)
}
// Build transform inner (uncompressed). None => use raw fallback.
fn build_inner(input: &[u8]) -> Option<Vec<u8>> {
    if input.is_empty() || input.contains(&0) {
        return None;
    }
    // split lines preserving endings
    let mut lines: Vec<(&[u8], &[u8])> = Vec::new();
    let mut s = 0usize;
    let mut i = 0usize;
    while i < input.len() {
        if input[i] == b'\n' {
            let (c, e) = if i > s && input[i - 1] == b'\r' {
                (&input[s..i - 1], &input[i - 1..i + 1] as &[u8])
            } else {
                (&input[s..i], &input[i..i + 1] as &[u8])
            };
            lines.push((c, e));
            s = i + 1;
        } else if input[i] == b'\r' {
            return None; // lone CR
        }
        i += 1;
    }
    if s < input.len() {
        lines.push((&input[s..], &[][..]));
    } else if s == input.len() && !input.is_empty() {
        // trailing newline already recorded; nothing more
    }
    if lines.is_empty() {
        return None;
    }
    // uniform ending check
    let mut code: u8 = 0; // 0=\n 1=\r\n
    if !lines.is_empty() {
        let e0 = lines[0].1;
        if e0 == b"\r\n" as &[u8] {
            code = 1;
        } else if e0 != b"\n" as &[u8] && !lines[0].1.is_empty() {
            return None;
        }
        let ecode: &[u8] = if code == 1 { b"\r\n" } else { b"\n" };
        for k in 0..lines.len() {
            let e = lines[k].1;
            let last = k + 1 == lines.len();
            if last && e.is_empty() {
                continue;
            }
            if e != ecode {
                return None;
            }
        }
    }
    let last_nl = !lines[lines.len() - 1].1.is_empty();
    // parse fields
    let mut grid: Vec<Vec<(usize, usize)>> = Vec::new();
    let mut ncols: Option<usize> = None;
    for (c, _) in &lines {
        if c.len() > 1_000_000 {
            return None;
        }
        let f = split_fields(c)?;
        if let Some(n) = ncols {
            if f.len() != n {
                return None;
            }
        } else {
            ncols = Some(f.len());
        }
        grid.push(f);
    }
    let nc = ncols?;
    let nr = lines.len();
    if nc == 0 || nc > 4096 || nr < 2 || nr > 100_000 {
        return None;
    }
    if (nr as u64).checked_mul(nc as u64)? > 4_000_000 {
        return None;
    }
    // header detection: row0 has a non-decimal token
    let mut hdr = false;
    {
        let mut any_num = false;
        let mut any_non = false;
        for (a, b) in &grid[0] {
            let t = &lines[0].0[*a..*b];
            match unwrap(t).and_then(|(_, _, c, _)| parse_dec(c)) {
                Some(_) => any_num = true,
                None => any_non = true,
            }
        }
        // header if row0 has non-numeric but row1 is mostly numeric
        if any_non {
            let mut n1 = 0;
            for (a, b) in &grid[1] {
                let t = &lines[1].0[*a..*b];
                if unwrap(t).and_then(|(_, _, c, _)| parse_dec(c)).is_some() {
                    n1 += 1;
                }
            }
            if n1 * 2 >= nc {
                hdr = true;
            } else {
                // generic non-numeric first row (e.g. JSON): fallback
                return None;
            }
        }
        let _ = any_num;
    }
    let d0 = if hdr { 1 } else { 0 };
    let nd = nr - d0;
    if nd == 0 {
        return None;
    }
    // collect raw tokens per column for data rows
    // dup detection + delta suitability
    enum CK {
        Raw,
        Dup(usize),
        Delta(u8, bool, u8, i32),
    }
    let mut kinds: Vec<CK> = Vec::new();
    // store per-col parsed info for encode
    let mut colraw: Vec<Vec<&[u8]>> = Vec::new();
    for c in 0..nc {
        let mut v = Vec::with_capacity(nd);
        for r in d0..nr {
            let (a, b) = grid[r][c];
            v.push(&lines[r].0[a..b]);
        }
        colraw.push(v);
    }
    for c in 0..nc {
        // dup check
        let mut dup: Option<usize> = None;
        for k in 0..c {
            if colraw[c].len() == colraw[k].len()
                && colraw[c].iter().zip(colraw[k].iter()).all(|(a, b)| a == b)
            {
                dup = Some(k);
                break;
            }
        }
        if let Some(k) = dup {
            kinds.push(CK::Dup(k));
            continue;
        }
        // find first parsable for common pattern
        let mut common: Option<(u8, bool, u8, i32)> = None;
        for t in &colraw[c] {
            if let Some((l, q, core, tr)) = unwrap(t) {
                if let Some((_, sc)) = parse_dec(core) {
                    common = Some((l, q, tr, sc));
                    break;
                }
            }
        }
        let (cl, cq, ctr, cs) = match common {
            Some(x) => x,
            None => {
                kinds.push(CK::Raw);
                continue;
            }
        };
        // count exceptions via exact reformat test
        let mut exc = 0usize;
        for t in &colraw[c] {
            let ok = (|| -> Option<bool> {
                let (l, q, core, tr) = unwrap(t)?;
                if l != cl || q != cq || tr != ctr {
                    return Some(false);
                }
                let (m, s) = parse_dec(core)?;
                if core.first() == Some(&b'+') {
                    return Some(false);
                }
                let mc = to_common(m, s, cs)?;
                let mut b = Vec::new();
                fmt_can(mc, cs, cl, cq, ctr, &mut b)?;
                Some(b.as_slice() == *t)
            })();
            if ok != Some(true) {
                exc += 1;
            }
        }
        if exc * 2 > nd {
            kinds.push(CK::Raw);
        } else {
            kinds.push(CK::Delta(cl, cq, ctr, cs));
        }
    }
    // require at least one delta/dup column, else fallback
    if !kinds.iter().any(|k| matches!(k, CK::Delta(..) | CK::Dup(_))) {
        return None;
    }
    let mut inner = Vec::new();
    inner.extend_from_slice(b"DD1");
    wuv(nd as u128, &mut inner);
    wuv(nc as u128, &mut inner);
    inner.push(if hdr { 1 } else { 0 });
    if hdr {
        wuv(lines[0].0.len() as u128, &mut inner);
        inner.extend_from_slice(lines[0].0);
    }
    inner.push(code);
    inner.push(if last_nl { 1 } else { 0 });
    for (c, k) in kinds.iter().enumerate() {
        match k {
            CK::Raw => {
                inner.push(0);
                for t in &colraw[c] {
                    wuv(t.len() as u128, &mut inner);
                    inner.extend_from_slice(t);
                }
            }
            CK::Dup(p) => {
                inner.push(2);
                wuv(*p as u128, &mut inner);
            }
            CK::Delta(cl, cq, ctr, cs) => {
                inner.push(1);
                inner.push(*cl);
                inner.push(if *cq { 1 } else { 0 });
                inner.push(*ctr);
                wuv(zig(*cs as i128), &mut inner);
                let mut prev: Option<i128> = None;
                for t in &colraw[c] {
                    let cv = (|| -> Option<i128> {
                        let (l, q, core, tr) = unwrap(t)?;
                        if l != *cl || q != *cq || tr != *ctr {
                            return None;
                        }
                        if core.first() == Some(&b'+') {
                            return None;
                        }
                        let (m, s) = parse_dec(core)?;
                        let mc = to_common(m, s, *cs)?;
                        let mut b = Vec::new();
                        fmt_can(mc, *cs, *cl, *cq, *ctr, &mut b)?;
                        if b.as_slice() != *t {
                            return None;
                        }
                        Some(mc)
                    })();
                    match cv {
                        None => {
                            inner.push(1);
                            wuv(t.len() as u128, &mut inner);
                            inner.extend_from_slice(t);
                            // prev unchanged
                        }
                        Some(mc) => {
                            inner.push(0);
                            match prev {
                                None => wuv(zig(mc), &mut inner),
                                Some(p) => {
                                    let d = mc.checked_sub(p)?;
                                    wuv(zig(d), &mut inner);
                                }
                            }
                            prev = Some(mc);
                        }
                    }
                }
            }
        }
    }
    Some(inner)
}

impl Codec for Candidate {
    fn train(_s: &[&[u8]]) -> Result<Vec<u8>, CodecError> {
        Ok(Vec::new())
    }
    fn encode(input: &[u8], _m: &[u8]) -> Result<Vec<u8>, CodecError> {
        if input.len() > crate::MAX_INPUT {
            return Err("input too large".into());
        }
        let rawz = zstd_compress(input, 19).map_err(|e| e.to_string())?;
        let mut best = Vec::with_capacity(rawz.len() + 1);
        best.push(0u8);
        best.extend_from_slice(&rawz);
        if let Some(inner) = build_inner(input) {
            if inner.len() <= crate::MAX_INPUT * 2 {
                if let Ok(z) = zstd_compress(&inner, 19) {
                    let mut cand = Vec::with_capacity(z.len() + 1);
                    cand.push(1u8);
                    cand.extend_from_slice(&z);
                    if cand.len() < best.len() {
                        best = cand;
                    }
                }
            }
        }
        if best.len() > crate::MAX_PAYLOAD {
            return Err("payload too large".into());
        }
        Ok(best)
    }
    fn decode(enc: &[u8], _m: &[u8], olen: usize) -> Result<Vec<u8>, CodecError> {
        if olen > crate::MAX_INPUT {
            return Err("bad original_len".into());
        }
        if enc.is_empty() {
            return Err("empty frame".into());
        }
        let tag = enc[0];
        let body = &enc[1..];
        if tag == 0 {
            let d = zstd_decompress(body).map_err(|e| e.to_string())?;
            if d.len() != olen {
                return Err("length mismatch".into());
            }
            return Ok(d);
        }
        if tag != 1 {
            return Err("bad tag".into());
        }
        let inner = zstd_decompress(body).map_err(|e| e.to_string())?;
        if inner.len() < 3 || &inner[..3] != b"DD1" {
            return Err("bad magic".into());
        }
        let mut p = 3usize;
        let nd = ruv(&inner, &mut p).ok_or("bad nd")? as usize;
        let nc = ruv(&inner, &mut p).ok_or("bad nc")? as usize;
        if nc == 0 || nc > 4096 || nd > 100_000 {
            return Err("bad dims".into());
        }
        let nc64 = nc as u64;
        let nd64 = nd as u64;
        nc64.checked_mul(nd64).ok_or("bad area")?;
        if nd64.checked_mul(nc64).ok_or("bad area")? > 4_000_000 {
            return Err("bad area".into());
        }
        if p >= inner.len() {
            return Err("bad hdr".into());
        }
        let hdr = inner[p] == 1;
        p += 1;
        let mut hdrline: &[u8] = &[];
        if hdr {
            let hl = ruv(&inner, &mut p).ok_or("bad hl")? as usize;
            if hl > crate::MAX_INPUT || p.checked_add(hl).ok_or("oob")? > inner.len() {
                return Err("bad header".into());
            }
            hdrline = &inner[p..p + hl];
            p += hl;
        }
        if p + 2 > inner.len() {
            return Err("bad nl".into());
        }
        let code = inner[p];
        let lastnl = inner[p + 1] == 1;
        p += 2;
        if code > 1 {
            return Err("bad code".into());
        }
        let nl: &[u8] = if code == 1 { b"\r\n" } else { b"\n" };
        let mut cols: Vec<Vec<Vec<u8>>> = Vec::new();
        cols.resize_with(nc, Vec::new);
        for c in 0..nc {
            if p >= inner.len() {
                return Err("bad kind".into());
            }
            let k = inner[p];
            p += 1;
            if k == 2 {
                let s = ruv(&inner, &mut p).ok_or("bad dup")? as usize;
                if s >= c {
                    return Err("bad dup".into());
                }
                cols[c] = cols[s].clone();
            } else if k == 0 {
                for _ in 0..nd {
                    let l = ruv(&inner, &mut p).ok_or("bad raw")? as usize;
                    if l > 1_000_000 || p.checked_add(l).ok_or("oob")? > inner.len() {
                        return Err("bad raw".into());
                    }
                    cols[c].push(inner[p..p + l].to_vec());
                    p += l;
                }
            } else if k == 1 {
                if p + 3 > inner.len() {
                    return Err("bad delta hdr".into());
                }
                let cl = inner[p];
                let cq = inner[p + 1] == 1;
                let ctr = inner[p + 2];
                p += 3;
                let cs = unzig(ruv(&inner, &mut p).ok_or("bad scale")?).ok_or("bad scale")? as i32;
                if cs < 0 || cs > 76 {
                    return Err("bad scale".into());
                }
                let mut prev: Option<i128> = None;
                for _ in 0..nd {
                    if p >= inner.len() {
                        return Err("bad row".into());
                    }
                    let f = inner[p];
                    p += 1;
                    if f == 1 {
                        let l = ruv(&inner, &mut p).ok_or("bad exc")? as usize;
                        if l > 1_000_000 || p.checked_add(l).ok_or("oob")? > inner.len() {
                            return Err("bad exc".into());
                        }
                        cols[c].push(inner[p..p + l].to_vec());
                        p += l;
                    } else if f == 0 {
                        let u = ruv(&inner, &mut p).ok_or("bad d")?;
                        let mc = match prev {
                            None => unzig(u).ok_or("bad z")?,
                            Some(q) => q.checked_add(unzig(u).ok_or("bad z")?).ok_or("overflow")?,
                        };
                        prev = Some(mc);
                        let mut b = Vec::new();
                        fmt_can(mc, cs, cl, cq, ctr, &mut b).ok_or("fmt")?;
                        cols[c].push(b);
                    } else {
                        return Err("bad flag".into());
                    }
                }
            } else {
                return Err("bad kind".into());
            }
        }
        let mut out = Vec::new();
        if hdr {
            out.extend_from_slice(hdrline);
            out.extend_from_slice(nl);
        }
        for r in 0..nd {
            for c in 0..nc {
                if c > 0 {
                    out.push(b',');
                }
                out.extend_from_slice(&cols[c][r]);
                if out.len() > crate::MAX_INPUT {
                    return Err("too big".into());
                }
            }
            let last = !hdr && r + 1 == nd || hdr && r + 1 == nd;
            if !last || lastnl {
                out.extend_from_slice(nl);
            }
        }
        if out.len() != olen {
            return Err("length mismatch".into());
        }
        Ok(out)
    }
}
