"""Oracle INDÉPENDANT du résolveur d'ancres (A1-3) : balayage exhaustif image par image, en fractions exactes.

Il n'importe rien de `kolnoa_editor.anchors` : ni index, ni arrondi entier, ni tolérance de jonction du résolveur. Il ne connaît que
la définition : l'image f d'un plan montre la source [lo + f/fps, lo + (f+1)/fps) (en µs, fractions exactes). Une ancre (µs) tombe
dans l'image si elle est dans l'intervalle décalé de eps µs (l'ancre est elle-même arrondie à la µs : ±½ µs).
Règle éditoriale modélisée à part : à une coupe (deux plans contigus sur la piste et dans la source), l'instant appartient au plan de droite."""

from fractions import Fraction as F

THRESH_US = 1000   # en dessous : arrondi ; une image fait ≥ 1000 µs jusqu'à 1000 i/s


def frames_of(tl, clips, lo_of, to_axis, eps, window=None):
    """[(clip, f, a, b)] : image f du plan, intervalle source [a, b) exact sur l'axe voulu."""
    out = []
    for c in clips:
        if window is not None:      # simple élagage : un plan dont la source entière est hors fenêtre n'a aucune image concernée
            lo = to_axis(c, F(c.src_in * 1_000_000).limit_denominator(10**9))
            hi = to_axis(c, F(c.src_in * 1_000_000).limit_denominator(10**9) + F(c.duration * 1_000_000, tl.fps))
            if hi < window[0] - 5 or lo > window[1] + 5:
                continue
        for f in range(c.duration):
            a = to_axis(c, F(c.src_in * 1_000_000).limit_denominator(10**9) + F(f * 1_000_000, tl.fps))
            b = to_axis(c, F(c.src_in * 1_000_000).limit_denominator(10**9) + F((f + 1) * 1_000_000, tl.fps))
            out.append((c, f, a, b))
    return out


def point_matches(tl, clips, us, to_axis, eps=F(1, 2)):
    hits = [(c, f) for c, f, a, b in frames_of(tl, clips, None, to_axis, eps, (us, us)) if a - eps <= us < b - eps]
    keep = []
    for c, f in hits:
        # coupe : si l'image est la dernière d'un plan et que l'image 0 du plan suivant (même piste) est aussi touchée → droite gagne
        if f == c.duration - 1 and any(c2.track == c.track and c2.start == c.end and f2 == 0 for c2, f2 in hits):
            continue
        keep.append((c, f))
    return keep


def point_state(tl, clips, us, to_axis, eps=F(1, 2)):
    m = point_matches(tl, clips, us, to_axis, eps)
    return ("hors_plan" if not m else "exacte" if len(m) == 1 else "multiple"), sorted((c.id, c.start + f) for c, f in m)


def range_state(tl, clips, s, e, to_axis, eps=F(1, 2)):
    """(état, manquant µs) ; segments = images de plans dont l'intervalle rencontre [s, e) de plus de THRESH_US/10."""
    frs = []
    for c, f, a, b in frames_of(tl, clips, None, to_axis, eps, (s, e)):
        lo, hi = max(a, s), min(b, e)
        if hi - lo > F(THRESH_US, 10):
            frs.append((c, f, lo, hi, a, b))
    if not frs:
        return "hors_plan", e - s
    spans = sorted((lo, hi) for _, _, lo, hi, _, _ in frs)
    union, cur = F(0), None
    for lo, hi in spans:
        if cur is None or lo > cur[1]:
            union += (cur[1] - cur[0]) if cur else 0
            cur = [lo, hi]
        else:
            cur[1] = max(cur[1], hi)
    union += cur[1] - cur[0]
    total = sum(hi - lo for _, _, lo, hi, _, _ in frs)
    missing = (e - s) - union
    # blocs : images consécutives sur la timeline (même piste) ET contiguës dans la source
    frs.sort(key=lambda t: (t[0].track, t[0].start + t[1]))
    chains = []
    for t in frs:
        for ch in chains:
            p = ch[-1]
            if p[0].track == t[0].track and p[0].start + p[1] + 1 == t[0].start + t[1] and abs(p[5] - t[4]) <= F(THRESH_US, 10):
                ch.append(t)
                break
        else:
            chains.append([t])
    dup = total - union > THRESH_US
    if missing > THRESH_US:
        return "fragmentée", missing
    if dup:
        return "multiple", missing
    if len(chains) > 1:
        return "fragmentée", missing
    return "exacte", missing
