import 'dart:io';

import 'package:editor_23/models/edit_setup.dart';
import 'package:editor_23/models/video_selection.dart';
import 'package:editor_23/screens/workspace_screen.dart';
import 'package:editor_23/services/backend_client.dart';
import 'package:editor_23/widgets/video_preview_player.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:path_provider_platform_interface/path_provider_platform_interface.dart';
import 'package:plugin_platform_interface/plugin_platform_interface.dart';

class FakeBackendClient extends BackendClient {
  String? renderInstruction;
  String? downloadRoute;
  String? downloadDest;
  Map<String, dynamic>? renderPlan;
  List<String> uploadPaths = <String>[];
  int capabilitiesFailures = 0;

  @override
  Future<Map<String, dynamic>> getCapabilities() async {
    if (capabilitiesFailures > 0) {
      capabilitiesFailures--;
      throw BackendException('effects list exploded', 500);
    }
    return <String, dynamic>{
      'sfx_assets': <String>['pop', 'chime', 'whoosh', 'click'],
    };
  }

  @override
  Future<Map<String, dynamic>> uploadFile(
    String path, {
    void Function(int sent, int total)? onProgress,
    String label = 'source',
  }) async {
    uploadPaths.add(path);
    return <String, dynamic>{'file_id': 'f1'};
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
        'adjustments': <String>['Added the requested first-5s trim.'],
        'model': 'muse-spark-1.3',
      };
    }
    if (route == '/api/transcribe') {
      return <String, dynamic>{
        'text': 'hello world',
        'approximate': false,
        'duration': 12.0,
        'turns': <Map<String, dynamic>>[
          <String, dynamic>{
            'text': 'hello',
            'raw': <String, dynamic>{'startMs': 500, 'endMs': 2500},
          },
          <String, dynamic>{
            'text': 'world',
            'raw': <String, dynamic>{'startMs': 3000, 'endMs': 5000},
          },
        ],
      };
    }
    if (route == '/api/render') {
      renderInstruction = body['instruction'] as String?;
      renderPlan = body['plan'] as Map<String, dynamic>?;
      return <String, dynamic>{
        'render_id': 'r1',
        'url': '/api/files/r1',
        'steps_used': <String>['plan_validated', 'ffmpeg_render'],
        'warnings': <String>[],
        'adjustments': <String>[],
        'plan': body['plan'],
        'output': <String, dynamic>{'duration': 5.0, 'has_audio': true},
      };
    }
    throw BackendException('Unexpected route $route', 404);
  }

  @override
  Future<void> downloadToFile(String route, String destPath) async {
    // Record only: real file IO would hang inside testWidgets' fake async
    // zone. The preview below only needs the path, and its player fails
    // gracefully on a missing platform anyway.
    downloadRoute = route;
    downloadDest = destPath;
  }
}

Future<void> _pumpWorkspace(
  WidgetTester tester, {
  required FakeBackendClient client,
  required List<String> shared,
  Future<String?> Function()? pickAudio,
}) async {
  await tester.pumpWidget(
    MaterialApp(
      home: WorkspaceScreen(
        video: const VideoSelection('clip.mp4'),
        setup: const EditSetup(instruction: 'keep only the first 5 seconds'),
        onChanged: (_) {},
        client: client,
        onShare: (String path) async {
          shared.add(path);
        },
        pickAudio: pickAudio,
      ),
    ),
  );
  // Let mocked async work (capabilities, preview) finish so scrolling
  // measures the final layout.
  await tester.pumpAndSettle();
}

class FakePathProviderPlatform extends PathProviderPlatform
    with MockPlatformInterfaceMixin {
  FakePathProviderPlatform(this.tempPath);

  final String tempPath;

  @override
  Future<String?> getTemporaryPath() async => tempPath;

  @override
  Future<String?> getApplicationDocumentsPath() async => tempPath;
}

Finder _key(String name) => find.byKey(ValueKey<String>(name));

Future<void> _expandAdvanced(WidgetTester tester) async {
  expect(find.text('Show step-by-step controls'), findsOneWidget);
  await tester.ensureVisible(find.text('Show step-by-step controls'));
  await tester.pumpAndSettle();
  await tester.tap(find.text('Show step-by-step controls'));
  await tester.pumpAndSettle();
}

