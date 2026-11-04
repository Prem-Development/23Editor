import 'package:flutter/material.dart';

/// One validated editing operation, for honest plan review.
@immutable
class PlanOperation {
  final String op;
  final Map<String, dynamic> raw;

  const PlanOperation({required this.op, required this.raw});

  String get label => switch (op) {
    'trim' => 'Trim',
    'crop' => 'Crop',
    'frame_subject' => 'Track subject',
    'resize_pad' => 'Resize',
    'volume' => 'Volume',
    'fade' => 'Fade',
    'keep' => 'Keep',
    'remove' => 'Remove',
    'text' => 'Text overlay',
    'zoom' => 'Zoom',
    'grade' => 'Grade',
    'transition' => 'Transition',
    'captions' => 'Captions',
    'music' => 'Music',
    'sfx' => 'Sound effect',
    'intro' => 'Intro image',
    'outro' => 'Outro image',
    'banner' => 'Banner overlay',
    _ => op,
  };

  IconData get icon => switch (op) {
    'trim' => Icons.content_cut_outlined,
    'crop' => Icons.crop_outlined,
    'frame_subject' => Icons.track_changes_outlined,
    'resize_pad' => Icons.aspect_ratio_outlined,
    'volume' => Icons.volume_up_outlined,
    'fade' => Icons.gradient_outlined,
    'keep' => Icons.playlist_add_outlined,
    'remove' => Icons.delete_outline,
    'text' => Icons.title_outlined,
    'zoom' => Icons.zoom_in,
    'grade' => Icons.palette_outlined,
    'transition' => Icons.animation_outlined,
    'captions' => Icons.closed_caption_outlined,
    'music' => Icons.music_note_outlined,
    'sfx' => Icons.graphic_eq_outlined,
    'intro' => Icons.slideshow_outlined,
    'outro' => Icons.slideshow_outlined,
    'banner' => Icons.branding_watermark_outlined,
    _ => Icons.tune_outlined,
  };

  /// Where this operation came from: the instruction, a style preset,
  /// or the user's own controls. Empty when the backend did not say
  /// (older plans).
  String get origin => switch (raw['origin']) {
    'instruction' => 'From your instruction',
    'preset' => 'From the style preset',
    'control' => 'From your controls',
    _ => '',
  };

  String describe() {
    String seconds(Object? value) {
      final double? seconds = (value as num?)?.toDouble();
      return seconds == null ? '?' : '${seconds.toStringAsFixed(1)}s';
    }

    String span() => '${seconds(raw['start'])} → ${seconds(raw['end'])}';

    String captionSummary() {
      final Object? segments = raw['segments'];
      if (raw['source'] == 'auto') {
        return 'Auto from speech';
      }
      if (segments is List) {
        return '${segments.length} lines';
      }
      return '';
    }

    return switch (op) {
      'trim' => span(),
      'keep' => span(),
      'remove' => span(),
      'crop' => '${raw['w']}×${raw['h']} at (${raw['x']}, ${raw['y']})',
      'frame_subject' =>
        '"${raw['subject'] ?? '?'}", ${seconds(raw['start'])} → ${seconds(raw['end'])}',
      'resize_pad' =>
        'Aspect ${raw['aspect'] ?? '?'}${raw['fit'] == 'crop' ? ', crop fill' : ''}',
      'volume' => '${(raw['gain_db'] as num?)?.toString() ?? '?'} dB',
      'fade' =>
        'In ${seconds(raw['in_sec'] ?? 0)}, out ${seconds(raw['out_sec'] ?? 0)}',
      'text' =>
        '"${raw['text'] ?? '?'}", ${raw['position'] ?? '?'}, ${raw['animation'] ?? '?'}',
      'zoom' => '${span()} ×${(raw['to'] as num?)?.toString() ?? '?'}',
      'grade' =>
        '${raw['preset'] ?? '?'} ${(((raw['intensity'] as num?)?.toDouble() ?? 0) * 100).round()}%',
      'transition' => '${raw['type'] ?? '?'} ${seconds(raw['duration'])}',
      'captions' => captionSummary(),
      'music' =>
        '${(raw['volume_db'] as num?)?.toString() ?? '?'} dB${raw['duck'] == true ? ', ducked' : ''}',
      'sfx' => '${raw['asset'] ?? 'audio'} at ${seconds(raw['at'])}',
      'intro' => seconds(raw['duration']),
      'outro' => seconds(raw['duration']),
      'banner' => '${raw['position'] ?? '?'}, ${span()} of the edit',
      _ => '',
    };
  }
}

/// A validated editing plan returned by POST /api/plan.
@immutable
class EditingPlan {
  final List<PlanOperation> operations;
  final String notes;
  final String model;
  final List<String> warnings;
  final List<String> unsupported;
  final List<String> pending;
  final List<String> adjustments;

  const EditingPlan({
    required this.operations,
    required this.notes,
    required this.model,
    required this.warnings,
    this.unsupported = const <String>[],
    this.pending = const <String>[],
    this.adjustments = const <String>[],
  });

  factory EditingPlan.fromJson(
    Map<String, dynamic> plan,
    String model,
    List<String> warnings, {
    List<String> pending = const <String>[],
    List<String> adjustments = const <String>[],
  }) {
    final Object? ops = plan['operations'];
    return EditingPlan(
      operations: ops is List
          ? ops
                .whereType<Map<String, dynamic>>()
                .map(
                  (Map<String, dynamic> op) =>
                      PlanOperation(op: op['op'] as String? ?? '?', raw: op),
                )
                .toList()
          : const <PlanOperation>[],
      notes: plan['notes'] as String? ?? '',
      model: model,
      warnings: warnings,
      unsupported: ((plan['unsupported'] as List?) ?? const [])
          .map((Object? item) => item.toString())
          .toList(),
      pending: pending,
      adjustments: adjustments,
    );
  }
}
