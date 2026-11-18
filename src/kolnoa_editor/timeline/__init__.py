"""Timeline canonique de Kolnoa Editor (voir docs/ARCHITECTURE_V2.md, §2-3)."""

from .convert import TimelineExportError, from_decision_list, to_decision_list
from .editing import (delete_range, link_clips, linked_ids, move_clips, place_clip, razor, replace_clip, ripple_trim,
                      ripple_edge, ripple_limits, roll_trim, snap_frame, snap_move, snap_points, trim_edge, trim_limits, unlink_clips)
from .multicam_live import angle_for_key, clip_at, cut_to_angle, key_angles, record_angle_switches
from .audio_edit import (add_crossfade, assign_master, clear_master, master_at, remove_crossfade, reset_channels,
                         set_audio_settings, set_clip_gain, set_fades, set_strip, set_track_audio, to_dual_mono, to_mono,
                         to_stereo)
from .audio_mix import MixEvent, MixPlan, MixReport, build_mix_plan, measure_loudness, render_mix
from .audio_quality import AudioMeasure, AudioProposal, measure_audio, propose_master, score_measure
from .chutier import adjust_subclip, mark_subclip, place_rush, place_subclip, remove_subclip, search_bin
from .model import (Angle, AudioSettings, AudioStrip, Crossfade, MasterRange, MulticamGroup, Subclip, Clip, LockedError, Marker, Timeline, TimelineError, Track, SCHEMA)
from .ops import (AddCrossfade, AddMaster, RemoveCrossfade, RemoveMaster, SetAudioSettings, SetClipStrips, AddSubclip, RemoveSubclip, SetSubclip, AddGroup, RemoveGroup, AddAngle, RemoveAngle, SetAngleSync, SetClipAngle, add_multicam_clip,
                  switch_angle, nudge_angle, lock_angle, accept_angle, AddClip, AddMarker, AddTrack, JoinClips, MoveClip, Op, RemoveClip, RemoveMarker,
                  RemoveTrack, SetClipProps, SetProject, SetTrackProps, ShiftClips, SplitClip,
                  ShiftAfter, Transaction, TrimClip, execute, op_from_dict, ripple_delete, trim_head, trim_tail)
from .session import Session
from .flatten import Flattened, Issue, VideoSpan, check_flatten, flatten_video, visible_video_tracks

__all__ = [n for n in dir() if not n.startswith("_")]