Future<void> _tapPlan(WidgetTester tester) async {
  expect(_key('generatePlanButton'), findsOneWidget);
  await tester.ensureVisible(_key('generatePlanButton'));
  await tester.pumpAndSettle();
  await tester.tap(_key('generatePlanButton'));
  await tester.pumpAndSettle();
}

Future<void> _tapRender(WidgetTester tester) async {
  expect(_key('renderVideoButton'), findsOneWidget);
  await tester.ensureVisible(_key('renderVideoButton'));
  await tester.pumpAndSettle();
  await tester.tap(_key('renderVideoButton'));
  await tester.pumpAndSettle();
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  setUpAll(() async {
    final Directory dir = await Directory.systemTemp.createTemp(
      'ws-render-test',
    );
    PathProviderPlatform.instance = FakePathProviderPlatform(dir.path);
  });

  testWidgets('rendered preview and share use the new output, not the source', (
    WidgetTester tester,
  ) async {
    final FakeBackendClient client = FakeBackendClient();
    final List<String> shared = <String>[];
    await _pumpWorkspace(tester, client: client, shared: shared);
    await _expandAdvanced(tester);
    await _tapPlan(tester);
    expect(find.text('Trim'), findsOneWidget);
    expect(find.text('0.0s → 5.0s'), findsOneWidget);
    expect(find.text('Edits to apply'), findsOneWidget);

    await _tapRender(tester);

    expect(find.text('Trim'), findsOneWidget);
    expect(client.renderInstruction, 'keep only the first 5 seconds');
    expect(client.downloadRoute, '/api/files/r1');
    expect(find.text('Edited video'), findsOneWidget);
    expect(
      find.text('Measured output: 0:05 • Saved on this device.'),
      findsOneWidget,
    );
    final String? dest = client.downloadDest;
    expect(dest, isNotNull);
    expect(dest, isNot('clip.mp4'));

    final List<String> previewPaths = tester
        .widgetList<VideoPreviewPlayer>(find.byType(VideoPreviewPlayer))
        .map((VideoPreviewPlayer player) => player.videoPath)
        .toList();
    expect(previewPaths, contains('clip.mp4'));
    expect(previewPaths, contains(dest));

    expect(_key('shareEditedButton'), findsOneWidget);
    await tester.ensureVisible(_key('shareEditedButton'));
    await tester.pumpAndSettle();
    await tester.tap(_key('shareEditedButton'));
    await tester.pumpAndSettle();
    expect(shared, <String>[dest!]);
  });

  testWidgets('editing the instruction invalidates the plan and render', (
    WidgetTester tester,
  ) async {
    final FakeBackendClient client = FakeBackendClient();
    final List<String> shared = <String>[];
    await _pumpWorkspace(tester, client: client, shared: shared);
    await _expandAdvanced(tester);
    await _tapPlan(tester);
    expect(find.text('Trim'), findsOneWidget);

    await tester.enterText(
      find.byType(TextField).first,
      'keep only the first 5 seconds please',
    );
    await tester.pump();

    expect(find.text('Trim'), findsNothing);
    expect(find.text('Generate a plan first.'), findsOneWidget);
  });

  testWidgets('Generate edited video runs the staged job end to end', (
    WidgetTester tester,
  ) async {
    final FakeBackendClient client = FakeBackendClient();
    final List<String> shared = <String>[];
    await _pumpWorkspace(tester, client: client, shared: shared);

    expect(_key('generateEditedVideoButton'), findsOneWidget);
    await tester.ensureVisible(_key('generateEditedVideoButton'));
    await tester.pumpAndSettle();
    await tester.tap(_key('generateEditedVideoButton'));
    await tester.pumpAndSettle();

    for (final String stage in <String>[
      'Uploading',
      'Planning',
      'Rendering',
      'Preparing output',
    ]) {
      expect(find.text(stage), findsOneWidget);
    }
    expect(find.text('Edited video'), findsOneWidget);
    expect(client.downloadRoute, '/api/files/r1');
    expect(client.uploadPaths, <String>['clip.mp4']);
  });

  testWidgets('extras attach captions, music, and effects to the render', (
    WidgetTester tester,
  ) async {
    final FakeBackendClient client = FakeBackendClient();
    final List<String> shared = <String>[];
    await _pumpWorkspace(
      tester,
      client: client,
      shared: shared,
      pickAudio: () async => 'song.wav',
    );

    await _expandAdvanced(tester);
    await _tapPlan(tester);
    expect(find.text('Trim'), findsOneWidget);

    // Attaching extras invalidates the plan; the flow re-plans after.
    expect(_key('transcribeButton'), findsOneWidget);
    await tester.ensureVisible(_key('transcribeButton'));
    await tester.pumpAndSettle();
    await tester.tap(_key('transcribeButton'));
    await tester.pumpAndSettle();
    expect(find.text('Trim'), findsNothing);
    final Finder captionFields = find.byWidgetPredicate(
      (Widget widget) =>
          widget is TextField && widget.decoration?.labelText == 'Caption text',
    );
    expect(captionFields, findsNWidgets(2));
    await tester.enterText(captionFields.first, 'hi there');
    await tester.pump();

    expect(find.text('Auto'), findsOneWidget);
    expect(find.text('On'), findsOneWidget);
    await tester.ensureVisible(find.text('On'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('On'));
    await tester.pump();
    expect(find.text('Requests captions in the render'), findsOneWidget);

    expect(_key('chooseMusicButton'), findsOneWidget);
    await tester.ensureVisible(_key('chooseMusicButton'));
    await tester.pumpAndSettle();
    await tester.tap(_key('chooseMusicButton'));
    await tester.pumpAndSettle();
    expect(find.text('song.wav'), findsOneWidget);

    expect(_key('sfxAtField'), findsOneWidget);
    expect(_key('addSfxButton'), findsOneWidget);
    await tester.ensureVisible(_key('sfxAtField'));
    await tester.pumpAndSettle();
    await tester.enterText(_key('sfxAtField'), '2.5');
    await tester.pump();
    await tester.tap(_key('addSfxButton'));
    await tester.pump();
    expect(find.text('pop at 2.5s'), findsOneWidget);

    await _tapPlan(tester);
    expect(find.text('Trim'), findsOneWidget);

    await _tapRender(tester);

    final Map<String, dynamic>? plan = client.renderPlan;
    expect(plan, isNotNull);
    final List<String> ops = (plan!['operations'] as List)
        .map((dynamic op) => (op as Map)['op'] as String)
        .toList();
    expect(ops, containsAll(<String>['trim', 'captions', 'music', 'sfx']));
    final Map<String, dynamic> sfxOp = (plan['operations'] as List)
        .map((dynamic op) => op as Map<String, dynamic>)
        .firstWhere((Map<String, dynamic> op) => op['op'] == 'sfx');
    expect(sfxOp['asset'], 'pop');
    expect(sfxOp['at'], 2.5);
    final Map<String, dynamic> captionsOp = (plan['operations'] as List)
        .map((dynamic op) => op as Map<String, dynamic>)
        .firstWhere((Map<String, dynamic> op) => op['op'] == 'captions');
    expect((captionsOp['segments'] as List).first['text'], 'hi there');
    expect(client.uploadPaths, contains('song.wav'));
    expect(find.text('Edited video'), findsOneWidget);
  });

  testWidgets('effects list failure is distinct and retry reloads it', (
    WidgetTester tester,
  ) async {
    final FakeBackendClient client = FakeBackendClient()
      ..capabilitiesFailures = 1;
    final List<String> shared = <String>[];
    await _pumpWorkspace(tester, client: client, shared: shared);

    // A list-loading failure names itself; it is not reported as an
    // unreachable backend.
    expect(
      find.textContaining('Could not load the effects list'),
      findsOneWidget,
    );
    expect(find.textContaining('effects list exploded'), findsOneWidget);
    expect(find.textContaining('Connect to the backend'), findsNothing);

    await tester.ensureVisible(_key('retrySfxButton'));
    await tester.pumpAndSettle();
    await tester.tap(_key('retrySfxButton'));
    await tester.pumpAndSettle();

    expect(find.textContaining('Could not load'), findsNothing);
    expect(_key('sfxAtField'), findsOneWidget);
  });
}
