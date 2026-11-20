"""Moteur de mixage audio de la timeline : plan de mixage (pur, testable) puis rendu ffmpeg.

`build_mix_plan` transforme la timeline en événements sonores (un par plan audible, ou par morceau quand la source maître
change au milieu d'un plan multicam) : positions, gains cumulés, canaux, fondus, fondus enchaînés, mute/solo, correction de
dérive. Il ne lit aucun média. `render_mix` écrit un WAV : mixage en virgule flottante à la fréquence du projet
(44,1 kHz → 48 kHz rééchantillonné), puis étage final optionnel de normalisation de sonie et limiteur de crête, avec rapport
mesuré (jamais estimé). Les médias d'origine ne sont jamais modifiés.
"""

from __future__ import annotations

import json
import math
import re
import subprocess
import tempfile
from dataclasses import dataclass, field, replace
from pathlib import Path

from ..ffmpeg import CancelToken, ffmpeg_bin, ffprobe_bin, run, run_hidden
from .audio_edit import master_at
from .model import AudioStrip, Clip, Timeline, TimelineError

EPS = 1e-9
LIMITER_ATTACK_MS = 5
CURVE = {"linear": "tri", "equal_power": "qsin", "log": "log"}      # courbes ffmpeg `afade`


@dataclass
class MixEvent:
    clip_id: str
    track: str
    media: str
    t_start: float                  # secondes de timeline
    duration: float                 # secondes de timeline
    src_start: float                # secondes dans le média
    tempo: float = 1.0              # secondes de source consommées par seconde de timeline (≠ 1 : compense la dérive)
    gain_db: float = 0.0
    strips: list[AudioStrip] | None = None
    fade_in: float = 0.0
    fade_in_curve: str = "linear"
    fade_out: float = 0.0
    fade_out_curve: str = "linear"
    angle: str | None = None        # source maître utilisée (plans multicam « audio=group »)

    @property
    def end(self) -> float:
        return self.t_start + self.duration


@dataclass
class MixPlan:
    events: list[MixEvent] = field(default_factory=list)
    silent: dict[str, str] = field(default_factory=dict)       # plan → pourquoi il ne s'entend pas
    warnings: list[str] = field(default_factory=list)
    sample_rate: int = 48000

    @property
    def duration(self) -> float:
        return max((e.end for e in self.events), default=0.0)


def _db_to_lin(db: float) -> float:
    return 10.0 ** (db / 20.0)


# --- plan -----------------------------------------------------------------------------------------
def _audible_strips(strips: list[AudioStrip]) -> list[AudioStrip]:
    live = [b for b in strips if not b.mute]
    if any(b.solo for b in strips):
        live = [b for b in live if b.solo]
    return live


def build_mix_plan(tl: Timeline, audio_durations: dict[str, float | None] | None = None) -> MixPlan:
    plan = MixPlan(sample_rate=tl.audio_settings.sample_rate)
    solo_tracks = {t.id for t in tl.tracks if t.solo}
    sw = tl.audio_settings.switch_fade_ms / 1000.0
    by_first: dict[str, MixEvent] = {}
    by_last: dict[str, MixEvent] = {}
    for c in sorted(tl.clips, key=lambda x: (x.start, x.id)):
        tr = tl.track(c.track)
        if tr.muted:
            plan.silent[c.id] = f"piste {tr.id} muette"
        elif solo_tracks and tr.id not in solo_tracks:
            plan.silent[c.id] = "une autre piste est en solo"
        elif c.audio == "mute":
            plan.silent[c.id] = "son coupé sur le plan"
        elif c.strips is not None and not _audible_strips(c.strips):
            plan.silent[c.id] = "tous les canaux sont muets (ou un autre est en solo)"
        if c.id in plan.silent:
            continue
        gain = tl.source_gain_db + tr.gain_db + c.gain_db
        if c.audio == "group":
            evs = _group_events(tl, c, gain, sw, plan, audio_durations)
        else:
            evs = [MixEvent(c.id, c.track, c.media, c.start / tl.fps, c.duration / tl.fps, c.src_in,
                            float((c.extra or {}).get('speed', 1.0)), gain, c.strips,
                            c.fade_in, c.fade_in_curve, c.fade_out, c.fade_out_curve)]
        if evs:
            plan.events += evs
            by_first[c.id], by_last[c.id] = evs[0], evs[-1]
    for x in tl.crossfades:                                  # centré sur la coupure : d/2 de chaque côté
        left, right = by_last.get(x.left), by_first.get(x.right)
        if left is None or right is None:
            plan.warnings.append(f"fondu enchaîné {x.id} ignoré : un des deux plans est muet")
            continue
        half = x.duration / 2
        left.duration += half
        left.fade_out, left.fade_out_curve = x.duration, x.curve
        shift = half * right.tempo
        if right.src_start - shift < -EPS:
            plan.warnings.append(f"fondu enchaîné {x.id} : pas assez de matière avant {x.right}, fondu raccourci")
            half = right.src_start / right.tempo
            shift = right.src_start
        right.t_start -= half
        right.src_start = max(0.0, right.src_start - shift)
        right.duration += half
        right.fade_in, right.fade_in_curve = x.duration, x.curve
    return plan


