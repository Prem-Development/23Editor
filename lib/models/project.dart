import 'package:editor_23/models/edit_setup.dart';
import 'package:flutter/foundation.dart';

/// A saved editing project: source reference, settings, plan, job state,
/// successful output references, and the project's deliberate extras
/// (captions, music, effects, fit, thumbnail). Stored as JSON on the
/// device. Reopening restores only this project's own choices.
@immutable
class Project {
  final String id;
  final String name;
  final String sourcePath;
  final String fileName;
  final String instruction;
  final EditStyle? style;
  final AspectRatioOption? aspectRatio;
  final ResizeFit fit;
  final String? fileId;
  final Map<String, dynamic>? plan;
  final String? planModel;
  final String? renderPath;
  final double? renderDurationSec;
  final int? renderWidth;
  final int? renderHeight;
  final List<String> steps;
  final String status;
  final DateTime updatedAt;

  /// Caption burn-in choice: 'on'/'off'/null (auto).
  final String? captionChoice;

  /// Edited caption lines: {start, end, text}.
  final List<Map<String, dynamic>> captionLines;

  /// Attached music and its mix settings.
  final String? musicPath;
  final double musicVolumeDb;
  final bool musicDuck;

  /// Sound effects: {asset, at}.
  final List<Map<String, dynamic>> sfxPicks;

  /// User-chosen kept/removed intervals: {keep, start, end} in
  /// source-video seconds, in row order.
  final List<Map<String, dynamic>> cuts;

  /// Generated thumbnail on this device, if any.
  final String? thumbPath;

  /// Image attachments: {kind, path, fileId?, name?, duration?,
  /// start?, end?, position?, size?}. kind is cover, intro, outro, or
  /// banner; path is the device-local image, fileId the confirmed
  /// server asset id (absent on projects saved before upload-first
  /// attachments, which re-upload the device file at render time).
  final List<Map<String, dynamic>> attachments;

  /// Main clips in story order: {devicePath, fileId?, name?, duration?,
  /// width?, height?, fps?, hasAudio?}. Empty on projects saved before
  /// multi-clip support, which reopen as [sourcePath].
  final List<Map<String, dynamic>> clips;

  /// Supporting-video placements: {kind, fileId, devicePath?, name?, at,
  /// duration, srcStart?, position?, widthFrac?, audio, phrase?,
  /// sourceAt?}. kind is cutaway, pip, or insert; at is edited-output
  /// seconds (for insert: converted to content seconds at render);
  /// phrase/sourceAt re-resolve the position when the plan changes.
  final List<Map<String, dynamic>> support;

  const Project({
    required this.id,
    required this.name,
    required this.sourcePath,
    required this.fileName,
    this.instruction = '',
    this.style,
    this.aspectRatio,
    this.fit = ResizeFit.pad,
    this.fileId,
    this.plan,
    this.planModel,
    this.renderPath,
    this.renderDurationSec,
    this.renderWidth,
    this.renderHeight,
    this.steps = const <String>[],
    this.status = 'draft',
    required this.updatedAt,
    this.captionChoice,
    this.captionLines = const <Map<String, dynamic>>[],
    this.musicPath,
    this.musicVolumeDb = -12,
    this.musicDuck = true,
    this.sfxPicks = const <Map<String, dynamic>>[],
    this.cuts = const <Map<String, dynamic>>[],
    this.thumbPath,
    this.attachments = const <Map<String, dynamic>>[],
    this.clips = const <Map<String, dynamic>>[],
    this.support = const <Map<String, dynamic>>[],
  });

  static T? _enumByName<T extends Enum>(List<T> values, Object? name) {
    for (final T value in values) {
      if (value.name == name) {
        return value;
      }
    }
    return null;
  }

  static List<Map<String, dynamic>> _mapList(Object? value) {
    if (value is! List) {
      return const <Map<String, dynamic>>[];
    }
    return value
        .whereType<Map>()
        .map((Map item) => item.cast<String, dynamic>())
        .toList();
  }

