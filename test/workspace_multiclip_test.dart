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

class JoinClient extends BackendClient {
  JoinClient({this.maxClips = 8, this.joinFailure});

  final int maxClips;
  BackendException? joinFailure;

  final Map<String, String> pathIds = <String, String>{};
  final Map<String, double> idDurations = <String, double>{};
  final List<List<String>> joinCalls = <List<String>>[];
  int frameCalls = 0;
  Map<String, dynamic>? lastPlan;

  static const Map<String, double> _durations = <String, double>{
    'a.mp4': 10.0,
    'b.mp4': 20.0,
    'c.mp4': 30.0,
    'clip.mp4': 13.0,
  };

  @override
  Future<Map<String, dynamic>> getCapabilities() async {
    return <String, dynamic>{
      'operations': <dynamic>[],
      'sfx_assets': <String>[],
      'join_limits': <String, dynamic>{
        'max_clips': maxClips,
        'max_duration_sec': 1800.0,
      },
    };
  }

  @override
  Future<Map<String, dynamic>> uploadFile(
    String path, {
    void Function(int sent, int total)? onProgress,
    String label = 'source',
  }) async {
    final String name = path.split(RegExp(r'[/\\]')).last;
    final String tag = name.isEmpty ? 'x' : name[0];
    final String fileId = '$tag${'f' * 30}${pathIds.length}';
    pathIds[path] = fileId;
    final double duration = _durations[name] ?? 13.0;
    idDurations[fileId] = duration;
    return <String, dynamic>{
      'file_id': fileId,
      'label': label,
      'media': <String, dynamic>{
        'duration': duration,
        'width': 640,
        'height': 360,
        'fps': 30.0,
        'has_audio': true,
      },
    };
  }

  @override
  Future<Map<String, dynamic>> postJson(
    String route,
    Map<String, dynamic> body, {
    Duration timeout = const Duration(seconds: 30),
  }) async {
    if (route == '/api/join') {
      final List<String> ids = (body['file_ids'] as List)
          .map((Object? id) => id.toString())
          .toList();
      // Record attempts, not just successes: the retry test counts the
      // failed call and its retry.
      joinCalls.add(ids);
      final BackendException? failure = joinFailure;
      joinFailure = null;
      if (failure != null) {
        throw failure;
      }
      double offset = 0;
      final List<Map<String, dynamic>> clips = <Map<String, dynamic>>[];
      for (final String id in ids) {
        final double duration = idDurations[id] ?? 13.0;
        clips.add(<String, dynamic>{
          'file_id': id,
          'offset': offset,
          'duration': duration,
        });
        offset += duration;
      }
      return <String, dynamic>{
        'join_id': 'j' * 32,
        'url': '/api/files/${'j' * 32}',
        'duration': offset,
        'width': 640,
        'height': 360,
        'fps': 30.0,
        'has_audio': true,
        'cache': 'built',
        'clips': clips,
      };
    }
    if (route == '/api/frame') {
      frameCalls++;
      return <String, dynamic>{
        'image_id': 't' * 32,
        'url': '/api/files/${'t' * 32}',
        't': body['t'],
      };
    }
    if (route == '/api/plan') {
      lastPlan = body;
      return <String, dynamic>{
        'plan': <String, dynamic>{
          'operations': <Map<String, dynamic>>[
            <String, dynamic>{'op': 'trim', 'start': 0.0, 'end': 5.0},
          ],
        },
        'warnings': <String>[],
        'model': 'muse-spark-1.3',
      };
    }
    if (route == '/api/render') {
      return <String, dynamic>{
        'render_id': 'r1',
        'url': '/api/files/r1',
        'steps_used': <String>['plan_validated', 'ffmpeg_render'],
        'warnings': <String>[],
        'adjustments': <String>[],
        'plan': body['plan'],
        'output': <String, dynamic>{
          'duration': 5.0,
          'has_audio': true,
          'width': 640,
          'height': 360,
          'sar': '1:1',
        },
      };
    }
    throw BackendException('Unexpected route $route', 404);
  }

  @override
  Future<void> downloadToFile(String route, String destPath) async {
    File(destPath).writeAsBytesSync(<int>[137, 80, 78, 71]);
  }
}

class FakePathProviderPlatform extends PathProviderPlatform
    with MockPlatformInterfaceMixin {
  FakePathProviderPlatform(this.tempPath);

  final String tempPath;

  @override
  Future<String?> getTemporaryPath() async => tempPath;
}

