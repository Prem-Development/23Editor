import 'dart:async';
import 'dart:io';

import 'package:editor_23/models/edit_setup.dart';
import 'package:editor_23/models/video_selection.dart';
import 'package:editor_23/screens/workspace_screen.dart';
import 'package:editor_23/services/backend_client.dart';
import 'package:editor_23/theme/app_theme.dart';
import 'package:editor_23/utils/duration_format.dart';
import 'package:editor_23/widgets/fade_in.dart';
import 'package:editor_23/widgets/option_chips.dart';
import 'package:editor_23/widgets/primary_button.dart';
import 'package:editor_23/widgets/section_card.dart';
import 'package:editor_23/widgets/video_preview_player.dart';
import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';
import 'package:path_provider/path_provider.dart';
import 'package:record/record.dart';

/// Step 1 of the flow: pick a real video, describe the edit by typing or
/// by voice, and choose requested style and aspect ratio settings.
class CreateScreen extends StatefulWidget {
  const CreateScreen({super.key});

  @override
  State<CreateScreen> createState() => _CreateScreenState();
}

class _CreateScreenState extends State<CreateScreen> {
  final TextEditingController _instructionController = TextEditingController();
  final BackendClient _client = BackendClient();
  AudioRecorder? _recorder;
  Timer? _recordTimer;
  final List<VideoSelection> _videos = <VideoSelection>[];
  final Map<String, Duration> _durations = <String, Duration>{};
  EditStyle? _style;
  AspectRatioOption? _aspectRatio;
  bool _picking = false;
  String? _pickError;
  String? _validationError;

  bool _recording = false;
  int _recordElapsed = 0;
  String? _voiceClipPath;
  bool _transcribing = false;
  double? _transcribeUpload;
  String? _transcript;
  String? _transcriptSource;
  String? _voiceError;

  @override
  void dispose() {
    _recordTimer?.cancel();
    final AudioRecorder? recorder = _recorder;
    _recorder = null;
    if (recorder != null) {
      recorder
          .stop()
          .catchError((Object _) => null)
          .whenComplete(() => recorder.dispose().catchError((Object _) {}));
    }
    _instructionController.dispose();
    _client.close();
    super.dispose();
  }

  /// Opens the Android system video picker and appends the chosen
  /// clip. Cancellation is quiet; failures are shown inline. Clips
  /// are never uploaded anywhere by picking them. One clip is the
  /// simplest valid project; up to 8 form the story in order.
  Future<void> _pickVideo() async {
    if (_picking) {
      return;
    }
    if (_videos.length >= 8) {
      setState(
        () => _pickError = 'A project holds at most 8 clips. Remove one '
            'to choose another.',
      );
      return;
    }
    setState(() {
      _picking = true;
      _pickError = null;
    });
    try {
      final XFile? file = await ImagePicker().pickVideo(
        source: ImageSource.gallery,
      );
      if (!mounted) {
        return;
      }
      setState(() {
        _picking = false;
        if (file != null) {
          _videos.add(VideoSelection(file.path));
          _validationError = null;
        }
      });
    } catch (_) {
      if (!mounted) {
        return;
      }
      setState(() {
        _picking = false;
        _pickError = 'Could not open the video picker. Please try again.';
      });
    }
  }

  void _removeVideoAt(int index) {
    setState(() {
      if (index >= 0 && index < _videos.length) {
        _durations.remove(_videos[index].path);
        _videos.removeAt(index);
        _validationError = null;
      }
    });
  }

  String _clipDurationLabel(String path) {
    final Duration? duration = _durations[path];
    if (duration == null) {
      return 'Reading duration…';
    }
    return 'Duration ${formatClipDuration(duration)}';
  }

  Future<void> _toggleRecord() async {
    if (_transcribing) {
      return;
    }
    if (_recording) {
      await _stopRecording();
      return;
    }
    final AudioRecorder recorder = _recorder ??= AudioRecorder();
    setState(() => _voiceError = null);
    bool allowed = false;
    try {
      allowed = await recorder.hasPermission();
    } catch (_) {
      allowed = false;
    }
    if (!allowed) {
      if (mounted) {
        setState(
          () => _voiceError =
              'Microphone permission denied. You can type '
              'your instruction instead.',
        );
      }
      return;
    }
    final Directory tempDir = await getTemporaryDirectory();
    if (!tempDir.existsSync()) {
      tempDir.createSync(recursive: true);
    }
    final String path =
        '${tempDir.path}/voice_${DateTime.now().millisecondsSinceEpoch}.m4a';
    try {
      await recorder.start(
        const RecordConfig(encoder: AudioEncoder.aacLc, numChannels: 1),
        path: path,
      );
    } catch (_) {
      if (mounted) {
        setState(() => _voiceError = 'Could not start recording.');
      }
      return;
    }
    _recordTimer?.cancel();
    _recordTimer = Timer.periodic(const Duration(seconds: 1), (_) {
      if (mounted) {
        setState(() => _recordElapsed += 1);
      }
    });
    setState(() {
      _recording = true;
      _recordElapsed = 0;
      _voiceClipPath = path;
      _transcript = null;
      _transcriptSource = null;
    });
  }

