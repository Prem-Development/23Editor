/// Formats a clip duration as `m:ss`, for example `0:42` or `3:05`.
String formatClipDuration(Duration duration) {
  final int totalSeconds = duration.inSeconds;
  if (totalSeconds <= 0) {
    return '0:00';
  }
  final int minutes = totalSeconds ~/ 60;
  final int seconds = totalSeconds % 60;
  return '$minutes:${seconds.toString().padLeft(2, '0')}';
}