  factory Project.fromJson(Map<String, dynamic> json) {
    return Project(
      id: json['id'] as String? ?? '',
      name: json['name'] as String? ?? 'Untitled',
      sourcePath: json['sourcePath'] as String? ?? '',
      fileName: json['fileName'] as String? ?? '',
      instruction: json['instruction'] as String? ?? '',
      style: _enumByName(EditStyle.values, json['style']),
      aspectRatio: _enumByName(AspectRatioOption.values, json['aspectRatio']),
      fit: _enumByName(ResizeFit.values, json['fit']) ?? ResizeFit.pad,
      fileId: json['fileId'] as String?,
      plan: (json['plan'] as Map?)?.cast<String, dynamic>(),
      planModel: json['planModel'] as String?,
      renderPath: json['renderPath'] as String?,
      renderDurationSec: (json['renderDurationSec'] as num?)?.toDouble(),
      renderWidth: (json['renderWidth'] as num?)?.toInt(),
      renderHeight: (json['renderHeight'] as num?)?.toInt(),
      steps:
          (json['steps'] as List?)
              ?.map((Object? item) => item.toString())
              .toList() ??
          const <String>[],
      status: json['status'] as String? ?? 'draft',
      updatedAt:
          DateTime.tryParse(json['updatedAt'] as String? ?? '') ??
          DateTime.fromMillisecondsSinceEpoch(0),
      captionChoice: json['captionChoice'] as String?,
      captionLines: _mapList(json['captionLines']),
      musicPath: json['musicPath'] as String?,
      musicVolumeDb: (json['musicVolumeDb'] as num?)?.toDouble() ?? -12,
      musicDuck: json['musicDuck'] as bool? ?? true,
      sfxPicks: _mapList(json['sfxPicks']),
      cuts: _mapList(json['cuts']),
      thumbPath: json['thumbPath'] as String?,
      attachments: _mapList(json['attachments']),
      clips: _mapList(json['clips']),
      support: _mapList(json['support']),
    );
  }

  Map<String, dynamic> toJson() {
    return <String, dynamic>{
      'id': id,
      'name': name,
      'sourcePath': sourcePath,
      'fileName': fileName,
      'instruction': instruction,
      'style': style?.name,
      'aspectRatio': aspectRatio?.name,
      'fit': fit.name,
      'fileId': fileId,
      'plan': plan,
      'planModel': planModel,
      'renderPath': renderPath,
      'renderDurationSec': renderDurationSec,
      'renderWidth': renderWidth,
      'renderHeight': renderHeight,
      'steps': steps,
      'status': status,
      'updatedAt': updatedAt.toIso8601String(),
      'captionChoice': captionChoice,
      'captionLines': captionLines,
      'musicPath': musicPath,
      'musicVolumeDb': musicVolumeDb,
      'musicDuck': musicDuck,
      'sfxPicks': sfxPicks,
      'cuts': cuts,
      'thumbPath': thumbPath,
      'attachments': attachments,
      'clips': clips,
      'support': support,
    };
  }

  Project copyWith({
    String? instruction,
    EditStyle? style,
    bool clearStyle = false,
    AspectRatioOption? aspectRatio,
    bool clearAspectRatio = false,
    ResizeFit? fit,
    String? fileId,
    Map<String, dynamic>? plan,
    String? planModel,
    String? renderPath,
    double? renderDurationSec,
    int? renderWidth,
    int? renderHeight,
    List<String>? steps,
    String? status,
    DateTime? updatedAt,
    String? captionChoice,
    List<Map<String, dynamic>>? captionLines,
    String? musicPath,
    double? musicVolumeDb,
    bool? musicDuck,
    List<Map<String, dynamic>>? sfxPicks,
    List<Map<String, dynamic>>? cuts,
    String? thumbPath,
    List<Map<String, dynamic>>? attachments,
    List<Map<String, dynamic>>? clips,
    List<Map<String, dynamic>>? support,
  }) {
    return Project(
      id: id,
      name: name,
      sourcePath: sourcePath,
      fileName: fileName,
      instruction: instruction ?? this.instruction,
      style: clearStyle ? null : (style ?? this.style),
      aspectRatio: clearAspectRatio ? null : (aspectRatio ?? this.aspectRatio),
      fit: fit ?? this.fit,
      fileId: fileId ?? this.fileId,
      plan: plan ?? this.plan,
      planModel: planModel ?? this.planModel,
      renderPath: renderPath ?? this.renderPath,
      renderDurationSec: renderDurationSec ?? this.renderDurationSec,
      renderWidth: renderWidth ?? this.renderWidth,
      renderHeight: renderHeight ?? this.renderHeight,
      steps: steps ?? this.steps,
      status: status ?? this.status,
      updatedAt: updatedAt ?? this.updatedAt,
      captionChoice: captionChoice ?? this.captionChoice,
      captionLines: captionLines ?? this.captionLines,
      musicPath: musicPath ?? this.musicPath,
      musicVolumeDb: musicVolumeDb ?? this.musicVolumeDb,
      musicDuck: musicDuck ?? this.musicDuck,
      sfxPicks: sfxPicks ?? this.sfxPicks,
      cuts: cuts ?? this.cuts,
      thumbPath: thumbPath ?? this.thumbPath,
      attachments: attachments ?? this.attachments,
      clips: clips ?? this.clips,
      support: support ?? this.support,
    );
  }
}
