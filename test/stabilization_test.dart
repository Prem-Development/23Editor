import 'dart:async';
import 'dart:io';

import 'package:editor_23/models/capability_status.dart';
import 'package:editor_23/models/edit_setup.dart';
import 'package:editor_23/models/project.dart';
import 'package:editor_23/models/video_selection.dart';
import 'package:editor_23/screens/workspace_screen.dart';
import 'package:editor_23/services/backend_client.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:path_provider_platform_interface/path_provider_platform_interface.dart';
import 'package:plugin_platform_interface/plugin_platform_interface.dart';

class StabilizeClient extends BackendClient {
  StabilizeClient({
    this.planOperations,
    this.planWarnings = const <String>[],
    this.planPending = const <String>[],
    this.planAdjustments = const <String>[],
    this.planTimings = const <String, int>{},
    this.renderTimings = const <String, int>{},
    this.transcript,
  });

  final List<Map<String, dynamic>>? planOperations;
  final List<String> planWarnings;
  final List<String> planPending;
  final List<String> planAdjustments;
  final Map<String, int> planTimings;
  final Map<String, int> renderTimings;
  final Map<String, dynamic>? transcript;

  Map<String, dynamic>? planBody;
  Map<String, dynamic>? renderPlan;
  List<String> uploadPaths = <String>[];
  int planCalls = 0;

  /// When set, the plan response waits until the test releases it, so
  /// a second tap lands while the first plan is still in flight.
  Completer<void>? planHold;

  @override
  Future<Map<String, dynamic>> getCapabilities() async {
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
      planBody = body;
      planCalls++;
      final Completer<void>? hold = planHold;
      if (hold != null) {
        await hold.future;
      }
      return <String, dynamic>{
        'plan': <String, dynamic>{
          'operations':
              planOperations ??
              <Map<String, dynamic>>[
                <String, dynamic>{'op': 'trim', 'start': 0.0, 'end': 5.0},
              ],
          'notes': '',
        },
        'warnings': planWarnings,
        'pending': planPending,
        'adjustments': planAdjustments,
        'timings': planTimings,
        'model': 'muse-spark-1.3',
      };
    }
    if (route == '/api/transcribe') {
      return transcript ??
          <String, dynamic>{
            'text': 'hello world',
            'approximate': true,
            'duration': 12.0,
            'turns': <Map<String, dynamic>>[
              <String, dynamic>{
                'text': 'First line. Second line! Third line?',
                'raw': <String, dynamic>{},
              },
            ],
          };
    }
    if (route == '/api/thumbnail') {
      return <String, dynamic>{'image_id': 't1', 'url': '/api/files/t1'};
    }
    if (route == '/api/render') {
      renderPlan = body['plan'] as Map<String, dynamic>?;
      return <String, dynamic>{
        'render_id': 'r1',
        'url': '/api/files/r1',
        'steps_used': <String>['plan_validated', 'ffmpeg_render'],
        'warnings': <String>[],
        'adjustments': <String>[],
        'timings': renderTimings,
        'plan': body['plan'],
        'output': <String, dynamic>{
          'duration': 5.0,
          'has_audio': true,
          'width': 1080,
          'height': 1920,
          'sar': '1:1',
        },
      };
    }
    throw BackendException('Unexpected route $route', 404);
  }

  @override
  Future<void> downloadToFile(String route, String destPath) async {}
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

Future<void> _pump(
  WidgetTester tester, {
  required StabilizeClient client,
  String video = 'clip.mp4',
  Future<String?> Function()? pickAudio,
  Future<void> Function(String path)? onSaveThumbnail,
  Future<void> Function(String path)? onShareThumbnail,
  Project? project,
}) async {
  await tester.pumpWidget(
    MaterialApp(
      home: WorkspaceScreen(
        video: VideoSelection(video),
        setup: const EditSetup(instruction: 'keep the first 5 seconds'),
        onChanged: (_) {},
        client: client,
        onShare: (_) async {},
        pickAudio: pickAudio,
        onSaveThumbnail: onSaveThumbnail,
        onShareThumbnail: onShareThumbnail,
        project: project,
      ),
    ),
  );
  // Settle the mocked capabilities load before any scrolling so later
  // ensureVisible calls measure the final layout, not a mid-load one.
  await tester.pumpAndSettle();
}

