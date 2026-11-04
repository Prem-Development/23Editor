import 'dart:async';
import 'dart:io';

import 'package:editor_23/models/edit_setup.dart';
import 'package:editor_23/models/project.dart';
import 'package:editor_23/models/video_selection.dart';
import 'package:editor_23/screens/workspace_screen.dart';
import 'package:editor_23/services/backend_client.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:path_provider_platform_interface/path_provider_platform_interface.dart';
import 'package:plugin_platform_interface/plugin_platform_interface.dart';

class FakePathProviderPlatform extends PathProviderPlatform
    with MockPlatformInterfaceMixin {
  FakePathProviderPlatform(this.tempPath);

  final String tempPath;

  @override
  Future<String?> getTemporaryPath() async => tempPath;

  @override
  Future<String?> getApplicationDocumentsPath() async => tempPath;
}

class AttachClient extends BackendClient {
  AttachClient({this.uploadGate, this.failUploads = 0});

  /// When set, the next asset upload waits on this gate (then clears it).
  Completer<Map<String, dynamic>>? uploadGate;

  /// Number of upcoming asset uploads that fail before succeeding.
  int failUploads;

  final List<Map<String, String>> uploads = <Map<String, String>>[];
  Map<String, dynamic>? lastRenderPlan;
  String? downloadRoute;
  String? downloadDest;

  int assetUploads() => uploads
      .where((Map<String, String> call) => call['label'] == 'asset')
      .length;

  @override
  Future<Map<String, dynamic>> getCapabilities() async {
    return <String, dynamic>{
      'sfx_assets': <String>['pop'],
      'banner_positions': <String>['top', 'bottom'],
    };
  }

  @override
  Future<Map<String, dynamic>> uploadFile(
    String path, {
    void Function(int sent, int total)? onProgress,
    String label = 'source',
  }) async {
    if (label == 'asset' && failUploads > 0) {
      failUploads--;
      throw BackendException('Photo upload failed.', 500);
    }
    final Completer<Map<String, dynamic>>? gate =
        label == 'asset' ? uploadGate : null;
    uploadGate = null;
    if (gate != null) {
      return gate.future;
    }
    uploads.add(<String, String>{'path': path, 'label': label});
    final String fileId = label == 'asset'
        ? '${'a' * 31}${uploads.length}'
        : 'f1';
    return <String, dynamic>{
      'file_id': fileId,
      'label': label,
      'media': <String, dynamic>{
        'duration': 13.0,
        'width': 640,
        'height': 360,
        'has_audio': true,
      },
    };
  }

  @override
  Future<Map<String, dynamic>> postJson(
    String route,
    Map<String, dynamic> body, {
    Duration timeout = const Duration(seconds: 120),
  }) async {
    if (route == '/api/plan') {
      return <String, dynamic>{
        'plan': <String, dynamic>{
          'operations': <Map<String, dynamic>>[
            <String, dynamic>{'op': 'trim', 'start': 0.0, 'end': 5.0},
          ],
          'notes': '',
        },
        'warnings': <String>[],
        'pending': <String>[],
        'model': 'test-model',
      };
    }
    if (route == '/api/render') {
      lastRenderPlan = body['plan'] as Map<String, dynamic>?;
      return <String, dynamic>{
        'render_id': 'r1',
        'url': '/api/files/r1',
        'steps_used': <String>['plan_validated', 'ffmpeg_render'],
        'warnings': <String>[],
        'adjustments': <String>[],
        'plan': body['plan'],
        'output': <String, dynamic>{'duration': 7.0, 'has_audio': true},
      };
    }
    throw BackendException('Unexpected route $route', 404);
  }

  @override
  Future<Map<String, dynamic>> getJson(String route) async {
    return <String, dynamic>{};
  }

  @override
  Future<Map<String, dynamic>> deleteJson(String route) async {
    return <String, dynamic>{};
  }

  @override
  Future<void> downloadToFile(String route, String destPath) async {
    downloadRoute = route;
    downloadDest = destPath;
  }
}

