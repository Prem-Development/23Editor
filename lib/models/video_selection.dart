import 'package:flutter/foundation.dart';

/// A video the user picked from the device. Only the file path is kept;
/// bytes are streamed by the player and never loaded fully into memory.
@immutable
class VideoSelection {
  final String path;

  const VideoSelection(this.path);

  /// File name portion of the path, without any directory segments.
  String get fileName {
    final String normalized = path.replaceAll(r'\', '/');
    final List<String> segments = normalized.split('/');
    final String last = segments.isEmpty ? path : segments.last;
    return last.isEmpty ? path : last;
  }
}