Future<void> _expandAdvanced(WidgetTester tester) async {
  expect(find.text('Show step-by-step controls'), findsOneWidget);
  await tester.ensureVisible(find.text('Show step-by-step controls'));
  await tester.pumpAndSettle();
  await tester.tap(find.text('Show step-by-step controls'));
  await tester.pumpAndSettle();
}

Future<void> _plan(WidgetTester tester) async {
  expect(_key('generatePlanButton'), findsOneWidget);
  await tester.ensureVisible(_key('generatePlanButton'));
  await tester.pumpAndSettle();
  await tester.tap(_key('generatePlanButton'));
  await tester.pumpAndSettle();
}

Future<void> _render(WidgetTester tester) async {
  expect(_key('renderVideoButton'), findsOneWidget);
  await tester.ensureVisible(_key('renderVideoButton'));
  await tester.pumpAndSettle();
  await tester.tap(_key('renderVideoButton'));
  await tester.pumpAndSettle();
}

Future<void> _showArtwork(WidgetTester tester) async {
  expect(find.text('Artwork'), findsOneWidget);
  await tester.ensureVisible(find.text('Artwork'));
  await tester.pumpAndSettle();
  await tester.tap(find.text('Artwork'));
  await tester.pumpAndSettle();
}

Finder _captionFields() => find.byWidgetPredicate(
  (Widget widget) =>
      widget is TextField && widget.decoration?.labelText == 'Caption text',
);