Finder _key(String name) => find.byKey(ValueKey<String>(name));

Future<void> _pump(
  WidgetTester tester, {
  required AttachClient client,
  required String imagePath,
  Future<void> Function(String path)? onSaveThumbnail,
  Future<void> Function(String path)? onShareThumbnail,
  VideoSelection video = const VideoSelection('clip.mp4'),
  List<String>? pickQueue,
  Project? project,
}) async {
  await tester.pumpWidget(
    MaterialApp(
      home: WorkspaceScreen(
        video: video,
        setup: const EditSetup(instruction: 'keep only the first 5 seconds'),
        onChanged: (_) {},
        client: client,
        onShare: (_) async {},
        pickImage: () async => pickQueue != null && pickQueue.isNotEmpty
            ? pickQueue.removeAt(0)
            : imagePath,
        onSaveThumbnail: onSaveThumbnail,
        onShareThumbnail: onShareThumbnail,
        project: project,
      ),
    ),
  );
  await tester.pumpAndSettle();
}

Future<void> _selectDropdown(
  WidgetTester tester,
  String fieldKey,
  String optionLabel,
) async {
  await tester.ensureVisible(_key(fieldKey));
  await tester.pumpAndSettle();
  await tester.tap(_key(fieldKey));
  await tester.pumpAndSettle();
  // The open menu duplicates the label in the overlay; the menu copy
  // is the last match.
  await tester.tap(find.text(optionLabel).last);
  await tester.pumpAndSettle();
}

Future<void> _uploadAndPickKind(WidgetTester tester, String kindLabel) async {
  await tester.ensureVisible(_key('uploadImageButton'));
  await tester.pumpAndSettle();
  await tester.tap(_key('uploadImageButton'));
  await tester.pumpAndSettle();
  await _selectDropdown(tester, 'attachKindField', kindLabel);
}

Future<void> _expandAdvanced(WidgetTester tester) async {
  await tester.ensureVisible(find.text('Show step-by-step controls'));
  await tester.pumpAndSettle();
  await tester.tap(find.text('Show step-by-step controls'));
  await tester.pumpAndSettle();
}

Future<void> _tapPlan(WidgetTester tester) async {
  await tester.ensureVisible(_key('generatePlanButton'));
  await tester.pumpAndSettle();
  await tester.tap(_key('generatePlanButton'));
  await tester.pumpAndSettle();
}

Future<void> _planAndRender(WidgetTester tester) async {
  if (_key('generatePlanButton').evaluate().isEmpty) {
    await _expandAdvanced(tester);
  }
  await _tapPlan(tester);
  await tester.ensureVisible(_key('renderVideoButton'));
  await tester.pumpAndSettle();
  await tester.tap(_key('renderVideoButton'));
  await tester.pumpAndSettle();
}

