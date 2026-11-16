"""Timeline canonique de Kolnoa Editor (voir docs/ARCHITECTURE_V2.md, §2-3)."""

from .color import ColorError, ColorGrade, grade_warnings, stack_for_clip
from .convert import TimelineExportError, from_decision_list, to_decision_list
from .model import (Angle, MulticamGroup, Clip, LockedError, Marker, Timeline, TimelineError, Track, SCHEMA)
from .ops import (AddGroup, RemoveGroup, AddAngle, RemoveAngle, SetAngleSync, SetClipAngle, add_multicam_clip,
                  switch_angle, nudge_angle, lock_angle, accept_angle, AddClip, AddMarker, AddTrack, JoinClips, MoveClip, Op, RemoveClip, RemoveMarker,
                  RemoveTrack, SetClipProps, SetProject, SetTrackProps, ShiftClips, SplitClip,
                  SetColorGrade, Transaction, TrimClip, execute, op_from_dict, ripple_delete, trim_head, trim_tail)
from .session import Session

__all__ = [n for n in dir() if not n.startswith("_")]