Finder _atField() => find.byKey(const ValueKey<String>('sfxAtField'));

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  setUpAll(() async {
    final Directory dir = await Directory.systemTemp.createTemp(
      'ws-stabilize-test',
    );
    PathProviderPlatform.instance = FakePathProviderPlatform(dir.path);
  });

  testWidgets('captions default to Auto and plan without captions', (
    WidgetTester tester,
  ) async {
    final StabilizeClient client = StabilizeClient();
    await _pump(tester, client: client);
    await _expandAdvanced(tester);
    await _plan(tester);

    expect(client.planBody?['captions'], 'auto');
    expect(client.renderPlan, isNull);
    // Auto with no instruction mention: no captions op planned.
    await _render(tester);
    final List<dynamic> ops = client.renderPlan!['operations'] as List<dynamic>;
    expect(ops.where((dynamic op) => (op as Map)['op'] == 'captions'), isEmpty);
  });

  testWidgets('coarse transcript splits into editable lines', (
    WidgetTester tester,
  ) async {
    final StabilizeClient client = StabilizeClient();
    await _pump(tester, client: client);

    expect(_key('transcribeButton'), findsOneWidget);
    await tester.ensureVisible(_key('transcribeButton'));
    await tester.pumpAndSettle();
    await tester.tap(_key('transcribeButton'));
    await tester.pumpAndSettle();

    expect(_captionFields(), findsNWidgets(3));
    expect(find.text('Timing is approximate'), findsNothing);
    expect(find.textContaining('Timing is approximate'), findsOneWidget);
  });

  testWidgets('captions On attaches lines; removing a line drops it', (
    WidgetTester tester,
  ) async {
    final StabilizeClient client = StabilizeClient();
    await _pump(tester, client: client);

    expect(_key('transcribeButton'), findsOneWidget);
    await tester.ensureVisible(_key('transcribeButton'));
    await tester.pumpAndSettle();
    await tester.tap(_key('transcribeButton'));
    await tester.pumpAndSettle();
    expect(_captionFields(), findsNWidgets(3));

    expect(find.byTooltip('Remove line'), findsNWidgets(3));
    await tester.tap(find.byTooltip('Remove line').first);
    await tester.pump();
    expect(_captionFields(), findsNWidgets(2));

    expect(_key('captionChoiceSegmented'), findsOneWidget);
    expect(find.text('On'), findsOneWidget);
    await tester.ensureVisible(find.text('On'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('On'));
    await tester.pump();

    await _expandAdvanced(tester);
    await _plan(tester);
    expect(client.planBody?['captions'], 'on');
    await _render(tester);
    final List<dynamic> ops = client.renderPlan!['operations'] as List<dynamic>;
    final Map<dynamic, dynamic> captionsOp = ops
        .map((dynamic op) => op as Map)
        .firstWhere((Map op) => op['op'] == 'captions');
    expect((captionsOp['segments'] as List), hasLength(2));
    expect(captionsOp['origin'], 'control');
  });

  testWidgets('captions Off suppresses a planned captions op', (
    WidgetTester tester,
  ) async {
    final StabilizeClient client = StabilizeClient(
      planOperations: <Map<String, dynamic>>[
        <String, dynamic>{'op': 'trim', 'start': 0.0, 'end': 5.0},
        <String, dynamic>{'op': 'captions', 'source': 'auto'},
      ],
    );
    await _pump(tester, client: client);
    await _expandAdvanced(tester);
    await _plan(tester);
    // The planned captions op inside the Edits card (scoped: the
    // Captions settings card carries the same title text).
    expect(
      find.descendant(of: _key('editsCard'), matching: find.text('Captions')),
      findsOneWidget,
    );

    expect(find.text('Off'), findsOneWidget);
    await tester.ensureVisible(find.text('Off'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Off'));
    await tester.pumpAndSettle();
    // Switching to Off invalidates the plan; re-plan, then render.
    await _plan(tester);
    expect(client.planBody?['captions'], 'off');
    await _render(tester);
    final List<dynamic> ops = client.renderPlan!['operations'] as List<dynamic>;
    expect(ops.where((dynamic op) => (op as Map)['op'] == 'captions'), isEmpty);
  });

  testWidgets('advanced section stays open across plan updates', (
    WidgetTester tester,
  ) async {
    final StabilizeClient client = StabilizeClient();
    await _pump(tester, client: client);
    await _expandAdvanced(tester);
    await _plan(tester);

    // Planning inserts the plan card above Advanced; the open panel
    // must survive without re-expanding.
    expect(_key('generatePlanButton'), findsOneWidget);
    expect(_key('renderVideoButton'), findsOneWidget);

    await tester.enterText(find.byType(TextField).first, 'changed words');
    await tester.pumpAndSettle();

    // Invalidating the plan removes the card above; Advanced stays open
    // and offers to plan again.
    expect(_key('generatePlanButton'), findsOneWidget);
    expect(find.text('Generate a plan first.'), findsOneWidget);
  });

  testWidgets('plan shows origin labels and pending requests', (
    WidgetTester tester,
  ) async {
    final StabilizeClient client = StabilizeClient(
      planOperations: <Map<String, dynamic>>[
        <String, dynamic>{
          'op': 'trim',
          'start': 0.0,
          'end': 5.0,
          'origin': 'instruction',
        },
        <String, dynamic>{
          'op': 'grade',
          'preset': 'warm',
          'intensity': 0.5,
          'origin': 'preset',
        },
      ],
      planPending: const <String>['Captions need speech.'],
    );
    await _pump(tester, client: client);
    await _expandAdvanced(tester);
    await _plan(tester);

    expect(find.text('From your instruction'), findsOneWidget);
    expect(find.text('From the style preset'), findsOneWidget);
    expect(
      find.textContaining('Waiting on you: Captions need speech.'),
      findsOneWidget,
    );
  });

  testWidgets('plan shows adjustments as info and warnings as warnings', (
    WidgetTester tester,
  ) async {
    final StabilizeClient client = StabilizeClient(
      planAdjustments: const <String>["Style 'Cinematic' added a fade."],
      planWarnings: const <String>['Tracked parts leave a gap.'],
    );
    await _pump(tester, client: client);
    await _expandAdvanced(tester);
    await _plan(tester);

    expect(find.text("Style 'Cinematic' added a fade."), findsOneWidget);
    expect(find.text('Tracked parts leave a gap.'), findsOneWidget);
    // Only the review item carries a warning icon; the preset note is
    // plain information.
    expect(
      find.descendant(
        of: _key('editsCard'),
        matching: find.byIcon(Icons.warning_amber_outlined),
      ),
      findsOneWidget,
    );
  });

  testWidgets('job shows measured stage and server timings', (
    WidgetTester tester,
  ) async {
    final StabilizeClient client = StabilizeClient(
      planTimings: const <String, int>{
        'visual_analysis_ms': 0,
        'model_ms': 9200,
        'model_roundtrips': 2,
      },
      renderTimings: const <String, int>{
        'sam_ms': 0,
        'transcribe_ms': 8100,
        'ffmpeg_ms': 22400,
        'verify_ms': 120,
      },
    );
    await _pump(tester, client: client);

    await tester.ensureVisible(_key('generateEditedVideoButton'));
    await tester.pumpAndSettle();
    await tester.tap(_key('generateEditedVideoButton'));
    await tester.pumpAndSettle();

    expect(
      find.textContaining('Uploading', findRichText: false),
      findsWidgets,
    );
    final Finder stages = _key('stageTimingsLine');
    expect(stages, findsOneWidget);
    expect(
      tester.widget<Text>(stages).data,
      contains('Planning'),
    );
    expect(
      tester.widget<Text>(stages).data,
      contains('Rendering'),
    );
    expect(
      tester.widget<Text>(_key('serverTimingsLine')).data,
      'Server: planning: model 9.2s • 2 round trips • '
      'render: speech 8.1s • FFmpeg 22.4s',
    );
    expect(
      tester.widget<Text>(_key('planTimingsLine')).data,
      'Server planning: model 9.2s • 2 round trips.',
    );
  });

  testWidgets('double tap plans once', (WidgetTester tester) async {
    final StabilizeClient client = StabilizeClient();
    client.planHold = Completer<void>();
    await _pump(tester, client: client);
    await _expandAdvanced(tester);

    await tester.ensureVisible(_key('generatePlanButton'));
    await tester.pumpAndSettle();
    await tester.tap(_key('generatePlanButton'));
    await tester.tap(_key('generatePlanButton'));
    // The second tap lands while the first plan is still in flight.
    expect(client.planCalls, 1);
    client.planHold!.complete();
    await tester.pumpAndSettle();
    expect(client.planCalls, 1);
  });

  testWidgets('transcribe shows the measured time', (
    WidgetTester tester,
  ) async {
    final StabilizeClient client = StabilizeClient(
      transcript: <String, dynamic>{
        'text': 'hello world',
        'approximate': true,
        'duration': 12.0,
        'turns': <Map<String, dynamic>>[
          <String, dynamic>{
            'text': 'First line. Second line! Third line?',
            'raw': <String, dynamic>{},
          },
        ],
        'timings': <String, int>{
          'convert_ms': 210,
          'classify_ms': 90,
          'stt_ms': 5100,
        },
      },
    );
    await _pump(tester, client: client);

    await tester.ensureVisible(_key('transcribeButton'));
    await tester.pumpAndSettle();
    await tester.tap(_key('transcribeButton'));
    await tester.pumpAndSettle();

    expect(
      tester.widget<Text>(_key('transcribeTimingsLine')).data,
      contains('Speech recognition 5.1s'),
    );
  });

  testWidgets('sfx time at the output end is blocked with a message', (
    WidgetTester tester,
  ) async {
    final StabilizeClient client = StabilizeClient();
    await _pump(tester, client: client);
    await _expandAdvanced(tester);
    await _plan(tester);

    expect(_atField(), findsOneWidget);
    expect(_key('addSfxButton'), findsOneWidget);
    await tester.ensureVisible(_atField());
    await tester.pumpAndSettle();
    await tester.enterText(_atField(), '5.0');
    await tester.pump();
    await tester.tap(_key('addSfxButton'));
    await tester.pump();
    expect(find.textContaining('inaudible'), findsOneWidget);
    expect(find.textContaining('pop at'), findsNothing);

    await tester.enterText(_atField(), '4.0');
    await tester.pump();
    await tester.tap(_key('addSfxButton'));
    await tester.pump();
    expect(find.text('pop at 4.0s'), findsOneWidget);
  });

  testWidgets('sfx picker has no overflow on a narrow scaled screen', (
    WidgetTester tester,
  ) async {
    final StabilizeClient client = StabilizeClient();
    tester.view.physicalSize = const Size(320, 800);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(() {
      tester.view.resetPhysicalSize();
      tester.view.resetDevicePixelRatio();
    });
    await tester.pumpWidget(
      MaterialApp(
        home: MediaQuery(
          data: const MediaQueryData(textScaler: TextScaler.linear(1.3)),
          child: WorkspaceScreen(
            video: const VideoSelection('clip.mp4'),
            setup: const EditSetup(instruction: 'keep 5 seconds'),
            onChanged: (_) {},
            client: client,
            onShare: (_) async {},
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();

    expect(_atField(), findsOneWidget);
    expect(_key('addSfxButton'), findsOneWidget);
    await tester.ensureVisible(_atField());
    await tester.pumpAndSettle();
    await tester.enterText(_atField(), '2.0');
    await tester.pump();
    await tester.tap(_key('addSfxButton'));
    await tester.pumpAndSettle();
    expect(find.text('pop at 2.0s'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('fit chips appear for 1:1 and reach the plan', (
    WidgetTester tester,
  ) async {
    final StabilizeClient client = StabilizeClient();
    await _pump(tester, client: client);
    expect(find.text('Frame fill'), findsNothing);

    await tester.ensureVisible(find.text('1:1'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('1:1'));
    await tester.pump();
    expect(find.text('Frame fill'), findsOneWidget);

    await tester.ensureVisible(find.text('Crop'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Crop'));
    await tester.pump();

    await _expandAdvanced(tester);
    await _plan(tester);
    expect(client.planBody?['aspect_ratio'], '1:1');
    expect(client.planBody?['fit'], 'crop');
  });

  testWidgets('music removal reaches the render', (WidgetTester tester) async {
    final StabilizeClient client = StabilizeClient();
    await _pump(tester, client: client, pickAudio: () async => 'song.wav');

    expect(_key('chooseMusicButton'), findsOneWidget);
    await tester.ensureVisible(_key('chooseMusicButton'));
    await tester.pumpAndSettle();
    await tester.tap(_key('chooseMusicButton'));
    await tester.pumpAndSettle();
    expect(find.text('song.wav'), findsOneWidget);

    expect(find.byTooltip('Remove music'), findsOneWidget);
    await tester.tap(find.byTooltip('Remove music'));
    await tester.pump();
    expect(find.text('Choose music file'), findsOneWidget);

    await _expandAdvanced(tester);
    await _plan(tester);
    await _render(tester);
    final List<dynamic> ops = client.renderPlan!['operations'] as List<dynamic>;
    expect(ops.where((dynamic op) => (op as Map)['op'] == 'music'), isEmpty);
  });

  testWidgets('new video starts with no inherited extras', (
    WidgetTester tester,
  ) async {
    final StabilizeClient client = StabilizeClient();
    await _pump(tester, client: client, pickAudio: () async => 'song.wav');
    expect(_key('chooseMusicButton'), findsOneWidget);
    await tester.ensureVisible(_key('chooseMusicButton'));
    await tester.pumpAndSettle();
    await tester.tap(_key('chooseMusicButton'));
    await tester.pumpAndSettle();
    expect(find.text('song.wav'), findsOneWidget);

    await _pump(
      tester,
      client: client,
      video: 'other.mp4',
      pickAudio: () async => 'song.wav',
    );
    await tester.pumpAndSettle();
    expect(find.text('Choose music file'), findsOneWidget);
    expect(find.text('song.wav'), findsNothing);
  });

  testWidgets('thumbnail save and share call back with the file', (
    WidgetTester tester,
  ) async {
    final StabilizeClient client = StabilizeClient();
    final List<String> saved = <String>[];
    final List<String> shared = <String>[];
    await _pump(
      tester,
      client: client,
      onSaveThumbnail: (String path) async {
        saved.add(path);
      },
      onShareThumbnail: (String path) async {
        shared.add(path);
      },
    );

    await _showArtwork(tester);
    expect(_key('generateThumbnailButton'), findsOneWidget);
    await tester.ensureVisible(_key('generateThumbnailButton'));
    await tester.pumpAndSettle();
    await tester.tap(_key('generateThumbnailButton'));
    await tester.pumpAndSettle();

    expect(_key('saveThumbnailButton'), findsOneWidget);
    await tester.ensureVisible(_key('saveThumbnailButton'));
    await tester.pumpAndSettle();
    await tester.tap(_key('saveThumbnailButton'));
    await tester.pumpAndSettle();
    expect(saved, hasLength(1));

    expect(_key('shareThumbnailButton'), findsOneWidget);
    await tester.ensureVisible(_key('shareThumbnailButton'));
    await tester.pumpAndSettle();
    await tester.tap(_key('shareThumbnailButton'));
    await tester.pumpAndSettle();
    expect(shared, hasLength(1));
    expect(shared.first, saved.first);
  });

  testWidgets('thumbnail save failure shows a clear message', (
    WidgetTester tester,
  ) async {
    final StabilizeClient client = StabilizeClient();
    await _pump(
      tester,
      client: client,
      onSaveThumbnail: (_) async {
        throw Exception('denied');
      },
    );

    await _showArtwork(tester);
    expect(_key('generateThumbnailButton'), findsOneWidget);
    await tester.ensureVisible(_key('generateThumbnailButton'));
    await tester.pumpAndSettle();
    await tester.tap(_key('generateThumbnailButton'));
    await tester.pumpAndSettle();

    expect(_key('saveThumbnailButton'), findsOneWidget);
    await tester.ensureVisible(_key('saveThumbnailButton'));
    await tester.pumpAndSettle();
    await tester.tap(_key('saveThumbnailButton'));
    await tester.pumpAndSettle();
    expect(
      find.text('Could not save the image to the gallery.'),
      findsOneWidget,
    );
  });

  test('project extras survive a JSON round trip', () {
    final Project project = Project(
      id: 'p1',
      name: 'clip.mp4',
      sourcePath: 'clip.mp4',
      fileName: 'clip.mp4',
      fit: ResizeFit.crop,
      renderWidth: 1080,
      renderHeight: 1920,
      updatedAt: DateTime.utc(2026, 11, 3),
      captionChoice: 'on',
      captionLines: <Map<String, dynamic>>[
        <String, dynamic>{'start': 0.0, 'end': 1.5, 'text': 'hi'},
      ],
      musicPath: 'song.wav',
      musicVolumeDb: -6,
      musicDuck: false,
      sfxPicks: <Map<String, dynamic>>[
        <String, dynamic>{'asset': 'pop', 'at': 2.0},
      ],
      thumbPath: 'thumb.png',
    );
    final Project restored = Project.fromJson(project.toJson());
    expect(restored.fit, ResizeFit.crop);
    expect(restored.renderWidth, 1080);
    expect(restored.renderHeight, 1920);
    expect(restored.captionChoice, 'on');
    expect(restored.captionLines, hasLength(1));
    expect(restored.captionLines.first['text'], 'hi');
    expect(restored.musicPath, 'song.wav');
    expect(restored.musicVolumeDb, -6);
    expect(restored.musicDuck, isFalse);
    expect(restored.sfxPicks, hasLength(1));
    expect(restored.thumbPath, 'thumb.png');
  });

  test('BackendStatus parses builds, stamps, and snake_case keys', () {
    final BackendStatus status = BackendStatus.fromJson({
      'api_key_present': true,
      'api_access': 'verified',
      'server_build': '1.1.0',
      'ffmpeg': {'present': true, 'version': 'ffmpeg 7.1'},
      'integrations': [
        {
          'id': 'spark_plan',
          'status': 'verified',
          'detail': 'Plans work.',
          'verified_at': '2026-11-03T16:00:00',
        },
      ],
    });
    expect(status.apiKeyPresent, isTrue);
    expect(status.apiAccess, 'verified');
    expect(status.serverBuild, '1.1.0');
    expect(status.integrations.single.verifiedAt, '2026-11-03T16:00:00');
  });
}