void main() {
  late Directory tempDir;

  setUpAll(() async {
    tempDir = await Directory.systemTemp.createTemp('multiclip_test');
    PathProviderPlatform.instance = FakePathProviderPlatform(tempDir.path);
    for (final String name in <String>['a.mp4', 'b.mp4', 'c.mp4']) {
      File('${tempDir.path}/$name').writeAsBytesSync(<int>[0, 1, 2, 3]);
    }
  });

  Finder key(String name) => find.byKey(ValueKey<String>(name));

  Future<void> pump(
    WidgetTester tester, {
    required JoinClient client,
    List<String>? clipNames,
    List<String>? pickQueue,
    Project? project,
  }) async {
    final List<VideoSelection> clips = <VideoSelection>[
      for (final String name in clipNames ?? <String>['clip.mp4'])
        VideoSelection(
          name.contains('/') ? name : '${tempDir.path}/$name',
        ),
    ];
    final List<String> queue = pickQueue == null
        ? <String>[]
        : <String>[for (final String n in pickQueue) '${tempDir.path}/$n'];
    await tester.pumpWidget(
      MaterialApp(
        home: WorkspaceScreen(
          video: clips.first,
          clips: clips,
          setup: const EditSetup(
            instruction: 'keep only the first 5 seconds',
          ),
          onChanged: (_) {},
          client: client,
          onShare: (_) async {},
          pickClip: queue.isEmpty ? null : () async => queue.removeAt(0),
          project: project,
        ),
      ),
    );
    await tester.pumpAndSettle();
  }

  Future<void> expandAdvanced(WidgetTester tester) async {
    await tester.ensureVisible(find.text('Show step-by-step controls'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Show step-by-step controls'));
    await tester.pumpAndSettle();
  }

  Future<void> tapPlan(WidgetTester tester) async {
    await tester.ensureVisible(key('generatePlanButton'));
    await tester.pumpAndSettle();
    await tester.tap(key('generatePlanButton'));
    await tester.pumpAndSettle();
  }

  testWidgets('three clips join in order with offsets', (
    WidgetTester tester,
  ) async {
    final JoinClient client = JoinClient();
    await pump(
      tester,
      client: client,
      clipNames: <String>['a.mp4', 'b.mp4', 'c.mp4'],
    );

    expect(find.text('Source clips (3)'), findsOneWidget);
    expect(find.text('a.mp4, b.mp4 + 1 more · 0.0s'), findsOneWidget);
    expect(key('clipRow_0'), findsOneWidget);
    expect(key('clipRow_2'), findsOneWidget);

    await expandAdvanced(tester);
    await tapPlan(tester);

    expect(client.joinCalls, hasLength(1));
    expect(client.joinCalls.single, hasLength(3));
    expect(find.text('a.mp4, b.mp4 + 1 more · 60.0s'), findsOneWidget);
    expect(
      find.textContaining('starts at 0.0s of the story'),
      findsOneWidget,
    );
    expect(
      find.textContaining('starts at 10.0s of the story'),
      findsOneWidget,
    );
    expect(
      find.textContaining('starts at 30.0s of the story'),
      findsOneWidget,
    );
    // The plan ran against the joined timeline id.
    expect(client.lastPlan?['file_id'], 'j' * 32);
  });

  testWidgets('reorder changes the join order and drops the plan', (
    WidgetTester tester,
  ) async {
    final JoinClient client = JoinClient();
    await pump(
      tester,
      client: client,
      clipNames: <String>['a.mp4', 'b.mp4'],
    );
    await expandAdvanced(tester);
    await tapPlan(tester);
    expect(client.joinCalls, hasLength(1));
    final List<String> firstOrder = client.joinCalls.single;

    await tester.ensureVisible(key('clipMenu_0'));
    await tester.pumpAndSettle();
    await tester.tap(key('clipMenu_0'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Move later').last);
    await tester.pumpAndSettle();

    expect(find.text('No plan yet.'), findsOneWidget);
    await tapPlan(tester);
    expect(client.joinCalls, hasLength(2));
    expect(client.joinCalls[1], <String>[firstOrder[1], firstOrder[0]]);
  });

  testWidgets('remove and replace invalidate the join', (
    WidgetTester tester,
  ) async {
    final JoinClient client = JoinClient();
    await pump(
      tester,
      client: client,
      clipNames: <String>['a.mp4', 'b.mp4', 'c.mp4'],
      pickQueue: <String>['a.mp4'],
    );
    await expandAdvanced(tester);
    await tapPlan(tester);
    expect(client.joinCalls.single, hasLength(3));

    await tester.ensureVisible(key('clipMenu_1'));
    await tester.pumpAndSettle();
    await tester.tap(key('clipMenu_1'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Remove').last);
    await tester.pumpAndSettle();
    expect(find.text('No plan yet.'), findsOneWidget);
    await tapPlan(tester);
    expect(client.joinCalls.last, hasLength(2));

    // Replace the first slot with a fresh pick.
    await tester.ensureVisible(key('clipMenu_0'));
    await tester.pumpAndSettle();
    await tester.tap(key('clipMenu_0'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Replace').last);
    await tester.pumpAndSettle();
    expect(find.text('No plan yet.'), findsOneWidget);
  });

  testWidgets('add clip appends and joins', (
    WidgetTester tester,
  ) async {
    final JoinClient client = JoinClient();
    await pump(
      tester,
      client: client,
      clipNames: <String>['a.mp4'],
      pickQueue: <String>['b.mp4'],
    );
    expect(find.text('Source video'), findsOneWidget);

    await tester.ensureVisible(key('addClipButton'));
    await tester.pumpAndSettle();
    await tester.tap(key('addClipButton'));
    await tester.pumpAndSettle();
    expect(find.text('Source clips (2)'), findsOneWidget);

    await expandAdvanced(tester);
    await tapPlan(tester);
    expect(client.joinCalls.single, hasLength(2));
  });

  testWidgets('missing server id re-uploads that clip and retries', (
    WidgetTester tester,
  ) async {
    final JoinClient client = JoinClient(
      joinFailure: BackendException('Unknown file_id.', 404, <String, dynamic>{
        'missing': 's' * 32,
      }),
    );
    final Project project = Project(
      id: 'p1',
      name: 'a.mp4',
      sourcePath: '${tempDir.path}/a.mp4',
      fileName: 'a.mp4',
      updatedAt: DateTime(2026, 1, 1),
      clips: <Map<String, dynamic>>[
        <String, dynamic>{
          'devicePath': '${tempDir.path}/a.mp4',
          'fileId': 's' * 32,
          'name': 'a.mp4',
          'duration': 10.0,
        },
        <String, dynamic>{
          'devicePath': '${tempDir.path}/b.mp4',
          'fileId': 't' * 32,
          'name': 'b.mp4',
          'duration': 20.0,
        },
      ],
    );
    await pump(
      tester,
      client: client,
      clipNames: <String>['a.mp4', 'b.mp4'],
      project: project,
    );

    await expandAdvanced(tester);
    await tapPlan(tester);

    // The stale clip was re-uploaded (new id) and the join retried.
    expect(client.joinCalls, hasLength(2));
    expect(client.pathIds['${tempDir.path}/a.mp4'], isNot('s' * 32));
    expect(client.lastPlan?['file_id'], 'j' * 32);
  });

  testWidgets('single clip skips joining', (
    WidgetTester tester,
  ) async {
    final JoinClient client = JoinClient();
    await pump(tester, client: client);

    await expandAdvanced(tester);
    await tapPlan(tester);

    expect(client.joinCalls, isEmpty);
    expect(find.text('clip.mp4 · 13.0s'), findsOneWidget);
  });

  testWidgets('clip thumbnails load from server frames', (
    WidgetTester tester,
  ) async {
    final JoinClient client = JoinClient();
    await pump(
      tester,
      client: client,
      clipNames: <String>['a.mp4', 'b.mp4'],
    );
    await expandAdvanced(tester);
    await tapPlan(tester);
    expect(client.frameCalls, 2);
  });

  testWidgets('add clip respects the server maximum', (
    WidgetTester tester,
  ) async {
    final JoinClient client = JoinClient(maxClips: 2);
    int picks = 0;
    await tester.pumpWidget(
      MaterialApp(
        home: WorkspaceScreen(
          video: VideoSelection('${tempDir.path}/a.mp4'),
          clips: <VideoSelection>[
            VideoSelection('${tempDir.path}/a.mp4'),
            VideoSelection('${tempDir.path}/b.mp4'),
          ],
          setup: const EditSetup(
            instruction: 'keep only the first 5 seconds',
          ),
          onChanged: (_) {},
          client: client,
          onShare: (_) async {},
          pickClip: () async {
            picks++;
            return '${tempDir.path}/c.mp4';
          },
        ),
      ),
    );
    await tester.pumpAndSettle();

    await tester.ensureVisible(key('addClipButton'));
    await tester.pumpAndSettle();
    await tester.tap(key('addClipButton'));
    await tester.pumpAndSettle();
    expect(find.textContaining('maximum of 2 clips'), findsOneWidget);
    expect(picks, 0);
  });
}