def _group_events(tl: Timeline, c: Clip, gain: float, sw: float, plan: MixPlan, audio_durations=None) -> list[MixEvent]:
    """Plan multicam « audio=group » : le son suit l'axe du groupe, avec la source maître de chaque séquence."""
    g = tl.group(c.group)
    A = g.angle(c.angle)
    t0 = A.to_group(c.src_in)
    t1 = A.to_group(c.src_in + c.duration / tl.fps)
    cuts = sorted({v for r in tl.masters if r.group == g.id for v in (r.start, r.end) if t0 + EPS < v < t1 - EPS})
    if audio_durations is not None:
        for angle in g.angles:
            if not angle.usable or angle.media not in audio_durations:
                continue
            duration = audio_durations[angle.media]
            limits = [angle.to_group(0)]
            if duration is not None:
                limits.append(angle.to_group(duration))
            if angle.range:
                limits.extend(angle.to_group(value) for value in angle.range)
            cuts.extend(v for v in limits if t0 + EPS < v < t1 - EPS)
    edges = [t0, *sorted(set(cuts)), t1]
    def ts(tg):                                              # instant de groupe → seconde de timeline
        return c.start / tl.fps + (A.to_angle(tg) - c.src_in)
    evs: list[MixEvent] = []
    for a, b in zip(edges, edges[1:]):
        src = master_at(tl, g.id, (a + b) / 2)
        M = g.angle(src)
        if not M.usable:
            plan.warnings.append(f"{c.id}: la source {src} n'est plus synchronisée de façon fiable : maître du groupe utilisé")
            M = g.angle(g.master)
            src = g.master
        if audio_durations is not None:
            def covers(angle):
                if not angle.usable or angle.media not in audio_durations:
                    return False
                position = angle.to_angle((a + b) / 2)
                duration = audio_durations[angle.media]
                return (position >= 0 and (duration is None or position < duration)
                        and (angle.range is None or angle.range[0] <= position < angle.range[1]))

            if not covers(M):
                fallback = next((angle for angle in (A, g.angle(g.master), *g.angles) if covers(angle)), None)
                if fallback is None:
                    plan.warnings.append(f"{c.id}: aucun angle audio fiable sur {a:.2f}–{b:.2f} s")
                    continue
                plan.warnings.append(f"{c.id}: son repris sur l'angle {fallback.id} sur {a:.2f}–{b:.2f} s")
                M, src = fallback, fallback.id
        tempo = (1 + A.drift_ppm * 1e-6) / (1 + M.drift_ppm * 1e-6)
        if evs and evs[-1].angle == src and abs(evs[-1].end - ts(a)) < EPS:
            evs[-1].duration = ts(b) - evs[-1].t_start
        else:
            evs.append(MixEvent(c.id, c.track, M.media, ts(a), ts(b) - ts(a), max(0.0, M.to_angle(a)), tempo, gain, c.strips, angle=src))
    for i, e in enumerate(evs):
        e.fade_in, e.fade_in_curve = (c.fade_in, c.fade_in_curve) if i == 0 else (min(sw, e.duration / 2), "linear")
        e.fade_out, e.fade_out_curve = (c.fade_out, c.fade_out_curve) if i == len(evs) - 1 else (min(sw, e.duration / 2), "linear")
    return evs


