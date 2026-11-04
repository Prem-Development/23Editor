import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:editor_23/models/edit_setup.dart';
import 'package:editor_23/models/editing_plan.dart';
import 'package:editor_23/models/project.dart';
import 'package:editor_23/models/video_selection.dart';
import 'package:editor_23/screens/status_screen.dart';
import 'package:editor_23/services/backend_client.dart';
import 'package:editor_23/services/project_store.dart';
import 'package:editor_23/theme/app_theme.dart';
import 'package:editor_23/utils/duration_format.dart';
import 'package:editor_23/widgets/brief_sheet.dart';
import 'package:editor_23/widgets/option_chips.dart';
import 'package:editor_23/widgets/primary_button.dart';
import 'package:editor_23/widgets/section_card.dart';
import 'package:editor_23/widgets/video_preview_player.dart';
import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:gal/gal.dart';
import 'package:image_picker/image_picker.dart';
import 'package:path_provider/path_provider.dart';
import 'package:share_plus/share_plus.dart';

enum _JobStage { upload, plan, render, prepare }

enum _StageState { idle, active, done, failed }

extension on _JobStage {
  String get label => switch (this) {
    _JobStage.upload => 'Uploading',
    _JobStage.plan => 'Planning',
    _JobStage.render => 'Rendering',
    _JobStage.prepare => 'Preparing output',
  };
}

class _CaptionLine {
  final double start;
  final double end;
  final TextEditingController controller;

  _CaptionLine({required this.start, required this.end, required String text})
    : controller = TextEditingController(text: text);
}

class _SfxPick {
  final String asset;
  final double at;

  const _SfxPick(this.asset, this.at);
}

/// One user-chosen kept or removed interval in source-video seconds.
/// The backend joins keeps in row order; removes cut their ranges out.
class _CutRange {
  final bool keep;
  final double start;
  final double end;

  const _CutRange(this.keep, this.start, this.end);
}

/// One main clip slot in story order. devicePath is the local file
/// (re-uploaded when the server copy is gone); fileId the confirmed
/// server id; offset the clip's start on the joined timeline, set by
/// /api/join. Media fields fill in from the upload response.
class _ClipSlot {
  String devicePath;
  String? fileId;
  String? name;
  double? duration;
  int? width;
  int? height;
  double? fps;
  bool? hasAudio;
  double offset = 0;
  String? thumbPath;

  _ClipSlot({
    required this.devicePath,
    this.fileId,
    this.name,
    this.duration,
    this.width,
    this.height,
    this.fps,
    this.hasAudio,
  });

  String get displayName {
    final String? named = name;
    if (named != null && named.isNotEmpty) {
      return named;
    }
    return VideoSelection(devicePath).fileName;
  }
}

/// One image attached to the edit. kind is cover (export-only, never
/// rendered into the video), intro, outro, or banner. fileId is the
/// server-confirmed asset id; path is the device-local copy used for
/// previews and cover export. Attachments saved before upload-first
/// flow carry a path only and are uploaded at render time.
class _ImageAttachment {
  final String kind;
  final String path;
  final String? fileId;
  final String? name;
  final double? duration;
  final double? start;
  final double? end;
  final String? position;
  final double? size;

  const _ImageAttachment({
    required this.kind,
    required this.path,
    this.fileId,
    this.name,
    this.duration,
    this.start,
    this.end,
    this.position,
    this.size,
  });

  String describe() {
    final String tag = name?.isNotEmpty == true ? ' ($name)' : '';
    switch (kind) {
      case 'cover':
        return 'Cover image$tag (saved beside the video)';
      case 'intro':
        return '${duration?.toStringAsFixed(1) ?? '?'}s intro image$tag';
      case 'outro':
        return '${duration?.toStringAsFixed(1) ?? '?'}s outro image$tag';
      case 'banner':
        return 'Banner ${start?.toStringAsFixed(1) ?? '?'}–'
            '${end?.toStringAsFixed(1) ?? '?'}s of the edit '
            '(${position ?? '?'})$tag';
      default:
        return kind;
    }
  }
}

/// One supporting-video placement. [at] is edited-output seconds as the
/// creator sees it (for insert: converted to content seconds at render
/// time by subtracting the intro). [sourceAt]/[phrase] re-resolve the
/// position from the current plan when the placement came from a spoken
/// phrase, so cuts and reordering stay correct.
class _SupportPlacement {
  final String kind;
  final String fileId;
  final String? devicePath;
  final String? name;
  final double at;
  final double duration;
  final double srcStart;
  final String? position;
  final double? widthFrac;
  final String audio;
  final String? phrase;
  final double? sourceAt;

  const _SupportPlacement({
    required this.kind,
    required this.fileId,
    this.devicePath,
    this.name,
    required this.at,
    required this.duration,
    this.srcStart = 0,
    this.position,
    this.widthFrac,
    this.audio = 'mute',
    this.phrase,
    this.sourceAt,
  });

  String describe() {
    final String tag = name?.isNotEmpty == true ? ' ($name)' : '';
    final String label = switch (kind) {
      'cutaway' => 'Cutaway',
      'pip' => 'Picture-in-picture',
      'insert' => 'Insert clip',
      _ => kind,
    };
    final String when =
        '${at.toStringAsFixed(1)}s of the edit, ${duration.toStringAsFixed(1)}s long';
    final String sound = audio == 'mute' ? 'muted' : audio;
    final String said = phrase?.isNotEmpty == true ? ' at “$phrase”' : '';
    return '$label$tag: $when, $sound$said';
  }
}

/// One spoken-phrase occurrence: source seconds plus the story clip that
/// speaks it.
class _PhraseHit {
  final double start;
  final double end;
  final int clipIndex;
  final String clipName;
  final String text;

  const _PhraseHit({
    required this.start,
    required this.end,
    required this.clipIndex,
    required this.clipName,
    required this.text,
  });
}

/// Immutable copy of one caption line's editable content.
class _CaptionLineSnapshot {
  final double start;
  final double end;
  final String text;

  const _CaptionLineSnapshot(this.start, this.end, this.text);
}

/// Immutable snapshot of every setting a generation may consume.
///
/// Captured once when a plan/render job starts; the plan request, the
/// effective render plan, and the recorded "planned" state all read
/// from the snapshot, never from live widgets. A mid-flight edit
/// therefore cannot leak half-old/half-new settings into a job —
/// [_WorkspaceScreenState._snapshotMatchesCurrent] compares the
/// recorded snapshot against the live settings instead.
class _JobSnapshot {
  final String instruction;
  final EditStyle? style;
  final AspectRatioOption? aspectRatio;
  final ResizeFit fit;
  final CaptionChoice captionChoice;
  final List<_CaptionLineSnapshot> captionLines;
  final String? musicPath;
  final double musicVolumeDb;
  final bool musicDuck;
  final List<_SfxPick> sfxPicks;
  final List<_CutRange> cuts;
  final List<_ImageAttachment> attachments;
  final List<_SupportPlacement> support;
  final Map<String, dynamic>? analysis;
  final bool allowWonder;
  final String extrasSignature;
  final String clipSignature;

  const _JobSnapshot({
    required this.instruction,
    required this.style,
    required this.aspectRatio,
    required this.fit,
    required this.captionChoice,
    required this.captionLines,
    required this.musicPath,
    required this.musicVolumeDb,
    required this.musicDuck,
    required this.sfxPicks,
    required this.cuts,
    required this.attachments,
    required this.support,
    required this.analysis,
    required this.allowWonder,
    required this.extrasSignature,
    required this.clipSignature,
  });
}

/// Step 2 of the flow: review the original clip, generate a real AI editing
/// plan, render it with FFmpeg, preview the render, and export it.
///
/// Every AI action calls the backend for real. When the backend is
/// unreachable, the failure is shown with a link to the Status tab —
/// nothing is simulated.
class WorkspaceScreen extends StatefulWidget {
  final VideoSelection video;
  final EditSetup setup;
  final ValueChanged<EditSetup> onChanged;

  /// Injected backend client (tests). Defaults to a real [BackendClient].
  final BackendClient? client;

  /// Injected share action (tests). Defaults to the platform share sheet.
  final Future<void> Function(String path)? onShare;

  /// Injected audio picker (tests). Defaults to the system file picker.
  final Future<String?> Function()? pickAudio;

  /// Injected image picker (tests). Defaults to the system file picker.
  final Future<String?> Function()? pickImage;

  /// Injected reference-video picker (tests). Defaults to image_picker.
  final Future<String?> Function()? pickReferenceVideo;

  /// Injected main-clip picker (tests). Defaults to image_picker.
  final Future<String?> Function()? pickClip;

  /// Injected supporting-video picker (tests). Defaults to image_picker.
  final Future<String?> Function()? pickSupport;

  /// Injected thumbnail save/share (tests). Defaults to the gallery
  /// saver and the platform share sheet.
  final Future<void> Function(String path)? onSaveThumbnail;
  final Future<void> Function(String path)? onShareThumbnail;

  /// Project storage and restored project for reopening saved work.
  final ProjectStore? projectStore;
  final Project? project;

  /// Main clips in story order. Defaults to [video] (one clip).
  final List<VideoSelection>? clips;

  const WorkspaceScreen({
    super.key,
    required this.video,
    required this.setup,
    required this.onChanged,
    this.client,
    this.onShare,
    this.pickAudio,
    this.pickImage,
    this.pickReferenceVideo,
    this.pickClip,
    this.pickSupport,
    this.onSaveThumbnail,
    this.onShareThumbnail,
    this.projectStore,
    this.project,
    this.clips,
  });

  @override
  State<WorkspaceScreen> createState() => _WorkspaceScreenState();
}

class _WorkspaceScreenState extends State<WorkspaceScreen> {
  late final TextEditingController _instructionController;
  late final TextEditingController _thumbPromptController;
  late final BackendClient _client;
  EditStyle? _style;
  AspectRatioOption? _aspectRatio;
  ResizeFit _fit = ResizeFit.pad;

  /// Explicit Wonder escalation, off by default and reset for each
  /// video. When on, a Spark plan that fails validation is retried
  /// once with Muse Wonder (higher cost).
  bool _allowWonder = false;

  // Staleness guards: any change to the video, instruction, style, or
  // aspect after planning drops the old plan/render and orphans
  // in-flight responses via the generation counters below.
  int _planGen = 0;
  int _renderGen = 0;
  int _jobGen = 0;
  String? _plannedInstruction;
  EditStyle? _plannedStyle;
  AspectRatioOption? _plannedAspect;
  ResizeFit? _plannedFit;
  CaptionChoice? _plannedCaptionChoice;
  String? _plannedExtrasSignature;
  String? _plannedClipSignature;

  String? _fileId;
  bool _uploading = false;
  double _uploadProgress = 0;

  /// Main clips in story order. _fileId is the joined timeline id
  /// (or the single clip's id) once [_ensureUpload] completes.
  List<_ClipSlot> _clips = <_ClipSlot>[];
  int _previewIndex = 0;
  String? _uploadStage;
  String? _clipsError;

  EditingPlan? _plan;
  Map<String, dynamic>? _rawPlan;
  bool _planning = false;
  String? _planError;

  /// Reviewable cut suggestions from the last plan response, and the
  /// keep-ranges under review (null while the review is closed).
  List<Map<String, dynamic>> _suggestions = <Map<String, dynamic>>[];
  List<_CutRange>? _reviewKeeps;
  Set<String> _dismissedSuggestions = <String>{};

  bool _rendering = false;
  String? _renderError;
  String? _renderPath;
  double? _renderDurationSec;
  int? _renderWidth;
  int? _renderHeight;
  String? _renderSar;
  List<String> _renderSteps = const <String>[];
  List<String> _renderWarnings = const <String>[];
  List<String> _renderAdjustments = const <String>[];

  bool _thumbBusy = false;
  String? _thumbError;
  String? _thumbPath;
  bool _thumbSaving = false;
  bool _thumbSharing = false;
  bool _thumbWantsCover = false;
  bool _showCoverTab = true;

  // Video cover from real footage frames.
  bool _coverBusy = false;
  String? _coverError;
  String? _coverPath;
  String? _attachedCoverError;
  late final TextEditingController _coverTitleController;
  late final TextEditingController _coverAtController;
  String _coverPosition = 'bottom';

  // Footage analysis ("What I found in your video").
  bool _analyzing = false;
  String? _analysisJobId;
  String _analysisStage = '';
  Map<String, dynamic>? _analysis;
  String? _analysisError;
  String? _analysisClipSignature;
  int _analysisGen = 0;

  // Reference style video (never rendered as output content).
  String? _referencePath;
  bool _referenceBusy = false;
  String? _referenceError;
  Map<String, dynamic>? _referenceStyle;
  bool _adoptReferenceAspect = true;

  // Caption diagnostics + title fallback.
  Map<String, dynamic>? _captionDiagnosis;
  late final TextEditingController _titleController;

  // Last raw backend failure for the diagnostics expander.
  String? _lastErrorDetail;

  bool _sharing = false;
  String? _connectionError;

  // Staged "Generate edited video" job: real per-stage states, no invented
  // percentages. Retry resumes from the failed stage.
  bool _jobRunning = false;
  String? _jobError;
  Map<_JobStage, _StageState> _jobStages = <_JobStage, _StageState>{
    for (final _JobStage stage in _JobStage.values) stage: _StageState.idle,
  };

  // Measured stage durations (client-side Stopwatch) plus the server's
  // own per-stage breakdown from each response. Reused stages record
  // no new duration; only measured numbers are ever shown.
  Map<_JobStage, Duration> _stageDurations = <_JobStage, Duration>{};
  Set<_JobStage> _stageReused = <_JobStage>{};
  Map<String, int> _planTimings = <String, int>{};
  Map<String, int> _renderTimings = <String, int>{};
  Map<String, int> _transcribeTimings = <String, int>{};
  Duration? _transcribeDuration;
  Duration? _analysisDuration;
  bool _analysisReused = false;
  Duration? _playerPrepDuration;

  // Optional extras attached to the next render.
  Map<String, dynamic>? _capabilities;
  String? _capabilitiesError;
  bool _transcribing = false;
  String? _captionError;
  List<_CaptionLine> _captionLines = <_CaptionLine>[];
  CaptionChoice _captionChoice = CaptionChoice.auto;
  bool _captionsApproximate = false;
  String? _musicPath;
  double _musicVolumeDb = -12;
  bool _musicDuck = true;
  String? _musicError;
  List<_SfxPick> _sfxPicks = <_SfxPick>[];
  String _sfxAsset = 'pop';
  late final TextEditingController _sfxAtController;
  String? _sfxError;

  // User-chosen kept/removed intervals (source-video seconds).
  List<_CutRange> _cuts = <_CutRange>[];
  String? _cutsError;
  bool _cutKeepMode = true;
  late final TextEditingController _cutStartController;
  late final TextEditingController _cutEndController;
  double? _sourceDurationSec;

  // Image attachments: cover (export-only), intro, outro, banner.
  // A picked photo moves Selected -> Uploading -> Uploaded (server
  // file id confirmed) and becomes Attached only when added to the
  // project with valid settings. _pickGen orphans late upload
  // responses after a re-pick, cancel, or removal.
  List<_ImageAttachment> _attachments = <_ImageAttachment>[];
  String? _attachmentError;
  String? _uploadedImagePath;
  String? _uploadedImageName;
  String? _uploadedFileId;
  String? _uploadedDims;
  bool _uploadingImage = false;
  String? _pendingImageError;
  int _pickGen = 0;
  String? _coverImageId;
  String? _thumbImageId;
  String _attachSource = 'cover';
  String _attachKind = 'cover';
  late final TextEditingController _stillDurController;
  late final TextEditingController _bannerStartController;
  late final TextEditingController _bannerEndController;
  String _bannerPosition = 'bottom';
  double _bannerSize = 0.4;

  // Supporting video (B-roll): one uploaded clip plus placements that
  // show it over the story (cutaway/PiP) or inside it (insert). The
  // main story always stays the background; _supportGen orphans late
  // upload responses after a re-pick or removal, like _pickGen.
  String? _supportPath;
  String? _supportName;
  String? _supportFileId;
  double? _supportDuration;
  bool _uploadingSupport = false;
  String? _supportError;
  int _supportGen = 0;
  List<_SupportPlacement> _supportPlacements = <_SupportPlacement>[];
  String? _supportAddError;
  String _supportKind = 'cutaway';
  String _supportAudio = 'mute';
  String _supportPosition = 'top_right';
  double _supportWidthFrac = 0.25;
  late final TextEditingController _supportAtController;
  late final TextEditingController _supportDurationController;
  late final TextEditingController _supportSrcController;
  late final TextEditingController _supportPhraseController;
  List<_PhraseHit> _phraseHits = <_PhraseHit>[];
  String? _phraseMessage;
  int? _phraseChoice;

  // Saved project backing this screen.
  late final ProjectStore _store;
  late final String _projectId;

  @override
  void initState() {
    super.initState();
    _client = widget.client ?? BackendClient();
    _store = widget.projectStore ?? const ProjectStore();
    _instructionController = TextEditingController(
      text: widget.setup.instruction,
    );
    _thumbPromptController = TextEditingController(text: 'Video thumbnail');
    _sfxAtController = TextEditingController();
    _cutStartController = TextEditingController();
    _cutEndController = TextEditingController();
    _coverTitleController = TextEditingController();
    _coverAtController = TextEditingController(text: '1.0');
    _titleController = TextEditingController();
    _stillDurController = TextEditingController(text: '2.0');
    _bannerStartController = TextEditingController();
    _bannerEndController = TextEditingController();
    _supportAtController = TextEditingController();
    _supportDurationController = TextEditingController(text: '2.0');
    _supportSrcController = TextEditingController(text: '0');
    _supportPhraseController = TextEditingController();
    _style = widget.setup.style;
    _aspectRatio = widget.setup.aspectRatio;
    _fit = widget.setup.fit;
    _clips = _initialClips();
    _instructionController.addListener(_publish);
    _loadCapabilities();
    final Project? restored = widget.project;
    if (restored != null) {
      _projectId = restored.id;
      _restoreProject(restored);
    } else {
      _projectId =
          '${DateTime.now().millisecondsSinceEpoch}-${widget.video.fileName.hashCode & 0xffff}';
      _saveProject('draft');
    }
  }

  @override
  void dispose() {
    _instructionController.removeListener(_publish);
    _instructionController.dispose();
    _thumbPromptController.dispose();
    _sfxAtController.dispose();
    _cutStartController.dispose();
    _cutEndController.dispose();
    _coverTitleController.dispose();
    _coverAtController.dispose();
    _titleController.dispose();
    _stillDurController.dispose();
    _bannerStartController.dispose();
    _bannerEndController.dispose();
    _supportAtController.dispose();
    _supportDurationController.dispose();
    _supportSrcController.dispose();
    _supportPhraseController.dispose();
    _analysisGen++;
    for (final _CaptionLine line in _captionLines) {
      line.controller.dispose();
    }
    _client.close();
    super.dispose();
  }

  Future<void> _loadCapabilities() async {
    try {
      final Map<String, dynamic> capabilities = await _client.getCapabilities();
      if (!mounted) {
        return;
      }
      setState(() {
        _capabilities = capabilities;
        _capabilitiesError = null;
      });
    } on BackendException catch (error) {
      // A failed list load is not a dead backend: only connection
      // errors mean unreachable. Either way the section below offers
      // a retry instead of guessing asset names.
      if (!mounted) {
        return;
      }
      setState(() {
        _capabilitiesError = error.isConnectionError ? null : error.message;
      });
    }
  }

  EditSetup get _draft => EditSetup(
    instruction: _instructionController.text,
    style: _style,
    aspectRatio: _aspectRatio,
    fit: _fit,
  );

  /// Opening clip list: the restored project's clips when present,
  /// else the clips handed over by the Create screen, else the
  /// single video (which keeps every old project and test working).
  List<_ClipSlot> _initialClips() {
    final List<Map<String, dynamic>>? saved = widget.project?.clips;
    if (saved != null && saved.isNotEmpty) {
      final List<_ClipSlot> restored = <_ClipSlot>[
        for (final Map<String, dynamic> item in saved)
          if ((item['devicePath'] as String? ?? '').isNotEmpty)
            _ClipSlot(
              devicePath: item['devicePath'] as String? ?? '',
              fileId: item['fileId'] as String?,
              name: item['name'] as String?,
              duration: (item['duration'] as num?)?.toDouble(),
              width: (item['width'] as num?)?.toInt(),
              height: (item['height'] as num?)?.toInt(),
              fps: (item['fps'] as num?)?.toDouble(),
              hasAudio: item['hasAudio'] as bool?,
            ),
      ];
      if (restored.isNotEmpty) {
        return restored;
      }
    }
    final List<VideoSelection>? handed = widget.clips;
    if (handed != null && handed.isNotEmpty) {
      return <_ClipSlot>[
        for (final VideoSelection clip in handed)
          _ClipSlot(devicePath: clip.path),
      ];
    }
    return <_ClipSlot>[_ClipSlot(devicePath: widget.video.path)];
  }

  /// Identity of the current clip set: server ids when known, else
  /// device paths. Any change invalidates upload, plan, and render.
  String _clipSignature() {
    return _clips
        .map((_ClipSlot clip) => clip.fileId ?? clip.devicePath)
        .join(',');
  }

  int get _maxClips {
    final Map<String, dynamic>? caps = _capabilities;
    final Map<String, dynamic>? join =
        caps?['join_limits'] as Map<String, dynamic>?;
    return (join?['max_clips'] as num?)?.toInt() ?? 8;
  }

  /// Header line: one clip keeps the historical "name · 13.0s" shape;
  /// several clips list names with the joined total.
  String _clipsHeaderLine() {
    if (_clips.length == 1) {
      final _ClipSlot clip = _clips.single;
      final double? duration = _sourceDurationSec ?? clip.duration;
      return duration == null
          ? clip.displayName
          : '${clip.displayName} · ${duration.toStringAsFixed(1)}s';
    }
    final String names = _clips
        .take(2)
        .map((_ClipSlot clip) => clip.displayName)
        .join(', ');
    final String more = _clips.length > 2
        ? ' + ${_clips.length - 2} more'
        : '';
    double total = _sourceDurationSec ?? 0;
    if (_sourceDurationSec == null) {
      for (final _ClipSlot clip in _clips) {
        total += clip.duration ?? 0;
      }
    }
    return '$names$more · ${total.toStringAsFixed(1)}s';
  }

  String get _previewPath {
    if (_clips.isEmpty) {
      return widget.video.path;
    }
    final int index = _previewIndex.clamp(0, _clips.length - 1);
    return _clips[index].devicePath;
  }

  /// Any clip-set change drops the join id and retires plan/render.
  void _clipsChanged() {
    _fileId = null;
    _sourceDurationSec = null;
    _analysis = null;
    _analysisError = null;
    if (_clips.isEmpty) {
      _clips = <_ClipSlot>[_ClipSlot(devicePath: widget.video.path)];
    }
    _previewIndex = _previewIndex.clamp(0, _clips.length - 1);
    _publish();
  }

  Future<void> _addClip() async {
    if (_clips.length >= _maxClips) {
      setState(
        () => _clipsError =
            'This project already has the maximum of $_maxClips clips.',
      );
      return;
    }
    setState(() => _clipsError = null);
    try {
      final Future<String?> Function()? pick = widget.pickClip;
      final String? path = pick != null
          ? await pick()
          : (await ImagePicker().pickVideo(source: ImageSource.gallery))
                ?.path;
      if (!mounted) {
        return;
      }
      if (path == null || path.isEmpty) {
        return;
      }
      if (!File(path).existsSync()) {
        setState(
          () => _clipsError = 'That clip is not readable on this device.',
        );
        return;
      }
      setState(() => _clips.add(_ClipSlot(devicePath: path)));
      _clipsChanged();
    } catch (_) {
      if (mounted) {
        setState(() => _clipsError = 'Could not open the video picker.');
      }
    }
  }

  Future<void> _replaceClip(int index) async {
    setState(() => _clipsError = null);
    try {
      final Future<String?> Function()? pick = widget.pickClip;
      final String? path = pick != null
          ? await pick()
          : (await ImagePicker().pickVideo(source: ImageSource.gallery))
                ?.path;
      if (!mounted) {
        return;
      }
      if (path == null || path.isEmpty) {
        return;
      }
      if (!File(path).existsSync()) {
        setState(
          () => _clipsError = 'That clip is not readable on this device.',
        );
        return;
      }
      setState(() {
        _clips[index] = _ClipSlot(devicePath: path);
      });
      _clipsChanged();
    } catch (_) {
      if (mounted) {
        setState(() => _clipsError = 'Could not open the video picker.');
      }
    }
  }

  void _removeClip(int index) {
    if (_clips.length <= 1) {
      setState(
        () => _clipsError = 'Keep at least one main clip in the project.',
      );
      return;
    }
    setState(() {
      _clips.removeAt(index);
      _clipsError = null;
    });
    _clipsChanged();
  }

  void _moveClip(int index, int direction) {
    final int target = index + direction;
    if (target < 0 || target >= _clips.length) {
      return;
    }
    setState(() {
      final _ClipSlot clip = _clips.removeAt(index);
      _clips.insert(target, clip);
      if (_previewIndex == index) {
        _previewIndex = target;
      }
    });
    _clipsChanged();
  }

  /// Best-effort clip thumbnail from the server copy. Decorative:
  /// failures stay silent and the row keeps its placeholder icon.
  Future<void> _fetchClipThumb(_ClipSlot clip) async {
    final String? fileId = clip.fileId;
    if (fileId == null || clip.thumbPath != null) {
      return;
    }
    try {
      final double duration = clip.duration ?? 0;
      final double at = duration > 2 ? 1.0 : duration / 2;
      final Map<String, dynamic> frame = await _client.postJson(
        '/api/frame',
        <String, dynamic>{'file_id': fileId, 't': at},
      );
      final Directory temp = await getTemporaryDirectory();
      final String dest =
          '${temp.path}/clip_${DateTime.now().microsecondsSinceEpoch}.jpg';
      await _client.downloadToFile(
        frame['url'] as String? ?? '',
        dest,
      );
      if (mounted && File(dest).existsSync()) {
        setState(() => clip.thumbPath = dest);
      }
    } catch (_) {
      // Thumbnails never block editing.
    }
  }

  List<Widget> _buildClipRows(TextTheme text) {
    return <Widget>[
      for (int index = 0; index < _clips.length; index++)
        _buildClipRow(text, index),
    ];
  }