List<Map<String, dynamic>> _renderOps(AttachClient client) {
  final List<dynamic> ops =
      (client.lastRenderPlan?['operations'] as List?) ?? <dynamic>[];
  return ops.whereType<Map<String, dynamic>>().toList();
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  late final Directory tempDir;
  late final String imagePath;

  setUpAll(() async {
    tempDir = await Directory.systemTemp.createTemp('ws-attach-test');
    PathProviderPlatform.instance = FakePathProviderPlatform(tempDir.path);
    final File image = File('${tempDir.path}/still.png');
    await image.writeAsBytes(<int>[137, 80, 78, 71]);
    imagePath = image.path;
  });

  testWidgets('intro attaches and reaches the render as an op', (
    WidgetTester tester,
  ) async {
    final AttachClient client = AttachClient();
    await _pump(tester, client: client, imagePath: imagePath);

    await _uploadAndPickKind(tester, 'Intro still');
    await tester.enterText(_key('attachDurationField'), '2.0');
    await tester.pump();
    await tester.ensureVisible(_key('addAttachmentButton'));
    await tester.pumpAndSettle();
    await tester.tap(_key('addAttachmentButton'));
    await tester.pumpAndSettle();

    expect(find.text('2.0s intro image (still.png)'), findsWidgets);
    await _planAndRender(tester);

    expect(
      client.uploads.any(
        (Map<String, String> call) =>
            call['label'] == 'asset' && call['path'] == imagePath,
      ),
      isTrue,
    );
    final List<Map<String, dynamic>> ops = _renderOps(client);
    final Map<String, dynamic> intro = ops.firstWhere(
      (Map<String, dynamic> op) => op['op'] == 'intro',
    );
    expect(intro['duration'], 2.0);
    expect(intro['origin'], 'control');
    expect((intro['file_id'] as String?)?.length, 32);
  });

  testWidgets('banner past the estimated end is rejected, valid one renders', (
    WidgetTester tester,
  ) async {
    final AttachClient client = AttachClient();
    await _pump(tester, client: client, imagePath: imagePath);

    // Planning uploads the source, so the app knows the 13s span.
    await _expandAdvanced(tester);
    await _tapPlan(tester);

    await _uploadAndPickKind(tester, 'Banner overlay');
    await tester.enterText(_key('attachBannerStartField'), '1');
    await tester.enterText(_key('attachBannerEndField'), '99');
    await tester.pump();
    await tester.ensureVisible(_key('addAttachmentButton'));
    await tester.pumpAndSettle();
    await tester.tap(_key('addAttachmentButton'));
    await tester.pumpAndSettle();
    expect(find.textContaining('past the estimated'), findsOneWidget);
    expect(find.textContaining('Banner 1.0'), findsNothing);

    await tester.enterText(_key('attachBannerEndField'), '4');
    await tester.pump();
    await tester.ensureVisible(_key('addAttachmentButton'));
    await tester.pumpAndSettle();
    await tester.tap(_key('addAttachmentButton'));
    await tester.pumpAndSettle();
    expect(find.textContaining('Banner 1.0–4.0s'), findsWidgets);

    await _planAndRender(tester);
    final List<Map<String, dynamic>> ops = _renderOps(client);
    final Map<String, dynamic> banner = ops.firstWhere(
      (Map<String, dynamic> op) => op['op'] == 'banner',
    );
    expect(banner['start'], 1.0);
    expect(banner['end'], 4.0);
    expect(banner['position'], 'bottom');
    expect(banner['width_frac'], 0.4);
  });

  testWidgets('cover attaches as export-only and can be removed', (
    WidgetTester tester,
  ) async {
    final AttachClient client = AttachClient();
    await _pump(tester, client: client, imagePath: imagePath);

    await tester.ensureVisible(_key('uploadImageButton'));
    await tester.pumpAndSettle();
    await tester.tap(_key('uploadImageButton'));
    await tester.pumpAndSettle();
    // Default kind is the cover.
    await tester.ensureVisible(_key('addAttachmentButton'));
    await tester.pumpAndSettle();
    await tester.tap(_key('addAttachmentButton'));
    await tester.pumpAndSettle();
    expect(
      find.text('Cover image (still.png) (saved beside the video)'),
      findsWidgets,
    );

    await _planAndRender(tester);
    expect(
      _renderOps(client).any(
        (Map<String, dynamic> op) =>
            op['op'] == 'cover' ||
            op['op'] == 'intro' ||
            op['op'] == 'outro' ||
            op['op'] == 'banner',
      ),
      isFalse,
    );
    // The photo uploaded once at pick time; the cover-only render
    // adds no render-time upload.
    expect(client.assetUploads(), 1);

    await tester.ensureVisible(_key('removeAttachment_cover'));
    await tester.pumpAndSettle();
    await tester.tap(_key('removeAttachment_cover'));
    await tester.pumpAndSettle();
    expect(
      find.text('Cover image (still.png) (saved beside the video)'),
      findsNothing,
    );
  });

  testWidgets('attached cover exports beside the edited video', (
    WidgetTester tester,
  ) async {
    final AttachClient client = AttachClient();
    final List<String> shared = <String>[];
    final List<String> saved = <String>[];
    await _pump(
      tester,
      client: client,
      imagePath: imagePath,
      onSaveThumbnail: (String path) async => saved.add(path),
      onShareThumbnail: (String path) async => shared.add(path),
    );

    await tester.ensureVisible(_key('uploadImageButton'));
    await tester.pumpAndSettle();
    await tester.tap(_key('uploadImageButton'));
    await tester.pumpAndSettle();
    await tester.ensureVisible(_key('addAttachmentButton'));
    await tester.pumpAndSettle();
    await tester.tap(_key('addAttachmentButton'));
    await tester.pumpAndSettle();

    await _planAndRender(tester);
    expect(find.text('Edited video'), findsOneWidget);
    await tester.ensureVisible(_key('shareAttachedCoverButton'));
    await tester.pumpAndSettle();
    await tester.tap(_key('shareAttachedCoverButton'));
    await tester.pumpAndSettle();
    expect(shared, <String>[imagePath]);

    await tester.ensureVisible(_key('saveAttachedCoverButton'));
    await tester.pumpAndSettle();
    await tester.tap(_key('saveAttachedCoverButton'));
    await tester.pumpAndSettle();
    expect(saved, <String>[imagePath]);
  });

  testWidgets('a new video clears image attachments', (
    WidgetTester tester,
  ) async {
    final AttachClient client = AttachClient();
    await _pump(tester, client: client, imagePath: imagePath);

    await _uploadAndPickKind(tester, 'Outro still');
    await tester.enterText(_key('attachDurationField'), '1.5');
    await tester.pump();
    await tester.ensureVisible(_key('addAttachmentButton'));
    await tester.pumpAndSettle();
    await tester.tap(_key('addAttachmentButton'));
    await tester.pumpAndSettle();
    expect(find.text('1.5s outro image (still.png)'), findsWidgets);

    await _pump(
      tester,
      client: client,
      imagePath: imagePath,
      video: const VideoSelection('other.mp4'),
    );
    expect(find.text('1.5s outro image (still.png)'), findsNothing);
    expect(
      find.text(
        'No image yet. Make a cover, generate artwork, or upload '
        'an image first.',
      ),
      findsOneWidget,
    );
  });

  testWidgets('picked photo shows name and upload state', (
    WidgetTester tester,
  ) async {
    final AttachClient client = AttachClient();
    await _pump(tester, client: client, imagePath: imagePath);

    await tester.ensureVisible(_key('uploadImageButton'));
    await tester.pumpAndSettle();
    await tester.tap(_key('uploadImageButton'));
    await tester.pumpAndSettle();

    expect(_key('pendingPhotoCard'), findsOneWidget);
    expect(find.text('still.png'), findsOneWidget);
    expect(find.textContaining('State: Uploaded'), findsOneWidget);
    expect(find.textContaining('640×360'), findsOneWidget);
  });

  testWidgets('attach waits for the pending upload', (
    WidgetTester tester,
  ) async {
    final Completer<Map<String, dynamic>> gate =
        Completer<Map<String, dynamic>>();
    final AttachClient client = AttachClient(uploadGate: gate);
    await _pump(tester, client: client, imagePath: imagePath);

    await tester.ensureVisible(_key('uploadImageButton'));
    await tester.pumpAndSettle();
    await tester.tap(_key('uploadImageButton'));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 100));
    expect(find.textContaining('State: Uploading'), findsOneWidget);
    // No confirmed source yet: nothing is attachable mid-upload.
    expect(_key('addAttachmentButton'), findsNothing);
    expect(find.textContaining('No image yet'), findsOneWidget);

    gate.complete(<String, dynamic>{
      'file_id': 'b' * 32,
      'label': 'asset',
      'media': <String, dynamic>{
        'duration': 0.0,
        'width': 800,
        'height': 600,
        'has_audio': false,
      },
    });
    await tester.pumpAndSettle();
    expect(find.textContaining('State: Uploaded'), findsOneWidget);
    expect(_key('addAttachmentButton'), findsOneWidget);

    await _selectDropdown(tester, 'attachKindField', 'Intro still');
    await tester.enterText(_key('attachDurationField'), '2.0');
    await tester.pump();
    await tester.ensureVisible(_key('addAttachmentButton'));
    await tester.pumpAndSettle();
    await tester.tap(_key('addAttachmentButton'));
    await tester.pumpAndSettle();
    expect(
      find.text('2.0s intro image (still.png)'),
      findsWidgets,
    );

    await _planAndRender(tester);
    final Map<String, dynamic> intro = _renderOps(client).firstWhere(
      (Map<String, dynamic> op) => op['op'] == 'intro',
    );
    expect(intro['file_id'], 'b' * 32);
  });

  testWidgets('late upload response after re-pick is ignored', (
    WidgetTester tester,
  ) async {
    final File second = File('${tempDir.path}/second.png');
    // Sync IO: the test body runs in fake async, where awaited file
    // writes never complete (see ProjectStore.memory's note).
    second.writeAsBytesSync(<int>[137, 80, 78, 71]);
    final Completer<Map<String, dynamic>> gate =
        Completer<Map<String, dynamic>>();
    final AttachClient client = AttachClient(uploadGate: gate);
    await _pump(
      tester,
      client: client,
      imagePath: imagePath,
      pickQueue: <String>[imagePath, second.path],
    );

    await tester.ensureVisible(_key('uploadImageButton'));
    await tester.pumpAndSettle();
    await tester.tap(_key('uploadImageButton'));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 100));

    // Re-pick while the first upload is still in flight.
    await tester.tap(_key('uploadImageButton'));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 100));
    expect(find.text('second.png'), findsOneWidget);

    // The stale first response must not clobber the second photo.
    gate.complete(<String, dynamic>{
      'file_id': 'b' * 32,
      'label': 'asset',
      'media': <String, dynamic>{
        'duration': 0.0,
        'width': 1,
        'height': 1,
        'has_audio': false,
      },
    });
    await tester.pumpAndSettle();
    expect(find.text('second.png'), findsOneWidget);
    expect(find.textContaining('State: Uploaded'), findsOneWidget);

    await _selectDropdown(tester, 'attachKindField', 'Intro still');
    await tester.enterText(_key('attachDurationField'), '2.0');
    await tester.pump();
    await tester.ensureVisible(_key('addAttachmentButton'));
    await tester.pumpAndSettle();
    await tester.tap(_key('addAttachmentButton'));
    await tester.pumpAndSettle();
    await _planAndRender(tester);
    final Map<String, dynamic> intro = _renderOps(client).firstWhere(
      (Map<String, dynamic> op) => op['op'] == 'intro',
    );
    expect(intro['file_id'], isNot('b' * 32));
    expect((intro['file_id'] as String).length, 32);
  });

  testWidgets('replacing photo A with B renders B', (
    WidgetTester tester,
  ) async {
    final File second = File('${tempDir.path}/pic_b.png');
    // Sync IO: the test body runs in fake async, where awaited file
    // writes never complete (see ProjectStore.memory's note).
    second.writeAsBytesSync(<int>[137, 80, 78, 71]);
    final AttachClient client = AttachClient();
    await _pump(
      tester,
      client: client,
      imagePath: imagePath,
      pickQueue: <String>[imagePath, second.path],
    );

    await _expandAdvanced(tester);
    await _tapPlan(tester);
    await _uploadAndPickKind(tester, 'Banner overlay');
    await tester.enterText(_key('attachBannerStartField'), '1');
    await tester.enterText(_key('attachBannerEndField'), '4');
    await tester.pump();
    await tester.ensureVisible(_key('addAttachmentButton'));
    await tester.pumpAndSettle();
    await tester.tap(_key('addAttachmentButton'));
    await tester.pumpAndSettle();
    await _planAndRender(tester);
    final String firstId = _renderOps(client).firstWhere(
      (Map<String, dynamic> op) => op['op'] == 'banner',
    )['file_id'] as String;

    await tester.ensureVisible(_key('uploadImageButton'));
    await tester.pumpAndSettle();
    await tester.tap(_key('uploadImageButton'));
    await tester.pumpAndSettle();
    expect(find.text('pic_b.png'), findsOneWidget);
    await _selectDropdown(tester, 'attachKindField', 'Banner overlay');
    await tester.enterText(_key('attachBannerStartField'), '1');
    await tester.enterText(_key('attachBannerEndField'), '4');
    await tester.pump();
    await tester.ensureVisible(_key('addAttachmentButton'));
    await tester.pumpAndSettle();
    await tester.tap(_key('addAttachmentButton'));
    await tester.pumpAndSettle();
    await _planAndRender(tester);
    final String secondId = _renderOps(client).firstWhere(
      (Map<String, dynamic> op) => op['op'] == 'banner',
    )['file_id'] as String;
    expect(secondId, isNot(firstId));
  });

  testWidgets('reopened project renders from confirmed id', (
    WidgetTester tester,
  ) async {
    final AttachClient client = AttachClient();
    final Project project = Project(
      id: 'p1',
      name: 'clip.mp4',
      sourcePath: 'clip.mp4',
      fileName: 'clip.mp4',
      updatedAt: DateTime(2026, 1, 1),
      attachments: <Map<String, dynamic>>[
        <String, dynamic>{
          'kind': 'intro',
          'path': '${tempDir.path}/gone.png',
          'fileId': 'c' * 32,
          'name': 'gone.png',
          'duration': 2.0,
        },
      ],
    );
    await _pump(
      tester,
      client: client,
      imagePath: imagePath,
      project: project,
    );
    expect(
      find.text('2.0s intro image (gone.png)'),
      findsWidgets,
    );

    await _planAndRender(tester);
    final Map<String, dynamic> intro = _renderOps(client).firstWhere(
      (Map<String, dynamic> op) => op['op'] == 'intro',
    );
    expect(intro['file_id'], 'c' * 32);
    expect(client.assetUploads(), 0);
  });

  testWidgets('cover export after reopen downloads the asset', (
    WidgetTester tester,
  ) async {
    final AttachClient client = AttachClient();
    final List<String> shared = <String>[];
    final Project project = Project(
      id: 'p1',
      name: 'clip.mp4',
      sourcePath: 'clip.mp4',
      fileName: 'clip.mp4',
      updatedAt: DateTime(2026, 1, 1),
      attachments: <Map<String, dynamic>>[
        <String, dynamic>{
          'kind': 'cover',
          'path': '${tempDir.path}/gone.png',
          'fileId': 'd' * 32,
          'name': 'gone.png',
        },
      ],
    );
    await _pump(
      tester,
      client: client,
      imagePath: imagePath,
      onShareThumbnail: (String path) async => shared.add(path),
      project: project,
    );

    await _planAndRender(tester);
    await tester.ensureVisible(_key('shareAttachedCoverButton'));
    await tester.pumpAndSettle();
    await tester.tap(_key('shareAttachedCoverButton'));
    await tester.pumpAndSettle();
    expect(client.downloadRoute, '/api/files/${'d' * 32}');
    expect(client.downloadDest, isNotNull);
    expect(shared, <String>[client.downloadDest!]);
  });

  testWidgets('failed photo upload offers retry', (
    WidgetTester tester,
  ) async {
    final AttachClient client = AttachClient(failUploads: 1);
    await _pump(tester, client: client, imagePath: imagePath);

    await tester.ensureVisible(_key('uploadImageButton'));
    await tester.pumpAndSettle();
    await tester.tap(_key('uploadImageButton'));
    await tester.pumpAndSettle();
    expect(find.textContaining('Photo upload failed.'), findsOneWidget);
    expect(_key('retryPhotoButton'), findsOneWidget);

    await tester.ensureVisible(_key('retryPhotoButton'));
    await tester.pumpAndSettle();
    await tester.tap(_key('retryPhotoButton'));
    await tester.pumpAndSettle();
    expect(find.textContaining('State: Uploaded'), findsOneWidget);
  });
}
