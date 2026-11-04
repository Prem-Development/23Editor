import 'dart:io';

import 'package:editor_23/theme/app_theme.dart';
import 'package:editor_23/utils/duration_format.dart';
import 'package:flutter/material.dart';
import 'package:video_player/video_player.dart';

/// Plays a local video file with play/pause and seeking.
///
/// The file is streamed by the platform player, so it is never loaded fully
/// into memory. The controller is paused when the route goes away and is
/// always disposed. Preview failures are shown inline instead of crashing.
class VideoPreviewPlayer extends StatefulWidget {
  final String videoPath;
  final ValueChanged<Duration>? onDurationReady;

  /// Called once the platform player finishes opening the file, with
  /// the measured open time. Never called when opening fails.
  final ValueChanged<Duration>? onPrepared;

  const VideoPreviewPlayer({
    super.key,
    required this.videoPath,
    this.onDurationReady,
    this.onPrepared,
  });

  @override
  State<VideoPreviewPlayer> createState() => _VideoPreviewPlayerState();
}

class _VideoPreviewPlayerState extends State<VideoPreviewPlayer> {
  VideoPlayerController? _controller;
  bool _failed = false;
  bool _playing = false;
  Duration _position = Duration.zero;

  @override
  void initState() {
    super.initState();
    _open(widget.videoPath);
  }

  @override
  void didUpdateWidget(covariant VideoPreviewPlayer oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.videoPath != widget.videoPath) {
      _open(widget.videoPath);
    }
  }

  Future<void> _open(String path) async {
    await _close();
    if (!mounted) {
      return;
    }
    setState(() {
      _failed = false;
      _playing = false;
      _position = Duration.zero;
    });
    final VideoPlayerController controller = VideoPlayerController.file(
      File(path),
    );
    _controller = controller;
    controller.addListener(_onTick);
    final Stopwatch clock = Stopwatch()..start();
    try {
      await controller.initialize();
    } catch (_) {
      if (!mounted) {
        return;
      }
      setState(() => _failed = true);
      return;
    }
    clock.stop();
    if (!mounted) {
      return;
    }
    widget.onDurationReady?.call(controller.value.duration);
    widget.onPrepared?.call(clock.elapsed);
    setState(() {});
  }

  Future<void> _close() async {
    final VideoPlayerController? controller = _controller;
    _controller = null;
    if (controller == null) {
      return;
    }
    controller.removeListener(_onTick);
    try {
      await controller.dispose();
    } catch (_) {
      // The controller was never fully opened; nothing left to release.
    }
  }

  void _onTick() {
    final VideoPlayerController? controller = _controller;
    if (controller == null || !mounted || !controller.value.isInitialized) {
      return;
    }
    final Duration position = controller.value.position;
    final bool playing = controller.value.isPlaying;
    final int bucket = position.inMilliseconds ~/ 250;
    final int lastBucket = _position.inMilliseconds ~/ 250;
    if (bucket != lastBucket || playing != _playing) {
      setState(() {
        _position = position;
        _playing = playing;
      });
    }
  }

  @override
  void deactivate() {
    _pauseForNavigation().ignore();
    super.deactivate();
  }

  Future<void> _pauseForNavigation() async {
    final VideoPlayerController? controller = _controller;
    if (controller == null || !controller.value.isInitialized) {
      return;
    }
    try {
      await controller.pause();
    } catch (_) {
      // Best effort: the preview is already going away.
    }
  }

  @override
  void dispose() {
    _close().ignore();
    super.dispose();
  }

  Future<void> _toggle() async {
    final VideoPlayerController? controller = _controller;
    if (controller == null || !controller.value.isInitialized) {
      return;
    }
    try {
      if (controller.value.isPlaying) {
        await controller.pause();
      } else {
        if (_position >= controller.value.duration) {
          await controller.seekTo(Duration.zero);
        }
        await controller.play();
      }
    } catch (_) {
      if (mounted) {
        setState(() => _failed = true);
      }
    }
  }

  Future<void> _seek(double millis) async {
    final VideoPlayerController? controller = _controller;
    if (controller == null || !controller.value.isInitialized) {
      return;
    }
    try {
      await controller.seekTo(Duration(milliseconds: millis.round()));
    } catch (_) {
      // Keep the current position when seeking is unsupported.
    }
  }

  @override
  Widget build(BuildContext context) {
    if (_failed) {
      return _PreviewMessage(
        icon: Icons.warning_amber_outlined,
        title: 'Preview unavailable',
        message: 'This video could not be opened for preview.',
        actionLabel: 'Retry',
        onAction: () => _open(widget.videoPath),
      );
    }
    final VideoPlayerController? controller = _controller;
    if (controller == null || !controller.value.isInitialized) {
      return const _PreviewMessage(
        icon: Icons.movie_outlined,
        title: 'Loading preview…',
        message: 'Reading the selected video file.',
        busy: true,
      );
    }
    final Duration duration = controller.value.duration;
    final double max = duration.inMilliseconds.toDouble();
    final double safeMax = max > 0 ? max : 1;
    final double value = _position.inMilliseconds
        .toDouble()
        .clamp(0, safeMax)
        .toDouble();
    final double aspectRatio = controller.value.aspectRatio > 0
        ? controller.value.aspectRatio
        : 16 / 9;
    final TextTheme text = Theme.of(context).textTheme;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: <Widget>[
        ClipRRect(
          borderRadius: BorderRadius.circular(12),
          child: AspectRatio(
            aspectRatio: aspectRatio,
            child: VideoPlayer(controller),
          ),
        ),
        const SizedBox(height: 8),
        Row(
          children: <Widget>[
            IconButton.filled(
              onPressed: _toggle,
              tooltip: _playing ? 'Pause' : 'Play',
              icon: Icon(_playing ? Icons.pause : Icons.play_arrow),
            ),
            const SizedBox(width: 4),
            SizedBox(
              width: 44,
              child: Text(
                formatClipDuration(_position),
                style: text.bodySmall,
                textAlign: TextAlign.center,
              ),
            ),
            Expanded(
              child: Slider(
                value: value,
                max: safeMax,
                label: formatClipDuration(_position),
                onChanged: _seek,
              ),
            ),
            SizedBox(
              width: 44,
              child: Text(
                formatClipDuration(duration),
                style: text.bodySmall,
                textAlign: TextAlign.center,
              ),
            ),
          ],
        ),
      ],
    );
  }
}

