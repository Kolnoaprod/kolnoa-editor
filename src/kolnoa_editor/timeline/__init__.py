"""Timeline canonique de Kolnoa Editor (voir docs/ARCHITECTURE_V2.md, §2-3)."""

from .convert import TimelineExportError, from_decision_list, to_decision_list
from .editing import (delete_range, link_clips, linked_ids, move_clips, place_clip, razor, replace_clip, ripple_trim,
                      roll_trim, snap_frame, snap_move, snap_points, unlink_clips)
from .multicam_live import angle_for_key, clip_at, cut_to_angle, key_angles, record_angle_switches
from .chutier import adjust_subclip, mark_subclip, place_subclip, remove_subclip, search_bin
from .model import (Angle, MulticamGroup, Subclip, Clip, LockedError, Marker, Timeline, TimelineError, Track, SCHEMA)
from .ops import (AddSubclip, RemoveSubclip, SetSubclip, AddGroup, RemoveGroup, AddAngle, RemoveAngle, SetAngleSync, SetClipAngle, add_multicam_clip,
                  switch_angle, nudge_angle, lock_angle, accept_angle, AddClip, AddMarker, AddTrack, JoinClips, MoveClip, Op, RemoveClip, RemoveMarker,
                  RemoveTrack, SetClipProps, SetProject, SetTrackProps, ShiftClips, SplitClip,
                  ShiftAfter, Transaction, TrimClip, execute, op_from_dict, ripple_delete, trim_head, trim_tail)
from .session import Session

__all__ = [n for n in dir() if not n.startswith("_")]