_SCRIPT_OPT: str | None = None


def _filter_script_args(path: str) -> list[str]:
    """Graphe de filtres lu depuis un fichier. FFmpeg ≥ 7.1 a retiré `-filter_complex_script` au profit de `-/filter_complex` (trouvé par la CI
    Windows, build FFmpeg récent) : on teste une fois ce que le binaire accepte au lieu de supposer une version."""
    global _SCRIPT_OPT
    if _SCRIPT_OPT is None:
        try:
            h = run_hidden([ffmpeg_bin(), "-hide_banner", "-h", "full"], capture_output=True, text=True, timeout=30).stdout
        except (OSError, subprocess.SubprocessError):
            h = ""
        _SCRIPT_OPT = "-filter_complex_script" if "filter_complex_script" in h else "-/filter_complex"
    return [_SCRIPT_OPT, path]


# --- sondes ---------------------------------------------------------------------------------------
@dataclass
class AudioInfo:
    channels: int
    sample_rate: int
    duration: float | None


def probe_audio(path: Path, *, cancel: CancelToken | None = None) -> AudioInfo | None:
    """Premier flux audio du média, ou None s'il n'en a pas (caméra muette)."""
    out = run([ffprobe_bin(), "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)],
              cancel=cancel, timeout=60).stdout
    d = json.loads(out)
    a = next((s for s in d["streams"] if s["codec_type"] == "audio"), None)
    if a is None:
        return None
    dur = a.get("duration") or d["format"].get("duration")
    return AudioInfo(int(a.get("channels", 0) or 0), int(a.get("sample_rate", 0) or 0), float(dur) if dur else None)


# --- graphe ffmpeg --------------------------------------------------------------------------------
def _pan_expr(ev: MixEvent, channels: int) -> str:
    if ev.strips is None:
        return "pan=stereo|FL=c0|FR=c0" if channels == 1 else "pan=stereo|FL=c0|FR=c1"
    fl: dict[int, float] = {}
    fr: dict[int, float] = {}
    for b in _audible_strips(ev.strips):
        if b.src >= channels:
            raise TimelineError(f"{ev.clip_id}: le canal source {b.src} n'existe pas (la source a {channels} canal/canaux)")
        theta = (b.pan + 1.0) * math.pi / 4.0                 # loi de pan −3 dB : gauche dur = (1, 0), centre = (.707, .707)
        g = _db_to_lin(b.gain_db)
        fl[b.src] = fl.get(b.src, 0.0) + g * math.cos(theta)    # ffmpeg refuse de citer deux fois un canal : on additionne
        fr[b.src] = fr.get(b.src, 0.0) + g * math.sin(theta)
    fl_s = [f"{v:.6f}*c{k}" for k, v in sorted(fl.items()) if abs(v) > 1e-9]
    fr_s = [f"{v:.6f}*c{k}" for k, v in sorted(fr.items()) if abs(v) > 1e-9]
    return f"pan=stereo|FL={'+'.join(fl_s) or '0*c0'}|FR={'+'.join(fr_s) or '0*c0'}"


def build_filtergraph(plan: MixPlan, infos: dict[str, AudioInfo], total: float, *,
                      window_start: float | None = None, input_offsets=None) -> tuple[list[str], str]:
    """(liste des médias dans l'ordre des entrées ffmpeg, graphe filter_complex). Les événements sans audio sont ignorés."""
    sr = plan.sample_rate
    media = list(dict.fromkeys(e.media for e in plan.events if e.media in infos))
    uses = {m: [e for e in plan.events if e.media == m] for m in media}
    lines, labels = [], []
    for k, m in enumerate(media):
        n = len(uses[m])
        if n == 1:
            lines.append(f"[{k}:a]anull[i{k}_0]")
        else:
            lines.append(f"[{k}:a]asplit={n}" + "".join(f"[i{k}_{j}]" for j in range(n)))
    for k, m in enumerate(media):
        for j, e in enumerate(uses[m]):
            ch = infos[m].channels
            src_dur = e.duration * e.tempo
            input_start = max(0.0, e.src_start - (input_offsets or {}).get(m, 0.0))
            chain = [f"atrim=start={input_start:.6f}:duration={src_dur:.6f}", "asetpts=PTS-STARTPTS"]
            if abs(e.tempo - 1.0) > 1e-9 and abs(e.tempo - 1.0) > 0.01:
                chain.append(f'atempo={e.tempo:.8f}')
            elif abs(e.tempo - 1.0) > 1e-9:
                # Une dérive est une erreur d'horloge d'échantillonnage : on la corrige en rééchantillonnant (pas d'atempo,
                # dont les fenêtres déplacent les transitoires de plusieurs ms). Sur-échantillonnage ×4 avant `asetrate`
                # pour que l'arrondi à l'Hz entier ne laisse que ~2 ppm d'erreur. Hauteur décalée de la dérive (qq cents).
                hi = infos[m].sample_rate * 4
                chain += [f"aresample={hi}", f"asetrate={round(hi * e.tempo)}"]
            chain.append(f"aresample={sr}")
            chain.append(_pan_expr(e, ch))
            if abs(e.gain_db) > 1e-9:
                chain.append(f"volume={e.gain_db:.4f}dB")
            if e.fade_in > 0:
                chain.append(f"afade=t=in:st=0:d={e.fade_in:.6f}:curve={CURVE[e.fade_in_curve]}")
            if e.fade_out > 0:
                chain.append(f"afade=t=out:st={max(0.0, e.duration - e.fade_out):.6f}:d={e.fade_out:.6f}:curve={CURVE[e.fade_out_curve]}")
            delay_start = e.t_start
            if window_start is not None:
                left = max(window_start, e.t_start)
                right = min(window_start + total, e.end)
                trim_start = max(0, round(left * sr) - round(e.t_start * sr))
                trim_end = round(right * sr) - round(e.t_start * sr)
                chain += [f"atrim=start_sample={trim_start}:end_sample={trim_end}", "asetpts=N/SR/TB"]
                delay_start -= window_start
            delay = max(0, round(delay_start * sr))
            if delay:
                chain.append(f"adelay=delays={delay}S:all=1")
            lab = f"e{k}_{j}"
            lines.append(f"[i{k}_{j}]" + ",".join(chain) + f"[{lab}]")
            labels.append(lab)
    tail = "asetpts=N/SR/TB"            # la longueur finale est imposée par `-t` (atrim se trompe après adelay : horloge des images inchangée)
    if not labels:
        lines.append(f"anullsrc=r={sr}:cl=stereo,atrim=duration={total:.6f},{tail}[out]")
    elif len(labels) == 1:
        lines.append(f"[{labels[0]}]apad=whole_dur={total:.6f},{tail}[out]")
    else:
        lines.append("".join(f"[{l}]" for l in labels) + f"amix=inputs={len(labels)}:normalize=0:duration=longest:dropout_transition=0,"
                     f"apad=whole_dur={total:.6f},{tail}[out]")
    return media, ";\n".join(lines)


# --- mesure et rendu --------------------------------------------------------------------------------
@dataclass
class Loudness:
    integrated_lufs: float | None
    true_peak_dbtp: float | None
    lra: float | None = None


_NUM = r"(-?\d+(?:\.\d+)?|-inf)"


def measure_loudness(path: Path, *, cancel: CancelToken | None = None) -> Loudness:
    """Sonie intégrée (LUFS, EBU R128) et crête vraie (dBTP) mesurées par ffmpeg `ebur128` sur le fichier entier."""
    r = run([ffmpeg_bin(), "-hide_banner", "-nostats", "-i", str(path), "-af", "ebur128=peak=true", "-f", "null", "-"],
            cancel=cancel, check=False)
    tail = r.stderr[r.stderr.rfind("Summary:"):] if "Summary:" in r.stderr else ""
    def grab(label):
        m = re.search(label + rf"\s*{_NUM}", tail)
        return None if not m or m.group(1) == "-inf" else float(m.group(1))
    lufs = grab(r"I:")
    if lufs is not None and lufs <= -69.99:                  # plancher de mesure d'ebur128 : « silence », pas une sonie
        lufs = None
    return Loudness(lufs, grab(r"Peak:"), grab(r"LRA:"))


@dataclass
class MixReport:
    duration: float
    sample_rate: int
    events: int
    before: Loudness                 # mixage brut
    after: Loudness                  # fichier écrit
    gain_applied_db: float = 0.0     # normalisation de sonie (gain constant)
    limiter: bool = False            # le limiteur de crête est intervenu
    warnings: list[str] = field(default_factory=list)


def _run(cmd: list[str], cancel=None) -> None:
    r = run(cmd, cancel=cancel, check=False)
    if r.returncode != 0:
        raise TimelineError("ffmpeg a échoué : " + "\n".join(r.stderr.strip().splitlines()[-6:]))


MIX_WINDOW_SECONDS = 60


def _render_dry(plan, infos, total, media_root, directory, dry, cancel):
    """Borne la taille du graphe audio en traitant de courts blocs au temps du programme."""
    script = directory / "graph.txt"
    inputs = [ffmpeg_bin(), "-y", "-v", "error"]

    def render_block(piece, duration, path, start=None):
        offsets = {}
        if start is not None:
            for event in piece.events:
                offsets[event.media] = min(event.src_start, offsets.get(event.media, event.src_start))
        media, graph = build_filtergraph(piece, infos, duration, window_start=start, input_offsets=offsets)
        script.write_text(graph, encoding="utf-8")
        cmd = list(inputs)
        for name in media:
            offset = offsets.get(name, 0.0)
            if offset > 0:
                cmd += ["-ss", f"{offset:.6f}"]
            cmd += ["-i", str(media_root / name)]
        cmd += [*_filter_script_args(str(script)), "-map", "[out]", "-t", f"{duration:.6f}",
                "-ar", str(plan.sample_rate), "-c:a", "pcm_f32le", str(path)]
        _run(cmd, cancel)

    if total <= MIX_WINDOW_SECONDS:
        render_block(plan, total, dry)
        return

    blocks = []
    end_sample = round(total * plan.sample_rate)
    step = MIX_WINDOW_SECONDS * plan.sample_rate
    for index, sample in enumerate(range(0, end_sample, step)):
        if cancel is not None:
            cancel.raise_if_cancelled()
        start = sample / plan.sample_rate
        end = min(sample + step, end_sample) / plan.sample_rate
        events = [e for e in plan.events if round(e.end * plan.sample_rate) > sample
                  and round(e.t_start * plan.sample_rate) < min(sample + step, end_sample)]
        path = directory / f"block-{index:05}.wav"
        render_block(replace(plan, events=events), end - start, path, start)
        blocks.append(path)
    playlist = directory / "blocks.txt"
    playlist.write_text("".join("file '" + path.resolve().as_posix().replace("'", "'\\''") + "'\n" for path in blocks),
                        encoding="utf-8")
    _run([*inputs, "-f", "concat", "-safe", "0", "-i", str(playlist), "-c:a", "copy", "-rf64", "auto", str(dry)], cancel)


def render_mix(tl: Timeline, media_root: Path, out_path: Path, *, duration: float | None = None, cancel=None) -> MixReport:
    """Rend le mixage audio de la timeline dans `out_path` (WAV PCM 24 bits stéréo). Ne modifie aucun média.

    1. mixage en flottant ; 2. mesure (sonie, crête vraie) ; 3. si `audio_settings.target_lufs` : gain constant vers la cible ;
    4. si la crête dépasse le plafond : limiteur (crête d'échantillon avec anticipation ; la crête vraie finale est mesurée
    et rapportée, pas supposée)."""
    media_root, out_path = Path(media_root), Path(out_path)
    plan = build_mix_plan(tl)
    warnings = list(plan.warnings)
    infos: dict[str, AudioInfo] = {}
    candidates = dict.fromkeys(e.media for e in plan.events)
    direct = {c.media for c in tl.clips if c.audio == "source" and c.id not in plan.silent}
    for c in tl.clips:
        if c.audio == "group" and c.id not in plan.silent:
            for angle in tl.group(c.group).angles:
                if angle.usable and angle.has_audio and (media_root / angle.media).is_file():
                    candidates.setdefault(angle.media, None)
    for m in candidates:
        f = media_root / m
        if not f.is_file():
            if m in direct:
                raise TimelineError(f"média introuvable : {f}")
            warnings.append(f"{m} : source audio multicam introuvable")
            continue
        info = probe_audio(f, cancel=cancel)
        if info is None:
            warnings.append(f"{m} n'a pas de piste audio : ignoré")
        else:
            infos[m] = info
            if info.sample_rate and info.sample_rate != plan.sample_rate:
                warnings.append(f"{m} : {info.sample_rate} Hz rééchantillonné en {plan.sample_rate} Hz")
            if info.channels > 2 and not any(e.strips for e in plan.events if e.media == m):
                warnings.append(f"{m} : {info.channels} canaux, seuls les deux premiers sont repris (définir des canaux explicites)")
    plan = build_mix_plan(tl, {m: info.duration for m, info in infos.items()})
    warnings.extend(note for note in plan.warnings if note not in warnings)
    plan.events = [e for e in plan.events if e.media in infos]
    total = duration if duration is not None else plan.duration
    with tempfile.TemporaryDirectory(prefix="kolnoa_mix_") as tmp:
        tmp = Path(tmp)
        dry = tmp / "dry.wav"
        _render_dry(plan, infos, total, media_root, tmp, dry, cancel)
        before = measure_loudness(dry, cancel=cancel)
        st = tl.audio_settings
        gain = 0.0
        if st.target_lufs is not None:
            if before.integrated_lufs is None:
                warnings.append("sonie non mesurable (mixage silencieux) : normalisation ignorée")
            else:
                gain = st.target_lufs - before.integrated_lufs
        peak_after = None if before.true_peak_dbtp is None else before.true_peak_dbtp + gain
        limiter = peak_after is not None and peak_after > st.ceiling_dbtp + 1e-9
        chain = []
        if abs(gain) > 1e-9:
            chain.append(f"volume={gain:.4f}dB")
        if limiter:
            # alimiter retarde le son de `attack` ms (anticipation) : on pousse du silence devant puis on rogne autant,
            # sinon tout le mixage serait décalé de 5 ms par rapport à l'image
            lookahead = round(LIMITER_ATTACK_MS / 1000.0 * plan.sample_rate)
            chain += [f"apad=pad_dur={LIMITER_ATTACK_MS / 1000.0}",
                      f"alimiter=limit={_db_to_lin(st.ceiling_dbtp):.6f}:attack={LIMITER_ATTACK_MS}:release=50:level=disabled",
                      f"atrim=start_sample={lookahead}", "asetpts=N/SR/TB"]
        out_path.parent.mkdir(parents=True, exist_ok=True)
        fin = [ffmpeg_bin(), "-y", "-v", "error", "-i", str(dry)]
        if chain:
            fin += ["-af", ",".join(chain)]
        _run(fin + ["-ar", str(plan.sample_rate), "-c:a", "pcm_s24le", str(out_path)], cancel)
    after = measure_loudness(out_path, cancel=cancel)
    if after.true_peak_dbtp is not None and after.true_peak_dbtp > st.ceiling_dbtp + 0.1:
        warnings.append(f"crête vraie {after.true_peak_dbtp:.1f} dBTP au-dessus du plafond {st.ceiling_dbtp:.1f} dBTP : "
                        "baisser les gains ou le plafond du limiteur")
    if limiter:
        warnings.append(f"limiteur intervenu : crête avant {before.true_peak_dbtp:.1f} dBTP (+ gain {gain:.1f} dB) au-dessus du plafond")
    return MixReport(total, plan.sample_rate, len(plan.events), before, after, round(gain, 4), limiter, warnings)