/// Placeholder box shown while the preview loads or when it fails.
class _PreviewMessage extends StatelessWidget {
  final IconData icon;
  final String title;
  final String message;
  final bool busy;
  final String? actionLabel;
  final VoidCallback? onAction;

  const _PreviewMessage({
    required this.icon,
    required this.title,
    required this.message,
    this.busy = false,
    this.actionLabel,
    this.onAction,
  });

  @override
  Widget build(BuildContext context) {
    final TextTheme text = Theme.of(context).textTheme;
    final String? label = actionLabel;
    final VoidCallback? action = onAction;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 28),
      decoration: BoxDecoration(
        color: AppColors.surfaceHigh,
        borderRadius: BorderRadius.circular(12),
      ),
      child: Column(
        children: <Widget>[
          if (busy)
            const SizedBox(
              width: 28,
              height: 28,
              child: CircularProgressIndicator(strokeWidth: 3),
            )
          else
            Icon(icon, size: 32, color: AppColors.violetSoft),
          const SizedBox(height: 12),
          Text(title, style: text.titleMedium, textAlign: TextAlign.center),
          const SizedBox(height: 4),
          Text(message, style: text.bodySmall, textAlign: TextAlign.center),
          if (label != null && action != null) ...<Widget>[
            const SizedBox(height: 12),
            OutlinedButton(onPressed: action, child: Text(label)),
          ],
        ],
      ),
    );
  }
}
