use crate::{Codec, CodecError};
// Cross-field telemetry codec: tests earlier-column prediction and exact
// duplicate data columns within one block. Numeric path stores exact integer
// mantissas/scales plus lexical info (quotes/padding/sign/int-width) so the
// inverse retains spelling. Unsupported layouts/columns use a tagged raw zstd
// fallback. Row-delta vs cross-field predictors are compared by actual zstd
// size per column. Limitations: quote-aware comma split only, no multiline
// quoted fields; strict decimals only (no exponents/plus/tabs); per-column
// uniform scale/quotes/padding required or the column stays raw.
pub struct Candidate;
impl Candidate {
    pub fn new(_t: u8, _p: Vec<u8>) -> Self {
        Self
    }
}
fn wv(mut v: u128, o: &mut Vec<u8>) {
    loop {
        let b = (v & 0x7f) as u8;
        v >>= 7;
        if v == 0 {
            o.push(b | 0x80);
            break;
        }
        o.push(b);
    }
}
fn rv(d: &[u8], mut p: usize) -> Result<(u128, usize), CodecError> {
    let (mut v, mut s) = (0u128, 0u32);
    loop {
        if p >= d.len() || s > 126 {
            return Err("varint".into());
        }
        let b = d[p];
        p += 1;
        v |= ((b & 0x7f) as u128).checked_shl(s).ok_or("shl".to_string())?;
        if b & 0x80 != 0 {
            break;
        }
        s += 7;
    }
    Ok((v, p))
}
fn ze(v: i128) -> u128 {
    ((v << 1) ^ (v >> 127)) as u128
}
fn zd(v: u128) -> i128 {
    ((v >> 1) as i128) ^ -((v & 1) as i128)
}
fn pw(e: u32) -> Option<i128> {
    let mut v = 1i128;
    for _ in 0..e {
        v = v.checked_mul(10)?;
    }
    Some(v)
}
// Strict decimal: ["], spaces, [-], digits [. digits], spaces, ["].
fn pn(t: &[u8]) -> Option<(i128, u32, u32, bool, bool, u32, u32)> {
    if t.is_empty() {
        return None;
    }
    let (q, inn) = if t.len() >= 2 && t[0] == b'"' && t[t.len() - 1] == b'"' {
        (true, &t[1..t.len() - 1])
    } else {
        (false, t)
    };
    if inn.contains(&b'"') {
        return None;
    }
    let (mut a, mut b) = (0usize, inn.len());
    while a < inn.len() && inn[a] == b' ' {
        a += 1;
    }
    while b > a && inn[b - 1] == b' ' {
        b -= 1;
    }
    let (ld, tr) = (a as u32, (inn.len() - b) as u32);
    let c = &inn[a..b];
    if c.is_empty() {
        return None;
    }
    let (mut i, mut neg) = (0usize, false);
    if c[0] == b'-' {
        neg = true;
        i = 1;
    }
    if i >= c.len() {
        return None;
    }
    let mut dots = 0;
    for &x in &c[i..] {
        if x == b'.' {
            dots += 1;
        } else if !x.is_ascii_digit() {
            return None;
        }
    }
    if dots > 1 {
        return None;
    }
    let (ip, fp) = match c[i..].iter().position(|&x| x == b'.') {
        Some(k) => (&c[i..i + k], &c[i + k + 1..]),
        None => (&c[i..], &[][..]),
    };
    if ip.is_empty() || ip.len() + fp.len() > 38 || fp.len() > 19 {
        return None;
    }
    if c[i..].iter().any(|&x| x == b'.') && fp.is_empty() {
        return None;
    }
    let mut m = 0i128;
    for &x in ip.iter().chain(fp.iter()) {
        m = m.checked_mul(10)?.checked_add((x - b'0') as i128)?;
    }
    if neg {
        m = m.checked_neg()?;
    }
    Some((m, fp.len() as u32, ip.len() as u32, neg, q, ld, tr))
}
fn spl(l: &[u8]) -> Option<Vec<&[u8]>> {
    let (mut f, mut q, mut s, mut i) = (Vec::new(), false, 0usize, 0usize);
    while i < l.len() {
        if l[i] == b'"' {
            if q && i + 1 < l.len() && l[i + 1] == b'"' {
                i += 2;
                continue;
            }
            q = !q;
        } else if l[i] == b',' && !q {
            f.push(&l[s..i]);
            s = i + 1;
        }
        i += 1;
    }
    if q {
        return None;
    }
    f.push(&l[s..]);
    Some(f)
}
struct NC {
    sc: u32,
    q: bool,
    ld: u32,
    tr: u32,
    m: Vec<i128>,
    ng: Vec<bool>,
    il: Vec<u32>,
}
fn build(inp: &[u8]) -> Option<Vec<u8>> {
    if inp.is_empty() || inp.contains(&0) {
        return None;
    }
    let mut lines: Vec<&[u8]> = Vec::new();
    let mut terms: Vec<u8> = Vec::new();
    let mut s = 0usize;
    for (i, &c) in inp.iter().enumerate() {
        if c == b'\n' {
            let (mut e, mut t) = (i, 1u8);
            if e > s && inp[e - 1] == b'\r' {
                e -= 1;
                t = 2;
            }
            lines.push(&inp[s..e]);
            terms.push(t);
            s = i + 1;
        }
    }
    if s < inp.len() {
        lines.push(&inp[s..]);
        terms.push(0);
    }
    if lines.len() < 2 {
        return None;
    }
    let hdr = spl(lines[0])?;
    let nc = hdr.len();
    if nc < 2 || nc > 64 {
        return None;
    }
    for h in hdr.iter() {
        if h.iter().any(|&c| c == b'{' || c == b'}' || c == b'[' || c == b']') {
            return None;
        }
    }
    let nr = lines.len() - 1;
    if nr == 0 || nr > 20000 {
        return None;
    }
    nc.checked_mul(nr)?;
    let mut cells: Vec<Vec<&[u8]>> = vec![Vec::with_capacity(nr); nc];
    for l in lines.iter().skip(1) {
        let f = spl(l)?;
        if f.len() != nc {
            return None;
        }
        for (j, x) in f.iter().enumerate() {
            if x.len() > 4_000_000 {
                return None;
            }
            cells[j].push(*x);
        }
    }
    let mut dup: Vec<Option<usize>> = vec![None; nc];
    for j in 0..nc {
        for k in 0..j {
            if cells[j].iter().zip(cells[k].iter()).all(|(a, b)| a == b) {
                dup[j] = Some(k);
                break;
            }
        }
    }
    let mut nums: Vec<Option<NC>> = Vec::with_capacity(nc);
    for j in 0..nc {
        if dup[j].is_some() {
            nums.push(None);
            continue;
        }
        let (mut vs, mut ng, mut il) = (Vec::with_capacity(nr), Vec::with_capacity(nr), Vec::with_capacity(nr));
        let (mut sc, mut qq, mut ld, mut tr, mut ok) = (0u32, false, 0u32, 0u32, true);
        let mut first = true;
        for r in 0..nr {
            match pn(cells[j][r]) {
                Some((m, s2, i2, n2, q2, l2, t2)) => {
                    if !first && (s2 != sc || q2 != qq || l2 != ld || t2 != tr) {
                        ok = false;
                        break;
                    }
                    first = false;
                    sc = s2;
                    qq = q2;
                    ld = l2;
                    tr = t2;
                    if i2 >= 128 {
                        ok = false;
                        break;
                    }
                    vs.push(m);
                    ng.push(n2);
                    il.push(i2);
                }
                None => {
                    ok = false;
                    break;
                }
            }
        }
        if ok {
            nums.push(Some(NC { sc, q: qq, ld, tr, m: vs, ng, il }));
        } else {
            nums.push(None);
        }
    }
    let mut kind = vec![0u8; nc];
    let mut cref = vec![0usize; nc];
    for j in 0..nc {
        if let Some(k) = dup[j] {
            kind[j] = 3;
            cref[j] = k;
        } else if nums[j].is_some() {
            kind[j] = 1;
        }
    }
    for j in 0..nc {
        let nj = match nums[j].as_ref() {
            Some(v) => v,
            None => continue,
        };
        let mut row = Vec::new();
        let mut pv = 0i128;
        for (r, &m) in nj.m.iter().enumerate() {
            let d = if r == 0 { m } else { m.checked_sub(pv)? };
            pv = m;
            wv(ze(d), &mut row);
        }
        let (mut bk, mut be) = (None, u128::MAX);
        for k in 0..j {
            let o = match nums[k].as_ref() {
                Some(v) if v.sc == nj.sc && kind[k] != 3 => v,
                _ => continue,
            };
            let mut e = 0u128;
            for r in 0..nr {
                let mut v = ze(nj.m[r].checked_sub(o.m[r])?);
                loop {
                    e += 1;
                    v >>= 7;
                    if v == 0 {
                        break;
                    }
                }
                if e >= be {
                    break;
                }
            }
            if e < be {
                be = e;
                bk = Some(k);
            }
        }
        if let Some(k) = bk {
            let o = nums[k].as_ref().unwrap();
            let mut cr = Vec::new();
            for r in 0..nr {
                wv(ze(nj.m[r].checked_sub(o.m[r])?), &mut cr);
            }
            if let (Ok(a), Ok(b)) = (crate::toolkit::zstd_compress(&row, 19), crate::toolkit::zstd_compress(&cr, 19)) {
                if b.len() < a.len() {
                    kind[j] = 2;
                    cref[j] = k;
                }
            }
        }
    }
    let mut pre = Vec::new();
    pre.extend(b"XF01");
    wv(nc as u128, &mut pre);
    wv(nr as u128, &mut pre);
    for h in hdr.iter() {
        if h.len() > 1_000_000 {
            return None;
        }
        wv(h.len() as u128, &mut pre);
        pre.extend(h);
    }
    if terms.len() != nr + 1 {
        return None;
    }
    pre.extend(terms.iter());
    for j in 0..nc {
        pre.push(kind[j]);
        match kind[j] {
            3 => wv(cref[j] as u128, &mut pre),
            0 => {
                for r in 0..nr {
                    wv(cells[j][r].len() as u128, &mut pre);
                    pre.extend(cells[j][r]);
                }
            }
            1 | 2 => {
                let n = nums[j].as_ref()?;
                wv(n.sc as u128, &mut pre);
                pre.push((n.q as u8) | ((n.ld.min(15) as u8) << 1) | ((n.tr.min(15) as u8) << 5));
                wv(n.ld as u128, &mut pre);
                wv(n.tr as u128, &mut pre);
                if kind[j] == 2 {
                    wv(cref[j] as u128, &mut pre);
                }
                for r in 0..nr {
                    pre.push((n.il[r] as u8) | ((n.ng[r] as u8) << 7));
                }
                if kind[j] == 1 {
                    let mut pv = 0i128;
                    for (r, &m) in n.m.iter().enumerate() {
                        let d = if r == 0 { m } else { m.checked_sub(pv)? };
                        pv = m;
                        wv(ze(d), &mut pre);
                    }
                } else {
                    let o = nums[cref[j]].as_ref()?;
                    if o.sc != n.sc {
                        return None;
                    }
                    for r in 0..nr {
                        wv(ze(n.m[r].checked_sub(o.m[r])?), &mut pre);
                    }
                }
            }
            _ => return None,
        }
    }
    Some(pre)
}
fn unbuild(d: &[u8]) -> Result<Vec<u8>, CodecError> {
    if d.len() < 4 || &d[0..4] != b"XF01" {
        return Err("magic".into());
    }
    let mut p = 4usize;
    let (nc_, p1) = rv(d, p)?;
    p = p1;
    let (nr_, p1) = rv(d, p)?;
    p = p1;
    if nc_ < 2 || nc_ > 64 || nr_ == 0 || nr_ > 20000 {
        return Err("dims".into());
    }
    let (nc, nr) = (nc_ as usize, nr_ as usize);
    nc.checked_mul(nr).ok_or("dims mul".to_string())?;
    let mut hdr = Vec::with_capacity(nc);
    for _ in 0..nc {
        let (l, p1) = rv(d, p)?;
        p = p1;
        if l > 1_000_000 {
            return Err("hdr len".into());
        }
        let l = l as usize;
        if p.checked_add(l).ok_or("oob".to_string())? > d.len() {
            return Err("hdr oob".into());
        }
        hdr.push(d[p..p + l].to_vec());
        p += l;
    }
    if p.checked_add(nr + 1).ok_or("oob".to_string())? > d.len() {
        return Err("terms oob".into());
    }
    let terms = d[p..p + nr + 1].to_vec();
    p += nr + 1;
    for &t in terms.iter() {
        if t > 2 {
            return Err("term".into());
        }
    }
    let mut cols: Vec<Vec<Vec<u8>>> = vec![Vec::new(); nc];
    for j in 0..nc {
        if p >= d.len() {
            return Err("kind oob".into());
        }
        let k = d[p];
        p += 1;
        match k {
            3 => {
                let (r2, p1) = rv(d, p)?;
                p = p1;
                if r2 >= j as u128 || cols[r2 as usize].len() != nr {
                    return Err("dup ref".into());
                }
                cols[j] = cols[r2 as usize].clone();
            }
            0 => {
                for _ in 0..nr {
                    let (l, p1) = rv(d, p)?;
                    p = p1;
                    if l > 4_000_000 {
                        return Err("cell len".into());
                    }
                    let l = l as usize;
                    if p.checked_add(l).ok_or("oob".to_string())? > d.len() {
                        return Err("cell oob".into());
                    }
                    cols[j].push(d[p..p + l].to_vec());
                    p += l;
                }
            }
            1 | 2 => {
                let (sc_, p1) = rv(d, p)?;
                p = p1;
                if p >= d.len() {
                    return Err("lex oob".into());
                }
                let fb = d[p];
                p += 1;
                let (ld_, p1) = rv(d, p)?;
                p = p1;
                let (tr_, p1) = rv(d, p)?;
                p = p1;
                if sc_ > 19 || ld_ > 64 || tr_ > 64 {
                    return Err("lex".into());
                }
                let (sc, q) = (sc_ as u32, fb & 1 != 0);
                let xr = if k == 2 {
                    let (r2, p1) = rv(d, p)?;
                    p = p1;
                    if r2 >= j as u128 {
                        return Err("xref".into());
                    }
                    Some(r2 as usize)
                } else {
                    None
                };
                if p.checked_add(nr).ok_or("oob".to_string())? > d.len() {
                    return Err("ilex oob".into());
                }
                let (mut il, mut ng) = (Vec::with_capacity(nr), Vec::with_capacity(nr));
                for r in 0..nr {
                    ng.push(d[p + r] >> 7 != 0);
                    il.push((d[p + r] & 0x7f) as u32);
                }
                p += nr;
                let mut df = Vec::with_capacity(nr);
                for _ in 0..nr {
                    let (v, p1) = rv(d, p)?;
                    p = p1;
                    df.push(zd(v));
                }
                let mut m = vec![0i128; nr];
                if k == 1 {
                    let mut a = 0i128;
                    for r in 0..nr {
                        a = if r == 0 { df[0] } else { a.checked_add(df[r]).ok_or("add".to_string())? };
                        m[r] = a;
                    }
                } else {
                    let x = xr.unwrap();
                    if cols[x].len() != nr {
                        return Err("xref order".into());
                    }
                    for r in 0..nr {
                        let (rmv, rs, _, _, _, _, _) = pn(&cols[x][r]).ok_or("xref num".to_string())?;
                        if rs != sc {
                            return Err("scale".into());
                        }
                        m[r] = rmv.checked_add(df[r]).ok_or("add".to_string())?;
                    }
                }
                let ipw = pw(sc).ok_or("pow".to_string())?;
                for r in 0..nr {
                    let a = if m[r] < 0 { m[r].checked_neg().ok_or("abs".to_string())? } else { m[r] };
                    let ip = a.checked_div(ipw).ok_or("div".to_string())?;
                    let fp = a.checked_rem(ipw).ok_or("rem".to_string())?;
                    let mut s0 = ip.to_string();
                    if (s0.len() as u32) < il[r] {
                        let pad = il[r] as usize - s0.len();
                        let mut t2 = String::with_capacity(il[r] as usize);
                        for _ in 0..pad {
                            t2.push('0');
                        }
                        t2.push_str(&s0);
                        s0 = t2;
                    } else if (s0.len() as u32) > il[r] {
                        return Err("width".into());
                    }
                    let mut core = String::new();
                    if ng[r] {
                        core.push('-');
                    }
                    core.push_str(&s0);
                    if sc > 0 {
                        let mut fs = fp.to_string();
                        if (fs.len() as u32) < sc {
                            let pad = sc as usize - fs.len();
                            let mut t2 = String::with_capacity(sc as usize);
                            for _ in 0..pad {
                                t2.push('0');
                            }
                            t2.push_str(&fs);
                            fs = t2;
                        }
                        core.push('.');
                        core.push_str(&fs);
                    }
                    let mut raw = Vec::new();
                    if q {
                        raw.push(b'"');
                    }
                    for _ in 0..ld_ {
                        raw.push(b' ');
                    }
                    raw.extend(core.as_bytes());
                    for _ in 0..tr_ {
                        raw.push(b' ');
                    }
                    if q {
                        raw.push(b'"');
                    }
                    if pn(&raw).ok_or("reparse".to_string())?.0 != m[r] {
                        return Err("reparse val".into());
                    }
                    cols[j].push(raw);
                }
            }
            _ => return Err("kind".into()),
        }
    }
    let mut out = Vec::new();
    for (j, h) in hdr.iter().enumerate() {
        if j > 0 {
            out.push(b',');
        }
        out.extend(h);
    }
    match terms[0] {
        1 => out.push(b'\n'),
        2 => out.extend(b"\r\n"),
        _ => {}
    }
    for r in 0..nr {
        for j in 0..nc {
            if j > 0 {
                out.push(b',');
            }
            out.extend(&cols[j][r]);
        }
        match terms[r + 1] {
            1 => out.push(b'\n'),
            2 => out.extend(b"\r\n"),
            _ => {}
        }
    }
    Ok(out)
}
impl Codec for Candidate {
    fn train(_s: &[&[u8]]) -> Result<Vec<u8>, CodecError> {
        Ok(Vec::new())
    }
    fn encode(input: &[u8], _m: &[u8]) -> Result<Vec<u8>, CodecError> {
        if input.len() > crate::MAX_INPUT {
            return Err("input too large".into());
        }
        let rz = crate::toolkit::zstd_compress(input, 19)?;
        let mut best = Vec::with_capacity(rz.len() + 1);
        best.push(0x00);
        best.extend(rz);
        if let Some(pre) = build(input) {
            let z = crate::toolkit::zstd_compress(&pre, 19)?;
            let mut c = Vec::with_capacity(z.len() + 1);
            c.push(0x01);
            c.extend(z);
            if c.len() < best.len() {
                best = c;
            }
        }
        if best.len() > crate::MAX_PAYLOAD {
            return Err("output too large".into());
        }
        Ok(best)
    }
    fn decode(enc: &[u8], _m: &[u8], orig: usize) -> Result<Vec<u8>, CodecError> {
        if enc.is_empty() {
            return Err("empty frame".into());
        }
        let d = match enc[0] {
            0x00 => crate::toolkit::zstd_decompress(&enc[1..])?,
            0x01 => {
                let p = crate::toolkit::zstd_decompress(&enc[1..])?;
                unbuild(&p)?
            }
            _ => return Err("bad tag".into()),
        };
        if d.len() != orig {
            return Err("length mismatch".into());
        }
        Ok(d)
    }
}