  Future<void> _stopRecording() async {
    _recordTimer?.cancel();
    final AudioRecorder? recorder = _recorder;
    String? path;
    if (recorder != null) {
      try {
        path = await recorder.stop();
      } catch (_) {
        path = null;
      }
    }
    if (!mounted) {
      return;
    }
    setState(() {
      _recording = false;
      if (path != null && path.isNotEmpty) {
        _voiceClipPath = path;
      }
    });
  }

  Future<void> _transcribeClip(String clipPath, String mode) async {
    setState(() {
      _transcribing = true;
      _transcribeUpload = null;
      _voiceError = null;
      _transcript = null;
      _transcriptSource = null;
    });
    try {
      final Map<String, dynamic> upload = await _client.uploadFile(
        clipPath,
        onProgress: (int sent, int total) {
          if (mounted && total > 0) {
            setState(() => _transcribeUpload = sent / total);
          }
        },
      );
      final Map<String, dynamic> json = await _client.postJson(
        '/api/transcribe',
        <String, dynamic>{
          'file_id': upload['file_id'],
          'mode': mode,
          'languages': <String>['Tamil', 'English'],
        },
        timeout: const Duration(minutes: 5),
      );
      if (!mounted) {
        return;
      }
      setState(() {
        _transcript = json['text'] as String? ?? '';
        _transcriptSource = mode == 'PUSH_TO_TALK' ? 'voice' : 'video';
        _transcribing = false;
      });
    } on BackendException catch (error) {
      if (!mounted) {
        return;
      }
      setState(() {
        _voiceError = error.message;
        _transcribing = false;
      });
    }
  }

  EditSetup get _setup => EditSetup(
    instruction: _instructionController.text,
    style: _style,
    aspectRatio: _aspectRatio,
  );

  void _applyWorkspaceChanges(EditSetup updated) {
    if (_instructionController.text != updated.instruction) {
      _instructionController.text = updated.instruction;
    }
    setState(() {
      _style = updated.style;
      _aspectRatio = updated.aspectRatio;
    });
  }

