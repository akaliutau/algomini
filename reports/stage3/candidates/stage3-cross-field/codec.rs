use crate::{Codec, CodecError};
// Cross-field telemetry codec: tests earlier-column prediction and exact
// duplicate columns within a block. Numeric path stores exact integer
// mantissas/scales plus lexical info (quotes/padding/sign/width) so the
// inverse retains spelling. Anything unsupported uses a tagged raw zstd
// fallback. Predictor choice is compared by actual zstd size.
// Limitations: quote-aware comma split only (no multiline quoted fields);
// strict canonical decimals only (no exponents/plus); per-column uniform
// quotes/padding/scale required or the column stays raw.

pub struct Candidate {
    pub tag: u8,
    pub payload: Vec<u8>,
}

impl Candidate {
    pub fn new(tag: u8, payload: Vec<u8>) -> Self {
        Self { tag, payload }
    }
}

struct Num {
    mant: i128,
    neg: bool,
    int_len: u32,
}

fn pow10(e: u32) -> Option<i128> {
    let mut v: i128 = 1;
    for _ in 0..e {
        v = v.checked_mul(10)?;
    }
    Some(v)
}

fn wvar(mut v: u128, o: &mut Vec<u8>) {
    loop {
        let b = (v & 0x7f) as u8;
        v >>= 7;
        if v == 0 {
            o.push(b | 0x80);
            break;
        } else {
            o.push(b);
        }
    }
}

fn rvar(d: &[u8], mut p: usize) -> Result<(u128, usize), CodecError> {
    let mut v: u128 = 0;
    let mut s = 0u32;
    loop {
        if p >= d.len() || s > 126 {
            return Err("varint".into());
        }
        let b = d[p];
        p += 1;
        v |= ((b & 0x7f) as u128)
            .checked_shl(s)
            .ok_or("varint shl".to_string())?;
        if b & 0x80 != 0 {
            break;
        }
        s += 7;
    }
    Ok((v, p))
}

fn zz_e(v: i128) -> u128 {
    ((v << 1) ^ (v >> 127)) as u128
}

fn zz_d(v: u128) -> i128 {
    ((v >> 1) as i128) ^ -((v & 1) as i128)
}

// Strict canonical decimal: optional quotes, spaces, '-', digits[.digits].
fn parse_num(t: &[u8]) -> Option<(i128, u32, u32, bool, bool, u32, u32)> {
    if t.is_empty() {
        return None;
    }
    let (q, inner) = if t.len() >= 2 && t[0] == b'"' && t[t.len() - 1] == b'"' {
        (true, &t[1..t.len() - 1])
    } else {
        (false, t)
    };
    let mut a = 0usize;
    while a < inner.len() && (inner[a] == b' ' || inner[a] == b'\t') {
        a += 1;
    }
    let mut b = inner.len();
    while b > a && (inner[b - 1] == b' ' || inner[b - 1] == b'\t') {
        b -= 1;
    }
    let (lead, trail) = (a as u32, (inner.len() - b) as u32);
    let c = &inner[a..b];
    if c.is_empty() {
        return None;
    }
    let mut i = 0usize;
    let mut neg = false;
    if c[0] == b'-' {
        neg = true;
        i = 1;
    }
    if i >= c.len() {
        return None;
    }
    for &x in &c[i..] {
        if !(x.is_ascii_digit() || x == b'.') {
            return None;
        }
    }
    let mut dot: Option<usize> = None;
    for (k, &x) in c[i..].iter().enumerate() {
        if x == b'.' {
            if dot.is_some() {
                return None;
            }
            dot = Some(k);
        }
    }
    let (ip, fp) = match dot {
        Some(k) => (&c[i..i + k], &c[i + k + 1..]),
        None => (&c[i..], &[][..]),
    };
    if ip.is_empty() || (dot.is_some() && fp.is_empty()) {
        return None;
    }
    if !ip.iter().all(|x| x.is_ascii_digit()) || !fp.iter().all(|x| x.is_ascii_digit()) {
        return None;
    }
    if ip.len() + fp.len() > 38 || fp.len() > 19 {
        return None;
    }
    // No inner quotes allowed (caller checks doubled quotes earlier).
    if ip.len() + fp.len() == 0 {
        return None;
    }
    let mut m: i128 = 0;
    for &x in ip.iter().chain(fp.iter()) {
        m = m.checked_mul(10)?.checked_add((x - b'0') as i128)?;
    }
    if neg {
        m = m.checked_neg()?;
    }
    // Reject doubled-quote leftovers.
    if inner.contains(&b'"') {
        return None;
    }
    Some((m, fp.len() as u32, ip.len() as u32, neg, q, lead, trail))
}