  Widget _buildClipRow(TextTheme text, int index) {
    final _ClipSlot clip = _clips[index];
    final bool selected = index == _previewIndex.clamp(0, _clips.length - 1);
    final List<String> meta = <String>[];
    final double? duration = clip.duration;
    if (duration != null) {
      meta.add('${duration.toStringAsFixed(1)}s');
    }
    if (clip.width != null && clip.height != null) {
      meta.add('${clip.width}×${clip.height}');
    }
    if (clip.hasAudio == false) {
      meta.add('silent');
    }
    if (_clips.length > 1 && _fileId != null) {
      meta.add('starts at ${clip.offset.toStringAsFixed(1)}s of the story');
    }
    meta.add(clip.fileId != null ? 'Uploaded' : 'On this device');
    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: InkWell(
        key: ValueKey<String>('clipRow_$index'),
        onTap: () => setState(() => _previewIndex = index),
        child: Container(
          padding: const EdgeInsets.all(8),
          decoration: BoxDecoration(
            border: Border.all(
              color: selected
                  ? Theme.of(context).colorScheme.primary
                  : Theme.of(context).dividerColor,
              width: selected ? 2 : 1,
            ),
            borderRadius: BorderRadius.circular(12),
          ),
          child: Row(
            children: <Widget>[
              CircleAvatar(
                radius: 14,
                child: Text(
                  '${index + 1}',
                  style: text.labelLarge,
                ),
              ),
              const SizedBox(width: 8),
              ClipRRect(
                borderRadius: BorderRadius.circular(8),
                child: clip.thumbPath != null
                    ? Image.file(
                        File(clip.thumbPath!),
                        width: 56,
                        height: 56,
                        fit: BoxFit.cover,
                        errorBuilder: (_, _, _) => const SizedBox(
                          width: 56,
                          height: 56,
                          child: Icon(Icons.movie_outlined),
                        ),
                      )
                    : const SizedBox(
                        width: 56,
                        height: 56,
                        child: Icon(Icons.movie_outlined),
                      ),
              ),
              const SizedBox(width: 8),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: <Widget>[
                    Text(
                      clip.displayName,
                      style: text.titleSmall,
                      overflow: TextOverflow.ellipsis,
                    ),
                    Text(meta.join(' · '), style: text.bodySmall),
                  ],
                ),
              ),
              PopupMenuButton<String>(
                key: ValueKey<String>('clipMenu_$index'),
                tooltip: 'Clip ${index + 1} options',
                onSelected: (String value) {
                  switch (value) {
                    case 'preview':
                      setState(() => _previewIndex = index);
                    case 'up':
                      _moveClip(index, -1);
                    case 'down':
                      _moveClip(index, 1);
                    case 'replace':
                      _replaceClip(index);
                    case 'remove':
                      _removeClip(index);
                  }
                },
                itemBuilder: (BuildContext context) => <PopupMenuEntry<String>>[
                  const PopupMenuItem<String>(
                    value: 'preview',
                    child: Text('Preview'),
                  ),
                  if (_clips.length > 1) ...<PopupMenuEntry<String>>[
                    const PopupMenuItem<String>(
                      value: 'up',
                      child: Text('Move earlier'),
                    ),
                    const PopupMenuItem<String>(
                      value: 'down',
                      child: Text('Move later'),
                    ),
                  ],
                  const PopupMenuItem<String>(
                    value: 'replace',
                    child: Text('Replace'),
                  ),
                  if (_clips.length > 1)
                    const PopupMenuItem<String>(
                      value: 'remove',
                      child: Text('Remove'),
                    ),
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }

  /// Signature of the attached extras. Any change — caption lines or
  /// choice, music file/volume/ducking, effects, cuts, image
  /// attachments, fit, Wonder escalation — invalidates the previous
  /// plan and output through [_publish].
  String _extrasSignature() {
    final StringBuffer buffer = StringBuffer()
      ..write(_captionChoice.name)
      ..write('|')
      ..write(_allowWonder ? 'w|' : '|');
    for (final _CutRange cut in _cuts) {
      buffer.write(
        '${cut.keep ? 'k' : 'r'},${cut.start.toStringAsFixed(3)},'
        '${cut.end.toStringAsFixed(3)}|',
      );
    }
    for (final _ImageAttachment attachment in _attachments) {
      buffer.write(
        '${attachment.kind},${attachment.path},${attachment.fileId ?? ''},'
        '${attachment.duration?.toStringAsFixed(3) ?? ''},'
        '${attachment.start?.toStringAsFixed(3) ?? ''},'
        '${attachment.end?.toStringAsFixed(3) ?? ''},'
        '${attachment.position ?? ''},'
        '${attachment.size?.toStringAsFixed(3) ?? ''}|',
      );
    }
    for (final _SupportPlacement placement in _supportPlacements) {
      buffer.write(
        '${placement.kind},${placement.fileId},'
        '${placement.at.toStringAsFixed(3)},'
        '${placement.duration.toStringAsFixed(3)},'
        '${placement.srcStart.toStringAsFixed(3)},'
        '${placement.position ?? ''},'
        '${placement.widthFrac?.toStringAsFixed(3) ?? ''},'
        '${placement.audio},${placement.sourceAt?.toStringAsFixed(3) ?? ''}|',
      );
    }
    for (final _CaptionLine line in _captionLines) {
      buffer.write(
        '${line.start.toStringAsFixed(3)},${line.end.toStringAsFixed(3)},'
        '${line.controller.text}|',
      );
    }
    buffer.write(
      '${_musicPath ?? ''},${_musicVolumeDb.toStringAsFixed(2)},'
      '$_musicDuck|',
    );
    for (final _SfxPick pick in _sfxPicks) {
      buffer.write('${pick.asset},${pick.at.toStringAsFixed(3)}|');
    }
    return buffer.toString();
  }

  /// Freezes the current settings for one generation job. The extras
  /// signature is computed from the same live state in the same instant,
  /// so it always matches the captured values.
  _JobSnapshot _captureSnapshot() {
    final Map<String, dynamic>? analysis = _currentAnalysis;
    return _JobSnapshot(
      instruction: _instructionController.text,
      style: _style,
      aspectRatio: _aspectRatio,
      fit: _fit,
      captionChoice: _captionChoice,
      captionLines: <_CaptionLineSnapshot>[
        for (final _CaptionLine line in _captionLines)
          _CaptionLineSnapshot(line.start, line.end, line.controller.text),
      ],
      musicPath: _musicPath,
      musicVolumeDb: _musicVolumeDb,
      musicDuck: _musicDuck,
      sfxPicks: List<_SfxPick>.of(_sfxPicks),
      cuts: List<_CutRange>.of(_cuts),
      attachments: List<_ImageAttachment>.of(_attachments),
      support: List<_SupportPlacement>.of(_supportPlacements),
      analysis: analysis == null
          ? null
          : jsonDecode(jsonEncode(analysis)) as Map<String, dynamic>,
      allowWonder: _allowWonder,
      extrasSignature: _extrasSignature(),
      clipSignature: _clipSignature(),
    );
  }

  void _publish() {
    widget.onChanged(_draft);
    _invalidateStaleResults();
    _saveProject(_plan == null ? 'draft' : 'planned');
  }

  void _restoreProject(Project project) {
    _fileId = project.fileId;
    // Restore only this project's deliberate choices — never another
    // video's extras.
    _fit = project.fit;
    _captionChoice = switch (project.captionChoice) {
      'on' => CaptionChoice.on,
      'off' => CaptionChoice.off,
      _ => CaptionChoice.auto,
    };
    _captionLines = <_CaptionLine>[
      for (final Map<String, dynamic> line in project.captionLines)
        _CaptionLine(
          start: (line['start'] as num?)?.toDouble() ?? 0,
          end: (line['end'] as num?)?.toDouble() ?? 0,
          text: line['text'] as String? ?? '',
        ),
    ];
    for (final _CaptionLine line in _captionLines) {
      line.controller.addListener(_publish);
    }
    _musicPath = project.musicPath;
    _musicVolumeDb = project.musicVolumeDb;
    _musicDuck = project.musicDuck;
    _sfxPicks = <_SfxPick>[
      for (final Map<String, dynamic> pick in project.sfxPicks)
        if ((pick['asset'] as String? ?? '').isNotEmpty)
          _SfxPick(
            pick['asset'] as String? ?? '',
            (pick['at'] as num?)?.toDouble() ?? 0,
          ),
    ];
    _cuts = <_CutRange>[
      for (final Map<String, dynamic> cut in project.cuts)
        _CutRange(
          cut['keep'] as bool? ?? true,
          (cut['start'] as num?)?.toDouble() ?? 0,
          (cut['end'] as num?)?.toDouble() ?? 0,
        ),
    ];
    if (project.thumbPath != null && File(project.thumbPath!).existsSync()) {
      _thumbPath = project.thumbPath;
    }
    _attachments = <_ImageAttachment>[
      for (final Map<String, dynamic> item in project.attachments)
        if ((item['path'] as String? ?? '').isNotEmpty &&
            const <String>{
              'cover',
              'intro',
              'outro',
              'banner',
            }.contains(item['kind']) &&
            ((item['fileId'] as String? ?? '').isNotEmpty ||
                File(item['path'] as String).existsSync()))
          _ImageAttachment(
            kind: item['kind'] as String? ?? 'cover',
            path: item['path'] as String? ?? '',
            fileId: (item['fileId'] as String?)?.isNotEmpty == true
                ? item['fileId'] as String?
                : null,
            name: item['name'] as String?,
            duration: (item['duration'] as num?)?.toDouble(),
            start: (item['start'] as num?)?.toDouble(),
            end: (item['end'] as num?)?.toDouble(),
            position: item['position'] as String?,
            size: (item['size'] as num?)?.toDouble(),
          ),
    ];
    _supportPlacements = <_SupportPlacement>[
      for (final Map<String, dynamic> item in project.support)
        if (const <String>{
          'cutaway',
          'pip',
          'insert',
        }.contains(item['kind']) &&
            _looksLikeFileId(item['fileId'] as String? ?? '') &&
            (item['at'] as num?) != null &&
            (item['duration'] as num?) != null)
          _SupportPlacement(
            kind: item['kind'] as String? ?? 'cutaway',
            fileId: item['fileId'] as String? ?? '',
            devicePath: item['devicePath'] as String?,
            name: item['name'] as String?,
            at: (item['at'] as num?)?.toDouble() ?? 0,
            duration: (item['duration'] as num?)?.toDouble() ?? 0,
            srcStart: (item['srcStart'] as num?)?.toDouble() ?? 0,
            position: item['position'] as String?,
            widthFrac: (item['widthFrac'] as num?)?.toDouble(),
            audio: item['audio'] as String? ?? 'mute',
            phrase: item['phrase'] as String?,
            sourceAt: (item['sourceAt'] as num?)?.toDouble(),
          ),
    ];
    final Map<String, dynamic>? plan = project.plan;
    if (plan != null) {
      _rawPlan = Map<String, dynamic>.from(plan);
      _plan = EditingPlan.fromJson(
        _rawPlan!,
        project.planModel ?? '',
        const <String>[],
      );
      _plannedInstruction = _instructionController.text;
      _plannedStyle = _style;
      _plannedAspect = _aspectRatio;
      _plannedFit = _fit;
      _plannedCaptionChoice = _captionChoice;
      _plannedExtrasSignature = _extrasSignature();
      _plannedClipSignature = _clipSignature();
    }
    final String? renderPath = project.renderPath;
    if (renderPath != null) {
      if (File(renderPath).existsSync()) {
        _renderPath = renderPath;
        _renderDurationSec = project.renderDurationSec;
        _renderWidth = project.renderWidth;
        _renderHeight = project.renderHeight;
        _renderSteps = project.steps;
      } else {
        _renderError = 'The saved output file is missing from this device.';
      }
    }
  }

  void _saveProject(String status) {
    final Project project = Project(
      id: _projectId,
      name: widget.video.fileName,
      sourcePath: widget.video.path,
      fileName: widget.video.fileName,
      instruction: _instructionController.text,
      style: _style,
      aspectRatio: _aspectRatio,
      fit: _fit,
      fileId: _fileId,
      plan: _rawPlan == null
          ? null
          : (jsonDecode(jsonEncode(_rawPlan)) as Map<String, dynamic>),
      planModel: _plan?.model,
      renderPath: _renderPath,
      renderDurationSec: _renderDurationSec,
      renderWidth: _renderWidth,
      renderHeight: _renderHeight,
      steps: _renderSteps,
      status: status,
      updatedAt: DateTime.now(),
      captionChoice: _captionChoice == CaptionChoice.auto
          ? null
          : _captionChoice.name,
      captionLines: <Map<String, dynamic>>[
        for (final _CaptionLine line in _captionLines)
          <String, dynamic>{
            'start': line.start,
            'end': line.end,
            'text': line.controller.text,
          },
      ],
      musicPath: _musicPath,
      musicVolumeDb: _musicVolumeDb,
      musicDuck: _musicDuck,
      sfxPicks: <Map<String, dynamic>>[
        for (final _SfxPick pick in _sfxPicks)
          <String, dynamic>{'asset': pick.asset, 'at': pick.at},
      ],
      cuts: <Map<String, dynamic>>[
        for (final _CutRange cut in _cuts)
          <String, dynamic>{
            'keep': cut.keep,
            'start': cut.start,
            'end': cut.end,
          },
      ],
      thumbPath: _thumbPath,
      attachments: <Map<String, dynamic>>[
        for (final _ImageAttachment attachment in _attachments)
          <String, dynamic>{
            'kind': attachment.kind,
            'path': attachment.path,
            'fileId': attachment.fileId,
            'name': attachment.name,
            'duration': attachment.duration,
            'start': attachment.start,
            'end': attachment.end,
            'position': attachment.position,
            'size': attachment.size,
          },
      ],
      support: <Map<String, dynamic>>[
        for (final _SupportPlacement placement in _supportPlacements)
          <String, dynamic>{
            'kind': placement.kind,
            'fileId': placement.fileId,
            'devicePath': placement.devicePath,
            'name': placement.name,
            'at': placement.at,
            'duration': placement.duration,
            'srcStart': placement.srcStart,
            'position': placement.position,
            'widthFrac': placement.widthFrac,
            'audio': placement.audio,
            'phrase': placement.phrase,
            'sourceAt': placement.sourceAt,
          },
      ],
      clips: <Map<String, dynamic>>[
        for (final _ClipSlot clip in _clips)
          <String, dynamic>{
            'devicePath': clip.devicePath,
            'fileId': clip.fileId,
            'name': clip.name,
            'duration': clip.duration,
            'width': clip.width,
            'height': clip.height,
            'fps': clip.fps,
            'hasAudio': clip.hasAudio,
          },
      ],
    );
    // Saving must never break editing; failures stay silent.
    unawaited(_persist(project));
  }

  Future<void> _persist(Project project) async {
    try {
      await _store.saveProject(project);
    } catch (_) {
      // Local bookkeeping only; the edit flow continues.
    }
  }

  bool _snapshotMatchesCurrent() {
    return _plannedInstruction == _instructionController.text &&
        _plannedStyle == _style &&
        _plannedAspect == _aspectRatio &&
        _plannedFit == _fit &&
        _plannedCaptionChoice == _captionChoice &&
        _plannedExtrasSignature == _extrasSignature() &&
        _plannedClipSignature == _clipSignature();
  }

  void _invalidateStaleResults() {
    // An edit during an active plan/render/job orphans the in-flight
    // work even when no plan exists yet: the job captured its own
    // snapshot, and anything it returns now belongs to old settings.
    final bool active = _planning || _rendering || _jobRunning;
    if (_plan == null && _renderPath == null && !active) {
      return;
    }
    if (!active && _snapshotMatchesCurrent()) {
      return;
    }
    // Settings changed after planning/rendering: drop the stale plan and
    // render, and orphan any in-flight responses so a late arrival can
    // never reappear as the current result.
    _planGen++;
    _renderGen++;
    _jobGen++;
    setState(() {
      _planning = false;
      _plan = null;
      _rawPlan = null;
      _planError = null;
      _suggestions = <Map<String, dynamic>>[];
      _reviewKeeps = null;
      _dismissedSuggestions = <String>{};
      _plannedInstruction = null;
      _plannedStyle = null;
      _plannedAspect = null;
      _plannedFit = null;
      _plannedCaptionChoice = null;
      _plannedExtrasSignature = null;
      _plannedClipSignature = null;
      _rendering = false;
      _renderPath = null;
      _renderDurationSec = null;
      _renderWidth = null;
      _renderHeight = null;
      _renderSar = null;
      _renderError = null;
      _renderSteps = const <String>[];
      _renderWarnings = const <String>[];
      _renderAdjustments = const <String>[];
      _jobRunning = false;
      _jobError = null;
      _jobStages = <_JobStage, _StageState>{
        for (final _JobStage stage in _JobStage.values) stage: _StageState.idle,
      };
      _stageDurations = <_JobStage, Duration>{};
      _stageReused = <_JobStage>{};
      _planTimings = <String, int>{};
      _renderTimings = <String, int>{};
      _playerPrepDuration = null;
    });
  }

  static List<String> _widgetClipPaths(WorkspaceScreen widget) {
    final List<VideoSelection>? clips = widget.clips;
    if (clips != null && clips.isNotEmpty) {
      return <String>[for (final VideoSelection clip in clips) clip.path];
    }
    return <String>[widget.video.path];
  }

  @override
  void didUpdateWidget(covariant WorkspaceScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    final List<String> oldPaths = _widgetClipPaths(oldWidget);
    final List<String> newPaths = _widgetClipPaths(widget);
    if (oldPaths.length == newPaths.length &&
        oldPaths.asMap().entries.every(
          (MapEntry<int, String> entry) => entry.value == newPaths[entry.key],
        )) {
      return;
    }
    // Different clips invalidate the upload, plan, and render alike.
    _planGen++;
    _renderGen++;
    _jobGen++;
    _pickGen++;
    _clips = <_ClipSlot>[
      for (final String path in newPaths) _ClipSlot(devicePath: path),
    ];
    _previewIndex = 0;
    for (final _CaptionLine line in _captionLines) {
      line.controller.dispose();
    }
    setState(() {
      _fileId = null;
      _clipsError = null;
      _uploadStage = null;
      _planning = false;
      _plan = null;
      _suggestions = <Map<String, dynamic>>[];
      _reviewKeeps = null;
      _dismissedSuggestions = <String>{};
      _rawPlan = null;
      _planError = null;
      _plannedInstruction = null;
      _plannedStyle = null;
      _plannedAspect = null;
      _plannedFit = null;
      _plannedCaptionChoice = null;
      _plannedExtrasSignature = null;
      _plannedClipSignature = null;
      _rendering = false;
      _renderPath = null;
      _renderDurationSec = null;
      _renderWidth = null;
      _renderHeight = null;
      _renderSar = null;
      _renderError = null;
      _renderSteps = const <String>[];
      _renderWarnings = const <String>[];
      _renderAdjustments = const <String>[];
      _jobRunning = false;
      _jobError = null;
      _jobStages = <_JobStage, _StageState>{
        for (final _JobStage stage in _JobStage.values) stage: _StageState.idle,
      };
      // A new video starts with no extras: nothing is inherited.
      _allowWonder = false;
      _captionLines = <_CaptionLine>[];
      _captionChoice = CaptionChoice.auto;
      _captionsApproximate = false;
      _captionError = null;
      _captionDiagnosis = null;
      _musicPath = null;
      _musicError = null;
      _sfxPicks = <_SfxPick>[];
      _sfxError = null;
      _cuts = <_CutRange>[];
      _cutsError = null;
      _cutKeepMode = true;
      _cutStartController.clear();
      _cutEndController.clear();
      _sourceDurationSec = null;
      _thumbPath = null;
      _thumbError = null;
      _coverPath = null;
      _coverError = null;
      _attachments = <_ImageAttachment>[];
      _attachmentError = null;
      _attachedCoverError = null;
      _uploadedImagePath = null;
      _uploadedImageName = null;
      _uploadedFileId = null;
      _uploadedDims = null;
      _uploadingImage = false;
      _pendingImageError = null;
      _coverImageId = null;
      _thumbImageId = null;
      _supportPath = null;
      _supportName = null;
      _supportFileId = null;
      _supportDuration = null;
      _uploadingSupport = false;
      _supportError = null;
      _supportPlacements = <_SupportPlacement>[];
      _supportAddError = null;
      _phraseHits = <_PhraseHit>[];
      _phraseMessage = null;
      _phraseChoice = null;
      _analysis = null;
      _analysisError = null;
      _analysisJobId = null;
      _analysisClipSignature = null;
      _analyzing = false;
      _analysisDuration = null;
      _analysisReused = false;
      _transcribeDuration = null;
      _transcribeTimings = <String, int>{};
      _stageDurations = <_JobStage, Duration>{};
      _stageReused = <_JobStage>{};
      _planTimings = <String, int>{};
      _renderTimings = <String, int>{};
      _playerPrepDuration = null;
      _referencePath = null;
      _referenceStyle = null;
      _referenceError = null;
    });
    _analysisGen++;
  }

  void _noteConnection(String message) {
    setState(() => _connectionError = message);
  }

  /// Routes a connection failure to the right surface.
  ///
  /// Plain unreachables (refused/dns) mark the backend disconnected.
  /// Timeouts are ambiguous — the backend may be busy, e.g. waiting on
  /// the model provider — so a fast /status probe decides: reachable
  /// means the FEATURE timed out (feature error, never the "backend
  /// is not connected" card), unreachable means the backend is gone.
  Future<void> _handleConnectionError(
    BackendException error,
    void Function(String message) setFeatureError,
    String feature,
  ) async {
    if (!error.timedOut) {
      _noteConnection(error.message);
      return;
    }
    bool reachable = false;
    try {
      reachable = await _client.probeReachable();
    } catch (_) {
      reachable = false;
    }
    if (!mounted) {
      return;
    }
    if (reachable) {
      setFeatureError(
        '$feature timed out, but the backend is still reachable. '
        'It may be busy — retry, or check AI Status.',
      );
    } else {
      _noteConnection(error.message);
    }
  }

  /// Records the raw failure for the diagnostics expander (jargon kept
  /// out of the main error line).
  void _noteErrorDetail(String where, BackendException error) {
    if (!mounted) {
      return;
    }
    setState(() {
      _lastErrorDetail =
          '$where: HTTP ${error.statusCode ?? 'no-connection'}: '
          '${error.message}';
    });
  }

  /// Uploads every clip missing a server id, then joins the set
  /// into one timeline. Returns the join id (one clip joins to
  /// itself with no transcode). Reuses confirmed uploads; a stale
  /// server id is re-uploaded from the device file and joined once
  /// more before failing.
  Future<String> _ensureUpload() async {
    final String? existing = _fileId;
    if (existing != null) {
      return existing;
    }
    if (_clips.isEmpty) {
      throw BackendException('Choose at least one main clip first.');
    }
    setState(() {
      _uploading = true;
      _uploadProgress = 0;
      _uploadStage = null;
      _connectionError = null;
      _clipsError = null;
    });
    try {
      for (int index = 0; index < _clips.length; index++) {
        final _ClipSlot clip = _clips[index];
        if (clip.fileId != null) {
          continue;
        }
        if (mounted) {
          setState(() {
            _uploadStage = _clips.length > 1
                ? 'Uploading clip ${index + 1} of ${_clips.length}…'
                : null;
            _uploadProgress = 0;
          });
        }
        await _uploadClip(index);
        unawaited(_fetchClipThumb(_clips[index]));
      }
      if (_clips.length == 1) {
        // One clip is already a timeline: no join request, exactly
        // the historical single-upload flow.
        final String fileId = _clips.single.fileId ?? '';
        if (fileId.isEmpty) {
          throw BackendException('Upload returned no file id.');
        }
        if (mounted) {
          setState(() {
            _fileId = fileId;
            final double? duration = _clips.single.duration;
            if (duration != null && duration > 0) {
              _sourceDurationSec = duration;
            }
            _uploading = false;
            _uploadStage = null;
          });
        }
        return fileId;
      }
      final String joinId = await _joinClips();
      if (mounted) {
        setState(() {
          _fileId = joinId;
          _uploading = false;
          _uploadStage = null;
        });
      }
      return joinId;
    } on BackendException {
      if (mounted) {
        setState(() {
          _uploading = false;
          _uploadStage = null;
        });
      }
      // Callers own the decision: they route connection failures
      // through _handleConnectionError with their own feature error.
      rethrow;
    }
  }

  /// Uploads one clip slot and records its server id and media.
  Future<void> _uploadClip(int index) async {
    final _ClipSlot clip = _clips[index];
    final Map<String, dynamic> json = await _client.uploadFile(
      clip.devicePath,
      onProgress: (int sent, int total) {
        if (mounted && total > 0) {
          setState(() => _uploadProgress = sent / total);
        }
      },
    );
    final String fileId = json['file_id'] as String? ?? '';
    if (fileId.isEmpty) {
      throw BackendException('Upload returned no file id.');
    }
    final Map<String, dynamic>? media =
        json['media'] as Map<String, dynamic>?;
    if (mounted) {
      setState(() {
        clip.fileId = fileId;
        clip.duration = (media?['duration'] as num?)?.toDouble();
        clip.width = (media?['width'] as num?)?.toInt();
        clip.height = (media?['height'] as num?)?.toInt();
        clip.fps = (media?['fps'] as num?)?.toDouble();
        clip.hasAudio = media?['has_audio'] as bool?;
      });
    }
  }

  /// Joins the uploaded clips (always at original geometry; the
  /// render's own resize owns the output aspect). Applies per-clip
  /// offsets and the joined duration from the response.
  Future<String> _joinClips({bool retried = false}) async {
    if (mounted && _clips.length > 1) {
      setState(() => _uploadStage = 'Joining ${_clips.length} clips…');
    }
    final List<String> ids = <String>[
      for (final _ClipSlot clip in _clips) clip.fileId ?? '',
    ];
    try {
      final Map<String, dynamic> joined = await _client.postJson(
        '/api/join',
        <String, dynamic>{'file_ids': ids, 'aspect_ratio': 'original'},
        timeout: const Duration(minutes: 10),
      );
      final String joinId = joined['join_id'] as String? ?? '';
      if (joinId.isEmpty) {
        throw BackendException('Join returned no id.');
      }
      final List<Object?> clipsJson =
          (joined['clips'] as List?)?.cast<Object?>() ??
          <Object?>[];
      if (mounted) {
        setState(() {
          for (int i = 0; i < _clips.length && i < clipsJson.length; i++) {
            final Object? item = clipsJson[i];
            if (item is Map<String, dynamic>) {
              _clips[i].offset =
                  (item['offset'] as num?)?.toDouble() ?? 0;
              _clips[i].duration ??=
                  (item['duration'] as num?)?.toDouble();
            }
          }
          final double? duration =
              (joined['duration'] as num?)?.toDouble();
          if (duration != null && duration > 0) {
            _sourceDurationSec = duration;
          }
        });
      }
      return joinId;
    } on BackendException catch (error) {
      final Object? missing = error.details['missing'];
      if (!retried && missing is String && missing.isNotEmpty) {
        final int index = _clips.indexWhere(
          (_ClipSlot clip) => clip.fileId == missing,
        );
        if (index >= 0) {
          _clips[index].fileId = null;
          await _uploadClip(index);
          return _joinClips(retried: true);
        }
      }
      rethrow;
    }
  }

  /// Starts footage analysis (cached per source) and polls the job.
  /// Safe to call repeatedly: a fresh upload analyzes once, later taps
  /// reuse the cached result without re-uploading.
  Future<void> _startAnalysis() async {
    if (_analyzing) {
      return;
    }
    final int gen = ++_analysisGen;
    setState(() {
      _analyzing = true;
      _analysisError = null;
      _analysisStage = 'Uploading footage.';
    });
    final Stopwatch clock = Stopwatch()..start();
    try {
      final String fileId = await _ensureUpload();
      if (!mounted || gen != _analysisGen) {
        return;
      }
      setState(() => _analysisStage = 'Starting analysis.');
      final Map<String, dynamic> started = await _client.postJson(
        '/api/analyze/start',
        <String, dynamic>{'file_id': fileId},
      );
      if (!mounted || gen != _analysisGen) {
        return;
      }
      if (started['state'] == 'done' && started['result'] is Map) {
        clock.stop();
        final bool cached = (started['stage'] as String? ?? '').contains(
          'cached',
        );
        setState(() {
          _analyzing = false;
          _analysis = Map<String, dynamic>.from(started['result'] as Map);
          _analysisClipSignature = _clipSignature();
          _analysisStage = '';
          _analysisDuration = clock.elapsed;
          _analysisReused = cached;
        });
        // Analysis feeds the plan request, so a fresh result retires
        // any plan built without it.
        _publish();
        return;
      }
      final String jobId = started['job_id'] as String? ?? '';
      if (jobId.isEmpty) {
        throw BackendException('Analysis did not start.');
      }
      setState(() => _analysisJobId = jobId);
      await _pollAnalysis(gen, jobId, clock);
    } on BackendException catch (error) {
      if (!mounted || gen != _analysisGen) {
        return;
      }
      setState(() {
        _analyzing = false;
        _analysisJobId = null;
        _analysisError = error.message;
      });
      if (error.isConnectionError) {
        await _handleConnectionError(
          error,
          (String message) => setState(() => _analysisError = message),
          'Footage analysis',
        );
      }
    }
  }

  Future<void> _pollAnalysis(int gen, String jobId, Stopwatch clock) async {
    while (mounted && gen == _analysisGen) {
      await Future<void>.delayed(const Duration(seconds: 1));
      if (!mounted || gen != _analysisGen) {
        return;
      }
      try {
        final Map<String, dynamic> job = await _client.getJson(
          '/api/analyze/$jobId',
        );
        if (!mounted || gen != _analysisGen) {
          return;
        }
        final String state = job['state'] as String? ?? '';
        if (state == 'done' && job['result'] is Map) {
          clock.stop();
          setState(() {
            _analyzing = false;
            _analysisJobId = null;
            _analysis = Map<String, dynamic>.from(job['result'] as Map);
            _analysisClipSignature = _clipSignature();
            _analysisStage = '';
            _analysisDuration = clock.elapsed;
            _analysisReused = false;
          });
          // Analysis feeds the plan request, so a fresh result retires
          // any plan built without it.
          _publish();
          return;
        }
        if (state == 'error') {
          setState(() {
            _analyzing = false;
            _analysisJobId = null;
            _analysisError = job['error'] as String? ?? 'Analysis failed.';
          });
          return;
        }
        if (state == 'cancelled') {
          setState(() {
            _analyzing = false;
            _analysisJobId = null;
            _analysisStage = '';
          });
          return;
        }
        setState(() => _analysisStage = job['stage'] as String? ?? 'Working.');
      } on BackendException catch (error) {
        if (!mounted || gen != _analysisGen) {
          return;
        }
        if (error.isConnectionError) {
          if (error.timedOut) {
            // A reachable backend means a slow poll, not a dead one:
            // keep polling instead of stopping the analysis.
            bool reachable = false;
            try {
              reachable = await _client.probeReachable();
            } catch (_) {
              reachable = false;
            }
            if (!mounted || gen != _analysisGen) {
              return;
            }
            if (reachable) {
              setState(() => _analysisStage = 'Retrying…');
            } else {
              _noteConnection(error.message);
              setState(() => _analyzing = false);
              return;
            }
          } else {
            _noteConnection(error.message);
            setState(() => _analyzing = false);
            return;
          }
        } else {
          setState(() => _analysisStage = 'Retrying…');
        }
      }
    }
  }

  Future<void> _cancelAnalysis() async {
    final int gen = ++_analysisGen;
    final String? jobId = _analysisJobId;
    setState(() {
      _analyzing = false;
      _analysisJobId = null;
      _analysisStage = '';
    });
    if (jobId == null || jobId.isEmpty) {
      return;
    }
    try {
      await _client.deleteJson('/api/analyze/$jobId');
    } catch (_) {
      // Cancel is best-effort; the stale guard already orphaned the job.
    }
    if (gen != _analysisGen) {
      return;
    }
  }

  /// Analysis only when it belongs to the current video.
  Map<String, dynamic>? get _currentAnalysis =>
      _analysisClipSignature == _clipSignature() ? _analysis : null;

  Future<Map<String, dynamic>> _postPlan(String fileId, _JobSnapshot snap) {
    final Map<String, dynamic> body = <String, dynamic>{
      'file_id': fileId,
      'instruction': snap.instruction,
      'style': snap.style?.label ?? '',
      'aspect_ratio': snap.aspectRatio?.backendValue ?? 'original',
      'fit': snap.fit.backendValue,
      'captions': snap.captionChoice.backendValue,
    };
    final Map<String, dynamic>? analysis = snap.analysis;
    if (analysis != null) {
      body['analysis'] = analysis;
    }
    final Map<String, dynamic>? explicitCuts = _explicitCutsBody(snap.cuts);
    if (explicitCuts != null) {
      body['explicit_cuts'] = explicitCuts;
    }
    if (snap.allowWonder) {
      body['allow_wonder'] = true;
    }
    return _client.postJson(
      '/api/plan',
      body,
      timeout: const Duration(minutes: 5),
    );
  }

  Future<Map<String, dynamic>> _postRender(
    String fileId,
    Map<String, dynamic> plan,
    _JobSnapshot snap,
  ) {
    return _client.postJson('/api/render', <String, dynamic>{
      'file_id': fileId,
      'plan': plan,
      'instruction': snap.instruction,
    }, timeout: const Duration(minutes: 30));
  }

  Future<String> _downloadRender(Map<String, dynamic> json) async {
    try {
      final Directory renders = await _store.rendersDir();
      final String dest =
          '${renders.path}/render_${DateTime.now().millisecondsSinceEpoch}.mp4';
      await _client.downloadToFile(json['url'] as String? ?? '', dest);
      return dest;
    } on BackendException {
      rethrow;
    } catch (error) {
      throw BackendException('Could not save the edited video: $error');
    }
  }

  void _applyPlanJson(Map<String, dynamic> json, _JobSnapshot snap) {
    final Map<String, dynamic> plan =
        (json['plan'] as Map<String, dynamic>?) ?? <String, dynamic>{};
    final List<String> warnings = (json['warnings'] as List? ?? const [])
        .map((Object? item) => item.toString())
        .toList();
    final List<String> pending = (json['pending'] as List? ?? const [])
        .map((Object? item) => item.toString())
        .toList();
    final List<String> adjustments =
        (json['adjustments'] as List? ?? const [])
            .map((Object? item) => item.toString())
            .toList();
    final Map<String, int> planTimings = _readTimings(json);
    setState(() {
      _planTimings = planTimings;
      _rawPlan = plan;
      _plan = EditingPlan.fromJson(
        plan,
        json['model'] as String? ?? '',
        warnings,
        pending: pending,
        adjustments: adjustments,
      );
      _suggestions = <Map<String, dynamic>>[
        for (final Object? item in (json['suggestions'] as List? ?? const []))
          if (item is Map<String, dynamic>) item,
      ];
      // Record what the plan was built from — the snapshot — so a
      // mid-flight edit is detected instead of silently adopted. The
      // clip baseline is read live: the snapshot predates the plan's
      // own upload, and an upload completing is not a user edit.
      _plannedInstruction = snap.instruction;
      _plannedStyle = snap.style;
      _plannedAspect = snap.aspectRatio;
      _plannedFit = snap.fit;
      _plannedCaptionChoice = snap.captionChoice;
      _plannedExtrasSignature = snap.extrasSignature;
      _plannedClipSignature = _clipSignature();
      _planning = false;
      if (_reviewKeeps != null) {
        _reviewKeeps = _planKeptRanges();
        _dismissedSuggestions = <String>{};
      }
    });
    _saveProject('planned');
  }

  void _applyRenderJson(Map<String, dynamic> json, String dest) {
    final Map? output = json['output'] as Map?;
    final Map<String, int> renderTimings = _readTimings(json);
    setState(() {
      _renderTimings = renderTimings;
      _renderPath = dest;
      _renderDurationSec = (output?['duration'] as num?)?.toDouble();
      _renderWidth = (output?['width'] as num?)?.toInt();
      _renderHeight = (output?['height'] as num?)?.toInt();
      _renderSar = output?['sar'] as String?;
      _renderSteps = (json['steps_used'] as List? ?? const [])
          .map((Object? item) => item.toString())
          .toList();
      _renderWarnings = (json['warnings'] as List? ?? const [])
          .map((Object? item) => item.toString())
          .toList();
      _renderAdjustments = (json['adjustments'] as List? ?? const [])
          .map((Object? item) => item.toString())
          .toList();
      _rendering = false;
    });
    _saveProject('rendered');
  }

  Future<void> _generatePlan({_JobSnapshot? snapshot}) async {
    if (_planning || _instructionController.text.trim().isEmpty) {
      return;
    }
    final String? cutsBlocked = _cutsBlocker();
    if (cutsBlocked != null) {
      setState(() => _planError = cutsBlocked);
      return;
    }
    await _generatePlanAttempt(
      retryUpload: true,
      snapshot: snapshot ?? _captureSnapshot(),
    );
  }

  Future<void> _generatePlanAttempt({
    required bool retryUpload,
    required _JobSnapshot snapshot,
  }) async {
    final int gen = ++_planGen;
    setState(() {
      _planning = true;
      _planError = null;
      _connectionError = null;
    });
    try {
      final String fileId = await _ensureUpload();
      final Map<String, dynamic> json = await _timedStage(
        _JobStage.plan,
        () => _postPlan(fileId, snapshot),
      );
      if (!mounted || gen != _planGen) {
        return;
      }
      _applyPlanJson(json, snapshot);
    } on BackendException catch (error) {
      if (!mounted || gen != _planGen) {
        return;
      }
      if (error.statusCode == 404 && retryUpload) {
        // The backend forgot this upload (e.g. after a restart):
        // re-upload once instead of failing. The retry reuses the
        // same snapshot: a retry must not pick up newer edits.
        _fileId = null;
        await _generatePlanAttempt(retryUpload: false, snapshot: snapshot);
        return;
      }
      setState(() => _planning = false);
      _noteErrorDetail('plan', error);
      if (error.isConnectionError) {
        await _handleConnectionError(
          error,
          (String message) => setState(() => _planError = message),
          'Planning',
        );
      } else {
        setState(() => _planError = error.message);
      }
    }
  }

  /// The current plan plus attached extras (captions, music, effects).
  /// Extras come from the job snapshot, never live widgets, so a
  /// mid-render edit cannot leak into this render.
  /// Throws [BackendException] when the music file cannot be uploaded.
  Future<Map<String, dynamic>> _effectivePlan(
    Map<String, dynamic> plan,
    _JobSnapshot snap,
  ) async {
    final Map<String, dynamic> effective =
        jsonDecode(jsonEncode(plan)) as Map<String, dynamic>;
    final List<dynamic> ops = (effective['operations'] as List?) ?? <dynamic>[];
    if (snap.captionChoice == CaptionChoice.off) {
      // Explicit Off suppresses any model-added captions.
      ops.removeWhere((Object? op) => op is Map && op['op'] == 'captions');
    } else if (snap.captionChoice == CaptionChoice.on) {
      if (snap.captionLines.isNotEmpty) {
        ops.add(<String, dynamic>{
          'op': 'captions',
          'origin': 'control',
          'segments': snap.captionLines
              .map(
                (_CaptionLineSnapshot line) => <String, dynamic>{
                  'start': line.start,
                  'end': line.end,
                  'text': line.text,
                },
              )
              .toList(),
        });
      } else {
        ops.add(<String, dynamic>{
          'op': 'captions',
          'origin': 'control',
          'source': 'auto',
        });
      }
    }
    final String? musicPath = snap.musicPath;
    if (musicPath != null) {
      final Map<String, dynamic> uploaded = await _client.uploadFile(musicPath);
      final String musicId = uploaded['file_id'] as String? ?? '';
      if (musicId.isEmpty) {
        throw BackendException('Music upload returned no file id.');
      }
      ops.add(<String, dynamic>{
        'op': 'music',
        'origin': 'control',
        'file_id': musicId,
        'volume_db': snap.musicVolumeDb,
        'duck': snap.musicDuck,
      });
    }
    for (final _SfxPick pick in snap.sfxPicks) {
      ops.add(<String, dynamic>{
        'op': 'sfx',
        'origin': 'control',
        'asset': pick.asset,
        'at': pick.at,
      });
    }
    for (final _SupportPlacement placement in snap.support) {
      final String fileId = placement.fileId;
      if (!_looksLikeFileId(fileId)) {
        throw BackendException(
          'A supporting clip lost its upload. Pick it again.',
        );
      }
      final String? audioError = _supportAudioError(
        placement.kind,
        placement.audio,
      );
      if (audioError != null) {
        throw BackendException(audioError);
      }
      // Phrase placements re-resolve against the current plan so cuts
      // and reordering stay correct; edited-time placements are fixed.
      double atOutput = placement.at;
      if (placement.sourceAt != null) {
        final double? content = _sourceToContent(
          placement.sourceAt!,
          inserts: snap.support,
        );
        if (content == null) {
          throw BackendException(
            '“${placement.phrase ?? 'That line'}” was cut from the edit, '
            'so the ${placement.kind} has nowhere to sit. Restore the '
            'line or remove the placement.',
          );
        }
        atOutput = content + _introDurationOf(snap.attachments);
      }
      if (placement.kind == 'insert') {
        final double contentAt =
            atOutput - _introDurationOf(snap.attachments);
        if (contentAt < -1e-6) {
          throw BackendException(
            'An insert sits inside the intro still. Move it after the '
            'intro.',
          );
        }
        ops.add(<String, dynamic>{
          'op': 'insert',
          'origin': 'control',
          'file_id': fileId,
          'at': contentAt.clamp(0, double.infinity),
          'duration': placement.duration,
          'src_start': placement.srcStart,
          'audio': placement.audio,
        });
        continue;
      }
      final Map<String, dynamic> op = <String, dynamic>{
        'op': placement.kind,
        'origin': 'control',
        'file_id': fileId,
        'at': atOutput,
        'duration': placement.duration,
        'src_start': placement.srcStart,
        'audio': placement.audio,
      };
      if (placement.kind == 'pip') {
        op['position'] = placement.position ?? 'top_right';
        op['width_frac'] = placement.widthFrac ?? 0.25;
      }
      ops.add(op);
    }
    for (final _ImageAttachment attachment in snap.attachments) {
      // A cover is export-only: it never becomes a render operation.
      if (attachment.kind == 'cover') {
        continue;
      }
      String imageId = attachment.fileId ?? '';
      if (!_looksLikeFileId(imageId)) {
        // Legacy path-only attachment: upload the device file now.
        if (!File(attachment.path).existsSync()) {
          throw BackendException(
            'The attached ${attachment.kind} image is no longer on '
            'this device and has no uploaded copy. Attach it again.',
          );
        }
        final Map<String, dynamic> uploaded = await _client.uploadFile(
          attachment.path,
          label: 'asset',
        );
        imageId = uploaded['file_id'] as String? ?? '';
      }
      if (!_looksLikeFileId(imageId)) {
        throw BackendException('Image upload returned no file id.');
      }
      switch (attachment.kind) {
        case 'intro' || 'outro':
          ops.add(<String, dynamic>{
            'op': attachment.kind,
            'origin': 'control',
            'file_id': imageId,
            'duration': attachment.duration,
          });
        case 'banner':
          ops.add(<String, dynamic>{
            'op': 'banner',
            'origin': 'control',
            'file_id': imageId,
            'start': attachment.start,
            'end': attachment.end,
            'position': attachment.position,
            'width_frac': attachment.size,
          });
      }
    }
    effective['operations'] = ops;
    return effective;
  }

  /// Message blocking a render, or null when captions are fine.
  String? _captionBlocker() {
    if (_captionChoice == CaptionChoice.on &&
        _captionLines.isNotEmpty &&
        !_captionsUsable) {
      return 'Caption lines need valid times and text first. Fix or '
          'remove the broken lines, or switch captions to Auto.';
    }
    return null;
  }

  Future<void> _render() async {
    final Map<String, dynamic>? plan = _rawPlan;
    if (_rendering || plan == null) {
      return;
    }
    final String? blocked = _captionBlocker();
    if (blocked != null) {
      setState(() => _renderError = blocked);
      return;
    }
    final String? cutsBlocked = _cutsBlocker();
    if (cutsBlocked != null) {
      setState(() => _renderError = cutsBlocked);
      return;
    }
    if (!_snapshotMatchesCurrent()) {
      setState(() {
        _renderError =
            'Settings changed since this plan was made. '
            'Generate a new plan first.';
      });
      return;
    }
    await _renderAttempt(plan, _captureSnapshot(), retryUpload: true);
  }

  Future<void> _renderAttempt(
    Map<String, dynamic> plan,
    _JobSnapshot snapshot, {
    required bool retryUpload,
  }) async {
    final int gen = ++_renderGen;
    setState(() {
      _rendering = true;
      _renderError = null;
      _connectionError = null;
      // Drop the previous output now so it can never appear as this result.
      _renderPath = null;
      _renderDurationSec = null;
      _renderWidth = null;
      _renderHeight = null;
      _renderSar = null;
      _renderSteps = const <String>[];
      _renderWarnings = const <String>[];
      _renderAdjustments = const <String>[];
    });
    try {
      final String fileId = await _ensureUpload();
      final Map<String, dynamic> effective = await _effectivePlan(
        plan,
        snapshot,
      );
      final Map<String, dynamic> json = await _timedStage(
        _JobStage.render,
        () => _postRender(fileId, effective, snapshot),
      );
      if (!mounted || gen != _renderGen) {
        return;
      }
      final String dest = await _timedStage(
        _JobStage.prepare,
        () => _downloadRender(json),
      );
      if (!mounted || gen != _renderGen) {
        return;
      }
      _applyRenderJson(json, dest);
    } on BackendException catch (error) {
      if (!mounted || gen != _renderGen) {
        return;
      }
      if (error.statusCode == 404 && retryUpload) {
        _fileId = null;
        await _renderAttempt(plan, snapshot, retryUpload: false);
        return;
      }
      setState(() => _rendering = false);
      _noteErrorDetail('render', error);
      if (error.isConnectionError) {
        await _handleConnectionError(
          error,
          (String message) => setState(() => _renderError = message),
          'Rendering',
        );
      } else {
        setState(() => _renderError = error.message);
      }
    }
  }

  bool _jobAlive(int gen) => mounted && gen == _jobGen;

  void _setStage(_JobStage stage, _StageState state) {
    if (!mounted) {
      return;
    }
    setState(() {
      _jobStages = Map<_JobStage, _StageState>.from(_jobStages)
        ..[stage] = state;
    });
  }

  /// Runs [run] and records its wall-clock duration for [stage].
  Future<T> _timedStage<T>(_JobStage stage, Future<T> Function() run) async {
    final Stopwatch clock = Stopwatch()..start();
    try {
      return await run();
    } finally {
      clock.stop();
      if (mounted) {
        setState(() {
          _stageDurations[stage] = clock.elapsed;
          _stageReused.remove(stage);
        });
      }
    }
  }

  /// Marks [stage] as reused (upload done, plan current): no new
  /// measurement, so the old duration is dropped instead of restated.
  void _markStageReused(_JobStage stage) {
    if (!mounted) {
      return;
    }
    setState(() {
      _stageReused.add(stage);
      _stageDurations.remove(stage);
    });
  }

  /// Seconds with one decimal below a minute, minutes above.
  String _formatSecs(num ms) {
    final double seconds = ms / 1000;
    if (seconds < 60) {
      return '${seconds.toStringAsFixed(1)}s';
    }
    final int minutes = seconds ~/ 60;
    final int rest = (seconds % 60).round();
    return '${minutes}m ${rest}s';
  }

  String _formatDuration(Duration duration) =>
      _formatSecs(duration.inMilliseconds);

  /// Server per-stage breakdown from a response's "timings" object.
  Map<String, int> _readTimings(Map<String, dynamic> json) {
    final Object? raw = json['timings'];
    if (raw is! Map) {
      return <String, int>{};
    }
    return <String, int>{
      for (final MapEntry<dynamic, dynamic> entry in raw.entries)
        if (entry.value is num)
          entry.key.toString(): (entry.value as num).toInt(),
    };
  }

  /// One compact client-side line: "Upload 3.1s • Planning reused • …".
  /// Stages with nothing measured or reused are left out.
  String _stageSummaryLine() {
    final List<String> parts = <String>[];
    for (final _JobStage stage in _JobStage.values) {
      final Duration? duration = _stageDurations[stage];
      if (duration != null) {
        parts.add('${stage.label} ${_formatDuration(duration)}');
      } else if (_stageReused.contains(stage)) {
        parts.add('${stage.label} reused');
      }
    }
    return parts.join(' • ');
  }

  /// Server planning breakdown: "Model 9.2s • 2 round trips".
  String _serverPlanLine() {
    final List<String> parts = <String>[];
    final int? visual = _planTimings['visual_analysis_ms'];
    if (visual != null && visual > 0) {
      parts.add('looking at footage ${_formatSecs(visual)}');
    }
    final int? model = _planTimings['model_ms'];
    if (model != null) {
      parts.add('model ${_formatSecs(model)}');
    }
    final int? trips = _planTimings['model_roundtrips'];
    if (trips != null && trips > 1) {
      parts.add('$trips round trips');
    }
    return parts.join(' • ');
  }

  /// Combined server line for the generate card.
  String _serverSummaryLine() {
    final List<String> parts = <String>[];
    final String plan = _serverPlanLine();
    if (plan.isNotEmpty) {
      parts.add('planning: $plan');
    }
    final String render = _serverRenderLine();
    if (render.isNotEmpty) {
      parts.add('render: $render');
    }
    if (parts.isEmpty) {
      return '';
    }
    return 'Server: ${parts.join(' • ')}';
  }

  /// Server footage-analysis breakdown from the cached result map.
  String _serverAnalysisLine(Map<String, dynamic> analysis) {
    final Object? raw = analysis['timings'];
    if (raw is! Map) {
      return '';
    }
    int msOf(String key) => (raw[key] as num?)?.toInt() ?? 0;
    final List<String> parts = <String>[];
    final int transcribe = msOf('transcribe_ms');
    if (transcribe > 0) {
      parts.add('speech ${_formatSecs(transcribe)}');
    }
    final int cuts = msOf('cuts_ms');
    if (cuts > 0) {
      parts.add('cuts ${_formatSecs(cuts)}');
    }
    final int frames = msOf('frames_ms');
    if (frames > 0) {
      parts.add('frames ${_formatSecs(frames)}');
    }
    if (parts.isEmpty) {
      return '';
    }
    return 'Server: ${parts.join(' • ')}';
  }

  /// Server transcription breakdown: "Speech recognition 5.1s".
  String _serverTranscribeLine() {
    final int? stt = _transcribeTimings['stt_ms'];
    if (stt == null || stt <= 0) {
      return '';
    }
    return 'Speech recognition ${_formatSecs(stt)}';
  }

  /// Server render breakdown: "Speech 8.1s • FFmpeg 22.4s".
  String _serverRenderLine() {
    final List<String> parts = <String>[];
    final int? sam = _renderTimings['sam_ms'];
    if (sam != null && sam > 0) {
      parts.add('tracking ${_formatSecs(sam)}');
    }
    final int? transcribe = _renderTimings['transcribe_ms'];
    if (transcribe != null && transcribe > 0) {
      parts.add('speech ${_formatSecs(transcribe)}');
    }
    final int? ffmpeg = _renderTimings['ffmpeg_ms'];
    if (ffmpeg != null) {
      parts.add('FFmpeg ${_formatSecs(ffmpeg)}');
    }
    return parts.join(' • ');
  }

  void _failJob(_JobStage stage, String message) {
    _setStage(stage, _StageState.failed);
    if (mounted) {
      setState(() {
        _jobRunning = false;
        _jobError = message;
      });
    }
  }

  /// Primary action: plan and render in sequence with real stage states.
  /// Completed stages (upload, current plan) are reused, so Retry never
  /// re-uploads or re-plans unnecessarily. Every stage reads from the
  /// snapshot captured below, so edits made while the job runs cannot
  /// leak into it; they orphan the job instead.
  Future<void> _generateEditedVideo() async {
    if (_jobRunning ||
        _planning ||
        _rendering ||
        _instructionController.text.trim().isEmpty) {
      return;
    }
    final String? blocked = _captionBlocker();
    if (blocked != null) {
      setState(() {
        _jobError = blocked;
        _jobStages = Map<_JobStage, _StageState>.from(_jobStages)
          ..[_JobStage.render] = _StageState.failed;
      });
      return;
    }
    final String? cutsBlocked = _cutsBlocker();
    if (cutsBlocked != null) {
      setState(() {
        _jobError = cutsBlocked;
        _jobStages = Map<_JobStage, _StageState>.from(_jobStages)
          ..[_JobStage.plan] = _StageState.failed;
      });
      return;
    }
    final _JobSnapshot snap = _captureSnapshot();
    final int gen = ++_jobGen;
    setState(() {
      _jobRunning = true;
      _jobError = null;
      _connectionError = null;
      for (final _JobStage stage in _JobStage.values) {
        if (_jobStages[stage] == _StageState.failed) {
          _jobStages[stage] = _StageState.idle;
        }
      }
    });
    try {
      if (_fileId == null) {
        _setStage(_JobStage.upload, _StageState.active);
        await _timedStage(_JobStage.upload, _ensureUpload);
        if (!_jobAlive(gen)) {
          return;
        }
      } else {
        _markStageReused(_JobStage.upload);
      }
      _setStage(_JobStage.upload, _StageState.done);
      if (_rawPlan == null || !_snapshotMatchesCurrent()) {
        _setStage(_JobStage.plan, _StageState.active);
        await _generatePlan(snapshot: snap);
        if (!_jobAlive(gen)) {
          return;
        }
        if (_rawPlan == null) {
          _failJob(_JobStage.plan, _planError ?? 'Planning failed.');
          return;
        }
      } else {
        _markStageReused(_JobStage.plan);
      }
      _setStage(_JobStage.plan, _StageState.done);
      final Map<String, dynamic>? plan = _rawPlan;
      if (plan == null || !_snapshotMatchesCurrent()) {
        _failJob(_JobStage.plan, 'The plan is no longer current.');
        return;
      }
      _setStage(_JobStage.render, _StageState.active);
      setState(() {
        _rendering = true;
        _renderError = null;
        _renderPath = null;
        _renderDurationSec = null;
        _renderWidth = null;
        _renderHeight = null;
        _renderSar = null;
      });
      final String fileId = await _ensureUpload();
      final Map<String, dynamic> effective = await _effectivePlan(plan, snap);
      final Map<String, dynamic> json = await _timedStage(
        _JobStage.render,
        () => _postRender(fileId, effective, snap),
      );
      if (!_jobAlive(gen)) {
        return;
      }
      _setStage(_JobStage.render, _StageState.done);
      _setStage(_JobStage.prepare, _StageState.active);
      final String dest = await _timedStage(
        _JobStage.prepare,
        () => _downloadRender(json),
      );
      if (!_jobAlive(gen)) {
        return;
      }
      _applyRenderJson(json, dest);
      _setStage(_JobStage.prepare, _StageState.done);
      if (mounted) {
        setState(() => _jobRunning = false);
      }
    } on BackendException catch (error) {
      if (!_jobAlive(gen)) {
        return;
      }
      if (mounted) {
        setState(() {
          _planning = false;
          _rendering = false;
        });
      }
      final _JobStage failed =
          _jobStages[_JobStage.prepare] == _StageState.active
          ? _JobStage.prepare
          : (_jobStages[_JobStage.render] == _StageState.active
                ? _JobStage.render
                : _JobStage.plan);
      if (error.isConnectionError) {
        if (!error.timedOut) {
          _noteConnection(error.message);
          _failJob(_JobStage.upload, error.message);
          return;
        }
        // A timeout may mean a busy backend: fail the active stage
        // when the probe says it is still reachable.
        await _handleConnectionError(
          error,
          (String message) => _failJob(failed, message),
          'Generation',
        );
        return;
      }
      if (error.statusCode == 404 && _fileId != null) {
        // Stale upload id (e.g. backend restarted): forget it so the
        // next Retry uploads fresh instead of failing again.
        _fileId = null;
      }
      _noteErrorDetail('job/${failed.name}', error);
      _failJob(failed, error.message);
    }
  }

  /// Transcribes the SOURCE video's speech for captions. This never
  /// becomes an editing instruction; it only supplies caption content.
  Future<void> _transcribeSource() async {
    if (_transcribing) {
      return;
    }
    setState(() {
      _transcribing = true;
      _captionError = null;
      _connectionError = null;
    });
    final Stopwatch clock = Stopwatch()..start();
    try {
      final String fileId = await _ensureUpload();
      final Map<String, dynamic> json = await _client.postJson(
        '/api/transcribe',
        <String, dynamic>{'file_id': fileId},
        timeout: const Duration(minutes: 10),
      );
      if (!mounted) {
        return;
      }
      clock.stop();
      final Map<String, int> serverTimings = _readTimings(json);
      final List<dynamic> turns = (json['turns'] as List?) ?? const <dynamic>[];
      final bool approximate = json['approximate'] as bool? ?? true;
      final double audioDuration = (json['duration'] as num?)?.toDouble() ?? 0;
      for (final _CaptionLine line in _captionLines) {
        line.controller.dispose();
      }
      final List<_CaptionLine> lines = _linesFromTurns(
        turns,
        approximate: approximate,
        audioDuration: audioDuration,
      );
      for (final _CaptionLine line in lines) {
        line.controller.addListener(_publish);
      }
      setState(() {
        _captionLines = lines;
        _captionsApproximate = approximate && lines.isNotEmpty;
        _captionDiagnosis = json['diagnosis'] is Map
            ? Map<String, dynamic>.from(json['diagnosis'] as Map)
            : null;
        _transcribeDuration = clock.elapsed;
        _transcribeTimings = serverTimings;
        _transcribing = false;
        if (lines.isEmpty) {
          _captionError = 'No speech found to caption.';
          _captionChoice = CaptionChoice.auto;
        }
      });
      _publish();
    } on BackendException catch (error) {
      if (!mounted) {
        return;
      }
      setState(() => _transcribing = false);
      if (error.isConnectionError) {
        await _handleConnectionError(
          error,
          (String message) => setState(() {
            _captionError = message;
            _captionDiagnosis = null;
          }),
          'Transcription',
        );
      } else {
        setState(() {
          _captionError = error.message;
          _captionDiagnosis = error.details['diagnosis'] is Map
              ? Map<String, dynamic>.from(error.details['diagnosis'] as Map)
              : null;
        });
      }
    }
  }

  /// Appends a user-written title instruction instead of speech captions.
  void _addTitleInstead() {
    final String title = _titleController.text.trim();
    if (title.isEmpty) {
      setState(() => _captionError = 'Type the title text first.');
      return;
    }
    final String current = _instructionController.text.trim();
    final String addition = 'Show the title "$title" for the first 3 seconds.';
    _instructionController.text = current.isEmpty
        ? addition
        : '$current $addition';
    setState(() => _captionError = null);
  }

  double? _turnSeconds(Map? raw, List<String> keys) {
    if (raw == null) {
      return null;
    }
    for (final String key in keys) {
      final Object? value = raw[key];
      if (value is num) {
        final double seconds = value.toDouble();
        return key.toLowerCase().contains('ms') ? seconds / 1000 : seconds;
      }
    }
    return null;
  }

  /// Build editable caption lines from transcription turns. Timed turns
  /// keep their timestamps; coarse turns split into sentences spread
  /// evenly over the audio so one whole-video blob never becomes one
  /// giant line. Mirrors the backend's sentence rule.
  List<_CaptionLine> _linesFromTurns(
    List<dynamic> turns, {
    required bool approximate,
    required double audioDuration,
  }) {
    final List<_CaptionLine> timed = <_CaptionLine>[];
    final List<String> coarse = <String>[];
    for (final Object? turn in turns) {
      if (turn is! Map) {
        continue;
      }
      final String text = (turn['text'] as String?) ?? '';
      if (text.trim().isEmpty) {
        continue;
      }
      final Map? raw = turn['raw'] as Map?;
      final double? start = _turnSeconds(raw, const <String>[
        'startMs',
        'start_ms',
        'start',
      ]);
      final double? end = _turnSeconds(raw, const <String>[
        'endMs',
        'end_ms',
        'end',
      ]);
      if (!approximate && start != null && end != null && end > start) {
        timed.add(_CaptionLine(start: start, end: end, text: text));
      } else {
        coarse.add(text);
      }
    }
    if (coarse.isEmpty) {
      return timed;
    }
    final List<String> chunks = <String>[
      for (final String text in coarse) ..._splitSentences(text),
    ];
    if (audioDuration <= 0 || chunks.isEmpty) {
      return timed;
    }
    final double slot = audioDuration / chunks.length;
    for (int index = 0; index < chunks.length; index++) {
      timed.add(
        _CaptionLine(
          start: index * slot,
          end: (index + 1) * slot,
          text: chunks[index],
        ),
      );
    }
    timed.sort((_CaptionLine a, _CaptionLine b) => a.start.compareTo(b.start));
    return timed;
  }

  /// Split a coarse transcript into sentence chunks (Tamil and English
  /// both end sentences with . ? !), then by length for long lines.
  List<String> _splitSentences(String text) {
    final RegExp end = RegExp(r'(.+?[.!?…]+)(?=\s+|$)');
    final List<String> parts = <String>[
      for (final RegExpMatch match in end.allMatches(text.trim()))
        match.group(1)!.trim(),
    ];
    final List<String> chunks = <String>[];
    for (final String part in (parts.isEmpty ? <String>[text.trim()] : parts)) {
      if (part.length <= 90) {
        chunks.add(part);
        continue;
      }
      final List<String> commaBits = <String>[
        for (final String bit in part.split(',')) bit.trim(),
      ].where((String bit) => bit.isNotEmpty).toList();
      if (commaBits.length > 1 &&
          commaBits.every((String bit) => bit.length <= 90)) {
        chunks.addAll(commaBits);
        continue;
      }
      final List<String> words = part.split(RegExp(r'\s+'));
      for (int at = 0; at < words.length; at += 12) {
        chunks.add(words.skip(at).take(12).join(' '));
      }
    }
    return chunks.where((String chunk) => chunk.isNotEmpty).toList();
  }

  Future<void> _pickMusic() async {
    setState(() => _musicError = null);
    try {
      final Future<String?> Function()? pick = widget.pickAudio;
      final String? path = pick != null
          ? await pick()
          : (await FilePicker.pickFile(type: FileType.audio))?.path;
      if (!mounted) {
        return;
      }
      if (path == null || path.isEmpty) {
        return;
      }
      setState(() => _musicPath = path);
      _publish();
    } catch (_) {
      if (mounted) {
        setState(() => _musicError = 'Could not open the audio picker.');
      }
    }
  }

  /// Images available for attachment: the footage cover, the generated
  /// artwork, and a picked photo — but only once each has a
  /// server-confirmed asset id. A merely selected photo is never
  /// attachable.
  Map<String, String> _attachSources() {
    final Map<String, String> sources = <String, String>{};
    final String? cover = _coverPath;
    if (cover != null && _coverImageId != null) {
      sources['cover'] = cover;
    }
    final String? artwork = _thumbPath;
    if (artwork != null && _thumbImageId != null) {
      sources['artwork'] = artwork;
    }
    final String? uploaded = _uploadedImagePath;
    if (uploaded != null && _uploadedFileId != null) {
      sources['uploaded'] = uploaded;
    }
    return sources;
  }

  static final RegExp _fileIdPattern = RegExp(r'^[0-9a-f]{32}$');

  static bool _looksLikeFileId(String value) =>
      _fileIdPattern.hasMatch(value);

  /// Server asset id from a /api/files/{id} download URL, or null.
  static String? _fileIdFromFilesUrl(String url) {
    final List<String> parts = url.split('/');
    final String last = parts.isEmpty ? '' : parts.last;
    return _looksLikeFileId(last) ? last : null;
  }

  /// First valid server asset id from a download URL or an explicit
  /// image_id field, preferring the URL.
  static String? _coverArtworkId(String url, String? imageId) {
    return _fileIdFromFilesUrl(url) ??
        ((imageId != null && _looksLikeFileId(imageId)) ? imageId : null);
  }

  /// Server asset id for an attachable source, or null when the
  /// source is not uploaded yet.
  String? _attachFileId(String source) {
    return switch (source) {
      'cover' => _coverImageId,
      'artwork' => _thumbImageId,
      'uploaded' => _uploadedFileId,
      _ => null,
    };
  }

  /// Display name for an attachable source.
  String _attachFileName(String source, String path) {
    if (source == 'uploaded' && _uploadedImageName != null) {
      return _uploadedImageName!;
    }
    if (source == 'cover') {
      return 'Video cover';
    }
    if (source == 'artwork') {
      return 'Generated artwork';
    }
    return path.split(RegExp(r'[/\\]')).last;
  }

  String _attachSourceLabel(String source) {
    return switch (source) {
      'cover' => 'Video cover',
      'artwork' => 'Generated artwork',
      'uploaded' => 'Uploaded image',
      _ => source,
    };
  }

  Future<void> _pickImage() async {
    setState(() {
      _attachmentError = null;
      _pendingImageError = null;
    });
    try {
      final Future<String?> Function()? pick = widget.pickImage;
      final String? path = pick != null
          ? await pick()
          : (await FilePicker.pickFile(
              type: FileType.custom,
              allowedExtensions: <String>['png', 'jpg', 'jpeg', 'webp'],
            ))?.path;
      if (!mounted) {
        return;
      }
      if (path == null || path.isEmpty) {
        return;
      }
      if (!File(path).existsSync()) {
        setState(
          () => _pendingImageError =
              'That photo is not readable on this device. Pick another.',
        );
        return;
      }
      final int gen = ++_pickGen;
      setState(() {
        _uploadedImagePath = path;
        _uploadedImageName = path.split(RegExp(r'[/\\]')).last;
        _uploadedFileId = null;
        _uploadedDims = null;
        _pendingImageError = null;
        _attachSource = 'uploaded';
      });
      _publish();
      await _uploadPendingImage(gen);
    } catch (_) {
      if (mounted) {
        setState(() => _attachmentError = 'Could not open the image picker.');
      }
    }
  }

  /// Uploads the currently selected photo as an asset. Only the
  /// response matching the latest pick generation is honored, so a
  /// late reply can never mark a replaced photo as uploaded.
  Future<void> _uploadPendingImage(int gen) async {
    final String? path = _uploadedImagePath;
    if (path == null || gen != _pickGen) {
      return;
    }
    setState(() {
      _uploadingImage = true;
      _pendingImageError = null;
    });
    try {
      final Map<String, dynamic> uploaded = await _client.uploadFile(
        path,
        label: 'asset',
      );
      if (!mounted || gen != _pickGen) {
        return;
      }
      final String fileId = uploaded['file_id'] as String? ?? '';
      if (fileId.isEmpty) {
        throw BackendException('Image upload returned no file id.');
      }
      final Map<String, dynamic>? media =
          uploaded['media'] as Map<String, dynamic>?;
      final int? width = (media?['width'] as num?)?.toInt();
      final int? height = (media?['height'] as num?)?.toInt();
      setState(() {
        _uploadedFileId = fileId;
        _uploadedDims = (width != null && height != null)
            ? '$width×$height'
            : null;
        _uploadingImage = false;
      });
      _publish();
    } on BackendException catch (error) {
      if (!mounted || gen != _pickGen) {
        return;
      }
      setState(() {
        _uploadingImage = false;
        if (error.isConnectionError && !error.timedOut) {
          _pendingImageError = error.message;
        } else if (error.isConnectionError) {
          _pendingImageError =
              'Photo upload timed out. Check the connection and retry.';
        } else {
          _pendingImageError = error.message;
        }
      });
    }
  }

  /// Drops the pending photo (and orphans any in-flight upload).
  void _clearPendingImage() {
    _pickGen++;
    setState(() {
      _uploadedImagePath = null;
      _uploadedImageName = null;
      _uploadedFileId = null;
      _uploadedDims = null;
      _uploadingImage = false;
      _pendingImageError = null;
      if (_attachSource == 'uploaded') {
        _attachSource = 'cover';
      }
    });
    _publish();
  }

  /// Banner positions from the backend, falling back to the documented
  /// set when the backend is unreachable.
  List<String> get _bannerPositions {
    final Map<String, dynamic>? capabilities = _capabilities;
    final Object? raw = capabilities?['banner_positions'];
    if (raw is List) {
      final List<String> positions = raw.whereType<String>().toList();
      if (positions.isNotEmpty) {
        return positions;
      }
    }
    return const <String>[
      'top',
      'bottom',
      'top_left',
      'top_right',
      'bottom_left',
      'bottom_right',
      'center',
    ];
  }

  void _addAttachment() {
    if (_attachSource == 'uploaded' &&
        _uploadedImagePath != null &&
        _uploadedFileId == null) {
      setState(
        () => _attachmentError = _uploadingImage
            ? 'The photo is still uploading. Attach becomes available '
                'once the server confirms it.'
            : 'Upload the photo first: retry the failed upload or pick '
                'another photo.',
      );
      return;
    }
    final Map<String, String> sources = _attachSources();
    final String? path = sources[_attachSource];
    final String? fileId = _attachFileId(_attachSource);
    if (path == null || fileId == null) {
      setState(
        () => _attachmentError =
            'Make a cover, generate artwork, or pick and upload a photo '
            'first.',
      );
      return;
    }
    if (!File(path).existsSync()) {
      setState(
        () => _attachmentError =
            'That image is no longer on this device. Make or upload it '
            'again.',
      );
      return;
    }
    final String kind = _attachKind;
    double? duration;
    double? start;
    double? end;
    if (kind == 'intro' || kind == 'outro') {
      duration = double.tryParse(_stillDurController.text.trim());
      if (duration == null || duration < 0.5 || duration > 10) {
        setState(
          () => _attachmentError =
              'Intro and outro need a duration within 0.5–10s.',
        );
        return;
      }
    }
    if (kind == 'banner') {
      start = double.tryParse(_bannerStartController.text.trim());
      end = double.tryParse(_bannerEndController.text.trim());
      if (start == null || end == null || start < 0 || end <= start) {
        setState(
          () => _attachmentError =
              'The banner needs 0 ≤ start < end in edited-video seconds.',
        );
        return;
      }
      final double? span = _outputSpanEstimate();
      final double bannerEnd = end;
      if (span != null && bannerEnd > span + 1e-6) {
        final String message =
            'The banner ends at ${bannerEnd.toStringAsFixed(1)}s, past the '
            'estimated ${span.toStringAsFixed(1)}s output end. Shorten '
            'it or lengthen the edit.';
        setState(() => _attachmentError = message);
        return;
      }
    }
    final _ImageAttachment attachment = _ImageAttachment(
      kind: kind,
      path: path,
      fileId: fileId,
      name: _attachFileName(_attachSource, path),
      duration: duration,
      start: start,
      end: end,
      position: kind == 'banner' ? _bannerPosition : null,
      size: kind == 'banner' ? _bannerSize : null,
    );
    setState(() {
      // One attachment per kind: attaching again replaces it.
      _attachments = <_ImageAttachment>[
        for (final _ImageAttachment existing in _attachments)
          if (existing.kind != kind) existing,
        attachment,
      ];
      _attachmentError = null;
    });
    _publish();
  }

  void _removeAttachment(String kind) {
    setState(() {
      _attachments = <_ImageAttachment>[
        for (final _ImageAttachment existing in _attachments)
          if (existing.kind != kind) existing,
      ];
    });
    _publish();
  }

  /// Picks a supporting video from the gallery and uploads it as an
  /// asset. The clip itself never joins the story; placements show it
  /// over the story (cutaway/PiP) or inside it (insert).
  Future<void> _pickSupport() async {
    final Future<String?> Function()? pick = widget.pickSupport;
    final String? path = pick != null
        ? await pick()
        : (await ImagePicker().pickVideo(source: ImageSource.gallery))
              ?.path;
    if (!mounted) {
      return;
    }
    if (path == null || path.isEmpty) {
      return;
    }
    if (!File(path).existsSync()) {
      setState(
        () => _supportError =
            'That video is not on this device. Pick it again.',
      );
      return;
    }
    final int gen = ++_supportGen;
    setState(() {
      _supportPath = path;
      _supportName = VideoSelection(path).fileName;
      _supportFileId = null;
      _supportDuration = null;
      _uploadingSupport = true;
      _supportError = null;
    });
    await _uploadSupport(gen);
  }

  Future<void> _uploadSupport(int gen) async {
    final String? path = _supportPath;
    if (path == null) {
      return;
    }
    try {
      final Map<String, dynamic> uploaded = await _client.uploadFile(
        path,
        label: 'asset',
      );
      if (!mounted || gen != _supportGen) {
        return;
      }
      final String fileId = uploaded['file_id'] as String? ?? '';
      final Map<String, dynamic>? media =
          uploaded['media'] as Map<String, dynamic>?;
      setState(() {
        _supportFileId = _looksLikeFileId(fileId) ? fileId : null;
        _supportDuration =
            (media?['duration'] as num?)?.toDouble();
        _uploadingSupport = false;
        if (_supportFileId == null) {
          _supportError = 'The server did not confirm that upload.';
        }
      });
      _publish();
    } on BackendException catch (error) {
      if (!mounted || gen != _supportGen) {
        return;
      }
      setState(() {
        _uploadingSupport = false;
        _supportError = error.message;
      });
    }
  }

  void _removeSupportClip() {
    _supportGen++;
    setState(() {
      _supportPath = null;
      _supportName = null;
      _supportFileId = null;
      _supportDuration = null;
      _uploadingSupport = false;
      _supportError = null;
      _supportPlacements = <_SupportPlacement>[];
      _phraseHits = <_PhraseHit>[];
      _phraseMessage = null;
      _phraseChoice = null;
    });
    _publish();
  }

  void _removeSupportPlacement(int index) {
    if (index < 0 || index >= _supportPlacements.length) {
      return;
    }
    setState(() {
      _supportPlacements = List<_SupportPlacement>.of(_supportPlacements)
        ..removeAt(index);
    });
    _publish();
  }

  /// Kept source ranges from the current plan in story order, or null
  /// when no plan exists yet.
  List<List<double>>? _keptSourceRanges() {
    final Map<String, dynamic>? plan = _rawPlan;
    if (plan == null) {
      return null;
    }
    final List<dynamic> ops =
        (plan['operations'] as List?) ?? const <dynamic>[];
    final List<List<double>> ranges = <List<double>>[];
    for (final Object? raw in ops) {
      if (raw is! Map) {
        continue;
      }
      if (raw['op'] != 'trim' && raw['op'] != 'keep') {
        continue;
      }
      final double? start = (raw['start'] as num?)?.toDouble();
      final double? end = (raw['end'] as num?)?.toDouble();
      if (start == null || end == null || end <= start) {
        continue;
      }
      ranges.add(<double>[start, end]);
    }
    return ranges;
  }

  /// Source seconds to content seconds (after cuts and earlier inserts),
  /// or null when the moment was cut or no plan exists yet.
  double? _sourceToContent(double source, {List<_SupportPlacement>? inserts}) {
    final List<List<double>>? ranges = _keptSourceRanges();
    if (ranges == null) {
      return null;
    }
    double content = 0;
    bool kept = false;
    for (final List<double> range in ranges) {
      if (source < range[0] || source >= range[1]) {
        content += range[1] - range[0];
        continue;
      }
      content += source - range[0];
      kept = true;
      break;
    }
    if (!kept) {
      return null;
    }
    // Earlier inserts extend the content before this moment. Inserts
    // apply in position order; each extends what follows it.
    final List<_SupportPlacement> ordered = <_SupportPlacement>[
      ...(inserts ?? _supportPlacements),
    ].where((_SupportPlacement p) => p.kind == 'insert').toList();
    final List<double> raws = <double>[];
    for (final _SupportPlacement ins in ordered) {
      final double? raw = ins.sourceAt == null
          ? _outputToContentGuess(ins.at)
          : _sourceToContentRaw(ins.sourceAt!);
      raws.add(raw ?? double.infinity);
    }
    final List<int> order = List<int>.generate(
      ordered.length,
      (int i) => i,
    )..sort((int a, int b) => raws[a].compareTo(raws[b]));
    double shift = 0;
    for (final int i in order) {
      if (raws[i].isFinite && raws[i] <= content + shift) {
        shift += ordered[i].duration;
      }
    }
    return content + shift;
  }

  /// Source seconds to raw content seconds through cuts only.
  double? _sourceToContentRaw(double source) {
    final List<List<double>>? ranges = _keptSourceRanges();
    if (ranges == null) {
      return null;
    }
    double content = 0;
    for (final List<double> range in ranges) {
      if (source < range[0] || source >= range[1]) {
        content += range[1] - range[0];
        continue;
      }
      return content + (source - range[0]);
    }
    return null;
  }

  /// Edited-output seconds back to content seconds by removing the
  /// intro still. Used only to order inserts; the render converts
  /// insert positions exactly.
  double _outputToContentGuess(double at) {
    return (at - _introDuration()).clamp(0, double.infinity);
  }

  double _introDuration() => _introDurationOf(_attachments);

  static double _introDurationOf(List<_ImageAttachment> attachments) {
    for (final _ImageAttachment attachment in attachments) {
      if (attachment.kind == 'intro') {
        return attachment.duration ?? 0;
      }
    }
    return 0;
  }

  /// Source seconds to edited-output seconds, or null when the moment
  /// was cut or no plan exists yet.
  double? _sourceToOutput(double source) {
    final double? content = _sourceToContent(source);
    if (content == null) {
      return null;
    }
    return content + _introDuration();
  }

  /// Finds caption lines containing [phrase] (Tamil/English, any case)
  /// and resolves each to its story clip. Timed lines only: untimed
  /// guesses cannot place video precisely.
  List<_PhraseHit> _findPhrase(String phrase) {
    final String needle = phrase.trim().toLowerCase();
    if (needle.isEmpty) {
      return <_PhraseHit>[];
    }
    final List<_PhraseHit> hits = <_PhraseHit>[];
    for (final _CaptionLine line in _captionLines) {
      if (line.end <= line.start) {
        continue;
      }
      if (!line.controller.text.toLowerCase().contains(needle)) {
        continue;
      }
      int clipIndex = 0;
      String clipName = _clips.isNotEmpty
          ? _clips.first.displayName
          : 'the story';
      for (int i = 0; i < _clips.length; i++) {
        final _ClipSlot clip = _clips[i];
        final double start = clip.offset;
        final double end = start + (clip.duration ?? 0);
        if (line.start >= start &&
            (clip.duration == null || line.start < end)) {
          clipIndex = i;
          clipName = clip.displayName;
        }
      }
      hits.add(
        _PhraseHit(
          start: line.start,
          end: line.end,
          clipIndex: clipIndex,
          clipName: clipName,
          text: line.controller.text,
        ),
      );
    }
    return hits;
  }

  void _searchPhrase() {
    final String phrase = _supportPhraseController.text;
    if (_captionLines.isEmpty) {
      setState(() {
        _phraseHits = <_PhraseHit>[];
        _phraseChoice = null;
        _phraseMessage =
            'Transcribe the story speech first, then search for a phrase.';
      });
      return;
    }
    final List<_PhraseHit> hits = _findPhrase(phrase);
    setState(() {
      _phraseHits = hits;
      _phraseChoice = hits.length == 1 ? 0 : null;
      if (hits.isEmpty) {
        _phraseMessage = 'No line says “${phrase.trim()}”. Try fewer words.';
      } else if (hits.length == 1) {
        _phraseMessage = null;
      } else {
        _phraseMessage =
            'That phrase occurs ${hits.length} times — pick one below.';
      }
    });
  }

  String? _supportAudioError(String kind, String audio) {
    final List<String> allowed = switch (kind) {
      'cutaway' => const <String>['mute', 'mix', 'use'],
      'pip' => const <String>['mute', 'mix'],
      'insert' => const <String>['mute', 'use'],
      _ => const <String>['mute'],
    };
    if (!allowed.contains(audio)) {
      return 'That sound choice is not available for $kind.';
    }
    return null;
  }

  void _addSupport() {
    final String? fileId = _supportFileId;
    if (fileId == null) {
      setState(
        () => _supportAddError = 'Pick and upload a supporting video first.',
      );
      return;
    }
    final String kind = _supportKind;
    final String audio = _supportAudio;
    final String? audioError = _supportAudioError(kind, audio);
    if (audioError != null) {
      setState(() => _supportAddError = audioError);
      return;
    }
    final double? duration = double.tryParse(
      _supportDurationController.text.trim(),
    );
    final double maxDuration = kind == 'insert' ? 60.0 : 30.0;
    if (duration == null || duration < 0.5 || duration > maxDuration) {
      setState(
        () => _supportAddError =
            'That clip needs a duration within 0.5–$maxDuration s.',
      );
      return;
    }
    final double srcStart =
        double.tryParse(_supportSrcController.text.trim()) ?? -1;
    if (srcStart < 0) {
      setState(
        () => _supportAddError =
            'The supporting clip start needs 0s or more.',
      );
      return;
    }
    final double? supportLen = _supportDuration;
    if (supportLen != null && srcStart + duration > supportLen + 1e-6) {
      setState(
        () => _supportAddError =
            'That needs ${srcStart + duration} s of a '
            '${supportLen.toStringAsFixed(1)}s supporting clip. Shorten it.',
      );
      return;
    }
    String? position;
    double? widthFrac;
    if (kind == 'pip') {
      position = _supportPosition;
      widthFrac = _supportWidthFrac;
    }
    // Phrase placement re-resolves at render; edited-time placement is
    // fixed now.
    String? phrase;
    double? sourceAt;
    double at;
    if (_phraseChoice != null &&
        _phraseChoice! >= 0 &&
        _phraseChoice! < _phraseHits.length) {
      final _PhraseHit hit = _phraseHits[_phraseChoice!];
      final double? output = _sourceToOutput(hit.start);
      if (output == null) {
        setState(
          () => _supportAddError =
              'That line was cut from the edit. Restore it in Keep & '
              'remove, generate a plan first, or pick another phrase.',
        );
        return;
      }
      phrase = _supportPhraseController.text.trim();
      sourceAt = hit.start;
      at = output;
    } else {
      final double? typed = double.tryParse(
        _supportAtController.text.trim(),
      );
      if (typed == null || typed < 0) {
        setState(
          () => _supportAddError =
              'Enter edited-video seconds, or find a spoken phrase.',
        );
        return;
      }
      at = typed;
    }
    if (kind == 'insert' && at < _introDuration() - 1e-6) {
      setState(
        () => _supportAddError =
            'That moment is inside the intro still — inserts join the '
            'story footage, after the intro.',
      );
      return;
    }
    final double? span = _outputSpanEstimate();
    if (span != null && at > span + 1e-6) {
      setState(
        () => _supportAddError =
            'That is past the estimated ${span.toStringAsFixed(1)}s edit. '
            'Shorten the time or generate a plan first.',
      );
      return;
    }
    setState(() {
      _supportPlacements = List<_SupportPlacement>.of(_supportPlacements)
        ..add(
          _SupportPlacement(
            kind: kind,
            fileId: fileId,
            devicePath: _supportPath,
            name: _supportName,
            at: at,
            duration: duration,
            srcStart: srcStart,
            position: position,
            widthFrac: widthFrac,
            audio: audio,
            phrase: phrase,
            sourceAt: sourceAt,
          ),
        );
      _supportAddError = null;
      _phraseHits = <_PhraseHit>[];
      _phraseChoice = null;
      _phraseMessage = null;
    });
    _publish();
  }

  /// Estimated edited length from the current plan's cuts, when the cuts
  /// alone determine it (trim/keep, minus dissolves). Null when unknown.
  double? _estimatedOutputLen() {
    final Map<String, dynamic>? plan = _rawPlan;
    if (plan == null) {
      return null;
    }
    final List<dynamic> ops =
        (plan['operations'] as List?) ?? const <dynamic>[];
    double total = 0;
    int pieces = 0;
    double dissolve = 0;
    for (final Object? raw in ops) {
      if (raw is! Map) {
        continue;
      }
      final String op = raw['op'] as String? ?? '';
      if (op == 'trim' || op == 'keep') {
        final double? start = (raw['start'] as num?)?.toDouble();
        final double? end = (raw['end'] as num?)?.toDouble();
        if (start == null || end == null || end <= start) {
          return null;
        }
        total += end - start;
        pieces++;
      } else if (op == 'remove') {
        return null; // Needs the source duration, which lives server-side.
      } else if (op == 'transition') {
        dissolve = (raw['duration'] as num?)?.toDouble() ?? 0;
      }
    }
    if (pieces == 0) {
      return null;
    }
    if (pieces > 1 && dissolve > 0) {
      total -= dissolve * (pieces - 1);
    }
    return total > 0 ? total : null;
  }

  /// explicit_cuts request body for [cuts], or null when nothing usable
  /// is chosen. Mixed keep/remove lists are a conflict: nothing is sent
  /// and [_cutsBlocker] stops generation with a message instead.
  static Map<String, dynamic>? _explicitCutsBody(List<_CutRange> cuts) {
    if (cuts.isEmpty) {
      return null;
    }
    final bool keep = cuts.first.keep;
    if (cuts.any((_CutRange cut) => cut.keep != keep)) {
      return null;
    }
    return <String, dynamic>{
      'mode': keep ? 'keep' : 'remove',
      'ranges': <List<double>>[
        for (final _CutRange cut in cuts) <double>[cut.start, cut.end],
      ],
    };
  }

  /// Clear blocker message when the cut list cannot render, else null.
  /// Conflict rule: one cutting mode only — keeps and removes never mix.
  String? _cutsBlocker([List<_CutRange>? cuts]) {
    final List<_CutRange> rows = cuts ?? _cuts;
    if (rows.isEmpty) {
      return null;
    }
    if (rows.any((_CutRange cut) => cut.keep) &&
        rows.any((_CutRange cut) => !cut.keep)) {
      return 'Keep and Remove cannot combine: the render uses one cutting '
          'mode. Remove one list to continue.';
    }
    if (rows.length > 8) {
      return 'At most 8 intervals are supported; remove extras.';
    }
    final List<_CutRange> ordered = List<_CutRange>.of(rows)
      ..sort((_CutRange a, _CutRange b) => a.start.compareTo(b.start));
    for (int i = 0; i + 1 < ordered.length; i++) {
      if (ordered[i + 1].start < ordered[i].end) {
        return 'Cut intervals must not overlap.';
      }
    }
    final double? duration = _sourceDurationSec;
    if (duration != null) {
      for (final _CutRange cut in rows) {
        if (cut.end > duration + 1e-6) {
          return 'An interval runs past the '
              '${duration.toStringAsFixed(1)}s source video.';
        }
      }
    }
    return null;
  }

  /// Estimated edited length from explicit cuts, else null.
  double? _cutsOutputLen([List<_CutRange>? cuts]) {
    final List<_CutRange> rows = cuts ?? _cuts;
    if (rows.isEmpty || _cutsBlocker(rows) != null) {
      return null;
    }
    if (rows.first.keep) {
      return rows.fold<double>(0, (double sum, _CutRange cut) {
        return sum + (cut.end - cut.start);
      });
    }
    final double? duration = _sourceDurationSec;
    if (duration == null) {
      return null;
    }
    return duration -
        rows.fold<double>(0, (double sum, _CutRange cut) {
          return sum + (cut.end - cut.start);
        });
  }

  /// Output-span preview per row in row order. Keep rows show where
  /// they play in the edit; remove rows show null (they are cut out).
  List<List<double>?> _cutOutputSpans() {
    if (_cuts.isEmpty || _cutsBlocker() != null || !_cuts.first.keep) {
      return List<List<double>?>.filled(_cuts.length, null);
    }
    final List<List<double>?> spans = <List<double>?>[];
    double out = 0;
    for (final _CutRange cut in _cuts) {
      final double len = cut.end - cut.start;
      spans.add(<double>[out, out + len]);
      out += len;
    }
    return spans;
  }

  /// Best-known edited-output length: explicit cuts first, then the
  /// current plan, then the full source when nothing is cut, plus any
  /// attached intro/outro stills (they extend the final timeline).
  double? _outputSpanEstimate() {
    final double? base =
        _cutsOutputLen() ?? _estimatedOutputLen() ?? _sourceDurationSec;
    if (base == null) {
      return null;
    }
    double total = base;
    for (final _ImageAttachment attachment in _attachments) {
      if (attachment.kind == 'intro' || attachment.kind == 'outro') {
        total += attachment.duration ?? 0;
      }
    }
    for (final _SupportPlacement placement in _supportPlacements) {
      if (placement.kind == 'insert') {
        total += placement.duration;
      }
    }
    return total;
  }

  Future<void> _addCut() async {
    final double? start = double.tryParse(_cutStartController.text.trim());
    final double? end = double.tryParse(_cutEndController.text.trim());
    if (start == null || end == null || start < 0 || end <= start) {
      setState(
        () => _cutsError =
            'Enter a valid interval: 0 ≤ start < end, in source seconds.',
      );
      return;
    }
    try {
      await _ensureUpload();
    } on BackendException catch (error) {
      if (error.isConnectionError) {
        await _handleConnectionError(
          error,
          (String message) => setState(() => _cutsError = message),
          'Measuring the source video',
        );
        return;
      }
      setState(
        () =>
            _cutsError = 'Could not measure the source video: ${error.message}',
      );
      return;
    }
    final double? duration = _sourceDurationSec;
    if (duration != null && end > duration + 1e-6) {
      setState(
        () => _cutsError =
            'That runs past the ${duration.toStringAsFixed(1)}s source video.',
      );
      return;
    }
    if (_cuts.length >= 8) {
      setState(() => _cutsError = 'At most 8 intervals are supported.');
      return;
    }
    final _CutRange candidate = _CutRange(_cutKeepMode, start, end);
    final List<_CutRange> sameMode = <_CutRange>[
      for (final _CutRange cut in _cuts)
        if (cut.keep == candidate.keep) cut,
      candidate,
    ]..sort((_CutRange a, _CutRange b) => a.start.compareTo(b.start));
    for (int i = 0; i + 1 < sameMode.length; i++) {
      if (sameMode[i + 1].start < sameMode[i].end) {
        setState(() => _cutsError = 'Intervals must not overlap.');
        return;
      }
    }
    setState(() {
      _cuts = List<_CutRange>.of(_cuts)..add(candidate);
      _cutsError = null;
      _cutStartController.clear();
      _cutEndController.clear();
    });
    _publish();
  }

  void _removeCut(int index) {
    setState(() {
      _cuts = List<_CutRange>.of(_cuts)..removeAt(index);
      _cutsError = null;
    });
    _publish();
  }

  void _addSfx() {
    final double? at = double.tryParse(_sfxAtController.text.trim());
    if (at == null || at < 0) {
      setState(
        () => _sfxError = 'Enter the effect time in edited-video seconds.',
      );
      return;
    }
    final double? outLen = _outputSpanEstimate();
    if (outLen != null && at >= outLen - 0.001) {
      setState(
        () => _sfxError =
            'That lands at the ${outLen.toStringAsFixed(1)}s output end, '
            'so it would be inaudible. Move it earlier.',
      );
      return;
    }
    setState(() {
      _sfxPicks = List<_SfxPick>.from(_sfxPicks)..add(_SfxPick(_sfxAsset, at));
      _sfxError = null;
      _sfxAtController.clear();
    });
    _publish();
  }

  String _extrasSummary() {
    final List<String> parts = <String>[];
    if (_cuts.isNotEmpty) {
      final bool keep = _cuts.first.keep;
      final bool mixed = _cuts.any((_CutRange cut) => cut.keep != keep);
      parts.add(
        mixed
            ? 'cut conflict (resolve below)'
            : keep
            ? '${_cuts.length} kept intervals'
            : '${_cuts.length} removed intervals',
      );
    }
    if (_captionChoice == CaptionChoice.on) {
      parts.add(
        _captionLines.isEmpty
            ? 'captions from speech'
            : '${_captionLines.length} caption lines',
      );
    }
    if (_musicPath != null) {
      parts.add('music');
    }
    if (_sfxPicks.isNotEmpty) {
      parts.add('${_sfxPicks.length} sound effects');
    }
    for (final _ImageAttachment attachment in _attachments) {
      parts.add(attachment.describe());
    }
    for (final _SupportPlacement placement in _supportPlacements) {
      parts.add(placement.describe());
    }
    return parts.join(', ');
  }

  /// Short notes for extras the user's choices suppress.
  List<String> _suppressions() {
    final List<String> notes = <String>[];
    final EditingPlan? plan = _plan;
    if (_captionChoice == CaptionChoice.off &&
        plan != null &&
        plan.operations.any((PlanOperation op) => op.op == 'captions')) {
      notes.add('Captions suppressed (your choice: Off).');
    }
    return notes;
  }

  Future<void> _export() async {
    final String? path = _renderPath;
    if (_sharing || path == null) {
      return;
    }
    setState(() => _sharing = true);
    try {
      final Future<void> Function(String path)? share = widget.onShare;
      if (share != null) {
        await share(path);
        if (!mounted) {
          return;
        }
        setState(() => _sharing = false);
        return;
      }
      final ShareResult result = await SharePlus.instance.share(
        ShareParams(files: <XFile>[XFile(path)], text: 'Made with 23Editor'),
      );
      if (!mounted) {
        return;
      }
      setState(() => _sharing = false);
      if (result.status == ShareResultStatus.unavailable) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('Sharing is unavailable here.')),
        );
      }
    } catch (_) {
      if (mounted) {
        setState(() => _sharing = false);
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('Share failed to open sharing.')),
        );
      }
    }
  }

  Future<void> _generateThumbnail() async {
    if (_thumbBusy || _thumbPromptController.text.trim().isEmpty) {
      return;
    }
    setState(() {
      _thumbBusy = true;
      _thumbError = null;
      _thumbWantsCover = false;
      _connectionError = null;
    });
    try {
      final Map<String, dynamic> body = <String, dynamic>{
        'prompt': _thumbPromptController.text,
      };
      final Map<String, dynamic>? analysis = _currentAnalysis;
      if (analysis != null) {
        body['analysis'] = analysis;
      }
      final Map<String, dynamic> json = await _client.postJson(
        '/api/thumbnail',
        body,
        timeout: const Duration(minutes: 5),
      );
      final Directory temp = await getTemporaryDirectory();
      if (!temp.existsSync()) {
        temp.createSync(recursive: true);
      }
      final String dest =
          '${temp.path}/thumb_${DateTime.now().millisecondsSinceEpoch}.png';
      await _client.downloadToFile(json['url'] as String? ?? '', dest);
      if (!mounted) {
        return;
      }
      setState(() {
        _thumbPath = dest;
        _thumbImageId = _coverArtworkId(
          json['url'] as String? ?? '',
          json['image_id'] as String?,
        );
        _thumbBusy = false;
      });
      _saveProject(
        _renderPath == null
            ? (_plan == null ? 'draft' : 'planned')
            : 'rendered',
      );
    } on BackendException catch (error) {
      if (!mounted) {
        return;
      }
      setState(() => _thumbBusy = false);
      if (error.isConnectionError) {
        await _handleConnectionError(
          error,
          (String message) => setState(() {
            _thumbError = message;
            _thumbWantsCover = false;
          }),
          'Artwork',
        );
      } else {
        // A clarification question or a policy refusal is shown as-is:
        // never retried with a disguised prompt.
        setState(() {
          _thumbError = error.message;
          _thumbWantsCover = error.details['suggest_cover'] == true;
        });
      }
    }
  }

  Future<void> _makeCover() async {
    if (_coverBusy) {
      return;
    }
    final double? at = double.tryParse(_coverAtController.text.trim());
    if (at == null || at < 0) {
      setState(() => _coverError = 'Enter the frame time in seconds.');
      return;
    }
    if (_coverTitleController.text.trim().isEmpty) {
      setState(() => _coverError = 'Type the cover title first.');
      return;
    }
    setState(() {
      _coverBusy = true;
      _coverError = null;
      _connectionError = null;
    });
    try {
      final String fileId = await _ensureUpload();
      final Map<String, dynamic> json = await _client
          .postJson('/api/cover', <String, dynamic>{
            'file_id': fileId,
            't': at,
            'title': _coverTitleController.text.trim(),
            'position': _coverPosition,
          }, timeout: const Duration(minutes: 5));
      final Directory temp = await getTemporaryDirectory();
      if (!temp.existsSync()) {
        temp.createSync(recursive: true);
      }
      final String dest =
          '${temp.path}/cover_${DateTime.now().millisecondsSinceEpoch}.png';
      await _client.downloadToFile(json['url'] as String? ?? '', dest);
      if (!mounted) {
        return;
      }
      setState(() {
        _coverPath = dest;
        _coverImageId = _coverArtworkId(
          json['url'] as String? ?? '',
          json['image_id'] as String?,
        );
        _coverBusy = false;
      });
    } on BackendException catch (error) {
      if (!mounted) {
        return;
      }
      setState(() => _coverBusy = false);
      if (error.isConnectionError) {
        // Cover creation is local-only: a timeout with a reachable
        // backend stays a cover error, never a disconnect card.
        await _handleConnectionError(
          error,
          (String message) => setState(() => _coverError = message),
          'Cover',
        );
      } else {
        setState(() => _coverError = error.message);
      }
    }
  }

  Future<void> _saveThumbnail() async {
    final String? path = _thumbPath;
    if (_thumbSaving || path == null) {
      return;
    }
    setState(() {
      _thumbSaving = true;
      _thumbError = null;
    });
    try {
      final Future<void> Function(String path)? save = widget.onSaveThumbnail;
      if (save != null) {
        await save(path);
      } else {
        if (!await Gal.hasAccess()) {
          final bool granted = await Gal.requestAccess();
          if (!granted) {
            if (mounted) {
              setState(() {
                _thumbSaving = false;
                _thumbError =
                    'Gallery permission was denied, so the image was '
                    'not saved.';
              });
            }
            return;
          }
        }
        await Gal.putImage(path, album: '23Editor');
      }
      if (!mounted) {
        return;
      }
      setState(() => _thumbSaving = false);
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Thumbnail saved to the gallery.')),
      );
    } on GalException catch (error) {
      if (!mounted) {
        return;
      }
      setState(() {
        _thumbSaving = false;
        _thumbError = switch (error.type) {
          GalExceptionType.accessDenied =>
            'Gallery permission was denied, so the image was not saved.',
          GalExceptionType.notEnoughSpace =>
            'Not enough space to save the image.',
          GalExceptionType.notSupportedFormat =>
            'This image format cannot be saved to the gallery.',
          _ => 'Could not save the image to the gallery.',
        };
      });
    } catch (_) {
      if (mounted) {
        setState(() {
          _thumbSaving = false;
          _thumbError = 'Could not save the image to the gallery.';
        });
      }
    }
  }

  Future<void> _shareThumbnail() async {
    final String? path = _thumbPath;
    if (_thumbSharing || path == null) {
      return;
    }
    setState(() {
      _thumbSharing = true;
      _thumbError = null;
    });
    try {
      final Future<void> Function(String path)? share = widget.onShareThumbnail;
      if (share != null) {
        await share(path);
      } else {
        final ShareResult result = await SharePlus.instance.share(
          ShareParams(files: <XFile>[XFile(path)], text: 'Made with 23Editor'),
        );
        if (!mounted) {
          return;
        }
        if (result.status == ShareResultStatus.unavailable) {
          ScaffoldMessenger.of(context).showSnackBar(
            const SnackBar(content: Text('Sharing is unavailable here.')),
          );
        }
      }
      if (mounted) {
        setState(() => _thumbSharing = false);
      }
    } catch (_) {
      if (mounted) {
        setState(() {
          _thumbSharing = false;
          _thumbError = 'Share failed to open sharing.';
        });
      }
    }
  }

  /// Save/share twins for the footage-frame cover slot.
  Future<void> _saveCover() async {
    final String? path = _coverPath;
    if (_thumbSaving || path == null) {
      return;
    }
    setState(() {
      _thumbSaving = true;
      _coverError = null;
    });
    try {
      final Future<void> Function(String path)? save = widget.onSaveThumbnail;
      if (save != null) {
        await save(path);
      } else {
        if (!await Gal.hasAccess()) {
          if (!await Gal.requestAccess()) {
            if (mounted) {
              setState(() {
                _thumbSaving = false;
                _coverError = 'Gallery permission was denied.';
              });
            }
            return;
          }
        }
        await Gal.putImage(path, album: '23Editor');
      }
      if (!mounted) {
        return;
      }
      setState(() => _thumbSaving = false);
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Cover saved to the gallery.')),
      );
    } catch (_) {
      if (mounted) {
        setState(() {
          _thumbSaving = false;
          _coverError = 'Could not save the cover to the gallery.';
        });
      }
    }
  }

  /// Device path of the attached cover image, if any.
  String? _attachedCoverPath() {
    for (final _ImageAttachment attachment in _attachments) {
      if (attachment.kind == 'cover') {
        return attachment.path;
      }
    }
    return null;
  }

  _ImageAttachment? _attachedCover() {
    for (final _ImageAttachment attachment in _attachments) {
      if (attachment.kind == 'cover') {
        return attachment;
      }
    }
    return null;
  }

  /// A readable device file for the attached cover: the stored path
  /// when it still exists, otherwise a fresh download of the
  /// server-confirmed asset (covers survive project reopening even
  /// after the temp copy is gone).
  Future<String> _coverExportPath(_ImageAttachment attachment) async {
    if (File(attachment.path).existsSync()) {
      return attachment.path;
    }
    final String? fileId = attachment.fileId;
    if (fileId == null || !_looksLikeFileId(fileId)) {
      throw BackendException(
        'The attached cover is no longer on this device. Attach it again.',
      );
    }
    final Directory temp = await getTemporaryDirectory();
    final String dest =
        '${temp.path}/cover_${DateTime.now().millisecondsSinceEpoch}.png';
    try {
      await _client.downloadToFile('/api/files/$fileId', dest);
    } on BackendException {
      rethrow;
    } catch (error) {
      throw BackendException('Could not download the attached cover: $error');
    }
    return dest;
  }

  /// The attached cover beside the edited video: separate exports for
  /// the MP4 and the cover image.
  Widget _buildAttachedCoverRow(TextTheme text) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: <Widget>[
        Text(
          'Cover image attached — export it beside the video.',
          style: text.bodySmall,
        ),
        const SizedBox(height: 8),
        Wrap(
          spacing: 8,
          runSpacing: 8,
          children: <Widget>[
            OutlinedButton.icon(
              key: const ValueKey<String>('saveAttachedCoverButton'),
              onPressed: _thumbSaving ? null : _saveAttachedCover,
              icon: const Icon(Icons.save_alt_outlined),
              label: Text(_thumbSaving ? 'Saving…' : 'Save cover'),
            ),
            OutlinedButton.icon(
              key: const ValueKey<String>('shareAttachedCoverButton'),
              onPressed: _thumbSharing ? null : _shareAttachedCover,
              icon: const Icon(Icons.ios_share_outlined),
              label: Text(_thumbSharing ? 'Sharing…' : 'Share cover'),
            ),
          ],
        ),
        if (_attachedCoverError != null) ...<Widget>[
          const SizedBox(height: 8),
          _InlineError(message: _attachedCoverError!),
        ],
      ],
    );
  }

  Future<void> _saveAttachedCover() async {
    final _ImageAttachment? cover = _attachedCover();
    if (_thumbSaving || cover == null) {
      return;
    }
    setState(() {
      _thumbSaving = true;
      _attachedCoverError = null;
    });
    try {
      final String path = await _coverExportPath(cover);
      final Future<void> Function(String path)? save = widget.onSaveThumbnail;
      if (save != null) {
        await save(path);
      } else {
        if (!await Gal.hasAccess()) {
          if (!await Gal.requestAccess()) {
            if (mounted) {
              setState(() {
                _thumbSaving = false;
                _attachedCoverError = 'Gallery permission was denied.';
              });
            }
            return;
          }
        }
        await Gal.putImage(path, album: '23Editor');
      }
      if (!mounted) {
        return;
      }
      setState(() => _thumbSaving = false);
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Cover saved to the gallery.')),
      );
    } on BackendException catch (error) {
      if (mounted) {
        setState(() {
          _thumbSaving = false;
          _attachedCoverError = error.message;
        });
      }
    } catch (_) {
      if (mounted) {
        setState(() {
          _thumbSaving = false;
          _attachedCoverError = 'Could not save the cover to the gallery.';
        });
      }
    }
  }

  Future<void> _shareAttachedCover() async {
    final _ImageAttachment? cover = _attachedCover();
    if (_thumbSharing || cover == null) {
      return;
    }
    setState(() {
      _thumbSharing = true;
      _attachedCoverError = null;
    });
    try {
      final String path = await _coverExportPath(cover);
      final Future<void> Function(String path)? share = widget.onShareThumbnail;
      if (share != null) {
        await share(path);
      } else {
        final ShareResult result = await SharePlus.instance.share(
          ShareParams(files: <XFile>[XFile(path)], text: 'Made with 23Editor'),
        );
        if (!mounted) {
          return;
        }
        if (result.status == ShareResultStatus.unavailable) {
          ScaffoldMessenger.of(context).showSnackBar(
            const SnackBar(content: Text('Sharing is unavailable here.')),
          );
        }
      }
      if (mounted) {
        setState(() => _thumbSharing = false);
      }
    } on BackendException catch (error) {
      if (mounted) {
        setState(() {
          _thumbSharing = false;
          _attachedCoverError = error.message;
        });
      }
    } catch (_) {
      if (mounted) {
        setState(() {
          _thumbSharing = false;
          _attachedCoverError = 'Share failed to open sharing.';
        });
      }
    }
  }

  Future<void> _shareCover() async {
    final String? path = _coverPath;
    if (_thumbSharing || path == null) {
      return;
    }
    setState(() {
      _thumbSharing = true;
      _coverError = null;
    });
    try {
      final Future<void> Function(String path)? share = widget.onShareThumbnail;
      if (share != null) {
        await share(path);
      } else {
        final ShareResult result = await SharePlus.instance.share(
          ShareParams(files: <XFile>[XFile(path)], text: 'Made with 23Editor'),
        );
        if (!mounted) {
          return;
        }
        if (result.status == ShareResultStatus.unavailable) {
          ScaffoldMessenger.of(context).showSnackBar(
            const SnackBar(content: Text('Sharing is unavailable here.')),
          );
        }
      }
      if (mounted) {
        setState(() => _thumbSharing = false);
      }
    } catch (_) {
      if (mounted) {
        setState(() {
          _thumbSharing = false;
          _coverError = 'Share failed to open sharing.';
        });
      }
    }
  }

  /// Picks a separate reference video and uploads it labeled so the
  /// backend never confuses it with the user's source footage.
  Future<void> _pickReference() async {
    if (_referenceBusy) {
      return;
    }
    setState(() {
      _referenceBusy = true;
      _referenceError = null;
    });
    try {
      final Future<String?> Function()? picker = widget.pickReferenceVideo;
      final String? pickedPath = picker != null
          ? await picker()
          : (await ImagePicker().pickVideo(source: ImageSource.gallery))?.path;
      if (pickedPath == null) {
        if (mounted) {
          setState(() => _referenceBusy = false);
        }
        return;
      }
      final Map<String, dynamic> uploaded = await _client.uploadFile(
        pickedPath,
        label: 'reference',
      );
      final String fileId = uploaded['file_id'] as String? ?? '';
      if (fileId.isEmpty) {
        throw BackendException('Reference upload returned no file id.');
      }
      if (!mounted) {
        return;
      }
      setState(() {
        _referencePath = pickedPath;
      });
      final Map<String, dynamic> json = await _client.postJson(
        '/api/reference/analyze',
        <String, dynamic>{'file_id': fileId},
        timeout: const Duration(minutes: 10),
      );
      if (!mounted) {
        return;
      }
      setState(() {
        _referenceBusy = false;
        _referenceStyle = json['style'] is Map
            ? Map<String, dynamic>.from(json['style'] as Map)
            : null;
      });
    } on BackendException catch (error) {
      if (!mounted) {
        return;
      }
      setState(() {
        _referenceBusy = false;
        _referenceError = error.message;
      });
      if (error.isConnectionError) {
        // Reference analysis is local-only: a timeout with a reachable
        // backend stays a reference error, never a disconnect card.
        await _handleConnectionError(
          error,
          (String message) => setState(() => _referenceError = message),
          'Reference analysis',
        );
      }
    }
  }

  /// Applies the reference style to the USER's footage: an explicit
  /// aspect choice plus a pacing note in the instruction. Never copies
  /// reference timestamps, dialogue, music, or footage.
  void _applyReference() {
    final Map<String, dynamic>? style = _referenceStyle;
    if (style == null) {
      return;
    }
    final String refAspect = style['aspect'] as String? ?? 'original';
    if (_adoptReferenceAspect && refAspect != 'original') {
      for (final AspectRatioOption option in AspectRatioOption.values) {
        if (option.backendValue == refAspect) {
          setState(() => _aspectRatio = option);
        }
      }
    }
    final Object? avgShot = style['avg_shot_len'];
    final Object? shots = style['shots'];
    final String note =
        'Match a ${refAspect == 'original' ? 'flexible' : refAspect} '
        'reference with about $shots shots '
        '(average shot ${avgShot}s); adapt the pacing to my footage.';
    final String current = _instructionController.text.trim();
    _instructionController.text = current.isEmpty ? note : '$current $note';
    _publish();
  }

  /// Opens the in-app brief builder; "Use this edit" fills the
  /// instruction and invalidates stale plans through the listener.
  void _openBrief({BriefGoal goal = BriefGoal.custom}) {
    showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      builder: (BuildContext context) => BriefSheet(
        client: _client,
        analysis: _currentAnalysis,
        referenceStyle: _referenceStyle,
        initialGoal: goal,
        initialDetail: _instructionController.text.trim(),
        onUse: (String brief) {
          _instructionController.text = brief;
        },
      ),
    );
  }

  void _openStatus() {
    Navigator.of(
      context,
    ).push(MaterialPageRoute<void>(builder: (_) => const StatusScreen()));
  }

  bool get _captionsUsable =>
      _captionLines.isNotEmpty &&
      _captionLines.every(
        (_CaptionLine line) =>
            line.end > line.start && line.controller.text.trim().isNotEmpty,
      );

  /// Step-by-step planning controls. Collapsed by default so the one
  /// primary action stays prominent; the detailed errors live here
  /// instead of duplicating the job error above.
  Widget _buildAdvancedSection(TextTheme text, bool hasPlan) {
    return SectionCard(
      // Keyed: inserting or removing the plan card above must not remount
      // this section and snap a user's open Advanced panel shut.
      key: const ValueKey<String>('advancedCard'),
      title: 'Advanced: plan & retry',
      icon: Icons.settings_outlined,
      helper: 'Plan and render one step at a time.',
      child: ExpansionTile(
        tilePadding: EdgeInsets.zero,
        childrenPadding: EdgeInsets.zero,
        title: Text('Show step-by-step controls', style: text.titleSmall),
        children: <Widget>[
          PrimaryButton(
            key: const ValueKey<String>('generatePlanButton'),
            label: _planning ? 'Planning…' : 'Generate plan',
            icon: Icons.auto_awesome_outlined,
            onPressed: _jobRunning || _planning || _uploading
                ? null
                : _generatePlan,
          ),
          if (_planError != null) ...<Widget>[
            const SizedBox(height: 10),
            _InlineError(message: _planError!),
          ],
          const SizedBox(height: 12),
          PrimaryButton(
            key: const ValueKey<String>('renderVideoButton'),
            label: _rendering ? 'Rendering…' : 'Render video',
            icon: Icons.movie_creation_outlined,
            onPressed: !hasPlan || _rendering || _uploading ? null : _render,
          ),
          if (!hasPlan) ...<Widget>[
            const SizedBox(height: 4),
            Text(
              'Generate a plan first.',
              style: text.bodySmall,
              textAlign: TextAlign.center,
            ),
          ],
          if (_renderError != null) ...<Widget>[
            const SizedBox(height: 10),
            _InlineError(message: _renderError!),
          ],
        ],
      ),
    );
  }

  String _editedVideoHelper() {
    final List<String> parts = <String>[];
    final double? durationSec = _renderDurationSec;
    if (durationSec != null) {
      parts.add(
        formatClipDuration(
          Duration(milliseconds: (durationSec * 1000).round()),
        ),
      );
    }
    final int? width = _renderWidth;
    final int? height = _renderHeight;
    if (width != null && height != null) {
      parts.add('$width×$height');
    }
    final String? sar = _renderSar;
    if (sar != null && sar != '1:1' && sar != '0:1') {
      parts.add('SAR $sar');
    }
    parts.add('Saved on this device.');
    return 'Measured output: ${parts.join(' • ')}';
  }

  /// Short "What I found in your video" lines. Suggestions stay
  /// suggestions: the user confirms or corrects them.
  List<String> _analysisSummary(Map<String, dynamic> analysis) {
    final List<String> lines = <String>[];
    final double duration = (analysis['duration'] as num?)?.toDouble() ?? 0;
    lines.add(
      '${formatClipDuration(Duration(milliseconds: (duration * 1000).round()))} • '
      '${analysis['orientation'] ?? '?'} • '
      "${analysis['width'] ?? '?'}×${analysis['height'] ?? '?'}",
    );
    final Map<String, dynamic>? audio = analysis['audio'] is Map
        ? Map<String, dynamic>.from(analysis['audio'] as Map)
        : null;
    final Map<String, dynamic>? speech = analysis['speech'] is Map
        ? Map<String, dynamic>.from(analysis['speech'] as Map)
        : null;
    final String audioClass = audio?['class'] as String? ?? 'unknown';
    final int words = (speech?['words'] as num?)?.toInt() ?? 0;
    lines.add(switch (audioClass) {
      'no_stream' => 'Sound: this video has no audio track.',
      'silence' => 'Sound: silent — captions need written titles.',
      'non_silent' when words > 0 =>
        'Sound: speech ($words words transcribed).',
      'non_silent' => 'Sound: music or background, no speech detected.',
      _ => 'Sound: unknown.',
    });
    final List<dynamic> cuts = (analysis['cuts'] as List?) ?? const <dynamic>[];
    lines.add(
      cuts.isEmpty
          ? 'Cuts: one continuous shot.'
          : 'Cuts: ${cuts.length} detected.',
    );
    final Map<String, dynamic>? hint = analysis['content_hint'] is Map
        ? Map<String, dynamic>.from(analysis['content_hint'] as Map)
        : null;
    if (hint != null) {
      lines.add(
        'Looks like: ${hint['guess'] ?? '?'} '
        '(${(hint['confidence'] ?? 'low')} confidence — please confirm).',
      );
    }
    return lines;
  }

  /// Footage-specific goal suggestions from the content hint.
  List<BriefGoal> _suggestionGoals(Map<String, dynamic> analysis) {
    final Map<String, dynamic>? hint = analysis['content_hint'] is Map
        ? Map<String, dynamic>.from(analysis['content_hint'] as Map)
        : null;
    final List<BriefGoal> goals = <BriefGoal>[
      switch (hint?['guess']) {
        'talking' => BriefGoal.captions,
        'music_or_ambience' => BriefGoal.reel,
        'silent' => BriefGoal.travel,
        'montage' => BriefGoal.reel,
        _ => BriefGoal.custom,
      },
    ];
    if (hint?['guess'] == 'talking') {
      goals.addAll(<BriefGoal>[BriefGoal.tutorial, BriefGoal.reel]);
    } else if (hint?['guess'] == 'silent') {
      goals.add(BriefGoal.product);
    } else if (goals.first != BriefGoal.custom) {
      goals.add(BriefGoal.custom);
    }
    if (_referenceStyle != null && !goals.contains(BriefGoal.reference)) {
      goals.add(BriefGoal.reference);
    }
    return goals;
  }

  Widget _buildAnalysisCard(TextTheme text) {
    final Map<String, dynamic>? analysis = _currentAnalysis;
    return SectionCard(
      key: const ValueKey<String>('analysisCard'),
      title: 'Understand your footage',
      icon: Icons.search_outlined,
      helper: analysis == null
          ? 'Find out what is in the video before choosing edits.'
          : 'Observations only — confirm or correct them below.',
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: <Widget>[
          if (analysis == null) ...<Widget>[
            OutlinedButton.icon(
              key: const ValueKey<String>('analyzeButton'),
              onPressed: _analyzing ? null : _startAnalysis,
              icon: const Icon(Icons.search_outlined),
              label: Text(
                _analyzing ? 'Analyzing…' : "Find what's in my video",
              ),
            ),
            if (_analyzing) ...<Widget>[
              const SizedBox(height: 8),
              Text(_analysisStage, style: text.bodySmall),
              const SizedBox(height: 8),
              OutlinedButton.icon(
                key: const ValueKey<String>('cancelAnalysisButton'),
                onPressed: _cancelAnalysis,
                icon: const Icon(Icons.close_outlined),
                label: const Text('Cancel'),
              ),
            ],
            if (_analysisError != null) ...<Widget>[
              const SizedBox(height: 8),
              _InlineError(message: _analysisError!),
              const SizedBox(height: 8),
              OutlinedButton.icon(
                onPressed: _analyzing ? null : _startAnalysis,
                icon: const Icon(Icons.refresh_outlined),
                label: const Text('Retry analysis'),
              ),
            ],
          ] else ...<Widget>[
            Text('What I found in your video', style: text.titleSmall),
            const SizedBox(height: 4),
            for (final String line in _analysisSummary(analysis))
              Padding(
                padding: const EdgeInsets.only(top: 2),
                child: Text('• $line', style: text.bodySmall),
              ),
            const SizedBox(height: 4),
            Text(
              (analysis['frame_budget_note'] as String?) ?? '',
              style: text.bodySmall,
            ),
            if (_analysisDuration != null) ...<Widget>[
              const SizedBox(height: 4),
              Text(
                _analysisReused
                    ? 'Reused the cached analysis in '
                        '${_formatDuration(_analysisDuration!)}.'
                    : 'Analyzed in ${_formatDuration(_analysisDuration!)}.',
                key: const ValueKey<String>('analysisTimingsLine'),
                style: text.bodySmall,
              ),
            ],
            if (_serverAnalysisLine(analysis).isNotEmpty) ...<Widget>[
              const SizedBox(height: 2),
              Text(
                _serverAnalysisLine(analysis),
                style: text.bodySmall,
              ),
            ],
            const SizedBox(height: 8),
            _buildFrameStrip(analysis, text),
            const SizedBox(height: 8),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: <Widget>[
                for (final BriefGoal goal in _suggestionGoals(analysis))
                  ActionChip(
                    key: ValueKey<String>('suggestion_${goal.value}'),
                    label: Text(goal.label),
                    onPressed: () => _openBrief(goal: goal),
                  ),
              ],
            ),
            const SizedBox(height: 8),
            OutlinedButton.icon(
              onPressed: _analyzing ? null : _startAnalysis,
              icon: const Icon(Icons.refresh_outlined),
              label: const Text('Re-check footage'),
            ),
          ],
        ],
      ),
    );
  }

  /// Timestamped frame thumbnails from the analysis (spot checks).
  Widget _buildFrameStrip(Map<String, dynamic> analysis, TextTheme text) {
    final List<dynamic> frames =
        (analysis['frames'] as List?) ?? const <dynamic>[];
    if (frames.isEmpty) {
      return const SizedBox.shrink();
    }
    return SizedBox(
      height: 96,
      child: ListView.separated(
        scrollDirection: Axis.horizontal,
        itemCount: frames.length,
        separatorBuilder: (_, _) => const SizedBox(width: 8),
        itemBuilder: (BuildContext context, int index) {
          final Map<String, dynamic> frame = Map<String, dynamic>.from(
            frames[index] as Map,
          );
          final String url = '${BackendClient.baseUrl}${frame['url'] ?? ''}';
          final Object? at = frame['t'];
          return Column(
            mainAxisSize: MainAxisSize.min,
            children: <Widget>[
              ClipRRect(
                borderRadius: BorderRadius.circular(8),
                child: Image.network(
                  url,
                  width: 96,
                  height: 64,
                  fit: BoxFit.cover,
                  errorBuilder: (_, _, _) => Container(
                    width: 96,
                    height: 64,
                    color: AppColors.surfaceHigh,
                    child: const Icon(
                      Icons.image_not_supported_outlined,
                      size: 20,
                    ),
                  ),
                ),
              ),
              Text('${at}s', style: text.bodySmall),
            ],
          );
        },
      ),
    );
  }

  Widget _buildReferenceCard(TextTheme text) {
    final Map<String, dynamic>? style = _referenceStyle;
    return SectionCard(
      key: const ValueKey<String>('referenceCard'),
      title: 'Reference style (optional)',
      icon: Icons.style_outlined,
      helper:
          'A reference lends its style only. Your footage stays the output; '
          'dialogue, music, logos, and clips are never imported.',
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: <Widget>[
          if (_referencePath == null)
            OutlinedButton.icon(
              key: const ValueKey<String>('pickReferenceButton'),
              onPressed: _referenceBusy ? null : _pickReference,
              icon: const Icon(Icons.video_library_outlined),
              label: Text(
                _referenceBusy ? 'Reading…' : 'Choose reference video',
              ),
            )
          else ...<Widget>[
            Row(
              children: <Widget>[
                Expanded(
                  child: Text(
                    VideoSelection(_referencePath!).fileName,
                    style: text.bodySmall,
                  ),
                ),
                IconButton(
                  tooltip: 'Remove reference',
                  onPressed: () => setState(() {
                    _referencePath = null;
                    _referenceStyle = null;
                    _referenceError = null;
                  }),
                  icon: const Icon(Icons.close_outlined),
                ),
              ],
            ),
          ],
          if (_referenceBusy && _referenceStyle == null) ...<Widget>[
            const SizedBox(height: 8),
            const LinearProgressIndicator(),
          ],
          if (_referenceError != null) ...<Widget>[
            const SizedBox(height: 8),
            _InlineError(message: _referenceError!),
          ],
          if (style != null) ...<Widget>[
            const SizedBox(height: 8),
            Text('Reference style', style: text.titleSmall),
            const SizedBox(height: 4),
            Text(
              'Aspect ${style['aspect'] ?? '?'} • '
              '${style['shots'] ?? '?'} shots '
              '(average ${style['avg_shot_len'] ?? '?'}s) • '
              '${style['fades_to_black'] ?? 0} fades to black • '
              'Audio ${(style['has_audio'] == true) ? 'present' : 'absent'}.',
              style: text.bodySmall,
            ),
            for (final Object? item
                in (style['transferable'] as List?) ?? const [])
              Padding(
                padding: const EdgeInsets.only(top: 2),
                child: Text('Matched: $item', style: text.bodySmall),
              ),
            if (((style['transferable'] as List?) ?? const []).isEmpty)
              Padding(
                padding: const EdgeInsets.only(top: 2),
                child: Text(
                  'Little transferable style evidence in this reference.',
                  style: text.bodySmall,
                ),
              ),
            for (final Object? item
                in (style['unsupported'] as List?) ?? const [])
              Padding(
                padding: const EdgeInsets.only(top: 2),
                child: Text('Not matched: $item', style: text.bodySmall),
              ),
            const SizedBox(height: 8),
            Row(
              children: <Widget>[
                Switch(
                  value: _adoptReferenceAspect,
                  onChanged: (bool value) =>
                      setState(() => _adoptReferenceAspect = value),
                ),
                Expanded(
                  child: Text(
                    'Use the reference aspect '
                    '(${style['aspect'] ?? '?'})',
                    style: text.bodySmall,
                  ),
                ),
              ],
            ),
            const SizedBox(height: 8),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: <Widget>[
                FilledButton.icon(
                  key: const ValueKey<String>('applyReferenceButton'),
                  onPressed: _applyReference,
                  icon: const Icon(Icons.check_outlined),
                  label: const Text('Apply to my video'),
                ),
                OutlinedButton(
                  onPressed: () => _openBrief(goal: BriefGoal.reference),
                  child: const Text('Describe with this style'),
                ),
              ],
            ),
          ],
        ],
      ),
    );
  }

  /// User-chosen kept/removed intervals in source-video seconds.
  /// These replace the planner's cut; keeps join in row order.
  Widget _buildCutsCard(TextTheme text) {
    final List<List<double>?> spans = _cutOutputSpans();
    final double? estimate = _cutsOutputLen();
    final double? duration = _sourceDurationSec;
    final String? conflict = _cutsBlocker();
    return SectionCard(
      key: const ValueKey<String>('cutsCard'),
      title: 'Keep & remove',
      icon: Icons.content_cut_outlined,
      helper:
          'Your intervals replace the planner\'s cut. Times are '
          'source-video seconds; keeps join in row order.',
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: <Widget>[
          Text(
            duration == null
                ? 'Source length: measuring on first use…'
                : 'Source length: ${duration.toStringAsFixed(1)}s',
            style: text.bodySmall,
          ),
          if (_cuts.isEmpty) ...<Widget>[
            const SizedBox(height: 4),
            Text(
              'No intervals: the instruction sets the cut.',
              style: text.bodySmall,
            ),
          ],
          if (estimate != null) ...<Widget>[
            const SizedBox(height: 4),
            Text(
              'Estimated edited length: '
              '${estimate.toStringAsFixed(1)}s (before transitions)',
              style: text.bodySmall,
            ),
          ],
          for (int i = 0; i < _cuts.length; i++) ...<Widget>[
            const SizedBox(height: 8),
            Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Chip(label: Text(_cuts[i].keep ? 'KEEP' : 'REMOVE')),
                const SizedBox(width: 8),
                Expanded(
                  child: Padding(
                    padding: const EdgeInsets.only(top: 12),
                    child: Text(
                      '${_cuts[i].start.toStringAsFixed(1)} → '
                      '${_cuts[i].end.toStringAsFixed(1)}s of source'
                      '${spans[i] == null ? '' : ' · plays ${spans[i]![0].toStringAsFixed(1)}–${spans[i]![1].toStringAsFixed(1)}s of the edit'}',
                      style: text.bodySmall,
                    ),
                  ),
                ),
                IconButton(
                  key: ValueKey<String>('removeCutButton_$i'),
                  tooltip: 'Remove interval',
                  onPressed: () => _removeCut(i),
                  icon: const Icon(Icons.close_outlined),
                ),
              ],
            ),
          ],
          const SizedBox(height: 12),
          SegmentedButton<bool>(
            key: const ValueKey<String>('cutModeToggle'),
            segments: const <ButtonSegment<bool>>[
              ButtonSegment<bool>(value: true, label: Text('Keep')),
              ButtonSegment<bool>(value: false, label: Text('Remove')),
            ],
            selected: <bool>{_cutKeepMode},
            onSelectionChanged: (Set<bool> selected) =>
                setState(() => _cutKeepMode = selected.first),
          ),
          const SizedBox(height: 8),
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              Expanded(
                child: TextField(
                  key: const ValueKey<String>('cutStartField'),
                  controller: _cutStartController,
                  keyboardType: const TextInputType.numberWithOptions(
                    decimal: true,
                  ),
                  decoration: const InputDecoration(labelText: 'Start (s)'),
                ),
              ),
              const SizedBox(width: 8),
              Expanded(
                child: TextField(
                  key: const ValueKey<String>('cutEndField'),
                  controller: _cutEndController,
                  keyboardType: const TextInputType.numberWithOptions(
                    decimal: true,
                  ),
                  decoration: const InputDecoration(labelText: 'End (s)'),
                ),
              ),
              const SizedBox(width: 8),
              Padding(
                padding: const EdgeInsets.only(top: 8),
                child: OutlinedButton.icon(
                  key: const ValueKey<String>('addCutButton'),
                  onPressed: _addCut,
                  icon: const Icon(Icons.add_outlined),
                  label: const Text('Add'),
                ),
              ),
            ],
          ),
          if (_cutsError != null) ...<Widget>[
            const SizedBox(height: 8),
            _InlineError(message: _cutsError!),
          ],
          if (conflict != null) ...<Widget>[
            const SizedBox(height: 8),
            _InlineError(message: conflict),
          ],
        ],
      ),
    );
  }

  Widget _buildCaptionsCard(TextTheme text) {
    return SectionCard(
      key: const ValueKey<String>('captionsCard'),
      title: 'Captions',
      icon: Icons.closed_caption_outlined,
      helper: 'Speech captions for the edited video, timed after cuts.',
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: <Widget>[
          Text('Captions from speech', style: text.titleSmall),
          const SizedBox(height: 4),
          Text(
            'Transcribes what people say in the video so it can burn in '
            'as captions. This never becomes an editing instruction.',
            style: text.bodySmall,
          ),
          const SizedBox(height: 8),
          OutlinedButton.icon(
            key: const ValueKey<String>('transcribeButton'),
            onPressed: _transcribing ? null : _transcribeSource,
            icon: const Icon(Icons.transcribe_outlined),
            label: Text(_transcribing ? 'Transcribing…' : 'Transcribe speech'),
          ),
          if (_transcribeDuration != null) ...<Widget>[
            const SizedBox(height: 4),
            Text(
              'Transcribed in ${_formatDuration(_transcribeDuration!)}'
              '${_serverTranscribeLine().isEmpty ? '.' : ' (${_serverTranscribeLine()}).'}',
              key: const ValueKey<String>('transcribeTimingsLine'),
              style: text.bodySmall,
            ),
          ],
          if (_captionError != null) ...<Widget>[
            const SizedBox(height: 8),
            _InlineError(message: _captionError!),
          ],
          if (_captionDiagnosis?['suggest_titles'] == true) ...<Widget>[
            const SizedBox(height: 8),
            Text(
              'Silent footage gets no fabricated captions. Write a '
              'title instead — it becomes a real text instruction.',
              style: text.bodySmall,
            ),
            const SizedBox(height: 8),
            Row(
              children: <Widget>[
                Expanded(
                  child: TextField(
                    key: const ValueKey<String>('titleInsteadField'),
                    controller: _titleController,
                    decoration: const InputDecoration(labelText: 'Title text'),
                  ),
                ),
                const SizedBox(width: 8),
                OutlinedButton(
                  key: const ValueKey<String>('addTitleInsteadButton'),
                  onPressed: _addTitleInstead,
                  child: const Text('Add title'),
                ),
              ],
            ),
          ],
          if (_captionsApproximate && _captionLines.isNotEmpty) ...<Widget>[
            const SizedBox(height: 8),
            Text(
              'Timing is approximate: the transcription carries no word '
              'timestamps. Correct or remove lines before rendering.',
              style: text.bodySmall,
            ),
          ],
          if (_captionLines.isNotEmpty) ...<Widget>[
            const SizedBox(height: 8),
            Text(
              'Line times are source-video seconds; they remap onto the '
              'edited video after cuts.',
              style: text.bodySmall,
            ),
          ],
          for (
            int index = 0;
            index < _captionLines.length;
            index++
          ) ...<Widget>[
            const SizedBox(height: 8),
            Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                SizedBox(
                  width: 92,
                  child: Padding(
                    padding: const EdgeInsets.only(top: 16),
                    child: Text(
                      '${formatClipDuration(Duration(milliseconds: (_captionLines[index].start * 1000).round()))} → '
                      '${formatClipDuration(Duration(milliseconds: (_captionLines[index].end * 1000).round()))}',
                      style: text.bodySmall,
                    ),
                  ),
                ),
                Expanded(
                  child: TextField(
                    controller: _captionLines[index].controller,
                    decoration: const InputDecoration(
                      labelText: 'Caption text',
                    ),
                  ),
                ),
                IconButton(
                  tooltip: 'Remove line',
                  onPressed: () => _removeCaptionLine(index),
                  icon: const Icon(Icons.close_outlined),
                ),
              ],
            ),
          ],
          const SizedBox(height: 12),
          Text('Burn captions into the render', style: text.titleSmall),
          const SizedBox(height: 8),
          SegmentedButton<CaptionChoice>(
            key: const ValueKey<String>('captionChoiceSegmented'),
            segments: <ButtonSegment<CaptionChoice>>[
              for (final CaptionChoice choice in CaptionChoice.values)
                ButtonSegment<CaptionChoice>(
                  value: choice,
                  label: Text(choice.label),
                  tooltip: choice.hint,
                ),
            ],
            selected: <CaptionChoice>{_captionChoice},
            onSelectionChanged: (Set<CaptionChoice> selected) {
              setState(() => _captionChoice = selected.first);
              _publish();
            },
          ),
          const SizedBox(height: 4),
          Text(_captionChoice.hint, style: text.bodySmall),
          if (_captionChoice == CaptionChoice.on &&
              !_captionsUsable) ...<Widget>[
            const SizedBox(height: 4),
            Text(
              _captionLines.isEmpty
                  ? 'No lines yet: the render will transcribe the kept '
                        'audio automatically.'
                  : 'Lines need valid times and text first.',
              style: text.bodySmall,
            ),
          ],
        ],
      ),
    );
  }

  Widget _buildMusicSfxCard(TextTheme text) {
    final Map<String, dynamic>? capabilities = _capabilities;
    final List<String> sfxAssets = capabilities == null
        ? const <String>[]
        : ((capabilities['sfx_assets'] as List?) ?? const [])
              .map((Object? item) => item.toString())
              .toList();
    return SectionCard(
      key: const ValueKey<String>('musicSfxCard'),
      title: 'Music & sound effects',
      icon: Icons.music_note_outlined,
      helper: 'Optional audio attached to the next render.',
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: <Widget>[
          Text('Background music', style: text.titleSmall),
          const SizedBox(height: 4),
          Text(
            'Pick an audio file on this device. It plays quietly under '
            'your voice from 0:00 of the edited video, and works on '
            'silent footage too.',
            style: text.bodySmall,
          ),
          const SizedBox(height: 8),
          if (_musicPath == null)
            OutlinedButton.icon(
              key: const ValueKey<String>('chooseMusicButton'),
              onPressed: _pickMusic,
              icon: const Icon(Icons.audio_file_outlined),
              label: const Text('Choose music file'),
            )
          else
            Row(
              children: <Widget>[
                Expanded(
                  child: Text(
                    VideoSelection(_musicPath!).fileName,
                    style: text.bodySmall,
                  ),
                ),
                IconButton(
                  tooltip: 'Remove music',
                  onPressed: () {
                    setState(() => _musicPath = null);
                    _publish();
                  },
                  icon: const Icon(Icons.close_outlined),
                ),
              ],
            ),
          if (_musicError != null) ...<Widget>[
            const SizedBox(height: 8),
            _InlineError(message: _musicError!),
          ],
          if (_musicPath != null) ...<Widget>[
            Row(
              children: <Widget>[
                Expanded(
                  child: Slider(
                    value: _musicVolumeDb,
                    min: -30,
                    max: 0,
                    divisions: 30,
                    label: '${_musicVolumeDb.round()} dB',
                    onChanged: (double value) =>
                        setState(() => _musicVolumeDb = value),
                    onChangeEnd: (_) => _publish(),
                  ),
                ),
                SizedBox(
                  width: 64,
                  child: Text(
                    '${_musicVolumeDb.round()} dB',
                    style: text.bodySmall,
                    textAlign: TextAlign.end,
                  ),
                ),
              ],
            ),
            Row(
              children: <Widget>[
                Switch(
                  value: _musicDuck,
                  onChanged: (bool value) {
                    setState(() => _musicDuck = value);
                    _publish();
                  },
                ),
                Expanded(
                  child: Text(
                    'Lower the music while you speak (needs voice)',
                    style: text.bodySmall,
                  ),
                ),
              ],
            ),
          ],
          const Divider(height: 28),
          Text('Sound effects', style: text.titleSmall),
          const SizedBox(height: 4),
          if (capabilities == null) ...<Widget>[
            Text(
              _capabilitiesError == null
                  ? 'Connect to the backend to load the bundled effects.'
                  : 'Could not load the effects list: $_capabilitiesError',
              style: text.bodySmall,
            ),
            const SizedBox(height: 8),
            OutlinedButton.icon(
              key: const ValueKey<String>('retrySfxButton'),
              onPressed: _loadCapabilities,
              icon: const Icon(Icons.refresh_outlined),
              label: const Text('Retry loading effects'),
            ),
          ] else if (sfxAssets.isEmpty)
            Text('No bundled effects available.', style: text.bodySmall)
          else ...<Widget>[
            _buildSfxPicker(sfxAssets),
            if (_sfxError != null) ...<Widget>[
              const SizedBox(height: 8),
              _InlineError(message: _sfxError!),
            ],
            for (int index = 0; index < _sfxPicks.length; index++)
              Row(
                children: <Widget>[
                  Expanded(
                    child: Text(
                      '${_sfxPicks[index].asset} at '
                      '${_sfxPicks[index].at.toStringAsFixed(1)}s',
                      style: text.bodySmall,
                    ),
                  ),
                  IconButton(
                    tooltip: 'Remove effect',
                    onPressed: () {
                      setState(
                        () =>
                            _sfxPicks = List<_SfxPick>.from(_sfxPicks)
                              ..removeAt(index),
                      );
                      _publish();
                    },
                    icon: const Icon(Icons.close_outlined),
                  ),
                ],
              ),
          ],
        ],
      ),
    );
  }

  void _removeCaptionLine(int index) {
    if (index < 0 || index >= _captionLines.length) {
      return;
    }
    final _CaptionLine removed = _captionLines[index];
    setState(() {
      _captionLines = List<_CaptionLine>.from(_captionLines)..removeAt(index);
    });
    removed.controller.dispose();
    _publish();
  }

  /// Effect picker that stacks on narrow screens instead of overflowing.
  Widget _buildSfxPicker(List<String> sfxAssets) {
    final DropdownButtonFormField<String> effectField =
        DropdownButtonFormField<String>(
          isExpanded: true,
          initialValue: sfxAssets.contains(_sfxAsset)
              ? _sfxAsset
              : sfxAssets.first,
          decoration: const InputDecoration(labelText: 'Effect'),
          items: <DropdownMenuItem<String>>[
            for (final String asset in sfxAssets)
              DropdownMenuItem<String>(
                value: asset,
                child: Text(asset, overflow: TextOverflow.ellipsis),
              ),
          ],
          onChanged: (String? value) {
            if (value != null) {
              setState(() => _sfxAsset = value);
            }
          },
        );
    final double? outLen = _outputSpanEstimate();
    final Widget timeField = TextField(
      key: const ValueKey<String>('sfxAtField'),
      controller: _sfxAtController,
      keyboardType: const TextInputType.numberWithOptions(decimal: true),
      decoration: InputDecoration(
        labelText: 'At (edited sec)',
        helperText: outLen == null
            ? 'Edited-video seconds'
            : 'Valid: 0–${(outLen - 0.1).toStringAsFixed(1)}s of the edited video',
      ),
    );
    final Widget addButton = OutlinedButton.icon(
      key: const ValueKey<String>('addSfxButton'),
      onPressed: _addSfx,
      icon: const Icon(Icons.add_outlined),
      label: const Text('Add'),
    );
    return LayoutBuilder(
      builder: (BuildContext context, BoxConstraints constraints) {
        if (constraints.maxWidth < 400) {
          return Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: <Widget>[
              effectField,
              const SizedBox(height: 8),
              Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: <Widget>[
                  Expanded(child: timeField),
                  const SizedBox(width: 8),
                  Padding(
                    padding: const EdgeInsets.only(top: 8),
                    child: addButton,
                  ),
                ],
              ),
            ],
          );
        }
        return Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Expanded(flex: 3, child: effectField),
            const SizedBox(width: 8),
            Expanded(flex: 2, child: timeField),
            const SizedBox(width: 8),
            Flexible(child: addButton),
          ],
        );
      },
    );
  }

  /// Video-cover composer: a real frame plus an editable title.
  Widget _buildCoverTab(TextTheme text) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: <Widget>[
        Text(
          'Picks a frame from your footage and burns a title on it. '
          'Tamil and English titles are supported.',
          style: text.bodySmall,
        ),
        const SizedBox(height: 8),
        Row(
          children: <Widget>[
            Expanded(
              child: TextField(
                key: const ValueKey<String>('coverTimeField'),
                controller: _coverAtController,
                keyboardType: const TextInputType.numberWithOptions(
                  decimal: true,
                ),
                decoration: const InputDecoration(labelText: 'Frame at (sec)'),
              ),
            ),
            const SizedBox(width: 8),
            Expanded(
              child: DropdownButtonFormField<String>(
                key: const ValueKey<String>('coverPositionField'),
                // Required: without isExpanded the dropdown's internal
                // Row sizes to content and overflows this narrow half.
                isExpanded: true,
                initialValue: _coverPosition,
                decoration: const InputDecoration(labelText: 'Title at'),
                items: const <DropdownMenuItem<String>>[
                  DropdownMenuItem<String>(
                    value: 'top',
                    child: Text('Top', overflow: TextOverflow.ellipsis),
                  ),
                  DropdownMenuItem<String>(
                    value: 'bottom',
                    child: Text('Bottom', overflow: TextOverflow.ellipsis),
                  ),
                ],
                onChanged: (String? value) {
                  if (value != null) {
                    setState(() => _coverPosition = value);
                  }
                },
              ),
            ),
          ],
        ),
        const SizedBox(height: 8),
        TextField(
          key: const ValueKey<String>('coverTitleField'),
          controller: _coverTitleController,
          textCapitalization: TextCapitalization.sentences,
          decoration: const InputDecoration(labelText: 'Cover title'),
        ),
        const SizedBox(height: 12),
        OutlinedButton.icon(
          key: const ValueKey<String>('makeCoverButton'),
          onPressed: _coverBusy ? null : _makeCover,
          icon: const Icon(Icons.image_outlined),
          label: Text(_coverBusy ? 'Making…' : 'Make cover'),
        ),
        if (_coverError != null) ...<Widget>[
          const SizedBox(height: 10),
          _InlineError(message: _coverError!),
        ],
        if (_coverPath != null) ...<Widget>[
          const SizedBox(height: 12),
          ClipRRect(
            borderRadius: BorderRadius.circular(12),
            child: Image.file(
              File(_coverPath!),
              errorBuilder: (_, _, _) => const SizedBox.shrink(),
            ),
          ),
          const SizedBox(height: 12),
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: <Widget>[
              OutlinedButton.icon(
                key: const ValueKey<String>('saveCoverButton'),
                onPressed: _coverBusy || _thumbSaving ? null : _saveCover,
                icon: const Icon(Icons.save_alt_outlined),
                label: Text(_thumbSaving ? 'Saving…' : 'Save cover'),
              ),
              OutlinedButton.icon(
                key: const ValueKey<String>('shareCoverButton'),
                onPressed: _coverBusy || _thumbSharing ? null : _shareCover,
                icon: const Icon(Icons.ios_share_outlined),
                label: Text(_thumbSharing ? 'Sharing…' : 'Share cover'),
              ),
            ],
          ),
        ],
      ],
    );
  }

  /// The picked photo with its real state: Selected (on this device
  /// only), Uploading, Uploaded (server-confirmed, with dimensions),
  /// or failed with retry. Attached appears only in the project list
  /// below once the photo is added with valid settings.
  Widget _buildPendingPhotoCard(TextTheme text) {
    final String path = _uploadedImagePath!;
    final String state = _uploadingImage
        ? 'Uploading…'
        : (_uploadedFileId != null ? 'Uploaded' : 'Selected');
    return Container(
      key: const ValueKey<String>('pendingPhotoCard'),
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        border: Border.all(color: Theme.of(context).dividerColor),
        borderRadius: BorderRadius.circular(12),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: <Widget>[
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              ClipRRect(
                borderRadius: BorderRadius.circular(8),
                child: Image.file(
                  File(path),
                  width: 64,
                  height: 64,
                  fit: BoxFit.cover,
                  errorBuilder: (_, _, _) => const SizedBox(
                    width: 64,
                    height: 64,
                    child: Icon(Icons.broken_image_outlined),
                  ),
                ),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: <Widget>[
                    Text(
                      _uploadedImageName ?? 'Selected photo',
                      style: text.titleSmall,
                      overflow: TextOverflow.ellipsis,
                    ),
                    const SizedBox(height: 2),
                    Text(
                      _uploadedDims == null
                          ? 'State: $state'
                          : 'State: $state · $_uploadedDims',
                      style: text.bodySmall,
                    ),
                  ],
                ),
              ),
            ],
          ),
          if (_pendingImageError != null) ...<Widget>[
            const SizedBox(height: 8),
            _InlineError(message: _pendingImageError!),
          ],
          const SizedBox(height: 8),
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: <Widget>[
              if (_uploadingImage)
                OutlinedButton.icon(
                  key: const ValueKey<String>('cancelPhotoButton'),
                  onPressed: _clearPendingImage,
                  icon: const Icon(Icons.close_outlined),
                  label: const Text('Cancel'),
                )
              else ...<Widget>[
                if (_pendingImageError != null)
                  OutlinedButton.icon(
                    key: const ValueKey<String>('retryPhotoButton'),
                    onPressed: () =>
                        _uploadPendingImage(_pickGen),
                    icon: const Icon(Icons.refresh_outlined),
                    label: const Text('Retry upload'),
                  ),
                OutlinedButton.icon(
                  key: const ValueKey<String>('removePhotoButton'),
                  onPressed: _clearPendingImage,
                  icon: const Icon(Icons.delete_outline),
                  label: const Text('Remove'),
                ),
              ],
            ],
          ),
        ],
      ),
    );
  }

  /// What each attachment use does, shown before Attach is tapped so
  /// Cover (a separate file) is never confused with an in-video use.
  String _attachKindHint() {
    return switch (_attachKind) {
      'cover' =>
        'Cover stays a separate image: save or share it beside the '
            'video. It never appears inside the video.',
      'intro' =>
        'Intro shows this photo full-screen before your footage, for '
            'the duration you set.',
      'outro' =>
        'Outro shows this photo full-screen after your footage, for '
            'the duration you set.',
      'banner' =>
        'Banner floats this photo over the playing video, between '
            'the edited-video times you set.',
      _ => '',
    };
  }

  /// Kept ranges of the latest plan in source seconds, mirroring
  /// the backend's cutting modes (trim, keep, remove, tracked
  /// segments, or the full source when the plan cuts nothing).
  List<_CutRange> _planKeptRanges() {
    final List<Object?> ops =
        (_rawPlan?['operations'] as List?)?.cast<Object?>() ?? <Object?>[];
    final double duration = _sourceDurationSec ?? 0;
    List<_CutRange> rangesOf(String op) {
      final List<_CutRange> out = <_CutRange>[];
      for (final Object? raw in ops) {
        if (raw is Map<String, dynamic> && raw['op'] == op) {
          final double? start = (raw['start'] as num?)?.toDouble();
          final double? end = (raw['end'] as num?)?.toDouble();
          if (start != null && end != null && end > start) {
            out.add(_CutRange(true, start, end));
          }
        }
      }
      out.sort(
        (_CutRange a, _CutRange b) => a.start.compareTo(b.start),
      );
      return out;
    }

    final List<_CutRange> tracked = rangesOf('frame_subject');
    if (tracked.isNotEmpty) {
      return tracked;
    }
    final List<_CutRange> trims = rangesOf('trim');
    if (trims.isNotEmpty) {
      return <_CutRange>[trims.first];
    }
    final List<_CutRange> keeps = rangesOf('keep');
    if (keeps.isNotEmpty) {
      return keeps;
    }
    final List<_CutRange> removes = rangesOf('remove');
    if (removes.isNotEmpty && duration > 0) {
      final List<_CutRange> out = <_CutRange>[];
      double cursor = 0;
      for (final _CutRange cut in removes) {
        if (cut.start > cursor) {
          out.add(_CutRange(true, cursor, cut.start));
        }
        cursor = cursor > cut.end ? cursor : cut.end;
      }
      if (cursor < duration) {
        out.add(_CutRange(true, cursor, duration));
      }
      return out;
    }
    if (duration > 0) {
      return <_CutRange>[_CutRange(true, 0, duration)];
    }
    return <_CutRange>[];
  }

  bool get _planIsTracked {
    final List<Object?> ops =
        (_rawPlan?['operations'] as List?)?.cast<Object?>() ?? <Object?>[];
    return ops.any(
      (Object? raw) => raw is Map<String, dynamic> && raw['op'] == 'frame_subject',
    );
  }

  /// Clip name owning a joined-timeline second, for the review rows.
  String _clipNameForSecond(double second) {
    for (final _ClipSlot clip in _clips) {
      final double start = clip.offset;
      final double dur = clip.duration ?? 0;
      final double end = dur > 0 ? start + dur : start;
      if (second >= start && (dur <= 0 || second < end)) {
        return clip.displayName;
      }
    }
    return _clips.isNotEmpty ? _clips.last.displayName : 'Source';
  }

  /// Edited-video start second of a kept span, or null when the span
  /// is not kept.
  double? _editedStartFor(double start, List<_CutRange> keeps) {
    double offset = 0;
    for (final _CutRange keep in keeps) {
      if (start >= keep.start && start < keep.end) {
        return offset + (start - keep.start);
      }
      offset += keep.end - keep.start;
    }
    return null;
  }

  /// Gaps between kept ranges over [0, duration): the removed spans.
  List<_CutRange> _removedSpans(List<_CutRange> keeps, double duration) {
    final List<_CutRange> ordered = List<_CutRange>.of(keeps)
      ..sort((_CutRange a, _CutRange b) => a.start.compareTo(b.start));
    final List<_CutRange> gaps = <_CutRange>[];
    double cursor = 0;
    for (final _CutRange keep in ordered) {
      if (keep.start > cursor) {
        gaps.add(_CutRange(false, cursor, keep.start));
      }
      cursor = cursor > keep.end ? cursor : keep.end;
    }
    if (cursor < duration) {
      gaps.add(_CutRange(false, cursor, duration));
    }
    return gaps;
  }

  List<_CutRange> _mergedKeeps(List<_CutRange> keeps) {
    final List<_CutRange> ordered = List<_CutRange>.of(keeps)
      ..sort((_CutRange a, _CutRange b) => a.start.compareTo(b.start));
    final List<_CutRange> merged = <_CutRange>[];
    for (final _CutRange keep in ordered) {
      if (keep.end <= keep.start) {
        continue;
      }
      if (merged.isEmpty || keep.start > merged.last.end) {
        merged.add(keep);
      } else {
        final _CutRange last = merged.removeLast();
        merged.add(
          _CutRange(
            true,
            last.start,
            keep.end > last.end ? keep.end : last.end,
          ),
        );
      }
    }
    return merged;
  }

  void _openReview() {
    setState(() {
      _reviewKeeps = _planKeptRanges();
      _dismissedSuggestions = <String>{};
    });
  }

  void _restoreReviewSpan(_CutRange gap) {
    final List<_CutRange>? keeps = _reviewKeeps;
    if (keeps == null) {
      return;
    }
    setState(() {
      _reviewKeeps = _mergedKeeps(
        <_CutRange>[...keeps, _CutRange(true, gap.start, gap.end)],
      );
    });
  }

  void _cutReviewSpan(double start, double end, String key) {
    final List<_CutRange>? keeps = _reviewKeeps;
    if (keeps == null) {
      return;
    }
    final List<_CutRange> out = <_CutRange>[];
    for (final _CutRange keep in keeps) {
      if (end <= keep.start || start >= keep.end) {
        out.add(keep);
        continue;
      }
      if (start > keep.start) {
        out.add(_CutRange(true, keep.start, start));
      }
      if (end < keep.end) {
        out.add(_CutRange(true, end, keep.end));
      }
    }
    setState(() {
      _reviewKeeps = _mergedKeeps(out);
      _dismissedSuggestions = <String>{..._dismissedSuggestions, key};
    });
  }

  /// Applies the reviewed keeps as manual Keep ranges and re-plans,
  /// so the reviewed decisions — not the raw model cuts — render.
  void _applyReview() {
    final List<_CutRange>? keeps = _reviewKeeps;
    if (keeps == null || keeps.isEmpty) {
      return;
    }
    setState(() {
      _cuts = <_CutRange>[
        for (final _CutRange keep in keeps)
          _CutRange(true, keep.start, keep.end),
      ];
      _cutKeepMode = true;
    });
    _publish();
    _generatePlan();
  }

  String _formatSpan(double start, double end) =>
      '${start.toStringAsFixed(1)}–${end.toStringAsFixed(1)}s';

  /// Cut review: every kept and removed span with its clip, reason,
  /// and edited time, plus restorable removals and suggestions.
  Widget _buildReviewSection(TextTheme text) {
    final List<_CutRange>? keeps = _reviewKeeps;
    if (keeps == null) {
      return OutlinedButton.icon(
        key: const ValueKey<String>('reviewCutsButton'),
        onPressed: _plan == null ? null : _openReview,
        icon: const Icon(Icons.playlist_play_outlined),
        label: const Text('Review cuts'),
      );
    }
    final double duration = _sourceDurationSec ?? 0;
    final bool tracked = _planIsTracked;
    final List<_CutRange> gaps = duration > 0
        ? _removedSpans(keeps, duration)
        : <_CutRange>[];
    final List<Map<String, dynamic>> suggestions = <Map<String, dynamic>>[
      for (final Map<String, dynamic> item in _suggestions)
        if (!_dismissedSuggestions.contains(
          '${item['start']}-${item['end']}',
        ))
          item,
    ];
    String reasonFor(bool kept) =>
        kept ? 'Kept for the story' : 'Removed by the brief';
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: <Widget>[
        Text('Cut review', style: text.titleSmall),
        const SizedBox(height: 4),
        Text(
          tracked
              ? 'Tracking plans follow the subject and cannot be '
                    'adjusted here. Change the brief to re-plan.'
              : 'Restore a removed span, cut a suggestion, then apply. '
                    'Applying replaces your manual Keep & remove ranges '
                    'and re-plans.',
          style: text.bodySmall,
        ),
        const SizedBox(height: 8),
        for (int i = 0; i < keeps.length; i++)
          _buildReviewRow(
            text,
            key: 'reviewKeep_$i',
            decision: 'Keep',
            span: keeps[i],
            reason: 'Kept for the story',
            edited: _editedSpanLabel(keeps[i], keeps),
            actionLabel: null,
            onAction: null,
          ),
        for (int i = 0; i < gaps.length; i++)
          _buildReviewRow(
            text,
            key: 'reviewGap_$i',
            decision: 'Remove',
            span: gaps[i],
            reason: reasonFor(false),
            edited: 'not in the edit',
            actionLabel: tracked ? null : 'Restore',
            onAction: tracked ? null : () => _restoreReviewSpan(gaps[i]),
          ),
        for (int i = 0; i < suggestions.length; i++)
          _buildSuggestionRow(text, i, suggestions[i], tracked),
        const SizedBox(height: 8),
        Wrap(
          spacing: 8,
          runSpacing: 8,
          children: <Widget>[
            if (!tracked)
              OutlinedButton.icon(
                key: const ValueKey<String>('applyReviewButton'),
                onPressed: (_planning || keeps.isEmpty) ? null : _applyReview,
                icon: const Icon(Icons.check_outlined),
                label: const Text('Apply & re-plan'),
              ),
            OutlinedButton.icon(
              key: const ValueKey<String>('resetReviewButton'),
              onPressed: _openReview,
              icon: const Icon(Icons.refresh_outlined),
              label: const Text('Reset'),
            ),
            OutlinedButton.icon(
              key: const ValueKey<String>('closeReviewButton'),
              onPressed: () => setState(() => _reviewKeeps = null),
              icon: const Icon(Icons.close_outlined),
              label: const Text('Close'),
            ),
          ],
        ),
      ],
    );
  }

  String _editedSpanLabel(_CutRange span, List<_CutRange> keeps) {
    final double? start = _editedStartFor(span.start, keeps);
    if (start == null) {
      return 'not in the edit';
    }
    final double end = start + (span.end - span.start);
    return 'edited ${_formatSpan(start, end)}';
  }

  Widget _buildReviewRow(
    TextTheme text, {
    required String key,
    required String decision,
    required _CutRange span,
    required String reason,
    required String edited,
    required String? actionLabel,
    required void Function()? onAction,
  }) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: Container(
        key: ValueKey<String>(key),
        padding: const EdgeInsets.all(8),
        decoration: BoxDecoration(
          border: Border.all(color: Theme.of(context).dividerColor),
          borderRadius: BorderRadius.circular(8),
        ),
        child: Row(
          children: <Widget>[
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: <Widget>[
                  Text(
                    '$decision ${_formatSpan(span.start, span.end)} · '
                    '${_clipNameForSecond(span.start)}',
                    style: text.titleSmall,
                  ),
                  Text('$reason · $edited', style: text.bodySmall),
                ],
              ),
            ),
            if (actionLabel != null)
              TextButton(
                onPressed: onAction,
                child: Text(actionLabel),
              ),
          ],
        ),
      ),
    );
  }

  Widget _buildSuggestionRow(
    TextTheme text,
    int index,
    Map<String, dynamic> item,
    bool tracked,
  ) {
    final double start = (item['start'] as num?)?.toDouble() ?? 0;
    final double end = (item['end'] as num?)?.toDouble() ?? 0;
    final String reason = item['reason'] as String? ?? 'Possible cut.';
    final String key = '$start-$end';
    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: Container(
        key: ValueKey<String>('reviewSuggestion_$index'),
        padding: const EdgeInsets.all(8),
        decoration: BoxDecoration(
          border: Border.all(color: Theme.of(context).dividerColor),
          borderRadius: BorderRadius.circular(8),
        ),
        child: Row(
          children: <Widget>[
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: <Widget>[
                  Text(
                    'Suggestion ${_formatSpan(start, end)} · '
                    '${_clipNameForSecond(start)}',
                    style: text.titleSmall,
                  ),
                  Text(reason, style: text.bodySmall),
                ],
              ),
            ),
            if (!tracked) ...<Widget>[
              TextButton(
                onPressed: () => _cutReviewSpan(start, end, key),
                child: const Text('Cut'),
              ),
              TextButton(
                onPressed: () => setState(
                  () => _dismissedSuggestions = <String>{
                    ..._dismissedSuggestions,
                    key,
                  },
                ),
                child: const Text('Keep'),
              ),
            ],
          ],
        ),
      ),
    );
  }

  /// Explicit image attachments: cover (export-only), intro, outro,
  /// banner. Nothing attaches automatically — every generated cover
  /// stays a separate file until the user attaches it here.
  Widget _buildAttachmentsSection(TextTheme text) {
    final Map<String, String> sources = _attachSources();
    final List<String> positions = _bannerPositions;
    final String positionValue = positions.contains(_bannerPosition)
        ? _bannerPosition
        : positions.first;
    final double? span = _outputSpanEstimate();
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: <Widget>[
        Text('Attach an image to the video', style: text.titleSmall),
        const SizedBox(height: 4),
        Text(
          'A cover is saved beside the video. Intro and outro stills '
          'extend the edit; a banner floats over edited-video seconds.',
          style: text.bodySmall,
        ),
        const SizedBox(height: 8),
        OutlinedButton.icon(
          key: const ValueKey<String>('uploadImageButton'),
          onPressed: _pickImage,
          icon: const Icon(Icons.upload_outlined),
          label: const Text('Upload image'),
        ),
        const SizedBox(height: 8),
        if (_uploadedImagePath != null) ...<Widget>[
          _buildPendingPhotoCard(text),
          const SizedBox(height: 8),
        ],
        if (sources.isEmpty)
          Text(
            'No image yet. Make a cover, generate artwork, or upload '
            'an image first.',
            style: text.bodySmall,
          )
        else ...<Widget>[
          DropdownButtonFormField<String>(
            key: const ValueKey<String>('attachSourceField'),
            isExpanded: true,
            initialValue: sources.containsKey(_attachSource)
                ? _attachSource
                : null,
            hint: const Text('Choose image'),
            decoration: const InputDecoration(labelText: 'Image'),
            items: <DropdownMenuItem<String>>[
              for (final MapEntry<String, String> entry in sources.entries)
                DropdownMenuItem<String>(
                  value: entry.key,
                  child: Text(
                    _attachSourceLabel(entry.key),
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
            ],
            onChanged: (String? value) {
              if (value != null) {
                setState(() => _attachSource = value);
              }
            },
          ),
          const SizedBox(height: 8),
          DropdownButtonFormField<String>(
            key: const ValueKey<String>('attachKindField'),
            isExpanded: true,
            initialValue: _attachKind,
            decoration: const InputDecoration(labelText: 'Use as'),
            items: const <DropdownMenuItem<String>>[
              DropdownMenuItem<String>(
                value: 'cover',
                child: Text('Cover image'),
              ),
              DropdownMenuItem<String>(
                value: 'intro',
                child: Text('Intro still'),
              ),
              DropdownMenuItem<String>(
                value: 'outro',
                child: Text('Outro still'),
              ),
              DropdownMenuItem<String>(
                value: 'banner',
                child: Text('Banner overlay'),
              ),
            ],
            onChanged: (String? value) {
              if (value != null) {
                setState(() => _attachKind = value);
              }
            },
          ),
          const SizedBox(height: 4),
          Text(_attachKindHint(), style: text.bodySmall),
          const SizedBox(height: 8),
          if (_attachKind == 'intro' || _attachKind == 'outro')
            TextField(
              key: const ValueKey<String>('attachDurationField'),
              controller: _stillDurController,
              keyboardType: const TextInputType.numberWithOptions(
                decimal: true,
              ),
              decoration: const InputDecoration(
                labelText: 'Still duration (sec)',
                helperText: '0.5–10s; added to the edited length.',
              ),
            ),
          if (_attachKind == 'banner') ...<Widget>[
            Row(
              children: <Widget>[
                Expanded(
                  child: TextField(
                    key: const ValueKey<String>('attachBannerStartField'),
                    controller: _bannerStartController,
                    keyboardType: const TextInputType.numberWithOptions(
                      decimal: true,
                    ),
                    decoration: const InputDecoration(
                      labelText: 'Start (edited sec)',
                    ),
                  ),
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: TextField(
                    key: const ValueKey<String>('attachBannerEndField'),
                    controller: _bannerEndController,
                    keyboardType: const TextInputType.numberWithOptions(
                      decimal: true,
                    ),
                    decoration: const InputDecoration(
                      labelText: 'End (edited sec)',
                    ),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 8),
            Row(
              children: <Widget>[
                Expanded(
                  child: DropdownButtonFormField<String>(
                    key: const ValueKey<String>('attachBannerPositionField'),
                    isExpanded: true,
                    initialValue: positionValue,
                    decoration: const InputDecoration(labelText: 'Position'),
                    items: <DropdownMenuItem<String>>[
                      for (final String position in positions)
                        DropdownMenuItem<String>(
                          value: position,
                          child: Text(
                            position,
                            overflow: TextOverflow.ellipsis,
                          ),
                        ),
                    ],
                    onChanged: (String? value) {
                      if (value != null) {
                        setState(() => _bannerPosition = value);
                      }
                    },
                  ),
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: DropdownButtonFormField<double>(
                    key: const ValueKey<String>('attachBannerSizeField'),
                    isExpanded: true,
                    initialValue: _bannerSize,
                    decoration: const InputDecoration(labelText: 'Size'),
                    items: const <DropdownMenuItem<double>>[
                      DropdownMenuItem<double>(
                        value: 0.25,
                        child: Text('Small'),
                      ),
                      DropdownMenuItem<double>(
                        value: 0.4,
                        child: Text('Medium'),
                      ),
                      DropdownMenuItem<double>(
                        value: 0.6,
                        child: Text('Large'),
                      ),
                    ],
                    onChanged: (double? value) {
                      if (value != null) {
                        setState(() => _bannerSize = value);
                      }
                    },
                  ),
                ),
              ],
            ),
            if (span != null) ...<Widget>[
              const SizedBox(height: 4),
              Text(
                'Edited output is about ${span.toStringAsFixed(1)}s; '
                'the banner must end inside it.',
                style: text.bodySmall,
              ),
            ],
          ],
          const SizedBox(height: 8),
          OutlinedButton.icon(
            key: const ValueKey<String>('addAttachmentButton'),
            onPressed: _addAttachment,
            icon: const Icon(Icons.attach_file_outlined),
            label: const Text('Attach'),
          ),
        ],
        if (_attachmentError != null) ...<Widget>[
          const SizedBox(height: 10),
          _InlineError(message: _attachmentError!),
        ],
        for (final _ImageAttachment attachment in _attachments) ...<Widget>[
          const SizedBox(height: 8),
          Row(
            children: <Widget>[
              const Icon(Icons.image_outlined, size: 20),
              const SizedBox(width: 8),
              Expanded(
                child: Text(attachment.describe(), style: text.bodySmall),
              ),
              TextButton(
                key: ValueKey<String>('removeAttachment_${attachment.kind}'),
                onPressed: () => _removeAttachment(attachment.kind),
                child: const Text('Remove'),
              ),
            ],
          ),
        ],
      ],
    );
  }

  String _supportKindHint() {
    return switch (_supportKind) {
      'cutaway' => 'Cutaway: the supporting video fills the screen while '
          'the main speech continues.',
      'pip' => 'Picture-in-picture: a small window over the main video. '
          'The story behind it keeps playing.',
      'insert' => 'Insert: a new segment inside the story — the edit '
          'grows longer by its duration.',
      _ => '',
    };
  }

  List<String> _supportAudioOptions() {
    return switch (_supportKind) {
      'cutaway' => const <String>['mute', 'mix', 'use'],
      'pip' => const <String>['mute', 'mix'],
      'insert' => const <String>['mute', 'use'],
      _ => const <String>['mute'],
    };
  }

  String _supportStateLabel() {
    if (_uploadingSupport) {
      return 'Uploading…';
    }
    if (_supportFileId != null) {
      return 'Uploaded';
    }
    return 'Selected';
  }

  /// Supporting video (B-roll): one uploaded clip plus placements.
  /// The main story always stays the background; placements show the
  /// supporting clip over it (cutaway/PiP) or inside it (insert).
  Widget _buildSupportSection(TextTheme text) {
    final List<String> audioOptions = _supportAudioOptions();
    final String audioValue =
        audioOptions.contains(_supportAudio) ? _supportAudio : 'mute';
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: <Widget>[
        Text('Supporting video (B-roll)', style: text.titleSmall),
        const SizedBox(height: 4),
        Text(
          'Illustrate what the speaker describes. The story stays '
          'behind it: a cutaway or window floats over edited-video '
          'seconds, an insert grows the edit. Photos go under '
          'intro/outro/banner instead. '
          'துணை வீடியோ கதையின் மேல் தோன்றும்.',
          style: text.bodySmall,
        ),
        const SizedBox(height: 8),
        OutlinedButton.icon(
          key: const ValueKey<String>('supportPickButton'),
          onPressed: _uploadingSupport ? null : _pickSupport,
          icon: const Icon(Icons.video_library_outlined),
          label: const Text('Pick supporting video'),
        ),
        if (_supportError != null) ...<Widget>[
          const SizedBox(height: 8),
          _InlineError(message: _supportError!),
        ],
        if (_supportPath != null) ...<Widget>[
          const SizedBox(height: 8),
          Row(
            children: <Widget>[
              const Icon(Icons.movie_outlined, size: 20),
              const SizedBox(width: 8),
              Expanded(
                child: Text(
                  '${_supportName ?? 'clip'} '
                  '(${_supportStateLabel()})'
                  '${_supportDuration != null ? ', ${_supportDuration!.toStringAsFixed(1)}s' : ''}',
                  style: text.bodySmall,
                ),
              ),
              TextButton(
                key: const ValueKey<String>('supportRemoveButton'),
                onPressed: _removeSupportClip,
                child: const Text('Remove'),
              ),
            ],
          ),
        ],
        if (_supportFileId == null)
          Text(
            'No supporting video yet. Pick one to place it at a time '
            'or a spoken phrase.',
            style: text.bodySmall,
          )
        else ...<Widget>[
          const SizedBox(height: 8),
          DropdownButtonFormField<String>(
            key: const ValueKey<String>('supportKindField'),
            isExpanded: true,
            initialValue: _supportKind,
            decoration: const InputDecoration(labelText: 'Show it as'),
            items: const <DropdownMenuItem<String>>[
              DropdownMenuItem<String>(
                value: 'cutaway',
                child: Text('Cutaway (full-screen)'),
              ),
              DropdownMenuItem<String>(
                value: 'pip',
                child: Text('Picture-in-picture'),
              ),
              DropdownMenuItem<String>(
                value: 'insert',
                child: Text('Insert clip (extends the edit)'),
              ),
            ],
            onChanged: (String? value) {
              if (value == null) {
                return;
              }
              setState(() {
                _supportKind = value;
                if (!_supportAudioOptions().contains(_supportAudio)) {
                  _supportAudio = 'mute';
                }
              });
            },
          ),
          const SizedBox(height: 4),
          Text(_supportKindHint(), style: text.bodySmall),
          const SizedBox(height: 8),
          Row(
            children: <Widget>[
              Expanded(
                child: TextField(
                  key: const ValueKey<String>('supportAtField'),
                  controller: _supportAtController,
                  keyboardType: const TextInputType.numberWithOptions(
                    decimal: true,
                  ),
                  decoration: const InputDecoration(
                    labelText: 'At (edited seconds)',
                  ),
                ),
              ),
              const SizedBox(width: 8),
              Expanded(
                child: TextField(
                  key: const ValueKey<String>('supportDurationField'),
                  controller: _supportDurationController,
                  keyboardType: const TextInputType.numberWithOptions(
                    decimal: true,
                  ),
                  decoration: const InputDecoration(
                    labelText: 'Length (s)',
                  ),
                ),
              ),
              const SizedBox(width: 8),
              Expanded(
                child: TextField(
                  key: const ValueKey<String>('supportSrcField'),
                  controller: _supportSrcController,
                  keyboardType: const TextInputType.numberWithOptions(
                    decimal: true,
                  ),
                  decoration: const InputDecoration(
                    labelText: 'Clip starts at (s)',
                  ),
                ),
              ),
            ],
          ),
          const SizedBox(height: 8),
          DropdownButtonFormField<String>(
            key: const ValueKey<String>('supportAudioField'),
            isExpanded: true,
            initialValue: audioValue,
            decoration: const InputDecoration(labelText: 'Supporting sound'),
            items: <DropdownMenuItem<String>>[
              for (final String option in audioOptions)
                DropdownMenuItem<String>(
                  value: option,
                  child: Text(option),
                ),
            ],
            onChanged: (String? value) {
              if (value != null) {
                setState(() => _supportAudio = value);
              }
            },
          ),
          const SizedBox(height: 4),
          Text(
            'Mute keeps the story sound. Mix adds both. Use replaces '
            'the story sound there.',
            style: text.bodySmall,
          ),
          if (_supportKind == 'pip') ...<Widget>[
            const SizedBox(height: 8),
            DropdownButtonFormField<String>(
              key: const ValueKey<String>('supportPositionField'),
              isExpanded: true,
              initialValue: _supportPosition,
              decoration: const InputDecoration(labelText: 'Corner'),
              items: const <DropdownMenuItem<String>>[
                DropdownMenuItem<String>(
                  value: 'top_left',
                  child: Text('Top left'),
                ),
                DropdownMenuItem<String>(
                  value: 'top_right',
                  child: Text('Top right'),
                ),
                DropdownMenuItem<String>(
                  value: 'bottom_left',
                  child: Text('Bottom left'),
                ),
                DropdownMenuItem<String>(
                  value: 'bottom_right',
                  child: Text('Bottom right'),
                ),
              ],
              onChanged: (String? value) {
                if (value != null) {
                  setState(() => _supportPosition = value);
                }
              },
            ),
            Row(
              children: <Widget>[
                Expanded(
                  child: Slider(
                    key: const ValueKey<String>('supportWidthField'),
                    value: _supportWidthFrac,
                    min: 0.15,
                    max: 0.6,
                    divisions: 9,
                    label: '${(_supportWidthFrac * 100).round()}% wide',
                    onChanged: (double value) {
                      setState(() => _supportWidthFrac = value);
                    },
                  ),
                ),
                Text(
                  '${(_supportWidthFrac * 100).round()}% wide',
                  style: text.bodySmall,
                ),
              ],
            ),
          ],
          const SizedBox(height: 8),
          Row(
            children: <Widget>[
              Expanded(
                child: TextField(
                  key: const ValueKey<String>('supportPhraseField'),
                  controller: _supportPhraseController,
                  decoration: const InputDecoration(
                    labelText: 'Or place at a spoken phrase',
                  ),
                ),
              ),
              const SizedBox(width: 8),
              OutlinedButton.icon(
                key: const ValueKey<String>('supportPhraseFindButton'),
                onPressed: _searchPhrase,
                icon: const Icon(Icons.search_outlined),
                label: const Text('Find'),
              ),
            ],
          ),
          if (_phraseMessage != null) ...<Widget>[
            const SizedBox(height: 4),
            Text(_phraseMessage!, style: text.bodySmall),
          ],
          if (_captionsApproximate && _phraseHits.isNotEmpty) ...<Widget>[
            const SizedBox(height: 4),
            Text(
              'Speech timings are estimated here.',
              style: text.bodySmall,
            ),
          ],
          for (int i = 0; i < _phraseHits.length; i++) ...<Widget>[
            const SizedBox(height: 4),
            _buildPhraseCandidate(text, i),
          ],
          const SizedBox(height: 8),
          OutlinedButton.icon(
            key: const ValueKey<String>('addSupportButton'),
            onPressed: _addSupport,
            icon: const Icon(Icons.add_outlined),
            label: const Text('Place supporting clip'),
          ),
        ],
        if (_supportAddError != null) ...<Widget>[
          const SizedBox(height: 10),
          _InlineError(message: _supportAddError!),
        ],
        for (int i = 0; i < _supportPlacements.length; i++) ...<Widget>[
          const SizedBox(height: 8),
          Row(
            children: <Widget>[
              const Icon(Icons.slideshow_outlined, size: 20),
              const SizedBox(width: 8),
              Expanded(
                child: Text(
                  _supportPlacements[i].describe(),
                  style: text.bodySmall,
                ),
              ),
              TextButton(
                key: ValueKey<String>('removeSupport_$i'),
                onPressed: () => _removeSupportPlacement(i),
                child: const Text('Remove'),
              ),
            ],
          ),
        ],
      ],
    );
  }

  /// One phrase occurrence with its story clip and live edited time.
  Widget _buildPhraseCandidate(TextTheme text, int index) {
    final _PhraseHit hit = _phraseHits[index];
    final bool selected = _phraseChoice == index;
    final double? output = _sourceToOutput(hit.start);
    final String timing = output == null
        ? 'cut from the edit'
        : '${output.toStringAsFixed(1)}s of the edit';
    return Row(
      children: <Widget>[
        Expanded(
          child: Text(
            'Clip ${hit.clipIndex + 1} (${hit.clipName}), '
            '${hit.start.toStringAsFixed(1)}s → $timing: “${hit.text}”',
            style: text.bodySmall,
          ),
        ),
        ChoiceChip(
          key: ValueKey<String>('supportCandidate_$index'),
          label: Text(selected ? 'Picked' : 'Pick'),
          selected: selected,
          onSelected: (bool value) {
            setState(() {
              _phraseChoice = selected ? null : index;
            });
          },
        ),
      ],
    );
  }

  /// Job progress, errors with retry, and raw diagnostics. Lives below
  /// Generate so the action sits under every editable setting.
  Widget _buildStatusCard(TextTheme text) {
    final bool stagesActive = _jobStages.values.any(
      (_StageState state) => state != _StageState.idle,
    );
    final bool hasAbove = stagesActive || _jobError != null;
    return SectionCard(
      key: const ValueKey<String>('statusCard'),
      title: 'Job status',
      icon: Icons.monitor_heart_outlined,
      helper: 'Live stages for the current generation.',
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: <Widget>[
          if (stagesActive) ...<Widget>[
            for (final _JobStage stage in _JobStage.values)
              _StageRow(
                stage: stage,
                state: _jobStages[stage] ?? _StageState.idle,
              ),
          ],
          if (_jobError != null) ...<Widget>[
            if (stagesActive) const SizedBox(height: 10),
            _InlineError(message: _jobError!),
            const SizedBox(height: 10),
            OutlinedButton.icon(
              key: const ValueKey<String>('retryJobButton'),
              onPressed: _jobRunning ? null : _generateEditedVideo,
              icon: const Icon(Icons.refresh_outlined),
              label: const Text('Retry'),
            ),
          ],
          if (hasAbove) const SizedBox(height: 8),
          ExpansionTile(
            tilePadding: EdgeInsets.zero,
            childrenPadding: EdgeInsets.zero,
            title: Text('Diagnostics', style: text.titleSmall),
            subtitle: Text(
              'Backend address, upload id, and the last raw error.',
              style: text.bodySmall,
            ),
            children: <Widget>[
              Align(
                alignment: Alignment.centerLeft,
                child: Text(
                  'Backend: ${BackendClient.baseUrl}\n'
                  'Upload id: ${_fileId ?? 'not uploaded yet'}\n'
                  'Plan: ${_plan == null ? 'none' : 'current'}\n'
                  'Last error: ${_lastErrorDetail ?? 'none'}',
                  style: text.bodySmall,
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }

  /// Current selections shown in "Edits to apply" before any plan
  /// exists, so the summary is complete at every step.
  Widget _buildSelectionsSummary(TextTheme text) {
    final String framing =
        _aspectRatio == null || _aspectRatio == AspectRatioOption.original
        ? 'Original'
        : '${_aspectRatio!.label} (${_fit.label})';
    final String extras = _extrasSummary().isEmpty
        ? 'No extras attached'
        : _extrasSummary();
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        Text('No plan yet.', style: text.titleSmall),
        const SizedBox(height: 4),
        Text(
          'Style: ${_style?.label ?? 'No style preset'}',
          style: text.bodySmall,
        ),
        Text('Framing: $framing', style: text.bodySmall),
        Text('Captions: ${_captionChoice.label}', style: text.bodySmall),
        Text('Attached: $extras', style: text.bodySmall),
        Text(
          _referenceStyle == null
              ? 'Reference video: none'
              : 'Reference style: ready to apply',
          style: text.bodySmall,
        ),
        const SizedBox(height: 6),
        Text(
          'Tap Generate edited video below to build the full '
          'step-by-step plan.',
          style: text.bodySmall,
        ),
      ],
    );
  }

  @override
  Widget build(BuildContext context) {
    final TextTheme text = Theme.of(context).textTheme;
    final EditingPlan? plan = _plan;
    return Scaffold(
      appBar: AppBar(title: const Text('Workspace')),
      body: SafeArea(
        child: SingleChildScrollView(
          padding: const EdgeInsets.all(20),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: <Widget>[
              Text(
                _clips.length > 1
                    ? 'Source clips (${_clips.length})'
                    : 'Source video',
                style: text.titleMedium,
              ),
              const SizedBox(height: 4),
              Text(_clipsHeaderLine(), style: text.bodySmall),
              const SizedBox(height: 4),
              Text(
                'Main clips form the story in order. A reference video '
                'guides style only and is never part of the output.',
                style: text.bodySmall,
              ),
              const SizedBox(height: 8),
              ..._buildClipRows(text),
              if (_clipsError != null) ...<Widget>[
                const SizedBox(height: 4),
                _InlineError(message: _clipsError!),
              ],
              const SizedBox(height: 8),
              OutlinedButton.icon(
                key: const ValueKey<String>('addClipButton'),
                onPressed:
                    (_uploading || _planning || _rendering || _jobRunning)
                    ? null
                    : _addClip,
                icon: const Icon(Icons.add_outlined),
                label: Text('Add a clip (${_clips.length}/$_maxClips)'),
              ),
              const SizedBox(height: 12),
              if (_clips.length > 1)
                Padding(
                  padding: const EdgeInsets.only(bottom: 4),
                  child: Text(
                    'Previewing clip ${_previewIndex + 1} of '
                    '${_clips.length}.',
                    style: text.bodySmall,
                  ),
                ),
              VideoPreviewPlayer(videoPath: _previewPath),
              const SizedBox(height: 16),
              SectionCard(
                key: const ValueKey<String>('instructionCard'),
                title: 'Describe your edit',
                icon: Icons.edit_note_outlined,
                helper:
                    'Speak or type what to change in the source video. '
                    'Voice instructions set the goal; source-video '
                    'captions stay separate.',
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: <Widget>[
                    TextField(
                      key: const ValueKey<String>('instructionField'),
                      controller: _instructionController,
                      minLines: 2,
                      maxLines: 4,
                      textCapitalization: TextCapitalization.sentences,
                      decoration: const InputDecoration(
                        labelText: 'How would you like to edit this video?',
                      ),
                    ),
                    const SizedBox(height: 8),
                    OutlinedButton.icon(
                      key: const ValueKey<String>('briefButton'),
                      onPressed: () => _openBrief(),
                      icon: const Icon(Icons.edit_note_outlined),
                      label: const Text('Help me describe my edit'),
                    ),
                  ],
                ),
              ),
              const SizedBox(height: 16),
              SectionCard(
                key: const ValueKey<String>('styleCard'),
                title: 'Style & framing',
                icon: Icons.tune_outlined,
                helper:
                    'Changes here are kept when you go back to the Create screen.',
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: <Widget>[
                    Text('Style', style: text.titleSmall),
                    const SizedBox(height: 8),
                    OptionChips<EditStyle>(
                      options: EditStyle.values,
                      selected: _style,
                      onSelected: (EditStyle? style) {
                        setState(() => _style = style);
                        _publish();
                      },
                      labelOf: (EditStyle style) => style.label,
                      iconOf: (EditStyle style) => style.icon,
                    ),
                    if (_style != null) ...<Widget>[
                      const SizedBox(height: 6),
                      Text(
                        '${_style!.label}: ${_style!.effect} Tap again to '
                        'clear it and remove its effect.',
                        style: text.bodySmall,
                      ),
                    ],
                    const SizedBox(height: 16),
                    Text('Aspect ratio', style: text.titleSmall),
                    const SizedBox(height: 8),
                    OptionChips<AspectRatioOption>(
                      options: AspectRatioOption.values,
                      selected: _aspectRatio,
                      onSelected: (AspectRatioOption? option) {
                        setState(() => _aspectRatio = option);
                        _publish();
                      },
                      labelOf: (AspectRatioOption option) => option.label,
                      iconOf: (AspectRatioOption option) => option.icon,
                    ),
                    if (_aspectRatio != null &&
                        _aspectRatio != AspectRatioOption.original) ...<Widget>[
                      const SizedBox(height: 12),
                      Text('Frame fill', style: text.titleSmall),
                      const SizedBox(height: 8),
                      OptionChips<ResizeFit>(
                        options: ResizeFit.values,
                        selected: _fit,
                        onSelected: (ResizeFit? fit) {
                          if (fit != null) {
                            setState(() => _fit = fit);
                            _publish();
                          }
                        },
                        labelOf: (ResizeFit fit) => fit.label,
                        iconOf: (ResizeFit fit) => fit.icon,
                      ),
                      const SizedBox(height: 6),
                      Text(_fit.hint, style: text.bodySmall),
                    ],
                  ],
                ),
              ),
              const SizedBox(height: 16),
              _buildAnalysisCard(text),
              const SizedBox(height: 16),
              _buildReferenceCard(text),
              const SizedBox(height: 16),
              _buildCutsCard(text),
              const SizedBox(height: 16),
              _buildCaptionsCard(text),
              const SizedBox(height: 16),
              _buildMusicSfxCard(text),
              const SizedBox(height: 16),
              SectionCard(
                key: const ValueKey<String>('thumbnailCard'),
                title: 'Video cover & artwork (optional)',
                icon: Icons.image_outlined,
                helper:
                    'A cover uses your own footage; artwork is generated. '
                    'Both are extra files and never replace your video.',
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: <Widget>[
                    SegmentedButton<bool>(
                      key: const ValueKey<String>('coverArtworkToggle'),
                      segments: const <ButtonSegment<bool>>[
                        ButtonSegment<bool>(
                          value: true,
                          label: Text('Video cover'),
                        ),
                        ButtonSegment<bool>(
                          value: false,
                          label: Text('Artwork'),
                        ),
                      ],
                      selected: <bool>{_showCoverTab},
                      onSelectionChanged: (Set<bool> selected) =>
                          setState(() => _showCoverTab = selected.first),
                    ),
                    const SizedBox(height: 12),
                    if (_showCoverTab)
                      _buildCoverTab(text)
                    else ...<Widget>[
                      TextField(
                        controller: _thumbPromptController,
                        textCapitalization: TextCapitalization.sentences,
                        decoration: const InputDecoration(
                          labelText: 'Artwork idea',
                        ),
                      ),
                      const SizedBox(height: 12),
                      OutlinedButton.icon(
                        key: const ValueKey<String>('generateThumbnailButton'),
                        onPressed: _thumbBusy ? null : _generateThumbnail,
                        icon: const Icon(Icons.add_photo_alternate_outlined),
                        label: Text(
                          _thumbBusy ? 'Generating…' : 'Generate artwork',
                        ),
                      ),
                      const SizedBox(height: 4),
                      Text(
                        'Ambiguous names are questioned first; policy '
                        'refusals are never reworded or retried.',
                        style: text.bodySmall,
                      ),
                    ],
                    if (!_showCoverTab && _thumbError != null) ...<Widget>[
                      const SizedBox(height: 10),
                      _InlineError(message: _thumbError!),
                    ],
                    if (!_showCoverTab && _thumbWantsCover) ...<Widget>[
                      const SizedBox(height: 8),
                      OutlinedButton.icon(
                        key: const ValueKey<String>('useCoverInsteadButton'),
                        onPressed: () => setState(() => _showCoverTab = true),
                        icon: const Icon(Icons.image_outlined),
                        label: const Text('Use a video frame instead'),
                      ),
                    ],
                    if (!_showCoverTab && _thumbPath != null) ...<Widget>[
                      const SizedBox(height: 12),
                      if (_thumbBusy)
                        Padding(
                          padding: const EdgeInsets.only(bottom: 8),
                          child: Text(
                            'Generating a replacement — this is the '
                            'previous image.',
                            style: text.bodySmall,
                          ),
                        ),
                      ClipRRect(
                        borderRadius: BorderRadius.circular(12),
                        child: Opacity(
                          opacity: _thumbBusy ? 0.55 : 1,
                          child: Image.file(
                            File(_thumbPath!),
                            errorBuilder: (_, _, _) => const SizedBox.shrink(),
                          ),
                        ),
                      ),
                      const SizedBox(height: 12),
                      Wrap(
                        spacing: 8,
                        runSpacing: 8,
                        children: <Widget>[
                          OutlinedButton.icon(
                            key: const ValueKey<String>('saveThumbnailButton'),
                            onPressed: _thumbBusy || _thumbSaving
                                ? null
                                : _saveThumbnail,
                            icon: const Icon(Icons.save_alt_outlined),
                            label: Text(
                              _thumbSaving ? 'Saving…' : 'Save image',
                            ),
                          ),
                          OutlinedButton.icon(
                            key: const ValueKey<String>('shareThumbnailButton'),
                            onPressed: _thumbBusy || _thumbSharing
                                ? null
                                : _shareThumbnail,
                            icon: const Icon(Icons.ios_share_outlined),
                            label: Text(
                              _thumbSharing ? 'Sharing…' : 'Share image',
                            ),
                          ),
                        ],
                      ),
                    ],
                    const SizedBox(height: 16),
                    const Divider(),
                    const SizedBox(height: 8),
                    _buildAttachmentsSection(text),
                    const SizedBox(height: 16),
                    const Divider(),
                    const SizedBox(height: 8),
                    _buildSupportSection(text),
                  ],
                ),
              ),
              const SizedBox(height: 16),
              SectionCard(
                key: const ValueKey<String>('editsCard'),
                title: 'Edits to apply',
                icon: Icons.fact_check_outlined,
                helper: plan == null
                    ? 'Your current selections. Generate to see the plan.'
                    : (plan.model.isEmpty
                          ? 'Review each edit and where it came from.'
                          : 'Planned by ${plan.model}. Review each edit and '
                                'where it came from.'),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: <Widget>[
                    if (plan == null)
                      _buildSelectionsSummary(text)
                    else ...<Widget>[
                      for (final PlanOperation op in plan.operations)
                        Padding(
                          padding: const EdgeInsets.only(bottom: 10),
                          child: Row(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: <Widget>[
                              Icon(
                                op.icon,
                                size: 20,
                                color: AppColors.violetSoft,
                              ),
                              const SizedBox(width: 10),
                              Expanded(
                                child: Column(
                                  crossAxisAlignment: CrossAxisAlignment.start,
                                  children: <Widget>[
                                    Text(op.label, style: text.titleSmall),
                                    if (op.describe().isNotEmpty)
                                      Text(
                                        op.describe(),
                                        style: text.bodySmall,
                                      ),
                                    if (op.origin.isNotEmpty)
                                      Text(
                                        op.origin,
                                        style: text.bodySmall?.copyWith(
                                          color: AppColors.violetSoft,
                                        ),
                                      ),
                                  ],
                                ),
                              ),
                            ],
                          ),
                        ),
                      if (plan.notes.isNotEmpty)
                        Text(plan.notes, style: text.bodySmall),
                      for (final String note in plan.adjustments)
                        Padding(
                          padding: const EdgeInsets.only(top: 6),
                          child: Text(note, style: text.bodySmall),
                        ),
                      for (final String warning in plan.warnings)
                        Padding(
                          padding: const EdgeInsets.only(top: 6),
                          child: _InlineWarning(message: warning),
                        ),
                      for (final String item in plan.unsupported)
                        Padding(
                          padding: const EdgeInsets.only(top: 6),
                          child: _InlineWarning(
                            message:
                                'Not supported yet, left out of this render: '
                                '$item',
                          ),
                        ),
                      for (final String item in plan.pending)
                        Padding(
                          padding: const EdgeInsets.only(top: 6),
                          child: _InlineWarning(
                            message: 'Waiting on you: $item',
                          ),
                        ),
                      if (_serverPlanLine().isNotEmpty)
                        Padding(
                          padding: const EdgeInsets.only(top: 6),
                          child: Text(
                            'Server planning: ${_serverPlanLine()}.',
                            key: const ValueKey<String>('planTimingsLine'),
                            style: text.bodySmall,
                          ),
                        ),
                      for (final String note in _suppressions())
                        Padding(
                          padding: const EdgeInsets.only(top: 6),
                          child: Text(note, style: text.bodySmall),
                        ),
                      if (_extrasSummary().isNotEmpty) ...<Widget>[
                        const SizedBox(height: 6),
                        Text(
                          'Attached for the next render: ${_extrasSummary()}',
                          style: text.bodySmall,
                        ),
                      ],
                      const SizedBox(height: 12),
                      _buildReviewSection(text),
                    ],
                  ],
                ),
              ),
              const SizedBox(height: 16),
              SectionCard(
                key: const ValueKey<String>('generateCard'),
                title: 'AI editing',
                icon: Icons.auto_awesome_outlined,
                helper:
                    'Uses your backend and Meta API (usage costs apply). '
                    'See the Status tab for what is connected.',
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: <Widget>[
                    if (_uploading) ...<Widget>[
                      LinearProgressIndicator(value: _uploadProgress),
                      const SizedBox(height: 8),
                      Text(
                        '${_uploadStage ?? 'Uploading source'}… '
                        '${(_uploadProgress * 100).round()}%',
                        style: text.bodySmall,
                      ),
                      const SizedBox(height: 12),
                    ],
                    CheckboxListTile(
                      key: const ValueKey<String>('allowWonderToggle'),
                      value: _allowWonder,
                      dense: true,
                      contentPadding: EdgeInsets.zero,
                      controlAffinity: ListTileControlAffinity.leading,
                      title: Text(
                        'Allow Muse Wonder retry',
                        style: text.bodyMedium,
                      ),
                      subtitle: Text(
                        'Off by default. If Spark\u2019s plan fails '
                        'validation, retry once with Muse Wonder '
                        '(higher cost).',
                        style: text.bodySmall,
                      ),
                      onChanged: (_jobRunning || _planning || _uploading)
                          ? null
                          : (bool? value) {
                              setState(
                                () => _allowWonder = value ?? false,
                              );
                              _publish();
                            },
                    ),
                    const SizedBox(height: 8),
                    PrimaryButton(
                      key: const ValueKey<String>('generateEditedVideoButton'),
                      label: _jobRunning
                          ? 'Generating…'
                          : 'Generate edited video',
                      icon: Icons.movie_creation_outlined,
                      onPressed:
                          _jobRunning || _planning || _rendering || _uploading
                          ? null
                          : _generateEditedVideo,
                    ),
                    if (_stageSummaryLine().isNotEmpty) ...<Widget>[
                      const SizedBox(height: 8),
                      Text(
                        _stageSummaryLine(),
                        key: const ValueKey<String>('stageTimingsLine'),
                        style: text.bodySmall,
                        textAlign: TextAlign.center,
                      ),
                    ],
                    if (_serverSummaryLine().isNotEmpty) ...<Widget>[
                      const SizedBox(height: 4),
                      Text(
                        _serverSummaryLine(),
                        key: const ValueKey<String>('serverTimingsLine'),
                        style: text.bodySmall,
                        textAlign: TextAlign.center,
                      ),
                    ],
                    if (_instructionController.text.trim().isEmpty &&
                        !_jobRunning) ...<Widget>[
                      const SizedBox(height: 8),
                      Text(
                        'Describe the edit above — or tap “Help me describe '
                        'my edit” — to enable Generate.',
                        style: text.bodySmall,
                      ),
                    ],
                  ],
                ),
              ),
              const SizedBox(height: 16),
              _buildStatusCard(text),
              const SizedBox(height: 16),
              if (_renderPath != null) ...<Widget>[
                const SizedBox(height: 16),
                SectionCard(
                  key: const ValueKey<String>('editedCard'),
                  title: 'Edited video',
                  icon: Icons.play_circle_outline,
                  helper: _editedVideoHelper(),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: <Widget>[
                      VideoPreviewPlayer(
                        key: ValueKey<String>(_renderPath!),
                        videoPath: _renderPath!,
                        onPrepared: (Duration elapsed) {
                          if (mounted) {
                            setState(
                              () => _playerPrepDuration = elapsed,
                            );
                          }
                        },
                      ),
                      if (_renderSteps.isNotEmpty) ...<Widget>[
                        const SizedBox(height: 10),
                        Wrap(
                          spacing: 8,
                          runSpacing: 8,
                          children: <Widget>[
                            for (final String step in _renderSteps)
                              Chip(label: Text(step)),
                          ],
                        ),
                      ],
                      for (final String warning in _renderWarnings)
                        Padding(
                          padding: const EdgeInsets.only(top: 6),
                          child: _InlineWarning(message: warning),
                        ),
                      for (final String note in _renderAdjustments)
                        Padding(
                          padding: const EdgeInsets.only(top: 6),
                          child: Text(note, style: text.bodySmall),
                        ),
                      if (_playerPrepDuration != null)
                        Padding(
                          padding: const EdgeInsets.only(top: 6),
                          child: Text(
                            'Preview ready in '
                            '${_formatDuration(_playerPrepDuration!)}.',
                            style: text.bodySmall,
                          ),
                        ),
                      const SizedBox(height: 12),
                      Row(
                        children: <Widget>[
                          Expanded(
                            child: OutlinedButton.icon(
                              key: const ValueKey<String>('shareEditedButton'),
                              onPressed: _sharing ? null : _export,
                              icon: const Icon(Icons.ios_share_outlined),
                              label: Text(_sharing ? 'Sharing…' : 'Share'),
                            ),
                          ),
                        ],
                      ),
                      if (_attachedCoverPath() != null) ...<Widget>[
                        const SizedBox(height: 12),
                        const Divider(),
                        const SizedBox(height: 8),
                        _buildAttachedCoverRow(text),
                      ],
                    ],
                  ),
                ),
              ] else ...<Widget>[
                const SizedBox(height: 16),
                OutlinedButton.icon(
                  onPressed: null,
                  icon: const Icon(Icons.ios_share_outlined),
                  label: const Text('Share'),
                ),
                const SizedBox(height: 4),
                Text(
                  'Share unlocks after a real render.',
                  style: text.bodySmall,
                  textAlign: TextAlign.center,
                ),
              ],
              const SizedBox(height: 16),
              _buildAdvancedSection(text, plan != null),
              if (_connectionError != null) ...<Widget>[
                const SizedBox(height: 16),
                Card(
                  child: Padding(
                    padding: const EdgeInsets.all(16),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.stretch,
                      children: <Widget>[
                        Row(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: <Widget>[
                            const Icon(
                              Icons.cloud_off_outlined,
                              size: 20,
                              color: AppColors.violetSoft,
                            ),
                            const SizedBox(width: 10),
                            Expanded(
                              child: Column(
                                crossAxisAlignment: CrossAxisAlignment.start,
                                children: <Widget>[
                                  Text(
                                    'Backend is not connected.',
                                    style: text.titleSmall,
                                  ),
                                  const SizedBox(height: 4),
                                  Text(
                                    _connectionError!,
                                    style: text.bodySmall,
                                  ),
                                ],
                              ),
                            ),
                          ],
                        ),
                        const SizedBox(height: 12),
                        OutlinedButton.icon(
                          onPressed: _openStatus,
                          icon: const Icon(Icons.insights_outlined),
                          label: const Text('Open AI Status'),
                        ),
                      ],
                    ),
                  ),
                ),
              ],
              const SizedBox(height: 12),
            ],
          ),
        ),
      ),
    );
  }
}