  void _reviewSetup() {
    if (_videos.isEmpty) {
      setState(() => _validationError = 'Select a video to continue.');
      return;
    }
    if (_instructionController.text.trim().isEmpty) {
      setState(
        () => _validationError =
            'Describe how you would like to edit this video.',
      );
      return;
    }
    setState(() => _validationError = null);
    Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) => WorkspaceScreen(
          video: _videos.first,
          clips: List<VideoSelection>.of(_videos),
          setup: _setup,
          onChanged: _applyWorkspaceChanges,
        ),
      ),
    );
  }

  String get _elapsedLabel {
    final int minutes = _recordElapsed ~/ 60;
    final int seconds = _recordElapsed % 60;
    return '$minutes:${seconds.toString().padLeft(2, '0')}';
  }

  @override
  Widget build(BuildContext context) {
    final TextTheme text = Theme.of(context).textTheme;
    final List<VideoSelection> videos = _videos;
    final String? validationError = _validationError;
    final String? pickError = _pickError;

    return Scaffold(
      appBar: AppBar(title: const Text('Create Video')),
      body: SafeArea(
        child: SingleChildScrollView(
          padding: const EdgeInsets.all(20),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: <Widget>[
              FadeIn(
                child: SectionCard(
                  title: videos.length > 1
                      ? 'Your clips (${videos.length})'
                      : 'Your clip',
                  icon: Icons.video_library_outlined,
                  helper: videos.isEmpty
                      ? 'Opens your device gallery. Videos stay on this phone.'
                      : 'Main clips play in order to form the story. You can '
                            'reorder them on the next screen.',
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: <Widget>[
                      for (int index = 0; index < videos.length; index++)
                        Padding(
                          padding: const EdgeInsets.only(bottom: 8),
                          child: Row(
                            children: <Widget>[
                              CircleAvatar(
                                radius: 13,
                                child: Text(
                                  '${index + 1}',
                                  style: text.labelLarge,
                                ),
                              ),
                              const SizedBox(width: 8),
                              Expanded(
                                child: Column(
                                  crossAxisAlignment:
                                      CrossAxisAlignment.start,
                                  children: <Widget>[
                                    Text(
                                      videos[index].fileName,
                                      style: text.titleSmall,
                                      maxLines: 2,
                                      overflow: TextOverflow.ellipsis,
                                    ),
                                    Text(
                                      _clipDurationLabel(videos[index].path),
                                      style: text.bodySmall,
                                    ),
                                  ],
                                ),
                              ),
                              IconButton(
                                tooltip: 'Remove clip ${index + 1}',
                                icon: const Icon(
                                  Icons.delete_outline,
                                  size: 20,
                                ),
                                onPressed: () => _removeVideoAt(index),
                              ),
                            ],
                          ),
                        ),
                      if (videos.isNotEmpty) ...<Widget>[
                        AnimatedSwitcher(
                          duration: const Duration(milliseconds: 300),
                          child: VideoPreviewPlayer(
                            key: ValueKey<String>(videos.first.path),
                            videoPath: videos.first.path,
                            onDurationReady: (Duration duration) {
                              if (mounted) {
                                setState(
                                  () => _durations[videos.first.path] =
                                      duration,
                                );
                              }
                            },
                          ),
                        ),
                        const SizedBox(height: 12),
                      ],
                      OutlinedButton.icon(
                        onPressed: _picking ? null : _pickVideo,
                        icon: _picking
                            ? const SizedBox(
                                width: 18,
                                height: 18,
                                child: CircularProgressIndicator(
                                  strokeWidth: 2,
                                ),
                              )
                            : const Icon(Icons.add_photo_alternate_outlined),
                        label: Text(
                          _picking
                              ? 'Opening gallery…'
                              : videos.isEmpty
                              ? 'Select video'
                              : 'Add another clip (${videos.length}/8)',
                        ),
                      ),
                      if (pickError != null) ...<Widget>[
                        const SizedBox(height: 10),
                        _InlineError(message: pickError),
                      ],
                    ],
                  ),
                ),
              ),
              const SizedBox(height: 16),
              SectionCard(
                title: 'Voice instruction (optional)',
                icon: Icons.mic_outlined,
                helper:
                    'Tamil and English, including mixed speech. '
                    'Recordings upload only when you tap Transcribe.',
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: <Widget>[
                    Row(
                      children: <Widget>[
                        Expanded(
                          child: OutlinedButton.icon(
                            onPressed: _transcribing ? null : _toggleRecord,
                            icon: Icon(
                              _recording
                                  ? Icons.stop_circle_outlined
                                  : Icons.mic_outlined,
                              color: _recording ? AppColors.danger : null,
                            ),
                            label: Text(
                              _recording
                                  ? 'Stop ($_elapsedLabel)'
                                  : 'Record instruction',
                            ),
                          ),
                        ),
                      ],
                    ),
                    if (_voiceClipPath != null && !_recording) ...<Widget>[
                      const SizedBox(height: 10),
                      Row(
                        children: <Widget>[
                          Expanded(
                            child: Text(
                              'Clip ready. Tap Transcribe to convert it '
                              'to text.',
                              style: text.bodySmall,
                            ),
                          ),
                          TextButton.icon(
                            onPressed: _transcribing
                                ? null
                                : () {
                                    setState(() {
                                      _voiceClipPath = null;
                                      _transcript = null;
                                    });
                                  },
                            icon: const Icon(Icons.delete_outline, size: 18),
                            label: const Text('Discard'),
                          ),
                        ],
                      ),
                      const SizedBox(height: 4),
                      PrimaryButton(
                        label: _transcribing && _transcriptSource == null
                            ? 'Transcribing…'
                            : 'Transcribe',
                        icon: Icons.transcribe_outlined,
                        onPressed: _transcribing
                            ? null
                            : () => _transcribeClip(
                                _voiceClipPath!,
                                'PUSH_TO_TALK',
                              ),
                      ),
                    ],
                    if (videos.isNotEmpty) ...<Widget>[
                      const SizedBox(height: 10),
                      OutlinedButton.icon(
                        onPressed: _transcribing
                            ? null
                            : () => _transcribeClip(
                                  videos.first.path,
                                  'ENDPOINTING',
                                ),
                        icon: const Icon(Icons.subtitles_outlined),
                        label: const Text('Transcribe video audio'),
                      ),
                    ],
                    if (_transcribing) ...<Widget>[
                      const SizedBox(height: 12),
                      if (_transcribeUpload != null)
                        LinearProgressIndicator(value: _transcribeUpload)
                      else
                        const LinearProgressIndicator(),
                      const SizedBox(height: 8),
                      Text('Working…', style: text.bodySmall),
                    ],
                    if (_transcript != null &&
                        _transcript!.isNotEmpty) ...<Widget>[
                      const SizedBox(height: 12),
                      Container(
                        padding: const EdgeInsets.all(12),
                        decoration: BoxDecoration(
                          color: AppColors.surfaceHigh,
                          borderRadius: BorderRadius.circular(12),
                        ),
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.stretch,
                          children: <Widget>[
                            Text(
                              _transcriptSource == 'video'
                                  ? 'Video transcript'
                                  : 'Voice transcript',
                              style: text.titleSmall,
                            ),
                            const SizedBox(height: 4),
                            Text(_transcript!, style: text.bodyMedium),
                            const SizedBox(height: 8),
                            Align(
                              alignment: Alignment.centerRight,
                              child: TextButton.icon(
                                onPressed: () {
                                  _instructionController.text = _transcript!;
                                },
                                icon: const Icon(
                                  Icons.drive_file_move_outline,
                                  size: 18,
                                ),
                                label: const Text('Use as instruction'),
                              ),
                            ),
                          ],
                        ),
                      ),
                    ],
                    if (_voiceError != null) ...<Widget>[
                      const SizedBox(height: 10),
                      _InlineError(message: _voiceError!),
                    ],
                  ],
                ),
              ),
              const SizedBox(height: 16),
              SectionCard(
                title: 'Describe your edit',
                icon: Icons.edit_outlined,
                child: TextField(
                  controller: _instructionController,
                  minLines: 3,
                  maxLines: 5,
                  textCapitalization: TextCapitalization.sentences,
                  decoration: const InputDecoration(
                    labelText: 'How would you like to edit this video?',
                    hintText:
                        'e.g. Cut the slow intro and keep the best moments.',
                  ),
                ),
              ),
              const SizedBox(height: 16),
              SectionCard(
                title: 'Style',
                icon: Icons.palette_outlined,
                helper:
                    'Tap again to clear. A requested style — the preview still shows the original video.',
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: <Widget>[
                    OptionChips<EditStyle>(
                      options: EditStyle.values,
                      selected: _style,
                      onSelected: (EditStyle? style) =>
                          setState(() => _style = style),
                      labelOf: (EditStyle style) => style.label,
                      iconOf: (EditStyle style) => style.icon,
                    ),
                    if (_style != null) ...<Widget>[
                      const SizedBox(height: 6),
                      Text(
                        '${_style!.label}: ${_style!.effect} Clearing it '
                        'removes its effect.',
                      ),
                    ],
                  ],
                ),
              ),
              const SizedBox(height: 16),
              SectionCard(
                title: 'Aspect ratio',
                icon: Icons.crop_outlined,
                helper:
                    'Tap again to clear — cleared means Original. The preview still shows the original video.',
                child: OptionChips<AspectRatioOption>(
                  options: AspectRatioOption.values,
                  selected: _aspectRatio,
                  onSelected: (AspectRatioOption? option) =>
                      setState(() => _aspectRatio = option),
                  labelOf: (AspectRatioOption option) => option.label,
                  iconOf: (AspectRatioOption option) => option.icon,
                ),
              ),
              if (validationError != null) ...<Widget>[
                const SizedBox(height: 16),
                _InlineError(message: validationError),
              ],
              const SizedBox(height: 20),
              PrimaryButton(
                label: 'Review Setup',
                icon: Icons.arrow_forward,
                onPressed: _reviewSetup,
              ),
              const SizedBox(height: 12),
            ],
          ),
        ),
      ),
    );
  }
}

class _InlineError extends StatelessWidget {
  final String message;

  const _InlineError({required this.message});

  @override
  Widget build(BuildContext context) {
    final TextTheme text = Theme.of(context).textTheme;
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        const Icon(Icons.error_outline, size: 20, color: AppColors.danger),
        const SizedBox(width: 8),
        Expanded(
          child: Text(
            message,
            style: text.bodyMedium?.copyWith(color: AppColors.danger),
          ),
        ),
      ],
    );
  }
}