fn split_csv<'a>(line: &'a [u8]) -> Option<Vec<&'a [u8]>> {
    let mut f = Vec::new();
    let mut q = false;
    let mut s = 0usize;
    let mut i = 0usize;
    while i < line.len() {
        let c = line[i];
        if c == b'"' {
            if q && i + 1 < line.len() && line[i + 1] == b'"' {
                i += 2;
                continue;
            }
            q = !q;
        } else if c == b',' && !q {
            f.push(&line[s..i]);
            s = i + 1;
        }
        i += 1;
    }
    if q {
        return None;
    }
    f.push(&line[s..]);
    Some(f)
}

fn layout(inp: &[u8]) -> Option<(Vec<Vec<u8>>, Vec<Vec<u8>>, Vec<u8>, usize)> {
    if inp.is_empty() || inp.contains(&0) {
        return None;
    }
    // Lines + terminators (0=none,1=LF,2=CRLF).
    let mut lines: Vec<&[u8]> = Vec::new();
    let mut terms: Vec<u8> = Vec::new();
    let mut s = 0usize;
    for (i, &c) in inp.iter().enumerate() {
        if c == b'\n' {
            let mut e = i;
            let mut t = 1u8;
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
    } else if s == inp.len() && inp.last() == Some(&b'\n') {
        // trailing newline already recorded; nothing more.
    } else if lines.is_empty() {
        lines.push(inp);
        terms.push(0);
    }
    if lines.len() < 2 {
        return None;
    }
    let hdr = split_csv(lines[0])?;
    let nc = hdr.len();
    if nc < 2 || nc > 64 {
        return None;
    }
    let mut cols: Vec<Vec<u8>> = vec![Vec::new(); nc];
    let mut lens: Vec<Vec<usize>> = vec![Vec::new(); nc];
    let nr = lines.len() - 1;
    if nr == 0 || nr > 20000 {
        return None;
    }
    nc.checked_mul(nr)?;
    for l in lines.iter().skip(1) {
        let f = split_csv(l)?;
        if f.len() != nc {
            return None;
        }
        for (j, x) in f.iter().enumerate() {
            lens[j].push(cols[j].len());
            cols[j].extend_from_slice(x);
        }
    }
    // Reject JSON-ish layouts.
    for h in hdr.iter() {
        if h.contains(&b'{') || h.contains(&b'}') || h.contains(&b'[') || h.contains(&b']') {
            return None;
        }
    }
    let hraw: Vec<Vec<u8>> = hdr.iter().map(|x| x.to_vec()).collect();
    // Build per-column cell slices later from cols/lens; stash header aside.
    // Pack: cols concatenated + row boundaries; return via closure data below.
    // To keep signature small, re-slice in encode using same parse.
    let _ = lens;
    // Store column bytes + header + terms; row split done by caller lengths.
    // We return cols concat plus header; row lengths reconstructed from lines.
    let mut out = Vec::new();
    out.extend(hraw.len().to_be_bytes());
    Ok((hraw, cols, terms, nc))
}

pub struct CandidateCodec;
impl Codec for Candidate {
    fn train(_s: &[&[u8]]) -> Result<Vec<u8>, CodecError> {
        Ok(Vec::new())
    }
    fn encode(input: &[u8], _m: &[u8]) -> Result<Vec<u8>, CodecError> {
        if input.len() > crate::MAX_INPUT {
            return Err("input too large".into());
        }
        let rawz = crate::toolkit::zstd_compress(input, 19)?;
        let mut best = vec![0x00u8];
        best.extend(rawz);
        let attempt = build_xf(input);
        if let Some(pre) = attempt {
            let z = crate::toolkit::zstd_compress(&pre, 19)?;
            let mut cand = vec![0x01u8];
            cand.extend(z);
            if cand.len() < best.len() {
                best = cand;
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
        match enc[0] {
            0x00 => {
                let d = crate::toolkit::zstd_decompress(&enc[1..])?;
                if d.len() != orig {
                    return Err("length mismatch".into());
                }
                Ok(d)
            }
            0x01 => {
                let d = crate::toolkit::zstd_decompress(&enc[1..])?;
                let out = decode_xf(&d, orig)?;
                if out.len() != orig {
                    return Err("length mismatch".into());
                }
                Ok(out)
            }
            _ => Err("bad tag".into()),
        }
    }
}

fn build_xf(input: &[u8]) -> Option<Vec<u8>> {
    let (hraw, cols, terms, nc) = layout(input)?;
    // Re-derive rows: re-split lines to get per-cell raw bytes.
    let mut lines: Vec<&[u8]> = Vec::new();
    let mut s = 0usize;
    for (i, &c) in input.iter().enumerate() {
        if c == b'\n' {
            let mut e = i;
            if e > s && input[e - 1] == b'\r' {
                e -= 1;
            }
            if s == 0 {
                // header line already have
            }
            lines.push(&input[s..e]);
            s = i + 1;
        }
    }
    if s < input.len() {
        lines.push(&input[s..]);
    }
    let nr = lines.len() - 1;
    let mut cells: Vec<Vec<&[u8]>> = vec![Vec::with_capacity(nr); nc];
    for l in lines.iter().skip(1) {
        let f = split_csv(l)?;
        for (j, x) in f.iter().enumerate() {
            cells[j].push(*x);
        }
    }
    // DUP detection on raw bytes (header excluded).
    let mut dup: Vec<Option<usize>> = vec![None; nc];
    for j in 0..nc {
        for k in 0..j {
            if dup[k].is_none() || dup[j].is_some() {
                // compare data cells only
            }
            let mut eq = true;
            for r in 0..nr {
                if cells[j][r] != cells[k][r] {
                    eq = false;
                    break;
                }
            }
            if eq {
                dup[j] = Some(k);
                break;
            }
        }
    }
    // Numeric classification.
    struct NC {
        scale: u32,
        q: bool,
        lead: u32,
        trail: u32,
        m: Vec<i128>,
        neg: Vec<bool>,
        ilen: Vec<u32>,
    }
    let mut nums: Vec<Option<NC>> = Vec::new();
    for j in 0..nc {
        if dup[j].is_some() {
            nums.push(None);
            continue;
        }
        let mut vs = Vec::with_capacity(nr);
        let mut ng = Vec::with_capacity(nr);
        let mut il = Vec::with_capacity(nr);
        let mut sc: Option<u32> = None;
        let mut qq: Option<bool> = None;
        let mut ld: Option<u32> = None;
        let mut tr: Option<u32> = None;
        let mut ok = true;
        for r in 0..nr {
            match parse_num(cells[j][r]) {
                Some((m, s2, il2, n2, q2, l2, t2)) => {
                    if (sc.is_some() && sc != Some(s2))
                        || (qq.is_some() && qq != Some(q2))
                        || (ld.is_some() && ld != Some(l2))
                        || (tr.is_some() && tr != Some(t2))
                    {
                        ok = false;
                        break;
                    }
                    sc = Some(s2);
                    qq = Some(q2);
                    ld = Some(l2);
                    tr = Some(t2);
                    vs.push(m);
                    ng.push(n2);
                    il.push(il2);
                }
                None => {
                    ok = false;
                    break;
                }
            }
        }
        if ok {
            nums.push(Some(NC {
                scale: sc.unwrap_or(0),
                q: qq.unwrap_or(false),
                lead: ld.unwrap_or(0),
                trail: tr.unwrap_or(0),
                m: vs,
                neg: ng,
                ilen: il,
            }));
        } else {
            nums.push(None);
        }
    }
    // Per-column predictor: 0 raw, 1 row, 2 cross, 3 dup.
    let mut kind = vec![0u8; nc];
    let mut cref: Vec<usize> = vec![0; nc];
    for j in 0..nc {
        if let Some(k) = dup[j] {
            kind[j] = 3;
            cref[j] = k;
        } else if nums[j].is_some() {
            kind[j] = 1;
        }
    }
    // Compare row vs cross by actual zstd size per column.
    for j in 0..nc {
        let ncj = match nums[j].as_ref() {
            Some(v) => v,
            None => continue,
        };
        // row stream
        let mut row = Vec::new();
        let mut prev = 0i128;
        for (r, &m) in ncj.m.iter().enumerate() {
            let d = if r == 0 { m } else { m.checked_sub(prev)? };
            prev = m;
            wvar(zz_e(d), &mut row);
        }
        // find best earlier same-scale numeric col
        let mut bk: Option<usize> = None;
        let mut best_est = u128::MAX;
        for k in 0..j {
            if let Some(o) = nums[k].as_ref() {
                if o.scale != ncj.scale || kind[k] == 3 {
                    continue;
                }
                let mut est: u128 = 0;
                for r in 0..nr {
                    let d = ncj.m[r].checked_sub(o.m[r])?;
                    // varint length estimate
                    let mut v = zz_e(d);
                    let mut n = 0u128;
                    loop {
                        n += 1;
                        v >>= 7;
                        if v == 0 {
                            break;
                        }
                    }
                    est += n;
                    if est >= best_est {
                        break;
                    }
                }
                if est < best_est {
                    best_est = est;
                    bk = Some(k);
                }
            }
        }
        if let Some(k) = bk {
            let o = nums[k].as_ref().unwrap();
            let mut cross = Vec::new();
            for r in 0..nr {
                wvar(zz_e(ncj.m[r].checked_sub(o.m[r])?), &mut cross);
            }
            // lexical part shared; compare data streams by actual zstd size
            let zr = crate::toolkit::zstd_compress(&row, 19).ok();
            let zc = crate::toolkit::zstd_compress(&cross, 19).ok();
            match (zr, zc) {
                (Some(a), Some(b)) => {
                    if b.len() < a.len() {
                        kind[j] = 2;
                        cref[j] = k;
                    }
                }
                _ => {}
            }
        }
    }
    // Assemble pre-zstd payload.
    let mut pre: Vec<u8> = Vec::new();
    pre.extend(b"XF01");
    wvar(nc as u128, &mut pre);
    wvar(nr as u128, &mut pre);
    wvar(hraw.len() as u128, &mut pre);
    for h in hraw.iter() {
        wvar(h.len() as u128, &mut pre);
        pre.extend(h);
    }
    if terms.len() != nr + 1 {
        return None;
    }
    for &t in terms.iter() {
        pre.push(t);
    }
    let _ = &cols;
    for j in 0..nc {
        pre.push(kind[j]);
        match kind[j] {
            3 => {
                wvar(cref[j] as u128, &mut pre);
            }
            0 => {
                for r in 0..nr {
                    wvar(cells[j][r].len() as u128, &mut pre);
                    pre.extend(cells[j][r]);
                }
            }
            1 | 2 => {
                let n = nums[j].as_ref()?;
                wvar(n.scale as u128, &mut pre);
                pre.push((n.q as u8) | ((n.lead.min(15) as u8) << 1) | ((n.trail.min(15) as u8) << 5));
                wvar(n.lead as u128, &mut pre);
                wvar(n.trail as u128, &mut pre);
                if kind[j] == 2 {
                    wvar(cref[j] as u128, &mut pre);
                }
                for r in 0..nr {
                    if n.ilen[r] >= 128 {
                        return None;
                    }
                    pre.push((n.ilen[r] as u8) | ((n.neg[r] as u8) << 7));
                }
                // residuals
                if kind[j] == 1 {
                    let mut prev = 0i128;
                    for (r, &m) in n.m.iter().enumerate() {
                        let d = if r == 0 { m } else { m.checked_sub(prev)? };
                        prev = m;
                        wvar(zz_e(d), &mut pre);
                    }
                } else {
                    let o = nums[cref[j]].as_ref()?;
                    if o.scale != n.scale {
                        return None;
                    }
                    for r in 0..nr {
                        wvar(zz_e(n.m[r].checked_sub(o.m[r])?), &mut pre);
                    }
                }
            }
            _ => return None,
        }
    }
    Some(pre)
}

fn decode_xf(d: &[u8], _orig: usize) -> Result<Vec<u8>, CodecError> {
    if d.len() < 4 || &d[0..4] != b"XF01" {
        return Err("magic".into());
    }
    let mut p = 4usize;
    let (nc, p1) = rvar(d, p)?;
    p = p1;
    let (nr, p1) = rvar(d, p)?;
    p = p1;
    let (nh, p1) = rvar(d, p)?;
    p = p1;
    if nc > 64 || nc < 2 || nr > 20000 || nr == 0 || nh != nc {
        return Err("dims".into());
    }
    let nc = nc as usize;
    let nr = nr as usize;
    let mut hdr: Vec<Vec<u8>> = Vec::with_capacity(nc);
    for _ in 0..nc {
        let (l, p1) = rvar(d, p)?;
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
    // decode in order (cross refs earlier cols only)
    let mut kinds = Vec::new();
    for j in 0..nc {
        if p >= d.len() {
            return Err("kind oob".into());
        }
        let k = d[p];
        p += 1;
        kinds.push(k);
        match k {
            3 => {
                let (r2, p1) = rvar(d, p)?;
                p = p1;
                if r2 >= j as u128 {
                    return Err("dup ref".into());
                }
                let s = r2 as usize;
                if cols[s].len() != nr {
                    return Err("dup order".into());
                }
                cols[j] = cols[s].clone();
            }
            0 => {
                for _ in 0..nr {
                    let (l, p1) = rvar(d, p)?;
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
                let (sc, p1) = rvar(d, p)?;
                p = p1;
                if p >= d.len() {
                    return Err("lex oob".into());
                }
                let fb = d[p];
                p += 1;
                let (ld, p1) = rvar(d, p)?;
                p = p1;
                let (tr, p1) = rvar(d, p)?;
                p = p1;
                let q = fb & 1 != 0;
                if sc > 19 || ld > 64 || tr > 64 {
                    return Err("lex".into());
                }
                let sc = sc as u32;
                let xr: Option<usize> = if k == 2 {
                    let (r2, p1) = rvar(d, p)?;
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
                let mut il = Vec::with_capacity(nr);
                let mut ng = Vec::with_capacity(nr);
                for r in 0..nr {
                    let b = d[p + r];
                    ng.push(b >> 7 != 0);
                    il.push((b & 0x7f) as u32);
                }
                p += nr;
                let mut diffs = Vec::with_capacity(nr);
                for _ in 0..nr {
                    let (v, p1) = rvar(d, p)?;
                    p = p1;
                    diffs.push(zz_d(v));
                }
                // resolve mantissas
                let mut m = vec![0i128; nr];
                if k == 1 {
                    let mut acc = 0i128;
                    for r in 0..nr {
                        if r == 0 {
                            acc = diffs[0];
                        } else {
                            acc = acc.checked_add(diffs[r]).ok_or("add".to_string())?;
                        }
                        m[r] = acc;
                    }
                } else {
                    let xr = xr.unwrap();
                    if cols[xr].is_empty() {
                        return Err("xref empty".into());
                    }
                    // ref col must be numeric; re-parse its decoded raw cells
                    let mut rm = Vec::with_capacity(nr);
                    for r in 0..nr {
                        let (_, s2, _, _, _, _, _) = parse_num(&cols[xr][r]).ok_or("xref num".to_string())?;
                        // ref scale must match; parse mantissa
                        let (mv, _, _, _, _, _, _) = parse_num(&cols[xr][r]).ok_or("xref num2".to_string())?;
                        let _ = s2;
                        rm.push(mv);
                    }
                    for r in 0..nr {
                        m[r] = rm[r].checked_add(diffs[r]).ok_or("add".to_string())?;
                    }
                    // verify scale equality
                    let (_, rs, _, _, _, _, _) = parse_num(&cols[xr][0]).ok_or("xscale".to_string())?;
                    if rs != sc {
                        return Err("scale".into());
                    }
                }
                let pw = pow10(sc).ok_or("pow".to_string())?;
                for r in 0..nr {
                    let mag = if !ng[r] { m[r] } else { m[r].checked_neg().ok_or("neg".to_string())? };
                    // mag when negated for -0: m[r] is 0
                    let a: i128 = if m[r] < 0 { m[r].checked_neg().ok_or("abs".to_string())? } else { m[r] };
                    let _ = mag;
                    let ipw = pow10(sc).ok_or("pow".to_string())?;
                    let ipart = a.checked_div(ipw).ok_or("div".to_string())?;
                    let fpart = a.checked_rem(ipw).ok_or("rem".to_string())?;
                    let mut istr = ipart.to_string();
                    // preserve leading zeros to ilen
                    if (istr.len() as u32) < il[r] {
                        let pad = (il[r] as usize) - istr.len();
                        let mut s2 = String::with_capacity(il[r] as usize);
                        for _ in 0..pad {
                            s2.push('0');
                        }
                        s2.push_str(&istr);
                        istr = s2;
                    } else if (istr.len() as u32) > il[r] {
                        return Err("width".into());
                    }
                    let mut core = String::new();
                    if ng[r] {
                        core.push('-');
                    }
                    core.push_str(&istr);
                    if sc > 0 {
                        let mut fs = fpart.to_string();
                        if (fs.len() as u32) < sc {
                            let pad = (sc as usize) - fs.len();
                            let mut s2 = String::with_capacity(sc as usize);
                            for _ in 0..pad {
                                s2.push('0');
                            }
                            s2.push_str(&fs);
                            fs = s2;
                        }
                        core.push('.');
                        core.push_str(&fs);
                    }
                    let mut raw = Vec::new();
                    if q {
                        raw.push(b'"');
                    }
                    for _ in 0..ld {
                        raw.push(b' ');
                    }
                    raw.extend(core.as_bytes());
                    for _ in 0..tr {
                        raw.push(b' ');
                    }
                    if q {
                        raw.push(b'"');
                    }
                    // validate round-trip through parser
                    let chk = parse_num(&raw).ok_or("reparse".to_string())?;
                    if chk.0 != m[r] {
                        return Err("reparse val".into());
                    }
                    let _ = pw;
                    cols[j].push(raw);
                }
            }
            _ => return Err("kind".into()),
        }
    }
    // Rejoin.
    let mut out = Vec::new();
    for j in 0..nc {
        if j > 0 {
            out.push(b',');
        }
        out.extend(&hdr[j]);
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
