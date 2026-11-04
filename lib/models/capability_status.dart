import 'package:flutter/material.dart';

/// Truthful capability labels, matching the backend's /status vocabulary.
enum CapabilityState {
  verified,
  implementedButUntested,
  unavailable,
  planned,
  providerError,
}

CapabilityState capabilityStateFrom(String raw) {
  return switch (raw) {
    'verified' => CapabilityState.verified,
    'implemented_but_untested' => CapabilityState.implementedButUntested,
    'planned' => CapabilityState.planned,
    'provider_error' => CapabilityState.providerError,
    _ => CapabilityState.unavailable,
  };
}

extension CapabilityStateDisplay on CapabilityState {
  String get label => switch (this) {
    CapabilityState.verified => 'Verified',
    CapabilityState.implementedButUntested => 'Not yet tested',
    CapabilityState.unavailable => 'Unavailable',
    CapabilityState.planned => 'Planned',
    CapabilityState.providerError => 'Provider error',
  };

  IconData get icon => switch (this) {
    CapabilityState.verified => Icons.check_circle_outline,
    CapabilityState.implementedButUntested => Icons.hourglass_empty_outlined,
    CapabilityState.unavailable => Icons.cloud_off_outlined,
    CapabilityState.planned => Icons.schedule_outlined,
    CapabilityState.providerError => Icons.error_outline_outlined,
  };
}

/// One integration row from GET /status.
@immutable
class CapabilityInfo {
  final String id;
  final String title;
  final CapabilityState state;
  final String detail;

  /// Last live success, persisted by the backend across restarts.
  final String? verifiedAt;

  const CapabilityInfo({
    required this.id,
    required this.title,
    required this.state,
    required this.detail,
    this.verifiedAt,
  });

  static const Map<String, String> _titles = {
    'spark_plan': 'Editing plans (Muse Spark)',
    'voice_transcribe': 'Voice transcription',
    'sam_track': 'Subject tracking (SAM 3.1)',
    'image_assets': 'Thumbnails (Muse Image)',
    'wonder_plan': 'Complex planning (Muse Wonder)',
    'open_weight': 'On-device open models',
    'ffmpeg_render': 'Video rendering (FFmpeg)',
    'social_publishing': 'Social publishing',
  };

  factory CapabilityInfo.fromJson(Map<String, dynamic> json) {
    final String id = json['id'] as String? ?? 'unknown';
    return CapabilityInfo(
      id: id,
      title: _titles[id] ?? id,
      state: capabilityStateFrom(json['status'] as String? ?? ''),
      detail: json['detail'] as String? ?? '',
      verifiedAt: json['verified_at'] as String?,
    );
  }
}

/// Parsed GET /status response.
@immutable
class BackendStatus {
  final bool apiKeyPresent;
  final String apiAccess;
  final bool ffmpegPresent;
  final String ffmpegVersion;
  final String serverBuild;
  final List<CapabilityInfo> integrations;

  const BackendStatus({
    required this.apiKeyPresent,
    required this.apiAccess,
    required this.ffmpegPresent,
    required this.ffmpegVersion,
    required this.serverBuild,
    required this.integrations,
  });

  factory BackendStatus.fromJson(Map<String, dynamic> json) {
    final Object? ffmpeg = json['ffmpeg'];
    final Object? items = json['integrations'];
    return BackendStatus(
      apiKeyPresent:
          (json['api_key_present'] as bool?) ??
          (json['apiKeyPresent'] as bool?) ??
          false,
      apiAccess:
          (json['api_access'] as String?) ??
          (json['apiAccess'] as String?) ??
          'unknown',
      serverBuild:
          (json['server_build'] as String?) ??
          (json['serverBuild'] as String?) ??
          '',
      ffmpegPresent: ffmpeg is Map<String, dynamic>
          ? ffmpeg['present'] as bool? ?? false
          : false,
      ffmpegVersion: ffmpeg is Map<String, dynamic>
          ? ffmpeg['version'] as String? ?? ''
          : '',
      integrations: items is List
          ? items
                .whereType<Map<String, dynamic>>()
                .map(CapabilityInfo.fromJson)
                .toList()
          : const <CapabilityInfo>[],
    );
  }
}