class _StageRow extends StatelessWidget {
  final _JobStage stage;
  final _StageState state;

  const _StageRow({required this.stage, required this.state});

  @override
  Widget build(BuildContext context) {
    final TextTheme text = Theme.of(context).textTheme;
    final Widget icon = switch (state) {
      _StageState.idle => const Icon(
        Icons.radio_button_unchecked_outlined,
        size: 20,
      ),
      _StageState.active => const SizedBox(
        width: 20,
        height: 20,
        child: CircularProgressIndicator(strokeWidth: 2),
      ),
      _StageState.done => const Icon(
        Icons.check_circle_outline,
        size: 20,
        color: Colors.green,
      ),
      _StageState.failed => Icon(
        Icons.error_outline,
        size: 20,
        color: Theme.of(context).colorScheme.error,
      ),
    };
    return Padding(
      padding: const EdgeInsets.only(bottom: 6),
      child: Row(
        children: <Widget>[
          icon,
          const SizedBox(width: 10),
          Text(stage.label, style: text.bodyMedium),
        ],
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

class _InlineWarning extends StatelessWidget {
  final String message;

  const _InlineWarning({required this.message});

  @override
  Widget build(BuildContext context) {
    final TextTheme text = Theme.of(context).textTheme;
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        const Icon(
          Icons.warning_amber_outlined,
          size: 20,
          color: AppColors.violetSoft,
        ),
        const SizedBox(width: 8),
        Expanded(child: Text(message, style: text.bodySmall)),
      ],
    );
  }
}
