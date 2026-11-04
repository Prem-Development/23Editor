import 'dart:async';
import 'dart:io';

import 'package:editor_23/models/edit_setup.dart';
import 'package:editor_23/models/video_selection.dart';
import 'package:editor_23/screens/workspace_screen.dart';
import 'package:editor_23/services/backend_client.dart';
import 'package:editor_23/widgets/primary_button.dart';
import 'package:editor_23/widgets/section_card.dart';
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

Map<String, dynamic> _planJson() => <String, dynamic>{
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

Map<String, dynamic> _renderJson(Map<String, dynamic> plan) =>
    <String, dynamic>{
      'render_id': 'r1',
      'url': '/api/files/r1',
      'steps_used': <String>['ffmpeg_render'],
      'warnings': <String>[],
      'adjustments': <String>[],
      'plan': plan,
      'output': <String, dynamic>{'duration': 5.0, 'has_audio': true},
    };

/// Fake client whose first /api/plan call waits on [planGate]; later
/// calls answer immediately. Lets a test edit settings mid-plan.
class GatingClient extends BackendClient {
  Completer<Map<String, dynamic>>? planGate;
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
    return <String, dynamic>{'file_id': 'f1', 'label': label};
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
        final Completer<Map<String, dynamic>>? gate = planGate;
        planGate = null;
        if (gate != null) {
          return gate.future;
        }
        return _planJson();
      case '/api/render':
        return _renderJson(
          (body['plan'] as Map?)?.cast<String, dynamic>() ??
              <String, dynamic>{},
        );
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
  required GatingClient client,
  String instruction = 'keep the first 5 seconds',
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

List<String> _cardOrder(WidgetTester tester) {
  final SingleChildScrollView scroll = tester.widget(
    find.byType(SingleChildScrollView),
  );
  final Column column = scroll.child! as Column;
  return <String>[
    for (final Widget child in column.children)
      if (child is SectionCard) ((child.key! as ValueKey).value as String),
  ];
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  setUpAll(() async {
    final Directory dir = await Directory.systemTemp.createTemp(
      'ws-order-test',
    );
    PathProviderPlatform.instance = FakePathProviderPlatform(dir.path);
  });

  testWidgets('settings come before summary, generate, and status', (
    WidgetTester tester,
  ) async {
    await _pump(tester, client: GatingClient());
    expect(_cardOrder(tester), <String>[
      'instructionCard',
      'styleCard',
      'analysisCard',
      'referenceCard',
      'cutsCard',
      'captionsCard',
      'musicSfxCard',
      'thumbnailCard',
      'editsCard',
      'generateCard',
      'statusCard',
      'advancedCard',
    ]);
  });

  testWidgets('exactly one primary Generate edited video action', (
    WidgetTester tester,
  ) async {
    await _pump(tester, client: GatingClient());
    expect(_key('generateEditedVideoButton'), findsOneWidget);
    expect(
      find.widgetWithText(PrimaryButton, 'Generate edited video'),
      findsOneWidget,
    );
  });

  testWidgets('wonder escalation is explicit, off by default', (
    WidgetTester tester,
  ) async {
    final GatingClient client = GatingClient();
    await _pump(tester, client: client);

    final CheckboxListTile toggle = tester.widget<CheckboxListTile>(
      _key('allowWonderToggle'),
    );
    expect(toggle.value, isFalse);

    await tester.ensureVisible(_key('generateEditedVideoButton'));
    await tester.pumpAndSettle();
    await tester.tap(_key('generateEditedVideoButton'));
    await tester.pumpAndSettle();
    expect(client.lastPlanBody?['allow_wonder'], isNull);

    // Enabling escalation invalidates the Spark-only plan, then the
    // next generation carries the explicit flag.
    await tester.ensureVisible(_key('allowWonderToggle'));
    await tester.pumpAndSettle();
    await tester.tap(_key('allowWonderToggle'));
    await tester.pumpAndSettle();
    expect(find.text('No plan yet.'), findsOneWidget);

    await tester.ensureVisible(_key('generateEditedVideoButton'));
    await tester.pumpAndSettle();
    await tester.tap(_key('generateEditedVideoButton'));
    await tester.pumpAndSettle();
    expect(client.lastPlanBody?['allow_wonder'], isTrue);
  });

  testWidgets('edits summary shows current selections before any plan', (
    WidgetTester tester,
  ) async {
    await _pump(tester, client: GatingClient());
    expect(find.text('Edits to apply'), findsOneWidget);
    expect(find.text('No plan yet.'), findsOneWidget);
    expect(find.text('Style: No style preset'), findsOneWidget);
    expect(find.text('Framing: Original'), findsOneWidget);
    expect(find.text('Captions: Auto'), findsOneWidget);
    expect(find.text('Attached: No extras attached'), findsOneWidget);
  });

  testWidgets('mid-plan edits discard the stale plan, then regenerate', (
    WidgetTester tester,
  ) async {
    final GatingClient client = GatingClient();
    final Completer<Map<String, dynamic>> gate =
        Completer<Map<String, dynamic>>();
    client.planGate = gate;
    await _pump(tester, client: client);

    await tester.ensureVisible(_key('generateEditedVideoButton'));
    await tester.pumpAndSettle();
    await tester.tap(_key('generateEditedVideoButton'));
    for (int i = 0; i < 20 && client.planCalls == 0; i++) {
      await tester.pump(const Duration(milliseconds: 50));
    }
    expect(client.planCalls, 1);

    // Edit while the plan request is in flight.
    await tester.ensureVisible(_key('instructionField'));
    // Bounded pumps only: the open gate keeps the stage spinner alive,
    // so pumpAndSettle cannot quiesce until the gate completes.
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 500));
    await tester.enterText(
      _key('instructionField'),
      'keep the first 5 seconds and add titles',
    );
    await tester.pump();
    expect(find.text('No plan yet.'), findsOneWidget);

    // The late plan response belongs to old settings: discarded.
    gate.complete(_planJson());
    await tester.pumpAndSettle();
    expect(find.text('No plan yet.'), findsOneWidget);
    expect(client.lastPlanBody?['instruction'], 'keep the first 5 seconds');

    // Regenerating with the current settings works end to end.
    await tester.ensureVisible(_key('generateEditedVideoButton'));
    await tester.pumpAndSettle();
    await tester.tap(_key('generateEditedVideoButton'));
    await tester.pumpAndSettle();
    expect(find.text('Edited video'), findsOneWidget);
  });
}
