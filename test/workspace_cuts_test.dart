import 'dart:io';

import 'package:editor_23/models/edit_setup.dart';
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

/// Fake backend whose plan echoes explicit cuts as keep/remove ops,
/// mirroring the server merge (explicit intervals win).
class CutsClient extends BackendClient {
  int planCalls = 0;
  Map<String, dynamic>? lastPlanBody;

  @override
  Future<Map<String, dynamic>> getCapabilities() async {
    return <String, dynamic>{
      'sfx_assets': <String>['pop'],
    };
  }

  @override
  Future<Map<String, dynamic>> uploadFile(
    String path, {
    void Function(int sent, int total)? onProgress,
    String label = 'source',
  }) async {
    return <String, dynamic>{
      'file_id': 'f1',
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
    switch (route) {
      case '/api/plan':
        planCalls++;
        lastPlanBody = body;
        final List<Map<String, dynamic>> ops = <Map<String, dynamic>>[];
        final Map<String, dynamic>? explicit =
            body['explicit_cuts'] as Map<String, dynamic>?;
        if (explicit != null) {
          for (final List<dynamic> range
              in explicit['ranges'] as List<dynamic>) {
            ops.add(<String, dynamic>{
              'op': explicit['mode'],
              'origin': 'control',
              'start': (range[0] as num).toDouble(),
              'end': (range[1] as num).toDouble(),
            });
          }
        } else {
          ops.add(<String, dynamic>{'op': 'trim', 'start': 0.0, 'end': 5.0});
        }
        return <String, dynamic>{
          'plan': <String, dynamic>{'operations': ops, 'notes': ''},
          'warnings': <String>[],
          'adjustments': explicit == null
              ? <String>[]
              : <String>[
                  "Your kept/removed intervals replaced the planner's cut.",
                ],
          'pending': <String>[],
          'model': 'test-model',
          'suggestions': <Map<String, dynamic>>[
            <String, dynamic>{
              'start': 2.0,
              'end': 3.0,
              'reason': '1.0s of silence — possible dead air.',
            },
          ],
        };
      case '/api/render':
        return <String, dynamic>{
          'render_id': 'r1',
          'url': '/api/files/r1',
          'steps_used': <String>['ffmpeg_render'],
          'warnings': <String>[],
          'adjustments': <String>[],
          'plan': body['plan'],
          'output': <String, dynamic>{'duration': 10.0, 'has_audio': true},
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
  Future<void> downloadToFile(String route, String destPath) async {}
}

Finder _key(String name) => find.byKey(ValueKey<String>(name));

Future<void> _pump(
  WidgetTester tester, {
  required CutsClient client,
  String instruction = 'keep the good parts',
}) async {
  await tester.pumpWidget(
    MaterialApp(
      home: WorkspaceScreen(
        video: const VideoSelection('clip.mp4'),
        setup: EditSetup(instruction: instruction),
        onChanged: (_) {},
        client: client,
        onShare: (_) async {},
      ),
    ),
  );
  await tester.pumpAndSettle();
}

Future<void> _addCut(WidgetTester tester, String start, String end) async {
  await tester.ensureVisible(_key('cutStartField'));
  await tester.pumpAndSettle();
  await tester.enterText(_key('cutStartField'), start);
  await tester.enterText(_key('cutEndField'), end);
  await tester.pump();
  await tester.ensureVisible(_key('addCutButton'));
  await tester.pumpAndSettle();
  await tester.tap(_key('addCutButton'));
  await tester.pumpAndSettle();
}

Future<void> _setCutMode(WidgetTester tester, String label) async {
  final Finder segment = find.descendant(
    of: _key('cutModeToggle'),
    matching: find.text(label),
  );
  expect(segment, findsOneWidget);
  await tester.ensureVisible(segment);
  await tester.pumpAndSettle();
  await tester.tap(segment);
  await tester.pumpAndSettle();
}

Future<void> _expandAdvanced(WidgetTester tester) async {
  await tester.ensureVisible(find.text('Show step-by-step controls'));
  await tester.pumpAndSettle();
  await tester.tap(find.text('Show step-by-step controls'));
  await tester.pumpAndSettle();
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  setUpAll(() async {
    final Directory dir = await Directory.systemTemp.createTemp('ws-cuts-test');
    PathProviderPlatform.instance = FakePathProviderPlatform(dir.path);
  });

  testWidgets('keep intervals preview output spans and reach the plan', (
    WidgetTester tester,
  ) async {
    final CutsClient client = CutsClient();
    await _pump(tester, client: client);

    await _addCut(tester, '0', '4');
    await _addCut(tester, '7', '13');
    expect(find.text('KEEP'), findsNWidgets(2));
    expect(find.textContaining('plays 0.0–4.0s of the edit'), findsOneWidget);
    expect(find.textContaining('plays 4.0–10.0s of the edit'), findsOneWidget);
    expect(
      find.text('Estimated edited length: 10.0s (before transitions)'),
      findsOneWidget,
    );

    await _expandAdvanced(tester);
    await tester.ensureVisible(_key('generatePlanButton'));
    await tester.pumpAndSettle();
    await tester.tap(_key('generatePlanButton'));
    await tester.pumpAndSettle();

    final Map<String, dynamic>? explicit =
        client.lastPlanBody?['explicit_cuts'] as Map<String, dynamic>?;
    expect(explicit?['mode'], 'keep');
    expect(explicit?['ranges'], <List<double>>[
      <double>[0.0, 4.0],
      <double>[7.0, 13.0],
    ]);
    expect(find.text('Keep'), findsWidgets);
  });

  testWidgets('remove mode estimates source minus removed', (
    WidgetTester tester,
  ) async {
    final CutsClient client = CutsClient();
    await _pump(tester, client: client);

    await _setCutMode(tester, 'Remove');
    await _addCut(tester, '4', '7');
    expect(find.text('REMOVE'), findsOneWidget);
    expect(
      find.text('Estimated edited length: 10.0s (before transitions)'),
      findsOneWidget,
    );
    expect(
      find.descendant(
        of: _key('cutsCard'),
        matching: find.textContaining('plays '),
      ),
      findsNothing,
    );
  });

  testWidgets('invalid intervals are rejected with a message', (
    WidgetTester tester,
  ) async {
    final CutsClient client = CutsClient();
    await _pump(tester, client: client);

    await _addCut(tester, '5', '5');
    expect(find.textContaining('0 ≤ start < end'), findsOneWidget);
    expect(find.text('KEEP'), findsNothing);

    await _addCut(tester, '0', '4');
    await _addCut(tester, '3', '6');
    expect(find.textContaining('must not overlap'), findsOneWidget);
    expect(find.text('KEEP'), findsOneWidget);

    await _addCut(tester, '0', '99');
    expect(find.textContaining('past the 13.0s source'), findsOneWidget);
    expect(find.text('KEEP'), findsOneWidget);
  });

  testWidgets('mixed keep and remove blocks generation', (
    WidgetTester tester,
  ) async {
    final CutsClient client = CutsClient();
    await _pump(tester, client: client);

    await _addCut(tester, '0', '4');
    await _setCutMode(tester, 'Remove');
    await _addCut(tester, '4', '7');
    expect(find.textContaining('cannot combine'), findsOneWidget);

    await tester.ensureVisible(_key('generateEditedVideoButton'));
    await tester.pumpAndSettle();
    await tester.tap(_key('generateEditedVideoButton'));
    await tester.pumpAndSettle();
    expect(client.planCalls, 0);
    expect(find.text('Edited video'), findsNothing);
  });

  testWidgets('removing an interval drops it from the render', (
    WidgetTester tester,
  ) async {
    final CutsClient client = CutsClient();
    await _pump(tester, client: client);

    await _addCut(tester, '0', '4');
    await _addCut(tester, '7', '13');
    await tester.ensureVisible(_key('removeCutButton_0'));
    await tester.pumpAndSettle();
    await tester.tap(_key('removeCutButton_0'));
    await tester.pumpAndSettle();
    expect(find.text('KEEP'), findsOneWidget);
    expect(
      find.text('Estimated edited length: 6.0s (before transitions)'),
      findsOneWidget,
    );
  });

  testWidgets('source duration and timelines are labeled', (
    WidgetTester tester,
  ) async {
    final CutsClient client = CutsClient();
    await _pump(tester, client: client);

    await _addCut(tester, '0', '4');
    expect(find.text('clip.mp4 · 13.0s'), findsOneWidget);
    expect(
      find.textContaining('Main clips form the story in order'),
      findsOneWidget,
    );
    expect(find.text('At (edited sec)'), findsOneWidget);
    expect(find.textContaining('source-video seconds'), findsWidgets);
    expect(
      find.textContaining('from 0:00 of the edited video'),
      findsOneWidget,
    );
  });

  Future<void> tapPlan(WidgetTester tester) async {
    await _expandAdvanced(tester);
    await tester.ensureVisible(_key('generatePlanButton'));
    await tester.pumpAndSettle();
    await tester.tap(_key('generatePlanButton'));
    await tester.pumpAndSettle();
  }

  Future<void> openReview(WidgetTester tester) async {
    await tester.ensureVisible(_key('reviewCutsButton'));
    await tester.pumpAndSettle();
    await tester.tap(_key('reviewCutsButton'));
    await tester.pumpAndSettle();
  }

  testWidgets('cut review lists spans with clip and edited time', (
    WidgetTester tester,
  ) async {
    final CutsClient client = CutsClient();
    await _pump(tester, client: client);
    await tapPlan(tester);
    await openReview(tester);

    expect(
      find.text('Keep 0.0–5.0s · clip.mp4'),
      findsOneWidget,
    );
    expect(
      find.text('Removed by the brief · not in the edit'),
      findsOneWidget,
    );
    expect(
      find.text('Remove 5.0–13.0s · clip.mp4'),
      findsOneWidget,
    );
    expect(
      find.text('Kept for the story · edited 0.0–5.0s'),
      findsOneWidget,
    );
    expect(
      find.text('Suggestion 2.0–3.0s · clip.mp4'),
      findsOneWidget,
    );
  });

  testWidgets('review restore and apply re-plan with keeps', (
    WidgetTester tester,
  ) async {
    final CutsClient client = CutsClient();
    await _pump(tester, client: client);
    await tapPlan(tester);
    await openReview(tester);

    // Restore the removed tail, then apply: the next plan carries
    // the reviewed keeps as explicit cuts.
    await tester.ensureVisible(find.text('Restore'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Restore'));
    await tester.pumpAndSettle();
    expect(find.text('Remove 5.0–13.0s · clip.mp4'), findsNothing);
    expect(find.text('Keep 0.0–13.0s · clip.mp4'), findsOneWidget);

    await tester.ensureVisible(_key('applyReviewButton'));
    await tester.pumpAndSettle();
    await tester.tap(_key('applyReviewButton'));
    await tester.pumpAndSettle();
    expect(client.planCalls, 2);
    final Map<String, dynamic> explicit =
        client.lastPlanBody?['explicit_cuts'] as Map<String, dynamic>;
    expect(explicit['mode'], 'keep');
    expect(explicit['ranges'], <dynamic>[
      <dynamic>[0.0, 13.0],
    ]);
  });

  testWidgets('review suggestion cut carves the span', (
    WidgetTester tester,
  ) async {
    final CutsClient client = CutsClient();
    await _pump(tester, client: client);
    await tapPlan(tester);
    await openReview(tester);

    await tester.ensureVisible(find.text('Cut'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Cut'));
    await tester.pumpAndSettle();
    expect(find.text('Keep 0.0–2.0s · clip.mp4'), findsOneWidget);
    expect(find.text('Keep 3.0–5.0s · clip.mp4'), findsOneWidget);
    expect(find.text('Suggestion 2.0–3.0s · clip.mp4'), findsNothing);
  });
}
